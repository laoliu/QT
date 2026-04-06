/**
 * live_backtest_deviation tool — AI-callable live vs backtest deviation analysis.
 *
 * Compares live/paper trading performance against backtest expectations,
 * computing return deviation, slippage analysis, win rate comparison,
 * equity curve correlation, and per-strategy health scoring.
 *
 * Actions:
 *   full_report   — comprehensive deviation analysis
 *   slippage      — slippage-only analysis
 *   equity_curve  — equity curve correlation
 *   alerts        — deviation alerts only
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { computeDeviation, computeSlippageAnalysis } from "../services/live-backtest-deviation.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { QuantTradingConfig, TradeRecord } from "../types.js";

function getTradesPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "trades.json");
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(getTradesPath(config), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

export function createLiveBacktestDeviationTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  backtestStore: BacktestStore,
  equityHistory: EquityHistory,
) {
  return {
    name: "live_backtest_deviation",
    description:
      "实盘vs回测偏差分析。对比实盘/模拟盘交易表现与回测预期的差异，" +
      "包含收益率偏差、滑点分析、胜率对比、资金曲线相关性和策略健康评分。" +
      "action=full_report 完整报告, action=slippage 滑点分析, " +
      "action=equity_curve 资金曲线相关性, action=alerts 偏差告警。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["full_report", "slippage", "equity_curve", "alerts"],
          description: "分析类型",
        },
        backtest_id: {
          type: "string",
          description: "回测记录ID (从 backtest store 获取)",
        },
        strategy_id: {
          type: "string",
          description: "策略ID (可选，用于按策略筛选)",
        },
        from: { type: "string", description: "起始日期 YYYY-MM-DD" },
        to: { type: "string", description: "截止日期 YYYY-MM-DD" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const backtestId = args.backtest_id as string | undefined;
      const strategyId = args.strategy_id as string | undefined;

      // Load live trades
      let trades = await loadTrades(config);
      if (trades.length === 0) {
        return { error: "暂无实盘交易记录，请先进行实盘/模拟盘交易" };
      }

      // Filter by strategy if specified
      if (strategyId) {
        trades = trades.filter((t) => t.strategyId === strategyId);
        if (trades.length === 0) {
          return { error: `策略 ${strategyId} 暂无交易记录` };
        }
      }

      // Filter by date range
      if (args.from) {
        trades = trades.filter((t) => (t.exitTime ?? t.entryTime) >= (args.from as string));
      }
      if (args.to) {
        trades = trades.filter((t) => (t.exitTime ?? t.entryTime) <= (args.to as string));
      }

      // For slippage-only, no backtest needed
      if (action === "slippage") {
        const slippage = computeSlippageAnalysis(trades, 100_000);
        return {
          action: "slippage",
          totalTrades: slippage.totalTrades,
          avgSlippageBps: `${slippage.avgSlippageBps.toFixed(1)} bps`,
          maxSlippageBps: `${slippage.maxSlippageBps.toFixed(1)} bps`,
          slippageStdBps: `${slippage.slippageStdBps.toFixed(1)} bps`,
          totalSlippageCost: `¥${slippage.totalSlippageCost.toFixed(2)}`,
          slippagePctOfCapital: `${slippage.slippagePctOfCapital.toFixed(2)}%`,
          buyAvgSlippageBps: `${slippage.buyAvgSlippageBps.toFixed(1)} bps`,
          sellAvgSlippageBps: `${slippage.sellAvgSlippageBps.toFixed(1)} bps`,
          summary:
            slippage.avgSlippageBps > 10
              ? "滑点偏高，建议检查订单执行质量或调整下单策略"
              : "滑点在合理范围内",
        };
      }

      // For full_report, equity_curve, alerts: need a backtest to compare against
      let backtestResult;
      if (backtestId) {
        const record = backtestStore.getFullResult(backtestId);
        if (!record) {
          return { error: `未找到回测记录: ${backtestId}` };
        }
        backtestResult = record.result;
      } else {
        // Auto-find the most recent backtest for the given strategy
        const sid = strategyId ?? trades[0]?.strategyId;
        if (!sid) {
          return { error: "请指定 backtest_id 或 strategy_id" };
        }
        const entries = backtestStore.query({ strategyId: sid, sortBy: "createdAt", sortDir: "desc", limit: 1 });
        if (entries.length === 0) {
          return { error: `未找到策略 ${sid} 的回测记录，请先运行回测` };
        }
        const record = backtestStore.getFullResult(entries[0]!.id);
        if (!record) {
          return { error: `回测记录数据损坏: ${entries[0]!.id}` };
        }
        backtestResult = record.result;
      }

      // Get equity snapshots
      const liveEquity = equityHistory.getAll();

      // Compute deviation
      const report = computeDeviation({
        liveTrades: trades,
        backtestResult,
        liveEquity: liveEquity.length > 0 ? liveEquity : undefined,
        initialCash: backtestResult.initialCash,
      });

      switch (action) {
        case "full_report":
          return {
            action: "full_report",
            dateRange: report.dateRange,
            overallHealthScore: report.overallHealthScore,
            overallHealthLevel:
              report.overallHealthScore >= 80
                ? "健康"
                : report.overallHealthScore >= 50
                  ? "需关注"
                  : "异常",
            metrics: report.overallMetrics.map((m) => ({
              metric: m.metric,
              backtest: formatNum(m.backtest),
              live: formatNum(m.live),
              deviation: formatNum(m.deviation),
              deviationPct: `${m.deviationPct.toFixed(1)}%`,
              severity: m.severity,
              severityCn: m.severity === "critical" ? "严重" : m.severity === "warning" ? "警告" : "正常",
            })),
            slippage: {
              avgSlippageBps: `${report.slippage.avgSlippageBps.toFixed(1)} bps`,
              maxSlippageBps: `${report.slippage.maxSlippageBps.toFixed(1)} bps`,
              totalSlippageCost: `¥${report.slippage.totalSlippageCost.toFixed(2)}`,
              slippagePctOfCapital: `${report.slippage.slippagePctOfCapital.toFixed(2)}%`,
            },
            equityCurveCorrelation: report.equityCurveCorrelation
              ? {
                  correlation: report.equityCurveCorrelation.correlation.toFixed(3),
                  meanAbsDeviation: `${report.equityCurveCorrelation.meanAbsDeviation.toFixed(2)}%`,
                  trackingError: `${report.equityCurveCorrelation.trackingError.toFixed(2)}%`,
                  maxDivergence: report.equityCurveCorrelation.maxDivergence
                    ? {
                        date: report.equityCurveCorrelation.maxDivergence.date,
                        gap: `${report.equityCurveCorrelation.maxDivergence.gap.toFixed(2)}%`,
                      }
                    : null,
                }
              : null,
            strategies: report.byStrategy.map((s) => ({
              strategyId: s.strategyId,
              liveTrades: s.liveTrades,
              backtestTrades: s.backtestTrades,
              healthScore: s.healthScore,
              summary: s.summary,
            })),
            alerts: report.alerts.slice(0, 10).map((a) => ({
              severity: a.severity,
              severityCn: a.severity === "critical" ? "严重" : "警告",
              message: a.message,
            })),
          };

        case "equity_curve":
          if (!report.equityCurveCorrelation) {
            return { error: "资金曲线数据不足，需要至少5个交易日的实盘和回测数据" };
          }
          return {
            action: "equity_curve",
            correlation: report.equityCurveCorrelation.correlation.toFixed(3),
            correlationLevel:
              report.equityCurveCorrelation.correlation > 0.8
                ? "高度相关"
                : report.equityCurveCorrelation.correlation > 0.5
                  ? "中等相关"
                  : "弱相关",
            meanAbsDeviation: `${report.equityCurveCorrelation.meanAbsDeviation.toFixed(2)}%`,
            trackingError: `${report.equityCurveCorrelation.trackingError.toFixed(2)}%`,
            maxDivergence: report.equityCurveCorrelation.maxDivergence
              ? {
                  date: report.equityCurveCorrelation.maxDivergence.date,
                  liveReturn: `${report.equityCurveCorrelation.maxDivergence.liveReturn.toFixed(2)}%`,
                  backtestReturn: `${report.equityCurveCorrelation.maxDivergence.backtestReturn.toFixed(2)}%`,
                  gap: `${report.equityCurveCorrelation.maxDivergence.gap.toFixed(2)}%`,
                }
              : null,
            summary:
              report.equityCurveCorrelation.correlation > 0.8
                ? "实盘资金曲线与回测高度吻合，策略执行效果良好"
                : report.equityCurveCorrelation.correlation > 0.5
                  ? "实盘资金曲线与回测存在一定偏差，建议检查执行细节"
                  : "实盘资金曲线与回测偏差较大，策略可能需要调整",
          };

        case "alerts":
          if (report.alerts.length === 0) {
            return { action: "alerts", alerts: [], summary: "实盘表现与回测预期一致，未发现显著偏差" };
          }
          return {
            action: "alerts",
            overallHealthScore: report.overallHealthScore,
            criticalCount: report.alerts.filter((a) => a.severity === "critical").length,
            warningCount: report.alerts.filter((a) => a.severity === "warning").length,
            alerts: report.alerts.map((a) => ({
              severity: a.severity,
              severityCn: a.severity === "critical" ? "严重" : "警告",
              metric: a.metric,
              message: a.message,
              strategyId: a.strategyId,
            })),
            recommendation: report.alerts.some((a) => a.severity === "critical")
              ? "存在严重偏差，建议暂停策略并检查原因"
              : "存在轻微偏差，建议持续监控",
          };

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

function formatNum(v: number): string {
  if (v === Infinity) return "∞";
  if (v === -Infinity) return "-∞";
  return v.toFixed(2);
}
