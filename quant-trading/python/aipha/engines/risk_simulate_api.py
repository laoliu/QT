"""
API bridge for MDD Gradient Risk Controller + Volatility Scaler.

Reads JSON from QUANT_CONFIG env var or stdin, runs simulation on
the provided NAV / returns series, and writes JSON to stdout.

Actions:
  simulate — Run full MDD gradient + vol scaling simulation on a NAV series
"""

from __future__ import annotations

import json
import math
import os
import sys

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


def get_input() -> dict:
    raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    return json.loads(raw)


def action_simulate(config: dict) -> dict:
    from aipha.engines.mdd_gradient_risk import MddGradientController, MddGradientConfig
    from aipha.engines.vol_scaling import VolScaler, VolScalingConfig

    nav_series = config.get("nav_series", [])
    if len(nav_series) < 5:
        return {"error": "需要至少 5 个 NAV 数据点"}

    nav_series = [float(v) for v in nav_series]
    dates = config.get("dates")  # optional YYYYMMDD array

    # MDD config
    mdd_cfg = config.get("mdd_config", {})
    mdd_controller = MddGradientController(MddGradientConfig(
        l1_threshold=float(mdd_cfg.get("l1_threshold", 0.12)),
        l2_threshold=float(mdd_cfg.get("l2_threshold", 0.18)),
        recovery_threshold=float(mdd_cfg.get("recovery_threshold", 0.06)),
        l1_position_scale=float(mdd_cfg.get("l1_position_scale", 0.50)),
        l2_position_scale=float(mdd_cfg.get("l2_position_scale", 0.00)),
        recovery_ramp_days=int(mdd_cfg.get("recovery_ramp_days", 5)),
    ))

    # Vol scaling config
    vol_cfg = config.get("vol_config", {})
    vol_scaler = VolScaler(VolScalingConfig(
        target_vol=float(vol_cfg.get("target_vol", 0.18)),
        lookback=int(vol_cfg.get("lookback", 60)),
        max_leverage=float(vol_cfg.get("max_leverage", 1.5)),
        min_scale=float(vol_cfg.get("min_scale", 0.10)),
    ))

    # Run simulation
    original_nav = []
    controlled_nav = []
    drawdown_series = []
    mdd_scale_series = []
    vol_scale_series = []
    combined_scale_series = []
    state_log = []

    controlled_current = nav_series[0]
    prev_nav = nav_series[0]

    for i, nav in enumerate(nav_series):
        dt = int(dates[i]) if dates and i < len(dates) else None

        # Daily return
        daily_ret = (nav / prev_nav - 1) if prev_nav > 0 and i > 0 else 0.0

        # Get MDD scale
        mdd_scale = mdd_controller.update(nav, dt)

        # Get vol scale
        vol_scale = vol_scaler.update(daily_ret)

        # Combined scale = min(mdd, vol) — conservative approach
        combined = min(mdd_scale, vol_scale)

        # Simulate controlled NAV
        if i > 0:
            controlled_ret = daily_ret * combined
            controlled_current *= (1 + controlled_ret)

        # Compute drawdown for display
        peak = max(nav_series[:i + 1])
        dd = (peak - nav) / peak if peak > 0 else 0.0

        original_nav.append(round(nav, 4))
        controlled_nav.append(round(controlled_current, 4))
        drawdown_series.append(round(dd, 4))
        mdd_scale_series.append(round(mdd_scale, 4))
        vol_scale_series.append(round(vol_scale, 4))
        combined_scale_series.append(round(combined, 4))

        prev_nav = nav

    # Stats for original
    orig_returns = [(nav_series[i] / nav_series[i - 1] - 1) for i in range(1, len(nav_series))]
    ctrl_returns = [(controlled_nav[i] / controlled_nav[i - 1] - 1)
                    for i in range(1, len(controlled_nav))
                    if controlled_nav[i - 1] > 0]

    def calc_stats(returns: list[float], navs: list[float]) -> dict:
        if not returns:
            return {}
        n = len(returns)
        mean_r = sum(returns) / n
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1))
        annualized_ret = mean_r * TRADING_DAYS_PER_YEAR
        annualized_vol = std_r * math.sqrt(TRADING_DAYS_PER_YEAR)
        sharpe = annualized_ret / annualized_vol if annualized_vol > 0 else 0

        peak = navs[0]
        max_dd = 0
        for v in navs:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        total_ret = (navs[-1] / navs[0] - 1) if navs[0] > 0 else 0

        return {
            "total_return": round(total_ret, 4),
            "annualized_return": round(annualized_ret, 4),
            "annualized_vol": round(annualized_vol, 4),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "calmar": round(annualized_ret / max_dd, 4) if max_dd > 0 else None,
        }

    return {
        "original_nav": original_nav,
        "controlled_nav": controlled_nav,
        "drawdown_series": drawdown_series,
        "mdd_scale_series": mdd_scale_series,
        "vol_scale_series": vol_scale_series,
        "combined_scale_series": combined_scale_series,
        "state_transitions": mdd_controller._history,
        "original_stats": calc_stats(orig_returns, nav_series),
        "controlled_stats": calc_stats(ctrl_returns, controlled_nav),
        "dates": dates,
    }


ACTIONS = {
    "simulate": action_simulate,
}


if __name__ == "__main__":
    config = get_input()
    action = config.get("action", "simulate")

    try:
        handler = ACTIONS.get(action)
        if handler is None:
            result = {"error": f"未知操作: {action}，支持: {', '.join(ACTIONS.keys())}"}
        else:
            result = handler(config)
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))
