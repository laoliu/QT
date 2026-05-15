"""可转债市场状态轮动策略（牛/熊/中性动态权重）"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

class MarketRegime(Enum):
    BULL = "bull"; NEUTRAL = "neutral"; BEAR = "bear"

@dataclass
class CBRegimeRotationConfig(CBRotationConfig):
    top_k: int = 8; rebalance_days: int = 1; buffer_k: int = 3; max_price: float = 150.0
    max_issue_size: float = 30000.0; min_issue_size: float = 0.0; min_turnover: float = 10.0
    min_days_to_maturity: int = 60; exclude_st: bool = True; exclude_called: bool = True
    max_premium: float = 80.0; regime_lookback: int = 20; bull_threshold: float = 0.03
    bear_threshold: float = -0.02; momentum_lookback: int = 10; surge_lookback: int = 20
    reversal_lookback: int = 5; warmup_bars: int = 25
    bull_w_size: float = 0.10; bull_w_momentum: float = 0.35; bull_w_surge: float = 0.30
    bull_w_premium: float = 0.10; bull_w_price: float = 0.05; bull_w_reversal: float = 0.10
    neutral_w_size: float = 0.25; neutral_w_momentum: float = 0.15; neutral_w_surge: float = 0.10
    neutral_w_premium: float = 0.20; neutral_w_price: float = 0.15; neutral_w_reversal: float = 0.15
    bear_w_size: float = 0.15; bear_w_momentum: float = 0.00; bear_w_surge: float = 0.05
    bear_w_premium: float = 0.15; bear_w_price: float = 0.25; bear_w_reversal: float = 0.40
    stop_loss_pct: float = 0.12; min_positions: int = 3; lot_size: int = 10

class CBRegimeRotationStrategy(CBRotationStrategy):
    name = "cb_regime_rotation"
    def __init__(self, config: CBRegimeRotationConfig | None = None, **kw: object):
        super().__init__(config=config or CBRegimeRotationConfig(), **kw)
        self._regime: MarketRegime = MarketRegime.NEUTRAL

    @property
    def config(self) -> CBRegimeRotationConfig: return self._config  # type: ignore

    @property
    def current_regime(self) -> MarketRegime: return self._regime

    @property
    def _current_regime(self) -> MarketRegime: return self._regime

    @_current_regime.setter
    def _current_regime(self, value: MarketRegime) -> None:
        self._regime = value

    def _on_start_extra(self) -> None:
        self._regime = MarketRegime.NEUTRAL

    def _detect_regime(self) -> None:
        cfg = self.config
        if len(self._market_index) < cfg.regime_lookback: return
        idx = list(self._market_index)
        ret = (idx[-1] - idx[-cfg.regime_lookback]) / idx[-cfg.regime_lookback] if idx[-cfg.regime_lookback] > 0 else 0
        if ret > cfg.bull_threshold: self._regime = MarketRegime.BULL
        elif ret < cfg.bear_threshold: self._regime = MarketRegime.BEAR
        else: self._regime = MarketRegime.NEUTRAL

    def _get_regime_weights(self) -> dict[str, float]:
        cfg = self.config; r = self._regime
        prefix = {"BULL": "bull", "NEUTRAL": "neutral", "BEAR": "bear"}[r.name]
        return {k: getattr(cfg, f"{prefix}_w_{k}", 0.0) for k in ("size", "momentum", "surge", "premium", "price", "reversal")}

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # Build equal-weight market index from bar closes
        closes = [bar.close for bar in bars.values() if getattr(bar, "close", None)]
        if closes:
            self._market_index.append(sum(closes) / len(closes))
        self._detect_regime()
        # Update per-symbol histories
        for sym, bar in bars.items():
            if sym not in self._close_history:
                self._close_history[sym] = deque(maxlen=120)
            self._close_history[sym].append(bar.close)
            meta = getattr(bar, "metadata", None) or {}
            amount = getattr(bar, "amount", None) or getattr(bar, "turnover", None) or meta.get("amount", 0.0)
            if sym not in self._turnover_history:
                self._turnover_history[sym] = deque(maxlen=120)
            self._turnover_history[sym].append(amount)

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium: return False
        # Require sufficient history
        if c.code not in self._close_history or len(self._close_history[c.code]) < 2:
            return False
        if c.code not in self._turnover_history or len(self._turnover_history[c.code]) < 2:
            return False
        return True

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config; w = self._get_regime_weights()
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        mom: dict[str, float] = {}; surge: dict[str, float] = {}; rev: dict[str, float] = {}
        for c in candidates:
            h = self._close_history.get(c.code); prices = list(h) if h else []
            mom[c.code] = (prices[-1] - prices[-cfg.momentum_lookback]) / prices[-cfg.momentum_lookback] if len(prices) >= cfg.momentum_lookback and prices[-cfg.momentum_lookback] > 0 else 0
            to = self._turnover_history.get(c.code); tvs = list(to) if to else []
            if len(tvs) >= cfg.surge_lookback:
                r5 = sum(tvs[-5:]) / 5; base = sum(tvs) / len(tvs); surge[c.code] = (r5 - base) / base if base > 0 else 0
            else: surge[c.code] = 0
            rev[c.code] = -((prices[-1] - prices[-cfg.reversal_lookback]) / prices[-cfg.reversal_lookback]) if len(prices) >= cfg.reversal_lookback and prices[-cfg.reversal_lookback] > 0 else 0
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        factors = {"size": size, "momentum": mom, "surge": surge, "premium": prem, "price": price, "reversal": rev}
        ranked = {n: _rank_scores(v) for n, v in factors.items()}
        composite = {c.code: sum(w.get(n, 0) * ranked[n].get(c.code, 0) for n in ranked) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
