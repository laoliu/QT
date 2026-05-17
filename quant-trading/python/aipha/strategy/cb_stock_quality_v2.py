"""正股质量增强 v2 可转债策略

在 cb_stock_quality (sq_v4b) 基础上扩展，增加更多正股基本面维度：

**v1 (sq_v4b) → v2 扩展对比**：
- v1: 5 因子 (price, premium, stock_roe, stock_ep, stock_vol)
- v2: 8 因子 (v1 基础 + cash_flow_quality + earnings_stability + growth)

**新增因子理论基础**（§11.2 ML 因子维度扩展）：
6. **现金流质量** (cash_flow_quality):
   OCF/NI > 1 → 盈利质量高，非会计操作（Sloan 1996 应计异象）
   经济解释：高质量现金流的正股 → 基本面可持续 → CB 转股价值有支撑

7. **盈利稳定性** (earnings_stability):
   近 4 季 ROE 标准差低 → 盈利可预测（Ball & Shivakumar 2005）
   经济解释：盈利稳定的正股 → 下修概率低 → CB 面值保底更可靠

8. **营收增长率** (revenue_growth):
   营收同比增长高 → 成长性好（Novy-Marx 2013 质量因子）
   经济解释：高成长正股 → 转股价值上行空间大 → CB 期权价值增加

**正交性分析**：
- v1 (sq_v4b) 与 triple_low 相关性 0.42 → 已验证正交
- v2 增加 3 个正股因子 → 预期与 triple_low 相关性进一步降低
- 现金流/盈利稳定性是全新的信息维度（CB 本体和价格因子均不包含）
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores
from aipha.strategy.registry import StrategyRegistry

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from datetime import date

    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class StockQualityV2Config(CBRotationConfig):
    """正股质量增强 v2 策略配置。

    Attributes:
        weight_price: 价格因子权重（越低越好）
        weight_premium: 溢价率因子权重（越低越好）
        weight_stock_roe: 正股 ROE 因子权重（越高越好）
        weight_stock_ep: 正股 EP 因子权重（越高越好）
        weight_stock_vol: 正股波动率因子权重（越高越好）
        weight_cash_flow: 现金流质量因子权重（OCF/NI，越高越好）
        weight_earnings_stability: 盈利稳定性因子权重（ROE std 越低越好）
        weight_growth: 营收增长率因子权重（越高越好）
        vol_lookback: 波动率计算回看天数
        use_stock_bridge: 是否启用正股数据桥接
        max_premium: 溢价率过滤上限 (%)
        use_age_filter: 是否启用次新过滤
        min_age_days: 最小上市天数
    """

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 150.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.25
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3

    # --- 溢价率过滤 ---
    max_premium: float = 80.0

    # --- 8 因子权重 ---
    # Layer 1: CB 本体因子
    weight_price: float = 0.15          # 低价好
    weight_premium: float = 0.15        # 低溢价好
    # Layer 2: 正股传导因子（v1 已有）
    weight_stock_roe: float = 0.15      # 高 ROE 好
    weight_stock_ep: float = 0.10       # 高 EP 好
    weight_stock_vol: float = 0.10      # 高波动好（期权价值）
    # Layer 3: 正股质量深化（v2 新增）
    weight_cash_flow: float = 0.15      # 高现金流质量好
    weight_earnings_stability: float = 0.10  # 高盈利稳定好
    weight_growth: float = 0.10         # 高增长好

    # --- 正股数据参数 ---
    vol_lookback: int = 60
    use_stock_bridge: bool = True

    # --- 默认值 ---
    default_roe: float = 0.0
    default_ep: float = 0.0
    default_ocf_ni_ratio: float = 1.0   # 中性
    default_roe_std: float = 0.05        # 中性 std
    default_revenue_growth: float = 0.0  # 中性


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_stock_quality_v2")
class CBStockQualityV2Strategy(CBRotationStrategy):
    """正股质量增强 v2 可转债策略。

    8 因子排名加权选股，v1 基础上扩展正股质量因子（现金流+盈利稳定+增长）。

    score = w_price        * rank(close, ↑)
          + w_premium      * rank(cb_over_rate, ↑)
          + w_roe          * rank(stock_roe, ↓)
          + w_ep           * rank(stock_ep, ↓)
          + w_vol          * rank(stock_vol_60d, ↓)
          + w_cash_flow    * rank(ocf_ni_ratio, ↓)      # v2 新增
          + w_earn_stab    * rank(-roe_std, ↓)            # v2 新增（低 std 好）
          + w_growth       * rank(revenue_growth, ↓)     # v2 新增
    """

    name = "cb_stock_quality_v2"

    def __init__(
        self,
        config: StockQualityV2Config | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or StockQualityV2Config(**kwargs)
        self._bridge = stock_bridge
        self._stk_price_history: dict[str, deque] = {}
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> StockQualityV2Config:
        return self._config

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._current_fundamentals.clear()
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:
                logger.warning(f"CBStockBridge 初始化失败: {e}")

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史和基本面缓存。"""
        self._current_fundamentals.clear()
        query_date = (
            context.current_time.date()
            if hasattr(context.current_time, "date")
            else context.current_time
        )

        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if not stk_code:
                continue

            # 更新正股价格代理
            cb_value = meta.get("cb_value")
            if cb_value and cb_value > 0:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(
                        maxlen=self._config.vol_lookback + 5
                    )
                self._stk_price_history[stk_code].append(float(cb_value))

            # 获取正股基本面
            if self._bridge is not None and stk_code not in self._current_fundamentals:
                try:
                    fund = self._bridge.get_fundamentals(stk_code, query_date)
                    self._current_fundamentals[stk_code] = fund
                except Exception:
                    pass

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率 + 次新过滤。"""
        cfg = self._config

        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """8 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []

        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # ----- 提取因子原始值 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]

        # 正股 ROE
        roes: list[float] = []
        eps_vals: list[float] = []
        vols: list[float] = []
        ocf_ni_ratios: list[float] = []
        roe_stds: list[float] = []
        rev_growths: list[float] = []

        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)

            # v1 因子
            roes.append(
                fund.roe if fund is not None and hasattr(fund, "roe") and fund.roe is not None
                else cfg.default_roe
            )
            eps_vals.append(
                fund.ep if fund is not None and hasattr(fund, "ep") and fund.ep is not None
                else cfg.default_ep
            )
            vol = self._compute_stk_vol(stk_code)
            # 波动率缺失时使用高惩罚值（避免 0.0 虚假低波信号）
            vols.append(vol if vol is not None else 1.0)

            # v2 新增因子
            ocf_ni_ratios.append(
                fund.ocf_ni_ratio
                if fund is not None and hasattr(fund, "ocf_ni_ratio") and fund.ocf_ni_ratio is not None
                else cfg.default_ocf_ni_ratio
            )
            roe_stds.append(
                fund.roe_std
                if fund is not None and hasattr(fund, "roe_std") and fund.roe_std is not None
                else cfg.default_roe_std
            )
            rev_growths.append(
                fund.revenue_growth
                if fund is not None and hasattr(fund, "revenue_growth") and fund.revenue_growth is not None
                else cfg.default_revenue_growth
            )

        # ----- 排名归一化 -----
        rank_price = _rank_scores(prices, ascending=True)
        rank_premium = _rank_scores(premiums, ascending=True)
        rank_roe = _rank_scores(roes, ascending=False)
        rank_ep = _rank_scores(eps_vals, ascending=False)
        rank_vol = _rank_scores(vols, ascending=False)
        rank_ocf = _rank_scores(ocf_ni_ratios, ascending=False)      # 高现金流好
        # 盈利稳定性：低 std 好 → ascending=True（低值排前）
        rank_stability = _rank_scores(roe_stds, ascending=True)
        rank_growth = _rank_scores(rev_growths, ascending=False)      # 高增长好

        # ----- 权重归一化 -----
        total_w = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_stock_roe + cfg.weight_stock_ep + cfg.weight_stock_vol
            + cfg.weight_cash_flow + cfg.weight_earnings_stability + cfg.weight_growth
        )
        if total_w <= 0:
            total_w = 1.0

        w_pr = cfg.weight_price / total_w
        w_pm = cfg.weight_premium / total_w
        w_roe = cfg.weight_stock_roe / total_w
        w_ep = cfg.weight_stock_ep / total_w
        w_vol = cfg.weight_stock_vol / total_w
        w_ocf = cfg.weight_cash_flow / total_w
        w_stab = cfg.weight_earnings_stability / total_w
        w_gr = cfg.weight_growth / total_w

        # ----- 综合打分 -----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                w_pr * rank_price[i]
                + w_pm * rank_premium[i]
                + w_roe * rank_roe[i]
                + w_ep * rank_ep[i]
                + w_vol * rank_vol[i]
                + w_ocf * rank_ocf[i]
                + w_stab * rank_stability[i]
                + w_gr * rank_growth[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        """计算正股波动率（年化）。"""
        if not stk_code:
            return None
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 10:
            return None

        prices = list(hist)
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(prices[i] / prices[i - 1] - 1.0)

        if len(returns) < 5:
            return None

        import math
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        daily_vol = math.sqrt(var)
        return daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR)  # §13.8: 中国 A 股年度交易日
