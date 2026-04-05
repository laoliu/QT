/**
 * live_backtest_comparison tool — AI-callable tool for comparing live trading
 * performance against backtest expectations.
 *
 * Actions:
 *   compare          — run live vs backtest comparison for a strategy
 *   list_comparable  — list strategies that have both live trades and backtest results
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BacktestResult, QuantTradingConfig, TradeRecord } from "../types.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { BacktestStore } from "../services/backtest-store.js";
import { LiveBacktestComparison } from "../services/live-backtest-comparison.js";
import { runBacktest } from "../engine/strategy-registry.js";

/** Safe toFixed — returns "N/A" when the value is nullish or not a finite number. */
function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

export function createLiveBacktestComparisonTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  equityHistory: EquityHistory,
  backtestStore?: BacktestStore,
) {
  const comparison = new LiveBacktestComparison(equityHistory, backtestStore);

  return {
    name: "live_backtest_comparison",
    description:
      "比较实盘交易表现与回测预期的差异。检测策略漂移、验证回测假设（滑点/成交率）、量化回测-实盘差距。" +
      "action=compare 运行对比分析, action=list_comparable 列出可对比的策略。" +
      "返回指标对比表、权益曲线叠加、差异诊断和健康评估。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "list_comparable"],
          description: "操作类型",
        },
        strategy_id: {
          type: "string",
          description: "策略ID (如 dual-ma-crossover)",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔 (如 600519,000858)",
        },
        start_date: {
          type: "string",
          description: "对比起始日期 YYYY-MM-DD",
        },
        end_date: {
          type: "string",
          description: "对比结束日期 YYYY-MM-DD",
        },
        backtest_id: {
          type: "string",
          description: "指定回测记录ID（可选，不填则自动匹配最近的回测结果）",
        },
        rerun_backtest: {
          type: "boolean",
          description: "是否重新运行回测用于对比（默认false，使用已有回测结果）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "list_comparable": {
          const engine = getEngine();
          const trades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          // Group trades by strategy
          const strategyTrades = new Map<string, TradeRecord[]>();
          for (const t of trades) {
            const sid = t.strategyId ?? "manual";
            if (!strategyTrades.has(sid)) strategyTrades.set(sid, []);
            strategyTrades.get(sid)!.push(t);
          }

          // Find matching backtests
          const comparable: Array<{
            strategyId: string;
            liveTrades: number;
            liveSymbols: string[];
            livePeriod: string;
            backtestCount: number;
            latestBacktestId: string | null;
          }> = [];

          for (const [stratId, sTrades] of strategyTrades) {
            const symbols = [...new Set(sTrades.map((t) => t.symbol))];
            const dates = sTrades
              .map((t) => t.entryTime.slice(0, 10))
              .sort();

            // Count matching backtests
            const btCandidates = backtestStore
              ? backtestStore.query({
                  strategyId: stratId,
                  sortBy: "createdAt",
                  sortDir: "desc",
                  limit: 5,
                })
              : [];

            comparable.push({
              strategyId: stratId,
              liveTrades: sTrades.length,
              liveSymbols: symbols.slice(0, 10),
              livePeriod:
                dates.length > 0
                  ? `${dates[0]} ~ ${dates[dates.length - 1]}`
                  : "无数据",
              backtestCount: btCandidates.length,
              latestBacktestId: btCandidates[0]?.id ?? null,
            });
          }

          return {
            total: comparable.length,
            strategies: comparable,
            hint:
              comparable.length === 0
                ? "暂无可对比数据。需要同时有实盘交易记录和回测结果才能进行对比。"
                : "使用 action=compare 并指定 strategy_id 进行详细对比分析。",
          };
        }

        case "compare": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

          const symbols = ((args.symbols as string) ?? "")
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
          const endDate =
            (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate =
            (args.start_date as string) ?? getDefaultStartDate(endDate);
          const backtestId = args.backtest_id as string | undefined;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          // Get live trades
          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          // Filter to strategy and period
          const stratTrades = allTrades.filter(
            (t) =>
              t.strategyId === strategyId &&
              t.entryTime.slice(0, 10) >= startDate &&
              t.entryTime.slice(0, 10) <= endDate,
          );

          // Determine symbols from trades if not provided
          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          if (compareSymbols.length === 0) {
            return {
              error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录，请提供 symbols 参数或检查日期范围`,
            };
          }

          // Optionally re-run backtest for a fresh comparison
          let freshBacktestResult: BacktestResult | undefined;
          if (rerunBacktest) {
            try {
              freshBacktestResult = await runBacktest(
                {
                  strategyId,
                  symbols: compareSymbols,
                  startDate,
                  endDate,
                  initialCash: 100_000,
                },
                config,
              );
            } catch (err) {
              api.logger.warn(
                `Re-run backtest failed: ${(err as Error).message}`,
              );
            }
          }

          const result = comparison.compare(
            {
              strategyId,
              symbols: compareSymbols,
              startDate,
              endDate,
              backtestId,
            },
            stratTrades,
            freshBacktestResult,
          );

          // Format for AI output
          return {
            strategyId: result.strategyId,
            symbols: result.symbols,
            period: result.period,
            backtestId: result.backtestId ?? "无匹配回测（建议使用 rerun_backtest=true）",

            verdict: result.verdict,
            verdictDescription: result.verdictDescription,

            metricsComparison: result.metrics.map((m) => ({
              指标: m.label,
              回测: `${fmt(m.backtest, 2)}${m.unit}`,
              实盘: `${fmt(m.live, 2)}${m.unit}`,
              差异: `${m.delta > 0 ? "+" : ""}${fmt(m.delta, 2)}${m.unit}`,
              偏离度: `${m.deltaPct > 0 ? "+" : ""}${fmt(m.deltaPct, 1)}%`,
              状态: m.degraded ? "⚠️ 退化" : "✅ 正常",
            })),

            gaps: result.gaps.map((g) => ({
              类别: g.category,
              描述: g.description,
              严重度: g.severity === "critical" ? "🔴 严重" :
                       g.severity === "warning" ? "🟡 警告" : "🟢 正常",
            })),

            liveStats: {
              交易次数: result.liveStats.totalTrades,
              胜率: `${fmt(result.liveStats.winRate, 1)}%`,
              总收益: `${fmt(result.liveStats.totalReturn, 2)}%`,
              最大回撤: `${fmt(result.liveStats.maxDrawdown, 2)}%`,
              平均滑点: `${fmt(result.liveStats.avgSlippage, 3)}%`,
            },

            backtestStats: {
              交易次数: result.backtestStats.totalTrades,
              胜率: `${fmt(result.backtestStats.winRate, 1)}%`,
              总收益: `${fmt(result.backtestStats.totalReturn, 2)}%`,
              最大回撤: `${fmt(result.backtestStats.maxDrawdown, 2)}%`,
              建模滑点: `${fmt(result.backtestStats.modeledSlippage, 3)}%`,
            },

            equityCurvePoints: result.equityCurve.length,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

function getDefaultStartDate(endDate: string): string {
  const d = new Date(endDate);
  d.setMonth(d.getMonth() - 3);
  return d.toISOString().slice(0, 10);
}
