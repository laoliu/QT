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


class MLFactorStrategy(EquityRotationStrategy):
    """ML 因子策略 — 加载预训练模型预测, 无模型时用线性回退"""
    name = "ml_factor"

    def __init__(self, config: MLFactorConfig | None = None, **kwargs: Any):
        super().__init__(config=config or MLFactorConfig(), **kwargs)
        self._model: Any = None
        self._oos_preds: dict[str, dict[str, float]] = {}

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
        # 尝试 OOS 预测
        if self._oos_preds:
            date_str = getattr(context, "current_date_str", "")
            preds = self._oos_preds.get(date_str, {})
            if preds:
                scored = [(c.code, preds.get(c.code, 0.0)) for c in candidates]
                scored.sort(key=lambda x: x[1], reverse=True)
                return [s[0] for s in scored[:cfg.top_k]]

        # 线性回退
        return self._linear_fallback(candidates)

    def _linear_fallback(self, candidates: list[EquityCandidateInfo]) -> list[str]:
        cfg = self.config
        w = cfg.fallback_weights
        scored: list[tuple[str, float]] = []
        for c in candidates:
            s = 0.0
            for feat, weight in w.items():
                val = getattr(c, feat, None)
                if val is not None:
                    s += weight * float(val)
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
