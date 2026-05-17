"""可转债轮动策略基类

将所有 CB 轮动策略的共性逻辑抽取到此处，消除代码重复：
- 通用预过滤 pipeline（价格/流动性/发行规模/到期日/ST）
- 止损检查
- 调仓判断
- 轮动指令生成（卖出不在目标池 → 等权买入新目标）
- on_start / on_fill 钩子

各子类只需实现：
- `_build_config()`: 返回 CBRotationConfig
- `_score_candidates()`: 对已过滤候选池打分/排名
- （可选）`_extra_filter()`: 策略特有的额外过滤条件
- （可选）`_custom_exit_check()`: 策略特有的退出逻辑（如溢价回归）
- （可选）`_on_bar_update_state()`: 每 bar 更新内部状态（如历史窗口）

重要设计决策（对齐 copilot-instructions.md 第 7.1 条）：
- 统一使用 issue_size 替代 remain_size 做规模过滤，消除前视偏差
- 策略层只输出 OrderIntent，不直接修改持仓/资金
- 过滤逻辑与排序逻辑分离，便于测试和审计
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 候选标的信息
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CandidateInfo:
    """已通过预过滤的候选转债信息。

    策略的 _score_candidates() 接收此列表，负责打分/排名。
    """

    symbol: str
    close: float
    metadata: dict[str, Any]

    # 常用字段快捷访问（从 metadata 解析）
    amount: float = 0.0
    issue_size: float = float("inf")
    cb_over_rate: float | None = None
    cb_value: float | None = None
    conv_price: float | None = None  # 当前转股价（元）
    maturity_date: str = ""
    list_date: str = ""
    stk_short_name: str = ""
    rating: str = ""  # 信用评级（AAA/AA+/AA/AA-/A+/A 等）


# ---------------------------------------------------------------------------
# 通用配置基类
# ---------------------------------------------------------------------------

@dataclass
class CBRotationConfig:
    """可转债轮动策略通用配置。

    子类可继承并添加策略特有字段。所有共性字段在此定义。
    """

    # --- 持仓 ---
    top_k: int = 20
    rebalance_days: int = 5

    # --- 通用筛选 ---
    max_price: float = 130.0
    min_price: float = 0.0           # 最低价格（0=不过滤），低于此值视为信用风险
    min_turnover: float = 50.0       # 最小成交额（万元）
    min_issue_size: float = 0.0      # 最小发行规模（万元），替代 remain_size
    max_issue_size: float = float("inf")  # 最大发行规模（万元）
    min_days_to_maturity: int = 30
    max_days_to_maturity: int = 0          # 最大到期天数（0=不过滤）
    exclude_st: bool = True

    # --- 信用评级筛选 ---
    min_rating: str = ""               # 最低信用评级（如 "AA"），空=不过滤
    exclude_unrated: bool = False       # 是否剔除无评级的转债

    # --- 强赎风控 ---
    force_redeem_threshold: float = 130.0   # 转股价值超此值视为强赎风险
    force_redeem_days: int = 8              # 连续 N 日后强制退出
    exclude_called: bool = True             # 是否剔除已公告实施强赎
    exclude_force_redeem_pending: bool = False  # 是否剔除已满足强赎条件的转债
    force_redeem_cooldown: int = 20         # 强赎退出后冷却期（交易日），0=不限制
    force_redeem_penalty_start: float = 0.0  # 渐进惩罚起点(cb_value)，0=不启用

    # --- Delta zone filter ---
    min_cb_value: float = 0.0               # 最低转股价值，低于此值剔除（0=不过滤）
    max_cb_value: float = 0.0               # 最高转股价值，高于此值剔除（0=不过滤）

    # --- 风控 ---
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0      # 追踪止损比例, 0=不启用

    # --- 集中度风控 ---
    min_positions: int = 5  # 最少持仓数：候选不足时按比例缩仓，防极端集中
    min_candidates_to_trade: int = 0  # 最低交易候选数：候选低于此值仅卖出不买入，0=不启用

    # --- 换手控制 ---
    buffer_k: int = 0  # 缓冲区大小：已持仓标的在 top_k+buffer_k 内则保留，减少换手

    # --- 仓内再平衡（V4e） ---
    rebalance_to_equal: bool = False       # 是否对保留持仓做等权再平衡
    min_weight_pct: float = 0.0            # 残余清理阈值：权重低于此值强制卖出，0=不清理
    drift_threshold: float = 0.0           # 漂移修正阈值：超配等权倍数超此值则卖出超配部分，0=不修正

    # --- 规模软约束 ---
    size_preference: str = "hard"          # "hard"=硬截断(默认) / "soft"=分段衰减 / "none"=不过滤
    size_soft_cap: float = 20000.0         # 软上限（万元）：低于此值权重=1.0
    size_hard_cap: float = 60000.0         # 硬上限（万元）：高于此值权重=floor
    size_floor_weight: float = 0.2         # 最低权重（不为0，大盘仍有机会）

    # --- 自适应降级（候选枯竭时放宽过滤器）---
    fallback_max_issue_size: float = 0.0   # 降级规模上限（万元），0=不放宽
    fallback_max_premium: float = 0.0      # 降级溢价率上限（%），0=不放宽
    hold_on_empty: bool = True             # 候选为空时保持现有持仓（不清仓）

    # --- 交易 ---
    lot_size: int = 10


# ---------------------------------------------------------------------------
# CB 轮动策略基类
# ---------------------------------------------------------------------------

class CBRotationStrategy(Strategy):
    """可转债轮动策略基类。

    实现了通用的 on_bar() 流程：
    1. _on_bar_update_state() — 更新内部状态（历史窗口等）
    2. 止损检查 + _custom_exit_check()
    3. 调仓判断 (_should_rebalance)
    4. 预过滤 → CandidateInfo 列表
    5. _score_candidates() → 子类排名
    6. 生成轮动指令

    子类必须实现：
    - config 属性（返回 CBRotationConfig 或其子类）
    - _score_candidates(candidates) → list[(symbol, score)]
    """

    @property
    @abstractmethod
    def config(self) -> CBRotationConfig:
        """返回策略配置。"""
        ...

    @abstractmethod
    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """对候选池打分并返回排名后的 symbol 列表（最多 top_k 个）。

        Args:
            candidates: 已通过预过滤的候选列表
            context: 当前策略上下文

        Returns:
            按优先级排序的 symbol 列表（长度 <= config.top_k）
        """
        ...

    def __init__(self) -> None:
        self._bar_count: int = 0
        self._entry_prices: dict[str, float] = {}
        self._peak_prices: dict[str, float] = {}  # 追踪止损用最高价
        # 强赎连续计数器：symbol -> 连续 cb_value >= threshold 的天数
        self._force_redeem_counter: dict[str, int] = {}
        # 强赎冷却期：symbol -> 剩余冷却天数（递减到 0 后解除）
        self._force_redeem_cooldown_remaining: dict[str, int] = {}
        # 自适应降级状态
        self._effective_max_age: int = 0  # 由子类在 on_start 中设置
        # 过滤漏斗结构化日志（A1-2 诊断用）
        self._filter_funnel_log: list[dict] = []

    def on_start(self) -> None:
        """策略启动时初始化内部状态。

        重置 bar 计数器、入场价格记录和强赎连续计数器，
        并调用子类的 ``_on_start_extra()`` 钩子完成额外初始化。
        """
        self._bar_count = 0
        self._entry_prices.clear()
        self._peak_prices.clear()
        self._force_redeem_counter.clear()
        self._force_redeem_cooldown_remaining.clear()
        self._filter_funnel_log.clear()
        self._on_start_extra()

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        """处理成交回报，更新入场价格记录。

        Args:
            symbol: 标的代码
            side: 买卖方向（BUY/SELL）
            qty: 成交数量
            price: 成交价格
        """
        if side == Side.BUY:
            self._entry_prices[symbol] = price
            self._peak_prices[symbol] = price
        elif side == Side.SELL:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """每 bar 核心处理流程（模板方法）。

        按顺序执行：状态更新 → 止损检查 → 强赎风控 → 子类退出 →
        调仓判断 → 预过滤 → 子类排名 → 生成轮动指令。

        Args:
            bars: 当前 bar 数据，``{symbol: Bar}``
            context: 策略上下文（持仓、资金、时间等）

        Returns:
            本 bar 产生的订单意图列表
        """
        self._bar_count += 1
        cfg = self.config
        intents: list[OrderIntent] = []

        # --- 1. 子类状态更新（如历史窗口）---
        self._on_bar_update_state(bars, context)

        # --- 2. 止损检查 ---
        intents.extend(self._check_stop_loss(bars, context))

        # --- 2.5. 强赎风控：更新计数器 + 触发退出 ---
        intents.extend(self._check_force_redeem(bars, context))

        # --- 3. 子类特殊退出逻辑 ---
        intents.extend(self._custom_exit_check(bars, context))

        # --- 4. 调仓判断 ---
        if not self._should_rebalance():
            return intents

        # --- 5. 预过滤（含自适应降级）---
        candidates = self._filter_with_fallback(bars, context)

        # --- 6. 子类排名 ---
        ranked_targets = self._score_candidates(candidates, context)

        # --- 6.5. 分散度日志（子类设置 _last_dispersion 时输出）---
        disp = getattr(self, "_last_dispersion", None)
        if disp is not None:
            logger.info(
                f"[{self.name}] dispersion: spread={disp.score_spread:.4f} "
                f"cv={disp.score_cv:.3f} density={disp.candidate_density:.1f} "
                f"n_cand={disp.n_candidates} "
                f"s@k={disp.score_at_k:.4f} s@2k={disp.score_at_2k:.4f}"
            )

        # --- 7. 候选为空时保持现有持仓（仅适用于过滤器筛空，不含策略主动清仓）---
        if not ranked_targets and cfg.hold_on_empty and not candidates:
            current_held = {s for s, q in context.positions.items() if q > 0}
            if current_held:
                logger.info(
                    f"[{self.name}] 候选为空, hold_on_empty=True, "
                    f"保持现有{len(current_held)}只持仓不变"
                )
                return intents  # 仅返回止损/强赎指令，不做轮动

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
        """是否应在当前 bar 调仓。"""
        return (self._bar_count - 1) % self.config.rebalance_days == 0

    def _check_stop_loss(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """检查持仓止损。

        使用 bar.low 作为当日触发价格：更保守，与实盘一致。
        日线策略中若当日最低价跌破止损价，次日开盘平仓。
        （历史修复：原实现使用 bar.close，可能漏掉当日急跌后反弹的
        止损信号。使用 bar.low 确保不遗漏任何已达止损阈值的 bar。）

        数据防护：bar.low <= 0 时跳过止损检查（CB 面值 100 元，
        low=0 为数据异常，不应触发假止损）。
        """
        cfg = self.config
        intents: list[OrderIntent] = []
        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue
            entry = self._entry_prices.get(symbol)

            # 数据防护：bar.low <= 0 为异常数据，跳过止损检查
            if bar.low <= 0:
                logger.warning(
                    f"[{self.name}] {symbol} bar.low={bar.low} ≤ 0，"
                    f"疑似数据异常，跳过止损检查"
                )
                continue

            # 固定止损 (stop_loss_pct <= 0 表示禁用)
            if cfg.stop_loss_pct > 0 and entry:
                stop_price = entry * (1 - cfg.stop_loss_pct)
                if bar.low <= stop_price:
                    intents.append(OrderIntent(
                        symbol=symbol, side=Side.SELL, qty=qty,
                        reason=f"止损: low={bar.low:.2f} < {stop_price:.2f} (entry={entry:.2f})",
                    ))
                    continue

            # 追踪止损 (trailing stop)
            if cfg.trailing_stop_pct > 0 and entry:
                peak = self._peak_prices.get(symbol, entry)
                if bar.high > peak:
                    peak = bar.high
                    self._peak_prices[symbol] = peak
                trail_price = peak * (1 - cfg.trailing_stop_pct)
                if bar.low <= trail_price:
                    intents.append(OrderIntent(
                        symbol=symbol, side=Side.SELL, qty=qty,
                        reason=(
                            f"追踪止损: low={bar.low:.2f} < "
                            f"{trail_price:.2f} (peak={peak:.2f})"
                        ),
                    ))
                    continue
        return intents

    def _check_force_redeem(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """强赎风控：更新连续计数器 + 触发退出 + 冷却期管理。

        逻辑：
        1. 冷却期递减：每 bar 对所有冷却中的标的递减 1 天
        2. 如果持仓的 cb_value >= force_redeem_threshold，连续计数+1
        3. 如果连续天数 >= force_redeem_days，发出卖出指令并设置冷却期
        4. 如果 is_called 为 True（已公告强赎），立即卖出并设置冷却期
        """
        cfg = self.config
        intents: list[OrderIntent] = []

        # 冷却期递减（每 bar 递减 1，到 0 时移除）
        expired = [s for s, d in self._force_redeem_cooldown_remaining.items() if d <= 1]
        for s in expired:
            self._force_redeem_cooldown_remaining.pop(s)
        for s in self._force_redeem_cooldown_remaining:
            self._force_redeem_cooldown_remaining[s] -= 1

        for symbol, qty in list(context.positions.items()):
            if qty <= 0:
                continue
            bar = bars.get(symbol)
            if bar is None:
                continue

            meta = bar.metadata or {}

            # 已公告强赎 → 立即退出
            if meta.get("is_called") is True:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=f"已公告强赎, status={meta.get('force_redeem_status', '')}",
                ))
                self._force_redeem_counter.pop(symbol, None)
                if cfg.force_redeem_cooldown > 0:
                    self._force_redeem_cooldown_remaining[symbol] = cfg.force_redeem_cooldown
                continue

            # cb_value 连续计数
            cb_value = meta.get("cb_value")
            if cb_value is not None and cb_value >= cfg.force_redeem_threshold:
                self._force_redeem_counter[symbol] = (
                    self._force_redeem_counter.get(symbol, 0) + 1
                )
            else:
                self._force_redeem_counter[symbol] = 0

            # 达到连续天数阈值 → 退出
            if self._force_redeem_counter.get(symbol, 0) >= cfg.force_redeem_days:
                intents.append(OrderIntent(
                    symbol=symbol, side=Side.SELL, qty=qty,
                    reason=(
                        f"强赎预警退出: cb_value={cb_value:.1f} "
                        f"连续 {self._force_redeem_counter[symbol]} 日 "
                        f">= {cfg.force_redeem_threshold}"
                    ),
                ))
                self._force_redeem_counter.pop(symbol, None)
                if cfg.force_redeem_cooldown > 0:
                    self._force_redeem_cooldown_remaining[symbol] = cfg.force_redeem_cooldown

        return intents

    @staticmethod
    def compute_size_soft_weight(
        issue_size: float,
        soft_cap: float = 20000.0,
        hard_cap: float = 60000.0,
        floor_weight: float = 0.2,
    ) -> float:
        """分段线性规模软权重函数。

        w(x) = 1.0                                         if x <= soft_cap
        w(x) = 1.0 - (1.0 - floor) * (x - soft_cap)       if soft_cap < x <= hard_cap
                      / (hard_cap - soft_cap)
        w(x) = floor_weight                                 if x > hard_cap

        Args:
            issue_size: 发行规模（万元）
            soft_cap: 软上限，低于此值权重=1.0
            hard_cap: 硬上限，高于此值权重=floor
            floor_weight: 最低权重（>0，大盘仍有机会入选）

        Returns:
            [floor_weight, 1.0] 范围内的权重
        """
        if issue_size <= soft_cap:
            return 1.0
        if issue_size >= hard_cap:
            return floor_weight
        # 线性衰减区间
        ratio = (issue_size - soft_cap) / (hard_cap - soft_cap)
        return 1.0 - (1.0 - floor_weight) * ratio

    def _filter_candidates(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[CandidateInfo]:
        """通用预过滤 pipeline：价格/流动性/发行规模/到期日/ST。

        注意：统一使用 issue_size 替代 remain_size，消除前视偏差。
        每次调仓输出过滤漏斗日志，便于诊断候选不足问题。
        """
        cfg = self.config
        candidates: list[CandidateInfo] = []

        # 过滤漏斗计数器
        n_total = len(bars)
        n_price = 0
        n_turnover = 0
        n_issue_size = 0
        n_maturity = 0
        n_st = 0
        n_rating = 0
        n_called = 0
        n_delta_zone = 0
        n_extra = 0

        for symbol, bar in bars.items():
            meta = bar.metadata or {}

            # 价格
            if bar.close <= 0 or bar.close > cfg.max_price:
                n_price += 1
                continue
            if cfg.min_price > 0 and bar.close < cfg.min_price:
                n_price += 1
                continue

            # 流动性（成交额）
            amount = meta.get("amount", 0)
            if amount < cfg.min_turnover:
                n_turnover += 1
                continue

            # 发行规模（静态字段，无前视偏差）
            issue_size = meta.get("issue_size", float("inf"))
            if cfg.size_preference == "hard":
                # 传统硬截断模式
                if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
                    n_issue_size += 1
                    continue
            else:
                # soft/none 模式：仅检查 min_issue_size（下限仍为硬性约束）
                if issue_size < cfg.min_issue_size:
                    n_issue_size += 1
                    continue

            # 到期日
            mat = meta.get("maturity_date", "")
            if mat:
                dtm = days_to_maturity(mat, context.current_time)
                if dtm < cfg.min_days_to_maturity:
                    n_maturity += 1
                    continue
                if cfg.max_days_to_maturity > 0 and dtm > cfg.max_days_to_maturity:
                    n_maturity += 1
                    continue

            # ST 排除
            stk_name = meta.get("stk_short_name", "")
            if cfg.exclude_st and "ST" in stk_name.upper():
                n_st += 1
                continue

            # 信用评级过滤
            rating_val = meta.get("rating", "")
            if cfg.exclude_unrated and not rating_val:
                n_rating += 1
                continue
            if cfg.min_rating and rating_val:
                if _rating_score(rating_val) < _rating_score(cfg.min_rating):
                    n_rating += 1
                    continue

            # 已公告强赎排除
            if cfg.exclude_called and meta.get("is_called") is True:
                n_called += 1
                continue

            # 已满足强赎条件（尚未公告实施，但可能随时强赎）
            if cfg.exclude_force_redeem_pending:
                fr_status = meta.get("force_redeem_status", "")
                if fr_status in ("已满足强赎条件", "公告提示强赎"):
                    n_called += 1
                    continue

            # 强赎冷却期排除：近期因强赎退出的标的不得重新入选
            if symbol in self._force_redeem_cooldown_remaining:
                n_called += 1
                continue

            # Delta zone filter: 转股价值过低/过高的标的剔除
            _cb_val = meta.get("cb_value")
            if _cb_val is not None:
                if cfg.min_cb_value > 0 and _cb_val < cfg.min_cb_value:
                    n_delta_zone += 1
                    continue
                if cfg.max_cb_value > 0 and _cb_val > cfg.max_cb_value:
                    n_delta_zone += 1
                    continue

            # 构建 CandidateInfo
            ci = CandidateInfo(
                symbol=symbol,
                close=bar.close,
                metadata=meta,
                amount=amount,
                issue_size=issue_size,
                cb_over_rate=meta.get("cb_over_rate"),
                cb_value=meta.get("cb_value"),
                conv_price=meta.get("conv_price"),
                maturity_date=mat,
                list_date=meta.get("list_date", ""),
                stk_short_name=stk_name,
                rating=meta.get("rating", ""),
            )

            # 子类额外过滤（如溢价率上限、次新日期等）
            if not self._extra_filter(ci, context):
                n_extra += 1
                continue

            candidates.append(ci)

        # 输出过滤漏斗日志（调仓日可见）
        n_pass = len(candidates)
        logger.info(
            f"[{self.name}] 过滤漏斗: 总{n_total} → "
            f"价格-{n_price} 流动性-{n_turnover} 规模-{n_issue_size} "
            f"到期-{n_maturity} ST-{n_st} 评级-{n_rating} 强赎-{n_called} "
            f"delta-{n_delta_zone} 策略-{n_extra} → 通过={n_pass}"
        )

        # 结构化漏斗记录（供诊断脚本使用）
        self._filter_funnel_log.append({
            "date": context.current_time.strftime("%Y-%m-%d"),
            "n_total": n_total,
            "n_price": n_price,
            "n_turnover": n_turnover,
            "n_issue_size": n_issue_size,
            "n_maturity": n_maturity,
            "n_st": n_st,
            "n_rating": n_rating,
            "n_called": n_called,
            "n_delta_zone": n_delta_zone,
            "n_extra": n_extra,
            "n_pass": n_pass,
        })

        return candidates

    def _filter_with_fallback(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[CandidateInfo]:
        """预过滤 + 自适应降级。

        当候选数 < min_candidates 时，逐步放宽过滤条件并重试（当前调仓日生效）：
        1. 先放宽 max_age（+90天，直到 fallback_max_age_days）
        2. 再放宽 max_issue_size（到 fallback_max_issue_size）
        3. 再放宽 max_premium（到 fallback_max_premium）

        当候选充足时逐步恢复到原始参数。
        """
        cfg = self.config
        candidates = self._filter_candidates(bars, context)
        n = len(candidates)

        # 检查是否需要降级
        min_needed = getattr(cfg, "min_candidates", 3)
        if n >= min_needed:
            # 候选充足 → 逐步恢复（不要一次跳回）
            self._maybe_tighten_filters()
            return candidates

        # 候选不足 → 尝试放宽并重试
        relaxed = self._relax_filters_one_step()
        if not relaxed:
            return candidates  # 已到极限

        # 用放宽后的参数重新过滤
        new_candidates = self._filter_candidates(bars, context)
        new_n = len(new_candidates)
        if new_n > n:
            logger.info(
                f"[{self.name}] 降级生效: 候选 {n} → {new_n}"
            )
            return new_candidates
        return new_candidates if new_candidates else candidates

    def _relax_filters_one_step(self) -> bool:
        """放宽一步过滤参数。返回 True 表示有变化。"""
        cfg = self.config
        changed = False

        # 1. 放宽 max_age
        if hasattr(self, "_effective_max_age"):
            max_age_target = getattr(cfg, "fallback_max_age_days", 0)
            base_max_age = getattr(cfg, "max_age_days", 180)
            if max_age_target > 0 and self._effective_max_age < max_age_target:
                self._effective_max_age = min(
                    max_age_target,
                    self._effective_max_age + 180,
                )
                logger.info(
                    f"[{self.name}] 降级: max_age → {self._effective_max_age}天"
                )
                changed = True
                return changed  # 每次只放宽一个维度

        # 2. 放宽 max_issue_size
        if cfg.fallback_max_issue_size > 0 and cfg.max_issue_size < cfg.fallback_max_issue_size:
            # 临时扩大
            old = cfg.max_issue_size
            cfg.max_issue_size = cfg.fallback_max_issue_size
            if cfg.max_issue_size != old:
                logger.info(
                    f"[{self.name}] 降级: max_issue_size {old:.0f} → {cfg.max_issue_size:.0f}"
                )
                changed = True
                return changed

        # 3. 放宽 max_premium
        if cfg.fallback_max_premium > 0:
            current_max_premium = getattr(self, "_effective_max_premium", 0)
            if current_max_premium > 0 and current_max_premium < cfg.fallback_max_premium:
                self._effective_max_premium = cfg.fallback_max_premium
                logger.info(
                    f"[{self.name}] 降级: max_premium → {self._effective_max_premium:.0f}%"
                )
                changed = True

        return changed

    def _maybe_tighten_filters(self) -> None:
        """候选充足时逐步恢复过滤参数（每次收紧一步，不一次跳回）。"""
        cfg = self.config
        base_max_age = getattr(cfg, "max_age_days", 180)
        if hasattr(self, "_effective_max_age") and self._effective_max_age > base_max_age:
            old = self._effective_max_age
            self._effective_max_age = max(
                base_max_age,
                self._effective_max_age - 90,
            )
            if self._effective_max_age != old:
                logger.info(
                    f"[{self.name}] 恢复: max_age {old} → {self._effective_max_age}天"
                )

    def _generate_rebalance(
        self,
        ranked_targets: list[str],
        context: StrategyContext,
        bars: dict[str, Bar],
    ) -> list[OrderIntent]:
        """生成轮动指令：卖出不在目标池 → 等权买入新目标。

        支持 buffer_k 缓冲机制：
        - buffer_k=0 (默认)：严格按 ranked_targets 调仓
        - buffer_k>0：已持仓标的若在 ranked_targets 的前 top_k+buffer_k 名内则保留，
          仅将排名外的持仓卖出，再从前 top_k 中选新标的补足至 top_k。
          这减少了因边际排名波动导致的不必要换手。

        集中度保护：
        - 当候选数 < min_positions 时，仅用 (候选数/min_positions) 比例的资金建仓，
          防止极端集中（如仅1只CB导致单日 -47% 崩溃）。

        按 ranked_targets 排名顺序买入，确保确定性。
        """
        cfg = self.config
        intents: list[OrderIntent] = []
        current_held = {s for s, q in context.positions.items() if q > 0}

        if cfg.buffer_k > 0 and current_held:
            # --- buffer 模式 ---
            buffer_limit = cfg.top_k + cfg.buffer_k
            # 扩展候选池：在 buffer 范围内的已持仓标的可以保留
            extended_set = set(ranked_targets[:buffer_limit])
            # 已持仓 且 在扩展池内 → 保留
            keep = current_held & extended_set
            # 已持仓 但 不在扩展池内 → 卖出
            to_sell = current_held - extended_set
            # 需要补足的数量
            n_need = max(0, cfg.top_k - len(keep))
            # 从 ranked_targets 前 top_k 中选未持仓的新标的
            to_buy_candidates = [
                s for s in ranked_targets[:cfg.top_k] if s not in current_held
            ]
            to_buy = to_buy_candidates[:n_need]
            # 最终目标集 = 保留 + 新买入
            target_set = keep | set(to_buy)
        else:
            # --- 严格模式（无 buffer）---
            target_set = set(ranked_targets)
            to_sell = {s for s in current_held if s not in target_set}
            to_buy = [s for s in ranked_targets if s not in current_held]
            keep = target_set & current_held  # 保留集合（供残余清理使用）

        # --- V4e Layer 1: 残余清理（rebalance_to_equal 启用时跳过） ---
        # 权重低于 min_weight_pct 的保留持仓强制卖出，释放席位和资金
        residual_sell: set[str] = set()
        if cfg.min_weight_pct > 0 and current_held and not cfg.rebalance_to_equal:
            nav = context.cash
            for s, q in context.positions.items():
                if q > 0:
                    b = bars.get(s)
                    nav += q * (b.close if b else 0)
            if nav > 0:
                for s in list(keep):
                    q = context.positions.get(s, 0)
                    b = bars.get(s)
                    if q > 0 and b and b.close > 0:
                        w = (q * b.close) / nav
                        if w < cfg.min_weight_pct:
                            residual_sell.add(s)
            if residual_sell:
                to_sell = to_sell | residual_sell
                # 从 keep / target_set 中移除，腾出席位给新标的
                keep = keep - residual_sell
                target_set = target_set - residual_sell
                # 补足新标的
                extra_need = len(residual_sell)
                existing_buy = set(to_buy)
                for s in ranked_targets[:cfg.top_k]:
                    if extra_need <= 0:
                        break
                    if s not in current_held and s not in existing_buy:
                        to_buy.append(s)
                        target_set.add(s)
                        extra_need -= 1
                logger.info(
                    f"[{self.name}] 残余清理: 卖出{len(residual_sell)}只"
                    f" 权重<{cfg.min_weight_pct:.1%}: "
                    f"{sorted(residual_sell)}"
                )

        # --- V4e Layer 2: 漂移修正（rebalance_to_equal 启用时跳过） ---
        drift_sells: list[OrderIntent] = []
        drift_sell_value = 0.0
        if cfg.drift_threshold > 0 and current_held and not cfg.rebalance_to_equal:
            # 计算当前 NAV（含现金）
            nav = context.cash
            held_values: dict[str, float] = {}
            for s, q in context.positions.items():
                if q > 0:
                    b = bars.get(s)
                    val = q * (b.close if b else 0)
                    held_values[s] = val
                    nav += val
            # 目标集中保留的持仓做漂移检查
            final_keep = (keep if cfg.buffer_k > 0 else (target_set & current_held)) - to_sell
            n_target = len(final_keep) + len(to_buy)
            if nav > 0 and n_target > 0:
                equal_w = 1.0 / n_target
                for s in final_keep:
                    val = held_values.get(s, 0)
                    w = val / nav
                    if w > equal_w * (1 + cfg.drift_threshold):
                        # 超配：卖出超出等权目标的部分
                        target_val = nav * equal_w
                        excess_val = val - target_val
                        b = bars.get(s)
                        if b and b.close > 0:
                            sell_qty = int(excess_val / b.close / cfg.lot_size) * cfg.lot_size
                            if sell_qty >= cfg.lot_size:
                                drift_sells.append(OrderIntent(
                                    symbol=s, side=Side.SELL, qty=sell_qty,
                                    reason=f"{self.name}漂移修正卖出",
                                ))
                                drift_sell_value += sell_qty * b.close

        # 卖出
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

        # 追加漂移修正卖单
        intents.extend(drift_sells)
        sell_value += drift_sell_value

        # 买入新目标
        if not to_buy and not cfg.rebalance_to_equal:
            return intents

        available = context.cash + sell_value * 0.995  # 与引擎 sell_credit_discount 一致

        # --- 集中度硬门禁 ---
        total_target_count = len(target_set)
        if cfg.min_candidates_to_trade > 0 and total_target_count < cfg.min_candidates_to_trade:
            logger.warning(
                f"[{self.name}] 候选{total_target_count}只 < "
                f"最低交易阈值{cfg.min_candidates_to_trade}，本期仅卖出不买入"
            )
            return intents  # 只执行卖出，不买入新标的

        # --- 集中度保护 ---
        if total_target_count < cfg.min_positions and cfg.min_positions > 0:
            scale = total_target_count / cfg.min_positions
            available *= scale
            logger.info(
                f"[{self.name}] 集中度保护: 候选{total_target_count}只"
                f" < min_positions={cfg.min_positions}, "
                f"资金缩减至 {scale:.0%}"
            )

        if cfg.rebalance_to_equal and keep:
            # --- V4e 等权再平衡：对 keep 持仓补仓/减仓至等权 ---
            # 用原始 available（不受集中度保护缩减）计算 NAV
            raw_available = context.cash + sell_value * 0.995
            keep_vals: dict[str, float] = {}
            total_nav = raw_available
            for s in keep:
                q = context.positions.get(s, 0)
                b = bars.get(s)
                if q > 0 and b and b.close > 0:
                    v = q * b.close
                    keep_vals[s] = v
                    total_nav += v

            n_target = max(total_target_count, 1)
            target_val = total_nav / n_target
            # 容差：drift_threshold>0 时用其值，否则默认 20%
            tol = cfg.drift_threshold if cfg.drift_threshold > 0 else 0.20

            # 1) 减仓超配的 keep 持仓（释放现金）
            trim_value = 0.0
            for s in keep:
                cur = keep_vals.get(s, 0)
                if cur > target_val * (1 + tol):
                    excess = cur - target_val
                    b = bars.get(s)
                    if b and b.close > 0:
                        sell_qty = int(excess / b.close / cfg.lot_size) * cfg.lot_size
                        if sell_qty >= cfg.lot_size:
                            intents.append(OrderIntent(
                                symbol=s, side=Side.SELL, qty=sell_qty,
                                reason=f"{self.name}等权减仓",
                            ))
                            trim_value += sell_qty * b.close

            raw_available += trim_value * 0.995

            # 2) 收集所有需要买入的需求
            buy_demands: list[tuple[str, float]] = []
            for s in to_buy:
                buy_demands.append((s, target_val))
            for s in keep:
                cur = keep_vals.get(s, 0)
                deficit = target_val - cur
                if deficit > target_val * tol:
                    buy_demands.append((s, deficit))

            if buy_demands:
                total_demand = sum(d for _, d in buy_demands)
                scale_f = min(1.0, raw_available / total_demand) if total_demand > 0 else 0.0
                for symbol, demand in buy_demands:
                    bar = bars.get(symbol)
                    if bar is None or bar.close <= 0:
                        continue
                    alloc = demand * scale_f
                    qty = int(alloc / bar.close / cfg.lot_size) * cfg.lot_size
                    if qty >= cfg.lot_size:
                        reason = (f"{self.name}等权补仓"
                                  if symbol in keep else f"{self.name}轮动买入")
                        intents.append(OrderIntent(
                            symbol=symbol, side=Side.BUY, qty=qty,
                            reason=reason,
                        ))
        else:
            # --- 原始等额分配（仅新标的） ---
            if not to_buy:
                return intents
            per_stock = available / len(to_buy)
            for symbol in to_buy:
                bar = bars.get(symbol)
                if bar is None or bar.close <= 0:
                    continue
                qty = int(per_stock / bar.close / cfg.lot_size) * cfg.lot_size
                if qty >= cfg.lot_size:
                    intents.append(OrderIntent(
                        symbol=symbol, side=Side.BUY, qty=qty,
                        reason=f"{self.name}轮动买入",
                    ))

        return intents

    # ------------------------------------------------------------------
    # 子类可覆盖的钩子
    # ------------------------------------------------------------------

    def _on_start_extra(self) -> None:
        """子类 on_start 额外初始化（如清空历史窗口）。"""
        pass

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """每 bar 更新内部状态（如成交额历史、价格历史）。"""
        pass

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """子类额外过滤条件。返回 True 表示保留。"""
        return True

    def _custom_exit_check(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> list[OrderIntent]:
        """子类特殊退出逻辑（如溢价回归、获利了结）。"""
        return []


# ---------------------------------------------------------------------------
# 共用工具函数
# ---------------------------------------------------------------------------

def days_to_maturity(maturity_str: str, now: datetime) -> int:
    """计算距到期日的天数。"""
    try:
        mat = datetime.strptime(maturity_str[:8], "%Y%m%d")
        return max((mat - now).days, 0)
    except (ValueError, TypeError):
        return 9999


def days_since_listed(list_date_str: str, now: datetime) -> int:
    """计算上市至今天数。"""
    try:
        ld = datetime.strptime(list_date_str[:8], "%Y%m%d")
        return max((now - ld).days, 0)
    except (ValueError, TypeError):
        return 9999


# 信用评级排序映射（数值越高评级越好）
_RATING_ORDER: dict[str, int] = {
    "AAA": 100,
    "AA+": 90,
    "AA": 80,
    "AA-": 70,
    "A+": 60,
    "A": 50,
    "A-": 40,
    "BBB+": 35,
    "BBB": 30,
    "BBB-": 25,
    "BB+": 20,
    "BB": 15,
    "BB-": 10,
    "B+": 8,
    "B": 5,
    "B-": 3,
    "CCC": 2,
    "CC": 1,
    "C": 0,
}


def _rating_score(rating: str) -> int:
    """将信用评级字符串转为可比较的数值分数。

    评级越高分数越高：AAA=100, AA+=90, ..., C=0。
    未识别的评级返回 -1（低于所有已知评级）。
    """

    return _RATING_ORDER.get(rating.strip().upper(), -1)


def rating_score(rating: str) -> int:
    """公开的评级分数函数（序数刻度 0-100）。

    用于评级比较与过滤，值越高评级越好。
    AAA=100, AA+=90, ..., C=0, 未识别=-1。

    注意：cb_enhanced_factors.rating_score() 返回 [0,1] 归一化版本，
    适合多因子排名融合场景。两者保持相同的排序语义。
    """

    return _rating_score(rating)
