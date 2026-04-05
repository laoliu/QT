/**
 * live_backtest_compare tool — AI-callable tool for comparing live trading
 * performance against backtest results (FR-8: 实盘 vs 回测对比).
 *
 * Actions:
 *   compare          — Run a backtest and compare against live equity history
 *   compare_existing — Compare live equity against an existing stored backtest
 *   live_summary     — View live equity performance summary only
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runBacktest } from "../engine/strategy-registry.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import {
  compareLiveVsBacktest,
  computeLivePerformance,
} from "../services/live-backtest-comparison.js";
import type { QuantTradingConfig } from "../types.js";

/** Safe toFixed — returns "N/A" when the value is nullish or not a finite number. */
function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

function pct(v: unknown, digits = 2): string {
  return typeof v === "number" && Number.isFinite(v)
    ? `${(v * 100).toFixed(digits)}%`
    : "N/A";
}

export function createLiveBacktestCompareTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  equityHistory?: EquityHistory | (() => EquityHistory),
  backtestStore?: BacktestStore,
) {
  const resolveEH = (): EquityHistory | undefined =>
    typeof equityHistory === "function" ? equityHistory() : equityHistory;
  return {
    name: "live_backtest_compare",
    description:
      "实盘 vs 回测对比分析。" +
      "将实盘交易表现与回测结果进行侧对比，检测过拟合、执行滑点和策略偏离。\n" +
      "action=compare 运行回测并与实盘对比, " +
      "action=compare_existing 用已保存的回测结果对比, " +
      "action=live_summary 仅查看实盘绩效摘要。\n" +
      "【重要提示】需要先有实盘权益历史(通过 live_trading 启动引擎后自动记录)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "compare_existing", "live_summary"],
          description: "操作类型",
        },
        strategyId: {
          type: "string",
          description: "策略ID，用于运行对比回测 (action=compare)",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔 (action=compare)",
        },
        startDate: {
          type: "string",
          description: "对比起始日期 YYYY-MM-DD (默认取实盘历史起始)",
        },
        endDate: {
          type: "string",
          description: "对比结束日期 YYYY-MM-DD (默认取今天)",
        },
        initialCash: {
          type: "number",
          description: "回测初始资金，默认匹配实盘起始权益",
        },
        backtestId: {
          type: "string",
          description: "已保存的回测记录ID (action=compare_existing)",
        },
        divergenceThreshold: {
          type: "number",
          description: "偏离阈值百分比，默认5 (用于识别偏离窗口)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const eh = resolveEH();

      // ── live_summary ──────────────────────────────────────
      if (action === "live_summary") {
        if (!eh) {
          return { error: "实盘权益历史服务未启用，请先启动交易引擎。" };
        }
        const snapshots = eh.getAll();
        if (snapshots.length < 2) {
          return { error: "实盘权益数据不足（至少需要2天记录），请先运行一段时间的实盘交易。" };
        }
        const startDate = args.startDate as string | undefined;
        const endDate = args.endDate as string | undefined;
        const perf = computeLivePerformance(snapshots, startDate, endDate);
        if (!perf) {
          return { error: "指定日期范围内实盘数据不足。" };
        }
        return {
          summary: {
            period: `${perf.startDate} ~ ${perf.endDate}`,
            tradingDays: perf.tradingDays,
            startEquity: `¥${perf.startEquity.toLocaleString()}`,
            endEquity: `¥${perf.endEquity.toLocaleString()}`,
            totalReturn: pct(perf.totalReturn),
            annualizedReturn: pct(perf.annualizedReturn),
            maxDrawdown: pct(perf.maxDrawdown),
            sharpeRatio: fmt(perf.sharpeRatio, 3),
            winRate: pct(perf.winRate),
            avgDailyPnl: `¥${fmt(perf.avgDailyPnl, 2)}`,
          },
          equityCurve: perf.equityCurve.slice(-60),
        };
      }

      // ── compare_existing ──────────────────────────────────
      if (action === "compare_existing") {
        if (!eh) {
          return { error: "实盘权益历史服务未启用。" };
        }
        const backtestId = args.backtestId as string;
        if (!backtestId) {
          return { error: "请提供 backtestId (已保存的回测记录ID)。" };
        }
        if (!backtestStore) {
          return { error: "回测存储未启用。" };
        }
        const record = backtestStore.getFullResult(backtestId);
        if (!record) {
          return { error: `未找到回测记录: ${backtestId}` };
        }

        const snapshots = eh.getAll();
        const result = compareLiveVsBacktest(snapshots, record.result, {
          startDate: args.startDate as string | undefined,
          endDate: args.endDate as string | undefined,
          divergenceThresholdPct: args.divergenceThreshold as number | undefined,
        });

        if (!result) {
          return { error: "实盘与回测数据重叠不足，无法对比。请确保两者有相同的日期范围。" };
        }

        return formatComparisonResult(result, record.result.strategyId);
      }

      // ── compare (run backtest then compare) ───────────────
      if (action === "compare") {
        if (!eh) {
          return { error: "实盘权益历史服务未启用，请先启动交易引擎。" };
        }

        const strategyId = args.strategyId as string;
        if (!strategyId) {
          return { error: "请提供策略ID (strategyId)。" };
        }

        const snapshots = eh.getAll();
        if (snapshots.length < 2) {
          return { error: "实盘权益数据不足（至少需要2天记录）。" };
        }

        // Determine date range from live history
        const sorted = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));
        const liveStart = (args.startDate as string) ?? sorted[0]!.date;
        const liveEnd = (args.endDate as string) ?? sorted[sorted.length - 1]!.date;

        // Use live starting equity as initial cash if not specified
        const liveStartSnap = sorted.find((s) => s.date >= liveStart);
        const initialCash =
          (args.initialCash as number) ?? liveStartSnap?.equity ?? 100_000;

        const symbols = ((args.symbols as string) ?? "000300")
          .split(",")
          .map((s) => s.trim());

        try {
          const btResult = await runBacktest(
            {
              strategyId,
              symbols,
              startDate: liveStart,
              endDate: liveEnd,
              initialCash,
            },
            config,
          );

          // Auto-save the backtest
          let savedId: string | undefined;
          if (backtestStore) {
            try {
              savedId = await backtestStore.save(btResult, {
                tags: ["live-compare"],
                engine: config.backtestEngine ?? "builtin",
              });
            } catch {
              // Best-effort
            }
          }

          const comparison = compareLiveVsBacktest(snapshots, btResult, {
            startDate: liveStart,
            endDate: liveEnd,
            divergenceThresholdPct: args.divergenceThreshold as number | undefined,
          });

          if (!comparison) {
            return {
              error: "实盘与回测数据重叠不足，无法对比。",
              backtestSummary: {
                strategy: strategyId,
                totalReturn: pct(btResult.totalReturn),
                sharpeRatio: fmt(btResult.sharpeRatio, 3),
              },
            };
          }

          return {
            ...formatComparisonResult(comparison, strategyId),
            savedBacktestId: savedId,
          };
        } catch (err) {
          return { error: `回测失败: ${(err as Error).message}` };
        }
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

function formatComparisonResult(
  result: ReturnType<typeof compareLiveVsBacktest> & {},
  strategyId: string,
) {
  return {
    strategy: strategyId,
    comparisonPeriod: `${result.comparisonPeriod.startDate} ~ ${result.comparisonPeriod.endDate}`,

    // Side-by-side summary
    sideBySide: {
      live: {
        totalReturn: pct(result.live.totalReturn),
        annualizedReturn: pct(result.live.annualizedReturn),
        maxDrawdown: pct(result.live.maxDrawdown),
        sharpeRatio: fmt(result.live.sharpeRatio, 3),
        winRate: pct(result.live.winRate),
        tradingDays: result.live.tradingDays,
      },
      backtest: {
        totalReturn: pct(result.backtest.totalReturn),
        annualizedReturn: pct(result.backtest.annualizedReturn),
        maxDrawdown: pct(result.backtest.maxDrawdown),
        sharpeRatio: fmt(result.backtest.sharpeRatio, 3),
        winRate: pct(result.backtest.winRate),
        totalTrades: result.backtest.totalTrades,
      },
    },

    // Metric diffs
    metrics: result.metrics.map((m) => ({
      metric: m.label,
      live: m.metric === "maxDrawdown" ? pct(m.live) : m.metric.includes("Return") ? pct(m.live) : fmt(m.live, 4),
      backtest: m.metric === "maxDrawdown" ? pct(m.backtest) : m.metric.includes("Return") ? pct(m.backtest) : fmt(m.backtest, 4),
      diff: `${m.diff > 0 ? "+" : ""}${fmt(m.diff, 4)}`,
      diffPct: `${m.diffPct > 0 ? "+" : ""}${fmt(m.diffPct, 1)}%`,
      verdict: m.liveWins ? "✅ 实盘优" : "⚠️ 回测优",
    })),

    // Tracking
    trackingScore: result.trackingScore,
    trackingVerdict:
      result.trackingScore >= 80
        ? "实盘高度跟踪回测，策略执行良好"
        : result.trackingScore >= 50
          ? "实盘与回测有一定偏差，需关注执行质量"
          : "实盘与回测偏差较大，策略可能过拟合或执行有重大问题",

    // Execution gap
    executionGap: {
      returnGap: `${result.executionGap.returnGap > 0 ? "+" : ""}${pct(result.executionGap.returnGap)}`,
      avgSlippage: `${fmt(result.executionGap.avgSlippagePct, 2)}%`,
      drawdownGap: `${result.executionGap.drawdownGap > 0 ? "+" : ""}${pct(result.executionGap.drawdownGap)}`,
    },

    // Divergence windows
    divergenceWindows:
      result.divergenceWindows.length > 0
        ? result.divergenceWindows.map((w) => ({
            period: `${w.startDate} ~ ${w.endDate}`,
            days: w.days,
            maxGap: `¥${fmt(w.maxGap, 2)}`,
            maxGapPct: `${fmt(w.maxGapPct, 1)}%`,
            direction: w.liveAbove ? "实盘高于回测" : "实盘低于回测",
          }))
        : "无显著偏离窗口",

    // Equity curves for chart overlay (last 60 points)
    equityCurves: {
      live: result.live.equityCurve.slice(-60),
      backtest: result.backtest.equityCurve.slice(-60),
    },

    // Warnings
    warnings: result.warnings.length > 0 ? result.warnings : undefined,
  };
}
