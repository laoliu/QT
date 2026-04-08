/**
 * market_sentiment_index — 050907 (AT-8)
 *
 * 1004041708 (0-100):
 *  - 07 (03/跌家数)
 *  - 0505
 *  - 换手率
 *  - 融资余额变化
 *  - 1406
 *  - VIX / 波动率代理
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketSentimentIndexTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_sentiment_index",
    description: "050907 — 1007/换手率/融资余额/连板等构建0-100情绪指数",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["current", "history", "components"],
          description: "current: 0208; history: 历史趋势; components: 分项明细",
        },
        days: { type: "number", description: "历史天数 (history 用, 08 30)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const days = (args.days as number) ?? 30;

      if (action === "current" || action === "components") {
        return await getCurrentSentiment(api, config, action === "components");
      } else if (action === "history") {
        return await getSentimentHistory(api, config, days);
      }
      return { error: "ā04 action01: current, history, components" };
    },
  };
}

async function getCurrentSentiment(api: OpenClawPluginApi, config: QuantTradingConfig, detailed: boolean) {
  const script = `
import akshare as ak
import json, sys
import numpy as np

detailed = ${detailed ? "True" : "False"}

try:
    components = {}
    scores = {}

    # 1. 07 (Advance/Decline ratio) — weight 25%
    try:
        spot = ak.stock_zh_a_spot_em()
        if spot is not None and len(spot) > 0:
            changes = spot['07'].astype(float)
            advancers = int((changes > 0).sum())
            decliners = int((changes < 0).sum())
            flat = int((changes == 0).sum())
            total = len(changes)
            ad_ratio = advancers / max(decliners, 1)
            # Score: ratio=1 → 50, ratio>=3 → 90, ratio<=0.33 → 10
            ad_score = min(95, max(5, 50 + (ad_ratio - 1) * 20))
            scores['advance_decline'] = round(ad_score, 1)
            components['advance_decline'] = {
                'advancers': advancers, 'decliners': decliners, 'flat': flat,
                'ratio': round(ad_ratio, 2), 'score': round(ad_score, 1),
            }

            # 2. 0505 — weight 20%
            limit_up = int((changes >= 9.9).sum())
            limit_down = int((changes <= -9.9).sum())
            net_limit = limit_up - limit_down
            # Score: net_limit=0 → 50, +50 → 90, -30 → 10
            limit_score = min(95, max(5, 50 + net_limit * 0.8))
            scores['limit_ratio'] = round(limit_score, 1)
            components['limit_ratio'] = {
                'limit_up': limit_up, 'limit_down': limit_down,
                'net': net_limit, 'score': round(limit_score, 1),
            }

            # 3. 平均换手率 — weight 15%
            turnover = spot['换手率'].astype(float)
            avg_turnover = float(turnover.mean())
            # Score: 2% → 50, >5% → 85 (hot), <1% → 25 (cold)
            turn_score = min(95, max(5, 25 + avg_turnover * 12))
            scores['turnover'] = round(turn_score, 1)
            components['turnover'] = {
                'avg_pct': round(avg_turnover, 2), 'score': round(turn_score, 1),
            }

            # Total market volume
            total_amount = float(spot['00'].sum()) / 1e8  # 亿
            components['total_amount_yi'] = round(total_amount, 2)
    except Exception as e:
        components['market_error'] = str(e)

    # 4. 融资余额变化 — weight 15%
    try:
        margin = ak.stock_margin_sse(start_date="20250101")
        if margin is not None and len(margin) >= 2:
            last = float(margin.iloc[-1].get('融资余额', 0) or 0) / 1e8
            prev = float(margin.iloc[-2].get('融资余额', 0) or 0) / 1e8
            change = last - prev
            change_pct = change / prev * 100 if prev > 0 else 0
            # Score: 0 change → 50, +1% → 70, -1% → 30
            margin_score = min(95, max(5, 50 + change_pct * 20))
            scores['margin'] = round(margin_score, 1)
            components['margin'] = {
                'balance_yi': round(last, 2), 'change_yi': round(change, 2),
                'change_pct': round(change_pct, 3), 'score': round(margin_score, 1),
            }
    except Exception as e:
        components['margin_error'] = str(e)

    # 5. 1406 — weight 15%
    try:
        lianban = ak.stock_zt_pool_em(date=None)
        if lianban is not None and len(lianban) > 0:
            if '连板数' in lianban.columns:
                lb_counts = lianban['连板数'].astype(int)
                max_lb = int(lb_counts.max())
                total_zt = len(lianban)
                lb_2plus = int((lb_counts >= 2).sum())
                # Score: more 连板 = higher; max_lb>=5 → hot
                lb_score = min(95, max(5, 30 + total_zt * 0.3 + lb_2plus * 2 + max_lb * 5))
                scores['lianban'] = round(lb_score, 1)
                components['lianban'] = {
                    'total_zt': total_zt, 'lb_2plus': lb_2plus,
                    'max_lb': max_lb, 'score': round(lb_score, 1),
                }
            else:
                total_zt = len(lianban)
                lb_score = min(95, max(5, 30 + total_zt * 0.4))
                scores['lianban'] = round(lb_score, 1)
                components['lianban'] = {'total_zt': total_zt, 'score': round(lb_score, 1)}
    except Exception as e:
        components['lianban_error'] = str(e)

    # 6. 北向资金 — weight 10%
    try:
        north = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
        if north is not None and len(north) >= 1:
            last_flow = float(north.iloc[-1].get('当日净流入', 0) or 0) / 1e8
            # Score: +50亿 → 80, 0 → 50, -50亿 → 20
            north_score = min(95, max(5, 50 + last_flow * 0.6))
            scores['northbound'] = round(north_score, 1)
            components['northbound'] = {
                'net_flow_yi': round(last_flow, 2), 'score': round(north_score, 1),
            }
    except Exception as e:
        components['northbound_error'] = str(e)

    # Composite score (weighted average)
    weights = {
        'advance_decline': 0.25,
        'limit_ratio': 0.20,
        'turnover': 0.15,
        'margin': 0.15,
        'lianban': 0.15,
        'northbound': 0.10,
    }
    total_weight = sum(weights[k] for k in scores if k in weights)
    if total_weight > 0:
        composite = sum(scores[k] * weights.get(k, 0) for k in scores) / total_weight
    else:
        composite = 50

    # Interpret
    if composite >= 80:
        mood = "极度贪婪"
        signal = "警惕过热，注意风险"
    elif composite >= 65:
        mood = "04"
        signal = "0102070701"
    elif composite >= 50:
        mood = "中性偏多"
        signal = "正常交易"
    elif composite >= 35:
        mood = "04"
        signal = "070002"
    else:
        mood = "0004"
        signal = "01050038"

    result = {
        "status": "ok",
        "composite_score": round(composite, 1),
        "mood": mood,
        "signal": signal,
        "component_scores": scores,
    }
    if detailed:
        result["components"] = components

    print(json.dumps(result, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

  try {
    return await runPythonScript(api, config, "market_sentiment_current", script);
  } catch (err) {
    return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function getSentimentHistory(api: OpenClawPluginApi, config: QuantTradingConfig, days: number) {
  const script = `
import akshare as ak
import json, sys
import numpy as np
from datetime import datetime, timedelta

days = ${days}

try:
    end = datetime.now()
    start = end - timedelta(days=days + 10)

    # Get index data for proxy
    df = ak.stock_zh_index_daily_em(symbol="sh000001")
    if df is None or len(df) < 10:
        print(json.dumps({"error": "190008"}))
        sys.exit(0)

    df = df.tail(days)
    history = []
    for _, row in df.iterrows():
        date = str(row.get('date', row.get('日期', '')))[:10]
        close = float(row.get('close', row.get('收盘', 0)) or 0)
        volume = float(row.get('volume', row.get('00', 0)) or 0)
        change_pct = float(row.get('pct_chg', row.get('07', 0)) or 0)
        
        # Simple sentiment proxy from index
        score = 50 + change_pct * 10
        score = max(5, min(95, score))
        
        history.append({
            "date": date,
            "index_close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "sentiment_proxy": round(score, 1),
        })

    print(json.dumps({
        "status": "ok",
        "days": len(history),
        "history": history,
        "avg_sentiment": round(np.mean([h["sentiment_proxy"] for h in history]), 1) if history else 50,
    }, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

  try {
    return await runPythonScript(api, config, "market_sentiment_history", script);
  } catch (err) {
    return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
  }
}
