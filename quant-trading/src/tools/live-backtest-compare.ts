/**
 * live_backtest_compare tool — AI-callable tool for comparing live/paper
 * trading performance against backtest predictions.
 *
 * Actions:
 *   compare       — run a full live-vs-backtest comparison
 *   drift_check   — quick drift score for a strategy
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import {
  compareLiveVsBacktest,
  type LiveBacktestCompareResult,
} from "../services/live-backtest-compare.js";
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

  return {
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
}

export function createLiveBacktestCompareTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  backtestStore: BacktestStore,
  equityHistoryPaper: EquityHistory,
  equityHistoryLive: EquityHistory,
) {
  return {
    name: "live_backtest_compare",
    description:
      "实盘回测对比分析。将实盘/模拟盘的实际交易表现与回测预测进行对比，" +
      "检测收益漂移、回撤偏差、执行质量和信号填充率。" +
      "action=compare 完整对比, action=drift_check 快速漂移检查, " +
      "action=list_backtests 列出可对比的回测记录。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "drift_check", "list_backtests"],
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
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "list_backtests": {
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

        case "compare":
        case "drift_check": {
          const backtestId = args.backtest_id as string | undefined;
          const strategyId = args.strategy_id as string | undefined;
          const mode = (args.mode as string) ?? "paper";
          const startDate = args.start_date as string | undefined;
          const endDate = args.end_date as string | undefined;

          // Resolve backtest result
          let backtestRecord;
          if (backtestId) {
            backtestRecord = backtestStore.getFullResult(backtestId);
          } else if (strategyId) {
            // Find most recent backtest for this strategy
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

          // Load live equity
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

          // Load trades and signals for the strategy
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

          const result = compareLiveVsBacktest({
            strategyId: sid,
            startDate: start,
            endDate: end,
            backtestResult: bt,
            liveEquity,
            liveTrades,
            liveSignals,
          });

          if (action === "drift_check") {
            return {
              策略: result.strategyId,
              漂移得分: `${result.drift.score}/100`,
              严重程度: result.drift.severity,
              收益偏差: `${fmt(result.returnComparison.returnDiff, 2)}%`,
              执行率: `${fmt(result.executionQuality.fillRate * 100, 0)}%`,
              观察: result.drift.observations,
            };
          }

          return formatResult(result);
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
