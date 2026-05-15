"""行业动量轮动"""
from __future__ import annotations
import logging
from collections import defaultdict
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_size_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
from aipha.domain.models import Side
logger = logging.getLogger(__name__)


def _industry_rank_scores(candidates: list[EquityCandidateInfo], lookback: int) -> dict[str, float]:
    """Compute industry-level momentum rank scores for each candidate (legacy compat)."""
    by_ind: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c in candidates:
        ind = getattr(c, "industry", None) or (c.metadata.get("industry", "") if c.metadata else "")
        ret = getattr(c, f"return_{lookback}d", None) or getattr(c, "return_20d", 0.0) or 0.0
        by_ind[ind].append((c.code, ret))
    ind_avg = {ind: sum(r for _, r in items) / max(len(items), 1) for ind, items in by_ind.items()}
    ranked = sorted(ind_avg, key=ind_avg.get, reverse=True)  # type: ignore
    n = max(len(ranked), 1)
    ind_score = {ind: 1.0 - i / n for i, ind in enumerate(ranked)}
    return {c.code: ind_score.get(getattr(c, "industry", None) or (c.metadata.get("industry", "") if c.metadata else ""), 0.5) for c in candidates}

@dataclass
class SectorMomentumConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 10; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 3000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.08; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.30; top_industries: int = 6; industry_momentum_lookback: int = 20
    weight_momentum: float = 0.30
    weight_volatility: float = 0.25
    weight_value: float = 0.20
    weight_quality: float = 0.15
    weight_size: float = 0.10

class SectorMomentumStrategy(EquityRotationStrategy):
    name = "sector_momentum"
    def __init__(self, config: SectorMomentumConfig | None = None, **kw: object):
        super().__init__(config=config or SectorMomentumConfig(), **kw)
    @property
    def config(self) -> SectorMomentumConfig: return self._config  # type: ignore

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        if side == Side.BUY:
            self._peak_prices[symbol] = price
        elif side == Side.SELL:
            self._peak_prices.pop(symbol, None)

    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        if not candidates:
            return []

        # Use strategy's price history
        ph = {c.code: list(self._price_history.get(c.code, [])) for c in candidates}

        # Step 1: Industry momentum filter
        by_ind: dict[str, list[EquityCandidateInfo]] = defaultdict(list)
        for c in candidates:
            ind = c.metadata.get("industry", "未知") if c.metadata else "未知"
            by_ind[ind].append(c)

        ind_mom: dict[str, float] = {}
        for ind, stocks in by_ind.items():
            moms = []
            for c in stocks:
                prices = ph.get(c.code, [])
                if len(prices) >= 2:
                    moms.append(prices[-1] / prices[0] - 1 if prices[0] > 0 else 0.0)
                else:
                    moms.append(0.0)
            ind_mom[ind] = sum(moms) / max(len(moms), 1)

        ranked_inds = sorted(ind_mom, key=ind_mom.get, reverse=True)
        hot_inds = set(ranked_inds[:cfg.top_industries])

        hot_candidates = [c for c in candidates if (c.metadata.get("industry", "未知") if c.metadata else "未知") in hot_inds]
        if not hot_candidates:
            hot_candidates = candidates

        # Step 2: Compute and rank-normalize factors
        ph_hot = {c.code: ph.get(c.code, []) for c in hot_candidates}
        mom_raw = compute_momentum_factor(ph_hot, hot_candidates, lookback=20)
        vol_raw = compute_volatility_factor(ph_hot, hot_candidates, lookback=20)
        val_raw = compute_value_factor(hot_candidates)
        qual_raw = compute_quality_factor(hot_candidates)
        sz_raw = compute_size_factor(hot_candidates)

        mom_r = _rank_scores(mom_raw) if mom_raw else {}
        vol_r = _rank_scores(vol_raw) if vol_raw else {}
        val_r = _rank_scores(val_raw) if val_raw else {}
        qual_r = _rank_scores(qual_raw) if qual_raw else {}
        sz_r = _rank_scores(sz_raw) if sz_raw else {}

        composite = {}
        for c in hot_candidates:
            code = c.code
            composite[code] = (
                cfg.weight_momentum * mom_r.get(code, 0.5)
                + cfg.weight_volatility * vol_r.get(code, 0.5)
                + cfg.weight_value * val_r.get(code, 0.5)
                + cfg.weight_quality * qual_r.get(code, 0.5)
                + cfg.weight_size * sz_r.get(code, 0.5)
            )

        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
