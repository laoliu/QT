"""Tests for tick_flow and attack_slope engines."""

import math

from engines.tick_flow import TickFlowTracker, classify_tick_direction
from engines.attack_slope import AttackSlopeCalculator


# ── classify_tick_direction tests ────────────────────────────────

def test_buy_at_ask():
    assert classify_tick_direction(10.5, 10.3, 10.5, 10.4) is True

def test_sell_at_bid():
    assert classify_tick_direction(10.3, 10.3, 10.5, 10.4) is False

def test_price_up_infer_buy():
    assert classify_tick_direction(10.5, 0, 0, 10.3) is True

def test_price_down_infer_sell():
    assert classify_tick_direction(10.3, 0, 0, 10.5) is False

def test_tie_defaults_buy():
    assert classify_tick_direction(10.0, 0, 0, 10.0) is True


# ── TickFlowTracker basic tests ──────────────────────────────────

def test_first_tick_returns_none():
    tracker = TickFlowTracker()
    result = tracker.update("600519", 100.0, 10000, 1000000, 95.0)
    assert result is None


def test_second_tick_returns_result():
    tracker = TickFlowTracker()
    tracker.update("600519", 100.0, 10000, 1000000, 95.0)
    result = tracker.update("600519", 101.0, 20000, 2100000, 95.0)
    assert result is not None
    assert result.delta_volume == 10000
    assert abs(result.delta_amount - 1100000) < 0.01
    # 1.1M >= 1M threshold → large order
    assert result.main_buy_amount > 0  # price went up → buy


def test_large_order_accumulation():
    tracker = TickFlowTracker(large_order_threshold=500_000)
    tracker.update("000001", 10.0, 0, 0, 9.5)

    # Tick 1: buy side, amount = 800K (large)
    result = tracker.update("000001", 10.2, 50000, 800000, 9.5, ask_price=10.2)
    assert result.main_buy_amount == 800000
    assert result.main_sell_amount == 0
    assert result.main_net == 800000

    # Tick 2: sell side, amount = 600K (large)
    result = tracker.update("000001", 9.8, 100000, 1400000, 9.5, bid_price=9.8)
    assert result.main_sell_amount == 600000
    assert result.main_net == 200000  # 800K - 600K


def test_small_order_not_counted():
    tracker = TickFlowTracker(large_order_threshold=1_000_000)
    tracker.update("000001", 10.0, 0, 0, 9.5)

    # 500K < 1M threshold → not counted as large order
    result = tracker.update("000001", 10.2, 50000, 500000, 9.5)
    assert result.main_buy_amount == 0
    assert result.main_sell_amount == 0


def test_sweep_detection():
    tracker = TickFlowTracker(
        sweep_volume_threshold=1_000_000,
        sweep_change_low=0.08,
        sweep_change_high=0.09,
    )
    pre_close = 10.0
    tracker.update("300001", 10.0, 0, 0, pre_close)

    # Price at +8.5% → in sweep zone, large volume → sweep!
    result = tracker.update(
        "300001", 10.85, 1_500_000, 16_000_000, pre_close,
        ask_price=10.85,  # buy side
    )
    assert result.change_pct > 0.08
    assert result.change_pct < 0.09
    assert result.is_sweep_tick is True
    assert result.sweep_triggered is True


def test_sweep_not_triggered_outside_zone():
    tracker = TickFlowTracker(
        sweep_volume_threshold=1_000_000,
        sweep_change_low=0.08,
        sweep_change_high=0.09,
    )
    pre_close = 10.0
    tracker.update("600519", 10.0, 0, 0, pre_close)

    # Price at +5% → outside sweep zone
    result = tracker.update(
        "600519", 10.5, 2_000_000, 20_000_000, pre_close,
        ask_price=10.5,
    )
    assert result.is_sweep_tick is False
    assert result.sweep_triggered is False


def test_daily_reset():
    tracker = TickFlowTracker()
    tracker.update("600519", 100.0, 10000, 1000000, 95.0, date="20260315")
    tracker.update("600519", 101.0, 20000, 2100000, 95.0, date="20260315")

    # New day → resets state
    result = tracker.update("600519", 50.0, 5000, 250000, 48.0, date="20260316")
    assert result is None  # first tick of new day


def test_no_new_volume_still_returns():
    tracker = TickFlowTracker()
    tracker.update("000001", 10.0, 10000, 100000, 9.5)
    # Same volume → delta_vol = 0
    result = tracker.update("000001", 10.1, 10000, 100000, 9.5)
    assert result is not None
    assert result.delta_volume == 0


def test_get_main_net():
    tracker = TickFlowTracker(large_order_threshold=100)
    tracker.update("600519", 100.0, 0, 0, 95.0)
    tracker.update("600519", 101.0, 10000, 500000, 95.0, ask_price=101.0)
    assert tracker.get_main_net("600519") == 500000
    assert tracker.get_main_net("nonexistent") == 0.0


# ── AttackSlopeCalculator tests ──────────────────────────────────

def test_slope_no_data():
    calc = AttackSlopeCalculator()
    assert calc.get_angle("600519") == 0.0
    assert calc.has_data("600519") is False


def test_slope_insufficient_data():
    calc = AttackSlopeCalculator(window_sec=300)
    calc.record("600519", 34200, 10.0)
    # Only 1 point → not enough
    assert calc.has_data("600519") is False


def test_slope_basic_calculation():
    calc = AttackSlopeCalculator(window_sec=300, scale_factor=300)
    # t=0: price=10.0, t=300: price=10.3 (3% rise in 5 min)
    calc.record("600519", 34200, 10.0)
    calc.record("600519", 34500, 10.3)

    result = calc.compute("600519")
    assert result.valid is True
    assert result.price_ref == 10.0
    assert result.price_now == 10.3

    # Slope = (10.3-10.0) / (10.0 * 5) = 0.006
    # Angle = atan(0.006 * 300) * 180/pi = atan(1.8) * 57.3 ≈ 60.9°
    assert 55 < result.angle < 65


def test_slope_flat_is_zero():
    calc = AttackSlopeCalculator(window_sec=300, scale_factor=300)
    calc.record("000001", 34200, 10.0)
    calc.record("000001", 34500, 10.0)

    result = calc.compute("000001")
    assert result.valid is True
    assert result.angle == 0.0


def test_slope_negative():
    calc = AttackSlopeCalculator(window_sec=300, scale_factor=300)
    calc.record("000001", 34200, 10.0)
    calc.record("000001", 34500, 9.7)

    result = calc.compute("000001")
    assert result.valid is True
    assert result.angle < 0


def test_slope_60_degree_threshold():
    """Verify ~2.9% rise in 5 min ≈ 60° with default scale=300."""
    calc = AttackSlopeCalculator(window_sec=300, scale_factor=300)
    calc.record("600519", 34200, 100.0)
    calc.record("600519", 34500, 102.9)  # +2.9%

    angle = calc.get_angle("600519")
    # Should be around 60°
    assert 58 < angle < 63


def test_slope_steep_attack():
    """5% rise in 5 min should give angle >> 60°."""
    calc = AttackSlopeCalculator(window_sec=300, scale_factor=300)
    calc.record("600519", 34200, 100.0)
    calc.record("600519", 34500, 105.0)  # +5%

    angle = calc.get_angle("600519")
    assert angle > 70


def test_slope_history_trimming():
    calc = AttackSlopeCalculator(window_sec=300, max_history_sec=600)
    # Add many points
    for i in range(100):
        calc.record("600519", 34200 + i * 10, 100.0 + i * 0.01)

    history = calc._histories.get("600519", [])
    # Should have trimmed old data (only keep last 600 sec)
    latest_ts = history[-1][0]
    for ts, _ in history:
        assert ts >= latest_ts - 600


def test_slope_tolerance():
    calc = AttackSlopeCalculator(window_sec=300, tolerance_ratio=0.5)
    # Point at t=0, then gap, then point at t=400 (no point near t=100)
    calc.record("600519", 34200, 10.0)
    calc.record("600519", 34600, 10.3)  # 400s gap

    result = calc.compute("600519", 34600)
    # target_ts = 34600 - 300 = 34300
    # closest point is 34200, diff = 100 < 300 * 0.5 = 150 → valid
    assert result.valid is True


def test_slope_beyond_tolerance():
    calc = AttackSlopeCalculator(window_sec=300, tolerance_ratio=0.3)
    # Only one old point very far from target
    calc.record("600519", 33000, 10.0)
    calc.record("600519", 34600, 10.3)

    result = calc.compute("600519", 34600)
    # target_ts = 34300, closest = 33000, diff = 1300 > 300*0.3=90 → invalid
    assert result.valid is False


def test_slope_reset():
    calc = AttackSlopeCalculator()
    calc.record("600519", 34200, 10.0)
    calc.record("600519", 34500, 10.3)
    assert calc.has_data("600519") is True

    calc.reset("600519")
    assert calc.has_data("600519") is False


def test_slope_reset_all():
    calc = AttackSlopeCalculator()
    calc.record("600519", 34200, 10.0)
    calc.record("600519", 34500, 10.3)
    calc.record("000001", 34200, 5.0)
    calc.record("000001", 34500, 5.1)

    calc.reset_all()
    assert not calc._histories


if __name__ == "__main__":
    import traceback
    tests = [f for f in dir() if f.startswith("test_")]
    passed = 0
    failed = 0
    for name in sorted(tests):
        try:
            globals()[name]()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
