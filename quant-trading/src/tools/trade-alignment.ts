/**
 * trade_alignment tool — AI-callable tool for aligning live vs backtest trades.
 *
 * Actions:
 *   align_signals     — align live trades against backtest signals (TradingSignal[])
 *   align_trades      — align live trades against backtest trade records (TradeRecord[])
 *   list_backtests    — list available backtest runs for alignment
 *   report            — generate a human-readable alignment report
 *   pnl_drift         — compute cumulative PnL drift between live and backtest
 *   export_csv        — export alignment result as CSV string
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BacktestStore } from "../services/backtest-store.js";
import {
  alignTrades,
  alignTradeRecords,
  computePnlDrift,
  exportAlignmentCsv,
  filterPairs,
  formatAlignmentReport,
  formatPnlDriftReport,
  formatTradeRecordAlignmentReport,
  A_SHARE_MORNING_SESSION,
  A_SHARE_AFTERNOON_SESSION,
} from "../services/trade-alignment.js";
import type {
  AlignmentConfig,
  AlignmentMatchType,
  QuantTradingConfig,
  TradingSession,
  TradeRecord,
} from "../types.js";

/** Load live/paper trades from the JSON file written by OrderManager. */
async function loadTradesFromFile(config: QuantTradingConfig, mode?: "paper" | "live"): Promise<TradeRecord[]> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const suffix = mode ?? "paper";
  const filePath = path.join(dir, `trades-${suffix}.json`);
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

export function createTradeAlignmentTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  backtestStore: BacktestStore,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "trade_alignment",
    description:
      "实盘回测对齐。将实盘交易记录与回测结果按时间/标的对齐配对，分析执行质量、滑点和盈亏差异。" +
      "action=align_signals 对齐实盘交易与回测信号, " +
      "action=align_trades 对齐实盘与回测交易记录, " +
      "action=list_backtests 列出可对齐的回测记录, " +
      "action=report 生成对齐报告, " +
      "action=pnl_drift 计算累计盈亏偏移曲线, " +
      "action=export_csv 导出对齐结果为CSV。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["align_signals", "align_trades", "list_backtests", "report", "pnl_drift", "export_csv"],
          description:
            "操作: align_signals=对齐信号, align_trades=对齐交易, list_backtests=列出回测, report=报告, pnl_drift=盈亏偏移, export_csv=导出CSV",
        },
        backtest_id: {
          type: "string",
          description: "回测运行ID (从 list_backtests 获取)",
        },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "交易模式: paper=模拟盘, live=实盘 (默认paper)",
        },
        symbol: {
          type: "string",
          description: "按标的代码筛选 (可选)",
        },
        strategy_id: {
          type: "string",
          description: "按策略ID筛选 (可选，用于 list_backtests)",
        },
        time_tolerance_hours: {
          type: "number",
          description: "时间容忍度（小时，默认24）",
        },
        price_tolerance_pct: {
          type: "number",
          description: "价格容忍度（百分比，默认5）",
        },
        filter_match_type: {
          type: "string",
          enum: ["exact", "time_shifted", "live_only", "backtest_only"],
          description: "只返回指定匹配类型的结果",
        },
        limit: {
          type: "number",
          description: "限制返回配对数量 (默认50)",
        },
        trading_session: {
          type: "string",
          enum: ["morning", "afternoon"],
          description: "A股交易时段筛选: morning=上午盘(9:30-11:30), afternoon=下午盘(13:00-15:00)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const mode = (args.mode as "paper" | "live") ?? "paper";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 50;

      const alignConfig: AlignmentConfig = {};
      if (args.time_tolerance_hours != null) {
        alignConfig.timeToleranceMs = (args.time_tolerance_hours as number) * 3_600_000;
      }
      if (args.price_tolerance_pct != null) {
        alignConfig.priceToleranceRatio = (args.price_tolerance_pct as number) / 100;
      }
      // Trading session filter
      if (args.trading_session === "morning") {
        alignConfig.tradingSession = A_SHARE_MORNING_SESSION;
      } else if (args.trading_session === "afternoon") {
        alignConfig.tradingSession = A_SHARE_AFTERNOON_SESSION;
      }

      switch (action) {
        // ── List available backtests ──────────────────────────────
        case "list_backtests": {
          const strategyId = args.strategy_id as string | undefined;
          const entries = backtestStore.query({
            strategyId,
            sortBy: "createdAt",
            sortDir: "desc",
            limit: 20,
          });
          if (entries.length === 0) {
            return { message: "暂无回测记录。请先运行回测。" };
          }
          return {
            backtests: entries.map((e) => ({
              id: e.id,
              strategyId: e.strategyId,
              symbols: e.symbols,
              dateRange: `${e.startDate} ~ ${e.endDate}`,
              totalReturn: `${(e.totalReturn * 100).toFixed(2)}%`,
              totalTrades: e.totalTrades,
              engine: e.engine,
              createdAt: e.createdAt,
              starred: e.starred,
            })),
            hint: "使用 backtest_id 参数来选择要对齐的回测运行",
          };
        }

        // ── Align live trades against backtest signals ───────────
        case "align_signals": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "需要 backtest_id 参数。使用 action=list_backtests 查看可用回测。" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `回测记录 ${backtestId} 不存在` };
          }

          // Load live trades
          let liveTrades = await loadLiveTrades(config, mode, getEngine);
          if (symbol) {
            liveTrades = liveTrades.filter((t) => t.symbol === symbol);
          }

          if (liveTrades.length === 0) {
            return {
              error: `暂无${mode === "live" ? "实盘" : "模拟盘"}交易记录`,
              hint: "请确认交易引擎已运行并产生成交记录",
            };
          }

          const result = alignTrades(liveTrades, record.result, alignConfig);

          // Apply filter
          let pairs = result.pairs;
          if (args.filter_match_type) {
            pairs = filterPairs(pairs, [args.filter_match_type as AlignmentMatchType]);
          }

          return {
            strategyId: result.strategyId,
            symbols: result.symbols,
            dateRange: result.dateRange,
            summary: formatSummary(result.summary),
            symbolBreakdown: result.symbolBreakdown
              ? formatSymbolBreakdown(result.symbolBreakdown)
              : undefined,
            pairs: pairs.slice(0, limit).map((p) => ({
              id: p.id,
              matchType: p.matchType,
              symbol: p.symbol,
              liveEntryTime: p.liveTrade?.entryTime,
              liveEntryPrice: p.liveTrade?.entryPrice,
              livePnl: p.liveTrade?.netPnl,
              backtestTimestamp: p.backtestTimestamp
                ? new Date(p.backtestTimestamp).toISOString()
                : undefined,
              backtestPrice: p.backtestPrice,
              timeDeltaHours: p.timeDeltaMs != null
                ? Number((p.timeDeltaMs / 3_600_000).toFixed(2))
                : undefined,
              priceSlippagePct: p.priceSlippage != null
                ? Number((p.priceSlippage * 100).toFixed(3))
                : undefined,
              pnlDelta: p.pnlDelta != null ? Number(p.pnlDelta.toFixed(2)) : undefined,
            })),
            totalPairsReturned: Math.min(pairs.length, limit),
            totalPairsAvailable: pairs.length,
          };
        }

        // ── Align live trades against backtest trade records ─────
        case "align_trades": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "需要 backtest_id 参数。使用 action=list_backtests 查看可用回测。" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `回测记录 ${backtestId} 不存在` };
          }

          // Load live trades
          let liveTrades = await loadLiveTrades(config, mode, getEngine);
          if (symbol) {
            liveTrades = liveTrades.filter((t) => t.symbol === symbol);
          }

          if (liveTrades.length === 0) {
            return {
              error: `暂无${mode === "live" ? "实盘" : "模拟盘"}交易记录`,
              hint: "请确认交易引擎已运行并产生成交记录",
            };
          }

          // Convert backtest signals to synthetic TradeRecord for alignment
          const backtestTrades = signalsToTradeRecords(record.result.signals, record.result.strategyId);

          const result = alignTradeRecords(liveTrades, backtestTrades, {
            strategyId: record.result.strategyId,
            symbols: symbol ? [symbol] : undefined,
            config: alignConfig,
          });

          // Apply filter
          let pairs = result.pairs;
          if (args.filter_match_type) {
            const matchType = args.filter_match_type as AlignmentMatchType;
            pairs = pairs.filter((p) => p.matchType === matchType);
          }

          return {
            strategyId: result.strategyId,
            symbols: result.symbols,
            dateRange: result.dateRange,
            summary: formatSummary(result.summary),
            symbolBreakdown: result.symbolBreakdown
              ? formatSymbolBreakdown(result.symbolBreakdown)
              : undefined,
            pairs: pairs.slice(0, limit).map((p) => ({
              id: p.id,
              matchType: p.matchType,
              symbol: p.symbol,
              live: p.liveTrade
                ? {
                    entryTime: p.liveTrade.entryTime,
                    exitTime: p.liveTrade.exitTime,
                    entryPrice: p.liveTrade.entryPrice,
                    exitPrice: p.liveTrade.exitPrice,
                    netPnl: p.liveTrade.netPnl,
                    holdingDays: p.liveTrade.holdingDays,
                  }
                : undefined,
              backtest: p.backtestTrade
                ? {
                    entryTime: p.backtestTrade.entryTime,
                    exitTime: p.backtestTrade.exitTime,
                    entryPrice: p.backtestTrade.entryPrice,
                    exitPrice: p.backtestTrade.exitPrice,
                    netPnl: p.backtestTrade.netPnl,
                    holdingDays: p.backtestTrade.holdingDays,
                  }
                : undefined,
              entryTimeDeltaHours: p.entryTimeDeltaMs != null
                ? Number((p.entryTimeDeltaMs / 3_600_000).toFixed(2))
                : undefined,
              exitTimeDeltaHours: p.exitTimeDeltaMs != null
                ? Number((p.exitTimeDeltaMs / 3_600_000).toFixed(2))
                : undefined,
              entrySlippagePct: p.entryPriceSlippage != null
                ? Number((p.entryPriceSlippage * 100).toFixed(3))
                : undefined,
              exitSlippagePct: p.exitPriceSlippage != null
                ? Number((p.exitPriceSlippage * 100).toFixed(3))
                : undefined,
              pnlDelta: p.pnlDelta != null ? Number(p.pnlDelta.toFixed(2)) : undefined,
              quantitySlippagePct: p.quantitySlippage != null
                ? Number((p.quantitySlippage * 100).toFixed(2))
                : undefined,
              holdingDaysDelta: p.holdingDaysDelta,
            })),
            totalPairsReturned: Math.min(pairs.length, limit),
            totalPairsAvailable: pairs.length,
          };
        }

        // ── Human-readable report ────────────────────────────────
        case "report": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "需要 backtest_id 参数。使用 action=list_backtests 查看可用回测。" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `回测记录 ${backtestId} 不存在` };
          }

          let liveTrades = await loadLiveTrades(config, mode, getEngine);
          if (symbol) {
            liveTrades = liveTrades.filter((t) => t.symbol === symbol);
          }

          if (liveTrades.length === 0) {
            return { error: `暂无${mode === "live" ? "实盘" : "模拟盘"}交易记录` };
          }

          // Generate both signal-level and trade-level reports
          const signalResult = alignTrades(liveTrades, record.result, alignConfig);
          const signalReport = formatAlignmentReport(signalResult);

          const backtestTrades = signalsToTradeRecords(record.result.signals, record.result.strategyId);
          const tradeResult = alignTradeRecords(liveTrades, backtestTrades, {
            strategyId: record.result.strategyId,
            symbols: symbol ? [symbol] : undefined,
            config: alignConfig,
          });
          const tradeReport = formatTradeRecordAlignmentReport(tradeResult);

          return {
            signalLevelReport: signalReport,
            tradeLevelReport: tradeReport,
            liveTrades: liveTrades.length,
            backtestSignals: record.result.signals.length,
            backtestSyntheticTrades: backtestTrades.length,
          };
        }

        // ── Cumulative PnL drift analysis ───────────────────────
        case "pnl_drift": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "需要 backtest_id 参数。使用 action=list_backtests 查看可用回测。" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `回测记录 ${backtestId} 不存在` };
          }

          let liveTrades = await loadLiveTrades(config, mode, getEngine);
          if (symbol) {
            liveTrades = liveTrades.filter((t) => t.symbol === symbol);
          }

          if (liveTrades.length === 0) {
            return { error: `暂无${mode === "live" ? "实盘" : "模拟盘"}交易记录` };
          }

          const backtestTrades = signalsToTradeRecords(record.result.signals, record.result.strategyId);
          const tradeResult = alignTradeRecords(liveTrades, backtestTrades, {
            strategyId: record.result.strategyId,
            symbols: symbol ? [symbol] : undefined,
            config: alignConfig,
          });

          const drift = computePnlDrift(tradeResult);
          const driftReport = formatPnlDriftReport(drift);

          return {
            ...drift,
            report: driftReport,
          };
        }

        // ── Export alignment result as CSV ──────────────────────
        case "export_csv": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "需要 backtest_id 参数。使用 action=list_backtests 查看可用回测。" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `回测记录 ${backtestId} 不存在` };
          }

          let liveTrades = await loadLiveTrades(config, mode, getEngine);
          if (symbol) {
            liveTrades = liveTrades.filter((t) => t.symbol === symbol);
          }

          if (liveTrades.length === 0) {
            return { error: `暂无${mode === "live" ? "实盘" : "模拟盘"}交易记录` };
          }

          const backtestTrades = signalsToTradeRecords(record.result.signals, record.result.strategyId);
          const tradeResult = alignTradeRecords(liveTrades, backtestTrades, {
            strategyId: record.result.strategyId,
            symbols: symbol ? [symbol] : undefined,
            config: alignConfig,
          });

          const csv = exportAlignmentCsv(tradeResult);

          return {
            csv,
            pairCount: tradeResult.pairs.length,
            hint: "CSV内容已生成，可直接复制到文件或Excel中使用",
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Internal helpers ────────────────────────────────────────────

/** Load live trades from OrderManager (if running) or from file. */
async function loadLiveTrades(
  config: QuantTradingConfig,
  mode: "paper" | "live",
  getEngine: () => LiveEngine | null,
): Promise<TradeRecord[]> {
  // Try to get from running OrderManager first
  const engine = getEngine();
  if (engine?.orderManager) {
    try {
      return await engine.orderManager.getTrades();
    } catch {
      // Fall through to file
    }
  }
  // Fallback to loading from file
  return loadTradesFromFile(config, mode);
}

/**
 * Convert backtest signals (buy/sell pairs) into synthetic TradeRecord objects
 * for trade-level alignment.
 *
 * Groups signals by symbol, pairs consecutive buy→sell signals into TradeRecords.
 */
function signalsToTradeRecords(signals: { timestamp: number | string; symbol: string; direction: string; price: number; quantity?: number; strategyId?: string }[], strategyId: string): TradeRecord[] {
  const records: TradeRecord[] = [];
  // Group by symbol
  const bySymbol = new Map<string, typeof signals>();
  for (const s of signals) {
    let arr = bySymbol.get(s.symbol);
    if (!arr) {
      arr = [];
      bySymbol.set(s.symbol, arr);
    }
    arr.push(s);
  }

  for (const [symbol, symSignals] of bySymbol) {
    const sorted = [...symSignals].sort((a, b) => {
      const tsA = typeof a.timestamp === "number" ? a.timestamp : new Date(a.timestamp).getTime();
      const tsB = typeof b.timestamp === "number" ? b.timestamp : new Date(b.timestamp).getTime();
      return tsA - tsB;
    });

    let pendingBuy: (typeof sorted)[0] | null = null;
    let counter = 0;

    for (const sig of sorted) {
      const dir = sig.direction.toLowerCase();
      if (dir === "buy" || dir === "buy_open" || dir === "cover") {
        pendingBuy = sig;
      } else if ((dir === "sell" || dir === "sell_close" || dir === "short") && pendingBuy) {
        const entryTs = typeof pendingBuy.timestamp === "number"
          ? pendingBuy.timestamp
          : new Date(pendingBuy.timestamp).getTime();
        const exitTs = typeof sig.timestamp === "number"
          ? sig.timestamp
          : new Date(sig.timestamp).getTime();
        const qty = pendingBuy.quantity ?? 100;
        const grossPnl = (sig.price - pendingBuy.price) * qty;
        const holdingDays = Math.max(1, Math.round((exitTs - entryTs) / 86_400_000));

        records.push({
          tradeId: `bt-${symbol}-${counter++}`,
          symbol,
          market: "cn_stock",
          side: "buy",
          entryTime: new Date(entryTs).toISOString(),
          exitTime: new Date(exitTs).toISOString(),
          entryPrice: pendingBuy.price,
          exitPrice: sig.price,
          quantity: qty,
          grossPnl,
          commission: 0,
          netPnl: grossPnl,
          holdingDays,
          strategyId,
          entryOrderId: `bt-entry-${counter}`,
          exitOrderId: `bt-exit-${counter}`,
        });
        pendingBuy = null;
      }
    }
  }

  return records;
}

function formatSummary(s: { totalPairs: number; exactMatches: number; timeShiftedMatches: number; liveOnlyTrades: number; backtestOnlySignals: number; avgTimeDeltaMs: number; avgPriceSlippage: number; avgPnlDelta: number; avgQuantitySlippage: number; executionRate: number; pnlCorrelation: number }) {
  return {
    totalPairs: s.totalPairs,
    exactMatches: s.exactMatches,
    timeShiftedMatches: s.timeShiftedMatches,
    liveOnlyTrades: s.liveOnlyTrades,
    backtestOnlySignals: s.backtestOnlySignals,
    avgTimeDeltaHours: Number((s.avgTimeDeltaMs / 3_600_000).toFixed(2)),
    avgPriceSlippagePct: Number((s.avgPriceSlippage * 100).toFixed(3)),
    avgQuantitySlippagePct: Number((s.avgQuantitySlippage * 100).toFixed(2)),
    avgPnlDelta: Number(s.avgPnlDelta.toFixed(2)),
    executionRate: `${(s.executionRate * 100).toFixed(1)}%`,
    pnlCorrelation: Number(s.pnlCorrelation.toFixed(4)),
  };
}

function formatSymbolBreakdown(breakdown: Record<string, { totalPairs: number; exactMatches: number; timeShiftedMatches: number; liveOnlyTrades: number; backtestOnlySignals: number; avgTimeDeltaMs: number; avgPriceSlippage: number; avgPnlDelta: number; avgQuantitySlippage: number; executionRate: number; pnlCorrelation: number }>) {
  const result: Record<string, ReturnType<typeof formatSummary>> = {};
  for (const [sym, s] of Object.entries(breakdown)) {
    result[sym] = formatSummary(s);
  }
  return result;
}
