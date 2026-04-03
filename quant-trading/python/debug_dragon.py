#!/usr/bin/env python3
"""Debug script for dragon return strategy — traces rejection reasons."""
import sys
sys.path.insert(0, ".")

from tests.test_dragon_return_strategy import _generate_dragon_return_scenario, _run, _make_data
import backtrader as bt
from strategies.dragon_return_strategy import DragonReturnStrategy

bars = _generate_dragon_return_scenario()
print(f"Total bars: {len(bars)}")

# First run the standard way
strat, cerebro = _run(bars)
signals = strat.get_signals()
print(f"Standard run signals: {len(signals)}")
for s in signals:
    print(f"  {s['date']} {s['direction']}: {s['reason']}")

if not signals:
    print("\n=== Tracing entry logic ===")

    class DebugDragonReturn(DragonReturnStrategy):
        def _try_entry(self, close, volume):
            klines = self._kline_history
            lookback = self.p.lookback
            bar_n = len(klines)

            if len(klines) < lookback + 2:
                return

            wave = self._detect_first_wave(klines, lookback)
            if wave is None:
                print(f"  bar {bar_n}: no wave detected (klines[-1]={klines[-1]['close']:.2f})", file=sys.stderr)
                return

            wave_base, wave_peak, wave_gain, board_count, peak_idx = wave

            if board_count < self.p.min_boards:
                print(f"  bar {bar_n}: boards={board_count} < {self.p.min_boards} (peak_idx={peak_idx}, peak={wave_peak:.2f} base={wave_base:.2f})", file=sys.stderr)
                return
            if wave_gain < self.p.min_first_wave_gain:
                print(f"  bar {bar_n}: gain={wave_gain:.2%} < {self.p.min_first_wave_gain:.0%}", file=sys.stderr)
                return

            pb_klines = klines[peak_idx + 1:-1]
            pullback = self._check_pullback(pb_klines, wave_base, wave_peak, wave_gain, klines, peak_idx)
            if pullback is None:
                print(f"  bar {bar_n}: no pullback (peak_idx={peak_idx}, pb_bars={len(pb_klines)})", file=sys.stderr)
                return

            pullback_days, pullback_low, retrace_ratio, vol_shrunk = pullback
            if pullback_days < self.p.min_pullback_days:
                print(f"  bar {bar_n}: pb_days={pullback_days} < {self.p.min_pullback_days}", file=sys.stderr)
                return
            if pullback_days > self.p.max_pullback_days:
                print(f"  bar {bar_n}: pb_days={pullback_days} > {self.p.max_pullback_days}", file=sys.stderr)
                return
            if retrace_ratio > self.p.max_retrace:
                print(f"  bar {bar_n}: retrace={retrace_ratio:.2%} > {self.p.max_retrace:.0%}", file=sys.stderr)
                return
            if not vol_shrunk:
                print(f"  bar {bar_n}: vol NOT shrunk", file=sys.stderr)
                return

            support_score = self._check_support(close, wave_base, wave_peak)
            if support_score < 1:
                print(f"  bar {bar_n}: support={support_score} < 1", file=sys.stderr)
                return

            if not pb_klines:
                return
            avg_pb_vol = sum(k["volume"] for k in pb_klines) / len(pb_klines)
            if avg_pb_vol <= 0:
                return
            vol_recovery = volume / avg_pb_vol
            if vol_recovery < self.p.vol_recovery_mult:
                print(f"  bar {bar_n}: vol_recovery={vol_recovery:.1f} < {self.p.vol_recovery_mult}", file=sys.stderr)
                return

            print(f"  bar {bar_n}: ALL PASS! boards={board_count} gain={wave_gain:.1%} "
                  f"pb={pullback_days}d retrace={retrace_ratio:.1%} support={support_score} "
                  f"vol_rec={vol_recovery:.1f}x", file=sys.stderr)
            super()._try_entry(close, volume)

    cerebro2 = bt.Cerebro()
    cerebro2.adddata(_make_data(bars))
    cerebro2.addstrategy(DebugDragonReturn)
    cerebro2.broker.setcash(1_000_000)
    results2 = cerebro2.run()
    sys.stderr.flush()
    dsig = results2[0].get_signals()
    print(f"\nDebug run signals: {len(dsig)}")
    for s in dsig:
        print(f"  {s['date']} {s['direction']}: {s['reason']}")
