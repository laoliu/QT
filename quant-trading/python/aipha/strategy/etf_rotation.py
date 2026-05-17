"""ETF 轮动策略

基于动量+估值+波动率的 ETF 轮动策略，支持宽基/行业/风格 ETF 轮动。

核心逻辑：
1. 每 rebalance_days 天调仓
2. 对候选 ETF 池计算多因子得分（动量/波动率/成交额）
3. 选 top_k 只 ETF 等权或反波动率加权持有
4. 支持趋势过滤（均线上方才可买入）
5. 支持止损/追踪止损

ETF 特点（vs 个股）：
- 无印花税，佣金极低
- 流动性极好（宽基 ETF 日均 10亿+）
- 无 ST/退市风险
- T+1 交易

设计遵循 copilot-instructions.md：
- 策略只输出 OrderIntent，不修改持仓/资金
- 配置驱动，参数可从 YAML 注入
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import (
    IntentType,
    OrderIntent,
    Strategy,
    StrategyContext,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class ETFRotationConfig:
    """ETF 轮动策略配置"""

    # --- 持仓 ---
    top_k: int = 3                    # 持有 ETF 数量
    rebalance_days: int = 20          # 调仓周期（交易日）

    # --- 因子权重 (Keller FAA 2013 五因子) ---
    weight_momentum: float = 0.35     # 动量因子权重
    weight_volatility: float = 0.25   # 低波动因子权重
    weight_volume: float = 0.10       # 成交活跃度权重
    weight_reversion: float = 0.00    # 短期反转权重
    weight_correlation: float = 0.30  # 相关性因子权重(C因子, Keller FAA)

    # --- 因子参数 ---
    momentum_lookback: int = 60       # 动量回看窗口（交易日）
    volatility_lookback: int = 20     # 波动率回看窗口
    reversion_lookback: int = 5       # 短期反转回看窗口
    trend_ma_period: int = 120        # 趋势过滤均线周期（0=不启用）

    # --- 风控 ---
    stop_loss_pct: float = 0.10       # 固定止损比例
    trailing_stop_pct: float = 0.08   # 追踪止损
    cash_reserve_pct: float = 0.02    # 现金缓冲
    max_single_pct: float = 0.50      # 单只 ETF 最大占比

    # --- 交易 ---
    lot_size: int = 100               # ETF 最小交易单位
    min_amount: float = 0.0           # 最低日均成交额过滤（万元）
    buffer_k: int = 1                 # 缓冲区：已持仓在 top_k+buffer_k 内保留

    # --- 加权模式 ---
    inverse_vol_weighting: bool = False  # True=反波动率加权, False=等权

    # --- 相关性因子 (C-factor, Keller FAA 2013) ---
    correlation_lookback: int = 60      # 相关性计算回看窗口(交易日)
    correlation_hard_exclude: float = 0.60  # 硬排除阈值: corr>此值不选入
    benchmark_returns_path: str = ""    # 核心策略基准收益率CSV路径

    # --- Regime Detection (P0b: 动态因子权重) ---
    regime_enabled: bool = False        # 是否启用制度检测
    regime_reference: str = "510300.SH"  # 市场参考 ETF (CSI300)
    regime_momentum_window: int = 60    # 制度判断动量窗口
    regime_bull_threshold: float = 0.05   # >5% 判定为 BULL
    regime_bear_threshold: float = -0.05  # <-5% 判定为 BEAR

    # BULL 制度: 增加动量, 减少防御
    bull_weight_momentum: float = 0.50
    bull_weight_volatility: float = 0.15
    bull_weight_correlation: float = 0.25
    bull_weight_volume: float = 0.10

    # BEAR 制度: 增加防御, 减少动量
    bear_weight_momentum: float = 0.10
    bear_weight_volatility: float = 0.50
    bear_weight_correlation: float = 0.25
    bear_weight_volume: float = 0.15


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("etf_rotation")
class ETFRotationStrategy(Strategy):
    """ETF FAA(Flexible Asset Allocation)轮动策略

    基于 Keller & Van Putten (2013) FAA 框架的五因子 ETF 轮动策略：
    - R: 中期动量（60日收益率）：趋势延续效应
    - V: 低波动率（20日标准差）：低风险异象
    - C: 相关性因子（与核心策略的60日滚动相关性）：分散化
    - 反转/成交量：辅助因子
    - 趋势过滤（均线上方）：牛市满仓，熊市减仓/空仓

    C 因子学术依据：加入后 SR ~0.9→~1.4，MDD ~25%→~12% (Keller 2013)
    """

    name = "etf_rotation"

    def __init__(
        self,
        config: ETFRotationConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self._cfg = config or ETFRotationConfig()
        # 从 kwargs 覆盖配置（YAML params 注入）
        for k, v in kwargs.items():
            if hasattr(self._cfg, k):
                setattr(self._cfg, k, type(getattr(self._cfg, k))(v))

        # 内部状态
        self._bar_count: int = 0
        self._price_history: dict[str, list[float]] = {}
        self._volume_history: dict[str, list[float]] = {}
        self._entry_prices: dict[str, float] = {}
        self._high_since_entry: dict[str, float] = {}

        # C-factor: 加载核心策略基准收益率
        self._benchmark_returns: dict[str, float] = {}  # date_str -> daily_return
        self._benchmark_dates: list[str] = []  # 排序后的日期列表
        self._current_regime: str = "NEUTRAL"  # P0b: 当前 regime 状态
        if self._cfg.benchmark_returns_path:
            self._load_benchmark_returns(self._cfg.benchmark_returns_path)

    def on_start(self) -> None:
        """初始化"""
        self._bar_count = 0
        self._price_history.clear()
        self._volume_history.clear()
        self._entry_prices.clear()
        self._high_since_entry.clear()

    def on_fill(
        self, symbol: str, side: Side, qty: int, price: float
    ) -> None:
        """成交回报"""
        if side == Side.BUY:
            self._entry_prices[symbol] = price
            self._high_since_entry[symbol] = price
        elif side == Side.SELL:
            # 检查是否全部卖出（根据持仓数量判断，而非价格）
            # 若 qty 等于持仓全量，清除 entry 记录
            # 注意：此处无法直接获取持仓量，保守做法是延迟清理
            # 在 on_bar 中通过 context.positions 检查并清理
            self._pending_sell_qty = getattr(
                self, '_pending_sell_qty', {}
            )
            self._pending_sell_qty[symbol] = (
                self._pending_sell_qty.get(symbol, 0) + qty
            )

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """每个 bar 的主逻辑"""
        self._bar_count += 1
        orders: list[OrderIntent] = []

        # 1. 更新价格/成交量历史
        self._update_history(bars)

        # 2. 更新追踪止损高点 + 清理已全部卖出的 entry 记录
        for sym, pos_qty in context.positions.items():
            if pos_qty > 0 and sym in bars:
                price = bars[sym].close
                prev_high = self._high_since_entry.get(sym, price)
                self._high_since_entry[sym] = max(prev_high, price)

        # 清理 pending sell 记录：若该标的已无持仓则清除 entry
        pending = getattr(self, '_pending_sell_qty', {})
        for sym in list(pending.keys()):
            if context.positions.get(sym, 0) <= 0:
                self._entry_prices.pop(sym, None)
                self._high_since_entry.pop(sym, None)
                del pending[sym]

        # 3. 检查止损
        stop_orders = self._check_stop_loss(bars, context)
        if stop_orders:
            orders.extend(stop_orders)
            return orders  # 止损优先，不调仓

        # 4. 调仓判断
        if self._bar_count % self._cfg.rebalance_days != 0:
            return orders

        # 5. 计算得分并选股
        max_lookback = max(
            self._cfg.momentum_lookback,
            self._cfg.volatility_lookback,
            self._cfg.trend_ma_period or 0,
        )
        if self._bar_count < max_lookback + 5:
            return orders  # 数据不足

        scored = self._score_etfs(bars, context)
        if not scored:
            return orders

        # 6. 确定目标持仓
        target_syms = self._select_targets(scored, context)

        # 7. 生成调仓指令
        orders = self._generate_rebalance(
            target_syms, bars, context
        )
        return orders

    # ------------------------------------------------------------------
    # 内部方法：Regime 检测 (P0b)
    # ------------------------------------------------------------------

    def _detect_regime(self) -> str:
        """基于参考 ETF 动量+均线检测市场制度。

        Returns:
            "BULL", "BEAR", 或 "NEUTRAL"
        """
        cfg = self._cfg
        ref = cfg.regime_reference
        hist = self._price_history.get(ref, [])
        window = cfg.regime_momentum_window

        if len(hist) < window + 1:
            return "NEUTRAL"

        # 60日动量
        momentum = hist[-1] / hist[-window] - 1.0

        # 均线趋势 (close vs MA120)
        ma_period = cfg.trend_ma_period or 120
        above_ma = True
        if len(hist) >= ma_period:
            ma = sum(hist[-ma_period:]) / ma_period
            above_ma = hist[-1] > ma

        if momentum > cfg.regime_bull_threshold and above_ma:
            return "BULL"
        elif momentum < cfg.regime_bear_threshold or (
            not above_ma and momentum < 0
        ):
            return "BEAR"
        return "NEUTRAL"

    def _get_regime_weights(self) -> tuple[float, float, float, float]:
        """根据当前 regime 返回调整后的因子权重。

        Returns:
            (momentum, volatility, correlation, volume) 权重元组
        """
        cfg = self._cfg
        if not cfg.regime_enabled:
            return (
                cfg.weight_momentum, cfg.weight_volatility,
                cfg.weight_correlation, cfg.weight_volume,
            )

        regime = self._detect_regime()
        self._current_regime = regime  # 记录以便日志

        if regime == "BULL":
            return (
                cfg.bull_weight_momentum, cfg.bull_weight_volatility,
                cfg.bull_weight_correlation, cfg.bull_weight_volume,
            )
        elif regime == "BEAR":
            return (
                cfg.bear_weight_momentum, cfg.bear_weight_volatility,
                cfg.bear_weight_correlation, cfg.bear_weight_volume,
            )
        return (
            cfg.weight_momentum, cfg.weight_volatility,
            cfg.weight_correlation, cfg.weight_volume,
        )

    # ------------------------------------------------------------------
    # 内部方法：历史维护
    # ------------------------------------------------------------------

    def _update_history(self, bars: dict[str, Bar]) -> None:
        """更新价格和成交量历史"""
        max_len = max(
            self._cfg.momentum_lookback,
            self._cfg.volatility_lookback,
            self._cfg.trend_ma_period or 1,
            self._cfg.regime_momentum_window if self._cfg.regime_enabled else 1,
            self._cfg.correlation_lookback,
        ) + 10

        for sym, bar in bars.items():
            if bar.close <= 0 or bar.is_suspended:
                continue
            hist = self._price_history.setdefault(sym, [])
            hist.append(bar.close)
            if len(hist) > max_len:
                self._price_history[sym] = hist[-max_len:]

            vol_hist = self._volume_history.setdefault(sym, [])
            amt = bar.volume * bar.close / 10000 if bar.volume else 0
            vol_hist.append(amt)
            if len(vol_hist) > 20:
                self._volume_history[sym] = vol_hist[-20:]

    # ------------------------------------------------------------------
    # 内部方法：基准加载与相关性计算 (C-factor)
    # ------------------------------------------------------------------

    def _load_benchmark_returns(self, path: str) -> None:
        """加载核心策略基准日收益率 CSV。

        CSV 格式: date,return (日期为 YYYY-MM-DD，return 为日收益率)
        """
        import csv
        from pathlib import Path

        p = Path(path)
        if not p.is_absolute():
            # 相对路径从项目根目录解析
            p = Path.cwd() / p
        if not p.exists():
            logger.warning("基准收益文件不存在: %s, C 因子将禁用", p)
            return

        with open(p, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = row["date"].strip()[:10]  # YYYY-MM-DD
                r = float(row["return"])
                self._benchmark_returns[d] = r
        self._benchmark_dates = sorted(self._benchmark_returns.keys())
        logger.info(
            "C-factor 基准加载: %d 天 (%s ~ %s)",
            len(self._benchmark_dates),
            self._benchmark_dates[0] if self._benchmark_dates else "N/A",
            self._benchmark_dates[-1] if self._benchmark_dates else "N/A",
        )

    def _calc_correlation_with_benchmark(
        self, sym: str, current_date: str
    ) -> float | None:
        """计算 ETF 与核心策略基准的滚动相关性。

        使用过去 correlation_lookback 天的日收益率的 Pearson 相关系数。
        返回 [-1, 1] 或 None (数据不足)。
        """
        cfg = self._cfg
        window = cfg.correlation_lookback
        hist = self._price_history.get(sym, [])
        if len(hist) < window + 1:
            return None
        if not self._benchmark_returns:
            return None

        # ETF 日收益率 (最近 window 天)
        etf_rets: list[float] = []
        for i in range(-window, 0):
            if hist[i - 1] > 0:
                etf_rets.append(hist[i] / hist[i - 1] - 1)
            else:
                etf_rets.append(0.0)

        # 获取对应日期的基准收益率
        # 使用 current_date 向前回溯 window 天
        try:
            idx = self._benchmark_dates.index(current_date)
        except ValueError:
            # 找最近的较早日期
            idx = -1
            for i, d in enumerate(self._benchmark_dates):
                if d <= current_date:
                    idx = i
            if idx < 0:
                return None

        start_idx = max(0, idx - window + 1)
        bm_dates_slice = self._benchmark_dates[start_idx:idx + 1]
        bm_rets = [self._benchmark_returns[d] for d in bm_dates_slice]

        # 对齐长度 (取较短者)
        min_len = min(len(etf_rets), len(bm_rets))
        if min_len < 20:  # 最少 20 天数据才算相关性
            return None
        etf_rets = etf_rets[-min_len:]
        bm_rets = bm_rets[-min_len:]

        # Pearson 相关系数
        n = min_len
        mean_e = sum(etf_rets) / n
        mean_b = sum(bm_rets) / n
        cov = sum(
            (etf_rets[i] - mean_e) * (bm_rets[i] - mean_b)
            for i in range(n)
        ) / n
        std_e = (sum((r - mean_e) ** 2 for r in etf_rets) / n) ** 0.5
        std_b = (sum((r - mean_b) ** 2 for r in bm_rets) / n) ** 0.5

        if std_e < 1e-10 or std_b < 1e-10:
            return 0.0
        return cov / (std_e * std_b)

    # ------------------------------------------------------------------
    # 内部方法：止损
    # ------------------------------------------------------------------

    def _check_stop_loss(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """检查止损条件"""
        orders: list[OrderIntent] = []
        cfg = self._cfg

        for sym, qty in list(context.positions.items()):
            if qty <= 0 or sym not in bars:
                continue
            price = bars[sym].close
            entry = self._entry_prices.get(sym)
            if not entry or entry <= 0:
                continue

            # 固定止损
            if cfg.stop_loss_pct > 0:
                loss = (price - entry) / entry
                if loss <= -cfg.stop_loss_pct:
                    orders.append(OrderIntent(
                        symbol=sym, side=Side.SELL, qty=qty,
                        intent_type=IntentType.ORDER,
                        limit_price=None,
                        reason=f"stop_loss:{loss:.1%}",
                        signal_strength=None,
                    ))
                    continue

            # 追踪止损
            if cfg.trailing_stop_pct > 0:
                high = self._high_since_entry.get(sym, entry)
                drawdown = (high - price) / high if high > 0 else 0
                if drawdown >= cfg.trailing_stop_pct:
                    orders.append(OrderIntent(
                        symbol=sym, side=Side.SELL, qty=qty,
                        intent_type=IntentType.ORDER,
                        limit_price=None,
                        reason=f"trailing_stop:{drawdown:.1%}",
                        signal_strength=None,
                    ))
        return orders

    # ------------------------------------------------------------------
    # 内部方法：ETF 评分
    # ------------------------------------------------------------------

    def _score_etfs(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[tuple[str, float]]:
        """对所有候选 ETF 打分（FAA 五因子：R+V+C+rev+vol）

        R115: 在 R+V 因子基础上增加 C 因子（相关性因子），
        基于 Keller & Van Putten (2013) FAA 框架。
        C 因子 = 该 ETF 与核心 ML+CB 策略的 60 日滚动 Pearson 相关性，
        低相关得高分（rank_normalize 后加权）。
        同时增加硬排除：corr > correlation_hard_exclude 的 ETF 不选入。

        Returns:
            [(symbol, score), ...] 按得分降序排列
        """
        cfg = self._cfg

        # 获取当前日期 (取第一个 bar 的 end_ts)
        current_date_str = ""
        for b in bars.values():
            current_date_str = b.end_ts.strftime("%Y-%m-%d")
            break

        # Phase 1: 收集所有候选 ETF 的因子原始值
        candidates: list[str] = []
        raw_momentum: list[float] = []
        raw_volatility: list[float] = []
        raw_reversion: list[float] = []
        raw_volume: list[float] = []
        raw_correlation: list[float] = []  # C 因子(取反: 低corr=高分)

        for sym in bars:
            hist = self._price_history.get(sym, [])
            if len(hist) < max(
                cfg.momentum_lookback, cfg.volatility_lookback + 1, 10
            ):
                continue

            # 趋势过滤
            if cfg.trend_ma_period > 0 and len(hist) >= cfg.trend_ma_period:
                ma = sum(hist[-cfg.trend_ma_period:]) / cfg.trend_ma_period
                if hist[-1] < ma:
                    continue

            # 最低成交额过滤
            if cfg.min_amount > 0:
                vol_hist = self._volume_history.get(sym, [])
                if vol_hist:
                    avg_amt = sum(vol_hist) / len(vol_hist)
                    if avg_amt < cfg.min_amount:
                        continue

            # C 因子：相关性硬排除
            corr_val = None
            if cfg.weight_correlation > 0 and self._benchmark_returns:
                corr_val = self._calc_correlation_with_benchmark(
                    sym, current_date_str
                )
                if (
                    corr_val is not None
                    and cfg.correlation_hard_exclude > 0
                    and corr_val > cfg.correlation_hard_exclude
                ):
                    logger.debug(
                        "C-factor 硬排除 %s: corr=%.3f > %.2f",
                        sym, corr_val, cfg.correlation_hard_exclude,
                    )
                    continue

            raw = self._calc_raw_factors(sym, hist)
            candidates.append(sym)
            raw_momentum.append(raw[0])
            raw_volatility.append(raw[1])
            raw_reversion.append(raw[2])
            raw_volume.append(raw[3])
            # C 因子: 取反（低相关 = 高分）
            raw_correlation.append(
                -corr_val if corr_val is not None else 0.0
            )

        if not candidates:
            return []

        # Phase 2: 排名归一化 + 加权求和
        n = len(candidates)
        r_mom = self._rank_normalize_list(raw_momentum)
        r_vol = self._rank_normalize_list(raw_volatility)
        r_rev = self._rank_normalize_list(raw_reversion)
        r_amt = self._rank_normalize_list(raw_volume)
        r_cor = self._rank_normalize_list(raw_correlation)

        # P0b: 使用 regime-aware 权重
        w_mom, w_vol, w_cor, w_amt = self._get_regime_weights()

        scores: list[tuple[str, float]] = []
        for i in range(n):
            s = (
                w_mom * r_mom[i]
                + w_vol * r_vol[i]
                + cfg.weight_reversion * r_rev[i]
                + w_amt * r_amt[i]
                + w_cor * r_cor[i]
            )
            scores.append((candidates[i], s))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    @staticmethod
    def _rank_normalize_list(values: list[float]) -> list[float]:
        """将原始值列表做排名归一化到 [0, 1]

        排名归一化: rank / (n - 1)，值越大排名越高 → 归一化值越大
        相同值取平均排名。当 n <= 1 时返回 [0.5...]。
        """
        n = len(values)
        if n <= 1:
            return [0.5] * n

        # 升序排名（值越大 → 排名越高 → 分越高）
        indexed = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[indexed[j + 1]] == values[indexed[j]]:
                j += 1
            avg_rank = (i + j) / 2.0
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1

        # 归一化到 [0, 1]
        max_rank = n - 1
        return [r / max_rank for r in ranks]

    def _calc_raw_factors(
        self, sym: str, hist: list[float]
    ) -> tuple[float, float, float, float]:
        """计算原始因子值（未归一化）

        Returns:
            (momentum, neg_volatility, reversion, volume_activity)
            方向已统一：值越大越好
        """
        cfg = self._cfg

        # 动量因子：N日收益率（越大越好）
        mom = 0.0
        if len(hist) >= cfg.momentum_lookback:
            mom = hist[-1] / hist[-cfg.momentum_lookback] - 1

        # 低波动因子：取反（低波动得高分，越大越好）
        neg_vol = 0.0
        if len(hist) >= cfg.volatility_lookback + 1:
            returns = []
            for i in range(-cfg.volatility_lookback, 0):
                if hist[i - 1] > 0:
                    returns.append(hist[i] / hist[i - 1] - 1)
            if returns:
                vol = (sum(r * r for r in returns) / len(returns)) ** 0.5
                neg_vol = -vol

        # 短期反转因子（取反：近期跌幅大→得分高）
        rev = 0.0
        if len(hist) >= cfg.reversion_lookback + 1:
            rev = -(hist[-1] / hist[-cfg.reversion_lookback] - 1)

        # 成交活跃度（正向）
        amt = 0.0
        vol_hist = self._volume_history.get(sym, [])
        if vol_hist:
            avg_amt = sum(vol_hist) / len(vol_hist)
            amt = math.log1p(avg_amt)

        return (mom, neg_vol, rev, amt)

    # ------------------------------------------------------------------
    # 内部方法：目标选择
    # ------------------------------------------------------------------

    def _select_targets(
        self,
        scored: list[tuple[str, float]],
        context: StrategyContext,
    ) -> list[str]:
        """选择目标持仓（含缓冲区逻辑）"""
        cfg = self._cfg
        held = {s for s, q in context.positions.items() if q > 0}

        top_syms = [s for s, _ in scored[: cfg.top_k]]
        buffer_set = {
            s for s, _ in scored[: cfg.top_k + cfg.buffer_k]
        }

        targets = list(top_syms)
        for s in held:
            if s in buffer_set and s not in targets:
                targets.append(s)
        return targets[: cfg.top_k + cfg.buffer_k]

    # ------------------------------------------------------------------
    # 内部方法：调仓指令生成
    # ------------------------------------------------------------------

    def _generate_rebalance(
        self,
        target_syms: list[str],
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """生成调仓指令：先卖后买"""
        cfg = self._cfg
        orders: list[OrderIntent] = []

        # 1. 卖出不在目标中的持仓
        for sym, qty in context.positions.items():
            if qty > 0 and sym not in target_syms:
                orders.append(OrderIntent(
                    symbol=sym, side=Side.SELL, qty=qty,
                    intent_type=IntentType.ORDER,
                    limit_price=None,
                    reason="not_in_target",
                    signal_strength=None,
                ))

        # 2. 可用资金（含卖出预估回收）
        sell_recovery = sum(
            context.positions.get(o.symbol, 0)
            * bars[o.symbol].close * 0.995
            for o in orders
            if o.side == Side.SELL and o.symbol in bars
        )
        available = context.cash + sell_recovery
        available *= (1 - cfg.cash_reserve_pct)

        n = len(target_syms)
        if n == 0:
            return orders

        # 3. 计算目标权重
        if cfg.inverse_vol_weighting:
            weights = self._calc_ivol_weights(target_syms)
        else:
            weights = {s: 1.0 / n for s in target_syms}

        for s in weights:
            weights[s] = min(weights[s], cfg.max_single_pct)
        w_sum = sum(weights.values())
        if w_sum > 0:
            weights = {s: w / w_sum for s, w in weights.items()}

        # 4. 买入/减仓指令
        for sym in target_syms:
            if sym not in bars or bars[sym].close <= 0:
                continue
            price = bars[sym].close
            target_val = available * weights.get(sym, 0)
            target_qty = (
                int(target_val / price / cfg.lot_size) * cfg.lot_size
            )
            current_qty = context.positions.get(sym, 0)
            diff = target_qty - current_qty

            if diff > 0:
                orders.append(OrderIntent(
                    symbol=sym, side=Side.BUY, qty=diff,
                    intent_type=IntentType.ORDER,
                    limit_price=None,
                    reason="rebalance_buy",
                    signal_strength=weights.get(sym, 0),
                ))
            elif diff < 0:
                orders.append(OrderIntent(
                    symbol=sym, side=Side.SELL, qty=abs(diff),
                    intent_type=IntentType.ORDER,
                    limit_price=None,
                    reason="rebalance_reduce",
                    signal_strength=weights.get(sym, 0),
                ))

        return orders

    def _calc_ivol_weights(
        self, symbols: list[str],
    ) -> dict[str, float]:
        """反波动率加权"""
        cfg = self._cfg
        inv_vols: dict[str, float] = {}
        for sym in symbols:
            hist = self._price_history.get(sym, [])
            if len(hist) < cfg.volatility_lookback + 1:
                inv_vols[sym] = 1.0
                continue
            rets = []
            for i in range(-cfg.volatility_lookback, 0):
                if hist[i - 1] > 0:
                    rets.append(hist[i] / hist[i - 1] - 1)
            if rets:
                vol = (sum(r * r for r in rets) / len(rets)) ** 0.5
                inv_vols[sym] = 1.0 / max(vol, 0.001)
            else:
                inv_vols[sym] = 1.0
        total = sum(inv_vols.values())
        if total > 0:
            return {s: v / total for s, v in inv_vols.items()}
        return {s: 1.0 / len(symbols) for s in symbols}
