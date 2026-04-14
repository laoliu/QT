"""市场状态门控（策略叠加工具类）"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

class GateResult(NamedTuple):
    regime: str; scale_applied: float; n_candidates: int; is_gated: bool

@dataclass
class RegimeGateConfig:
    bull_scale: float = 1.0; neutral_scale: float = 0.7; bear_scale: float = 0.5
    min_scale: float = 0.1; max_scale: float = 2.0

CB_GATE_CONFIG = RegimeGateConfig(bull_scale=1.0, neutral_scale=0.7, bear_scale=0.3)
EQUITY_GATE_CONFIG = RegimeGateConfig(bull_scale=1.0, neutral_scale=0.8, bear_scale=0.5)

class RegimeStrategyGate:
    def __init__(self, config: RegimeGateConfig | None = None):
        self._config = config or RegimeGateConfig()
        self._last_regime: str = "neutral"
    @property
    def config(self) -> RegimeGateConfig: return self._config
    def get_scale(self, regime: str) -> float:
        cfg = self._config; self._last_regime = regime
        scale_map = {"bull": cfg.bull_scale, "neutral": cfg.neutral_scale, "bear": cfg.bear_scale}
        s = scale_map.get(regime.lower(), cfg.neutral_scale)
        return max(cfg.min_scale, min(cfg.max_scale, s))
    def scale_scores(self, scores: dict[str, float], regime: str) -> dict[str, float]:
        s = self.get_scale(regime)
        return {k: v * s for k, v in scores.items()}
    def apply(self, scores: dict[str, float], regime: str) -> GateResult:
        scaled = self.scale_scores(scores, regime)
        is_gated = self.get_scale(regime) < 1.0
        return GateResult(regime=regime, scale_applied=self.get_scale(regime), n_candidates=len(scaled), is_gated=is_gated)
    def get_regime_summary(self) -> dict[str, str]:
        return {"last_regime": self._last_regime}

def make_cb_gate(neutral_scale: float = 0.7, bear_scale: float = 0.3) -> RegimeStrategyGate:
    return RegimeStrategyGate(RegimeGateConfig(neutral_scale=neutral_scale, bear_scale=bear_scale))
