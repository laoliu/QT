"""Regenerate signal-level fixtures from the original Backtrader strategy classes.

Run from the repository root with Backtrader and pandas installed:
    python scripts/generate_legacy_signal_fixtures.py

The broker uses close fills and zero fees to isolate decision rules. These
fixtures do not establish fill, fee, T+1 or live-execution equivalence.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import backtrader as bt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant-trading" / "python"))
from strategies.ema_cross_strategy import EMACrossStrategy
from strategies.donchian_breakout_strategy import DonchianBreakoutStrategy
from strategies.kdj_strategy import KDJStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.adx_trend_strategy import ADXTrendStrategy
from strategies.dmi_strategy import DMIStrategy
from strategies.dema_strategy import DEMACrossStrategy
from strategies.aroon_strategy import AroonStrategy
from strategies.trix_strategy import TRIXStrategy
from strategies.envelope_strategy import EnvelopeStrategy
from strategies.price_channel_breakout_strategy import PriceChannelBreakoutStrategy
from strategies.mfi_strategy import MFIStrategy
from strategies.roc_strategy import ROCStrategy
from strategies.adaptive_ma_strategy import AdaptiveMAStrategy
from strategies.multi_timeframe_ma_strategy import MultiTimeframeMAStrategy
from strategies.supertrend_strategy import SupertrendStrategy


def bars():
    rows = []
    for i in range(240):
        # Multiple trend reversals, volatility squeezes and isolated jumps.
        amplitude = 0.12 if 80 <= i < 125 else 8
        close = round(100 + amplitude * math.sin(i * 0.28) + 3 * math.sin(i * 0.07), 6)
        if i in (45, 140, 190):
            close -= 15
        if i in (60, 150, 210):
            close += 15
        spread = 0.05 if 80 <= i < 125 else 0.6
        # Asymmetric range shocks exercise the retained Supertrend band resets.
        high = close + (60 if i in (30, 75, 145) else spread)
        low = close - (60 if i in (50, 100, 170) else spread)
        rows.append({"open": close, "high": high, "low": low,
                     "close": close, "volume": 350_000 if i % 11 == 0 else 100_000})
    return pd.DataFrame(rows, index=pd.date_range("2024-01-01", periods=len(rows)))


def run(cls, frame, parameters):
    class Observed(cls):
        def calc_position_size_pct(self, price, pct=0.95):
            self._last_pct = pct * 100
            return super().calc_position_size_pct(price, pct)

        def record_signal(self, direction, price, reason="", symbol=""):
            super().record_signal(direction, price, reason, symbol)
            self._signals[-1]["index"] = len(self.data) - 1
            if direction == "buy":
                self._signals[-1]["sizePct"] = self._last_pct

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(1_000_000)
    cerebro.broker.set_coc(True)
    cerebro.adddata(bt.feeds.PandasData(dataname=frame), name="TEST")
    cerebro.addstrategy(Observed, **parameters)
    result = cerebro.run()[0]
    return [{k: sig[k] for k in ("index", "direction", "sizePct") if k in sig}
            for sig in result.get_signals()]


def main():
    frame = bars()
    cases = []
    classes = [
        ("ema-cross", "ema-cross-long-only", EMACrossStrategy, {"fast_period": 3, "slow_period": 10}),
        ("donchian-breakout", "donchian-long-only", DonchianBreakoutStrategy, {"upper_period": 7, "lower_period": 3}),
        ("kdj", "kdj-long-only", KDJStrategy, {"k_period": 5, "d_period": 2, "j_smooth": 4, "overbought": 75, "oversold": 25}),
        ("bollinger", "bollinger-squeeze-reversion", BollingerStrategy, {"period": 10, "devfactor": 1.5, "squeeze_pct": 80}),
        ("macd", "macd-long-only", MACDStrategy, {"fast_period": 5, "slow_period": 15, "signal_period": 4}),
        ("rsi", "rsi-long-only", RSIStrategy, {"period": 7, "oversold": 25, "overbought": 75}),
        ("adx-trend", "adx-long-only", ADXTrendStrategy, {"period": 7, "adx_threshold": 18}),
        ("dmi", "dmi-long-only", DMIStrategy, {"period": 7}),
        ("dema-cross", "dema-long-only", DEMACrossStrategy, {"fast_period": 5, "slow_period": 15}),
        ("aroon", "aroon-long-only", AroonStrategy, {"period": 12, "upper_threshold": 60, "lower_threshold": 20}),
        ("trix", "trix-long-only", TRIXStrategy, {"period": 7, "signal_period": 4}),
        ("envelope", "envelope-long-only", EnvelopeStrategy, {"ma_period": 10, "pct": 2}),
        ("price-channel-breakout", "price-channel-long-only", PriceChannelBreakoutStrategy,
         {"breakout_period": 7, "exit_period": 3, "vol_confirm": False}),
        ("mfi", "mfi-long-only", MFIStrategy, {"period": 7, "oversold": 30, "overbought": 70}),
        ("roc", "roc-long-only", ROCStrategy, {"period": 6}),
        ("adaptive-ma", "kama-long-only", AdaptiveMAStrategy, {"period": 7, "fast_period": 3, "slow_period": 20}),
        ("multi-tf-ma", "triple-ma-long-only", MultiTimeframeMAStrategy,
         {"short_period": 3, "medium_period": 10, "long_period": 30}),
        ("supertrend", "supertrend-python-long-only", SupertrendStrategy, {"atr_period": 7, "multiplier": 1.5}),
    ]
    for legacy_id, canonical_id, cls, custom in classes:
        for params in ({}, custom):
            expected = run(cls, frame, params)
            assert expected, legacy_id
            source = Path(sys.modules[cls.__module__].__file__)
            cases.append({"legacyId": legacy_id, "canonicalId": canonical_id,
                          "source": source.relative_to(ROOT).as_posix(),
                          "sourceSha256": hashlib.sha256(source.read_text(encoding="utf-8").encode()).hexdigest(),
                          "parameters": params, "expected": expected})
    payload = {"backtraderVersion": bt.__version__, "scope": "signal_rules_close_fills_zero_fees",
               "bars": [{"timestamp": int(ts.timestamp() * 1000), **row.to_dict()}
                        for ts, row in frame.iterrows()], "cases": cases}
    target = ROOT / "quant-trading" / "src" / "engine" / "strategies" / "fixtures" / "legacy-signals.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} signal parity cases to {target}")


if __name__ == "__main__":
    main()
