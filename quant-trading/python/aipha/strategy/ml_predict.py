"""ML 预测策略 — 预训练回归/分类模型选股"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

logger = logging.getLogger(__name__)

_DEFAULT_META_FEATURES = ["roe", "pe_ttm", "pb", "dv_ratio", "turnover_rate", "circ_mv"]
_DEFAULT_FALLBACK_WEIGHTS = {
    "roe": 0.18, "pe_ttm": -0.12, "pb": -0.08, "dv_ratio": 0.15,
    "turnover_rate": -0.05, "circ_mv": -0.10, "momentum": 0.12, "volatility": -0.10,
}


@dataclass
class MLStrategyConfig(EquityRotationConfig):
    model_path: str = ""
    feature_columns: list[str] = field(default_factory=lambda: list(_DEFAULT_META_FEATURES))
    prediction_threshold: float = float("-inf")
    momentum_lookback: int = 20
    volatility_lookback: int = 20
    fallback_weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_FALLBACK_WEIGHTS))
    winsorize: bool = True
    winsorize_lower: float = 0.02
    winsorize_upper: float = 0.98


class MLPredictStrategy(EquityRotationStrategy):
    """ML 预测 — 加载 sklearn/lightgbm 模型, 使用基本面特征预测收益率"""
    name = "ml_predict"

    def __init__(self, config: MLStrategyConfig | None = None, **kwargs: Any):
        super().__init__(config=config or MLStrategyConfig(), **kwargs)
        self._model: Any = None
        self._load_model()

    @property
    def config(self) -> MLStrategyConfig:
        return self._config  # type: ignore[return-value]

    def reload_model(self, model_dir: str) -> bool:
        self._config.model_path = model_dir  # type: ignore[attr-defined]
        self._load_model()
        return self._model is not None

    def _load_model(self) -> None:
        p = self.config.model_path
        if not p:
            return
        try:
            import pickle
            from pathlib import Path
            if Path(p).exists():
                with Path(p).open("rb") as f:
                    self._model = pickle.load(f)  # noqa: S301
        except Exception as e:
            logger.warning("Cannot load model from %s: %s", p, e)

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        if self._model is not None:
            return self._predict_with_model(candidates)
        return self._linear_fallback(candidates)

    def _predict_with_model(self, candidates: list[EquityCandidateInfo]) -> list[str]:
        cfg = self.config
        try:
            import numpy as np
            features = []
            codes = []
            for c in candidates:
                row = [float(getattr(c, f, 0) or 0) for f in cfg.feature_columns]
                features.append(row)
                codes.append(c.code)
            X = np.array(features)
            preds = self._model.predict(X)
            scored = sorted(zip(codes, preds), key=lambda x: x[1], reverse=True)
            return [s[0] for s in scored[:cfg.top_k]]
        except Exception as e:
            logger.warning("Model prediction failed: %s", e)
            return self._linear_fallback(candidates)

    def _linear_fallback(self, candidates: list[EquityCandidateInfo]) -> list[str]:
        cfg = self.config
        w = cfg.fallback_weights
        scored: list[tuple[str, float]] = []
        for c in candidates:
            s = sum(w.get(f, 0) * float(getattr(c, f, 0) or 0) for f in w)
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
