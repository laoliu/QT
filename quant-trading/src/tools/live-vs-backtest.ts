/**
 * live_vs_backtest tool — AI-callable tool for comparing live trading
 * performance against backtest results and detecting strategy decay.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveBacktestCompareService } from "../services/live-backtest-compare.js";
import type { QuantTradingConfig } from "../types.js";

/** Safe toFixed — returns "N/A" when the value is nullish or not a finite number. */
function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

export function createLiveVsBacktestTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  compareService: LiveBacktestCompareService,
) {
  return {
    name: "live_vs_backtest",
    description:
      "实盘 vs 回测对比工具。对比指定策略在实盘中的表现与回测预期之间的偏差，" +
      "检测策略衰减（当实盘表现比回测差超过阈值时告警）。\n" +
      "action=compare: 对比实盘与回测指标\n" +
      "action=decay_alerts: 查看最近的策略衰减告警",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "decay_alerts"],
          description: "操作: compare=对比实盘与回测, decay_alerts=查看衰减告警",
        },
        strategyId: {
          type: "string",
          description: "策略ID（compare 必填，decay_alerts 可选过滤）",
        },
        dateFrom: {
          type: "string",
          description: "对比起始日期 YYYY-MM-DD（compare 必填）",
        },
        dateTo: {
          type: "string",
          description: "对比结束日期 YYYY-MM-DD（compare 必填）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "compare") {
        const strategyId = args.strategyId as string;
        if (!strategyId) return { error: "请提供策略ID (strategyId)" };
        const dateFrom = args.dateFrom as string;
        const dateTo = args.dateTo as string;
        if (!dateFrom || !dateTo) return { error: "请提供日期范围 (dateFrom, dateTo)" };

        try {
          const result = compareService.compare({ strategyId, dateFrom, dateTo });
          return {
            strategyId: result.strategyId,
            period: `${result.dateFrom} ~ ${result.dateTo}`,
            backtestSummary: {
              totalReturn: `${fmt(result.backtestSummary.totalReturn, 2)}%`,
              annualizedReturn: `${fmt(result.backtestSummary.annualizedReturn, 2)}%`,
              maxDrawdown: `${fmt(result.backtestSummary.maxDrawdown, 2)}%`,
              sharpeRatio: fmt(result.backtestSummary.sharpeRatio, 3),
              winRate: `${fmt(result.backtestSummary.winRate, 1)}%`,
              totalTrades: result.backtestSummary.totalTrades,
            },
            liveSummary: {
              totalReturn: `${fmt(result.liveSummary.totalReturn, 2)}%`,
              equityStart: `¥${result.liveSummary.equityStart.toLocaleString()}`,
              equityEnd: `¥${result.liveSummary.equityEnd.toLocaleString()}`,
              maxDrawdown: `${fmt(result.liveSummary.maxDrawdown, 2)}%`,
              tradingDays: result.liveSummary.tradingDays,
            },
            metrics: result.metrics.map((m) => ({
              metric: m.label,
              backtest: fmt(m.backtest, 2),
              live: fmt(m.live, 2),
              divergence: `${fmt(m.divergencePct, 1)}%`,
              decayed: m.decayed,
            })),
            overallDecayScore: `${fmt(result.overallDecayScore, 1)}%`,
            hasDecay: result.hasDecay,
            decayAlerts: result.decayAlerts,
          };
        } catch (err) {
          return { error: `对比失败: ${(err as Error).message}` };
        }
      }

      if (action === "decay_alerts") {
        const strategyId = (args.strategyId as string) ?? undefined;
        const alerts = compareService.getDecayAlerts(strategyId);
        return {
          count: alerts.length,
          alerts: alerts.map((a) => ({
            strategyId: a.strategyId,
            metric: a.label,
            divergence: `${fmt(a.divergencePct, 1)}%`,
            threshold: `${a.threshold}%`,
            message: a.message,
            time: a.timestamp,
          })),
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
