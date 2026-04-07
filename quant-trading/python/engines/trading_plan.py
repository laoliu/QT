"""
trading_plan — Per-stock daily trading plan generator.

Generates actionable trading plans for watchlist/portfolio stocks:
- Entry zone, target price, stop-loss
- Key technical levels
- Upcoming events/catalysts
- Risk/reward assessment

Input:
  {
    "action": "generate" | "batch",
    "symbol": "600519",
    "symbols": "600519,000858",
    "entry_price": 1680
  }

Output:
  { "plans": [...] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_trading_plan(config: dict) -> dict:
    """Dispatch trading plan generation."""
    action = config.get("action", "generate")

    if action == "generate":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _generate_plan(symbol, config)
    elif action == "batch":
        symbols_raw = config.get("symbols", "")
        if isinstance(symbols_raw, list):
            symbols = symbols_raw
        else:
            symbols = [s.strip() for s in str(symbols_raw).split(",") if s.strip()]
        if not symbols:
            return {"error": "请提供股票代码列表 (symbols)"}
        return _batch_plans(symbols, config)
    else:
        return {"error": f"未知action: {action}"}


def _generate_plan(symbol: str, config: dict) -> dict:
    """Generate a single-stock daily trading plan."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    plan: dict[str, Any] = {
        "symbol": symbol,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }

    # 1. Get current quote and recent klines
    try:
        kdf = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if kdf is None or kdf.empty:
            return {"error": f"无法获取{symbol}的K线数据"}

        kdf = kdf.tail(60)
        closes = kdf["收盘"].tolist()
        highs = kdf["最高"].tolist()
        lows = kdf["最低"].tolist()
        volumes = kdf["成交量"].tolist()

        current = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else current
        plan["name"] = ""  # Will be filled below
        plan["current_price"] = current
        plan["prev_close"] = prev_close

        # Trend assessment
        ma5 = _ma(closes, 5)
        ma10 = _ma(closes, 10)
        ma20 = _ma(closes, 20)
        ma60 = _ma(closes, 60)

        plan["trend"] = {
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "direction": _assess_trend(closes),
            "price_vs_ma20": round((current / ma20 - 1) * 100, 2) if ma20 else None,
        }

        # Key levels
        recent_high = max(highs[-20:])
        recent_low = min(lows[-20:])
        prev_high = highs[-2] if len(highs) >= 2 else current
        prev_low = lows[-2] if len(lows) >= 2 else current

        # ATR for stop-loss calculation
        atr = _calc_atr(highs, lows, closes, 14)

        # Support levels (nearest below current price)
        supports = sorted([
            p for p in [ma5, ma10, ma20, ma60, recent_low, prev_low]
            if p is not None and p < current * 0.998
        ], reverse=True)[:3]

        # Resistance levels (nearest above current price)
        resistances = sorted([
            p for p in [ma5, ma10, ma20, ma60, recent_high, prev_high]
            if p is not None and p > current * 1.002
        ])[:3]

        plan["key_levels"] = {
            "supports": [round(s, 2) for s in supports],
            "resistances": [round(r, 2) for r in resistances],
            "recent_high_20d": round(recent_high, 2),
            "recent_low_20d": round(recent_low, 2),
            "atr_14": round(atr, 2) if atr else None,
        }

        # Trading recommendation
        entry_price = config.get("entry_price")
        if not entry_price:
            # Suggest entry zone
            if supports:
                entry_low = supports[0] * 0.995
                entry_high = supports[0] * 1.005
            else:
                entry_low = current * 0.98
                entry_high = current * 1.00

            plan["entry_zone"] = {
                "low": round(entry_low, 2),
                "high": round(entry_high, 2),
                "note": "建议在支撑位附近分批进入",
            }
        else:
            plan["entry_zone"] = {
                "low": round(float(entry_price) * 0.995, 2),
                "high": round(float(entry_price) * 1.005, 2),
                "note": "基于用户指定入场价",
            }

        # Stop-loss
        entry_ref = entry_price if entry_price else (supports[0] if supports else current)
        if entry_ref:
            entry_ref = float(entry_ref)
            if atr:
                stop_loss = round(entry_ref - 2 * atr, 2)
            else:
                stop_loss = round(entry_ref * 0.95, 2)

            # Target price (2:1 risk-reward minimum)
            risk = entry_ref - stop_loss
            target_1 = round(entry_ref + 2 * risk, 2)
            target_2 = round(entry_ref + 3 * risk, 2)

            plan["stop_loss"] = stop_loss
            plan["targets"] = {
                "target_1": target_1,
                "target_2": target_2,
                "risk_reward_1": "2:1",
                "risk_reward_2": "3:1",
            }
            plan["risk_per_share"] = round(risk, 2)

        # Volume analysis
        vol_5 = _ma(volumes, 5)
        vol_20 = _ma(volumes, 20)
        plan["volume"] = {
            "avg_5d": int(vol_5) if vol_5 else None,
            "avg_20d": int(vol_20) if vol_20 else None,
            "ratio_5_20": round(vol_5 / vol_20, 2) if vol_5 and vol_20 else None,
            "signal": "放量" if vol_5 and vol_20 and vol_5 > vol_20 * 1.3 else
                      "缩量" if vol_5 and vol_20 and vol_5 < vol_20 * 0.7 else "正常",
        }

        # RSI
        rsi = _rsi(closes, 14)
        plan["rsi_14"] = rsi
        plan["rsi_signal"] = (
            "超买" if rsi and rsi > 70 else
            "超卖" if rsi and rsi < 30 else
            "中性"
        )

    except Exception as e:
        errors.append(f"K线分析失败: {str(e)[:100]}")

    # 2. Get stock name
    try:
        info_df = ak.stock_individual_info_em(symbol=symbol)
        if info_df is not None and not info_df.empty:
            for _, row in info_df.iterrows():
                if str(row.iloc[0]) == "股票简称":
                    plan["name"] = str(row.iloc[1])
                    break
    except Exception:
        pass

    plan["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    result: dict[str, Any] = {
        "action": "generate",
        "plan": plan,
    }
    if errors:
        result["errors"] = errors
    return result


def _batch_plans(symbols: list[str], config: dict) -> dict:
    """Generate trading plans for multiple stocks."""
    plans = []
    errors: list[str] = []

    for symbol in symbols[:10]:  # Limit to 10 stocks
        result = _generate_plan(symbol, config)
        if "error" in result:
            errors.append(f"{symbol}: {result['error']}")
        elif "plan" in result:
            plans.append(result["plan"])
            if "errors" in result:
                errors.extend(result["errors"])

    result: dict[str, Any] = {
        "action": "batch",
        "plans": plans,
        "count": len(plans),
    }
    if errors:
        result["errors"] = errors
    return result


def _assess_trend(closes: list[float]) -> str:
    if len(closes) < 20:
        return "数据不足"
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20

    if ma5 > ma20:
        if closes[-1] > closes[-5]:
            return "强势上升"
        return "上升趋势"
    elif ma5 < ma20:
        if closes[-1] < closes[-5]:
            return "加速下跌"
        return "下降趋势"
    return "横盘震荡"


def _ma(data: list, period: int) -> float | None:
    if len(data) < period:
        return None
    return round(sum(data[-period:]) / period, 4)


def _calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return round(f, 4) if abs(f) < 1e15 else None
    except (ValueError, TypeError):
        return None


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_trading_plan(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
