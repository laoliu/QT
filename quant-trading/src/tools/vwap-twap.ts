/**
 * vwap_twap — VWAP/TWAP 算法交易辅助工具 (AT-4)
 *
 * 计算 VWAP 0615TWAP 061701025921
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createVwapTwapTool(api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    description: "VWAP/TWAP 1102 — 061206010221",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["analyze", "split_plan"],
          description: "analyze: VWAP/TWAP 分析; split_plan: 2007",
        },
        symbol: { type: "string", description: "股票代码" },
        days: { type: "number", description: "分析天数 (0820)" },
        total_quantity: { type: "number", description: "拆单总委托量 (split_plan 用)" },
        algo: { type: "string", enum: ["vwap", "twap"], description: "算法类型 (split_plan 用, 08 vwap)" },
        slices: { type: "number", description: "拆分份数 (split_plan 用, 0810)" },
      },
      required: ["action", "symbol"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      if (!symbol) return { error: "59 symbol" };

      if (action === "analyze") {
        return await analyzeVwapTwap(api, config, symbol, (args.days as number) ?? 20);
      } else if (action === "split_plan") {
        const qty = args.total_quantity as number;
        if (!qty || qty <= 0) return { error: "59 total_quantity" };
        return await splitPlan(
          api, config, symbol,
          qty,
          (args.algo as string) ?? "vwap",
          (args.slices as number) ?? 10,
        );
      }
      return { error: "ā04 action01: analyze, split_plan" };
    },
  };
}

async function analyzeVwapTwap(
  api: QuantTradingRuntime, config: QuantTradingConfig,
  symbol: string, days: number,
) {
  const script = `
import akshare as ak
import json, sys
import numpy as np

symbol = "${symbol}?
days = ${days}

try:
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
    if df is None or len(df) < 5:
        print(json.dumps({"error": "数据不足"}))
        sys.exit(0)

    df = df.tail(days)
    closes = df['收盘'].values.astype(float)
    volumes = df['00'].values.astype(float)
    highs = df['最高'].values.astype(float)
    lows = df['最低'].values.astype(float)
    amounts = df['00'].values.astype(float)
    dates = df['日期'].values.astype(str)
    current = float(closes[-1])

    # VWAP = sum(amount) / sum(volume)
    vwap = float(np.sum(amounts) / np.sum(volumes)) if np.sum(volumes) > 0 else current
    twap = float(np.mean((highs + lows + closes) / 3.0))

    vwap_dev = round((current - vwap) / vwap * 100, 2)
    twap_dev = round((current - twap) / twap * 100, 2)

    # Intraday volume distribution estimate (by day-of-week pattern)
    volume_by_day = {}
    for i in range(len(dates)):
        import datetime
        d = datetime.datetime.strptime(dates[i][:10], "%Y-%m-%d")
        wd = d.weekday()
        volume_by_day.setdefault(wd, []).append(float(volumes[i]))

    avg_volume_pattern = {}
    for wd, vols in sorted(volume_by_day.items()):
        avg_volume_pattern[["Mon","Tue","Wed","Thu","Fri"][wd]] = round(np.mean(vols))

    # Daily VWAP series (recent 5 days)
    daily_vwap = []
    for i in range(max(0, len(df)-5), len(df)):
        dv = float(amounts[i] / volumes[i]) if volumes[i] > 0 else float(closes[i])
        daily_vwap.append({
            "date": dates[i][:10],
            "vwap": round(dv, 2),
            "close": round(float(closes[i]), 2),
            "deviation_pct": round((float(closes[i]) - dv) / dv * 100, 2),
        })

    signal = "neutral"
    if vwap_dev > 2:
        signal = "overvalued_vs_vwap"
    elif vwap_dev < -2:
        signal = "undervalued_vs_vwap"

    print(json.dumps({
        "status": "ok",
        "symbol": symbol,
        "period_days": days,
        "current_price": current,
        "vwap": round(vwap, 2),
        "twap": round(twap, 2),
        "vwap_deviation_pct": vwap_dev,
        "twap_deviation_pct": twap_dev,
        "signal": signal,
        "daily_vwap": daily_vwap,
        "volume_pattern_by_weekday": avg_volume_pattern,
        "avg_daily_volume": int(np.mean(volumes)),
    }, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

  try {
    return await runInlinePython(script, config);
  } catch (err) {
    return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function splitPlan(
  api: QuantTradingRuntime, config: QuantTradingConfig,
  symbol: string, totalQty: number, algo: string, slices: number,
) {
  const script = `
import akshare as ak
import json, sys
import numpy as np

symbol = "${symbol}"
total_qty = ${totalQty}
algo = "${algo}"
slices = ${slices}

try:
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
    if df is None or len(df) < 10:
        print(json.dumps({"error": "数据不足"}))
        sys.exit(0)

    df = df.tail(20)
    volumes = df['00'].values.astype(float)
    amounts = df['00'].values.astype(float)
    closes = df['收盘'].values.astype(float)
    current = float(closes[-1])

    avg_daily_vol = float(np.mean(volumes))
    participation_rate = total_qty / avg_daily_vol if avg_daily_vol > 0 else 1.0

    plan = []
    if algo == "vwap":
        # VWAP: allocate proportionally to typical intraday volume pattern
        # Simplified pattern: morning heavy, midday light, afternoon moderate
        intraday_pattern = [0.15, 0.13, 0.10, 0.08, 0.06, 0.05, 0.07, 0.09, 0.12, 0.15]
        if slices != 10:
            intraday_pattern = [1.0 / slices] * slices
        for i in range(slices):
            qty = max(100, int(round(total_qty * intraday_pattern[i] / 100) * 100))
            plan.append({
                "slice": i + 1,
                "quantity": qty,
                "pct_of_total": round(intraday_pattern[i] * 100, 1),
                "note": "VWAP-weighted"
            })
    else:
        # TWAP: equal distribution
        base_qty = max(100, int(round(total_qty / slices / 100) * 100))
        remainder = total_qty - base_qty * slices
        for i in range(slices):
            qty = base_qty + (100 if i < remainder / 100 else 0)
            plan.append({
                "slice": i + 1,
                "quantity": max(100, qty),
                "pct_of_total": round(100.0 / slices, 1),
                "note": "TWAP-equal"
            })

    # Normalize to match total
    total_plan = sum(s["quantity"] for s in plan)
    if total_plan != total_qty and len(plan) > 0:
        diff = total_qty - total_plan
        plan[-1]["quantity"] = max(100, plan[-1]["quantity"] + diff)

    risk_warning = None
    if participation_rate > 0.2:
        risk_warning = f?参与率 {round(participation_rate*100, 1)}% 0113012-304?
    elif participation_rate > 0.1:
        risk_warning = f?参与率 {round(participation_rate*100, 1)}%，注意市场冲击?

    print(json.dumps({
        "status": "ok",
        "symbol": symbol,
        "algorithm": algo.upper(),
        "total_quantity": total_qty,
        "slices": slices,
        "estimated_amount": round(total_qty * current, 2),
        "avg_daily_volume": int(avg_daily_vol),
        "participation_rate_pct": round(participation_rate * 100, 2),
        "risk_warning": risk_warning,
        "plan": plan,
    }, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

  try {
    return await runInlinePython(script, config);
  } catch (err) {
    return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
  }
}
