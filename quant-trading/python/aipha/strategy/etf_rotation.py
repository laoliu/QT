"""ETF 轮动策略 — 动量/波动/成交量/相关性/反转 多因子 + 可选情绪择时"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class ETFRotationConfig:
    top_k: int = 3
    rebalance_days: int = 20
    weight_momentum: float = 0.35
    weight_volatility: float = 0.25
    weight_volume: float = 0.10
    weight_reversion: float = 0.00
    weight_correlation: float = 0.30
    momentum_lookback: int = 60
    volatility_lookback: int = 20
    reversion_lookback: int = 5
    trend_ma_period: int = 120
    stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.08
    cash_reserve_pct: float = 0.02
    max_single_pct: float = 0.50
    lot_size: int = 100
    min_amount: float = 0.0
    buffer_k: int = 1
    inverse_vol_weighting: bool = False
    correlation_lookback: int = 60
    correlation_hard_exclude: float = 0.60
    benchmark_returns_path: str = ""
    # 情绪择时
    regime_enabled: bool = False
    regime_reference: str = "510300.SH"
    regime_momentum_window: int = 60
    regime_bull_threshold: float = 0.05
    regime_bear_threshold: float = -0.05
    bull_weight_momentum: float = 0.50
    bull_weight_volatility: float = 0.15
    bull_weight_correlation: float = 0.25
    bull_weight_volume: float = 0.10
    bear_weight_momentum: float = 0.10
    bear_weight_volatility: float = 0.50
    bear_weight_correlation: float = 0.25
    bear_weight_volume: float = 0.15


class ETFRotationStrategy(Strategy):
    """ETF 轮动 — FAA (Flexible Asset Allocation) 风格多因子"""
    name = "etf_rotation"

    def __init__(self, config: ETFRotationConfig | None = None, **kwargs: Any):
        self._config = config or ETFRotationConfig()
        self._price_hist: dict[str, list[float]] = {}
        self._volume_hist: dict[str, list[float]] = {}
        self._positions: dict[str, int] = {}
        self._peak_prices: dict[str, float] = {}
        self._bar_count = 0
        self._benchmark_returns: dict[str, float] = {}

    @property
    def config(self) -> ETFRotationConfig:
        return self._config

    def on_start(self) -> None:
        self._price_hist.clear()
        self._volume_hist.clear()
        self._positions.clear()
        self._peak_prices.clear()
        self._bar_count = 0
        if self._config.benchmark_returns_path:
            self._load_benchmark_returns(self._config.benchmark_returns_path)

    def on_fill(self, symbol: str, side: Side, qty: int, price: float) -> None:
        cur = self._positions.get(symbol, 0)
        if side == Side.BUY:
            self._positions[symbol] = cur + qty
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, 0), price)
        else:
            self._positions[symbol] = max(0, cur - qty)

    def on_bar(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        self._update_history(bars)

        intents = self._check_stop_loss(bars, context)
        if self._bar_count % self._config.rebalance_days != 0:
            return intents

        scored = self._score_etfs(bars, context)
        targets = self._select_targets(scored, context)
        intents.extend(self._generate_rebalance(targets, bars, context))
        return intents

    def _detect_regime(self) -> str:
        cfg = self._config
        ref_hist = self._price_hist.get(cfg.regime_reference)
        if not ref_hist or len(ref_hist) < cfg.regime_momentum_window:
            return "NEUTRAL"
        old = ref_hist[-cfg.regime_momentum_window]
        if old <= 0:
            return "NEUTRAL"
        mom = (ref_hist[-1] - old) / old
        if mom > cfg.regime_bull_threshold:
            return "BULL"
        if mom < cfg.regime_bear_threshold:
            return "BEAR"
        return "NEUTRAL"

    def _get_regime_weights(self) -> tuple[float, float, float, float]:
        cfg = self._config
        if not cfg.regime_enabled:
            return cfg.weight_momentum, cfg.weight_volatility, cfg.weight_correlation, cfg.weight_volume
        regime = self._detect_regime()
        if regime == "BULL":
            return cfg.bull_weight_momentum, cfg.bull_weight_volatility, cfg.bull_weight_correlation, cfg.bull_weight_volume
        if regime == "BEAR":
            return cfg.bear_weight_momentum, cfg.bear_weight_volatility, cfg.bear_weight_correlation, cfg.bear_weight_volume
        return cfg.weight_momentum, cfg.weight_volatility, cfg.weight_correlation, cfg.weight_volume

    def _update_history(self, bars: dict[str, "Bar"]) -> None:
        for sym, bar in bars.items():
            self._price_hist.setdefault(sym, []).append(bar.close)
            vol = getattr(bar, "volume", None) or getattr(bar, "amount", 0)
            self._volume_hist.setdefault(sym, []).append(float(vol or 0))
            if sym in self._positions and self._positions[sym] > 0:
                self._peak_prices[sym] = max(self._peak_prices.get(sym, 0), bar.close)

    def _load_benchmark_returns(self, path: str) -> None:
        try:
            import csv
            from pathlib import Path
            p = Path(path)
            if p.exists():
                with p.open() as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        dt = row.get("date", "")
                        ret = row.get("return", "0")
                        self._benchmark_returns[dt] = float(ret)
        except Exception as e:
            logger.warning("Failed to load benchmark returns: %s", e)

    def _calc_correlation_with_benchmark(self, sym: str, current_date: str) -> float | None:
        # 简化实现
        return None

    def _check_stop_loss(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        cfg = self._config
        intents: list[OrderIntent] = []
        for sym, qty in list(self._positions.items()):
            if qty <= 0 or sym not in bars:
                continue
            bar = bars[sym]
            peak = self._peak_prices.get(sym, bar.close)
            if peak > 0:
                dd = (peak - bar.close) / peak
                if dd > cfg.trailing_stop_pct:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.EXIT,
                        side=Side.SELL, quantity=qty,
                    ))
        return intents

    def _score_etfs(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[tuple[str, float]]:
        w_mom, w_vol, w_corr, w_volume = self._get_regime_weights()
        scored: list[tuple[str, float]] = []

        for sym in bars:
            hist = self._price_hist.get(sym, [])
            if len(hist) < max(self._config.momentum_lookback, self._config.volatility_lookback):
                continue
            mom, vol, rev, vol_score = self._calc_raw_factors(sym, hist)
            score = w_mom * mom + w_vol * (1 - vol) + w_volume * vol_score + self._config.weight_reversion * rev
            scored.append((sym, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @staticmethod
    def _rank_normalize_list(values: list[float]) -> list[float]:
        n = len(values)
        if n == 0:
            return []
        indexed = sorted(enumerate(values), key=lambda x: x[1])
        result = [0.0] * n
        for rank, (idx, _) in enumerate(indexed):
            result[idx] = rank / max(n - 1, 1)
        return result

    def _calc_raw_factors(self, sym: str, hist: list[float]) -> tuple[float, float, float, float]:
        cfg = self._config
        # 动量
        lb = cfg.momentum_lookback
        old = hist[-lb] if len(hist) >= lb else hist[0]
        mom = (hist[-1] - old) / old if old > 0 else 0

        # 波动率
        vlb = cfg.volatility_lookback
        recent = hist[-vlb:]
        returns = [(recent[i] - recent[i - 1]) / recent[i - 1]
                    for i in range(1, len(recent)) if recent[i - 1] > 0]
        vol = math.sqrt(sum(r ** 2 for r in returns) / max(len(returns), 1)) if returns else 0

        # 反转
        rlb = cfg.reversion_lookback
        old_r = hist[-rlb] if len(hist) >= rlb else hist[0]
        rev = -(hist[-1] - old_r) / old_r if old_r > 0 else 0

        # 成交量 (近期 vs 长期)
        vol_hist = self._volume_hist.get(sym, [])
        if len(vol_hist) >= 20:
            recent_vol = sum(vol_hist[-5:]) / 5
            long_vol = sum(vol_hist[-20:]) / 20
            vol_score = min(2.0, recent_vol / long_vol) if long_vol > 0 else 1.0
        else:
            vol_score = 1.0

        return mom, vol, rev, vol_score

    def _select_targets(self, scored: list[tuple[str, float]], context: StrategyContext) -> list[str]:
        return [s[0] for s in scored[:self._config.top_k]]

    def _generate_rebalance(
        self, target_syms: list[str], bars: dict[str, "Bar"],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        # 卖出不在目标中的
        for sym, qty in list(self._positions.items()):
            if qty > 0 and sym not in target_syms:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.EXIT,
                    side=Side.SELL, quantity=qty,
                ))
        # 等权 (或反波动率) 买入
        if not target_syms:
            return intents
        weights = self._calc_ivol_weights(target_syms) if self._config.inverse_vol_weighting else {
            s: 1.0 / len(target_syms) for s in target_syms
        }
        cash = getattr(context, "cash", 0.0) * (1.0 - self._config.cash_reserve_pct)
        lot = self._config.lot_size
        for sym in target_syms:
            if sym not in bars:
                continue
            if self._positions.get(sym, 0) > 0:
                continue
            alloc = cash * weights.get(sym, 0)
            price = bars[sym].close
            if price > 0:
                qty = int(alloc / price / lot) * lot
                if qty > 0:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.ENTRY,
                        side=Side.BUY, quantity=qty,
                    ))
        return intents

    def _calc_ivol_weights(self, symbols: list[str]) -> dict[str, float]:
        inv_vols: dict[str, float] = {}
        for sym in symbols:
            hist = self._price_hist.get(sym, [])
            if len(hist) < 20:
                inv_vols[sym] = 1.0
                continue
            recent = hist[-20:]
            returns = [(recent[i] - recent[i - 1]) / recent[i - 1]
                        for i in range(1, len(recent)) if recent[i - 1] > 0]
            vol = math.sqrt(sum(r ** 2 for r in returns) / max(len(returns), 1)) if returns else 0.01
            inv_vols[sym] = 1.0 / max(vol, 0.001)
        total = sum(inv_vols.values()) or 1.0
        return {s: v / total for s, v in inv_vols.items()}
