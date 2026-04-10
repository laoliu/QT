/**
 * block_trade_analysis — 大宗交易深度分析 (AT-9)
 *
 * 在已有 institutional_flow block_trades 基础上增加:
 *  - 120802
 *  - 020901 (知名游资/070807)
 *  - 趋势分析 (成交额走势)
 *  - 0119080004
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createBlockTradeAnalysisTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "block_trade_analysis",
    description: "大宗交易深度分析 — 12080209010100010108",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["overview", "discount_analysis", "top_brokers", "stock_history", "trend"],
          description: "overview: 近期全市场概览; discount_analysis: 120802; top_brokers: 060209; stock_history: 0108; trend: 成交额趋势",
        },
        symbol: { type: "string", description: "股票代码 (stock_history 必填)" },
        days: { type: "number", description: "分析天数 (08 30)" },
        limit: { type: "number", description: "返回数量 (08 20)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = (args.symbol as string) ?? "";
      const days = (args.days as number) ?? 30;
      const limit = (args.limit as number) ?? 20;

      const script = `
import akshare as ak
import json, sys
import numpy as np
from datetime import datetime, timedelta
from collections import Counter

action = "${action}?
symbol = "${symbol}"
days = ${days}
limit = ${limit}

try:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    if action == "overview":
        # Recent market-wide block trades
        df = ak.stock_dzjy_mrtj()
        if df is None or len(df) == 0:
            print(json.dumps({"status": "ok", "trades": [], "message": "151019"}))
            sys.exit(0)

        trades = []
        for _, row in df.head(limit).iterrows():
            trades.append({
                "date": str(row.get("交易日期", ""))[:10],
                "symbol": str(row.get("0804", "")),
                "name": str(row.get("0804", "")),
                "close_price": float(row.get("06", 0) or 0),
                "avg_price": float(row.get("成交均价", 0) or 0),
                "volume_wan": round(float(row.get("00", 0) or 0) / 10000, 2),
                "amount_wan": round(float(row.get("00", 0) or 0) / 10000, 2),
                "premium_pct": round(float(row.get("折溢率", 0) or 0), 2),
            })

        # Summary stats
        premiums = [t["premium_pct"] for t in trades if t["premium_pct"] != 0]
        total_amount = sum(t["amount_wan"] for t in trades)

        print(json.dumps({
            "status": "ok",
            "total_trades": len(trades),
            "total_amount_wan": round(total_amount, 2),
            "avg_premium_pct": round(np.mean(premiums), 2) if premiums else 0,
            "premium_positive_count": sum(1 for p in premiums if p > 0),
            "premium_negative_count": sum(1 for p in premiums if p < 0),
            "trades": trades,
        }, ensure_ascii=False, default=str))

    elif action == "discount_analysis":
        df = ak.stock_dzjy_mrtj()
        if df is None or len(df) == 0:
            print(json.dumps({"status": "ok", "distribution": {}, "message": "无数据"}))
            sys.exit(0)

        premiums = df['折溢率'].astype(float).dropna().values

        # Distribution buckets
        buckets = {"<-10%": 0, "-10~-5%": 0, "-5~-2%": 0, "-2~0%": 0,
                    "0~2%": 0, "2~5%": 0, "5~10%": 0, ">10%": 0}
        for p in premiums:
            if p < -10: buckets["<-10%"] += 1
            elif p < -5: buckets["-10~-5%"] += 1
            elif p < -2: buckets["-5~-2%"] += 1
            elif p < 0: buckets["-2~0%"] += 1
            elif p < 2: buckets["0~2%"] += 1
            elif p < 5: buckets["2~5%"] += 1
            elif p < 10: buckets["5~10%"] += 1
            else: buckets[">10%"] += 1

        print(json.dumps({
            "status": "ok",
            "total_records": len(premiums),
            "avg_premium_pct": round(float(np.mean(premiums)), 2),
            "median_premium_pct": round(float(np.median(premiums)), 2),
            "std_premium_pct": round(float(np.std(premiums)), 2),
            "min_premium_pct": round(float(np.min(premiums)), 2),
            "max_premium_pct": round(float(np.max(premiums)), 2),
            "distribution": buckets,
            "insight": "1208 " + str(round(sum(1 for p in premiums if p < 0) / len(premiums) * 100, 1)) + "%" if len(premiums) > 0 else ??,
        }, ensure_ascii=False, default=str))

    elif action == "top_brokers":
        # 020901
        try:
            df = ak.stock_dzjy_yybph(period="05")
        except:
            df = ak.stock_dzjy_mrtj()

        if df is None or len(df) == 0:
            print(json.dumps({"status": "ok", "brokers": []}))
            sys.exit(0)

        # Try to identify broker columns
        buyer_col = None
        seller_col = None
        for c in df.columns:
            if '买' in c and '0209' in c: buyer_col = c
            if '卖' in c and '0209' in c: seller_col = c
            if '0209' in c and buyer_col is None: buyer_col = c

        brokers = []
        if buyer_col or '0209' in df.columns:
            col = buyer_col or '0209'
            if col in df.columns:
                for _, row in df.head(limit).iterrows():
                    entry = {"name": str(row.get(col, ""))}
                    for c in ['买入次数', '次数', '00', '买入额']:
                        if c in df.columns:
                            entry[c] = str(row.get(c, ""))
                    brokers.append(entry)

        print(json.dumps({
            "status": "ok",
            "top_brokers": brokers[:limit],
        }, ensure_ascii=False, default=str))

    elif action == "stock_history":
        if not symbol:
            print(json.dumps({"error": "59 symbol 参数"}))
            sys.exit(0)

        df = ak.stock_dzjy_mrmx(symbol=symbol)
        if df is None or len(df) == 0:
            print(json.dumps({"status": "ok", "trades": [], "message": f?{symbol} 16190604"}))
            sys.exit(0)

        trades = []
        for _, row in df.head(50).iterrows():
            trades.append({
                "date?: str(row.get("交易日期", ""))[:10],
                "price": float(row.get("00", 0) or 0),
                "volume": float(row.get("00", 0) or 0),
                "amount": float(row.get("00", 0) or 0),
                "buyer": str(row.get("0209", "")),
                "seller": str(row.get("0209", "")),
            })

        # Stats
        amounts = [t["amount"] for t in trades if t["amount"] > 0]
        prices = [t["price"] for t in trades if t["price"] > 0]

        print(json.dumps({
            "status": "ok",
            "symbol": symbol,
            "total_trades": len(trades),
            "total_amount": round(sum(amounts), 2) if amounts else 0,
            "avg_price": round(np.mean(prices), 2) if prices else 0,
            "trades": trades[:limit],
        }, ensure_ascii=False, default=str))

    elif action == "trend":
        # Market-wide block trade volume trend
        df = ak.stock_dzjy_mrtj()
        if df is None or len(df) == 0:
            print(json.dumps({"status": "ok", "trend": []}))
            sys.exit(0)

        # Group by date
        if '交易日期' in df.columns:
            df['交易日期'] = df['交易日期'].astype(str).str[:10]
            grouped = df.groupby('交易日期').agg(
                count=('0804', 'count'),
                total_amount=('00', 'sum'),
            ).reset_index().sort_values('交易日期').tail(days)

            trend = []
            for _, row in grouped.iterrows():
                trend.append({
                    "date": row['交易日期'],
                    "trade_count": int(row['count']),
                    "total_amount_yi": round(float(row['total_amount']) / 1e8, 2),
                })

            print(json.dumps({
                "status": "ok",
                "trend": trend,
                "avg_daily_count": round(np.mean([t["trade_count"] for t in trend]), 1) if trend else 0,
                "avg_daily_amount_yi": round(np.mean([t["total_amount_yi"] for t in trend]), 2) if trend else 0,
            }, ensure_ascii=False, default=str))
        else:
            print(json.dumps({"status": "ok", "trend": [], "message": "列名不匹配"}))

    else:
        print(json.dumps({"error": f?ā04 action: {action}"}))

except Exception as e:
    print(json.dumps({"error?: str(e)}))
`;

      try {
        return await runInlinePython(script, config);
      } catch (err) {
        return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };
}
