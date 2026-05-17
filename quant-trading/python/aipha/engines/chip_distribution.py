"""
chip_distribution — 筹码分布 (CYQ) analysis via AkShare / 东方财富.

Provides winner ratio (获利比例), average cost (平均成本), and chip
concentration (集中度) for A-share stocks.  These metrics help identify
support/resistance zones and institutional accumulation/distribution.

Usage:
  from aipha.engines.chip_distribution import fetch_chip_distribution
  cyq = fetch_chip_distribution("600519")
  # cyq = {
  #   "benefitPart": 0.82,      # 82% of holders are profitable
  #   "avgCost": 1580.23,       # average chip cost
  #   "concentration70": 0.08,  # 70% chip concentration (tight = controlled)
  #   "concentration90": 0.15,
  #   "support": 1520.0,        # 90% chip low boundary → support
  #   "resistance": 1640.0,     # 90% chip high boundary → resistance
  #   "costAbovePct": 0.18,     # % chips above current price (trapped sellers)
  #   "chipDensityZone": [1520.0, 1640.0],
  # }
"""

from __future__ import annotations

import os
import traceback
import urllib.request
from typing import Any

# Proxy bypass for Chinese data hosts — same as institution_activity.py
for _k in (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests
    import requests.utils
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
except ImportError:
    pass


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        num = float(val)
        return default if num != num else num
    except (TypeError, ValueError):
        return default


def fetch_chip_distribution(symbol: str) -> dict[str, Any] | None:
    """Fetch latest CYQ data for *symbol* (6-digit A-share code).

    Returns a dict of chip metrics, or ``None`` on any failure so callers
    can gracefully skip CYQ enrichment.

    Runs in a subprocess to isolate mini-racer (V8 engine) crashes that
    would otherwise abort the entire Python process on Python 3.13+.
    """
    import json
    import subprocess
    import sys

    script = f"""
import json, sys
try:
    import akshare as ak
    df = ak.stock_cyq_em(symbol="{symbol}", adjust="")
    if df is None or df.empty:
        print(json.dumps(None))
        sys.exit(0)
    last = df.iloc[-1]
    def sf(v):
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
    bp = sf(last.get("获利比例"))
    print(json.dumps({{
        "benefitPart": round(bp, 4),
        "avgCost": round(sf(last.get("平均成本")), 2),
        "concentration70": round(sf(last.get("70集中度")), 4),
        "concentration90": round(sf(last.get("90集中度")), 4),
        "support": round(sf(last.get("90成本-低")), 2),
        "resistance": round(sf(last.get("90成本-高")), 2),
        "chipDensityZone": [round(sf(last.get("70成本-低")), 2), round(sf(last.get("70成本-高")), 2)],
        "costAbovePct": round(1.0 - bp, 4) if bp > 0 else None,
    }}))
except Exception as e:
    print(json.dumps(None))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout.strip())
        return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def score_chip_distribution(
    cyq: dict[str, Any],
    current_price: float,
) -> tuple[float, list[str], str]:
    """Score a stock based on its chip distribution metrics.

    Returns ``(score_delta, reasons, evidence_type)`` where
    *evidence_type* is ``"build"`` / ``"distribution"`` / ``"neutral"``.

    Heuristics:
    - High winner ratio (>90%) at high price → distribution risk
    - Low winner ratio (<15%) → washout / bottom accumulation
    - Tight concentration (70% conc < 0.08) → strong control
    - Price near support (90-low) → support zone
    - Price near resistance (90-high) → pressure zone
    """
    score = 0.0
    reasons: list[str] = []
    evidence = "neutral"

    benefit = cyq.get("benefitPart", 0.5)
    avg_cost = cyq.get("avgCost", 0)
    conc_70 = cyq.get("concentration70", 0.5)
    support = cyq.get("support", 0)
    resistance = cyq.get("resistance", 0)

    # ── Winner ratio signals ──
    if benefit >= 0.92:
        score -= 5
        reasons.append(f"筹码获利比例极高({benefit:.0%})，套牢盘少抛压待释放")
        evidence = "distribution"
    elif benefit >= 0.80:
        score -= 2
        reasons.append(f"筹码获利比例偏高({benefit:.0%})")
        evidence = "distribution"
    elif benefit <= 0.10:
        score += 4
        reasons.append(f"筹码获利比例极低({benefit:.0%})，近底部洗盘区")
        evidence = "build"
    elif benefit <= 0.25:
        score += 2
        reasons.append(f"筹码获利比例低({benefit:.0%})，抛压有限")
        evidence = "build"

    # ── Concentration signals (tight = 庄家控盘) ──
    if conc_70 < 0.06:
        score += 3
        reasons.append(f"筹码高度集中(70%集中度{conc_70:.2%})，控盘度高")
        if evidence == "neutral":
            evidence = "build"
    elif conc_70 < 0.10:
        score += 1
        reasons.append(f"筹码较集中(70%集中度{conc_70:.2%})")
    elif conc_70 > 0.25:
        score -= 1
        reasons.append(f"筹码分散(70%集中度{conc_70:.2%})，共识度低")

    # ── Support / resistance proximity ──
    if current_price > 0 and support > 0 and resistance > 0:
        dist_to_support = (current_price - support) / current_price if current_price > 0 else 1
        dist_to_resistance = (resistance - current_price) / current_price if current_price > 0 else 1

        if dist_to_support <= 0.02:
            score += 2
            reasons.append(f"接近筹码支撑位{support:.2f}")
            if evidence == "neutral":
                evidence = "build"
        elif dist_to_resistance <= 0.02:
            score -= 2
            reasons.append(f"接近筹码压力位{resistance:.2f}")
            if evidence == "neutral":
                evidence = "distribution"

    # ── Average cost vs current price ──
    if avg_cost > 0 and current_price > 0:
        cost_ratio = current_price / avg_cost
        if cost_ratio < 0.85:
            score += 2
            reasons.append(f"现价低于筹码均价{(1-cost_ratio):.0%}，偏低估")
            if evidence == "neutral":
                evidence = "build"
        elif cost_ratio > 1.25:
            score -= 1
            reasons.append(f"现价高出筹码均价{(cost_ratio-1):.0%}")

    return round(score, 1), reasons, evidence
