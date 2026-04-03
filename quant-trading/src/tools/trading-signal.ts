import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * trading_signal tool — query and manage strategy-generated signals.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { SignalHitRateTracker } from "../services/signal-hitrate.js";
import type { QuantTradingConfig, TradingSignal } from "../types.js";

function getSignalsPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "signals.json");
}

async function loadSignals(config: QuantTradingConfig): Promise<TradingSignal[]> {
  try {
    const raw = await fs.readFile(getSignalsPath(config), "utf-8");
    return JSON.parse(raw) as TradingSignal[];
  } catch {
    return [];
  }
}

export function createTradingSignalTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  hitRateTracker?: SignalHitRateTracker,
) {
  return {
    name: "trading_signal",
    description:
      "查询策略生成的交易信号。" +
      "action=recent 查看最近信号, action=by_strategy 按策略查看, " +
      "action=by_symbol 按标的查看, action=hit_rate 信号命中率, " +
      "action=check_accuracy 立即检查命中率。\n" +
      "【重要】当用户要求'明天可以买的股票'时，应在回测完成后用 action=recent 查询是否有活跃的买入信号，" +
      "结合 backtest 返回的 forwardSignals 综合判断。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "recent",
            "by_strategy",
            "by_symbol",
            "stats",
            "hit_rate",
            "check_accuracy",
            "clear",
          ],
          description:
            "查询类型: recent 最近信号, by_strategy 按策略, by_symbol 按标的, stats 统计概览, hit_rate 信号命中率, check_accuracy 检查命中率, clear 清除信号记录",
        },
        strategyId: { type: "string", description: "策略ID" },
        symbol: { type: "string", description: "标的代码" },
        limit: { type: "number", description: "返回数量，默20" },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const limit = (args.limit as number) ?? 20;
      const signals = await loadSignals(config);

      let filtered: TradingSignal[];
      switch (action) {
        case "recent":
          filtered = signals.slice(-limit);
          break;
        case "by_strategy":
          filtered = signals.filter((s) => s.strategyId === args.strategyId).slice(-limit);
          break;
        case "by_symbol":
          filtered = signals.filter((s) => s.symbol === args.symbol).slice(-limit);
          break;
        case "stats": {
          if (signals.length === 0) return { total: 0, strategies: [], symbols: [] };
          // Group manually (Map.groupBy requires ES2024 target)
          const byStrategy = new Map<string | undefined, typeof signals>();
          const bySymbol = new Map<string, typeof signals>();
          for (const s of signals) {
            const stk = s.strategyId;
            if (!byStrategy.has(stk)) byStrategy.set(stk, []);
            byStrategy.get(stk)!.push(s);
            const sym = s.symbol;
            if (!bySymbol.has(sym)) bySymbol.set(sym, []);
            bySymbol.get(sym)!.push(s);
          }
          const latest = signals[signals.length - 1];
          return {
            total: signals.length,
            latestAt: latest ? new Date(latest.timestamp).toISOString() : null,
            strategies: [...byStrategy.entries()].map(([id, sigs]) => ({
              strategyId: id,
              count: sigs.length,
              buys: sigs.filter((s) => s.direction === "buy").length,
              sells: sigs.filter((s) => s.direction === "sell").length,
            })),
            symbols: [...bySymbol.entries()].map(([sym, sigs]) => ({
              symbol: sym,
              count: sigs.length,
            })),
          };
        }
        case "hit_rate": {
          if (!hitRateTracker) return { error: "信号命中率追踪未启用" };
          const stats = hitRateTracker.getStats();
          return {
            totalTracked: stats.totalSignals,
            checkedSignals: stats.checkedSignals,
            horizons: Object.entries(stats.horizons).map(([k, v]) => ({
              horizon: k,
              checked: v.checked,
              hits: v.hits,
              misses: v.misses,
              hitRate: `${(v.hitRate * 100).toFixed(1)}%`,
            })),
            strategies: Object.entries(stats.strategies).map(([id, s]) => ({
              strategyId: id,
              total: s.total,
              hits: s.hits,
              misses: s.misses,
              hitRate: `${(s.hitRate * 100).toFixed(1)}%`,
            })),
          };
        }
        case "check_accuracy": {
          if (!hitRateTracker) return { error: "信号命中率追踪未启用" };
          const updated = await hitRateTracker.checkAccuracy();
          const stats = hitRateTracker.getStats();
          return {
            message: `已检查并更新 ${updated} 个检查点`,
            totalTracked: stats.totalSignals,
            checkedSignals: stats.checkedSignals,
          };
        }
        case "clear": {
          const signalsPath = getSignalsPath(config);
          await fs.writeFile(signalsPath, "[]");
          return { success: true, message: `已清除 ${signals.length} 条信号记录` };
        }
        default:
          return { error: `未知操作: ${action}` };
      }

      return {
        count: filtered.length,
        signals: filtered.map((s) => ({
          date: new Date(s.timestamp).toISOString().slice(0, 10),
          symbol: s.symbol,
          direction: s.direction,
          price: s.price.toFixed(2),
          strategy: s.strategyId,
          reason: s.reason,
          confidence: s.confidence,
        })),
      };
    },
  };
}
