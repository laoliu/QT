"""双低增强5因子"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class CBDLEnhanced5FConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 135.0; min_turnover: float = 20.0
    min_issue_size: float = 0.0; max_issue_size: float = float("inf"); min_days_to_maturity: int = 30
    exclude_st: bool = True; stop_loss_pct: float = 0.10; buffer_k: int = 2
    force_redeem_threshold: float = 130.0; force_redeem_days: int = 8; exclude_called: bool = True
    min_rating: str = ""; exclude_unrated: bool = False; max_premium: float = 40.0
    dl_percentile_window: int = 120; reversal_window: int = 20; rv_window: int = 20
    rv_percentile_window: int = 120; min_history: int = 20; premium_weight_in_dl: float = 1.0
    w_double_low: float = 0.70; w_dl_percentile: float = 0.0; w_reversal: float = 0.30
    w_rv_percentile: float = 0.0; w_rating: float = 0.0

class CBDLEnhanced5FStrategy(CBRotationStrategy):
    name = "cb_dl_enhanced_5f"
    def __init__(self, config: CBDLEnhanced5FConfig | None = None, **kw: object):
        super().__init__(config=config or CBDLEnhanced5FConfig(), **kw)
        self._dl_history: dict[str, deque[float]] = {}
        self._close_history: dict[str, deque[float]] = {}
    @property
    def config(self) -> CBDLEnhanced5FConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # bars provided as argument
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history:
                self._close_history[sym] = deque(maxlen=max(self.config.reversal_window, self.config.rv_percentile_window, self.config.dl_percentile_window) + 10)
            self._close_history[sym].append(close)
    def _compute_dl(self, c: CandidateInfo) -> float:
        price = getattr(c, "close", 200.0) or 200.0
        prem = getattr(c, "premium_rate", 50.0) or 50.0
        return price + self.config.premium_weight_in_dl * prem
    def _compute_reversal(self, symbol: str) -> float:
        hist = self._close_history.get(symbol)
        if not hist or len(hist) < self.config.reversal_window: return 0.0
        recent = list(hist)[-self.config.reversal_window:]
        return (recent[0] - recent[-1]) / recent[0] if recent[0] > 0 else 0.0
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        dl = {c.code: -self._compute_dl(c) for c in candidates}
        rev = {c.code: self._compute_reversal(c.code) for c in candidates}
        rdl = _rank_scores(dl); rrev = _rank_scores(rev)
        composite = {c.code: cfg.w_double_low * rdl.get(c.code, 0) + cfg.w_reversal * rrev.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
