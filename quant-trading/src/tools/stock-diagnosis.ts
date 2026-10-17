/**
 * stock_diagnosis — 个股综合诊断工具 (AT-3)
 *
 * 技术面 + 基本面 + 04050700
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockDiagnosisTool(api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    description: "个股综合诊断 — 技术面+基本面+0405071005, 输出健康评分",
    parameters: {
      type: "object" as const,
      properties: {
        symbol: { type: "string", description: "股票代码 (必填)" },
      },
      required: ["symbol"],
    },

    async execute(args: Record<string, unknown>) {
      const symbol = args.symbol as string;
      if (!symbol) return { error: "59 symbol" };

      const script = `
import akshare as ak
import json, sys
import numpy as np

symbol = "${symbol}?
diagnosis = {"symbol": symbol}

try:
    # 1. Basic info + valuation
    spot_df = ak.stock_zh_a_spot_em()
    if spot_df is not None:
        match = spot_df[spot_df['代码'] == symbol]
        if len(match) > 0:
            row = match.iloc[0]
            diagnosis["basic"] = {
                "name": str(row.get("名称", "")),
                "price": float(row.get("04", 0) or 0),
                "change_pct": float(row.get("07", 0) or 0),
                "pe": float(row.get("07-00", 0) or 0),
                "pb": float(row.get("о", 0) or 0),
                "market_cap": float(row.get("05", 0) or 0),
                "turnover": float(row.get("换手率", 0) or 0),
                "volume_ratio": float(row.get("量比", 0) or 0),
            }

    # 2. Technical analysis (recent K-lines)
    try:
        kl_df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if kl_df is not None and len(kl_df) >= 20:
            closes = kl_df['收盘'].values[-60:].astype(float)
            volumes = kl_df['00'].values[-60:].astype(float)

            ma5 = float(np.mean(closes[-5:]))
            ma20 = float(np.mean(closes[-20:]))
            ma60 = float(np.mean(closes[-min(60, len(closes)):]))
            current = float(closes[-1])

            # RSI (14-day)
            deltas = np.diff(closes[-15:])
            gains = np.mean(deltas[deltas > 0]) if np.any(deltas > 0) else 0
            losses = -np.mean(deltas[deltas < 0]) if np.any(deltas < 0) else 0.001
            rsi = float(100 - 100 / (1 + gains / losses))

            # Volume trend
            avg_vol_5 = float(np.mean(volumes[-5:]))
            avg_vol_20 = float(np.mean(volumes[-20:]))

            diagnosis["technical"] = {
                "ma5": round(ma5, 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "rsi14": round(rsi, 1),
                "trend": "上升" if ma5 > ma20 > ma60 else ("05" if ma5 < ma20 < ma60 else ?震荡"),
                "price_vs_ma20?: f?{((current/ma20 - 1) * 100):.1f}%",
                "volume_trend" : "放量" if avg_vol_5 > avg_vol_20 * 1.3 else ("缩量" if avg_vol_5 < avg_vol_20 * 0.7 else ?正常"),
            }
    except Exception:
        diagnosis["technical"] = {"error" : "技术指标计算失败"}

    # 3. Fund flow (主力资金)
    try:
        flow_df = ak.stock_individual_fund_flow(stock=symbol, market="sh" if symbol.startswith("6") else ?sz")
        if flow_df is not None and len(flow_df) > 0:
            latest = flow_df.iloc[0]
            diagnosis["fund_flow"] = {
                "date?: str(latest.iloc[0]),
                "main_net_inflow": str(latest.get("主力净流入-净额", "N/A")),
            }
    except Exception:
        diagnosis["fund_flow"] = {"note": "资金流向数据暂不可用"}

    # 4. Health score (0-100)
    score = 50
    tech = diagnosis.get("technical", {})
    basic = diagnosis.get("basic", {})
    if isinstance(tech, dict) and ?rsi14? in tech:
        rsi_val = tech["rsi14"]
        if 40 <= rsi_val <= 60: score += 10  # neutral is good
        elif rsi_val > 70: score -= 10  # overbought
        elif rsi_val < 30: score -= 5   # oversold (may bounce)
    if isinstance(tech, dict) and tech.get("trend") == "上升": score += 15
    elif isinstance(tech, dict) and tech.get("trend") == "05": score -= 15
    if isinstance(basic, dict):
        pe = basic.get("pe", 0)
        if 0 < pe < 30: score += 10
        elif pe > 100: score -= 10
        if basic.get("volume_ratio", 0) > 2: score += 5

    diagnosis["health_score"] = max(0, min(100, score))
    diagnosis["status"] = "ok"

    print(json.dumps(diagnosis, ensure_ascii=False, default=str))

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
