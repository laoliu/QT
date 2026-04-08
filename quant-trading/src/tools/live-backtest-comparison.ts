/**
 * live_backtest_comparison tool 66AI-callable tool for comparing live trading
 * performance against backtest expectations.
 *
 * Actions:
 *   compare          66run live vs backtest comparison for a strategy
 *   list_comparable  66list strategies that have both live trades and backtest results
 *   match_trades     66trade-by-trade matching between live and backtest signals
 *   rolling          66rolling window comparison over time
 *   batch_compare    66compare multiple strategies simultaneously
 *   export_report    66generate a Markdown comparison report
 *   symbol_breakdown 66per-symbol breakdown of live vs backtest differences
 *   execution_quality 66execution quality analysis (slippage, timing, holding, profit factor)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BacktestResult, QuantTradingConfig, TradeRecord } from "../types.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { AlertStore } from "../services/alert-store.js";
import { LiveBacktestComparison } from "../services/live-backtest-comparison.js";
import { runBacktest } from "../engine/strategy-registry.js";
import { runPythonScript } from "../bridge/python-bridge.js";

/** Safe toFixed 66returns "N/A? when the value is nullish or not a finite number. */
function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

export function createLiveBacktestComparisonTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  equityHistory: EquityHistory,
  backtestStore?: BacktestStore,
  alertStore?: AlertStore,
) {
  const comparison = new LiveBacktestComparison(equityHistory, backtestStore);

  return {
    name: "live_backtest_comparison",
    description:
      "08060705100011043205010810/00080606" +
      "action=compare 010302, action=list_comparable 0503030004, " +
      "action=match_trades 0405, action=rolling 1203, " +
      "action=batch_compare 多策略批量对比, action=export_report 导出Markdown报告, " +
      "action=symbol_breakdown 0406030208010701, " +
      "action=execution_quality 040205000402/盈亏毀夏普比率? " +
      "action=regime_comparison 050609030200/熊市/26000906vs1001? " +
      "action=summary_dashboard 000505010706vs100800" +
      "0803060711030801",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "list_comparable", "match_trades", "rolling", "batch_compare", "export_report", "symbol_breakdown", "execution_quality", "regime_comparison", "summary_dashboard"],
          description: "操作类型",
        },
        strategy_id: {
          type: "string",
          description: "策略ID (jdual-ma-crossover)",
        },
        strategy_ids: {
          type: "string",
          description: "多个策略ID0908 (用于 batch_compare)",
        },
        symbols: {
          type: "string",
          description: "06030908 (j600519,000858)",
        },
        start_date: {
          type: "string",
          description: "0303 YYYY-MM-DD",
        },
        end_date: {
          type: "string",
          description: "0308 YYYY-MM-DD",
        },
        backtest_id: {
          type: "string",
          description: "081004ID（可选，不填则自动匹配最近的回测结果",
        },
        rerun_backtest: {
          type: "boolean",
          description: "09111012030208false001110",
        },
        rolling_days: {
          type: "number",
          description: "滚动窗口天数（默讀0，用了rolling 操作",
        },
        step_days: {
          type: "number",
          description: "滚动步进天数（默讀，用了rolling 操作",
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
                  : "无数",
              backtestCount: btCandidates.length,
              latestBacktestId: btCandidates[0]?.id ?? null,
            });
          }

          return {
            total: comparable.length,
            strategies: comparable,
            hint:
              comparable.length === 0 ? "17030313080402060706040910170103" : "00 action=compare 08strategy_id 060302",
          };
        }

        case "compare": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const result = await runCompare(
            comparison, api, config, getEngine, strategyId, args,
          );
          if ("error" in result) return result;

          // Fire drift alert if warranted
          if (alertStore && (result.verdict === "significant_drift" || result.verdict === "critical_divergence")) {
            const severity = result.verdict === "critical_divergence" ? "critical": "warning";
            alertStore.add(
              severity,
              "strategy_drift",
              `策略 ${strategyId} 实盘与回测出率{result.verdict === "critical_divergence" ? "严重" : "显著"}01: ${result.verdictDescription}`,
              { strategyId, verdict: result.verdict, period: result.period },
            );
          }

          return formatCompareResult(result);
        }

        case "match_trades": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const symbols = parseSymbols(args.symbols as string | undefined);
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);
          const backtestId = args.backtest_id as string | undefined;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const stratTrades = allTrades.filter(
            (t) =>
              t.strategyId === strategyId &&
              t.entryTime.slice(0, 10) >= startDate &&
              t.entryTime.slice(0, 10) <= endDate,
          );

          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          if (compareSymbols.length === 0) {
            return { error: `策略 ${strategyId} 在${startDate} ~ ${endDate} 18150604` };
          }

          let btResult = await getBacktestResult(
            backtestStore, backtestId, strategyId, compareSymbols, startDate, endDate, rerunBacktest, config, api,
          );

          const matchResult = comparison.matchTrades(
            { strategyId, symbols: compareSymbols, startDate, endDate },
            stratTrades,
            btResult ?? undefined,
          );

          return {
            strategyId: matchResult.strategyId,
            period: matchResult.period,
            summary: {
              "0607": matchResult.summary.totalLiveTrades,
              "1002": matchResult.summary.totalBacktestSignals,
              "0205": matchResult.summary.exactMatches,
              "近似匹配": matchResult.summary.approximateMatches,
              "未匹配实盘": matchResult.summary.unmatchedLiveTrades,
              "0802": matchResult.summary.missedSignals,
              "090201": `${fmt(matchResult.summary.avgTimingDeltaDays, 1)}天`,
              "091801": `${fmt(matchResult.summary.avgPriceDeltaPct, 2)}%`,
            },
            matches: matchResult.matches.slice(0, 20).map((m) => ({
              "交易ID": m.liveTrade.tradeId,
              "标的": m.liveTrade.symbol,
              "匹配类型": m.matchType === "exact" ? "精确" : m.matchType === "approximate" ? "近似" : "未匹配",
              "时间差": m.timingDeltaDays,
              "1801": `${fmt(m.priceDeltaPct, 2)}%`,
              "滑点影响": `${fmt(m.slippageImpact, 3)}%`,
              "1002": m.backtestSignal ? new Date(m.backtestSignal.timestamp).toISOString().slice(0, 10) : "鏃",
              "10020418": m.backtestSignal?.price ?? "鏃",
            })),
            missedSignals: matchResult.missedSignals.slice(0, 10).map((s) => ({
              "日期": new Date(s.timestamp).toISOString().slice(0, 10),
              "标的": s.symbol,
              "方向": s.direction,
              18: s.price,
            })),
          };
        }

        case "rolling": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const symbols = parseSymbols(args.symbols as string | undefined);
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const rollingDays = (args.rolling_days as number) ?? 30;
          const stepDays = (args.step_days as number) ?? 7;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const stratTrades = allTrades.filter((t) => t.strategyId === strategyId);
          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          let btResult: BacktestResult | undefined;
          if (rerunBacktest && compareSymbols.length > 0) {
            const lookbackStart = new Date(endDate);
            lookbackStart.setDate(lookbackStart.getDate() - rollingDays * 6);
            try {
              btResult = await runBacktest(
                { strategyId, symbols: compareSymbols, startDate: lookbackStart.toISOString().slice(0, 10), endDate, initialCash: 100_000 },
                config,
              );
            } catch (err) {
              api.logger.warn(`Re-run backtest for rolling comparison failed: ${(err as Error).message}`);
            }
          }

          const result = comparison.rollingComparison(
            strategyId, compareSymbols, endDate, stratTrades, btResult, rollingDays, stepDays,
          );

          const verdictLabels: Record<string, string> = {
            consistent: "05",
            minor_drift: "0101",
            significant_drift: "显著偏离",
            critical_divergence: "严重偏离",
          };
          const trendLabels: Record<string, string> = {
            improving: "94 改善",
            stable: "7915 01",
            deteriorating: "95 恶化",
          };

          return {
            strategyId: result.strategyId,
            symbols: result.symbols,
            rollingDays: result.rollingDays,
            trend: trendLabels[result.trend] ?? result.trend,
            trendDescription: result.trendDescription,
            points: result.points.map((p) => ({
              "窗口": `${p.windowStart} ~ ${p.date}`,
              "评估": verdictLabels[p.verdict] ?? p.verdict,
              "收益偏差": `${p.returnDelta > 0 ? "+" : ""}${fmt(p.returnDelta, 2)}%`,
              "1101": `${p.drawdownDelta > 0 ? "+" : ""}${fmt(p.drawdownDelta, 2)}%`,
              "0901": `${p.winRateDelta > 0 ? "+" : ""}${fmt(p.winRateDelta, 1)}%`,
              "实盘交易": p.liveTrades,
            })),
          };
        }

        case "batch_compare": {
          const strategyIdsStr = (args.strategy_ids as string) ?? (args.strategy_id as string);
          if (!strategyIdsStr) return { error: "请提供strategy_ids090804ID锛" };

          const strategyIds = strategyIdsStr.split(",").map((s) => s.trim()).filter(Boolean);
          if (strategyIds.length < 2) return { error: "批量对比至少需要个策略ID" };

          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const strategies = strategyIds.map((sid) => {
            const trades = allTrades.filter(
              (t) =>
                t.strategyId === sid &&
                t.entryTime.slice(0, 10) >= startDate &&
                t.entryTime.slice(0, 10) <= endDate,
            );
            const symbols = [...new Set(trades.map((t) => t.symbol))];
            return { strategyId: sid, symbols, trades };
          });

          const result = comparison.batchCompare(strategies, startDate, endDate);

          const verdictLabels: Record<string, string> = {
            consistent: "08 05",
            minor_drift: "07 0101",
            significant_drift: "06 显著偏离",
            critical_divergence: "92 严重偏离",
          };

          return {
            period: `${startDate} ~ ${endDate}`,
            strategyCount: result.entries.length,
            ranking: result.ranking.map((r, i) => ({
              "排名": i + 1,
              "策略": r.strategyId,
              "评估": verdictLabels[r.verdict] ?? r.verdict,
              "收益偏差": `${r.returnDelta > 0 ? "+" : ""}${fmt(r.returnDelta, 2)}%`,
              "1101": `${r.drawdownDelta > 0 ? "+" : ""}${fmt(r.drawdownDelta, 2)}%`,
              "评分": fmt(r.score, 2),
            })),
            hint: "0105090506100505",
          };
        }

        case "export_report": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const result = await runCompare(
            comparison, api, config, getEngine, strategyId, args,
          );
          if ("error" in result) return result;

          const report = comparison.generateReport(result);
          return {
            format: "markdown",
            report,
            hint: "0501C05.md 文件或通过消息渠道分享",
          };
        }

        case "symbol_breakdown": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const symbols = parseSymbols(args.symbols as string | undefined);
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);
          const backtestId = args.backtest_id as string | undefined;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const stratTrades = allTrades.filter(
            (t) =>
              t.strategyId === strategyId &&
              t.entryTime.slice(0, 10) >= startDate &&
              t.entryTime.slice(0, 10) <= endDate,
          );

          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          if (compareSymbols.length === 0) {
            return { error: `策略 ${strategyId} 在${startDate} ~ ${endDate} 18150604` };
          }

          let btResult = await getBacktestResult(
            backtestStore, backtestId, strategyId, compareSymbols, startDate, endDate, rerunBacktest, config, api,
          );

          const result = comparison.symbolBreakdown(
            { strategyId, symbols: compareSymbols, startDate, endDate },
            stratTrades,
            btResult ?? undefined,
          );

          const verdictLabels: Record<string, string> = {
            consistent: "08 05",
            minor_drift: "07 0101",
            significant_drift: "06 显著偏离",
            critical_divergence: "92 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,
            worstSymbol: result.worstSymbol,
            bestSymbol: result.bestSymbol,
            symbols: result.symbols.map((s) => ({
              "标的": s.symbol,
              "0607": s.liveTrades,
              "1095": s.backtestTrades,
              "实盘收益": `${fmt(s.liveReturn, 2)}%`,
              "回测收益": `${fmt(s.backtestReturn, 2)}%`,
              "收益偏差": `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              "0609": `${fmt(s.liveWinRate, 1)}%`,
              "1009": `${fmt(s.backtestWinRate, 1)}%`,
              "0901": `${s.winRateDelta > 0 ? "+" : ""}${fmt(s.winRateDelta, 1)}%`,
              "060702": fmt(s.avgHoldingDaysLive, 1),
              "1002": fmt(s.avgHoldingDaysBacktest, 1),
              "评估": verdictLabels[s.verdict] ?? s.verdict,
            })),
            hint: result.worstSymbol
              ? `标的 ${result.worstSymbol} 018313090704`
              : "各标的表现与回测基本一",
          };
        }

        case "execution_quality": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const symbols = parseSymbols(args.symbols as string | undefined);
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);
          const backtestId = args.backtest_id as string | undefined;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const stratTrades = allTrades.filter(
            (t) =>
              t.strategyId === strategyId &&
              t.entryTime.slice(0, 10) >= startDate &&
              t.entryTime.slice(0, 10) <= endDate,
          );

          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          if (compareSymbols.length === 0) {
            return { error: `策略 ${strategyId} 在${startDate} ~ ${endDate} 18150604` };
          }

          let btResult = await getBacktestResult(
            backtestStore, backtestId, strategyId, compareSymbols, startDate, endDate, rerunBacktest, config, api,
          );

          const result = comparison.executionQuality(
            { strategyId, symbols: compareSymbols, startDate, endDate },
            stratTrades,
            btResult ?? undefined,
          );

          const verdictLabels: Record<string, string> = {
            consistent: "08 05",
            minor_drift: "07 0101",
            significant_drift: "06 显著偏离",
            critical_divergence: "92 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,

            "信号执行": {
              "成交率": `${fmt(result.signalFillRate, 1)}%`,
              "平均时间": `${fmt(result.avgSignalToFillMinutes, 0)}分钟`,
            },

            "滑点分析": {
              "平均滑点": `${fmt(result.avgSlippagePct, 3)}%`,
              "中位数滑点": `${fmt(result.medianSlippagePct, 3)}%`,
              "最大滑点": `${fmt(result.maxSlippagePct, 3)}%`,
            },

            "持仓周期": {
              "060902": fmt(result.avgHoldingDaysLive, 1),
              "100902": fmt(result.avgHoldingDaysBacktest, 1),
              "0201": `${result.holdingDaysDelta > 0 ? "+" : ""}${fmt(result.holdingDaysDelta, 1)}`,
            },

            "0108": {
              "实盘利润因子": fmt(result.liveProfitFactor, 2),
              "回测利润因子": fmt(result.backtestProfitFactor, 2),
              "0607": fmt(result.liveSharpe, 2),
              "1007": fmt(result.backtestSharpe, 2),
              "实盘索提诺比率": fmt(result.liveSortino, 2),
              "回测索提诺比率": fmt(result.backtestSortino, 2),
            },

            "标的明细": result.symbolBreakdowns.map((s) => ({
              "标的": s.symbol,
              "0607": s.liveTrades,
              "收益偏差": `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              "评估": verdictLabels[s.verdict] ?? s.verdict,
            })),

            "诊断建议": result.diagnostics,
          };
        }

        case "regime_comparison": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供strategy_id" };

          const symbols = parseSymbols(args.symbols as string | undefined);
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);
          const backtestId = args.backtest_id as string | undefined;
          const rerunBacktest = args.rerun_backtest as boolean | undefined;

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          const stratTrades = allTrades.filter(
            (t) =>
              t.strategyId === strategyId &&
              t.entryTime.slice(0, 10) >= startDate &&
              t.entryTime.slice(0, 10) <= endDate,
          );

          const compareSymbols =
            symbols.length > 0
              ? symbols
              : [...new Set(stratTrades.map((t) => t.symbol))];

          if (compareSymbols.length === 0) {
            return { error: `策略 ${strategyId} 在${startDate} ~ ${endDate} 18150604` };
          }

          let btResult = await getBacktestResult(
            backtestStore, backtestId, strategyId, compareSymbols, startDate, endDate, rerunBacktest, config, api,
          );

          // Fetch regime timeline from Python strategy_router
          let regimeTimeline: Array<{ regime: string; startDate: string; endDate: string }> = [];
          try {
            const result = await runPythonScript<{
              regimes?: Array<{ regime: string; start_date: string; end_date: string }>;
            }>(
              "engines/strategy_router.py",
              { action: "get_regime_timeline", start_date: startDate, end_date: endDate },
              config,
            );
            if (result.ok && result.data?.regimes) {
              regimeTimeline = result.data.regimes.map((r) => ({
                regime: r.regime,
                startDate: r.start_date,
                endDate: r.end_date,
              }));
            }
          } catch {
            // Fallback: treat entire period as single "unknown" regime
          }

          // If no regime data, create a simple fallback based on market performance
          if (regimeTimeline.length === 0) {
            regimeTimeline = [{ regime: "unknown", startDate, endDate }];
          }

          const result = comparison.regimeComparison(
            { strategyId, symbols: compareSymbols, startDate, endDate },
            stratTrades,
            regimeTimeline,
            btResult ?? undefined,
          );

          const verdictLabels: Record<string, string> = {
            consistent: "08 05",
            minor_drift: "07 0101",
            significant_drift: "06 显著偏离",
            critical_divergence: "92 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,
            bestRegime: result.bestRegime,
            worstRegime: result.worstRegime,
            segments: result.segments.map((s) => ({
              "市场环境": s.regime,
              "日期": `${s.startDate} ~ ${s.endDate}`,
              "交易日": s.tradingDays,
              "0607": s.liveTrades,
              "1095": s.backtestTrades,
              "实盘收益": `${fmt(s.liveReturn, 2)}%`,
              "回测收益": `${fmt(s.backtestReturn, 2)}%`,
              "收益偏差": `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              "0609": `${fmt(s.liveWinRate, 1)}%`,
              "1009": `${fmt(s.backtestWinRate, 1)}%`,
              "评估": verdictLabels[s.verdict] ?? s.verdict,
            })),
            diagnostics: result.diagnostics,
            hint: result.worstRegime && result.worstRegime !== result.bestRegime
              ? `策略在${result.worstRegime}"环境下偏离最大，在${result.bestRegime}"0503180500080507`
              : "05050305",
          };
        }

        case "summary_dashboard": {
          const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
          const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);

          const engine = getEngine();
          const allTrades = engine?.orderManager
            ? await engine.orderManager.getTrades()
            : [];

          // Group trades by strategy
          const strategyTrades = new Map<string, TradeRecord[]>();
          for (const t of allTrades) {
            const sid = t.strategyId ?? "manual";
            if (!strategyTrades.has(sid)) strategyTrades.set(sid, []);
            strategyTrades.get(sid)!.push(t);
          }

          // Also include strategies from the live engine that may have no trades yet
          try {
            const registry = engine?.strategyRegistry;
            const liveStrategies = registry?.list?.() ?? [];
            for (const ls of liveStrategies) {
              const sid = ls.strategyId;
              if (sid && !strategyTrades.has(sid)) {
                strategyTrades.set(sid, []);
              }
            }
          } catch {
            // registry not available
          }

          const strategies = [...strategyTrades.entries()].map(([sid, trades]) => {
            const symbols = [...new Set(trades.map((t) => t.symbol))];
            return { strategyId: sid, symbols, trades };
          });

          const result = comparison.summaryDashboard(strategies, startDate, endDate);

          const verdictLabels: Record<string, string> = {
            consistent: "08 05",
            minor_drift: "07 0101",
            significant_drift: "06 显著偏离",
            critical_divergence: "92 严重偏离",
          };

          return {
            period: `${startDate} ~ ${endDate}`,
            totalStrategies: result.totalStrategies,
            healthDistribution: {
              "08 05": result.healthDistribution.consistent,
              "07 0101": result.healthDistribution.minor_drift,
              "06 显著偏离": result.healthDistribution.significant_drift,
              "92 严重偏离": result.healthDistribution.critical_divergence,
              "⚠️ 无数据": result.healthDistribution.no_data,
            },
            strategies: result.strategies.map((s) => ({
              "策略": s.strategyId,
              "0607": s.liveTradeCount,
              "0618": s.livePeriod,
              "评估": s.score < 0 ? "⚠️ 无数据" : (verdictLabels[s.verdict] ?? s.verdict),
              "收益偏差": s.score < 0 ? "N/A" : `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              "回撤偏差": s.score < 0 ? "N/A" : `${s.drawdownDelta > 0 ? "+" : ""}${fmt(s.drawdownDelta, 2)}%`,
              "胜率偏差": s.score < 0 ? "N/A" : `${s.winRateDelta > 0 ? "+" : ""}${fmt(s.winRateDelta, 1)}%`,
              "有回测": s.hasBacktest ? "是" : "否",
            })),
            alerts: result.alerts,
            hint: "00 action=compare 08strategy_id 09510603",
          };
        }

        default:
          return { error: `ā04: ${action}` };
      }
    },
  };
}

// ── Shared helpers ──────────────────────────────────────────

function parseSymbols(symbolsStr: string | undefined): string[] {
  return (symbolsStr ?? "").split(",").map((s) => s.trim()).filter(Boolean);
}

function getDefaultStartDate(endDate: string): string {
  const d = new Date(endDate);
  d.setMonth(d.getMonth() - 3);
  return d.toISOString().slice(0, 10);
}

async function getBacktestResult(
  backtestStore: BacktestStore | undefined,
  backtestId: string | undefined,
  strategyId: string,
  symbols: string[],
  startDate: string,
  endDate: string,
  rerunBacktest: boolean | undefined,
  config: QuantTradingConfig,
  api: OpenClawPluginApi,
): Promise<BacktestResult | null> {
  if (rerunBacktest) {
    try {
      return await runBacktest(
        { strategyId, symbols, startDate, endDate, initialCash: 100_000 },
        config,
      );
    } catch (err) {
      api.logger.warn(`Re-run backtest failed: ${(err as Error).message}`);
    }
  }
  if (backtestStore && backtestId) {
    const record = backtestStore.getFullResult(backtestId);
    if (record) return record.result;
  }
  return null;
}

async function runCompare(
  comparison: LiveBacktestComparison,
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  strategyId: string,
  args: Record<string, unknown>,
) {
  const symbols = parseSymbols(args.symbols as string | undefined);
  const endDate = (args.end_date as string) ?? new Date().toISOString().slice(0, 10);
  const startDate = (args.start_date as string) ?? getDefaultStartDate(endDate);
  const backtestId = args.backtest_id as string | undefined;
  const rerunBacktest = args.rerun_backtest as boolean | undefined;

  const engine = getEngine();
  const allTrades = engine?.orderManager
    ? await engine.orderManager.getTrades()
    : [];

  const stratTrades = allTrades.filter(
    (t) =>
      t.strategyId === strategyId &&
      t.entryTime.slice(0, 10) >= startDate &&
      t.entryTime.slice(0, 10) <= endDate,
  );

  const compareSymbols =
    symbols.length > 0
      ? symbols
      : [...new Set(stratTrades.map((t) => t.symbol))];

  if (compareSymbols.length === 0) {
    return {
      error: `策略 ${strategyId} 在${startDate} ~ ${endDate} 18150604symbols 参数或检查日期范围`,
    };
  }

  let freshBacktestResult: BacktestResult | undefined;
  if (rerunBacktest) {
    try {
      freshBacktestResult = await runBacktest(
        { strategyId, symbols: compareSymbols, startDate, endDate, initialCash: 100_000 },
        config,
      );
    } catch (err) {
      api.logger.warn(`Re-run backtest failed: ${(err as Error).message}`);
    }
  }

  return comparison.compare(
    { strategyId, symbols: compareSymbols, startDate, endDate, backtestId },
    stratTrades,
    freshBacktestResult,
  );
}

function formatCompareResult(result: import("../services/live-backtest-comparison.js").LiveVsBacktestResult) {
  return {
    strategyId: result.strategyId,
    symbols: result.symbols,
    period: result.period,
    backtestId: result.backtestId ?? "无，可通过 rerun_backtest=true 重新回测",

    verdict: result.verdict,
    verdictDescription: result.verdictDescription,

    metricsComparison: result.metrics.map((m) => ({
      "指标": m.label,
      "回测": `${fmt(m.backtest, 2)}${m.unit}`,
      "实盘": `${fmt(m.live, 2)}${m.unit}`,
      "差异": `${m.delta > 0 ? "+" : ""}${fmt(m.delta, 2)}${m.unit}`,
      "偏差%": `${m.deltaPct > 0 ? "+" : ""}${fmt(m.deltaPct, 1)}%`,
      "״̬": m.degraded ? "⚠️ 退化" : "✅ 正常",
    })),

    gaps: result.gaps.map((g) => ({
      "类别": g.category,
      "描述": g.description,
      "严重程度": g.severity === "critical" ? "🚨 严重" :
               g.severity === "warning" ? "⚠️ 警告" : "✅ 正常",
    })),

    liveStats: {
      "交易次数": result.liveStats.totalTrades,
      "胜率": `${fmt(result.liveStats.winRate, 1)}%`,
      "收益": `${fmt(result.liveStats.totalReturn, 2)}%`,
      "最大回撤": `${fmt(result.liveStats.maxDrawdown, 2)}%`,
      "平均滑点": `${fmt(result.liveStats.avgSlippage, 3)}%`,
    },

    backtestStats: {
      "交易次数": result.backtestStats.totalTrades,
      "胜率": `${fmt(result.backtestStats.winRate, 1)}%`,
      "收益": `${fmt(result.backtestStats.totalReturn, 2)}%`,
      "最大回撤": `${fmt(result.backtestStats.maxDrawdown, 2)}%`,
      "建模滑点": `${fmt(result.backtestStats.modeledSlippage, 3)}%`,
    },

    equityCurvePoints: result.equityCurve.length,
  };
}
