/**
 * live_backtest_deviation tool — AI-callable live vs backtest deviation analysis.
 *
 * Compares live/paper trading performance against backtest expectations,
 * computing return deviation, slippage analysis, win rate comparison,
 * equity curve correlation, fee impact, holding period analysis,
 * monthly return deviation, and per-strategy health scoring.
 *
 * Actions:
 *   full_report   — comprehensive deviation analysis
 *   slippage      — slippage-only analysis
 *   equity_curve  — equity curve correlation
 *   alerts        — deviation alerts only
 *   fee_impact    — fee impact comparison
 *   holding       — holding period analysis
 *   monthly       — monthly return deviation
 *   win_loss      — win/loss size and streak analysis
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
      "包含收益率偏差、年化收益偏差、夏普/索提诺比率偏差、滑点分析、胜率对比、" +
      "资金曲线相关性、费用影响对比、持仓周期分析、月度收益偏差、" +
      "连胜连亏分析和策略健康评分。" +
      "action=full_report 完整报告, action=slippage 滑点分析, " +
      "action=equity_curve 资金曲线相关性, action=alerts 偏差告警, " +
      "action=fee_impact 费用影响对比, action=holding 持仓周期分析, " +
      "action=monthly 月度收益偏差, action=win_loss 盈亏/连胜连亏分析。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["full_report", "slippage", "equity_curve", "alerts", "fee_impact", "holding", "monthly", "win_loss"],
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
          medianSlippageBps: `${slippage.medianSlippageBps.toFixed(1)} bps`,
          maxSlippageBps: `${slippage.maxSlippageBps.toFixed(1)} bps`,
          p95SlippageBps: `${slippage.p95SlippageBps.toFixed(1)} bps`,
          slippageStdBps: `${slippage.slippageStdBps.toFixed(1)} bps`,
          totalSlippageCost: `¥${slippage.totalSlippageCost.toFixed(2)}`,
          slippagePctOfCapital: `${slippage.slippagePctOfCapital.toFixed(2)}%`,
          buyAvgSlippageBps: `${slippage.buyAvgSlippageBps.toFixed(1)} bps`,
          sellAvgSlippageBps: `${slippage.sellAvgSlippageBps.toFixed(1)} bps`,
          topSlippageTrades: slippage.tradeDetails
            .sort((a, b) => b.slippageBps - a.slippageBps)
            .slice(0, 5)
            .map((d) => ({ symbol: d.symbol, slippage: `${d.slippageBps.toFixed(1)} bps`, side: d.side })),
          summary:
            slippage.avgSlippageBps > 10
              ? "滑点偏高，建议检查订单执行质量或调整下单策略"
              : slippage.avgSlippageBps > 5
                ? "滑点略高于正常范围，建议关注大单执行"
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
              medianSlippageBps: `${report.slippage.medianSlippageBps.toFixed(1)} bps`,
              maxSlippageBps: `${report.slippage.maxSlippageBps.toFixed(1)} bps`,
              p95SlippageBps: `${report.slippage.p95SlippageBps.toFixed(1)} bps`,
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
            feeImpact: report.feeImpact
              ? {
                  backtestFees: `¥${report.feeImpact.backtestFees.toFixed(2)}`,
                  liveFees: `¥${report.feeImpact.liveFees.toFixed(2)}`,
                  feeDifference: `¥${report.feeImpact.feeDifference.toFixed(2)}`,
                  backtestFeeImpactPct: `${report.feeImpact.backtestFeeImpactPct.toFixed(2)}%`,
                  liveFeeImpactPct: `${report.feeImpact.liveFeeImpactPct.toFixed(2)}%`,
                }
              : null,
            holdingPeriod: {
              backtestAvgDays: report.holdingPeriod.backtestAvgDays.toFixed(1),
              liveAvgDays: report.holdingPeriod.liveAvgDays.toFixed(1),
              liveMedianDays: report.holdingPeriod.liveMedianDays.toFixed(1),
              deviationDays: report.holdingPeriod.deviationDays.toFixed(1),
              distribution: report.holdingPeriod.liveDistribution,
            },
            winLoss: {
              liveAvgWin: `¥${report.winLossComparison.liveAvgWin.toFixed(2)}`,
              liveAvgLoss: `¥${report.winLossComparison.liveAvgLoss.toFixed(2)}`,
              maxConsecWins: report.winLossComparison.liveMaxConsecWins,
              maxConsecLosses: report.winLossComparison.liveMaxConsecLosses,
              currentStreak: report.winLossComparison.liveCurrentStreak,
              currentStreakLabel: report.winLossComparison.liveCurrentStreak > 0
                ? `连赢${report.winLossComparison.liveCurrentStreak}笔`
                : report.winLossComparison.liveCurrentStreak < 0
                  ? `连亏${Math.abs(report.winLossComparison.liveCurrentStreak)}笔`
                  : "无连续记录",
            },
            monthlyReturns: report.monthlyReturns
              ? report.monthlyReturns.map((mr) => ({
                  month: mr.month,
                  backtest: `${mr.backtestReturn.toFixed(2)}%`,
                  live: `${mr.liveReturn.toFixed(2)}%`,
                  deviation: `${mr.deviation.toFixed(2)}pp`,
                  severity: mr.severity,
                }))
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

        case "fee_impact":
          if (!report.feeImpact) {
            return { error: "回测结果中未包含费用数据，无法进行费用对比分析" };
          }
          return {
            action: "fee_impact",
            backtestFees: `¥${report.feeImpact.backtestFees.toFixed(2)}`,
            liveFees: `¥${report.feeImpact.liveFees.toFixed(2)}`,
            feeDifference: `¥${report.feeImpact.feeDifference.toFixed(2)}`,
            feeDiffPctOfCapital: `${report.feeImpact.feeDiffPctOfCapital.toFixed(2)}%`,
            backtestFeeImpactPct: `${report.feeImpact.backtestFeeImpactPct.toFixed(2)}%`,
            liveFeeImpactPct: `${report.feeImpact.liveFeeImpactPct.toFixed(2)}%`,
            summary: report.feeImpact.feeDifference > 0
              ? `实盘费用高于回测模拟 ¥${report.feeImpact.feeDifference.toFixed(2)}，占资金 ${report.feeImpact.feeDiffPctOfCapital.toFixed(2)}%`
              : report.feeImpact.feeDifference < 0
                ? `实盘费用低于回测模拟 ¥${Math.abs(report.feeImpact.feeDifference).toFixed(2)}，实际交易成本控制良好`
                : "实盘费用与回测模拟基本一致",
          };

        case "holding":
          return {
            action: "holding",
            backtestAvgDays: report.holdingPeriod.backtestAvgDays.toFixed(1),
            liveAvgDays: report.holdingPeriod.liveAvgDays.toFixed(1),
            liveMedianDays: report.holdingPeriod.liveMedianDays.toFixed(1),
            deviationDays: report.holdingPeriod.deviationDays.toFixed(1),
            distribution: report.holdingPeriod.liveDistribution,
            summary: Math.abs(report.holdingPeriod.deviationDays) > 5
              ? `持仓周期偏差 ${report.holdingPeriod.deviationDays.toFixed(1)} 天，${report.holdingPeriod.deviationDays > 0 ? "实盘持仓时间偏长，可能错过最佳卖点" : "实盘持仓时间偏短，可能过早止盈止损"}`
              : "持仓周期与回测预期基本一致",
          };

        case "monthly":
          if (!report.monthlyReturns || report.monthlyReturns.length === 0) {
            return { error: "月度收益数据不足，需要回测结果包含 monthlyReturns 字段" };
          }
          return {
            action: "monthly",
            months: report.monthlyReturns.map((mr) => ({
              month: mr.month,
              backtestReturn: `${mr.backtestReturn.toFixed(2)}%`,
              liveReturn: `${mr.liveReturn.toFixed(2)}%`,
              deviation: `${mr.deviation.toFixed(2)}pp`,
              severity: mr.severity,
              severityCn: mr.severity === "critical" ? "严重" : mr.severity === "warning" ? "警告" : "正常",
            })),
            worstMonth: (() => {
              const worst = [...report.monthlyReturns].sort((a, b) => b.deviation - a.deviation)[0];
              return worst ? { month: worst.month, deviation: `${worst.deviation.toFixed(2)}pp` } : null;
            })(),
            criticalMonths: report.monthlyReturns.filter((m) => m.severity === "critical").length,
            warningMonths: report.monthlyReturns.filter((m) => m.severity === "warning").length,
            summary: report.monthlyReturns.some((m) => m.severity === "critical")
              ? "部分月份收益偏差严重，建议逐月排查策略执行问题"
              : report.monthlyReturns.some((m) => m.severity === "warning")
                ? "部分月份存在轻微偏差，整体趋势可接受"
                : "各月度收益与回测预期基本吻合",
          };

        case "win_loss":
          return {
            action: "win_loss",
            liveAvgWin: `¥${report.winLossComparison.liveAvgWin.toFixed(2)}`,
            liveAvgLoss: `¥${report.winLossComparison.liveAvgLoss.toFixed(2)}`,
            avgWinLossRatio: report.winLossComparison.liveAvgLoss > 0
              ? (report.winLossComparison.liveAvgWin / report.winLossComparison.liveAvgLoss).toFixed(2)
              : "∞",
            maxConsecWins: report.winLossComparison.liveMaxConsecWins,
            maxConsecLosses: report.winLossComparison.liveMaxConsecLosses,
            currentStreak: report.winLossComparison.liveCurrentStreak,
            currentStreakLabel: report.winLossComparison.liveCurrentStreak > 0
              ? `连赢${report.winLossComparison.liveCurrentStreak}笔`
              : report.winLossComparison.liveCurrentStreak < 0
                ? `连亏${Math.abs(report.winLossComparison.liveCurrentStreak)}笔`
                : "无连续记录",
            summary: report.winLossComparison.liveMaxConsecLosses >= 5
              ? `最大连亏 ${report.winLossComparison.liveMaxConsecLosses} 笔，需关注策略稳定性和风控`
              : report.winLossComparison.liveCurrentStreak < -3
                ? `当前连亏 ${Math.abs(report.winLossComparison.liveCurrentStreak)} 笔，建议检查市场环境是否适配策略`
                : "盈亏分布正常",
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
