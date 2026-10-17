/**
 * performance_attribution tool — AI-callable performance analysis.
 *
 * Analyzes completed trades and breaks down P&L by strategy, symbol, and time period.
 *
 * Actions:
 *   full_report   — comprehensive attribution analysis
 *   by_strategy   — P&L breakdown by strategy
 *   by_symbol     — P&L breakdown by symbol
 *   by_period     — P&L by day/week/month/year
 *   chart_data    — chart-ready data series (equity curve, drawdown, distribution, heatmap)
 *   text_summary  — plain-text formatted performance report
 *   html_summary  — HTML formatted performance report with styling
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingRuntime } from "../runtime.js";
import {
  computeAttribution,
  computeRiskMetrics,
  generateHtmlSummary,
  generateTextSummary,
  prepareChartData,
} from "../services/performance-attribution.js";
import type { QuantTradingConfig, TradeRecord } from "../types.js";

function getTradesPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "trades.json");
}

function getEquityCurvePath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "equity-curve.json");
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(getTradesPath(config), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

async function loadEquityCurve(
  config: QuantTradingConfig,
): Promise<{ date: string; value: number }[]> {
  try {
    return JSON.parse(await fs.readFile(getEquityCurvePath(config), "utf-8")) as {
      date: string;
      value: number;
    }[];
  } catch {
    return [];
  }
}

export function createPerformanceAttributionTool(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
) {
  return {
    name: "performance_attribution",
    description:
      "绩效归因分析与报告生成。分析交易记录，按策略/标的/时间维度分解收益来源，生成图表数据和格式化报告。" +
      "action=full_report 完整报告, action=by_strategy 按策略, " +
      "action=by_symbol 按标的, action=by_period 按时间, " +
      "action=chart_data 图表数据, action=text_summary 文本报告, action=html_summary HTML报告。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "full_report",
            "by_strategy",
            "by_symbol",
            "by_period",
            "chart_data",
            "text_summary",
            "html_summary",
          ],
          description: "分析类型",
        },
        from: { type: "string", description: "起始日期 YYYY-MM-DD" },
        to: { type: "string", description: "截止日期 YYYY-MM-DD" },
        period_type: {
          type: "string",
          enum: ["daily", "weekly", "monthly", "yearly"],
          description: "时间粒度 (by_period 操作)",
        },
        limit: { type: "number", description: "返回前N项" },
        initial_cash: { type: "number", description: "初始资金 (默认1000000)" },
        risk_free_rate: { type: "number", description: "无风险利率 (默认0.03)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const trades = await loadTrades(config);
      if (trades.length === 0 && action !== "text_summary" && action !== "html_summary") {
        return { error: "暂无交易记录" };
      }

      const result = computeAttribution(trades, {
        from: args.from as string | undefined,
        to: args.to as string | undefined,
      });

      const limit = (args.limit as number) ?? 20;
      const initialCash = (args.initial_cash as number) ?? 1000000;
      const riskFreeRate = (args.risk_free_rate as number) ?? 0.03;

      switch (action) {
        case "full_report":
          return {
            dateRange: result.dateRange,
            totalTrades: result.totalTrades,
            totalNetPnl: `¥${result.totalNetPnl.toFixed(2)}`,
            totalGrossPnl: `¥${result.totalGrossPnl.toFixed(2)}`,
            totalCommission: `¥${result.totalCommission.toFixed(2)}`,
            overallWinRate: `${(result.overallWinRate * 100).toFixed(1)}%`,
            avgWin: `¥${result.avgWin.toFixed(2)}`,
            avgLoss: `¥${result.avgLoss.toFixed(2)}`,
            profitFactor: result.profitFactor === Infinity ? "∞" : result.profitFactor.toFixed(2),
            maxConsecutiveWins: result.maxConsecutiveWins,
            maxConsecutiveLosses: result.maxConsecutiveLosses,
            avgHoldingDays: result.avgHoldingDays.toFixed(1),
            largestWin: `¥${result.largestWin.toFixed(2)}`,
            largestLoss: `¥${result.largestLoss.toFixed(2)}`,
            expectancy: `¥${result.expectancy.toFixed(2)}/笔`,
            topStrategies: result.byStrategy.slice(0, limit).map((s) => ({
              strategy: s.strategyId,
              trades: s.trades,
              netPnl: `¥${s.netPnl.toFixed(2)}`,
              winRate: `${(s.winRate * 100).toFixed(1)}%`,
              contribution: `${s.contributionPct.toFixed(1)}%`,
            })),
            topSymbols: result.bySymbol.slice(0, limit).map((s) => ({
              symbol: s.symbol,
              trades: s.trades,
              netPnl: `¥${s.netPnl.toFixed(2)}`,
              winRate: `${(s.winRate * 100).toFixed(1)}%`,
              contribution: `${s.contributionPct.toFixed(1)}%`,
            })),
            monthlyPnl: result.byMonth.map((p) => ({
              month: p.period,
              trades: p.trades,
              netPnl: `¥${p.netPnl.toFixed(2)}`,
              cumPnl: `¥${p.cumPnl.toFixed(2)}`,
            })),
          };

        case "by_strategy":
          return {
            totalTrades: result.totalTrades,
            strategies: result.byStrategy.slice(0, limit).map((s) => ({
              strategy: s.strategyId,
              trades: s.trades,
              wins: s.wins,
              losses: s.losses,
              winRate: `${(s.winRate * 100).toFixed(1)}%`,
              netPnl: `¥${s.netPnl.toFixed(2)}`,
              avgWin: `¥${s.avgWin.toFixed(2)}`,
              avgLoss: `¥${s.avgLoss.toFixed(2)}`,
              largestWin: `¥${s.largestWin.toFixed(2)}`,
              largestLoss: `¥${s.largestLoss.toFixed(2)}`,
              profitFactor: s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2),
              contribution: `${s.contributionPct.toFixed(1)}%`,
            })),
          };

        case "by_symbol":
          return {
            totalTrades: result.totalTrades,
            symbols: result.bySymbol.slice(0, limit).map((s) => ({
              symbol: s.symbol,
              trades: s.trades,
              netPnl: `¥${s.netPnl.toFixed(2)}`,
              avgPnl: `¥${s.avgPnl.toFixed(2)}`,
              winRate: `${(s.winRate * 100).toFixed(1)}%`,
              avgHoldingDays: s.holdingDaysAvg.toFixed(1),
              contribution: `${s.contributionPct.toFixed(1)}%`,
            })),
          };

        case "by_period": {
          const periodType = (args.period_type as string) ?? "daily";
          const periods =
            periodType === "weekly"
              ? result.byWeek
              : periodType === "monthly"
                ? result.byMonth
                : periodType === "yearly"
                  ? result.byYear
                  : result.byDay;
          return {
            periodType,
            periods: periods.slice(-limit).map((p) => ({
              period: p.period,
              trades: p.trades,
              netPnl: `¥${p.netPnl.toFixed(2)}`,
              cumPnl: `¥${p.cumPnl.toFixed(2)}`,
              wins: p.wins,
              losses: p.losses,
            })),
          };
        }

        case "chart_data": {
          const equityCurve = await loadEquityCurve(config);
          const chartData = prepareChartData(trades, equityCurve, initialCash);
          return {
            equityCurve: chartData.equityCurve.slice(-limit * 10),
            cumulativePnl: chartData.cumulativePnl,
            dailyReturns: chartData.dailyReturns.slice(-limit * 10),
            returnDistribution: chartData.returnDistribution,
            monthlyHeatmap: chartData.monthlyHeatmap,
            drawdownSeries: chartData.drawdownSeries.slice(-limit * 10),
            pnlByDayOfWeek: chartData.pnlByDayOfWeek,
          };
        }

        case "text_summary": {
          const equityCurve = await loadEquityCurve(config);
          const risk = equityCurve.length >= 2
            ? computeRiskMetrics(equityCurve, riskFreeRate)
            : null;
          const text = generateTextSummary(result, risk);
          return { format: "text", content: text };
        }

        case "html_summary": {
          const equityCurve = await loadEquityCurve(config);
          const risk = equityCurve.length >= 2
            ? computeRiskMetrics(equityCurve, riskFreeRate)
            : null;
          const charts = prepareChartData(trades, equityCurve, initialCash);
          const html = generateHtmlSummary(result, risk, charts);
          return { format: "html", content: html };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
