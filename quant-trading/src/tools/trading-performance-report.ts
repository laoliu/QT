/**
 * trading_performance_report tool 66comprehensive trading performance report.
 *
 * Generates an on-demand performance report combining equity curve, trade attribution,
 * streak analysis, holding period analysis, capital utilization, and period comparison.
 *
 * Actions:
 *   full_report    66complete performance report
 *   equity_summary 66equity curve & drawdown summary
 *   streak_analysis 66win/loss streak analysis
 *   holding_analysis 66holding period breakdown (short/medium/long term)
 *   capital_utilization 66capital deployment efficiency
 *   period_compare 66week-over-week / month-over-month comparison
 *   best_worst     66best and worst trades
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingRuntime } from "../runtime.js";
import { EquityHistory } from "../services/equity-history.js";
import { generatePerformanceReport } from "../services/trading-performance-report.js";
import type { QuantTradingConfig, TradeRecord } from "../types.js";

function getTradesPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  return path.join(dir, "trades.json");
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(getTradesPath(config), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

export function createTradingPerformanceReportTool(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
) {
  return {
    name: "trading_performance_report",
    description:
      "06100805050507\n" +
      "050008010102130406010300008409\n" +
      "action=full_report 完整报告, action=equity_summary 05? " +
      "action=streak_analysis 连胜连亏, action=holding_analysis 持仓周期, " +
      "action=capital_utilization 资金利用? action=period_compare 0103, " +
      "action=best_worst 最佳最差交易",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "full_report",
            "equity_summary",
            "streak_analysis",
            "holding_analysis",
            "capital_utilization",
            "period_compare",
            "best_worst",
          ],
          description: "报告类型",
        },
        from: { type: "string", description: "起始日期 YYYY-MM-DD" },
        to: { type: "string", description: "截止日期 YYYY-MM-DD" },
        top_n: { type: "number", description: "008405(085)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const from = args.from as string | undefined;
      const to = args.to as string | undefined;
      const topN = (args.top_n as number) ?? 5;

      // Load trades
      const trades = await loadTrades(config);

      // Load equity history
      const equityHistory = new EquityHistory({
        dataDir: config.dataDir,
        mode: config.tradingAccount?.mode,
      });
      await equityHistory.load();
      const snapshots = equityHistory.getAll();

      if (trades.length === 0 && snapshots.length === 0) {
        return { error: "150604020513081905060700" };
      }

      const report = generatePerformanceReport(trades, snapshots, { from, to, topN });

      const fmtPnl = (n: number) => `${n >= 0 ? "+" : ""}06${n.toFixed(2)}`;
      const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

      switch (action) {
        case "full_report":
          return {
            reportDate: report.reportDate,
            dateRange: report.dateRange,
            equitySummary: {
              startEquity: `06${report.equity.startEquity.toFixed(2)}`,
              endEquity: `06${report.equity.endEquity.toFixed(2)}`,
              totalReturn: fmtPnl(report.equity.totalReturn),
              totalReturnPct: fmtPct(report.equity.totalReturnPct),
              maxDrawdown: `06${report.equity.maxDrawdown.toFixed(2)}`,
              maxDrawdownPct: `${report.equity.maxDrawdownPct.toFixed(2)}%`,
              bestDay: report.equity.bestDay
                ? `${report.equity.bestDay.date} ${fmtPnl(report.equity.bestDay.pnl)}`
                : null,
              worstDay: report.equity.worstDay
                ? `${report.equity.worstDay.date} ${fmtPnl(report.equity.worstDay.pnl)}`
                : null,
              winDayRate: `${report.equity.winDayRate.toFixed(1)}%`,
              totalDays: report.equity.totalDays,
            },
            tradeStats: {
              totalTrades: report.trades.totalTrades,
              totalNetPnl: fmtPnl(report.trades.totalNetPnl),
              totalCommission: `06${report.trades.totalCommission.toFixed(2)}`,
              winRate: `${(report.trades.winRate * 100).toFixed(1)}%`,
              avgWin: `06${report.trades.avgWin.toFixed(2)}`,
              avgLoss: `06${report.trades.avgLoss.toFixed(2)}`,
              profitFactor:
                report.trades.profitFactor === Infinity ? "鈭" : report.trades.profitFactor.toFixed(2),
              avgTradesPerWeek: report.trades.avgTradesPerWeek.toFixed(1),
              expectancy: fmtPnl(report.trades.expectancy),
            },
            streaks: {
              currentStreak: `${report.streaks.currentStreakType === "win" ? "09": report.streaks.currentStreakType === "loss" ? "连亏": "鏃"} ${report.streaks.currentStreak}笔`,
              maxWinStreak: `${report.streaks.maxWinStreak}笔(${fmtPnl(report.streaks.maxWinStreakPnl)})`,
              maxLossStreak: `${report.streaks.maxLossStreak}笔(${fmtPnl(report.streaks.maxLossStreakPnl)})`,
            },
            capitalUtilization: {
              avgUtilization: `${report.capitalUtilization.avgUtilizationPct.toFixed(1)}%`,
              maxUtilization: `${report.capitalUtilization.maxUtilizationPct.toFixed(1)}%`,
              highUtilizationDays: report.capitalUtilization.highUtilizationDays,
              lowUtilizationDays: report.capitalUtilization.lowUtilizationDays,
            },
            holdingAnalysis: {
              avgHoldingDays: report.holdingAnalysis.avgHoldingDays.toFixed(1),
              medianHoldingDays: report.holdingAnalysis.medianHoldingDays,
              shortTerm: `${report.holdingAnalysis.shortTermCount}笔${fmtPnl(report.holdingAnalysis.shortTermPnl)} 09${(report.holdingAnalysis.shortTermWinRate * 100).toFixed(0)}%`,
              mediumTerm: `${report.holdingAnalysis.mediumTermCount}笔${fmtPnl(report.holdingAnalysis.mediumTermPnl)} 09${(report.holdingAnalysis.mediumTermWinRate * 100).toFixed(0)}%`,
              longTerm: `${report.holdingAnalysis.longTermCount}笔${fmtPnl(report.holdingAnalysis.longTermPnl)} 09${(report.holdingAnalysis.longTermWinRate * 100).toFixed(0)}%`,
            },
            bestTrades: report.bestTrades.map((t) => ({
              symbol: t.symbol,
              name: t.name,
              netPnl: fmtPnl(t.netPnl),
              returnPct: fmtPct(t.returnPct),
              holdingDays: t.holdingDays,
              strategy: t.strategyId,
            })),
            worstTrades: report.worstTrades.map((t) => ({
              symbol: t.symbol,
              name: t.name,
              netPnl: fmtPnl(t.netPnl),
              returnPct: fmtPct(t.returnPct),
              holdingDays: t.holdingDays,
              strategy: t.strategyId,
            })),
            weekOverWeek: report.weekOverWeek
              ? {
                  current: `${report.weekOverWeek.currentPeriod}: ${report.weekOverWeek.current.trades}笔${fmtPnl(report.weekOverWeek.current.netPnl)}`,
                  previous: `${report.weekOverWeek.previousPeriod}: ${report.weekOverWeek.previous.trades}笔${fmtPnl(report.weekOverWeek.previous.netPnl)}`,
                  pnlChange: fmtPnl(report.weekOverWeek.delta.pnlChange),
                  winRateChange: fmtPct(report.weekOverWeek.delta.winRateChange * 100),
                }
              : null,
            monthOverMonth: report.monthOverMonth
              ? {
                  current: `${report.monthOverMonth.currentPeriod}: ${report.monthOverMonth.current.trades}笔${fmtPnl(report.monthOverMonth.current.netPnl)}`,
                  previous: `${report.monthOverMonth.previousPeriod}: ${report.monthOverMonth.previous.trades}笔${fmtPnl(report.monthOverMonth.previous.netPnl)}`,
                  pnlChange: fmtPnl(report.monthOverMonth.delta.pnlChange),
                  winRateChange: fmtPct(report.monthOverMonth.delta.winRateChange * 100),
                }
              : null,
          };

        case "equity_summary":
          return {
            dateRange: report.dateRange,
            startEquity: `06${report.equity.startEquity.toFixed(2)}`,
            endEquity: `06${report.equity.endEquity.toFixed(2)}`,
            totalReturn: fmtPnl(report.equity.totalReturn),
            totalReturnPct: fmtPct(report.equity.totalReturnPct),
            maxDrawdown: `06${report.equity.maxDrawdown.toFixed(2)}`,
            maxDrawdownPct: `${report.equity.maxDrawdownPct.toFixed(2)}%`,
            avgDailyPnl: fmtPnl(report.equity.avgDailyPnl),
            bestDay: report.equity.bestDay
              ? { date: report.equity.bestDay.date, pnl: fmtPnl(report.equity.bestDay.pnl) }
              : null,
            worstDay: report.equity.worstDay
              ? { date: report.equity.worstDay.date, pnl: fmtPnl(report.equity.worstDay.pnl) }
              : null,
            winDays: report.equity.winDays,
            lossDays: report.equity.lossDays,
            winDayRate: `${report.equity.winDayRate.toFixed(1)}%`,
            totalDays: report.equity.totalDays,
          };

        case "streak_analysis":
          return {
            currentStreak: report.streaks.currentStreak,
            currentStreakType:
              report.streaks.currentStreakType === "win"
                ? "09"
                : report.streaks.currentStreakType === "loss"
                  ? "连亏"
                  : "鏃",
            maxWinStreak: report.streaks.maxWinStreak,
            maxWinStreakPnl: fmtPnl(report.streaks.maxWinStreakPnl),
            maxLossStreak: report.streaks.maxLossStreak,
            maxLossStreakPnl: fmtPnl(report.streaks.maxLossStreakPnl),
            totalTrades: report.trades.totalTrades,
            winRate: `${(report.trades.winRate * 100).toFixed(1)}%`,
          };

        case "holding_analysis":
          return {
            avgHoldingDays: report.holdingAnalysis.avgHoldingDays.toFixed(1),
            medianHoldingDays: report.holdingAnalysis.medianHoldingDays,
            shortTerm: {
              label: "短线 (≈大",
              count: report.holdingAnalysis.shortTermCount,
              netPnl: fmtPnl(report.holdingAnalysis.shortTermPnl),
              winRate: `${(report.holdingAnalysis.shortTermWinRate * 100).toFixed(1)}%`,
            },
            mediumTerm: {
              label: "波段 (6-20大",
              count: report.holdingAnalysis.mediumTermCount,
              netPnl: fmtPnl(report.holdingAnalysis.mediumTermPnl),
              winRate: `${(report.holdingAnalysis.mediumTermWinRate * 100).toFixed(1)}%`,
            },
            longTerm: {
              label: "гb(>20大",
              count: report.holdingAnalysis.longTermCount,
              netPnl: fmtPnl(report.holdingAnalysis.longTermPnl),
              winRate: `${(report.holdingAnalysis.longTermWinRate * 100).toFixed(1)}%`,
            },
          };

        case "capital_utilization":
          return {
            avgUtilization: `${report.capitalUtilization.avgUtilizationPct.toFixed(1)}%`,
            maxUtilization: `${report.capitalUtilization.maxUtilizationPct.toFixed(1)}%`,
            minUtilization: `${report.capitalUtilization.minUtilizationPct.toFixed(1)}%`,
            highUtilizationDays: report.capitalUtilization.highUtilizationDays,
            lowUtilizationDays: report.capitalUtilization.lowUtilizationDays,
            totalDays: report.equity.totalDays,
          };

        case "period_compare":
          return {
            weekOverWeek: report.weekOverWeek
              ? {
                  currentPeriod: report.weekOverWeek.currentPeriod,
                  previousPeriod: report.weekOverWeek.previousPeriod,
                  current: {
                    trades: report.weekOverWeek.current.trades,
                    netPnl: fmtPnl(report.weekOverWeek.current.netPnl),
                    winRate: `${(report.weekOverWeek.current.winRate * 100).toFixed(1)}%`,
                  },
                  previous: {
                    trades: report.weekOverWeek.previous.trades,
                    netPnl: fmtPnl(report.weekOverWeek.previous.netPnl),
                    winRate: `${(report.weekOverWeek.previous.winRate * 100).toFixed(1)}%`,
                  },
                  delta: {
                    pnlChange: fmtPnl(report.weekOverWeek.delta.pnlChange),
                    tradeCountChange: report.weekOverWeek.delta.tradeCountChange,
                    winRateChange: fmtPct(report.weekOverWeek.delta.winRateChange * 100),
                  },
                }
              : "本周/上周暂无交易数据",
            monthOverMonth: report.monthOverMonth
              ? {
                  currentPeriod: report.monthOverMonth.currentPeriod,
                  previousPeriod: report.monthOverMonth.previousPeriod,
                  current: {
                    trades: report.monthOverMonth.current.trades,
                    netPnl: fmtPnl(report.monthOverMonth.current.netPnl),
                    winRate: `${(report.monthOverMonth.current.winRate * 100).toFixed(1)}%`,
                  },
                  previous: {
                    trades: report.monthOverMonth.previous.trades,
                    netPnl: fmtPnl(report.monthOverMonth.previous.netPnl),
                    winRate: `${(report.monthOverMonth.previous.winRate * 100).toFixed(1)}%`,
                  },
                  delta: {
                    pnlChange: fmtPnl(report.monthOverMonth.delta.pnlChange),
                    tradeCountChange: report.monthOverMonth.delta.tradeCountChange,
                    winRateChange: fmtPct(report.monthOverMonth.delta.winRateChange * 100),
                  },
                }
              : "本月/上月暂无交易数据",
          };

        case "best_worst":
          return {
            bestTrades: report.bestTrades.map((t) => ({
              symbol: t.symbol,
              name: t.name,
              entryTime: t.entryTime.slice(0, 10),
              exitTime: t.exitTime.slice(0, 10),
              netPnl: fmtPnl(t.netPnl),
              returnPct: fmtPct(t.returnPct),
              holdingDays: t.holdingDays,
              strategy: t.strategyId,
            })),
            worstTrades: report.worstTrades.map((t) => ({
              symbol: t.symbol,
              name: t.name,
              entryTime: t.entryTime.slice(0, 10),
              exitTime: t.exitTime.slice(0, 10),
              netPnl: fmtPnl(t.netPnl),
              returnPct: fmtPct(t.returnPct),
              holdingDays: t.holdingDays,
              strategy: t.strategyId,
            })),
          };

        default:
          return { error: `ā04: ${action}` };
      }
    },
  };
}
