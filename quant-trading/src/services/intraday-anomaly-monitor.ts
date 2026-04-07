/**
 * IntradayAnomalyMonitor — background service that scans watchlist + positions
 * during trading hours for real-time anomalies:
 *
 *   1. Price surge/plunge — 5-min change exceeds threshold (default ±3%)
 *   2. Volume spike — current 5-min volume > N-period average × multiplier
 *   3. Limit-up / limit-down proximity — price within threshold of daily limit
 *
 * Runs every `scanIntervalSecs` (default 120s = 2 minutes) while market is open.
 * Emits 'alert' events via EventBus and persists to AlertStore.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";
import type { AlertStore } from "./alert-store.js";
import type { EventBus } from "./event-bus.js";
import { tryNotify } from "./notify-helper.js";

// ── Config ───────────────────────────────────────────────────────

export type IntradayAnomalyConfig = {
  /** Scan interval in seconds (default 120) */
  scanIntervalSecs?: number;
  /** 5-min price change threshold for surge/plunge alert (default 0.03 = 3%) */
  priceChangeThreshold?: number;
  /** Volume spike multiplier over N-period average (default 2.0) */
  volumeSpikeMultiplier?: number;
  /** Number of prior periods for volume average (default 6) */
  volumeAvgPeriods?: number;
  /** How close to daily limit triggers alert, as pct of price (default 0.01 = 1%) */
  limitProximityPct?: number;
  /** CN stock daily limit pct (default 0.10 = 10%) */
  dailyLimitPct?: number;
  /** Enable/disable the monitor (default true) */
  enabled?: boolean;
};

const DEFAULTS: Required<IntradayAnomalyConfig> = {
  scanIntervalSecs: 120,
  priceChangeThreshold: 0.03,
  volumeSpikeMultiplier: 2.0,
  volumeAvgPeriods: 6,
  limitProximityPct: 0.01,
  dailyLimitPct: 0.10,
  enabled: true,
};

// ── Anomaly types ────────────────────────────────────────────────

export type AnomalyType = "price_surge" | "price_plunge" | "volume_spike" | "limit_proximity";

export type AnomalyEvent = {
  type: AnomalyType;
  symbol: string;
  message: string;
  timestamp: string;
  metadata: Record<string, unknown>;
};

// ── Helpers ──────────────────────────────────────────────────────

function getPortfolioPaths(config: QuantTradingConfig): string[] {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return [
    path.join(dir, "portfolio-paper.json"),
    path.join(dir, "portfolio-live.json"),
    path.join(dir, "portfolio.json"),
  ];
}

function getWatchlistPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "watchlist.json");
}

async function loadTargetSymbols(config: QuantTradingConfig): Promise<string[]> {
  const seen = new Set<string>();
  for (const pfPath of getPortfolioPaths(config)) {
    try {
      const raw = await fs.readFile(pfPath, "utf-8");
      const pf = JSON.parse(raw) as { positions: Array<{ symbol: string }> };
      for (const p of pf.positions) seen.add(p.symbol);
    } catch {
      // file may not exist
    }
  }
  try {
    const raw = await fs.readFile(getWatchlistPath(config), "utf-8");
    const list = JSON.parse(raw) as string[];
    for (const s of list) seen.add(s);
  } catch {
    // no watchlist file
  }
  return [...seen];
}

// ── Service factory ──────────────────────────────────────────────

export function createIntradayAnomalyMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  eventBus?: EventBus,
  alertStore?: AlertStore,
): OpenClawPluginService {
  const monitorCfg: Required<IntradayAnomalyConfig> = {
    ...DEFAULTS,
    ...(config.intradayAnomalyMonitor ?? {}),
  };

  let interval: ReturnType<typeof setInterval> | null = null;
  let provider: ReturnType<typeof createDataProvider> | null = null;

  // Per-symbol tracking for volume history and dedup
  const volumeHistory = new Map<string, number[]>();
  // Dedup: only fire each anomaly type per symbol once per 30 minutes
  const lastFired = new Map<string, number>();
  const DEDUP_INTERVAL_MS = 30 * 60 * 1000;

  const shouldFire = (symbol: string, type: AnomalyType): boolean => {
    const key = `${symbol}:${type}`;
    const now = Date.now();
    const last = lastFired.get(key);
    if (last && now - last < DEDUP_INTERVAL_MS) return false;
    lastFired.set(key, now);
    return true;
  };

  const emitAnomaly = async (anomaly: AnomalyEvent): Promise<void> => {
    if (!shouldFire(anomaly.symbol, anomaly.type)) return;

    api.logger.info(
      `[IntradayAnomaly] ${anomaly.type}: ${anomaly.symbol} — ${anomaly.message}`,
    );

    // Persist to AlertStore
    alertStore?.add("warning", "price_alert", anomaly.message, {
      anomalyType: anomaly.type,
      symbol: anomaly.symbol,
      ...anomaly.metadata,
    });

    // Emit to EventBus for SSE streaming
    eventBus?.emit("alert", {
      severity: "warning",
      type: "intraday_anomaly",
      anomalyType: anomaly.type,
      symbol: anomaly.symbol,
      message: anomaly.message,
      ...anomaly.metadata,
    });

    await tryNotify(api, `⚡ [盘中异动] ${anomaly.message}`);
  };

  const tick = async () => {
    if (!isMarketOpen()) return;

    const symbols = await loadTargetSymbols(config);
    if (symbols.length === 0) return;

    const dp = provider ?? createDataProvider(config);
    const now = new Date();
    const timestamp = now.toISOString();

    for (const symbol of symbols) {
      try {
        const market = detectMarketFromSymbol(symbol);
        const quote = await dp.getQuote(symbol, market as never);
        if (!quote) continue;

        const { price, changePct, volume, open, preClose } = quote as {
          price: number;
          changePct: number;
          volume: number;
          open?: number;
          preClose?: number;
        };

        if (!price || price <= 0) continue;

        // Reference price for limit calculation
        const refPrice = preClose ?? open ?? 0;

        // ── 1. Price surge / plunge (based on daily changePct) ──
        if (changePct !== undefined && changePct !== null) {
          const absPct = Math.abs(changePct / 100); // changePct is in percent form
          if (absPct >= monitorCfg.priceChangeThreshold) {
            const direction = changePct > 0 ? "急涨" : "急跌";
            const type: AnomalyType = changePct > 0 ? "price_surge" : "price_plunge";
            await emitAnomaly({
              type,
              symbol,
              message: `${symbol} ${direction} ${changePct.toFixed(2)}%，现价 ¥${price.toFixed(2)}`,
              timestamp,
              metadata: { price, changePct, refPrice },
            });
          }
        }

        // ── 2. Volume spike ──
        if (volume > 0) {
          const history = volumeHistory.get(symbol) ?? [];
          history.push(volume);
          // Keep only the configured number of periods
          if (history.length > monitorCfg.volumeAvgPeriods + 1) {
            history.splice(0, history.length - monitorCfg.volumeAvgPeriods - 1);
          }
          volumeHistory.set(symbol, history);

          if (history.length > monitorCfg.volumeAvgPeriods) {
            // Average of prior N periods (exclude current)
            const priorVolumes = history.slice(0, -1);
            const avgVolume = priorVolumes.reduce((a, b) => a + b, 0) / priorVolumes.length;
            if (avgVolume > 0 && volume > avgVolume * monitorCfg.volumeSpikeMultiplier) {
              const ratio = (volume / avgVolume).toFixed(1);
              await emitAnomaly({
                type: "volume_spike",
                symbol,
                message: `${symbol} 成交量突增 ${ratio}倍（当前 ${(volume / 10000).toFixed(0)}万，均量 ${(avgVolume / 10000).toFixed(0)}万）`,
                timestamp,
                metadata: { volume, avgVolume, ratio: volume / avgVolume },
              });
            }
          }
        }

        // ── 3. Limit-up / limit-down proximity ──
        if (refPrice > 0) {
          const limitUp = refPrice * (1 + monitorCfg.dailyLimitPct);
          const limitDown = refPrice * (1 - monitorCfg.dailyLimitPct);
          const proximityThreshold = refPrice * monitorCfg.limitProximityPct;

          if (price >= limitUp - proximityThreshold) {
            const distPct = ((limitUp - price) / refPrice * 100).toFixed(2);
            await emitAnomaly({
              type: "limit_proximity",
              symbol,
              message: `${symbol} 逼近涨停（距涨停 ${distPct}%），现价 ¥${price.toFixed(2)}，涨停价 ¥${limitUp.toFixed(2)}`,
              timestamp,
              metadata: { price, limitUp, distancePct: (limitUp - price) / refPrice },
            });
          } else if (price <= limitDown + proximityThreshold) {
            const distPct = ((price - limitDown) / refPrice * 100).toFixed(2);
            await emitAnomaly({
              type: "limit_proximity",
              symbol,
              message: `${symbol} 逼近跌停（距跌停 ${distPct}%），现价 ¥${price.toFixed(2)}，跌停价 ¥${limitDown.toFixed(2)}`,
              timestamp,
              metadata: { price, limitDown, distancePct: (price - limitDown) / refPrice },
            });
          }
        }
      } catch (err) {
        api.logger.warn?.(
          `[IntradayAnomaly] ${symbol} scan failed: ${(err as Error).message}`,
        );
      }
    }
  };

  return {
    id: "quant-intraday-anomaly-monitor",
    async start() {
      if (!monitorCfg.enabled) {
        api.logger.info("IntradayAnomalyMonitor: disabled by config");
        return;
      }
      provider = createDataProvider(config);
      api.logger.info(
        `IntradayAnomalyMonitor: started (interval=${monitorCfg.scanIntervalSecs}s, ` +
          `priceThreshold=${(monitorCfg.priceChangeThreshold * 100).toFixed(0)}%, ` +
          `volumeMultiplier=${monitorCfg.volumeSpikeMultiplier}x)`,
      );
      interval = setInterval(() => void tick(), monitorCfg.scanIntervalSecs * 1000);
      // First scan immediately
      void tick();
    },
    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      volumeHistory.clear();
      lastFired.clear();
      api.logger.info("IntradayAnomalyMonitor: stopped");
    },
  };
}
