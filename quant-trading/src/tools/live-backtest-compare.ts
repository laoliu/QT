/**
 * live_backtest_compare tool — AI-callable tool for comparing live/paper
 * trading performance against backtest predictions.
 *
 * Actions:
 *   compare          — run a full live-vs-backtest comparison
 *   drift_check      — quick drift score for a strategy
 *   list_backtests   — list stored backtest records available for comparison
 *   period_compare   — monthly or weekly segmented comparison
 *   trade_match      — signal-by-signal matching analysis
 *   drift_history    — rolling drift trend tracking
 *   symbol_breakdown — per-symbol breakdown of drift contributions
 *   report           — comprehensive combined report with recommendations
 *   out_of_sample    — forward walk validation (post-backtest live performance)
 *   closest_backtest — find best-matching backtest among multiple candidates
 *   export           — export comparison results as CSV file
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import {
  compareLiveVsBacktest,
  comparePeriodSegmented,
  matchTrades,
  computeDriftHistory,
  compareBySymbol,
  compareOutOfSample,
  findClosestBacktest,
  exportComparisonCsv,
  type LiveBacktestCompareResult,
  type SegmentGranularity,
  type PeriodSegmentedResult,
  type TradeMatchResult,
  type DriftHistory,
  type PerSymbolBreakdownResult,
} from "../services/live-backtest-compare.js";
import type { AlertStore } from "../services/alert-store.js";
import type { QuantTradingConfig, TradeRecord, TradingSignal } from "../types.js";

function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  try {
    return JSON.parse(await fs.readFile(path.join(dir, "trades.json"), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

async function loadSignals(config: QuantTradingConfig): Promise<TradingSignal[]> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  try {
    return JSON.parse(
      await fs.readFile(path.join(dir, "signals.json"), "utf-8"),
    ) as TradingSignal[];
  } catch {
    return [];
  }
}

function formatResult(result: LiveBacktestCompareResult) {
  const { returnComparison: rc, drawdownComparison: dc, riskComparison: rkc, executionQuality: eq, drift } = result;

  const base: Record<string, unknown> = {
    策略: result.strategyId,
    比较期间: `${result.period.start} ~ ${result.period.end}`,
    收益对比: {
      回测收益: `${fmt(rc.backtestReturn, 2)}%`,
      实盘收益: `${fmt(rc.liveReturn, 2)}%`,
      收益偏差: `${fmt(rc.returnDiff, 2)}%`,
      回测年化: `${fmt(rc.backtestAnnualizedReturn, 2)}%`,
      实盘年化: `${fmt(rc.liveAnnualizedReturn, 2)}%`,
    },
    回撤对比: {
      回测最大回撤: `${fmt(dc.backtestMaxDrawdown, 2)}%`,
      实盘最大回撤: `${fmt(dc.liveMaxDrawdown, 2)}%`,
      回撤偏差: `${fmt(dc.drawdownDiff, 2)}%`,
    },
    风险对比: {
      回测Sharpe: fmt(rkc.backtestSharpe, 2),
      实盘Sharpe: fmt(rkc.liveSharpe, 2),
      回测胜率: `${fmt(rkc.backtestWinRate, 1)}%`,
      实盘胜率: `${fmt(rkc.liveWinRate, 1)}%`,
    },
    执行质量: {
      回测信号数: eq.backtestSignalCount,
      实盘执行数: eq.liveExecutedCount,
      执行率: `${fmt(eq.fillRate * 100, 0)}%`,
      平均滑点: `${fmt(eq.avgSlippagePct, 3)}%`,
      回测交易数: eq.backtestTrades,
      实盘交易数: eq.liveTrades,
    },
    漂移分析: {
      漂移得分: `${drift.score}/100`,
      严重程度: drift.severity,
      分项: {
        收益漂移: drift.components.returnDrift,
        回撤漂移: drift.components.drawdownDrift,
        执行漂移: drift.components.executionDrift,
        风险漂移: drift.components.riskDrift,
      },
      观察: drift.observations,
    },
    权益曲线数据点: result.equityCurveOverlay.points.length,
  };

  // Benchmark comparison (if available)
  if (result.benchmarkComparison) {
    const bc = result.benchmarkComparison;
    base.基准对比 = {
      基准收益: `${fmt(bc.benchmarkReturn, 2)}%`,
      实盘Alpha: `${fmt(bc.liveAlpha, 2)}%`,
      回测Alpha: `${fmt(bc.backtestAlpha, 2)}%`,
      归因: bc.attribution === "market_decline" ? "市场下跌" :
        bc.attribution === "strategy_specific" ? "策略自身" :
        bc.attribution === "both" ? "市场+策略" : "跑赢基准",
    };
  }

  // Extended risk metrics
  if (result.extendedRisk) {
    const er = result.extendedRisk;
    base.扩展风险指标 = {
      回测Sortino: fmt(er.backtestSortino, 2),
      实盘Sortino: fmt(er.liveSortino, 2),
      回测Calmar: fmt(er.backtestCalmar, 2),
      实盘Calmar: fmt(er.liveCalmar, 2),
      回测盈亏比: fmt(er.backtestProfitFactor, 2),
      实盘盈亏比: fmt(er.liveProfitFactor, 2),
    };
  }

  // Cost decomposition
  if (result.costDecomposition) {
    const cd = result.costDecomposition;
    base.成本分析 = {
      实盘佣金: `¥${fmt(cd.totalCommission, 2)}`,
      实盘税费: `¥${fmt(cd.totalTax, 2)}`,
      成本占比: `${fmt(cd.costPct, 3)}%`,
      回测费用: `¥${fmt(cd.backtestFees, 2)}`,
      费用差异: `¥${fmt(cd.costDiff, 2)}`,
      成本解释收益差距: `${fmt(cd.costExplainedGapPct, 1)}%`,
    };
  }

  return base;
}

export function createLiveBacktestCompareTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  backtestStore: BacktestStore,
  equityHistoryPaper: EquityHistory,
  equityHistoryLive: EquityHistory,
  alertStore?: AlertStore,
) {
  return {
    name: "live_backtest_compare",
    description:
      "实盘回测对比分析。将实盘/模拟盘的实际交易表现与回测预测进行对比，" +
      "检测收益漂移、回撤偏差、执行质量和信号填充率。" +
      "action=compare 完整对比, action=drift_check 快速漂移检查, " +
      "action=list_backtests 列出可对比的回测记录, " +
      "action=period_compare 按月/周分段对比, " +
      "action=trade_match 逐笔交易匹配分析, " +
      "action=drift_history 漂移趋势跟踪, " +
      "action=symbol_breakdown 按标的分拆对比, " +
      "action=report 生成完整对比报告, " +
      "action=out_of_sample 样本外验证（回测后实盘表现）, " +
      "action=closest_backtest 在多个回测中找到最匹配实盘走势的回测, " +
      "action=export 导出CSV格式对比报告（含权益曲线和逐笔匹配）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "drift_check", "list_backtests", "period_compare", "trade_match", "drift_history", "symbol_breakdown", "report", "out_of_sample", "closest_backtest", "export"],
          description: "操作类型",
        },
        backtest_id: {
          type: "string",
          description: "回测记录ID (来自backtest store)",
        },
        strategy_id: {
          type: "string",
          description: "策略ID (用于筛选回测记录和交易记录)",
        },
        start_date: { type: "string", description: "起始日期 YYYY-MM-DD" },
        end_date: { type: "string", description: "截止日期 YYYY-MM-DD" },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "实盘模式: paper=模拟盘, live=实盘 (默认paper)",
        },
        granularity: {
          type: "string",
          enum: ["weekly", "monthly"],
          description: "分段粒度 (period_compare使用，默认monthly)",
        },
        window_days: {
          type: "number",
          description: "漂移跟踪滚动窗口天数 (drift_history使用，默认30)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "list_backtests") {
        const strategyId = args.strategy_id as string | undefined;
        const entries = backtestStore.query({
          strategyId,
          sortBy: "createdAt",
          sortDir: "desc",
          limit: 20,
        });
        if (entries.length === 0) {
          return { message: strategyId ? `策略 ${strategyId} 无回测记录` : "暂无回测记录" };
        }
        return {
          count: entries.length,
          records: entries.map((e) => ({
            id: e.id,
            策略: e.strategyId,
            标的: e.symbols.join(","),
            期间: `${e.startDate}~${e.endDate}`,
            收益: `${fmt(e.totalReturn, 2)}%`,
            Sharpe: fmt(e.sharpeRatio, 2),
            创建时间: e.createdAt,
          })),
        };
      }

      // All other actions need backtest + live data
      const resolved = await resolveCompareData(args);
      if ("error" in resolved) return resolved;
      const { req } = resolved;

      switch (action) {
        case "compare": {
          const result = compareLiveVsBacktest(req);
          fireDriftAlert(result);
          return formatResult(result);
        }

        case "drift_check": {
          const result = compareLiveVsBacktest(req);
          fireDriftAlert(result);
          return {
            策略: result.strategyId,
            漂移得分: `${result.drift.score}/100`,
            严重程度: result.drift.severity,
            收益偏差: `${fmt(result.returnComparison.returnDiff, 2)}%`,
            执行率: `${fmt(result.executionQuality.fillRate * 100, 0)}%`,
            观察: result.drift.observations,
          };
        }

        case "period_compare": {
          const granularity = (args.granularity as SegmentGranularity) ?? "monthly";
          const segmented = comparePeriodSegmented(req, granularity);
          return {
            策略: segmented.strategyId,
            粒度: segmented.granularity === "monthly" ? "月度" : "周度",
            分段数: segmented.segments.length,
            漂移起始: segmented.driftOnset ?? "未检测到明显漂移",
            最差分段: segmented.worstSegment ?? "N/A",
            分段详情: segmented.segments.map((s) => ({
              区间: s.label,
              日期: `${s.startDate}~${s.endDate}`,
              回测收益: `${fmt(s.backtestReturn, 2)}%`,
              实盘收益: `${fmt(s.liveReturn, 2)}%`,
              收益偏差: `${fmt(s.returnDiff, 2)}%`,
              回测交易数: s.backtestTrades,
              实盘交易数: s.liveTrades,
              漂移得分: s.driftScore,
            })),
          };
        }

        case "trade_match": {
          const matched = matchTrades(req);
          const s = matched.summary;
          return {
            策略: matched.strategyId,
            期间: `${matched.period.start}~${matched.period.end}`,
            匹配统计: {
              回测信号总数: s.totalBacktestSignals,
              已匹配: s.matchedCount,
              未执行: s.missedCount,
              额外实盘交易: s.extraLiveCount,
              匹配率: `${fmt(s.matchRate * 100, 1)}%`,
              平均延迟: s.avgLatencyMs > 0 ? `${fmt(s.avgLatencyMs / 1000, 1)}秒` : "N/A",
              平均滑点: `${fmt(s.avgSlippagePct, 3)}%`,
            },
            问题标的: s.problematicSymbols.length > 0
              ? s.problematicSymbols.map((p) => `${p.symbol}: 未执行${p.missedCount}次`)
              : "无",
            逐笔明细: matched.matches.slice(0, 20).map((m) => ({
              类型: m.matchType === "matched" ? "已匹配" : m.matchType === "missed" ? "未执行" : "额外交易",
              标的: m.backtestSignal.symbol,
              方向: m.backtestSignal.direction,
              信号价格: fmt(m.backtestSignal.price, 2),
              成交价格: m.liveTrade ? fmt(m.liveTrade.entryPrice, 2) : "N/A",
              滑点: m.slippagePct != null ? `${fmt(m.slippagePct, 3)}%` : "N/A",
              实盘盈亏: m.liveTrade ? fmt(m.liveTrade.netPnl, 2) : "N/A",
            })),
            提示: matched.matches.length > 20 ? `仅显示前20条，共${matched.matches.length}条` : undefined,
          };
        }

        case "drift_history": {
          const windowDays = (args.window_days as number) ?? 30;
          const history = computeDriftHistory(req, windowDays);
          return {
            策略: history.strategyId,
            趋势: history.trend === "improving" ? "改善中" : history.trend === "worsening" ? "恶化中" : "稳定",
            平均漂移: `${history.avgDrift}/100`,
            高风险次数: history.alertCount,
            快照数: history.snapshots.length,
            漂移轨迹: history.snapshots.map((s) => ({
              日期: s.date,
              得分: s.score,
              严重程度: s.severity,
              收益漂移: s.components.returnDrift,
              回撤漂移: s.components.drawdownDrift,
              执行漂移: s.components.executionDrift,
              风险漂移: s.components.riskDrift,
            })),
          };
        }

        case "symbol_breakdown": {
          const breakdown = compareBySymbol(req);
          return {
            策略: breakdown.strategyId,
            期间: `${breakdown.period.start}~${breakdown.period.end}`,
            标的数: breakdown.symbols.length,
            漂移最大标的: breakdown.worstSymbol ?? "N/A",
            表现最好标的: breakdown.bestSymbol ?? "N/A",
            标的明细: breakdown.symbols.map((s) => ({
              标的: s.symbol,
              实盘收益: `¥${fmt(s.liveReturn, 2)}`,
              回测交易数: s.backtestTrades,
              实盘交易数: s.liveTrades,
              未执行信号: s.missedSignals,
              平均滑点: `${fmt(s.avgSlippagePct, 3)}%`,
              交易成本: `¥${fmt(s.totalCost, 2)}`,
              漂移贡献: fmt(s.driftContribution, 1),
            })),
          };
        }

        case "report": {
          const result = compareLiveVsBacktest(req);
          fireDriftAlert(result);
          const granularity = (args.granularity as SegmentGranularity) ?? "monthly";
          const segmented = comparePeriodSegmented(req, granularity);
          const matched = matchTrades(req);
          const history = computeDriftHistory(req);
          const breakdown = compareBySymbol(req);

          const report: Record<string, unknown> = {
            标题: `策略 ${req.strategyId} 实盘回测对比报告`,
            期间: `${req.startDate} ~ ${req.endDate}`,
            综合对比: formatResult(result),
            分段分析: {
              漂移起始: segmented.driftOnset ?? "未检测到",
              最差分段: segmented.worstSegment ?? "N/A",
              分段数: segmented.segments.length,
              分段摘要: segmented.segments.map((s) => ({
                区间: s.label,
                收益偏差: `${fmt(s.returnDiff, 2)}%`,
                漂移得分: s.driftScore,
              })),
            },
            交易匹配: {
              匹配率: `${fmt(matched.summary.matchRate * 100, 1)}%`,
              未执行信号: matched.summary.missedCount,
              额外交易: matched.summary.extraLiveCount,
              问题标的: matched.summary.problematicSymbols,
            },
            标的分拆: {
              漂移最大标的: breakdown.worstSymbol ?? "N/A",
              标的数: breakdown.symbols.length,
              明细: breakdown.symbols.slice(0, 10).map((s) => ({
                标的: s.symbol,
                实盘收益: `¥${fmt(s.liveReturn, 2)}`,
                未执行: s.missedSignals,
                滑点: `${fmt(s.avgSlippagePct, 3)}%`,
              })),
            },
            漂移趋势: {
              趋势: history.trend === "improving" ? "改善中" : history.trend === "worsening" ? "恶化中" : "稳定",
              平均漂移: history.avgDrift,
              高风险次数: history.alertCount,
            },
            建议: generateRecommendations(result, segmented, matched, history),
          };

          // Include out-of-sample analysis if live data exists after backtest period
          try {
            const bt = req.backtestResult;
            const mode2 = (args.mode as string) ?? "paper";
            const eh2 = mode2 === "live" ? equityHistoryLive : equityHistoryPaper;
            const oosEquity = eh2.getRange(bt.endDate, "9999-12-31");
            if (oosEquity.length >= 2) {
              const allTrades2 = await loadTrades(config);
              const oosTrades = allTrades2.filter((t) => t.strategyId === req.strategyId && t.entryTime > bt.endDate);
              const oos = compareOutOfSample(bt, oosEquity, oosTrades);
              report.样本外验证 = {
                样本外期间: `${oos.outOfSamplePeriod.start} ~ ${oos.outOfSamplePeriod.end}`,
                样本外年化收益: `${fmt(oos.outOfSampleReturn, 2)}%`,
                收益衰减: `${fmt(oos.returnDecay, 2)}%`,
                Sharpe衰减: fmt(oos.sharpeDecay, 2),
                判定: oos.verdict === "on_track" ? "运行正常" : oos.verdict === "degrading" ? "表现衰减" : "策略失效",
              };
            }
          } catch { /* out-of-sample data may not be available */ }

          // Include closest backtest match if multiple backtests exist
          try {
            const strategyId2 = args.strategy_id as string | undefined;
            const entries2 = backtestStore.query({
              strategyId: strategyId2 ?? req.strategyId,
              sortBy: "createdAt",
              sortDir: "desc",
              limit: 20,
            });
            if (entries2.length >= 2) {
              const bts = entries2
                .map((e) => ({ id: e.id, result: backtestStore.getFullResult(e.id)?.result }))
                .filter((b): b is { id: string; result: import("../types.js").BacktestResult } => b.result != null);
              const mode3 = (args.mode as string) ?? "paper";
              const eh3 = mode3 === "live" ? equityHistoryLive : equityHistoryPaper;
              const liveEq = eh3.getRange(req.startDate, req.endDate);
              if (liveEq.length >= 2 && bts.length >= 2) {
                const closestMatch = findClosestBacktest(bts, liveEq);
                if (closestMatch.bestMatch) {
                  report.最佳匹配回测 = {
                    回测ID: closestMatch.bestMatch.backtestId,
                    策略: closestMatch.bestMatch.strategyId,
                    相关性得分: `${closestMatch.bestMatch.correlationScore}/100`,
                    收益偏差: `${fmt(closestMatch.bestMatch.returnDiff, 2)}%`,
                  };
                }
              }
            }
          } catch { /* closest backtest may not be available */ }

          return report;
        }

        case "out_of_sample": {
          // Out-of-sample: compare live performance AFTER the backtest period
          const backtestId = args.backtest_id as string | undefined;
          const strategyId = args.strategy_id as string | undefined;
          const mode = (args.mode as string) ?? "paper";

          let btRecord;
          if (backtestId) {
            btRecord = backtestStore.getFullResult(backtestId);
          } else if (strategyId) {
            const entries = backtestStore.query({ strategyId, sortBy: "createdAt", sortDir: "desc", limit: 1 });
            if (entries.length > 0) btRecord = backtestStore.getFullResult(entries[0]!.id);
          }
          if (!btRecord) return { error: "未找到回测记录" };

          const bt = btRecord.result;
          const eh = mode === "live" ? equityHistoryLive : equityHistoryPaper;
          // Get all equity data after the backtest end date
          const allLiveEquity = eh.getRange(bt.endDate, "9999-12-31");
          if (allLiveEquity.length === 0) {
            return { error: `回测结束日期 ${bt.endDate} 之后无${mode === "live" ? "实盘" : "模拟盘"}权益数据` };
          }

          const allTrades = await loadTrades(config);
          const sid = strategyId ?? bt.strategyId;
          const oosLiveTrades = allTrades.filter((t) => t.strategyId === sid && t.entryTime > bt.endDate);

          const oos = compareOutOfSample(bt, allLiveEquity, oosLiveTrades);
          return {
            策略: oos.strategyId,
            样本内期间: `${oos.inSamplePeriod.start} ~ ${oos.inSamplePeriod.end}`,
            样本外期间: `${oos.outOfSamplePeriod.start} ~ ${oos.outOfSamplePeriod.end}`,
            样本内表现: {
              年化收益: `${fmt(oos.inSampleReturn, 2)}%`,
              Sharpe: fmt(oos.inSampleSharpe, 2),
              最大回撤: `${fmt(oos.inSampleMaxDrawdown, 2)}%`,
            },
            样本外表现: {
              年化收益: `${fmt(oos.outOfSampleReturn, 2)}%`,
              Sharpe: fmt(oos.outOfSampleSharpe, 2),
              最大回撤: `${fmt(oos.outOfSampleMaxDrawdown, 2)}%`,
            },
            衰减分析: {
              收益衰减: `${fmt(oos.returnDecay, 2)}%`,
              Sharpe衰减: fmt(oos.sharpeDecay, 2),
            },
            判定: oos.verdict === "on_track" ? "运行正常" : oos.verdict === "degrading" ? "表现衰减" : "策略失效",
            观察: oos.observations,
          };
        }

        case "closest_backtest": {
          const strategyId = args.strategy_id as string | undefined;
          const mode = (args.mode as string) ?? "paper";
          const startDate = args.start_date as string | undefined;
          const endDate = args.end_date as string | undefined;

          // Get all backtests (optionally filtered by strategy)
          const entries = backtestStore.query({
            strategyId,
            sortBy: "createdAt",
            sortDir: "desc",
            limit: 20,
          });
          if (entries.length < 2) {
            return { error: "需要至少2条回测记录才能进行多回测匹配" };
          }

          const backtests = entries
            .map((e) => ({ id: e.id, result: backtestStore.getFullResult(e.id)?.result }))
            .filter((b): b is { id: string; result: import("../types.js").BacktestResult } => b.result != null);

          const eh = mode === "live" ? equityHistoryLive : equityHistoryPaper;
          const start = startDate ?? backtests[0]!.result.startDate;
          const end = endDate ?? new Date().toISOString().slice(0, 10);
          const liveEquity = eh.getRange(start, end);

          if (liveEquity.length === 0) {
            return { error: `${mode === "live" ? "实盘" : "模拟盘"}在 ${start} ~ ${end} 期间无权益数据` };
          }

          const match = findClosestBacktest(backtests, liveEquity);
          return {
            实盘期间: `${match.livePeriod.start} ~ ${match.livePeriod.end}`,
            实盘收益: `${fmt(match.liveReturn, 2)}%`,
            候选回测数: match.candidates.length,
            最佳匹配: match.bestMatch ? {
              回测ID: match.bestMatch.backtestId,
              策略: match.bestMatch.strategyId,
              相关性得分: `${match.bestMatch.correlationScore}/100`,
              RMSE: fmt(match.bestMatch.rmse, 2),
              回测收益: `${fmt(match.bestMatch.backtestReturn, 2)}%`,
              收益偏差: `${fmt(match.bestMatch.returnDiff, 2)}%`,
              回测期间: `${match.bestMatch.backtestPeriod.start} ~ ${match.bestMatch.backtestPeriod.end}`,
            } : "无匹配",
            全部候选: match.candidates.slice(0, 5).map((c) => ({
              回测ID: c.backtestId,
              策略: c.strategyId,
              相关性: `${c.correlationScore}/100`,
              收益偏差: `${fmt(c.returnDiff, 2)}%`,
            })),
          };
        }

        case "export": {
          const resolved2 = await resolveCompareData(args);
          if ("error" in resolved2) return resolved2;
          const { req: req2 } = resolved2;
          const result2 = compareLiveVsBacktest(req2);
          const matched2 = matchTrades(req2);
          const csv = exportComparisonCsv(result2, matched2);

          // Write CSV to data directory
          const dir2 = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
          const filename = `live-backtest-compare-${req2.strategyId}-${new Date().toISOString().slice(0, 10)}.csv`;
          const filePath = path.join(dir2, filename);
          await fs.writeFile(filePath, csv, "utf-8");

          return {
            message: `对比报告已导出`,
            文件路径: filePath,
            策略: req2.strategyId,
            期间: `${req2.startDate} ~ ${req2.endDate}`,
            漂移得分: `${result2.drift.score}/100`,
            交易匹配率: `${fmt(matched2.summary.matchRate * 100, 1)}%`,
            权益曲线数据点: result2.equityCurveOverlay.points.length,
            逐笔匹配数: matched2.matches.length,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };

  /** Fire an alert via AlertStore when drift severity is high or critical. */
  function fireDriftAlert(result: LiveBacktestCompareResult): void {
    if (!alertStore) return;
    const { drift, strategyId } = result;
    if (drift.severity === "high" || drift.severity === "critical") {
      const severity = drift.severity === "critical" ? "critical" : "warning";
      alertStore.add(
        severity,
        "strategy",
        `策略 ${strategyId} 漂移${drift.severity === "critical" ? "严重" : "偏高"} (${drift.score}/100): ${drift.observations[0] ?? ""}`,
        {
          strategyId,
          driftScore: drift.score,
          driftSeverity: drift.severity,
          components: drift.components,
        },
      );
    }
  }

  // ── Internal helpers ──────────────────────────────────────────

  async function resolveCompareData(args: Record<string, unknown>) {
    const backtestId = args.backtest_id as string | undefined;
    const strategyId = args.strategy_id as string | undefined;
    const mode = (args.mode as string) ?? "paper";
    const startDate = args.start_date as string | undefined;
    const endDate = args.end_date as string | undefined;

    let backtestRecord;
    if (backtestId) {
      backtestRecord = backtestStore.getFullResult(backtestId);
    } else if (strategyId) {
      const entries = backtestStore.query({
        strategyId,
        sortBy: "createdAt",
        sortDir: "desc",
        limit: 1,
      });
      if (entries.length > 0) {
        backtestRecord = backtestStore.getFullResult(entries[0]!.id);
      }
    }

    if (!backtestRecord) {
      return {
        error: backtestId
          ? `未找到回测记录: ${backtestId}`
          : `策略 ${strategyId ?? "未指定"} 无回测记录，请先运行回测`,
      };
    }

    const bt = backtestRecord.result;
    const sid = strategyId ?? bt.strategyId;

    const eh = mode === "live" ? equityHistoryLive : equityHistoryPaper;
    const start = startDate ?? bt.startDate;
    const end = endDate ?? bt.endDate;
    const liveEquity = eh.getRange(start, end);

    if (liveEquity.length === 0) {
      return {
        error: `${mode === "live" ? "实盘" : "模拟盘"}在 ${start} ~ ${end} 期间无权益快照数据`,
        hint: "请确认交易引擎在该期间运行过",
      };
    }

    const allTrades = await loadTrades(config);
    const liveTrades = allTrades.filter(
      (t) =>
        t.strategyId === sid &&
        t.entryTime >= start &&
        t.entryTime <= end + "T23:59:59",
    );

    const allSignals = await loadSignals(config);
    const liveSignals = allSignals.filter(
      (s) =>
        s.strategyId === sid &&
        new Date(s.timestamp).toISOString().slice(0, 10) >= start &&
        new Date(s.timestamp).toISOString().slice(0, 10) <= end,
    );

    return {
      req: {
        strategyId: sid,
        startDate: start,
        endDate: end,
        backtestResult: bt,
        liveEquity,
        liveTrades,
        liveSignals,
      },
    };
  }
}

function generateRecommendations(
  result: LiveBacktestCompareResult,
  segmented: PeriodSegmentedResult,
  matched: TradeMatchResult,
  history: DriftHistory,
): string[] {
  const recs: string[] = [];

  if (result.drift.severity === "critical" || result.drift.severity === "high") {
    recs.push("漂移严重，建议暂停实盘运行并审查策略参数");
  }

  if (matched.summary.matchRate < 0.7) {
    recs.push(`信号执行率仅${(matched.summary.matchRate * 100).toFixed(0)}%，建议检查下单通道和流动性`);
  }

  if (matched.summary.avgSlippagePct > 0.3) {
    recs.push(`平均滑点${matched.summary.avgSlippagePct.toFixed(2)}%过高，建议使用限价单或优化执行时机`);
  }

  if (result.drawdownComparison.drawdownDiff > 5) {
    recs.push("实盘回撤显著超过回测预期，建议收紧止损或降低仓位");
  }

  if (history.trend === "worsening") {
    recs.push("漂移趋势恶化中，策略可能正在失效，建议重新评估市场适用性");
  }

  if (segmented.driftOnset) {
    recs.push(`漂移从${segmented.driftOnset}开始加剧，建议回查该时段市场环境变化`);
  }

  if (matched.summary.problematicSymbols.length > 0) {
    const syms = matched.summary.problematicSymbols.map((p) => p.symbol).join(", ");
    recs.push(`标的 ${syms} 信号执行率低，建议检查其流动性或交易限制`);
  }

  if (recs.length === 0) {
    recs.push("策略运行状态良好，建议继续监控并定期对比");
  }

  return recs;
}
