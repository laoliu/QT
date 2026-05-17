"""正股质量 + 债性因子增强 v3 可转债策略

在 cb_stock_quality v1 基础上新增债性因子（YTM + 债底比率），
依据 Phase C-G 评估结论（CONDITIONAL_GO: ytm ICIR=0.32, bond_floor 0.29）。

**因子矩阵（7+1 因子）**：
Layer 1 — CB 本体因子：
  1. 价格因子 (close): 低价 → 债底保护（权重 0.20）
  2. 溢价率因子 (cb_over_rate): 低溢价 → 股性弹性（权重 0.15）
Layer 2 — 正股传导因子：
  3. 正股 ROE (stock_roe): 高 ROE → 优质正股（权重 0.15）
  4. 正股 EP (stock_ep): 高 EP → 正股低估（权重 0.10）
  5. 正股波动率 (stock_vol): 高波动 → 期权价值（权重 0.08）
Layer 3 — 债性因子（新增）：
  6. YTM (ytm): 高 YTM → 债性保护高（权重 0.15）
  7. 债底比率 (bond_floor_ratio): 高 → 面值/价格比高（权重 0.10）
Weak：
  8. 发行规模 (issue_size): 小规模 → 定价偏差（权重 0.07）

理论基础：
- YTM（到期收益率）：高 YTM 意味 CB 价格接近或低于纯债价值，
  即使正股不涨也有票息收益，提供安全垫（C-G: ICIR=0.3228✓）
- bond_floor_ratio（100/price）：面值与价格的比率，直接度量
  债底保护程度（C-G: ICIR=0.2917，接近阈值）
- 两个因子与权益侧因子（ROE/EP/vol）信息正交：
  权益因子衡量"涨了能赚多少"，债性因子衡量"不涨亏多少"

与 v1/v2 的差异：
- v1: 5+1 因子（纯权益侧 + 价格/溢价）
- v2: 8 因子（v1 + 现金流/盈利稳定/成长）— 仍全部是权益侧
- v3: 7+1 因子（v1 核心 + 债性因子）— 首次引入 fixed-income 维度
"""
from __future__ import annotations

import logging
import math
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


@dataclass
class StockQualityV3Config(CBRotationConfig):
    """v3 配置：v1 核心 + 债性因子。"""

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 150.0
    max_issue_size: float = float("inf")
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.25
    lot_size: int = 10

    use_age_filter: bool = True
    min_age_days: int = 3
    max_premium: float = 80.0

    # 因子权重 (7+1)
    weight_price: float = 0.20
    weight_premium: float = 0.15
    weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.10
    weight_stock_vol: float = 0.08
    weight_ytm: float = 0.15
    weight_bond_floor: float = 0.10
    weight_issue_size: float = 0.07

    vol_lookback: int = 60
    use_stock_bridge: bool = True
    default_roe: float = 0.0
    default_ep: float = 0.0


@StrategyRegistry().register("cb_stock_quality_v3")
class CBStockQualityV3Strategy(CBRotationStrategy):
    """正股质量 + 债性因子增强 v3。"""

    name = "cb_stock_quality_v3"

    def __init__(
        self,
        config: StockQualityV3Config | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or StockQualityV3Config(**kwargs)
        self._bridge = stock_bridge
        self._stk_price_history: dict[str, deque] = {}
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> StockQualityV3Config:
        return self._config

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._current_fundamentals.clear()
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:
                logger.warning("CBStockBridge 初始化失败: %s", e)

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史 + 基本面。"""
        self._current_fundamentals.clear()
        qd = (context.current_time.date()
              if hasattr(context.current_time, "date")
              else context.current_time)
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if not stk_code:
                continue
            cb_value = meta.get("cb_value")
            if cb_value and cb_value > 0:
                if stk_code not in self._stk_price_history:
                    self._stk_price_history[stk_code] = deque(
                        maxlen=self._config.vol_lookback + 5)
                self._stk_price_history[stk_code].append(float(cb_value))
            if self._bridge and stk_code not in self._current_fundamentals:
                try:
                    fund = self._bridge.get_fundamentals(stk_code, qd)
                    self._current_fundamentals[stk_code] = fund
                except Exception:
                    pass

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext,
    ) -> bool:
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
        """7+1 因子排名加权评分。"""
        cfg = self._config
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]

        roes: list[float] = []
        eps_vals: list[float] = []
        vols: list[float] = []
        ytms: list[float] = []
        bf_ratios: list[float] = []

        for c in candidates:
            stk = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk)
            roes.append(
                fund.roe if fund and fund.roe is not None else cfg.default_roe)
            eps_vals.append(
                fund.ep if fund and fund.ep is not None else cfg.default_ep)
            vol = self._compute_stk_vol(stk)
            vols.append(vol if vol is not None else 0.0)
            ytms.append(self._compute_ytm(c))
            bf_ratios.append(self._compute_bond_floor_ratio(c))

        # 排名归一化
        rk_price = _rank_scores(prices, ascending=True)
        rk_prem = _rank_scores(premiums, ascending=True)
        rk_roe = _rank_scores(roes, ascending=False)
        rk_ep = _rank_scores(eps_vals, ascending=False)
        rk_vol = _rank_scores(vols, ascending=False)
        rk_ytm = _rank_scores(ytms, ascending=False)     # 高 YTM 好
        rk_bf = _rank_scores(bf_ratios, ascending=False)  # 高债底比好
        rk_sz = _rank_scores(sizes, ascending=True)

        tw = (cfg.weight_price + cfg.weight_premium + cfg.weight_stock_roe
              + cfg.weight_stock_ep + cfg.weight_stock_vol
              + cfg.weight_ytm + cfg.weight_bond_floor
              + cfg.weight_issue_size)
        if tw <= 0:
            tw = 1.0

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_price * rk_price[i]
                 + cfg.weight_premium * rk_prem[i]
                 + cfg.weight_stock_roe * rk_roe[i]
                 + cfg.weight_stock_ep * rk_ep[i]
                 + cfg.weight_stock_vol * rk_vol[i]
                 + cfg.weight_ytm * rk_ytm[i]
                 + cfg.weight_bond_floor * rk_bf[i]
                 + cfg.weight_issue_size * rk_sz[i]) / tw
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1])
        return [sym for sym, _ in scored[:cfg.top_k]]

    def _compute_ytm(self, c: CandidateInfo) -> float:
        """简化 YTM = (100/price)^(1/years) - 1。"""
        price = c.close
        if price <= 0:
            return 0.0
        meta = c.metadata or {}
        mat_date = meta.get("maturity_date")
        if mat_date:
            try:
                from datetime import datetime
                mat = datetime.strptime(str(mat_date)[:8], "%Y%m%d")
                now = datetime.now()
                years = max((mat - now).days / 365.25, 0.01)
            except Exception:
                years = 5.0
        else:
            years = 5.0
        return (100.0 / price) ** (1.0 / years) - 1.0

    def _compute_bond_floor_ratio(self, c: CandidateInfo) -> float:
        """债底比率 = 100 / price。"""
        return 100.0 / c.close if c.close > 0 else 0.0

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        """计算正股波动率。"""
        if not stk_code:
            return None
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 10:
            return None
        prices = list(hist)
        log_rets = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i - 1] > 0 and prices[i] > 0
        ]
        if len(log_rets) < 5:
            return None
        mean_r = sum(log_rets) / len(log_rets)
        var = sum((r - mean_r) ** 2 for r in log_rets) / len(log_rets)
        return math.sqrt(var * TRADING_DAYS_PER_YEAR)  # 年化
