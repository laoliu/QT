"""
一夜持股法（隔夜价差战法）筛选器。

尾盘买入、次日开盘卖出的短线价差策略，通过8层过滤选出高确定性标的：
  1. 筛选涨幅：当日涨幅 2~5%（温和上涨，非涨停）
  2. 筛选量比：量比 ≥ 1.5（资金活跃）
  3. 筛选换手率：换手率 3~15%（流动性好但不过度投机）
  4. 筛选流通值：流通市值 20~200 亿（中盘股，拉升空间大）
  5. 筛选成交量：近 5 日成交量递增趋势
  6. ɸѡK线形态：实体阳线，上影线短（买盘强势）
  7. 筛选分时图：收盘价 > 当日均价（尾盘走强）
  8. 筛选均价线：收盘价站上 5 日和 10 日均线
"""

from __future__ import annotations

from typing import Any

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class OvernightScreener(BaseScreener):
    """一夜持股法：隔夜价差筛选策略。"""

    screener_id = "overnight"
    screener_name = "一夜持股法（隔夜价差）"
    screener_description = (
        "尾盘价差战法，8层过滤选股：涨幅2~5%、量比≥1.5、"
        "换手率3~15%、流通值20~200亿、量能递增、阳线实体、"
        "收盘价>均价、站上5日/10日均线。适合尾盘介入隔日卖出。"
    )
    supported_markets = ["cn_stock"]

    # Default thresholds — can be overridden via params
    _DEFAULTS: dict[str, Any] = {
        # Step 1: 涨幅
        "change_pct_min": 2.0,
        "change_pct_max": 5.0,
        # Step 2: 量比
        "volume_ratio_min": 1.5,
        # Step 3: 换手率
        "turnover_rate_min": 3.0,
        "turnover_rate_max": 15.0,
        # Step 4: 流通市值（亿元）
        "circulating_mv_min": 20.0,
        "circulating_mv_max": 200.0,
        # Step 5: 成交量递增天数
        "volume_trend_days": 5,
        # Step 6: K 线形态 — 上影线占实体比例上限
        "upper_shadow_ratio_max": 0.5,
        # Step 7: 收盘价 > 均价（VWAP）
        # Step 8: 均线
        "ma_short": 5,
        "ma_long": 10,
        # 最少需要的 K 线天数
        "min_bars": 15,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        cfg = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []

        for code, data in stock_data.items():
            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {}

            # ── Step 1: 涨幅 2~5% ─────────────────────────
            change_pct = data.get("change_pct")
            if change_pct is None:
                continue
            try:
                change_pct = float(change_pct)
            except (ValueError, TypeError):
                continue
            if not (cfg["change_pct_min"] <= change_pct <= cfg["change_pct_max"]):
                continue
            score += 1.0
            signals.append(f"涨幅 {change_pct:.2f}%（{cfg['change_pct_min']}~{cfg['change_pct_max']}%）")
            factors["change_pct"] = change_pct

            # ── Step 2: 量比 ≥ 1.5 ────────────────────────
            klines = data.get("klines", [])
            if len(klines) < cfg["min_bars"]:
                continue

            # Calculate volume ratio: today's volume / avg of past 5 days
            volumes = [float(k.get("volume", 0)) for k in klines if k.get("volume")]
            if len(volumes) < 6:
                continue
            today_vol = volumes[-1]
            avg_vol_5 = sum(volumes[-6:-1]) / 5
            if avg_vol_5 <= 0:
                continue
            volume_ratio = today_vol / avg_vol_5

            if volume_ratio < cfg["volume_ratio_min"]:
                continue
            score += 1.0
            signals.append(f"量比 {volume_ratio:.2f}（≥{cfg['volume_ratio_min']}）")
            factors["volume_ratio"] = round(volume_ratio, 2)

            # ── Step 3: 换手率 3~15% ──────────────────────
            turnover_rate = data.get("turnover_rate")
            if turnover_rate is None:
                # Estimate from volume & circulating shares if available
                circ_val = data.get("circulating_market_val")
                price = data.get("price")
                if circ_val and price and float(price) > 0:
                    circ_shares = float(circ_val) / float(price)
                    if circ_shares > 0:
                        turnover_rate = (today_vol / circ_shares) * 100
            if turnover_rate is None:
                continue
            try:
                turnover_rate = float(turnover_rate)
            except (ValueError, TypeError):
                continue
            if not (cfg["turnover_rate_min"] <= turnover_rate <= cfg["turnover_rate_max"]):
                continue
            score += 1.0
            signals.append(f"换手率 {turnover_rate:.2f}%（{cfg['turnover_rate_min']}~{cfg['turnover_rate_max']}%）")
            factors["turnover_rate"] = round(turnover_rate, 2)

            # ── Step 4: 流通市值 20~200 亿 ────────────────
            circ_mv = data.get("circulating_market_val")
            if circ_mv is None:
                continue
            try:
                circ_mv_yi = float(circ_mv) / 1e8  # Ԫ → 亿
            except (ValueError, TypeError):
                continue
            if not (cfg["circulating_mv_min"] <= circ_mv_yi <= cfg["circulating_mv_max"]):
                continue
            score += 1.0
            signals.append(f"流通值 {circ_mv_yi:.1f}亿（{cfg['circulating_mv_min']}~{cfg['circulating_mv_max']}亿）")
            factors["circulating_mv_yi"] = round(circ_mv_yi, 1)

            # ── Step 5: 成交量递增趋势 ────────────────────
            trend_days = cfg["volume_trend_days"]
            if len(volumes) >= trend_days:
                recent_vols = volumes[-trend_days:]
                increasing_count = sum(
                    1 for i in range(1, len(recent_vols)) if recent_vols[i] > recent_vols[i - 1]
                )
                # At least (n-2) of (n-1) comparisons should be increasing
                vol_trend_ok = increasing_count >= (trend_days - 2)
            else:
                vol_trend_ok = False

            if vol_trend_ok:
                score += 1.5
                signals.append(f"近{trend_days}日量能递增（{increasing_count}/{trend_days - 1}日放量）")
                factors["volume_trend_up"] = increasing_count
            else:
                # Partial credit if volume is at least above average
                if today_vol > avg_vol_5 * 1.2:
                    score += 0.5
                    signals.append("量能高于均量但未持续递增")
                else:
                    continue  # Volume trend is a core filter

            # ── Step 6: K 线形态（实体阳线 + 短上影） ─────
            last_k = klines[-1]
            k_open = float(last_k.get("open", 0))
            k_close = float(last_k.get("close", 0))
            k_high = float(last_k.get("high", 0))
            k_low = float(last_k.get("low", 0))

            if k_open <= 0 or k_close <= k_open:
                continue  # Must be a bullish candle (close > open)

            body = k_close - k_open
            upper_shadow = k_high - k_close
            if body <= 0:
                continue

            upper_ratio = upper_shadow / body
            if upper_ratio > cfg["upper_shadow_ratio_max"]:
                continue  # Upper shadow too long → selling pressure

            score += 1.5
            signals.append(
                f"阳线实体（上影/实体={upper_ratio:.2f}，≤{cfg['upper_shadow_ratio_max']}）"
            )
            factors["upper_shadow_ratio"] = round(upper_ratio, 2)
            factors["candle_body_pct"] = round(body / k_open * 100, 2)

            # ── Step 7: 收盘价 > 当日均价（VWAP 近似） ───
            k_turnover = float(last_k.get("amount", 0)) or float(data.get("turnover", 0))
            k_volume = float(last_k.get("volume", 0)) or today_vol
            if k_volume > 0 and k_turnover > 0:
                vwap = k_turnover / k_volume
                if k_close > vwap:
                    score += 1.0
                    signals.append(f"收盘价({k_close:.2f}) > 均价({vwap:.2f})，尾盘走强")
                    factors["vwap"] = round(vwap, 2)
                else:
                    # Fallback: use (high + low + close) / 3 as typical price proxy
                    typical = (k_high + k_low + k_close) / 3
                    if k_close >= typical:
                        score += 0.5
                        signals.append(f"收盘价接近均价水平")
                    else:
                        continue
            else:
                # No turnover data, use typical price
                typical = (k_high + k_low + k_close) / 3
                if k_close >= typical:
                    score += 0.5
                    signals.append("收盘价 ≥ 典型价格")
                else:
                    continue

            # ── Step 8: 站上 5 日和 10 日均线 ─────────────
            closes = [float(k.get("close", 0)) for k in klines if k.get("close")]
            ma_s = cfg["ma_short"]
            ma_l = cfg["ma_long"]

            if len(closes) >= ma_l:
                ma_short_val = sum(closes[-ma_s:]) / ma_s
                ma_long_val = sum(closes[-ma_l:]) / ma_l
                above_short = k_close > ma_short_val
                above_long = k_close > ma_long_val
                ma_bullish = ma_short_val > ma_long_val  # 5MA > 10MA (golden cross)

                if above_short and above_long:
                    score += 1.5
                    signals.append(
                        f"站上{ma_s}日均线({ma_short_val:.2f})和{ma_l}日均线({ma_long_val:.2f})"
                    )
                    if ma_bullish:
                        score += 0.5
                        signals.append(f"{ma_s}日均线 > {ma_l}日均线（多头排列）")
                    factors["ma5"] = round(ma_short_val, 2)
                    factors["ma10"] = round(ma_long_val, 2)
                elif above_short:
                    score += 0.5
                    signals.append(f"站上{ma_s}日均线，但未站上{ma_l}日均线")
                else:
                    continue  # Must at least be above short MA
            else:
                continue

            # ── Final: price for reference ────────────────
            factors["price"] = k_close

            results.append(
                ScreenerResult(
                    stock_id=code,
                    name=data.get("name", ""),
                    market=market,
                    score=score,
                    factors=factors,
                    signals=signals,
                )
            )

        # Sort by score descending, then by change_pct (higher momentum first)
        results.sort(
            key=lambda r: (r.score, r.factors.get("change_pct", 0)),
            reverse=True,
        )
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1
        return results[:limit]
