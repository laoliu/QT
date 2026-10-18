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
        rows.append({"open": close, "high": close + spread, "low": close - spread,
                     "close": close, "volume": 100_000})
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
