"""可转债ML排序策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

RATING_NUM: dict[str, float] = {"AAA": 6.0, "AA+": 5.0, "AA": 4.0, "AA-": 3.5, "A+": 3.0, "A": 2.5, "A-": 2.0, "BBB+": 1.5, "BBB": 1.0, "BBB-": 0.5, "BB+": 0.3, "BB": 0.0}

@dataclass
class CBMLRankingConfig(CBRotationConfig):
    model_path: str = ""; max_premium: float = 40.0; use_fallback: bool = True
    top_k: int = 20; rebalance_days: int = 5

class CBMLRankingStrategy(CBRotationStrategy):
    name = "cb_ml_ranking"
    config_class = CBMLRankingConfig
    def __init__(self, config: CBMLRankingConfig | None = None, **kw: object):
        super().__init__(config=config or CBMLRankingConfig(), **kw)
        self._model: Any = None
    @property
    def config(self) -> CBMLRankingConfig: return self._config  # type: ignore
    @property
    def _fallback_mode(self) -> bool:
        return self._model is None
    def _load_model(self) -> None:
        cfg = self.config
        if not cfg.model_path: return
        try:
            import joblib  # type: ignore
            self._model = joblib.load(cfg.model_path)
        except Exception as e:
            logger.warning("Failed to load model %s: %s", cfg.model_path, e)
    def on_start(self) -> None:
        self._load_model()
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium: return False
        return True
    @staticmethod
    def _rating_to_score(rating: str) -> float:
        return RATING_NUM.get(rating.strip().upper(), 3.0) if rating else 3.0
    def _build_features(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[dict[str, float]]:
        feats = []
        for c in candidates:
            prem = getattr(c, "cb_over_rate", 0.0) or 0.0
            close = getattr(c, "close", 100.0) or 100.0
            feats.append({
                "close": close,
                "premium_rate": getattr(c, "premium_rate", 0.0) or 0.0,
                "remain_years": getattr(c, "remain_years", 1.0) or 1.0,
                "issue_size": getattr(c, "issue_size", 5000.0) or 5000.0,
                "volume": getattr(c, "volume", 0.0) or 0.0,
                "rating": self._rating_to_score(getattr(c, "rating", "")),
                "ytm": getattr(c, "ytm_to_maturity", 0.0) or 0.0,
                "iv_bias": prem / 100.0 - 0.1 if prem else 0.0,
                "gamma_convexity": max(0.0, (110.0 - close) / 110.0),
            })
        return feats
    def _fallback_score(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        rp, rpm = _rank_scores(price), _rank_scores(prem)
        composite = {c.code: 0.5 * rp.get(c.code, 0) + 0.5 * rpm.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:self.config.top_k]  # type: ignore
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if self._model is None:
            if self.config.use_fallback: return self._fallback_score(candidates, ctx)
            return [c.code for c in candidates[:self.config.top_k]]
        feats = self._build_features(candidates, ctx)
        try:
            import pandas as pd  # type: ignore
            df = pd.DataFrame(feats)
            preds = self._model.predict(df)
            scored = {c.code: float(preds[i]) for i, c in enumerate(candidates)}
            return sorted(scored, key=scored.get, reverse=True)[:self.config.top_k]  # type: ignore
        except Exception as e:
            logger.warning("ML prediction failed: %s, using fallback", e)
            return self._fallback_score(candidates, ctx)
