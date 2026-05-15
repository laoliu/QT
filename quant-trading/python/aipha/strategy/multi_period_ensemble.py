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
        self._model_ready: bool = False
        self._period_configs: list[PeriodConfig] = [
            PeriodConfig(
                horizon_days=p.get("horizon_days", 5),
                predictions_path=p.get("predictions_path", ""),
                weight=p.get("weight", 1.0),
            ) for p in self.config.periods
        ]
        self._period_scores: dict[int, dict[str, dict[str, float]]] = {p.horizon_days: {} for p in self._period_configs}
        self._period_returns: dict[int, dict[str, float]] = {p.horizon_days: {} for p in self._period_configs}
        self._ic_cache: dict[int, dict[str, float]] = {p.horizon_days: {} for p in self._period_configs}
    @property
    def config(self) -> MultiPeriodEnsembleConfig: return self._config  # type: ignore
    def _load_all_predictions(self) -> None:
        if not hasattr(self, '_predictions'):
            self._predictions = {}
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

    def _compute_ic_weights(self, date_str: str) -> dict[int, float]:
        cfg = self.config
        horizons = list(self._ic_cache.keys()) if self._ic_cache else [p.horizon_days for p in self._period_configs]
        pc_map = {p.horizon_days: p for p in self._period_configs}
        if cfg.blend_method == "equal":
            n = len(horizons)
            if n == 0: return {}
            w = 1.0 / n
            return {h: w for h in horizons}
        elif cfg.blend_method == "fixed":
            return {h: pc_map[h].weight if h in pc_map else 1.0 for h in horizons}
        else:  # ic_weighted
            result: dict[int, float] = {}
            for h in horizons:
                ic_val = self._ic_cache.get(h, {}).get(str(date_str), cfg.ic_min_weight)
                result[h] = max(cfg.ic_min_weight, ic_val)
            total = sum(result.values())
            if total > 0:
                return {h: w / total for h, w in result.items()}
            return {h: 1.0 / len(result) for h in result} if result else {}

    def _blend_scores(self, date_str: str, symbols: list[str]) -> dict[str, float]:
        weights = self._compute_ic_weights(date_str)
        blended: dict[str, float] = {s: 0.0 for s in symbols}
        for horizon, scores_by_date in self._period_scores.items():
            date_scores = scores_by_date.get(str(date_str), {})
            w = weights.get(horizon, 0.0)
            for sym in symbols:
                blended[sym] += w * date_scores.get(sym, 0.0)
        if self.config.score_normalize and blended:
            import numpy as np
            values = list(blended.values())
            mean = float(np.mean(values))
            std = float(np.std(values, ddof=1)) if len(values) > 1 else 1.0
            if std > 0:
                blended = {s: (v - mean) / std for s, v in blended.items()}
        return blended
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        if not getattr(self, '_predictions', None):
            self._load_all_predictions()
        if not getattr(self, '_predictions', None):
            # Try blend_scores path if period_scores populated
            if getattr(self, '_period_scores', None):
                date_str = getattr(ctx, "trade_date", None) or (ctx.current_time.strftime("%Y-%m-%d") if ctx.current_time else "")
                symbols = [c.code for c in candidates]
                blended = self._blend_scores(str(date_str), symbols)
                if any(v != 0.0 for v in blended.values()):
                    return sorted(blended, key=blended.get, reverse=True)[:self.config.top_k]  # type: ignore
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
