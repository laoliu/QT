"""A 股轮动策略基类

将 SmallCapRotationStrategy / MultiFactorEnhanceStrategy 等 A 股轮动策略的
共性逻辑抽取到此处，消除代码重复（对标 cb_rotation_base.py 的模板方法模式）：

- 通用预过滤 pipeline（价格/市值/成交额/上市天数/ST/停牌）
- 价格/成交额历史窗口维护
- 动量 & 波动率计算工具
- 止损检查
- 调仓判断（含 buffer_k 缓冲机制 + 集中度保护）
- 轮动指令生成（卖出不在目标池 → 等权买入新目标）
- on_start / on_fill 钩子

各子类只需实现：
- `config` 属性（返回 EquityRotationConfig 或其子类）
- `_score_candidates()`: 对已过滤候选池打分/排名

可选覆盖钩子：
- `_extra_filter()`: 策略特有额外过滤条件
- `_custom_exit_check()`: 策略特有退出逻辑
- `_on_bar_update_state()`: 每 bar 更新额外内部状态
- `_on_start_extra()`: 额外初始化

设计对齐 copilot-instructions.md：
- 策略层只输出 OrderIntent，不直接修改持仓/资金
- 过滤逻辑与排序逻辑分离，便于测试和审计
- UniversePolicy 显式过滤，不得默认"全 A 股都可交易"

**因子解耦规划（TODO：后续版本）**

当前各子策略的 _score_candidates() 内联了因子计算逻辑（动量/波动率/质量/价值等），
这在策略数 <= 10 时尚可维护，但存在以下问题：
1. 同一因子在不同策略中重复实现（如 ROE 质量因子、动量因子）
2. 因子值无法跨策略复用或持久化缓存
3. 因子研发与策略逻辑耦合，不利于独立回测/A-B测试

后续计划通过 `src/aipha/features/` 模块提供独立的因子计算框架：
- FactorEngine: 负责因子注册、依赖解析、批量计算、缓存
- Factor 抽象基类: 统一接口 (bars, metadata) → factor_values
- 策略层只引用 factor_name + weight，不再内嵌因子计算逻辑
参考 Qlib 的 Alpha158/DataHandler 与 Alphalens 的因子分析流程。
"""

from __future__ import annotations

import logging
import math
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)

# 卖出资金预估折扣系数：预估卖出所得时留出 0.1% 安全边际以覆盖费用+滑点
_SELL_CREDIT_DISCOUNT = 0.995  # 与 engine.py sell_credit_discount 默认值对齐


# ---------------------------------------------------------------------------
# 候选标的信息
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EquityCandidateInfo:
    """已通过预过滤的 A 股候选标的信息。

    策略的 _score_candidates() 接收此列表，负责打分/排名。
    """

    symbol: str
    close: float
    metadata: dict[str, Any]

    # 常用字段快捷访问
    circ_mv: float = 0.0         # 流通市值（万元）
    turnover_rate: float = 0.0   # 换手率 (%)
    amount: float = 0.0          # 成交额（万元）
    list_date: str = ""          # 上市日期 YYYYMMDD
    name: str = ""               # 股票简称


# 向后兼容别名（旧代码使用 CandidateInfo）
CandidateInfo = EquityCandidateInfo


def rank_normalize(
    values: list[float | None],
    *,
    fill: str = "median",
) -> list[float]:
    """对值列表做排名归一化（模块级辅助函数）。

    将原始值按排名映射到 [0, 1] 区间。缺失值（None / NaN / ±inf）按
    ``fill`` 处理：

    - ``"median"``（默认）：给中位分 0.5，不奖励也不惩罚
    - ``"zero"``：给 0 分，等同排最后

    这是 EquityRotationStrategy.rank_normalize 静态方法的简化列表接口。

    注意 NaN 必须和 None 走同一条路。之前只判 ``is not None``，NaN 会混进
    排序里 —— 而 NaN 的所有比较都是 False，sort 不会移动它，结果是
    "因子缺失的标的拿到了最高分"。对选股策略来说，这等于把数据缺失
    当成了利好。
    """
    n = len(values)
    if n == 0:
        return []
    if fill not in ("median", "zero"):
        raise ValueError(f"fill 只支持 'median' / 'zero'，收到 {fill!r}")

    missing_score = 0.5 if fill == "median" else 0.0

    # 过滤出有效值并排序（None / NaN / inf 都算缺失）
    indexed = [
        (i, float(v)) for i, v in enumerate(values)
        if v is not None and math.isfinite(v)
    ]
    indexed.sort(key=lambda x: x[1])
    result = [missing_score] * n
    for rank, (idx, _) in enumerate(indexed):
        result[idx] = rank / max(len(indexed) - 1, 1)
    return result


# ---------------------------------------------------------------------------
# 通用配置基类
# ---------------------------------------------------------------------------

@dataclass
class EquityRotationConfig:
    """A 股轮动策略通用配置。

    子类可继承并添加策略特有字段。所有共性字段在此定义。
    """

    # --- 持仓 ---
    top_k: int = 30
    rebalance_days: int = 10

    # --- 通用筛选 ---
    min_price: float = 3.0
    max_price: float = 50.0
    min_mv: float = 100_000.0       # 最小流通市值（万元，默认 10 亿）
    max_mv: float = float("inf")    # 最大流通市值（万元）
    min_turnover: float = 500.0     # 最小成交额（万元）
    min_listed_days: int = 60       # 最少上市天数（排除次新）
    exclude_st: bool = True
    check_index_member: bool = True  # 是否检查指数成分归属（OOS回放模式可关闭）

    # --- 风控 ---
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0    # 追踪止损：从持仓期间最高价回撤超过此比例则卖出（0=禁用）
    min_positions: int = 5   # 最少持仓数：候选不足时按比例缩仓
    min_candidates_to_trade: int = 0  # 最低交易候选数，0=不启用
    max_industry_pct: float = 1.0  # 单行业最大持仓占比（1.0=不限；建议 0.20~0.25）

    # --- 换手控制 ---
    buffer_k: int = 0  # 缓冲区大小：已持仓标的在 top_k+buffer_k 内则保留

    # --- 交易 ---
    lot_size: int = 100
    max_single_pct: float = 1.0     # 单票最大占比（默认不限）
    cash_reserve_pct: float = 0.05  # 现金缓冲比例
    inverse_vol_weighting: bool = False  # 反波动率加权（低波动→高权重，朴素风险平价）

    # --- 因子计算参数（子类可覆盖）---
    momentum_lookback: int = 20
    volatility_lookback: int = 20
    momentum_skip_recent: int = 0   # 跳过最近 N 天（剔除短期反转噪声）
    momentum_multi_period: bool = False  # 多周期动量混合

    # --- 因子预处理 ---
    winsorize: bool = True           # 启用因子缩尾处理（消除极端值对排名的扭曲）
    winsorize_lower: float = 0.02    # 缩尾下限分位数
    winsorize_upper: float = 0.98    # 缩尾上限分位数


# A 股策略持仓集中度推荐阈值
MIN_RECOMMENDED_TOP_K = 15  # 低于此值视为过度集中（单票风险过高）
RECOMMENDED_MAX_INDUSTRY_PCT = 0.20  # 推荐单行业上限 20%


# ---------------------------------------------------------------------------
# A 股轮动策略基类
# ---------------------------------------------------------------------------

class EquityRotationStrategy(Strategy):
    """A 股轮动策略基类。

    实现了通用的 on_bar() 流程（模板方法模式）：
    1. _on_bar_update_state() — 更新内部状态
    2. _update_history() — 维护价格/成交额历史窗口
    3. _check_stop_loss() — 止损检查
    4. _custom_exit_check() — 子类特殊退出
    5. _should_rebalance() — 调仓判断
    6. _filter_universe() → EquityCandidateInfo 列表
    7. _score_candidates() → 子类排名
    8. _generate_rebalance() — 生成轮动指令

    子类必须实现：
    - config 属性（返回 EquityRotationConfig 或其子类）
    - _score_candidates(candidates, context) → list[str]
    """

    # 标记：策略层已通过 context.metadata["position_scale"] 调整买入资金分配，
    # 引擎不应再对 BUY 意图乘以 position_scale（避免双重缩减）。
    handles_position_scale: bool = True

    @property
    @abstractmethod
    def config(self) -> EquityRotationConfig:
        """返回策略配置。"""
        ...

    @abstractmethod
    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """对候选池打分并返回排名后的 symbol 列表。

        返回长度应 >= config.top_k + config.buffer_k，以支持缓冲带机制。
        _generate_rebalance() 会根据 top_k/buffer_k 做最终裁剪。

        Args:
            candidates: 已通过预过滤的候选列表
            context: 当前策略上下文

        Returns:
            按优先级排序的 symbol 列表
        """
        ...

    def __init__(self) -> None:
        self._bar_count: int = 0
        self._entry_prices: dict[str, float] = {}
        self._max_prices: dict[str, float] = {}  # 追踪止损用：持仓期间最高价
        # 价格历史窗口（用于计算动量和波动率）
        self._price_history: dict[str, list[float]] = {}
        # 成交额历史（用于流动性判断）
        self._amount_history: dict[str, list[float]] = {}

    def on_start(self) -> None:
        """策略启动时初始化内部状态。

        重置 bar 计数器、入场价格、追踪止损最高价、价格/成交额历史窗口，
        调用子类 ``_on_start_extra()`` 钩子，并检查持仓集中度配置。
        """
        self._bar_count = 0
        self._entry_prices.clear()
        self._max_prices.clear()
        self._price_history.clear()
        self._amount_history.clear()
        self._on_start_extra()
        # 持仓集中度检查
        cfg = self.config
        if cfg.top_k < MIN_RECOMMENDED_TOP_K:
            import logging
            logging.getLogger(__name__).warning(
                f"⚠️ top_k={cfg.top_k} 低于推荐最低值 {MIN_RECOMMENDED_TOP_K}。"
                f"过度集中的持仓增加单票风险，可能导致过拟合。"
            )
        if cfg.max_industry_pct >= 1.0:
            import logging
            logging.getLogger(__name__).info(
                f"ℹ️ 行业集中度限制未启用 (max_industry_pct={cfg.max_industry_pct})。"
                f"建议设置 max_industry_pct={RECOMMENDED_MAX_INDUSTRY_PCT} 防止行业过度暴露。"
            )

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        """处理成交回报，更新入场价格和追踪止损最高价。

        Args:
            symbol: 标的代码
            side: 买卖方向（BUY/SELL）
            qty: 成交数量
            price: 成交价格
        """
        if side == Side.BUY:
            self._entry_prices[symbol] = price
            # 追踪止损：初始化最高价为买入价
            self._max_prices[symbol] = price
        elif side == Side.SELL:
            self._entry_prices.pop(symbol, None)
            self._max_prices.pop(symbol, None)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """每 bar 核心处理流程（模板方法）。

        按顺序执行：状态更新 → 历史窗口更新 → 止损检查 → 子类退出 →
        调仓判断 → 预过滤 → 子类排名 → 生成轮动指令。

        Args:
            bars: 当前 bar 数据，``{symbol: Bar}``
            context: 策略上下文（持仓、资金、时间等）

        Returns:
            本 bar 产生的订单意图列表
        """
        self._bar_count += 1
        intents: list[OrderIntent] = []

        # --- 1. 子类状态更新 ---
        self._on_bar_update_state(bars, context)

        # --- 2. 价格/成交额历史窗口更新 ---
        self._update_history(bars)

        # --- 3. 止损检查 ---
        stop_loss_intents = self._check_stop_loss(bars, context)
        intents.extend(stop_loss_intents)
        # 记录止损卖出的标的，避免调仓时重新买入刚止损的股票
        stop_loss_symbols = {i.symbol for i in stop_loss_intents}

        # --- 4. 子类特殊退出逻辑 ---
        intents.extend(self._custom_exit_check(bars, context))

        # --- 5. 调仓判断 ---
        if not self._should_rebalance():
            return intents

        # --- 6. 预过滤 ---
        candidates = self._filter_universe(bars, context)

        # --- 7. 子类排名 ---
        ranked_targets = self._score_candidates(candidates, context)

        # 排除当 bar 已止损的标的，避免逻辑冲突（刚止损就又买入）
        if stop_loss_symbols:
            ranked_targets = [s for s in ranked_targets if s not in stop_loss_symbols]

        # --- 8. 生成轮动指令 ---
        intents.extend(self._generate_rebalance(ranked_targets, context, bars))

        logger.info(
            f"[{self.name}] bar#{self._bar_count} "
            f"候选={len(candidates)} 目标={len(ranked_targets)} "
            f"订单={len(intents)}"
        )
        return intents

    # ------------------------------------------------------------------
    # 通用子方法
    # ------------------------------------------------------------------

    def _should_rebalance(self) -> bool:
        """是否应在当前 bar 调仓。

        首次调仓需要至少积累足够历史数据：
        - 使用动量/波动率因子时: momentum_lookback + skip_recent + 1 bar
        - 仅使用截面因子时: rebalance_days+1 bar 即可
        """
        cfg = self.config
        # 仅当策略实际使用动量/波动率因子时才要求长 warmup
        needs_history = getattr(cfg, 'weight_momentum', 0) > 0 or getattr(cfg, 'inverse_vol_weighting', False)
        if needs_history:
            skip = getattr(cfg, 'momentum_skip_recent', 0)
            min_bars = max(cfg.momentum_lookback + skip + 1, cfg.rebalance_days + 1)
        else:
            min_bars = cfg.rebalance_days + 1
        if self._bar_count < min_bars:
            return False
        return (self._bar_count - 1) % cfg.rebalance_days == 0

    def _update_history(self, bars: dict[str, Bar]) -> None:
        """更新价格和成交额历史窗口。"""
        cfg = self.config
        # +5 缓冲：避免周末/节假日导致历史窗口不足的边缘情况
        _HISTORY_BUFFER = 5
        skip = getattr(cfg, 'momentum_skip_recent', 0)
        max_len = max(cfg.momentum_lookback + skip, cfg.volatility_lookback) + _HISTORY_BUFFER
        for sym, bar in bars.items():
            # 价格历史
            hist = self._price_history.setdefault(sym, [])
            hist.append(bar.close)
            if len(hist) > max_len:
                del hist[:-max_len]

            # 成交额历史
            meta = bar.metadata or {}
            amt = meta.get("amount", bar.volume * bar.close / 10000 if bar.volume else 0)
            ahist = self._amount_history.setdefault(sym, [])
            ahist.append(amt)
            if len(ahist) > max_len:
                del ahist[:-max_len]

    def _filter_universe(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[EquityCandidateInfo]:
        """通用预过滤 pipeline：价格/市值/成交额/上市天数/ST/停牌。

        每次调仓输出过滤漏斗日志，便于诊断候选不足问题。
        """
        cfg = self.config
        candidates: list[EquityCandidateInfo] = []
        now = context.current_time

        # 过滤漏斗计数器
        n_total = len(bars)
        n_price = 0
        n_mv = 0
        n_turnover = 0
        n_listed = 0
        n_st = 0
        n_halt = 0
        n_extra = 0
        n_not_member = 0

        # 当 check_index_member=True 时，检查是否有 bar 携带 is_index_member 字段
        # 若所有 bar 均缺少该字段，则过滤器实际无效（默认 True 放行所有），需 warning
        if cfg.check_index_member and bars:
            _sample_meta = next(iter(bars.values())).metadata or {}
            if "is_index_member" not in _sample_meta:
                logger.warning(
                    f"[{self.name}] check_index_member=True 但 bar metadata 中"
                    f"缺少 is_index_member 字段，成分股过滤实际未生效。"
                    f"请确认数据加载器是否配置了 base_index，"
                    f"或设置 check_index_member: false 以明确关闭。"
                )

        for symbol, bar in bars.items():
            meta = bar.metadata or {}

            # 成分股归属过滤（动态成分模式下，仅保留当期成分）
            if cfg.check_index_member and not meta.get("is_index_member", True):
                n_not_member += 1
                continue

            # 价格过滤
            if bar.close < cfg.min_price or bar.close > cfg.max_price:
                n_price += 1
                continue

            # 停牌
            if bar.is_suspended:
                n_halt += 1
                continue

            # 流通市值
            circ_mv = meta.get("circ_mv")
            if circ_mv is None or circ_mv <= 0:
                n_mv += 1
                continue
            if circ_mv < cfg.min_mv or circ_mv > cfg.max_mv:
                n_mv += 1
                continue

            # 成交额
            amount = meta.get("amount", 0)
            if amount < cfg.min_turnover:
                n_turnover += 1
                continue

            # 上市天数
            list_date = meta.get("list_date", "")
            if list_date and now:
                try:
                    ld = datetime.strptime(str(list_date)[:8], "%Y%m%d")
                    if (now - ld).days < cfg.min_listed_days:
                        n_listed += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # ST 排除：同时检查名称和 is_st 结构化标志
            stock_name = meta.get("name", "")
            is_st_flag = meta.get("is_st", False)
            if cfg.exclude_st and (
                is_st_flag
                or "ST" in stock_name.upper()
                or "ST" in meta.get("stk_short_name", "").upper()
                or "退市" in stock_name  # 捕获退市整理期标的
            ):
                n_st += 1
                continue

            # 构建 EquityCandidateInfo
            ci = EquityCandidateInfo(
                symbol=symbol,
                close=bar.close,
                metadata=meta,
                circ_mv=circ_mv,
                turnover_rate=meta.get("turnover_rate", 0) or 0,
                amount=amount,
                list_date=str(list_date),
                name=stock_name,
            )

            # 子类额外过滤
            if not self._extra_filter(ci, context):
                n_extra += 1
                continue

            candidates.append(ci)

        n_pass = len(candidates)
        logger.info(
            f"[{self.name}] 过滤漏斗: 总{n_total} → "
            f"非成分-{n_not_member} 价格-{n_price} 市值-{n_mv} "
            f"流动性-{n_turnover} 上市-{n_listed} ST-{n_st} 停牌-{n_halt} "
            f"策略-{n_extra} → 通过={n_pass}"
        )
        return candidates

    def _check_stop_loss(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """策略级止损：支持固定止损 + 追踪止损双机制。

        1. 固定止损 (stop_loss_pct): 基于入场价 × (1 - stop_loss_pct)
        2. 追踪止损 (trailing_stop_pct): 基于持仓期间最高价 × (1 - trailing_stop_pct)
           保护浮盈，当从最高价回撤超过阈值则卖出

        两层止损互不冲突，取先触发者。

        注意：系统还存在引擎级止损（BacktestEngine._engine_stop_loss_check），
        与策略级止损独立运行。详见 engine.py 中的"双止损机制说明"。

        止损标的会被 on_bar() 记录到 stop_loss_symbols，
        防止调仓阶段把刚止损卖出的股票又买回来。
        """
        cfg = self.config
        intents: list[OrderIntent] = []
        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue

            # 更新追踪最高价
            if symbol in self._entry_prices:
                self._max_prices[symbol] = max(
                    self._max_prices.get(symbol, bar.close), bar.close,
                )

            # 固定止损检查
            entry = self._entry_prices.get(symbol)
            if cfg.stop_loss_pct > 0 and entry and bar.close <= entry * (1 - cfg.stop_loss_pct):
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"止损: {bar.close:.2f} < {entry * (1 - cfg.stop_loss_pct):.2f}",
                ))
                continue  # 已触发固定止损，无需再检查追踪

            # 追踪止损检查
            if cfg.trailing_stop_pct > 0:
                max_price = self._max_prices.get(symbol)
                if max_price and bar.close <= max_price * (1 - cfg.trailing_stop_pct):
                    intents.append(OrderIntent(
                        symbol=symbol, side=Side.SELL, qty=qty,
                        reason=(
                            f"追踪止损: {bar.close:.2f} < "
                            f"最高{max_price:.2f} × {1 - cfg.trailing_stop_pct:.2f}"
                        ),
                    ))
        return intents

    # ------------------------------------------------------------------
    # 行业集中度限制
    # ------------------------------------------------------------------

    def _cap_industry_in_target(
        self,
        ranked_targets: list[str],
        target_set: set[str],
        bars: dict[str, "Bar"],
    ) -> set[str]:
        """对目标持仓集合执行行业集中度上限。

        按 ranked_targets 的排名顺序遍历 target_set 中的标的，
        若某行业已达每行业上限则跳过该标的。
        被移除的空位按排名从 ranked_targets 中补充（优先选未超限行业）。

        Returns:
            经行业上限约束后的新 target_set
        """
        cfg = self.config
        desired_count = len(target_set)
        max_per_industry = max(1, round(desired_count * cfg.max_industry_pct))

        def _get_industry(symbol: str) -> str:
            bar = bars.get(symbol)
            if bar and bar.metadata:
                return bar.metadata.get("industry", "未知")
            return "未知"

        # 第一遍：按排名保留目标池中不超限的标的
        capped: list[str] = []
        ind_counts: dict[str, int] = {}
        removed_industries: dict[str, int] = {}

        for symbol in ranked_targets:
            if symbol not in target_set:
                continue
            ind = _get_industry(symbol)
            cnt = ind_counts.get(ind, 0)
            if cnt >= max_per_industry:
                removed_industries[ind] = removed_industries.get(ind, 0) + 1
                continue
            capped.append(symbol)
            ind_counts[ind] = cnt + 1

        # 第二遍：空位从 ranked_targets 中非原目标池的标的补充
        n_open = desired_count - len(capped)
        filled = 0
        if n_open > 0:
            capped_set = set(capped)
            for symbol in ranked_targets:
                if n_open <= 0:
                    break
                if symbol in capped_set or symbol in target_set:
                    continue
                ind = _get_industry(symbol)
                if ind_counts.get(ind, 0) >= max_per_industry:
                    continue
                capped.append(symbol)
                capped_set.add(symbol)
                ind_counts[ind] = ind_counts.get(ind, 0) + 1
                n_open -= 1
                filled += 1

        if removed_industries:
            summary = ", ".join(f"{k}:{v}" for k, v in removed_industries.items())
            logger.info(
                f"[{self.name}] 行业集中度限制 (≤{max_per_industry}只/行业): "
                f"移除 {sum(removed_industries.values())} 只 ({summary})"
                + (f", 补充 {filled} 只其他行业标的" if filled > 0 else "")
            )

        return set(capped)

    def _generate_rebalance(
        self,
        ranked_targets: list[str],
        context: StrategyContext,
        bars: dict[str, Bar],
    ) -> list[OrderIntent]:
        """生成轮动指令：卖出不在目标池 → 等权再平衡所有目标持仓。

        支持 buffer_k 缓冲机制（减少因排名波动导致的不必要换手）和集中度保护。
        等权再平衡：不仅对新买入标的做等权，还对已持仓标的做权重漂移修正。
        """
        cfg = self.config
        intents: list[OrderIntent] = []
        current_held = {s for s, q in context.positions.items() if q > 0}

        if cfg.buffer_k > 0 and current_held:
            # --- buffer 模式 ---
            buffer_limit = cfg.top_k + cfg.buffer_k
            extended_set = set(ranked_targets[:buffer_limit])
            keep = current_held & extended_set
            to_sell = current_held - extended_set
            n_need = max(0, cfg.top_k - len(keep))
            to_buy_candidates = [
                s for s in ranked_targets[:cfg.top_k] if s not in current_held
            ]
            to_buy = to_buy_candidates[:n_need]
            target_set = keep | set(to_buy)
        else:
            # --- 严格模式 ---
            top_targets = ranked_targets[:cfg.top_k]
            target_set = set(top_targets)
            to_sell = {s for s in current_held if s not in target_set}
            to_buy = [s for s in top_targets if s not in current_held]

        # --- 行业集中度上限 ---
        if cfg.max_industry_pct < 1.0 and target_set:
            capped_target = self._cap_industry_in_target(
                ranked_targets, target_set, bars
            )
            if capped_target != target_set:
                target_set = capped_target
                # 重建 to_sell / to_buy（行业限制可能移除已持仓或新买入标的）
                to_sell = {s for s in current_held if s not in target_set}
                to_buy = [
                    s for s in ranked_targets
                    if s in target_set and s not in current_held
                ]

        # 卖出不在目标池的标的
        sell_value = 0.0
        for symbol in to_sell:
            qty = context.positions.get(symbol, 0)
            if qty > 0:
                bar = bars.get(symbol)
                if bar:
                    sell_value += qty * bar.close
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"{self.name}轮动卖出",
                ))

        # 无目标标可买入时直接返回
        if not target_set:
            return intents

        # 计算已保留持仓市值
        keep_value = 0.0
        for symbol in (target_set & current_held):
            bar = bars.get(symbol)
            if bar:
                keep_value += context.positions.get(symbol, 0) * bar.close

        # 等权目标基于 NAV / 标的数（不扣 cash_reserve_pct）
        # cash_reserve_pct 只影响新买入可用资金
        total_portfolio = context.cash + sell_value * _SELL_CREDIT_DISCOUNT + keep_value

        # --- 集中度硬门禁 ---
        total_target_count = len(target_set)
        if cfg.min_candidates_to_trade > 0 and total_target_count < cfg.min_candidates_to_trade:
            logger.warning(
                f"[{self.name}] 候选{total_target_count}只 < "
                f"最低交易阈值{cfg.min_candidates_to_trade}，本期仅卖出不买入"
            )
            return intents

        # --- 集中度保护 ---
        if total_target_count < cfg.min_positions and cfg.min_positions > 0:
            scale = total_target_count / cfg.min_positions
            total_portfolio *= scale
            logger.info(
                f"[{self.name}] 集中度保护: 候选{total_target_count}只"
                f" < min_positions={cfg.min_positions}, "
                f"资金缩减至 {scale:.0%}"
            )

        target_value_each = total_portfolio / total_target_count

        # 单票上限
        if cfg.max_single_pct < 1.0:
            max_value = context.nav * cfg.max_single_pct
            target_value_each = min(target_value_each, max_value)

        # --- 组合优化：优化器 → 不等权 / 等权回退 ---
        optimizer = context.metadata.get("weight_optimizer")
        if optimizer is not None and total_target_count > 1:
            opt_weights = self._compute_optimizer_weights(
                optimizer, list(target_set)
            )
        elif cfg.inverse_vol_weighting and total_target_count > 1:
            # 反波动率加权：低波动标的获得更高权重（朴素风险平价）
            opt_weights = self._compute_inverse_vol_weights(list(target_set))
        else:
            opt_weights = None  # 等权

        def _target_value_for(sym: str) -> float:
            """返回单只标的的目标市值。"""
            if opt_weights is not None:
                tv = total_portfolio * opt_weights.get(sym, 0.0)
            else:
                tv = target_value_each
            if cfg.max_single_pct < 1.0:
                tv = min(tv, context.nav * cfg.max_single_pct)
            return tv

        # --- 再平衡：对已持仓标的做权重漂移修正 ---
        _DRIFT_THRESHOLD = 0.15  # 偏离 15% 以上才调整，减少交易摩擦
        # 价格安全边际：NextBarOpen 成交价可能高于昨收，预留 2% 缓冲防止超买
        _PRICE_SAFETY_MARGIN = 1.02
        for symbol in (target_set & current_held):
            bar = bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            current_qty = context.positions.get(symbol, 0)
            current_value = current_qty * bar.close
            sym_target_value = _target_value_for(symbol)
            est_price = bar.close * _PRICE_SAFETY_MARGIN
            target_qty = int(sym_target_value / est_price / cfg.lot_size) * cfg.lot_size

            if current_value > 0:
                drift = abs(current_value - sym_target_value) / sym_target_value if sym_target_value > 0 else 0
                if drift < _DRIFT_THRESHOLD:
                    continue  # 偏离不大，不调整

            delta = target_qty - current_qty
            if delta > 0 and delta >= cfg.lot_size:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.BUY, qty=delta,
                    reason=f"{self.name}再平衡补仓",
                ))
            elif delta < 0 and abs(delta) >= cfg.lot_size:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=abs(delta),
                    reason=f"{self.name}再平衡减仓",
                ))

        # --- 新买入标的：可用资金扣除 cash_reserve_pct ---
        # 集成 position_scale（由 MarketRegimeDetector 提供）实现动态现金分配：
        #   BULL → position_scale≈1.0 → cash_reserve≈ cfg 值
        #   BEAR → position_scale≈0.5 → cash_reserve 大幅提升，自动减仓
        position_scale = max(0.0, min(1.0, context.metadata.get("position_scale", 1.0)))
        effective_reserve = cfg.cash_reserve_pct + (1 - position_scale) * (1 - cfg.cash_reserve_pct)
        # 扣除漂移修正补仓已预占的资金
        drift_buy_cost = sum(
            i.qty * (bars[i.symbol].close * _PRICE_SAFETY_MARGIN if i.symbol in bars else 0)
            for i in intents if i.side == Side.BUY and "补仓" in (i.reason or "")
        )
        available_for_new = (
            (context.cash + sell_value * _SELL_CREDIT_DISCOUNT - drift_buy_cost)
            * (1 - effective_reserve)
        )
        available_for_new = max(0.0, available_for_new)  # 防负数
        for symbol in to_buy:
            bar = bars.get(symbol)
            if bar is None or bar.close <= 0:
                continue
            # 新买入使用目标值（可能不等权），但不超过剩余可用资金
            sym_target_value = _target_value_for(symbol)
            buy_value = min(sym_target_value, available_for_new)
            est_price = bar.close * _PRICE_SAFETY_MARGIN
            qty = int(buy_value / est_price / cfg.lot_size) * cfg.lot_size
            if qty >= cfg.lot_size:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.BUY, qty=qty,
                    reason=f"{self.name}轮动买入",
                ))
                available_for_new -= qty * est_price

        return intents

    def _compute_optimizer_weights(
        self,
        optimizer: Any,
        symbols: list[str],
    ) -> dict[str, float]:
        """调用组合优化器计算目标权重。

        从 ``_price_history`` 提取历史日收益率序列，供优化器使用。
        若优化失败，回退为等权。

        Args:
            optimizer: WeightOptimizer 实例
            symbols: 目标持仓标的列表

        Returns:
            {symbol: weight} 字典，权重之和为 1.0
        """
        # 从价格历史提取收益率
        returns: dict[str, list[float]] = {}
        for sym in symbols:
            hist = self._price_history.get(sym, [])
            if len(hist) >= 2:
                rets = []
                for i in range(1, len(hist)):
                    if hist[i - 1] > 0:
                        rets.append((hist[i] - hist[i - 1]) / hist[i - 1])
                    else:
                        rets.append(0.0)
                returns[sym] = rets
            else:
                returns[sym] = []

        try:
            weights = optimizer.optimize(
                symbols=symbols,
                returns=returns if any(len(r) > 0 for r in returns.values()) else None,
            )
            # 归一化确保权重和为 1
            total_w = sum(weights.values())
            if total_w > 0:
                return {s: w / total_w for s, w in weights.items()}
        except Exception as e:
            logger.warning(
                "[%s] 组合优化器调用失败 (%s)，回退等权: %s",
                self.name, type(optimizer).__name__, e,
            )

        # 回退等权
        n = len(symbols)
        return {s: 1.0 / n for s in symbols} if n > 0 else {}

    def _compute_inverse_vol_weights(
        self,
        symbols: list[str],
    ) -> dict[str, float] | None:
        """反波动率加权：低波动标的获得更高权重（朴素风险平价）。

        每只标的权重 ∝ 1/σ，σ 为近期日收益率标准差。
        若无法计算波动率（历史不足），则回退为等权。

        Args:
            symbols: 目标持仓标的列表

        Returns:
            {symbol: weight} 字典，权重之和为 1.0；若无法计算则返回 None（等权回退）
        """
        inv_vols: dict[str, float] = {}
        for sym in symbols:
            vol = self.calc_volatility(sym)
            if vol > 1e-10:
                inv_vols[sym] = 1.0 / vol
            else:
                # 波动率为零或无法计算 → 回退等权
                return None
        total = sum(inv_vols.values())
        if total <= 0:
            return None
        return {s: v / total for s, v in inv_vols.items()}

    # ------------------------------------------------------------------
    # 因子计算工具方法
    # ------------------------------------------------------------------

    def calc_momentum(self, symbol: str) -> float:
        """计算动量因子 = 过去 N 日收益率。"""
        cfg = self.config
        hist = self._price_history.get(symbol, [])
        if len(hist) < cfg.momentum_lookback + 1:
            return 0.0
        old = hist[-(cfg.momentum_lookback + 1)]
        new = hist[-1]
        return (new - old) / old if old > 0 else 0.0

    def calc_volatility(self, symbol: str) -> float:
        """计算波动率 = 过去 N 日日收益率标准差。"""
        cfg = self.config
        hist = self._price_history.get(symbol, [])
        if len(hist) < cfg.volatility_lookback + 1:
            return 0.0
        prices = hist[-(cfg.volatility_lookback + 1):]
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(variance)

    @staticmethod
    def rank_normalize(
        candidates: list[EquityCandidateInfo],
        factor_values: dict[str, float],
        weight: float,
        scores: dict[str, float],
        *,
        winsorize: bool = True,
        winsorize_lower: float = 0.02,
        winsorize_upper: float = 0.98,
        nan_handling: str = "median",
    ) -> None:
        """对因子值做排名归一化并加权累加到 scores 中。

        Args:
            candidates: 候选列表
            factor_values: {symbol: factor_value} 映射
            weight: 因子权重
            scores: {symbol: composite_score} 累加字典（会被修改）
            winsorize: 是否在排名前做缩尾处理（默认 True）
            winsorize_lower: 缩尾下限分位数（默认 2%）
            winsorize_upper: 缩尾上限分位数（默认 98%）
            nan_handling: 缺失值处理策略
                - "median"（默认）: 缺失因子获中位排名分（宽容模式）
                - "pollute": NaN 污染（任一因子缺失→综合分 NaN→rank_sort 剔除）
                - "skip": 缺失因子不参与评分（保留已有分数）

        Note:
            此处 docstring 一度把 "pollute" 写成默认值，与签名相反，
            pead_event 就是照着这句写的（"辅助因子缺失不应排除候选"
            的注释只有在默认污染时才成立）。默认确实是 "median"。

            另外 "pollute" 不是粘性的：下面第 1000 行，只要后续某个因子
            以 median/skip 模式调用且该标的有值，已有的 NaN 会被当 0
            重新累加，污染就没了。要靠 NaN 剔除标的，**所有**因子调用
            都得传 "pollute"，否则请在调用方显式过滤候选列表。
        """
        if weight <= 0:
            return

        # 缩尾处理：在排名前裁剪极端值，降低异常值对排名的扭曲
        if winsorize and len(factor_values) >= 5:
            from aipha.factors.equity_factors import winsorize_factors
            factor_values = winsorize_factors(
                factor_values, winsorize_lower, winsorize_upper,
            )

        # 有效值排名（nan/inf 排最后）
        valid = [
            (sym, v) for sym, v in factor_values.items()
            if math.isfinite(v)
        ]
        valid.sort(key=lambda x: x[1], reverse=True)

        n_valid = len(valid)
        median_rank_score = 0.5  # 中位排名分

        for rank, (sym, _) in enumerate(valid):
            rank_score = (n_valid - rank) / n_valid if n_valid > 0 else 0
            prev = scores.get(sym, 0.0)
            if math.isnan(prev):
                if nan_handling == "pollute":
                    scores[sym] = float("nan")  # NaN 传染
                else:
                    # median/skip 模式下，已有 NaN 视为 0 重新累加
                    scores[sym] = weight * rank_score
            else:
                scores[sym] = prev + weight * rank_score

        # 缺失因子的标的处理
        n_missing = 0
        for c in candidates:
            is_missing = (
                c.symbol not in factor_values
                or math.isnan(factor_values.get(c.symbol, 0.0))
            )
            if is_missing:
                n_missing += 1
                if nan_handling == "pollute":
                    scores[c.symbol] = float("nan")
                elif nan_handling == "median":
                    # 给中位排名分，不惩罚也不奖励
                    prev = scores.get(c.symbol, 0.0)
                    if math.isnan(prev):
                        prev = 0.0
                    scores[c.symbol] = prev + weight * median_rank_score
                else:  # "skip"
                    # 保留已有分数不变
                    if c.symbol not in scores:
                        scores[c.symbol] = 0.0
            elif c.symbol not in scores:
                # 值为 ±inf 的标的未进入排名，给 0 分（排最末但不排除）
                scores[c.symbol] = scores.get(c.symbol, 0.0)

        # 当缺失率 > 20% 时发出警告
        if n_missing > 0 and len(candidates) > 0:
            miss_pct = n_missing / len(candidates) * 100
            if miss_pct > 20:
                import logging
                logging.getLogger(__name__).warning(
                    f"rank_normalize: {n_missing}/{len(candidates)} "
                    f"({miss_pct:.0f}%) 候选标的缺失因子值"
                )

    @staticmethod
    def rank_sort(scores: dict[str, float]) -> list[str]:
        """对综合分降序排列，NaN 分数的标的排在最后并被过滤。

        Args:
            scores: {symbol: composite_score} 字典

        Returns:
            排序后的 symbol 列表（不含 NaN 分数的标的）
        """
        valid = [(sym, s) for sym, s in scores.items() if math.isfinite(s)]
        valid.sort(key=lambda x: x[1], reverse=True)
        return [sym for sym, _ in valid]

    # ------------------------------------------------------------------
    # 子类可覆盖的钩子
    # ------------------------------------------------------------------

    def _on_start_extra(self) -> None:
        """子类 on_start 额外初始化。"""

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """每 bar 更新内部状态（在 _update_history 之前调用）。"""

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        """子类额外过滤条件。返回 True 表示保留。"""
        return True

    def _custom_exit_check(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """子类特殊退出逻辑。"""
        return []


# ---------------------------------------------------------------------------
# 共用工具函数
# ---------------------------------------------------------------------------

def days_since_listed(list_date_str: str, now: datetime) -> int:
    """计算上市至今天数。"""
    try:
        ld = datetime.strptime(str(list_date_str)[:8], "%Y%m%d")
        return max((now - ld).days, 0)
    except (ValueError, TypeError):
        return 9999
