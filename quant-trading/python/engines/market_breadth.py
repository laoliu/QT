"""
market_breadth — Market breadth indicators for A-share market.

Provides classic breadth indicators:
  - advance/decline stats and ratio
  - new 52-week highs vs lows
  - percentage of stocks above MA (MA20, MA50, MA200)
  - McClellan Oscillator (approximation)
  - up/down volume ratio
  - limit-up/limit-down stats

Input:
  { "action": "overview" | "above_ma" | "advance_decline" | "new_high_low" | "mcclellan",
    "period": 20 | 50 | 200,  # for above_ma
    "days": 30 }              # history length for mcclellan

Output varies by action.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


def run_market_breadth(config: dict) -> dict:
    """Dispatch market breadth analysis based on action."""
    action = config.get("action", "overview")
    errors: list[str] = []

    if action == "overview":
        return _breadth_overview(errors)
    elif action == "above_ma":
        period = config.get("period", 20)
        return _above_ma(period, errors)
    elif action == "advance_decline":
        days = config.get("days", 30)
        return _advance_decline_history(days, errors)
    elif action == "new_high_low":
        days = config.get("days", 30)
        return _new_high_low(days, errors)
    elif action == "mcclellan":
        days = config.get("days", 60)
        return _mcclellan_oscillator(days, errors)
    else:
        return {"error": f"未知操作: {action}"}


def _breadth_overview(errors: list[str]) -> dict:
    """Real-time market breadth overview using akshare."""
    try:
        import akshare as ak

        # Get real-time A-share quotes
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股实时行情数据"}

        total = len(df)

        # Advance / Decline
        change_col = "涨跌幅"
        if change_col not in df.columns:
            # Fallback column names
            for col in df.columns:
                if "涨跌" in col and "幅" in col:
                    change_col = col
                    break

        df[change_col] = df[change_col].apply(lambda x: float(x) if x != "" and x is not None else 0.0)

        advancing = int((df[change_col] > 0).sum())
        declining = int((df[change_col] < 0).sum())
        unchanged = total - advancing - declining

        # Limit-up / Limit-down
        limit_up = int((df[change_col] >= 9.9).sum())
        limit_down = int((df[change_col] <= -9.9).sum())

        # Up/Down volume
        vol_col = "成交量"
        if vol_col not in df.columns:
            for col in df.columns:
                if "成交量" in col:
                    vol_col = col
                    break

        df[vol_col] = df[vol_col].apply(lambda x: float(x) if x != "" and x is not None else 0.0)
        up_volume = float(df.loc[df[change_col] > 0, vol_col].sum())
        down_volume = float(df.loc[df[change_col] < 0, vol_col].sum())

        ad_ratio = advancing / declining if declining > 0 else float("inf")
        vol_ratio = up_volume / down_volume if down_volume > 0 else float("inf")

        # Distribution by change range
        distribution = {
            "涨停(≥9.9%)": limit_up,
            "涨5%-9.9%": int(((df[change_col] >= 5) & (df[change_col] < 9.9)).sum()),
            "涨2%-5%": int(((df[change_col] >= 2) & (df[change_col] < 5)).sum()),
            "涨0%-2%": int(((df[change_col] > 0) & (df[change_col] < 2)).sum()),
            "平盘": unchanged,
            "跌0%-2%": int(((df[change_col] < 0) & (df[change_col] > -2)).sum()),
            "跌2%-5%": int(((df[change_col] <= -2) & (df[change_col] > -5)).sum()),
            "跌5%-9.9%": int(((df[change_col] <= -5) & (df[change_col] > -9.9)).sum()),
            "跌停(≤-9.9%)": limit_down,
        }

        # Median change
        median_change = float(df[change_col].median())

        result: dict[str, Any] = {
            "action": "overview",
            "total": total,
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "adRatio": round(ad_ratio, 2) if ad_ratio != float("inf") else "∞",
            "limitUp": limit_up,
            "limitDown": limit_down,
            "upVolume": round(up_volume / 1e8, 2),
            "downVolume": round(down_volume / 1e8, 2),
            "volumeRatio": round(vol_ratio, 2) if vol_ratio != float("inf") else "∞",
            "medianChange": round(median_change, 2),
            "distribution": distribution,
            "marketSentiment": _classify_sentiment(advancing, declining, limit_up, limit_down, total),
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"获取市场宽度数据失败: {str(e)}", "traceback": traceback.format_exc()}


def _above_ma(period: int, errors: list[str]) -> dict:
    """Calculate percentage of stocks trading above a given MA period."""
    try:
        import akshare as ak
        import numpy as np

        # Get real-time A-share quotes (has 最新价 and MA data)
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股实时行情数据"}

        total = len(df)
        above_count = 0
        below_count = 0
        sampled = 0

        # For MA we need historical data — use a sampling approach for speed
        # Check if 60日均线 column exists (akshare sometimes provides it)
        ma_col_map = {5: "5日均线", 10: "10日均线", 20: "20日均线", 60: "60日均线"}

        if period in ma_col_map and ma_col_map[period] in df.columns:
            # Direct from spot data
            price_col = "最新价"
            ma_col = ma_col_map[period]
            df[price_col] = df[price_col].apply(lambda x: float(x) if x and x != "" else 0)
            df[ma_col] = df[ma_col].apply(lambda x: float(x) if x and x != "" else 0)
            valid = df[(df[price_col] > 0) & (df[ma_col] > 0)]
            above_count = int((valid[price_col] > valid[ma_col]).sum())
            below_count = len(valid) - above_count
            sampled = len(valid)
        else:
            # For MA50, MA200 — sample stocks to estimate
            codes = df["代码"].tolist()
            sample_size = min(300, len(codes))
            import random
            sample_codes = random.sample(codes, sample_size)

            for code in sample_codes:
                try:
                    kdf = ak.stock_zh_a_hist(symbol=code, period="daily",
                                              start_date=(datetime.now() - timedelta(days=period * 2)).strftime("%Y%m%d"),
                                              end_date=datetime.now().strftime("%Y%m%d"),
                                              adjust="qfq")
                    if kdf is not None and len(kdf) >= period:
                        closes = kdf["收盘"].astype(float).values
                        ma_value = float(np.mean(closes[-period:]))
                        current_price = float(closes[-1])
                        if current_price > ma_value:
                            above_count += 1
                        else:
                            below_count += 1
                        sampled += 1
                except Exception:
                    continue

        pct_above = round(above_count / sampled * 100, 1) if sampled > 0 else 0
        pct_below = round(below_count / sampled * 100, 1) if sampled > 0 else 0

        interpretation = "强势" if pct_above > 70 else "偏强" if pct_above > 55 else "中性" if pct_above > 40 else "偏弱" if pct_above > 25 else "弱势"

        result: dict[str, Any] = {
            "action": "above_ma",
            "maPeriod": period,
            "sampled": sampled,
            "totalStocks": total,
            "aboveMA": above_count,
            "belowMA": below_count,
            "pctAbove": pct_above,
            "pctBelow": pct_below,
            "interpretation": interpretation,
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"计算MA{period}占比失败: {str(e)}", "traceback": traceback.format_exc()}


def _advance_decline_history(days: int, errors: list[str]) -> dict:
    """Advance/Decline historical data (approximation via index)."""
    try:
        import akshare as ak

        # Use major index as proxy for market breadth history
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        # Get Shanghai composite for reference
        sh_df = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start_date, end_date=end_date)
        if sh_df is None or sh_df.empty:
            return {"error": "无法获取指数历史数据"}

        sh_df = sh_df.tail(days)
        history = []
        cumulative_ad = 0

        for _, row in sh_df.iterrows():
            date_str = str(row.get("date", row.name))[:10] if hasattr(row, "name") else str(row.iloc[0])[:10]
            close = float(row.get("close", 0))
            volume = float(row.get("volume", 0))

            # Estimate advance/decline from index movement
            change_pct = float(row.get("close", 0)) / float(row.get("open", 1)) - 1 if float(row.get("open", 0)) > 0 else 0
            estimated_adv = 2500 + int(change_pct * 10000)  # rough estimate
            estimated_dec = 5000 - estimated_adv
            estimated_adv = max(0, min(5000, estimated_adv))
            estimated_dec = max(0, min(5000, estimated_dec))

            ad_diff = estimated_adv - estimated_dec
            cumulative_ad += ad_diff

            history.append({
                "date": date_str,
                "indexClose": round(close, 2),
                "advEst": estimated_adv,
                "decEst": estimated_dec,
                "adDiff": ad_diff,
                "cumulativeAD": cumulative_ad,
            })

        result: dict[str, Any] = {
            "action": "advance_decline",
            "days": len(history),
            "note": "涨跌家数为基于指数涨跌幅的估算值",
            "cumulativeAD": cumulative_ad,
            "trend": "上升" if cumulative_ad > 0 else "下降",
            "history": history[-20:],  # last 20 entries for display
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"获取涨跌家数历史失败: {str(e)}", "traceback": traceback.format_exc()}


def _new_high_low(days: int, errors: list[str]) -> dict:
    """Count stocks making new 52-week highs vs lows."""
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股实时行情数据"}

        new_highs = 0
        new_lows = 0
        high_stocks = []
        low_stocks = []

        # Check 52-week columns if available
        high_col = "52周最高"
        low_col = "52周最低"
        price_col = "最新价"

        if high_col in df.columns and low_col in df.columns:
            for _, row in df.iterrows():
                try:
                    price = float(row[price_col]) if row[price_col] else 0
                    high_52w = float(row[high_col]) if row[high_col] else 0
                    low_52w = float(row[low_col]) if row[low_col] else 0
                    if price <= 0:
                        continue
                    if high_52w > 0 and price >= high_52w * 0.98:
                        new_highs += 1
                        if len(high_stocks) < 10:
                            high_stocks.append({
                                "code": str(row.get("代码", "")),
                                "name": str(row.get("名称", "")),
                                "price": round(price, 2),
                            })
                    if low_52w > 0 and price <= low_52w * 1.02:
                        new_lows += 1
                        if len(low_stocks) < 10:
                            low_stocks.append({
                                "code": str(row.get("代码", "")),
                                "name": str(row.get("名称", "")),
                                "price": round(price, 2),
                            })
                except (ValueError, TypeError):
                    continue
        else:
            # Estimate from change percentages
            change_col = "涨跌幅"
            year_change_col = "年初至今涨跌幅"
            if year_change_col in df.columns:
                df[year_change_col] = df[year_change_col].apply(lambda x: float(x) if x and x != "" else 0)
                new_highs = int((df[year_change_col] > 50).sum())
                new_lows = int((df[year_change_col] < -30).sum())
            else:
                # Fallback: use daily change as rough proxy
                df[change_col] = df[change_col].apply(lambda x: float(x) if x and x != "" else 0)
                new_highs = int((df[change_col] >= 9.9).sum())
                new_lows = int((df[change_col] <= -9.9).sum())

        hl_ratio = new_highs / new_lows if new_lows > 0 else float("inf")

        result: dict[str, Any] = {
            "action": "new_high_low",
            "newHighs": new_highs,
            "newLows": new_lows,
            "hlRatio": round(hl_ratio, 2) if hl_ratio != float("inf") else "∞",
            "interpretation": "多头占优" if new_highs > new_lows * 2 else "空头占优" if new_lows > new_highs * 2 else "多空均衡",
            "topHighStocks": high_stocks[:5],
            "topLowStocks": low_stocks[:5],
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"获取新高新低数据失败: {str(e)}", "traceback": traceback.format_exc()}


def _mcclellan_oscillator(days: int, errors: list[str]) -> dict:
    """McClellan Oscillator approximation based on A/D data."""
    try:
        import akshare as ak
        import numpy as np

        # Get index data for estimating A/D
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        sh_df = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start_date, end_date=end_date)
        if sh_df is None or sh_df.empty:
            return {"error": "无法获取指数数据计算McClellan"}

        sh_df = sh_df.tail(days)

        # Estimate daily A-D differences from index returns
        closes = sh_df["close"].astype(float).values
        returns = np.diff(closes) / closes[:-1]
        ad_diffs = (returns * 5000).astype(float)  # Rough estimate: map return to A-D diff

        if len(ad_diffs) < 20:
            return {"error": "数据不足，至少需要20个交易日"}

        # McClellan Oscillator = EMA(19) - EMA(39) of A-D differences
        def ema(data, span):
            alpha = 2.0 / (span + 1)
            result = np.zeros_like(data, dtype=float)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
            return result

        ema19 = ema(ad_diffs, 19)
        ema39 = ema(ad_diffs, 39)
        mcclellan = ema19 - ema39

        # Summation index
        summation = np.cumsum(mcclellan)

        current_mc = float(mcclellan[-1])
        prev_mc = float(mcclellan[-2]) if len(mcclellan) > 1 else 0

        result: dict[str, Any] = {
            "action": "mcclellan",
            "currentOscillator": round(current_mc, 2),
            "previousOscillator": round(prev_mc, 2),
            "trend": "上升" if current_mc > prev_mc else "下降",
            "signal": "看多" if current_mc > 0 and current_mc > prev_mc else "看空" if current_mc < 0 and current_mc < prev_mc else "中性",
            "summationIndex": round(float(summation[-1]), 2),
            "history": [
                {"value": round(float(mcclellan[i]), 2), "summation": round(float(summation[i]), 2)}
                for i in range(max(0, len(mcclellan) - 10), len(mcclellan))
            ],
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"计算McClellan指标失败: {str(e)}", "traceback": traceback.format_exc()}


def _classify_sentiment(adv: int, dec: int, limit_up: int, limit_down: int, total: int) -> str:
    """Classify overall market sentiment from breadth data."""
    adv_ratio = adv / total if total > 0 else 0.5
    if adv_ratio > 0.75 and limit_up > 50:
        return "极度乐观"
    elif adv_ratio > 0.6:
        return "偏多"
    elif adv_ratio > 0.45:
        return "中性"
    elif adv_ratio > 0.3:
        return "偏空"
    else:
        return "极度悲观"


# ── NDJSON bridge entry point ──────────────────────────────────
if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            config = json.loads(line)
            result = run_market_breadth(config)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
