"""机器学习因子策略 — 预训练模型预测 + 线性回退"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.factors.equity_factors import compute_momentum_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

logger = logging.getLogger(__name__)

_DEFAULT_META_FEATURES = ["roe", "pe_ttm", "pb", "dv_ratio", "turnover_rate", "circ_mv"]
_DERIVED_FEATURES = {"momentum_20d", "volatility_20d"}
_DEFAULT_FALLBACK_WEIGHTS = {
    "roe": 0.20, "pe_ttm": -0.15, "pb": -0.10, "dv_ratio": 0.15,
    "turnover_rate": -0.05, "circ_mv": -0.10, "momentum": 0.15, "volatility": -0.10,
}


@dataclass
class MLFactorConfig(EquityRotationConfig):
    model_path: str = ""
    feature_columns: list[str] = field(default_factory=lambda: list(_DEFAULT_META_FEATURES))
    prediction_threshold: float = float("-inf")
    momentum_lookback: int = 20
    volatility_lookback: int = 20
    fallback_weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_FALLBACK_WEIGHTS))
    model_type: str = "lightgbm"
    oos_predictions_path: str = ""
    oos_predictions_paths: list[str] = field(default_factory=list)
    ensemble_method: str = "rank_average"
    ensemble_weights: dict[str, float] = field(default_factory=dict)
    min_train_samples: int = 5000
    retrain_interval_days: int = 60
    model_objective: str = 'regression'
    train_years: int = 3
    forward_days: int = 5


class MLFactorStrategy(EquityRotationStrategy):
    """ML 因子策略 — 加载预训练模型预测, 无模型时用线性回退"""
    name = "ml_factor"

    def __init__(self, config: MLFactorConfig | None = None, **kwargs: Any):
        super().__init__(config=config or MLFactorConfig(), **kwargs)
        self._model: Any = None
        self._model_ready: bool = False
        self._selector: Any = None
        self._oos_preds: dict[str, dict[str, float]] = {}
        self._oos_scores: dict[str, dict[str, float]] = {}
        self._multi_oos_scores: dict[str, dict[str, dict[str, float]]] = {}

    @property
    def config(self) -> MLFactorConfig:
        return self._config  # type: ignore[return-value]

    def reload_model(self, model_dir: str) -> bool:
        return self._load_pretrained_model(model_dir)

    def _load_pretrained_model(self, path: str = "") -> bool:
        p = path or self.config.model_path
        if not p:
            return False
        try:
            import pickle
            from pathlib import Path
            model_file = Path(p)
            if model_file.exists():
                with model_file.open("rb") as f:
                    self._model = pickle.load(f)  # noqa: S301
                return True
        except Exception as e:
            logger.warning("Failed to load model: %s", e)
        return False

    def _load_oos_predictions(self) -> None:
        p = self.config.oos_predictions_path
        if not p:
            return
        try:
            import json
            from pathlib import Path
            if Path(p).exists():
                with open(p) as f:
                    self._oos_preds = json.load(f)
        except Exception as e:
            logger.warning("Failed to load OOS predictions: %s", e)

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        date_str = ""
        if hasattr(context, "current_date"):
            d = context.current_date
            if hasattr(d, "strftime"):
                date_str = d.strftime("%Y-%m-%d")
            else:
                date_str = str(d)[:10]

        # 1) 多模型集成 OOS
        if self._multi_oos_scores:
            ensemble_scores: dict[str, float] = {}
            has_date = False
            for mname, mdata in self._multi_oos_scores.items():
                day_scores = mdata.get(date_str, {})
                if day_scores:
                    has_date = True
                    w = cfg.ensemble_weights.get(mname, 1.0)
                    for c in candidates:
                        s = day_scores.get(c.code, 0.0)
                        ensemble_scores[c.code] = ensemble_scores.get(c.code, 0.0) + w * s
            if has_date and ensemble_scores:
                scored = sorted(ensemble_scores.items(), key=lambda x: x[1], reverse=True)
                return [s[0] for s in scored[:cfg.top_k]]

        # 2) 单模型 OOS
        if self._oos_scores:
            preds = self._oos_scores.get(date_str, {})
            if preds:
                scored_list = [(c.code, preds.get(c.code, 0.0)) for c in candidates]
                scored_list.sort(key=lambda x: x[1], reverse=True)
                return [s[0] for s in scored_list[:cfg.top_k]]

        # Legacy single OOS
        if self._oos_preds:
            preds = self._oos_preds.get(date_str, {})
            if preds:
                scored_list = [(c.code, preds.get(c.code, 0.0)) for c in candidates]
                scored_list.sort(key=lambda x: x[1], reverse=True)
                return [s[0] for s in scored_list[:cfg.top_k]]

        # 3) ML selector
        if self._model_ready and self._selector is not None:
            try:
                import pandas as pd
                symbols = [c.code for c in candidates]
                preds = self._selector.predict(symbols)
                if hasattr(preds, 'get'):
                    scored_list = [(c.code, float(preds.get(c.code, 0.0))) for c in candidates]
                else:
                    scored_list = [(c.code, float(preds[i])) for i, c in enumerate(candidates)]
                scored_list.sort(key=lambda x: x[1], reverse=True)
                return [s[0] for s in scored_list[:cfg.top_k]]
            except Exception:
                return self._linear_fallback(candidates)

        # 4) 线性回退
        return self._linear_fallback(candidates)

    def _linear_fallback(self, candidates: list[EquityCandidateInfo]) -> list[str]:
        cfg = self.config
        w = cfg.fallback_weights
        scored: list[tuple[str, float]] = []
        for c in candidates:
            s = 0.0
            for feat, weight in w.items():
                val = getattr(c, feat, None)
                if val is not None and val == val:  # NaN check
                    try:
                        s += weight * float(val)
                    except (TypeError, ValueError):
                        pass
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
