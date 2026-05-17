"""CB IV 事件驱动卫星策略

将 CB 从普通选股转向 IV/定价偏差 + 条款事件驱动的高凸性卫星 sleeve。
定位：组合中 5%-10% 的卫星配置，提供非对称收益（高凸性 + 事件催化）。

策略逻辑（双引擎融合）：
1. **IV 引擎 (40%)**：
   - 定价偏差 (pricing_bias): 市场价 vs 理论价偏离
   - IV 溢价率 (iv_prem): 隐含波动率 vs 历史波动率偏离
   - Gamma 凸性: 高 gamma → 正凸性，涨多跌少
2. **事件引擎 (40%)**：
   - 回售接近度 (putback_proximity): 发行人下修博弈
   - 到期催化 (maturity_catalyst): 1-3 年到期窗口
   - 下修事件信号: PIT 安全查询历史下修事件
3. **安全垫 (20%)**：
   - 低价偏好: 债底保护
   - 低溢价率: 股性弹性

Alpha 来源假说：
- IV 定价偏差：散户主导市场的隐含波动率系统性偏离理论价
- 条款博弈：发行人理性选择下修 > 回售，创造确定性 alpha
- 两引擎相关性低：IV 是连续信号，事件是离散催化 → 组合信噪比更高

References:
    Tsiveriotis-Fernandes (1998) — CB price decomposition
    Campbell et al. (2008) — CB expiry alpha
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.factors.cb_iv_greeks import compute_cb_iv_factors
from aipha.factors.enhanced_putback import (
    compute_clause_play_score,
    compute_putback_from_cb,
)
from aipha.factors.maturity_catalyst import compute_maturity_catalyst
from aipha.monitoring.downward_revision_tracker import DownwardRevisionTracker
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBIVEventConfig(CBRotationConfig):
    """CB IV 事件卫星配置"""

    # 持仓 — 集中持仓追求高凸性
    top_k: int = 15
    rebalance_days: int = 5

    # 选股范围 — 中低价 CB
    max_price: float = 130.0
    min_price: float = 90.0   # 排除深度折价信用债
    min_turnover: float = 100.0

    # Delta zone — 偏好平衡型到偏股型
    min_cb_value: float = 80.0
    max_cb_value: float = 140.0

    # 风控
    stop_loss_pct: float = 0.10
    buffer_k: int = 3

    # --- IV 引擎权重 ---
    weight_pricing_bias: float = 0.15
    weight_iv_prem: float = 0.15
    weight_gamma: float = 0.10

    # --- 事件引擎权重 ---
    weight_putback: float = 0.15
    weight_maturity_catalyst: float = 0.15
    weight_downward_revision: float = 0.10

    # --- 安全垫权重 ---
    weight_price: float = 0.10
    weight_premium: float = 0.10

    # --- IV 参数 ---
    iv_risk_free_rate: float = 0.02
    iv_credit_spread: float = 0.015

    # --- 事件参数 ---
    putback_distance_threshold: float = 1.30
    maturity_sweet_spot_years: float = 2.0


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_iv_event_satellite")
class CBIVEventSatelliteStrategy(CBRotationStrategy):
    """CB IV 事件驱动卫星策略。

    双引擎（IV 定价偏差 + 条款事件催化）融合，
    定位为组合 5-10% 的高凸性卫星 sleeve。
    """

    name = "cb_iv_event_satellite"

    def __init__(self, config: CBIVEventConfig | None = None, **kwargs: Any):
        self._config = config or CBIVEventConfig(**kwargs)
        super().__init__()
        self._dr_tracker = DownwardRevisionTracker()

    @property
    def config(self) -> CBIVEventConfig:
        return self._config

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """双引擎 (IV + 事件) 加权排名融合。"""
        cfg = self._config
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # --- 安全垫因子 ---
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]

        # --- IV 引擎 ---
        iv_bias, iv_prem, iv_gamma = self._compute_iv_scores(candidates, context)

        # --- 事件引擎 ---
        ev_putback, ev_catalyst, ev_revision = self._compute_event_scores(
            candidates, context,
        )

        # --- 排名归一化 ---
        r_price = _rank_asc(prices)         # 低价优先
        r_prem = _rank_asc(premiums)        # 低溢价优先
        r_bias = _rank_asc(iv_bias)         # 低偏差(低估)优先
        r_ivp = _rank_asc(iv_prem)          # 低 IV 溢价优先
        r_gamma = _rank_desc(iv_gamma)      # 高 gamma 优先
        r_putback = _rank_desc(ev_putback)  # 高回售接近度优先
        r_catalyst = _rank_desc(ev_catalyst)  # 高到期催化优先
        r_revision = _rank_desc(ev_revision)  # 高下修信号优先

        # --- 权重归一化 ---
        total_w = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_pricing_bias + cfg.weight_iv_prem + cfg.weight_gamma
            + cfg.weight_putback + cfg.weight_maturity_catalyst
            + cfg.weight_downward_revision
        )
        if total_w <= 0:
            total_w = 1.0

        # --- 综合打分 ---
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                cfg.weight_price / total_w * r_price[i]
                + cfg.weight_premium / total_w * r_prem[i]
                + cfg.weight_pricing_bias / total_w * r_bias[i]
                + cfg.weight_iv_prem / total_w * r_ivp[i]
                + cfg.weight_gamma / total_w * r_gamma[i]
                + cfg.weight_putback / total_w * r_putback[i]
                + cfg.weight_maturity_catalyst / total_w * r_catalyst[i]
                + cfg.weight_downward_revision / total_w * r_revision[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[:cfg.top_k]]

    def _compute_iv_scores(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> tuple[list[float], list[float], list[float]]:
        """计算 IV 引擎三因子：pricing_bias, iv_prem, gamma。"""
        cfg = self._config
        td = context.current_time
        td_str = td.strftime("%Y%m%d") if hasattr(td, "strftime") else str(td)[:8]

        biases: list[float] = []
        prems: list[float] = []
        gammas: list[float] = []

        for c in candidates:
            iv_result = self._single_iv_compute(c, td_str, cfg)
            if iv_result is not None:
                biases.append(iv_result[0])
                prems.append(iv_result[1])
                gammas.append(iv_result[2])
            else:
                biases.append(float("nan"))
                prems.append(float("nan"))
                gammas.append(float("nan"))

        # 中位数填充 NaN
        biases = _fill_nan_median(biases)
        prems = _fill_nan_median(prems)
        gammas = _fill_nan_median(gammas)
        return biases, prems, gammas

    def _single_iv_compute(
        self,
        c: CandidateInfo,
        td_str: str,
        cfg: CBIVEventConfig,
    ) -> tuple[float, float, float] | None:
        """单只转债 IV 计算，返回 (pricing_bias, iv_prem, gamma)。"""
        conv_price = c.conv_price
        if not conv_price or conv_price <= 0:
            return None
        cb_val = c.cb_value
        if not cb_val or cb_val <= 0:
            return None
        S = float(cb_val) * float(conv_price) / 100.0
        if S <= 0:
            return None

        mat = c.maturity_date
        if not mat:
            return None
        try:
            mat_dt = datetime.strptime(str(mat)[:8], "%Y%m%d")
            td_dt = datetime.strptime(td_str[:8], "%Y%m%d")
            T = max(0.0, (mat_dt - td_dt).days / 365.25)
        except (ValueError, TypeError):
            return None
        if T <= 0.01:
            return None

        result = compute_cb_iv_factors(
            symbol=c.symbol,
            cb_price=float(c.close),
            stock_price=S,
            conversion_price=float(conv_price),
            T=T,
            r=cfg.iv_risk_free_rate,
            spread=cfg.iv_credit_spread,
        )
        if result is None:
            return None

        bias = result.pricing_bias if math.isfinite(result.pricing_bias) else 0.0
        prem = result.iv_prem if math.isfinite(result.iv_prem) else 0.0
        gamma = result.gamma if math.isfinite(result.gamma) else 0.0
        return (bias, prem, gamma)

    def _compute_event_scores(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> tuple[list[float], list[float], list[float]]:
        """计算事件引擎三因子：putback, maturity_catalyst, revision。"""
        cfg = self._config
        now = datetime(
            context.current_time.year,
            context.current_time.month,
            context.current_time.day,
        )
        td_str = now.strftime("%Y%m%d")
        revision_lookup = (context.metadata or {}).get(
            "downward_revision_lookup", {},
        )

        putbacks: list[float] = []
        catalysts: list[float] = []
        revisions: list[float] = []

        for c in candidates:
            # 回售接近度
            pb = compute_putback_from_cb(
                c.cb_value, trigger_ratio=cfg.putback_distance_threshold,
            )
            putbacks.append(pb if pb is not None else -1.0)

            # 到期催化
            dtm = _days_to_maturity(c.maturity_date, now)
            cat = compute_maturity_catalyst(dtm)
            catalysts.append(cat)

            # 下修事件信号
            rev_info = revision_lookup.get(c.symbol, {})
            rev_score = self._compute_revision_signal(rev_info, td_str)
            revisions.append(rev_score)

        return putbacks, catalysts, revisions

    @staticmethod
    def _compute_revision_signal(
        rev_info: dict[str, Any], trade_date: str,
    ) -> float:
        """从下修查询表提取事件信号强度 (0-1)。"""
        if not rev_info:
            return 0.0
        last_date = rev_info.get("last_revision_date", "")
        if not last_date:
            return 0.0
        try:
            from datetime import timedelta
            rev_dt = datetime.strptime(str(last_date)[:8], "%Y%m%d")
            td_dt = datetime.strptime(trade_date[:8], "%Y%m%d")
            days_since = (td_dt - rev_dt).days
            if days_since < 0:
                return 0.0
            # 近期下修 → 高信号（180天内衰减）
            if days_since <= 30:
                return 1.0
            elif days_since <= 90:
                return 0.7
            elif days_since <= 180:
                return 0.3
            return 0.0
        except (ValueError, TypeError):
            return 0.0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _rank_asc(values: list[float]) -> list[float]:
    """升序排名归一化到 [0,1]，越小越好。"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    indexed = [(i, v) for i, v in enumerate(values)]
    indexed.sort(key=lambda x: x[1])
    result = [0.5] * n
    for rank, (idx, _) in enumerate(indexed):
        result[idx] = rank / max(n - 1, 1)
    return result


def _rank_desc(values: list[float]) -> list[float]:
    """降序排名归一化到 [0,1]，越大越好。"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    indexed = [(i, v) for i, v in enumerate(values)]
    indexed.sort(key=lambda x: x[1], reverse=True)
    result = [0.5] * n
    for rank, (idx, _) in enumerate(indexed):
        result[idx] = rank / max(n - 1, 1)
    return result


def _fill_nan_median(values: list[float]) -> list[float]:
    """将 NaN 值填充为列表中位数。"""
    valid = [v for v in values if math.isfinite(v)]
    if not valid:
        return [0.0] * len(values)
    valid.sort()
    mid = len(valid) // 2
    median = valid[mid] if len(valid) % 2 == 1 else (valid[mid - 1] + valid[mid]) / 2
    return [v if math.isfinite(v) else median for v in values]


def _days_to_maturity(maturity_date: str, now: datetime) -> int:
    """计算到期天数。"""
    if not maturity_date:
        return 9999
    try:
        mat_dt = datetime.strptime(str(maturity_date)[:8], "%Y%m%d")
        return max(0, (mat_dt - now).days)
    except (ValueError, TypeError):
        return 9999
