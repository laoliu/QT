"""
lianban — Consecutive limit-up board (连板) detection engine.

Provides reusable helpers for identifying 涨停 (limit-up), counting
consecutive boards, classifying board types, and scoring board quality.
Also wraps akshare limit-up pool APIs.

Used by: lianban_screener, lianban_relay_strategy, qmt_lianban_monitor,
         lianban_dashboard.
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# Proxy bypass for Chinese data hosts
for _k in (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


# ── Limit-Up Thresholds ──────────────────────────────────────────

def _limit_up_ratio(symbol: str) -> float:
    """Return the daily limit-up percentage based on stock code prefix.

    Rules:
      - 主板 (60xxxx, 00xxxx): 10%
      - 创业板 (30xxxx):       20%
      - 科创板 (688xxx):       20%
      - 北交所 (4xxxxx, 8xxxxx except 688): 30%
      - ST 股 (detected by name, not code): 5% — caller should handle
    """
    code = str(symbol).split(".")[0].lstrip("0") if symbol else ""
    full = str(symbol).split(".")[0]

    if full.startswith("688"):
        return 0.20  # 科创板
    if full.startswith("30"):
        return 0.20  # 创业板
    if full.startswith("4") or full.startswith("8"):
        return 0.30  # 北交所
    return 0.10  # 主板 (沪深)


def _limit_down_ratio(symbol: str) -> float:
    """Return the daily limit-down percentage (same as limit-up)."""
    return _limit_up_ratio(symbol)


# ── Single-Bar Detection ─────────────────────────────────────────

def detect_limit_up(
    row: dict[str, Any],
    prev_close: float | None = None,
    symbol: str = "",
    is_st: bool = False,
) -> bool:
    """Check whether a single K-line bar hit limit-up.

    Args:
        row: K-line bar with open/high/low/close fields.
        prev_close: Previous close price. If None, tries row["prev_close"].
        symbol: Stock code for determining limit percentage.
        is_st: Whether stock is ST (5% limit).

    Returns:
        True if close price reached the limit-up threshold.
    """
    close = _safe_float(row.get("close"))
    if close <= 0:
        return False

    pc = prev_close if prev_close is not None else _safe_float(row.get("prev_close"))
    if pc is None or pc <= 0:
        return False

    ratio = 0.05 if is_st else _limit_up_ratio(symbol)
    # Allow small rounding tolerance (0.3%)
    threshold = 1 + ratio - 0.003
    return close / pc >= threshold


def detect_limit_down(
    row: dict[str, Any],
    prev_close: float | None = None,
    symbol: str = "",
    is_st: bool = False,
) -> bool:
    """Check whether a single K-line bar hit limit-down."""
    close = _safe_float(row.get("close"))
    if close <= 0:
        return False

    pc = prev_close if prev_close is not None else _safe_float(row.get("prev_close"))
    if pc is None or pc <= 0:
        return False

    ratio = 0.05 if is_st else _limit_down_ratio(symbol)
    threshold = 1 - ratio + 0.003
    return close / pc <= threshold


def is_one_word_board(row: dict[str, Any]) -> bool:
    """Check if bar is a 一字板 (open == close == high == low)."""
    o = _safe_float(row.get("open"))
    h = _safe_float(row.get("high"))
    l = _safe_float(row.get("low"))
    c = _safe_float(row.get("close"))
    if o <= 0:
        return False
    return o == c == h == l


# ── Board Type Classification ────────────────────────────────────

def classify_board_type(
    row: dict[str, Any],
    prev_close: float | None = None,
    symbol: str = "",
) -> str | None:
    """Classify the type of limit-up board.

    Returns:
        "一字板" — open == close == high (no chance to buy)
        "T字板" — open == low, but high > open (brief opening)
        "换手板" — normal close at limit, traded range exists (best quality)
        None     — not a limit-up bar
    """
    if not detect_limit_up(row, prev_close, symbol):
        return None

    o = _safe_float(row.get("open"))
    h = _safe_float(row.get("high"))
    l = _safe_float(row.get("low"))
    c = _safe_float(row.get("close"))

    if o == c == h == l:
        return "一字板"
    if abs(o - l) < 0.01 and h > o:
        return "T字板"
    return "换手板"


# ── Consecutive Board Counting ────────────────────────────────────

def count_consecutive_boards(
    klines: list[dict[str, Any]],
    symbol: str = "",
    is_st: bool = False,
) -> int:
    """Count consecutive limit-up days from the most recent bar backwards.

    Args:
        klines: List of K-line bars sorted by date ascending (oldest first).
        symbol: Stock code.
        is_st: Whether stock is ST.

    Returns:
        Number of consecutive limit-up days ending at the latest bar.
        Returns 0 if the latest bar is not limit-up.
    """
    if len(klines) < 2:
        return 0

    count = 0
    for i in range(len(klines) - 1, 0, -1):
        curr = klines[i]
        prev = klines[i - 1]
        prev_close = _safe_float(prev.get("close"))
        if detect_limit_up(curr, prev_close, symbol, is_st):
            count += 1
        else:
            break
    return count


def get_board_history(
    klines: list[dict[str, Any]],
    symbol: str = "",
    is_st: bool = False,
) -> list[dict[str, Any]]:
    """Get detailed info for each bar in the current consecutive board streak.

    Returns list of dicts (newest first) with:
      date, board_type, turnover_rate, volume, close, change_pct
    """
    if len(klines) < 2:
        return []

    boards: list[dict[str, Any]] = []
    for i in range(len(klines) - 1, 0, -1):
        curr = klines[i]
        prev = klines[i - 1]
        prev_close = _safe_float(prev.get("close"))
        if not detect_limit_up(curr, prev_close, symbol, is_st):
            break

        change_pct = ((curr["close"] / prev_close) - 1) * 100 if prev_close > 0 else 0
        # K-line data may have "date" string or "timestamp" (ms epoch)
        bar_date = curr.get("date", "")
        if not bar_date and curr.get("timestamp"):
            try:
                bar_date = datetime.fromtimestamp(curr["timestamp"] / 1000).strftime("%Y-%m-%d")
            except Exception:
                bar_date = ""
        boards.append({
            "date": bar_date,
            "board_type": classify_board_type(curr, prev_close, symbol) or "δ֪",
            "turnover_rate": _safe_float(curr.get("turnover_rate")),
            "volume": _safe_float(curr.get("volume")),
            "amount": _safe_float(curr.get("amount")),
            "close": _safe_float(curr.get("close")),
            "change_pct": round(change_pct, 2),
            "open": _safe_float(curr.get("open")),
            "high": _safe_float(curr.get("high")),
            "low": _safe_float(curr.get("low")),
        })

    return boards  # newest first


# ── Board Quality Scoring ─────────────────────────────────────────

def board_quality_score(
    klines: list[dict[str, Any]],
    symbol: str = "",
    is_st: bool = False,
) -> dict[str, Any]:
    """Compute a quality score for the current consecutive board streak.

    Factors:
      - Board type preference: 换手板 > T字板 > 一字板
      - Turnover rate: 5%-15% optimal (active trading, not too speculative)
      - Volume trend: increasing volume across boards is positive
      - Board count: more boards = more momentum
      - Price position: avoid chasing high-altitude boards

    Returns:
        {
          "total_score": 0-100,
          "board_count": int,
          "factors": { "board_type_score", "turnover_score", "volume_trend_score", ... },
          "boards": [ ...get_board_history... ],
          "warnings": [ str... ],
        }
    """
    boards = get_board_history(klines, symbol, is_st)
    if not boards:
        return {
            "total_score": 0, "board_count": 0,
            "factors": {}, "boards": [], "warnings": ["未检测到连板"],
        }

    board_count = len(boards)
    warnings: list[str] = []

    # ── Factor 1: Board type score (30 points max) ──
    type_scores = {"换手板": 10, "T字板": 7, "一字板": 3, "δ֪": 5}
    type_total = sum(type_scores.get(b["board_type"], 5) for b in boards)
    board_type_score = min(type_total / board_count, 10) * 3  # normalize to 30

    one_word_count = sum(1 for b in boards if b["board_type"] == "一字板")
    if one_word_count == board_count:
        warnings.append("全部一字板，无法参与")
        board_type_score *= 0.3

    # ── Factor 2: Turnover rate score (25 points max) ──
    turnover_rates = [b["turnover_rate"] for b in boards if b["turnover_rate"] > 0]
    if turnover_rates:
        avg_turnover = sum(turnover_rates) / len(turnover_rates)
        # Optimal range: 5-15%
        if 5 <= avg_turnover <= 15:
            turnover_score = 25
        elif 3 <= avg_turnover < 5 or 15 < avg_turnover <= 25:
            turnover_score = 18
        elif avg_turnover > 25:
            turnover_score = 8
            warnings.append(f"换手率过高({avg_turnover:.1f}%)，投机性强")
        else:
            turnover_score = 12
    else:
        avg_turnover = 0
        turnover_score = 10

    # ── Factor 3: Volume trend (20 points max) ──
    volumes = [b["volume"] for b in reversed(boards) if b["volume"] > 0]
    if len(volumes) >= 2:
        # Check if volume is trending up (each board should have more volume)
        increasing = sum(
            1 for i in range(1, len(volumes)) if volumes[i] > volumes[i - 1]
        )
        vol_trend_ratio = increasing / (len(volumes) - 1)
        volume_trend_score = vol_trend_ratio * 20
        if vol_trend_ratio < 0.3:
            warnings.append("量能萎缩，封板持续性存疑")
    else:
        volume_trend_score = 10

    # ── Factor 4: Board count bonus (15 points max) ──
    # 2板 = 10, 3板 = 13, 4板+ = 15 (but higher boards have diminishing returns)
    count_score = min(10 + (board_count - 2) * 3, 15) if board_count >= 2 else 5

    # ── Factor 5: Latest board freshness (10 points max) ──
    latest = boards[0]
    if latest["board_type"] == "换手板":
        freshness = 10
    elif latest["board_type"] == "T字板":
        freshness = 7
    else:
        freshness = 3

    total = board_type_score + turnover_score + volume_trend_score + count_score + freshness
    total = min(total, 100)

    return {
        "total_score": round(total, 1),
        "board_count": board_count,
        "factors": {
            "board_type_score": round(board_type_score, 1),
            "turnover_score": round(turnover_score, 1),
            "volume_trend_score": round(volume_trend_score, 1),
            "count_score": round(count_score, 1),
            "freshness_score": round(freshness, 1),
            "avg_turnover": round(avg_turnover, 1) if turnover_rates else None,
        },
        "boards": boards,
        "warnings": warnings,
    }


# ── Akshare Pool APIs ────────────────────────────────────────────

def fetch_zt_pool(date: str | None = None) -> dict[str, Any]:
    """Fetch current-day limit-up pool (涨停板池) via akshare.

    Returns:
        {"stocks": [{code, name, price, change_pct, turnover_rate, amount,
                     first_board_time, last_board_time, open_count,
                     lian_ban_count, industry}], "date": "...", "error": ...}
    """
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装", "stocks": []}

    try:
        date_str = date or datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or df.empty:
            return {"stocks": [], "date": date_str}

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": _safe_float(row.get("最新价")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "amount": _safe_float(row.get("成交额")),
                "flow_value": _safe_float(row.get("流通市值")),
                "total_value": _safe_float(row.get("总市值")),
                "first_board_time": str(row.get("首次封板时间", "")),
                "last_board_time": str(row.get("最后封板时间", "")),
                "open_count": _safe_int(row.get("炸板次数", row.get("打开次数", 0))),
                "seal_amount": _safe_float(row.get("封单金额", row.get("封板资金", 0))),
                "lian_ban_count": _safe_int(row.get("连板数", 0)),
                "industry": str(row.get("所属行业", "")),
            })
        return {"stocks": stocks, "date": date_str}
    except Exception as e:
        return {"error": f"涨停池获取失败: {e}", "stocks": []}


def fetch_zt_pool_previous(date: str | None = None) -> dict[str, Any]:
    """Fetch yesterday's limit-up stocks (昨日涨停) via akshare.

    Returns:
        {"stocks": [{code, name, price, ...}], "date": "...", "error": ...}
    """
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装", "stocks": []}

    try:
        date_str = date or datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_previous_em(date=date_str)
        if df is None or df.empty:
            return {"stocks": [], "date": date_str}

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": _safe_float(row.get("最新价")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "amount": _safe_float(row.get("成交额")),
                "flow_value": _safe_float(row.get("流通市值")),
                "total_value": _safe_float(row.get("总市值")),
                "open_count": _safe_int(row.get("炸板次数", row.get("打开次数", 0))),
                "lian_ban_count": _safe_int(row.get("昨日连板数", 1)),
                "industry": str(row.get("所属行业", "")),
            })
        return {"stocks": stocks, "date": date_str}
    except Exception as e:
        return {"error": f"昨日涨停获取失败: {e}", "stocks": []}


def fetch_zt_pool_strong(date: str | None = None) -> dict[str, Any]:
    """Fetch strong-stocks pool (强势股池) via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装", "stocks": []}

    try:
        date_str = date or datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_strong_em(date=date_str)
        if df is None or df.empty:
            return {"stocks": [], "date": date_str}

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": _safe_float(row.get("最新价")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "amount": _safe_float(row.get("成交额")),
                "flow_value": _safe_float(row.get("流通市值")),
                "industry": str(row.get("所属行业", "")),
            })
        return {"stocks": stocks, "date": date_str}
    except Exception as e:
        return {"error": f"强势股池获取失败: {e}", "stocks": []}


def fetch_zt_pool_zbgc(date: str | None = None) -> dict[str, Any]:
    """Fetch failed-board pool (炸板股池) via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装", "stocks": []}

    try:
        date_str = date or datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_zbgc_em(date=date_str)
        if df is None or df.empty:
            return {"stocks": [], "date": date_str}

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": _safe_float(row.get("最新价")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "turnover_rate": _safe_float(row.get("换手率")),
                "amount": _safe_float(row.get("成交额")),
                "open_count": _safe_int(row.get("打开次数", 0)),
                "first_board_time": str(row.get("首次封板时间", "")),
                "industry": str(row.get("所属行业", "")),
            })
        return {"stocks": stocks, "date": date_str}
    except Exception as e:
        return {"error": f"炸板股池获取失败: {e}", "stocks": []}


def fetch_dt_pool(date: str | None = None) -> dict[str, Any]:
    """Fetch limit-down pool (跌停板池) via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装", "stocks": []}

    try:
        date_str = date or datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_dtgc_em(date=date_str)
        if df is None or df.empty:
            return {"stocks": [], "date": date_str}

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": _safe_float(row.get("最新价")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "amount": _safe_float(row.get("成交额")),
                "industry": str(row.get("所属行业", "")),
            })
        return {"stocks": stocks, "date": date_str}
    except Exception as e:
        return {"error": f"跌停池获取失败: {e}", "stocks": []}


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        num = float(val)
        return default if num != num else num
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
