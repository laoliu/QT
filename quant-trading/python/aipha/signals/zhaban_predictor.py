"""Limit-up break (炸板) risk predictor.

Scores the probability of a limit-up board breaking using 7 factors:
open count, seal ratio, board time, turnover anomaly, industry
isolation, height decay, and market-cap fit.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["compute_zhaban_risk"]

# -- factor weights (total 100) --
_W_OPEN_COUNT = 25
_W_SEAL_RATIO = 20
_W_BOARD_TIME = 15
_W_TURNOVER = 15
_W_INDUSTRY = 10
_W_HEIGHT = 10
_W_MCAP = 5

# board time benchmarks (hours after midnight, e.g. 9.5 = 09:30)
_EARLY_BOARD = 9.5   # 09:30
_MID_BOARD = 10.5    # 10:30
_LATE_BOARD = 14.0   # 14:00

# optimal market-cap range (亿)
_MCAP_LOW = 30.0
_MCAP_HIGH = 150.0


def _score_open_count(open_count: int) -> float:
    if open_count == 0:
        return 0.0
    if open_count == 1:
        return 40.0
    if open_count == 2:
        return 65.0
    if open_count == 3:
        return 80.0
    return 100.0


def _score_seal_ratio(seal_amount: float, trade_amount: float) -> float:
    if trade_amount <= 0:
        return 50.0
    ratio = seal_amount / trade_amount
    if ratio >= 3.0:
        return 0.0
    if ratio >= 1.5:
        return 20.0
    if ratio >= 0.5:
        return 50.0
    if ratio >= 0.2:
        return 75.0
    return 100.0


def _parse_board_time(time_str: str) -> Optional[float]:
    """Parse 'HH:MM:SS' or 'HHMM' to decimal hours."""
    if not time_str:
        return None
    try:
        parts = time_str.replace(":", "")
        h = int(parts[:2])
        m = int(parts[2:4])
        return h + m / 60.0
    except (ValueError, IndexError):
        return None


def _score_board_time(time_str: str) -> float:
    t = _parse_board_time(time_str)
    if t is None:
        return 50.0
    if t <= _EARLY_BOARD:
        return 0.0
    if t <= _MID_BOARD:
        return 30.0
    if t <= _LATE_BOARD:
        return 60.0
    return 90.0


def _score_turnover(turnover_rate: float) -> float:
    if turnover_rate <= 0:
        return 50.0
    if turnover_rate < 5.0:
        return 10.0
    if turnover_rate < 15.0:
        return 30.0
    if turnover_rate < 30.0:
        return 60.0
    return 90.0


def _score_industry_isolation(
    industry: str, industry_counts: Dict[str, int], total_zt: int,
) -> float:
    count = industry_counts.get(industry, 0)
    if total_zt <= 0:
        return 50.0
    ratio = count / total_zt
    if ratio >= 0.2:
        return 10.0
    if ratio >= 0.1:
        return 30.0
    if count >= 2:
        return 50.0
    return 80.0


def _score_height_decay(board_height: int) -> float:
    if board_height <= 1:
        return 10.0
    if board_height == 2:
        return 30.0
    if board_height == 3:
        return 50.0
    if board_height <= 5:
        return 70.0
    return 90.0


def _score_mcap(market_cap_yi: float) -> float:
    if market_cap_yi <= 0:
        return 50.0
    if _MCAP_LOW <= market_cap_yi <= _MCAP_HIGH:
        return 10.0
    if market_cap_yi < _MCAP_LOW:
        return 40.0
    return 60.0


def compute_zhaban_risk(
    stock: Dict[str, Any],
    industry_counts: Optional[Dict[str, int]] = None,
    total_zt: int = 0,
    env_failure_rate: float = 0.0,
) -> Dict[str, Any]:
    """Compute limit-up break risk score.

    Parameters
    ----------
    stock : dict
        Must contain keys: open_count, seal_amount, trade_amount,
        board_time, turnover_rate, industry, board_height, market_cap_yi.
    industry_counts : dict
        Number of limit-up stocks per industry today.
    total_zt : int
        Total limit-up stocks today.
    env_failure_rate : float
        Overall market failure rate (0-1) as environment modifier.

    Returns
    -------
    dict with keys: score, level, factors (breakdown), risk_factors (list).
    """
    ic = industry_counts or {}
    f1 = _score_open_count(stock.get("open_count", 0))
    f2 = _score_seal_ratio(stock.get("seal_amount", 0), stock.get("trade_amount", 0))
    f3 = _score_board_time(stock.get("board_time", ""))
    f4 = _score_turnover(stock.get("turnover_rate", 0))
    f5 = _score_industry_isolation(stock.get("industry", ""), ic, total_zt)
    f6 = _score_height_decay(stock.get("board_height", 1))
    f7 = _score_mcap(stock.get("market_cap_yi", 0))

    raw = (
        f1 * _W_OPEN_COUNT + f2 * _W_SEAL_RATIO + f3 * _W_BOARD_TIME
        + f4 * _W_TURNOVER + f5 * _W_INDUSTRY + f6 * _W_HEIGHT + f7 * _W_MCAP
    ) / 100.0

    # environment modifier
    env_mod = env_failure_rate * 15.0 if env_failure_rate > 0.3 else 0.0
    score = min(100.0, raw + env_mod)

    if score >= 70:
        level = "极高"
    elif score >= 50:
        level = "高"
    elif score >= 30:
        level = "中"
    else:
        level = "低"

    risk_factors: List[str] = []
    if f1 >= 60:
        risk_factors.append(f"多次开板({stock.get('open_count', 0)}次)")
    if f2 >= 60:
        risk_factors.append("封单比偏低")
    if f3 >= 60:
        risk_factors.append("封板时间偏晚")
    if f4 >= 60:
        risk_factors.append("换手率异常偏高")
    if f5 >= 60:
        risk_factors.append("行业孤立")
    if f6 >= 60:
        risk_factors.append(f"连板高度衰减({stock.get('board_height', 1)}板)")

    return {
        "score": round(score, 1),
        "level": level,
        "factors": {
            "open_count": round(f1, 1),
            "seal_ratio": round(f2, 1),
            "board_time": round(f3, 1),
            "turnover": round(f4, 1),
            "industry_isolation": round(f5, 1),
            "height_decay": round(f6, 1),
            "market_cap": round(f7, 1),
        },
        "risk_factors": risk_factors,
    }
