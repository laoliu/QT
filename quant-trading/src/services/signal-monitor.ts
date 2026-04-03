import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * SignalMonitor service — two modes:
 *
 * 1. Passive (always-on): watches signals.json for new entries and emits alerts.
 * 2. Active (market hours): reads live-strategies.json, fetches recent klines
 *    for each (strategyId, symbol) pair, runs the builtin engine, and appends
 *    fresh signals to signals.json — which then triggers passive alerts.
 *
 * Supports breakout confirmation: when a strategy sets confirmationBars > 0,
 * signals must appear in that many consecutive check cycles before being emitted.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import { createDataProvider } from "../data/market-data-provider.js";
import { LiveStrategyRegistry } from "../engine/live-strategy-registry.js";
import { runBacktest } from "../engine/strategy-registry.js";
import type { QuantTradingConfig, TradingSignal } from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import type { AlertStore } from "./alert-store.js";
import type { EventBus } from "./event-bus.js";
import { tryNotify } from "./notify-helper.js";
import type { OrderManager } from "./order-manager.js";

function getSignalsPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "signals.json");
}

async function loadSignals(config: QuantTradingConfig): Promise<TradingSignal[]> {
  try {
    const raw = await fs.readFile(getSignalsPath(config), "utf-8");
    return JSON.parse(raw) as TradingSignal[];
  } catch {
    return [];
  }
}

// Serialize signal file writes to prevent concurrent read-modify-write races
const signalsMutex = new AsyncMutex();

/**
 * Tracks pending signal confirmations.
 * Key = "strategyId:symbol:direction", value = consecutive count + latest signal.
 * A signal is emitted only when count >= confirmationBars.
 */
const pendingConfirmations = new Map<
  string,
  { count: number; cycleId: number; signal: TradingSignal }
>();
/** Monotonically increasing cycle counter for confirmation tracking. */
let confirmationCycleId = 0;

/**
 * Filter signals through the confirmation logic.
 * - Strategies with confirmationBars=0 or undefined pass through immediately.
 * - Others must persist for N consecutive cycles.
 */
function applyConfirmation(
  signals: TradingSignal[],
  confirmMap: Map<string, number>, // strategyId → confirmationBars
): TradingSignal[] {
  confirmationCycleId++;
  const cycle = confirmationCycleId;
  const confirmed: TradingSignal[] = [];

  // Track which keys are seen this cycle (to expire stale entries)
  const seenKeys = new Set<string>();

  for (const sig of signals) {
    const reqBars = confirmMap.get(sig.strategyId ?? "") ?? 0;
    if (reqBars <= 0) {
      // No confirmation needed — pass through
      confirmed.push(sig);
      continue;
    }

    const key = `${sig.strategyId ?? ""}:${sig.symbol}:${sig.direction}`;
    seenKeys.add(key);

    const existing = pendingConfirmations.get(key);
    if (existing && existing.cycleId === cycle - 1) {
      // Continuing from previous cycle — increment
      existing.count++;
      existing.cycleId = cycle;
      existing.signal = sig; // Use latest signal data
    } else {
      // Fresh signal or gap — start from 1
      pendingConfirmations.set(key, { count: 1, cycleId: cycle, signal: sig });
    }

    const entry = pendingConfirmations.get(key)!;
    if (entry.count >= reqBars) {
      confirmed.push(sig);
      pendingConfirmations.delete(key); // Reset after confirmation
    }
  }

  // Expire entries not seen this cycle (breakout broken)
  for (const [key, entry] of pendingConfirmations) {
    if (entry.cycleId < cycle) {
      pendingConfirmations.delete(key);
    }
  }

  return confirmed;
}

export async function appendSignals(
  newSigs: TradingSignal[],
  config: QuantTradingConfig,
): Promise<void> {
  if (newSigs.length === 0) return;
  const release = await signalsMutex.acquire();
  try {
    const existing = await loadSignals(config);
    const all = [...existing, ...newSigs];
    // Keep at most 500 most recent
    const trimmed = all.slice(-500);
    const filePath = getSignalsPath(config);
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, JSON.stringify(trimmed, null, 2));
  } finally {
    release();
  }
}

/**
/**
 * Actively run enabled live strategies against recent kline data
 * and return any new signals that are newer than the watermark.
 *
 * Date window by interval:
 *   1m  → 1 day  (~240 bars)
 *   5m  → 5 days (~288 bars/day)
 *   15m → 7 days
 *   30m → 10 days
 *   60m → 20 days
 *   1d  → 120 days (unchanged)
 */
async function generateActiveSignals(
  config: QuantTradingConfig,
  lastSeenTs: number,
  api: OpenClawPluginApi,
): Promise<TradingSignal[]> {
  const registry = new LiveStrategyRegistry(config);
  await registry.load();
  const active = registry.list(true /* enabledOnly */);
  if (active.length === 0) return [];

  const today = new Date().toISOString().slice(0, 10);

  /** Choose how many calendar days to look back based on interval. */
  function lookbackDays(interval: string | undefined): number {
    switch (interval) {
      case "1m":
        return 1;
      case "5m":
        return 5;
      case "15m":
        return 7;
      case "30m":
        return 10;
      case "60m":
        return 20;
      default:
        return 120; // "1d" or unset
    }
  }

  const allNew: TradingSignal[] = [];

  for (const ls of active) {
    // ── Time window filter: skip strategy if outside configured windows ──
    if (ls.timeWindows && ls.timeWindows.length > 0 && !isWithinTimeWindows(ls.timeWindows)) {
      api.logger.debug?.(
        `SignalMonitor: strategy ${ls.strategyId} skipped — outside time windows ${ls.timeWindows.join(", ")}`,
      );
      continue;
    }

    const interval = ls.interval ?? "1d";
    const days = lookbackDays(interval);
    const startDate = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);

    for (const symbol of ls.symbols) {
      try {
        const result = await runBacktest(
          {
            strategyId: ls.strategyId,
            symbols: [symbol],
            startDate,
            endDate: today,
            initialCash: 100000,
            interval,
            parameters: ls.parameters,
          },
          config,
        );
        // Only take signals newer than the watermark — single loop instead of filter+map
        for (const s of result.signals) {
          if (s.timestamp > lastSeenTs) {
            allNew.push({ ...s, strategyId: ls.strategyId });
          }
        }
      } catch (err) {
        api.logger.debug?.(
          `SignalMonitor: strategy ${ls.strategyId}/${symbol} failed: ${(err as Error).message}`,
        );
      }
    }
  }

  // ── Apply breakout confirmation filter ──
  // Build confirmationBars map from active strategies
  const confirmMap = new Map<string, number>();
  for (const ls of active) {
    if (ls.confirmationBars && ls.confirmationBars > 0) {
      confirmMap.set(ls.strategyId, ls.confirmationBars);
    }
  }
  if (confirmMap.size > 0) {
    return applyConfirmation(allNew, confirmMap);
  }

  return allNew;
}

/**
 * Check if current time (in CN market timezone, Asia/Shanghai) falls within
 * any of the specified time windows.
 *
 * @param windows — array of "HH:MM-HH:MM" strings, e.g. ["09:45-10:30", "14:00-15:00"]
 */
function isWithinTimeWindows(windows: string[]): boolean {
  const now = new Date();
  // Format current time in Asia/Shanghai
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(now);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  const nowMinutes = hour * 60 + minute;

  for (const w of windows) {
    const match = /^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$/.exec(w.trim());
    if (!match) continue;
    const startMin = Number(match[1]) * 60 + Number(match[2]);
    const endMin = Number(match[3]) * 60 + Number(match[4]);
    if (nowMinutes >= startMin && nowMinutes <= endMin) return true;
  }
  return false;
}

export function createSignalMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getOrderManager?: () => OrderManager | null,
  eventBus?: EventBus,
  alertStore?: AlertStore,
): OpenClawPluginService {
  let interval: ReturnType<typeof setInterval> | null = null;
  /** Epoch ms of the most recently processed signal. */
  let lastSeenTs = 0;

  const emitAlert = async (sig: TradingSignal): Promise<void> => {
    const emoji = sig.direction === "buy" ? "📈" : sig.direction === "sell" ? "📉" : "⚠️";
    const msg =
      `${emoji} 新交易信号 — ${sig.strategyId} ` +
      `[${sig.direction.toUpperCase()}] ${sig.symbol} ` +
      `@ ¥${sig.price.toFixed(2)}` +
      (sig.confidence != null ? ` 置信度: ${(sig.confidence * 100).toFixed(0)}%` : "") +
      (sig.reason ? ` (${sig.reason})` : "");
    api.logger.info(`SignalMonitor: ${msg}`);

    // Emit to EventBus for SSE dashboard streaming
    eventBus?.emit("signal", {
      symbol: sig.symbol,
      direction: sig.direction,
      price: sig.price,
      strategyId: sig.strategyId,
      confidence: sig.confidence,
      reason: sig.reason,
      source: "signal-monitor",
    });

    // Persist to AlertStore
    alertStore?.add("info", "signal", msg, {
      symbol: sig.symbol,
      direction: sig.direction,
      strategyId: sig.strategyId,
    });

    await tryNotify(api, msg);
  };

  return {
    id: "quant-signal-monitor",
    async start() {
      api.logger.info("SignalMonitor service started");

      // Seed watermark from existing signals
      const existing = await loadSignals(config);
      if (existing.length > 0) {
        lastSeenTs = Math.max(...existing.map((s) => s.timestamp));
      }

      const tick = async () => {
        try {
          const marketOpen = isMarketOpen();

          if (marketOpen) {
            // ── Active mode: run strategies, generate new signals ──
            const fresh = await generateActiveSignals(config, lastSeenTs, api);
            if (fresh.length > 0) {
              await appendSignals(fresh, config);
              for (const sig of fresh) await emitAlert(sig);
              lastSeenTs = Math.max(lastSeenTs, ...fresh.map((s) => s.timestamp));
              api.logger.info(`SignalMonitor: generated ${fresh.length} new signal(s)`);

              // ── Forward to OrderManager when in live mode ──
              if (config.tradingAccount?.mode === "live") {
                const orderMgr = getOrderManager?.();
                if (orderMgr) {
                  for (const sig of fresh) {
                    try {
                      const result = await orderMgr.submitFromSignal(sig);
                      if (result.ok) {
                        api.logger.info(
                          `SignalMonitor: order submitted for ${sig.symbol} ${sig.direction} @ ¥${sig.price}`,
                        );
                      } else {
                        api.logger.warn(
                          `SignalMonitor: order rejected for ${sig.symbol}: ${result.reason}`,
                        );
                      }
                    } catch (err) {
                      api.logger.error(
                        `SignalMonitor: order submission failed for ${sig.symbol}: ${(err as Error).message}`,
                      );
                    }
                  }
                }
              }

              return; // watermark already updated; skip passive check
            }
          }

          // ── Passive mode: detect signals written by external sources ──
          const signals = await loadSignals(config);
          const newSigs = signals.filter((s) => s.timestamp > lastSeenTs);
          if (newSigs.length > 0) {
            lastSeenTs = Math.max(...newSigs.map((s) => s.timestamp));
            for (const sig of newSigs) await emitAlert(sig);
          } else {
            api.logger.debug?.("SignalMonitor: no new signals");
          }
        } catch (err) {
          api.logger.error(`SignalMonitor error: ${(err as Error).message}`);
        }
      };

      interval = setInterval(tick, 60 * 1000); // 1 min — fine-grained enough for 5m intraday strategies
    },

    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("SignalMonitor service stopped");
    },
  };
}
