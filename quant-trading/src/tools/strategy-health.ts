import type { QuantTradingRuntime } from "../runtime.js";
import { StrategyHealthMonitor } from "../services/strategy-health.js";
/**
 * AI-callable tool for strategy health monitoring (策略健康度).
 */
import type { QuantTradingConfig } from "../types.js";

export function createStrategyHealthTool(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
  monitor: StrategyHealthMonitor,
) {
  return {
    name: "strategy_health",
    description:
      "策略健康度监控。追踪每个策略的胜率、连亏、信号频率。\n" +
      "action=check_all 查看所有策略健康状态，\n" +
      "action=check 查看单个策略状态，\n" +
      "action=list 列出追踪中的策略，\n" +
      "action=remove 停止追踪指定策略。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["check_all", "check", "list", "remove"],
          description:
            "操作: check_all=查看所有, check=查看单个, list=列出追踪策略, remove=停止追踪",
        },
        strategyId: {
          type: "string",
          description: "策略ID (action=check/remove 时使用)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "check_all": {
          const statuses = monitor.checkAll();
          if (statuses.length === 0) {
            return JSON.stringify({ message: "暂无策略健康数据。交易完成后自动追踪。" });
          }
          const levelIcon = { healthy: "✅", warning: "⚠️", critical: "🚨" };
          return JSON.stringify({
            strategies: statuses.map((s) => ({
              icon: levelIcon[s.level],
              strategyId: s.strategyId,
              level: s.level,
              winRate: `${(s.winRate * 100).toFixed(1)}%`,
              consecutiveLosses: s.consecutiveLosses,
              daysSinceLastSignal: s.daysSinceLastSignal,
              totalTrades: s.totalTrades,
              recentPnl: s.recentPnl,
              issues: s.issues,
            })),
          });
        }

        case "check": {
          const strategyId = args.strategyId as string;
          if (!strategyId) return JSON.stringify({ error: "需要 strategyId 参数" });
          const status = monitor.checkStrategy(strategyId);
          if (!status) return JSON.stringify({ error: `策略 ${strategyId} 不在追踪列表中` });
          return JSON.stringify({
            strategyId: status.strategyId,
            level: status.level,
            winRate: `${(status.winRate * 100).toFixed(1)}%`,
            consecutiveLosses: status.consecutiveLosses,
            daysSinceLastSignal: status.daysSinceLastSignal,
            totalTrades: status.totalTrades,
            recentPnl: status.recentPnl,
            issues: status.issues.length > 0 ? status.issues : ["无异常"],
          });
        }

        case "list": {
          const strategies = monitor.getTrackedStrategies();
          return JSON.stringify({
            count: strategies.length,
            strategies,
          });
        }

        case "remove": {
          const strategyId = args.strategyId as string;
          if (!strategyId) return JSON.stringify({ error: "需要 strategyId 参数" });
          const removed = monitor.remove(strategyId);
          return JSON.stringify({
            success: removed,
            message: removed ? `已停止追踪策略 ${strategyId}` : `策略 ${strategyId} 不在追踪列表中`,
          });
        }

        default:
          return JSON.stringify({ error: `未知操作: ${action}` });
      }
    },
  };
}
