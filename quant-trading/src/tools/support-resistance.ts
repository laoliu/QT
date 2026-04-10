/**
 * support_resistance — 010880 (AT-6)
 *
 * 080710 (14110306160013)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSupportResistanceTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "支撑阻力位检测 — 08071008160013",
    parameters: {
      type: "object" as const,
      properties: {
        symbol: { type: "string", description: "股票代码" },
        days: { type: "number", description: "分析最近N天数据 (08120)" },
      },
      required: ["symbol"],
    },

    async execute(args: Record<string, unknown>) {
      const symbol = args.symbol as string;
      const days = (args.days as number) ?? 120;
      if (!symbol) return { error: "59 symbol" };

      const script = `
import akshare as ak
import json, sys
import numpy as np

symbol = "${symbol}?
days = ${days}

try:
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
    if df is None or len(df) < 20:
        print(json.dumps({"error": "数据不足"}))
        sys.exit(0)

    df = df.tail(days)
    highs = df['最高'].values.astype(float)
    lows = df['最低'].values.astype(float)
    closes = df['收盘'].values.astype(float)
    volumes = df['00'].values.astype(float)
    current = float(closes[-1])

    supports = []
    resistances = []

    # 1. Recent swing high/low points (local extremes)
    for i in range(2, len(highs) - 2):
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            supports.append({"price": round(float(lows[i]), 2), "type": "swing_low", "strength": 1})
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            resistances.append({"price": round(float(highs[i]), 2), "type": "swing_high", "strength": 1})

    # 2. Volume-weighted price clusters (1600)
    price_range = np.linspace(min(lows), max(highs), 50)
    volume_profile = np.zeros(50)
    for j in range(len(closes)):
        idx = np.argmin(np.abs(price_range - closes[j]))
        volume_profile[idx] += volumes[j]

    # Find peaks in volume profile
    for k in range(1, len(volume_profile) - 1):
        if volume_profile[k] > volume_profile[k-1] and volume_profile[k] > volume_profile[k+1]:
            if volume_profile[k] > np.mean(volume_profile) * 1.5:
                p = round(float(price_range[k]), 2)
                entry = {"price": p, "type": "volume_cluster", "strength": 2}
                if p < current:
                    supports.append(entry)
                else:
                    resistances.append(entry)

    # 3. Round numbers (整数关口)
    step = 1 if current < 20 else (5 if current < 100 else 10)
    for rn in range(int(current * 0.8 / step) * step, int(current * 1.2 / step + 1) * step, step):
        if rn > 0:
            if rn < current:
                supports.append({"price": float(rn), "type": "round_number", "strength": 1})
            elif rn > current:
                resistances.append({"price": float(rn), "type": "round_number", "strength": 1})

    # Deduplicate similar levels (within 1%)
    def dedup(levels):
        levels.sort(key=lambda x: x["price"])
        result = []
        for lv in levels:
            if not result or abs(lv["price"] - result[-1]["price"]) / result[-1]["price"] > 0.01:
                result.append(lv)
            else:
                result[-1]["strength"] = max(result[-1]["strength"], lv["strength"]) + 1
        return result

    supports = dedup(supports)
    resistances = dedup(resistances)

    # Get nearest support/resistance
    nearest_support = max([s for s in supports if s["price"] < current], key=lambda x: x["price"], default=None)
    nearest_resistance = min([r for r in resistances if r["price"] > current], key=lambda x: x["price"], default=None)

    print(json.dumps({
        "status": "ok",
        "symbol": symbol,
        "current_price": current,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "supports": sorted(supports, key=lambda x: -x["price"])[:5],
        "resistances": sorted(resistances, key=lambda x: x["price"])[:5],
    }, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

      try {
        const result = await runInlinePython(script, config);
        return result;
      } catch (err) {
        return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };
}
