/**
 * live_backtest_comparison tool — AI-callable tool for comparing live trading
 * performance against backtest expectations.
 *
 * Actions:
 *   compare          — run live vs backtest comparison for a strategy
 *   list_comparable  — list strategies that have both live trades and backtest results
 *   match_trades     — trade-by-trade matching between live and backtest signals
 *   rolling          — rolling window comparison over time
 *   batch_compare    — compare multiple strategies simultaneously
 *   export_report    — generate a Markdown comparison report
 *   symbol_breakdown — per-symbol breakdown of live vs backtest differences
 *   execution_quality — execution quality analysis (slippage, timing, holding, profit factor)
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
  alertStore?: AlertStore,
) {
  const comparison = new LiveBacktestComparison(equityHistory, backtestStore);

  return {
    name: "live_backtest_comparison",
    description:
      "比较实盘交易表现与回测预期的差异。检测策略漂移、验证回测假设（滑点/成交率）、量化回测-实盘差距。" +
      "action=compare 运行对比分析, action=list_comparable 列出可对比的策略, " +
      "action=match_trades 逐笔交易匹配, action=rolling 滚动窗口对比, " +
      "action=batch_compare 多策略批量对比, action=export_report 导出Markdown报告, " +
      "action=symbol_breakdown 按标的拆分对比（定位哪只股票偏离最大）, " +
      "action=execution_quality 执行质量分析（滑点分布/信号成交率/持仓周期/盈亏比/夏普比率）, " +
      "action=regime_comparison 按市场环境分段对比（牛市/熊市/震荡各阶段的实盘vs回测偏离）, " +
      "action=summary_dashboard 全策略健康总览（一览所有策略的实盘vs回测状态）。" +
      "返回指标对比表、权益曲线叠加、差异诊断和健康评估。",
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
          description: "策略ID (如 dual-ma-crossover)",
        },
        strategy_ids: {
          type: "string",
          description: "多个策略ID，逗号分隔 (用于 batch_compare)",
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
        rolling_days: {
          type: "number",
          description: "滚动窗口天数（默认30，用于 rolling 操作）",
        },
        step_days: {
          type: "number",
          description: "滚动步进天数（默认7，用于 rolling 操作）",
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

          const result = await runCompare(
            comparison, api, config, getEngine, strategyId, args,
          );
          if ("error" in result) return result;

          // Fire drift alert if warranted
          if (alertStore && (result.verdict === "significant_drift" || result.verdict === "critical_divergence")) {
            const severity = result.verdict === "critical_divergence" ? "critical" : "warning";
            alertStore.add(
              severity,
              "strategy_drift",
              `策略 ${strategyId} 实盘与回测出现${result.verdict === "critical_divergence" ? "严重" : "显著"}偏离: ${result.verdictDescription}`,
              { strategyId, verdict: result.verdict, period: result.period },
            );
          }

          return formatCompareResult(result);
        }

        case "match_trades": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

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
            return { error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录` };
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
              实盘交易: matchResult.summary.totalLiveTrades,
              回测信号: matchResult.summary.totalBacktestSignals,
              精确匹配: matchResult.summary.exactMatches,
              近似匹配: matchResult.summary.approximateMatches,
              未匹配实盘交易: matchResult.summary.unmatchedLiveTrades,
              遗漏信号: matchResult.summary.missedSignals,
              平均时间偏差: `${fmt(matchResult.summary.avgTimingDeltaDays, 1)}天`,
              平均价格偏差: `${fmt(matchResult.summary.avgPriceDeltaPct, 2)}%`,
            },
            matches: matchResult.matches.slice(0, 20).map((m) => ({
              交易ID: m.liveTrade.tradeId,
              标的: m.liveTrade.symbol,
              匹配类型: m.matchType === "exact" ? "精确" : m.matchType === "approximate" ? "近似" : "未匹配",
              时间偏差天: m.timingDeltaDays,
              价格偏差: `${fmt(m.priceDeltaPct, 2)}%`,
              滑点影响: `${fmt(m.slippageImpact, 3)}%`,
              回测信号日期: m.backtestSignal ? new Date(m.backtestSignal.timestamp).toISOString().slice(0, 10) : "无",
              回测信号价格: m.backtestSignal?.price ?? "无",
            })),
            missedSignals: matchResult.missedSignals.slice(0, 10).map((s) => ({
              日期: new Date(s.timestamp).toISOString().slice(0, 10),
              标的: s.symbol,
              方向: s.direction,
              价格: s.price,
            })),
          };
        }

        case "rolling": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

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
            consistent: "一致",
            minor_drift: "轻微偏离",
            significant_drift: "显著偏离",
            critical_divergence: "严重偏离",
          };
          const trendLabels: Record<string, string> = {
            improving: "📈 改善",
            stable: "➡️ 稳定",
            deteriorating: "📉 恶化",
          };

          return {
            strategyId: result.strategyId,
            symbols: result.symbols,
            rollingDays: result.rollingDays,
            trend: trendLabels[result.trend] ?? result.trend,
            trendDescription: result.trendDescription,
            points: result.points.map((p) => ({
              窗口: `${p.windowStart} ~ ${p.date}`,
              评估: verdictLabels[p.verdict] ?? p.verdict,
              收益偏差: `${p.returnDelta > 0 ? "+" : ""}${fmt(p.returnDelta, 2)}%`,
              回撤偏差: `${p.drawdownDelta > 0 ? "+" : ""}${fmt(p.drawdownDelta, 2)}%`,
              胜率偏差: `${p.winRateDelta > 0 ? "+" : ""}${fmt(p.winRateDelta, 1)}%`,
              实盘交易数: p.liveTrades,
            })),
          };
        }

        case "batch_compare": {
          const strategyIdsStr = (args.strategy_ids as string) ?? (args.strategy_id as string);
          if (!strategyIdsStr) return { error: "请提供 strategy_ids（逗号分隔的策略ID）" };

          const strategyIds = strategyIdsStr.split(",").map((s) => s.trim()).filter(Boolean);
          if (strategyIds.length < 2) return { error: "批量对比至少需要2个策略ID" };

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
            consistent: "🟢 一致",
            minor_drift: "🟡 轻微偏离",
            significant_drift: "🟠 显著偏离",
            critical_divergence: "🔴 严重偏离",
          };

          return {
            period: `${startDate} ~ ${endDate}`,
            strategyCount: result.entries.length,
            ranking: result.ranking.map((r, i) => ({
              排名: i + 1,
              策略: r.strategyId,
              评估: verdictLabels[r.verdict] ?? r.verdict,
              收益偏差: `${r.returnDelta > 0 ? "+" : ""}${fmt(r.returnDelta, 2)}%`,
              回撤偏差: `${r.drawdownDelta > 0 ? "+" : ""}${fmt(r.drawdownDelta, 2)}%`,
              一致性得分: fmt(r.score, 2),
            })),
            hint: "得分越低表示实盘与回测一致性越好",
          };
        }

        case "export_report": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

          const result = await runCompare(
            comparison, api, config, getEngine, strategyId, args,
          );
          if ("error" in result) return result;

          const report = comparison.generateReport(result);
          return {
            format: "markdown",
            report,
            hint: "可以将此报告保存为 .md 文件或通过消息渠道分享",
          };
        }

        case "symbol_breakdown": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

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
            return { error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录` };
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
            consistent: "🟢 一致",
            minor_drift: "🟡 轻微偏离",
            significant_drift: "🟠 显著偏离",
            critical_divergence: "🔴 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,
            worstSymbol: result.worstSymbol,
            bestSymbol: result.bestSymbol,
            symbols: result.symbols.map((s) => ({
              标的: s.symbol,
              实盘交易: s.liveTrades,
              回测交易: s.backtestTrades,
              实盘收益: `${fmt(s.liveReturn, 2)}%`,
              回测收益: `${fmt(s.backtestReturn, 2)}%`,
              收益偏差: `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              实盘胜率: `${fmt(s.liveWinRate, 1)}%`,
              回测胜率: `${fmt(s.backtestWinRate, 1)}%`,
              胜率偏差: `${s.winRateDelta > 0 ? "+" : ""}${fmt(s.winRateDelta, 1)}%`,
              实盘持仓天数: fmt(s.avgHoldingDaysLive, 1),
              回测持仓天数: fmt(s.avgHoldingDaysBacktest, 1),
              评估: verdictLabels[s.verdict] ?? s.verdict,
            })),
            hint: result.worstSymbol
              ? `标的 ${result.worstSymbol} 偏离最大，建议重点检查该标的的执行情况`
              : "各标的表现与回测基本一致",
          };
        }

        case "execution_quality": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

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
            return { error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录` };
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
            consistent: "🟢 一致",
            minor_drift: "🟡 轻微偏离",
            significant_drift: "🟠 显著偏离",
            critical_divergence: "🔴 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,

            执行效率: {
              信号成交率: `${fmt(result.signalFillRate, 1)}%`,
              平均信号到成交延迟: `${fmt(result.avgSignalToFillMinutes, 0)}分钟`,
            },

            滑点分析: {
              平均滑点: `${fmt(result.avgSlippagePct, 3)}%`,
              中位数滑点: `${fmt(result.medianSlippagePct, 3)}%`,
              最大单笔滑点: `${fmt(result.maxSlippagePct, 3)}%`,
            },

            持仓周期: {
              实盘平均持仓天数: fmt(result.avgHoldingDaysLive, 1),
              回测平均持仓天数: fmt(result.avgHoldingDaysBacktest, 1),
              持仓偏差天数: `${result.holdingDaysDelta > 0 ? "+" : ""}${fmt(result.holdingDaysDelta, 1)}`,
            },

            风险调整指标: {
              实盘盈亏比: fmt(result.liveProfitFactor, 2),
              回测盈亏比: fmt(result.backtestProfitFactor, 2),
              实盘夏普比率: fmt(result.liveSharpe, 2),
              回测夏普比率: fmt(result.backtestSharpe, 2),
              实盘索提诺比率: fmt(result.liveSortino, 2),
              回测索提诺比率: fmt(result.backtestSortino, 2),
            },

            标的明细: result.symbolBreakdowns.map((s) => ({
              标的: s.symbol,
              实盘交易: s.liveTrades,
              收益偏差: `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              评估: verdictLabels[s.verdict] ?? s.verdict,
            })),

            诊断建议: result.diagnostics,
          };
        }

        case "regime_comparison": {
          const strategyId = args.strategy_id as string;
          if (!strategyId) return { error: "请提供 strategy_id" };

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
            return { error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录` };
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
            consistent: "🟢 一致",
            minor_drift: "🟡 轻微偏离",
            significant_drift: "🟠 显著偏离",
            critical_divergence: "🔴 严重偏离",
          };

          return {
            strategyId: result.strategyId,
            period: result.period,
            bestRegime: result.bestRegime,
            worstRegime: result.worstRegime,
            segments: result.segments.map((s) => ({
              市场环境: s.regime,
              期间: `${s.startDate} ~ ${s.endDate}`,
              交易日: s.tradingDays,
              实盘交易: s.liveTrades,
              回测交易: s.backtestTrades,
              实盘收益: `${fmt(s.liveReturn, 2)}%`,
              回测收益: `${fmt(s.backtestReturn, 2)}%`,
              收益偏差: `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              实盘胜率: `${fmt(s.liveWinRate, 1)}%`,
              回测胜率: `${fmt(s.backtestWinRate, 1)}%`,
              评估: verdictLabels[s.verdict] ?? s.verdict,
            })),
            diagnostics: result.diagnostics,
            hint: result.worstRegime && result.worstRegime !== result.bestRegime
              ? `策略在"${result.worstRegime}"环境下偏离最大，在"${result.bestRegime}"环境下最一致。建议在不利行情下降低仓位或暂停策略。`
              : "各市场环境下策略表现一致",
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
            consistent: "🟢 一致",
            minor_drift: "🟡 轻微偏离",
            significant_drift: "🟠 显著偏离",
            critical_divergence: "🔴 严重偏离",
          };

          return {
            period: `${startDate} ~ ${endDate}`,
            totalStrategies: result.totalStrategies,
            healthDistribution: {
              "🟢 一致": result.healthDistribution.consistent,
              "🟡 轻微偏离": result.healthDistribution.minor_drift,
              "🟠 显著偏离": result.healthDistribution.significant_drift,
              "🔴 严重偏离": result.healthDistribution.critical_divergence,
              "⚪ 无数据": result.healthDistribution.no_data,
            },
            strategies: result.strategies.map((s) => ({
              策略: s.strategyId,
              实盘交易: s.liveTradeCount,
              实盘期间: s.livePeriod,
              评估: s.score < 0 ? "⚪ 无数据" : (verdictLabels[s.verdict] ?? s.verdict),
              收益偏差: s.score < 0 ? "N/A" : `${s.returnDelta > 0 ? "+" : ""}${fmt(s.returnDelta, 2)}%`,
              回撤偏差: s.score < 0 ? "N/A" : `${s.drawdownDelta > 0 ? "+" : ""}${fmt(s.drawdownDelta, 2)}%`,
              胜率偏差: s.score < 0 ? "N/A" : `${s.winRateDelta > 0 ? "+" : ""}${fmt(s.winRateDelta, 1)}%`,
              有回测数据: s.hasBacktest ? "是" : "否",
            })),
            alerts: result.alerts,
            hint: "使用 action=compare 并指定 strategy_id 可查看单策略详细对比",
          };
        }

        default:
          return { error: `未知操作: ${action}` };
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
      error: `策略 ${strategyId} 在 ${startDate} ~ ${endDate} 期间无交易记录，请提供 symbols 参数或检查日期范围`,
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
