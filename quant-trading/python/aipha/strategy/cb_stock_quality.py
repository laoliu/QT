"""正股质量增强可转债策略

在 CB 本体因子（价格 + 溢价率）基础上，融合正股质量因子（ROE、EP）
和正股波动率因子（vol_60d），构建多维度 Alpha。

因子矩阵（5+1 因子，3 层架构）：
Layer 1 — CB 本体因子（已验证）：
  1. **价格因子** (close): 低价 → 债底保护（权重 0.25）
  2. **溢价率因子** (cb_over_rate): 低溢价 → 股性弹性（权重 0.20）
Layer 2 — 正股传导因子（新增 P0）：
  3. **正股 ROE** (stock_roe): 高 ROE → 优质正股 → 转股价值有支撑（权重 0.20）
  4. **正股 EP** (stock_ep): 高 EP → 正股低估 → 上行空间（权重 0.15）
  5. **正股波动率** (stock_vol): 高波动 → 期权价值大 → CB 凸性收益（权重 0.10）
Weak：
  6. **发行规模** (issue_size): 小规模 → 定价偏差（权重 ≤ 0.10）

理论基础（对齐 copilot-instructions.md §11.2）：
- ROE (IC 4-6%): 巴菲特核心指标，高 ROE 正股支撑转股价值
- EP (IC 3-5%): 盈利收益率 = 1/PE_TTM，低 PE 正股有估值修复空间
- Vol (IC 5-7%): 正股波动越大，CB 期权价值越高（BSM → Γ*σ²/2）
- 不使用严格的 max_issue_size 过滤，改用排名弱因子

数据来源：
- CB 本体：CandidateInfo（已有）
- 正股基本面：CBStockBridge → TuSharePITProvider (ROE PIT)
                             → TuShareEquityProvider (PE_TTM daily_basic → EP)
- 正股波动率：需策略维护正股价格历史

与现有策略对比：
- 现有所有 CB 策略使用 ZERO 正股数据 → 本策略首次引入正股基本面
- double_low_plus: 6 因子但全部 CB 本体 → 本策略 5 因子跨 CB+正股
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
from aipha.factors.cb_quality_ranking import (
    CBQualityFilters,
    ScoreDispersionInfo,
    rank_cb_quality,
    rank_cb_quality_extended,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from datetime import date

    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 共享辅助 — 保持向后兼容（cb_iv_quality 等仍从此处导入）
# ---------------------------------------------------------------------------
def _rank_scores(values: list[float], ascending: bool = True) -> list[float]:
    """排名归一化到 [0, 1]（委托 rank_utils）。"""
    from aipha.utils.rank_utils import rank_normalize_list
    return rank_normalize_list(values, ascending=ascending, handle_ties=True)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class StockQualityCBConfig(CBRotationConfig):
    """正股质量增强可转债策略配置。

    Attributes:
        weight_price: 价格因子权重（越低越好）
        weight_premium: 溢价率因子权重（越低越好）
        weight_stock_roe: 正股 ROE 因子权重（越高越好）
        weight_stock_ep: 正股 EP 因子权重（越高越好）
        weight_stock_vol: 正股波动率因子权重（越高越好）
        weight_issue_size: 发行规模因子权重（越小越好，弱因子 ≤10%）
        vol_lookback: 波动率计算回看天数
        use_stock_bridge: 是否启用正股数据桥接（False=纯 CB 因子回退模式）
        max_premium: 溢价率过滤上限 (%)
        use_age_filter: 是否启用次新过滤
        min_age_days: 最小上市天数
    """

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 150.0          # 比双低更宽松（不依赖小票效应）
    max_issue_size: float = float("inf")  # 不做硬性规模过滤！
    min_turnover: float = 20.0        # 万元
    min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.25
    lot_size: int = 10

    # --- 次新过滤 ---
    use_age_filter: bool = True
    min_age_days: int = 3

    # --- 溢价率过滤 ---
    max_premium: float = 80.0

    # --- 5+1+2 因子权重 ---
    weight_price: float = 0.25
    weight_premium: float = 0.20
    weight_stock_roe: float = 0.20
    weight_stock_ep: float = 0.15
    weight_stock_vol: float = 0.10
    weight_issue_size: float = 0.10   # 弱因子 ≤10%
    weight_ytm: float = 0.0           # YTM 因子（高 YTM = 低估 = 好, C-G ICIR=0.3228）
    weight_bond_floor: float = 0.0    # 纯债价值比 (bond_floor/close, 高 = 债底保护强)

    # --- 正股数据参数 ---
    vol_lookback: int = 60            # 波动率回看天数
    use_stock_bridge: bool = True     # 是否用正股桥接

    # --- ROE/EP 缺失时的默认值（中性排名）---
    default_roe: float = 0.0
    default_ep: float = 0.0


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_stock_quality")
class CBStockQualityStrategy(CBRotationStrategy):
    """正股质量增强可转债策略。

    5+1+2 因子排名加权选股，融合正股基本面 + 债性因子。

    score = w_price   * rank(close, ↑)
          + w_premium * rank(cb_over_rate, ↑)
          + w_roe     * rank(stock_roe, ↓)      # 越高越好
          + w_ep      * rank(stock_ep, ↓)        # 越高越好
          + w_vol     * rank(stock_vol_60d, ↓)   # 越高越好
          + w_size    * rank(issue_size, ↑)       # 越小越好
          + w_ytm     * rank(ytm, ↓)             # 高YTM=低估=好 (C-G ICIR=0.32)
          + w_bf      * rank(bond_floor_ratio, ↓) # 高=债底保护强

    YTM/bond_floor 权重默认为 0（向后兼容 v4b），v5 配置启用。
    """

    name = "cb_stock_quality"

    def __init__(
        self,
        config: StockQualityCBConfig | None = None,
        stock_bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._config = config or StockQualityCBConfig(**kwargs)
        self._bridge = stock_bridge
        # 正股价格历史 {stk_code: deque[float]}
        self._stk_price_history: dict[str, deque] = {}
        # 当日正股基本面缓存 {stk_code: StockFundamentals}
        self._current_fundamentals: dict[str, Any] = {}
        # 最近一次评分的分散度指标（供 base 层审计/自适应使用）
        self._last_dispersion: ScoreDispersionInfo | None = None
        # 全历史分散度记录 [(trade_date_str, dispersion)]
        self._dispersion_history: list[tuple[str, ScoreDispersionInfo]] = []

    @property
    def config(self) -> StockQualityCBConfig:
        return self._config

    def _on_start_extra(self) -> None:
        self._stk_price_history.clear()
        self._current_fundamentals.clear()
        # 若未在构造时注入 bridge，尝试延迟创建
        if self._bridge is None and self._config.use_stock_bridge:
            try:
                from aipha.data.cb_stock_bridge import CBStockBridge
                self._bridge = CBStockBridge()
            except Exception as e:
                logger.warning(f"CBStockBridge 初始化失败: {e}，降级为纯 CB 因子模式")

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        """更新正股价格历史 + 获取正股基本面。"""
        self._current_fundamentals.clear()
        query_date = context.current_time.date() if hasattr(context.current_time, "date") else context.current_time

        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            stk_code = meta.get("stk_code")
            if not stk_code:
                continue

            # 更新正股价格历史（通过 cb_value 反推：cb_value = 正股价*100/转股价）
            # 但更稳健的做法是用 bridge 获取正股真实价格
            # 这里先用 cb_value 作为代理，后续可改为真实正股价
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

        # 溢价率
        if candidate.cb_over_rate is not None:
            if candidate.cb_over_rate > cfg.max_premium:
                return False

        # 次新
        if cfg.use_age_filter and candidate.list_date:
            age = days_since_listed(candidate.list_date, context.current_time)
            if age < cfg.min_age_days:
                return False

        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """5+1+2 因子排名加权评分（ytm/bond_floor 可选）。"""
        cfg = self._config
        if not candidates:
            return []

        if len(candidates) == 1:
            return [candidates[0].symbol]

        # ----- 提取因子原始值 -----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]

        # 正股 ROE
        roes: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and fund.roe is not None:
                roes.append(fund.roe)
            else:
                roes.append(cfg.default_roe)

        # 正股 EP
        eps_vals: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and fund.ep is not None:
                eps_vals.append(fund.ep)
            else:
                eps_vals.append(cfg.default_ep)

        # 正股波动率 (from cb_value 代理)
        vols: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            vol = self._compute_stk_vol(stk_code)
            vols.append(vol if vol is not None else 0.0)

        # YTM / bond_floor_ratio (债性因子 — C-G 验证显著)
        ytm_vals: list[float] = []
        bf_vals: list[float] = []
        use_ytm = cfg.weight_ytm > 0 or cfg.weight_bond_floor > 0
        if use_ytm:
            ytm_vals, bf_vals = self._compute_ytm_factors(candidates, context)

        rows: list[dict[str, float | str]] = []
        for i, c in enumerate(candidates):
            row: dict[str, float | str] = {
                "ts_code": c.symbol,
                "close": prices[i],
                "cb_over_rate": premiums[i],
                "roe": roes[i],
                "ep": eps_vals[i],
                "vol": vols[i],
                "issue_size": sizes[i],
            }
            if ytm_vals:
                row["ytm"] = ytm_vals[i]
            if bf_vals:
                row["bond_floor"] = bf_vals[i]
            rows.append(row)

        import pandas as pd

        weights = {
            "price": cfg.weight_price,
            "premium": cfg.weight_premium,
            "roe": cfg.weight_stock_roe,
            "ep": cfg.weight_stock_ep,
            "vol": cfg.weight_stock_vol,
            "issue_size": cfg.weight_issue_size,
            "ytm": cfg.weight_ytm,
            "bond_floor": cfg.weight_bond_floor,
        }
        # 规模软约束：size_preference="soft" 时计算乘法惩罚
        size_mults: dict[str, float] | None = None
        if cfg.size_preference == "soft":
            from aipha.strategy.cb_rotation_base import CBRotationStrategy
            size_mults = {
                c.symbol: CBRotationStrategy.compute_size_soft_weight(
                    c.issue_size,
                    soft_cap=cfg.size_soft_cap,
                    hard_cap=cfg.size_hard_cap,
                    floor_weight=cfg.size_floor_weight,
                )
                for c in candidates
            }

        # 渐进式强赎惩罚：cb_value >= penalty_start 时线性降低得分
        redeem_start = cfg.force_redeem_penalty_start
        redeem_thresh = cfg.force_redeem_threshold
        if redeem_start > 0 and redeem_thresh > redeem_start:
            for c in candidates:
                cv = c.cb_value
                if cv is not None and cv >= redeem_start:
                    # 线性惩罚: penalty_start→1.0, threshold→0.5
                    ratio = min(1.0, (cv - redeem_start) / (redeem_thresh - redeem_start))
                    penalty = 1.0 - 0.5 * ratio  # [1.0, 0.5]
                    if size_mults is None:
                        size_mults = {}
                    size_mults[c.symbol] = size_mults.get(c.symbol, 1.0) * penalty

        result = rank_cb_quality_extended(
            pd.DataFrame(rows),
            weights,
            cfg.top_k,
            CBQualityFilters(
                max_price=cfg.max_price,
                max_premium=cfg.max_premium,
                min_turnover=cfg.min_turnover,
                min_days_to_maturity=cfg.min_days_to_maturity,
            ),
            score_multipliers=size_mults,
        )
        self._last_dispersion = result.dispersion
        if result.dispersion is not None:
            dt = context.current_time
            dt_str = dt.strftime("%Y%m%d") if hasattr(dt, "strftime") else str(dt)
            self._dispersion_history.append((dt_str, result.dispersion))
        return result.symbols

    # ----- 辅助方法 -----

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        """计算正股波动率（年化）from 价格历史。"""
        if not stk_code:
            return None
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 10:
            return None

        prices = list(hist)
        from aipha.data.cb_stock_bridge import CBStockBridge

        # 日收益率
        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append(prices[i] / prices[i - 1] - 1.0)

        return CBStockBridge.compute_volatility(returns)

    def _compute_ytm_factors(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> tuple[list[float], list[float]]:
        """计算 YTM 和 bond_floor_ratio 因子。

        Returns:
            (ytm_vals, bf_vals) — 每个 candidate 一个值。
        """
        from aipha.factors.cb_ytm import bond_floor_pv, ytm_solve

        trade_dt = context.current_time
        if hasattr(trade_dt, "date"):
            trade_dt = trade_dt.date()

        ytm_vals: list[float] = []
        bf_vals: list[float] = []
        for c in candidates:
            ytm_val = 0.0
            bf_val = 1.0  # 中性默认
            mat_str = c.maturity_date
            if mat_str and c.close > 0:
                try:
                    from datetime import datetime as _dt
                    mat_date = _dt.strptime(mat_str[:8], "%Y%m%d").date()
                    T = (mat_date - trade_dt).days / 365.25
                    if T > 0.05:
                        y = ytm_solve(c.close, T)
                        ytm_val = y if y is not None else 0.0
                        bf = bond_floor_pv(T)
                        bf_val = bf / c.close if c.close > 0 else 1.0
                except Exception:
                    pass
            ytm_vals.append(ytm_val)
            bf_vals.append(bf_val)
        return ytm_vals, bf_vals
