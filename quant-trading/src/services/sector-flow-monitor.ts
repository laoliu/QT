/**
 * SectorFlowMonitor — background service that monitors sector-level fund flow
 * anomalies during market hours.
 *
 * Every 15 minutes (during CN market hours):
 *  1. Calls fund_flow(action=sector) via the Python bridge to get sector flow ranking.
 *  2. Stores daily sector flow data in SQLite (or in-memory) for the last 5 trading days.
 *  3. Detects anomalies:
 *     a. Sectors with consecutive N-day large net inflow or outflow.
 *     b. Single-day abnormal inflow exceeding 2x the 20-day moving average.
 *  4. Pushes alerts via tryNotify on anomaly detection.
 */
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { runPythonScript } from "../bridge/python-bridge.js";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { QuantTradingConfig } from "../types.js";
import type { EventBus } from "./event-bus.js";
import type { AlertStore } from "./alert-store.js";
import { tryNotify } from "./notify-helper.js";

// ── Types ────────────────────────────────────────────────────

export interface SectorFlowRecord {
  /** Sector name (板块名称) */
  sector: string;
  /** Date string YYYY-MM-DD */
  date: string;
  /** Net inflow in yuan (主力净流入) */
  netInflow: number;
  /** Timestamp when recorded */
  recordedAt: string;
}

export interface SectorFlowAnomaly {
  type: "consecutive_inflow" | "consecutive_outflow" | "abnormal_inflow";
  sector: string;
  /** Number of consecutive days (for consecutive type) */
  days?: number;
  /** Current day net inflow */
  netInflow: number;
  /** 20-day average (for abnormal type) */
  avg20d?: number;
  /** Ratio of current inflow to 20-day average */
  ratio?: number;
  message: string;
}

// ── Persistence (JSON file fallback) ─────────────────────────

interface SectorFlowStore {
  /** date → sector → SectorFlowRecord */
  records: Record<string, Record<string, SectorFlowRecord>>;
}

function getStorePath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "sector-flow-history.json");
}

async function loadStore(config: QuantTradingConfig): Promise<SectorFlowStore> {
  try {
    const raw = await fs.readFile(getStorePath(config), "utf-8");
    return JSON.parse(raw) as SectorFlowStore;
  } catch {
    return { records: {} };
  }
}

async function saveStore(store: SectorFlowStore, config: QuantTradingConfig): Promise<void> {
  const filePath = getStorePath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(store, null, 2));
}

/** Prune store to keep only the last `keepDays` trading days. */
function pruneStore(store: SectorFlowStore, keepDays: number): void {
  const dates = Object.keys(store.records).sort();
  while (dates.length > keepDays) {
    const oldest = dates.shift()!;
    delete store.records[oldest];
  }
}

// ── Anomaly detection ────────────────────────────────────────

/** Default: consider 3 consecutive days as noteworthy. */
const DEFAULT_CONSECUTIVE_DAYS = 3;
/** Default: alert when single-day inflow exceeds 2x the 20-day average. */
const DEFAULT_ABNORMAL_RATIO = 2.0;
/** How many historical days to keep for analysis. */
const HISTORY_DAYS = 25; // 20 trading days + buffer

function detectAnomalies(
  store: SectorFlowStore,
  today: string,
  consecutiveDays: number = DEFAULT_CONSECUTIVE_DAYS,
  abnormalRatio: number = DEFAULT_ABNORMAL_RATIO,
): SectorFlowAnomaly[] {
  const anomalies: SectorFlowAnomaly[] = [];
  const dates = Object.keys(store.records).sort();
  const todayIdx = dates.indexOf(today);
  if (todayIdx < 0) return anomalies;

  // Collect all sector names seen today
  const todaySectors = store.records[today];
  if (!todaySectors) return anomalies;

  for (const [sector, record] of Object.entries(todaySectors)) {
    // ── 1. Consecutive inflow/outflow detection ──
    let consecutiveInflow = 0;
    let consecutiveOutflow = 0;
    // Walk backwards from today
    for (let i = todayIdx; i >= 0; i--) {
      const dateRecord = store.records[dates[i]!];
      const sectorRecord = dateRecord?.[sector];
      if (!sectorRecord) break;

      if (sectorRecord.netInflow > 0) {
        if (consecutiveOutflow > 0) break; // direction changed
        consecutiveInflow++;
      } else if (sectorRecord.netInflow < 0) {
        if (consecutiveInflow > 0) break; // direction changed
        consecutiveOutflow++;
      } else {
        break;
      }
    }

    if (consecutiveInflow >= consecutiveDays) {
      const totalInflow = computeTotalForDays(store, dates, todayIdx, sector, consecutiveInflow);
      anomalies.push({
        type: "consecutive_inflow",
        sector,
        days: consecutiveInflow,
        netInflow: record.netInflow,
        message:
          `🔴 板块资金异动: ${sector} 连续${consecutiveInflow}日主力净流入，` +
          `今日净流入 ${formatAmount(record.netInflow)}，` +
          `累计净流入 ${formatAmount(totalInflow)}`,
      });
    }

    if (consecutiveOutflow >= consecutiveDays) {
      const totalOutflow = computeTotalForDays(store, dates, todayIdx, sector, consecutiveOutflow);
      anomalies.push({
        type: "consecutive_outflow",
        sector,
        days: consecutiveOutflow,
        netInflow: record.netInflow,
        message:
          `🟢 板块资金异动: ${sector} 连续${consecutiveOutflow}日主力净流出，` +
          `今日净流出 ${formatAmount(Math.abs(record.netInflow))}，` +
          `累计净流出 ${formatAmount(Math.abs(totalOutflow))}`,
      });
    }

    // ── 2. Single-day abnormal large inflow ──
    if (record.netInflow > 0) {
      const historicalValues: number[] = [];
      for (let i = todayIdx - 1; i >= 0 && historicalValues.length < 20; i--) {
        const dateRecord = store.records[dates[i]!];
        const sectorRecord = dateRecord?.[sector];
        if (sectorRecord) {
          historicalValues.push(sectorRecord.netInflow);
        }
      }

      if (historicalValues.length >= 5) {
        // Use absolute values of inflows for average calculation
        const avgAbsFlow =
          historicalValues.reduce((sum, v) => sum + Math.abs(v), 0) / historicalValues.length;
        if (avgAbsFlow > 0) {
          const ratio = record.netInflow / avgAbsFlow;
          if (ratio >= abnormalRatio) {
            anomalies.push({
              type: "abnormal_inflow",
              sector,
              netInflow: record.netInflow,
              avg20d: avgAbsFlow,
              ratio,
              message:
                `⚡ 板块资金异动: ${sector} 今日主力净流入 ${formatAmount(record.netInflow)}，` +
                `是近${historicalValues.length}日均值的 ${ratio.toFixed(1)} 倍（均值 ${formatAmount(avgAbsFlow)}）`,
            });
          }
        }
      }
    }
  }

  return anomalies;
}

function computeTotalForDays(
  store: SectorFlowStore,
  dates: string[],
  endIdx: number,
  sector: string,
  numDays: number,
): number {
  let total = 0;
  for (let i = endIdx; i > endIdx - numDays && i >= 0; i--) {
    const dateRecord = store.records[dates[i]!];
    const sectorRecord = dateRecord?.[sector];
    if (sectorRecord) total += sectorRecord.netInflow;
  }
  return total;
}

function formatAmount(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(value / 1e4).toFixed(0)}万`;
  return `${value.toFixed(0)}元`;
}

// ── Service factory ──────────────────────────────────────────

export function createSectorFlowMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  eventBus?: EventBus,
  alertStore?: AlertStore,
): OpenClawPluginService {
  let interval: ReturnType<typeof setInterval> | null = null;

  return {
    id: "quant-sector-flow-monitor",
    async start() {
      api.logger.info("SectorFlowMonitor service started");

      const tick = async () => {
        if (!isMarketOpen()) {
          api.logger.debug?.("SectorFlowMonitor: market closed, skipping");
          return;
        }

        try {
          // 1. Fetch sector fund flow via Python bridge
          const pyResult = await runPythonScript<{
            action?: string;
            sectors?: Array<Record<string, unknown>>;
            count?: number;
            error?: string;
          }>("engines/fund_flow.py", { action: "sector", limit: 50 }, config);

          if (!pyResult.ok || !pyResult.data?.sectors || pyResult.data.sectors.length === 0) {
            api.logger.debug?.(
              `SectorFlowMonitor: no sector data — ${pyResult.error ?? pyResult.data?.error ?? "empty response"}`,
            );
            return;
          }

          const sectors = pyResult.data.sectors;
          const today = new Date().toISOString().slice(0, 10);

          // 2. Load existing store and add today's data
          const store = await loadStore(config);
          if (!store.records[today]) {
            store.records[today] = {};
          }

          for (const s of sectors) {
            const sectorName = (s.name ?? s.sector ?? s.板块名称 ?? "") as string;
            if (!sectorName) continue;

            // Try multiple possible field names for net inflow
            const netInflow =
              ((s.net_inflow ?? s.netInflow ?? s.主力净流入 ?? s.净流入 ?? 0) as number) || 0;

            store.records[today]![sectorName] = {
              sector: sectorName,
              date: today,
              netInflow,
              recordedAt: new Date().toISOString(),
            };
          }

          // Prune old data
          pruneStore(store, HISTORY_DAYS);

          // Save updated store
          await saveStore(store, config);

          // 3. Detect anomalies
          const anomalies = detectAnomalies(store, today);

          if (anomalies.length > 0) {
            api.logger.info(
              `SectorFlowMonitor: detected ${anomalies.length} anomaly(ies)`,
            );

            // 4. Send notifications
            for (const anomaly of anomalies) {
              api.logger.info(`SectorFlowMonitor: ${anomaly.message}`);

              // Persist to AlertStore
              alertStore?.add("info", "sector_flow", anomaly.message, {
                type: anomaly.type,
                sector: anomaly.sector,
                netInflow: anomaly.netInflow,
                days: anomaly.days,
                ratio: anomaly.ratio,
              });

              // Emit to EventBus for SSE dashboard
              eventBus?.emit("sector_flow_anomaly", {
                type: anomaly.type,
                sector: anomaly.sector,
                netInflow: anomaly.netInflow,
                days: anomaly.days,
                ratio: anomaly.ratio,
                message: anomaly.message,
                source: "sector-flow-monitor",
              });
            }

            // Batch notification to user
            const header = `📊 板块资金异动通报 (${anomalies.length}条)`;
            const body = anomalies.map((a) => a.message).join("\n");
            await tryNotify(api, `${header}\n\n${body}`);
          } else {
            api.logger.debug?.("SectorFlowMonitor: no anomalies detected");
          }
        } catch (err) {
          api.logger.error(`SectorFlowMonitor error: ${(err as Error).message}`);
        }
      };

      // Run every 15 minutes
      interval = setInterval(tick, 15 * 60 * 1000);
      // Also run once shortly after start (5s delay to let other services initialize)
      setTimeout(() => void tick(), 5_000);
    },

    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("SectorFlowMonitor service stopped");
    },
  };
}
