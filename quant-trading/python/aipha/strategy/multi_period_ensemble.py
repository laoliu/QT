"""多周期集成策略（多时间尺度ML预测融合）"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class PeriodConfig:
    horizon_days: int; predictions_path: str = ""; weight: float = 1.0; ic_lookback: int = 60

@dataclass
class MultiPeriodEnsembleConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 5
    periods: list[dict] = field(default_factory=list)
    blend_method: str = "ic_weighted"; ic_min_weight: float = 0.05; ic_decay_halflife: int = 20
    score_normalize: bool = True; oos_predictions_path: str = ""; forward_days: int = 5
    fallback_weights: dict[str, float] = field(default_factory=lambda: {"pe_ttm": -1, "pb": -1, "roe": 1, "circ_mv": -0.5, "turnover_rate": -0.3})

class MultiPeriodEnsembleStrategy(EquityRotationStrategy):
    name = "ml_factor_ensemble"
    def __init__(self, config: MultiPeriodEnsembleConfig | None = None, **kw: Any):
        super().__init__(config=config or MultiPeriodEnsembleConfig(), **kw)
        self._predictions: dict[int, dict[str, dict[str, float]]] = {}
    @property
    def config(self) -> MultiPeriodEnsembleConfig: return self._config  # type: ignore
    def _load_all_predictions(self) -> None:
        for p in self.config.periods:
            horizon = p.get("horizon_days", 5); path = p.get("predictions_path", "")
            if not path: continue
            try:
                import pandas as pd  # type: ignore
                df = pd.read_parquet(path)
                self._predictions[horizon] = {str(dt): dict(zip(df.columns, row)) for dt, row in df.iterrows()}
            except Exception as e:
                logger.warning("Failed to load predictions for horizon %d: %s", horizon, e)
    def _linear_fallback(self, candidates: list[EquityCandidateInfo]) -> list[str]:
        fw = self.config.fallback_weights
        composite: dict[str, float] = {}
        for c in candidates:
            s = sum(w * (getattr(c, n, 0.0) or 0.0) for n, w in fw.items())
            composite[c.code] = s
        return sorted(composite, key=composite.get, reverse=True)[:self.config.top_k]  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        if not self._predictions:
            self._load_all_predictions()
        if not self._predictions:
            return self._linear_fallback(candidates)
        date_str = getattr(ctx, "trade_date", "")
        symbols = [c.code for c in candidates]
        blended: dict[str, float] = {}
        total_w = 0.0
        for horizon, date_preds in self._predictions.items():
            preds = date_preds.get(str(date_str), {})
            if not preds: continue
            w = 1.0; total_w += w
            for sym in symbols:
                blended[sym] = blended.get(sym, 0) + w * preds.get(sym, 0)
        if total_w > 0:
            blended = {k: v / total_w for k, v in blended.items()}
        if not blended: return self._linear_fallback(candidates)
        return sorted(blended, key=blended.get, reverse=True)[:self.config.top_k]  # type: ignore
