/**
 * DividendReminderService — background service that periodically scans
 * portfolio positions and watchlist stocks for upcoming dividend events.
 *
 * Features:
 * - Fetches dividend/ex-dividend data from AKShare via Python bridge
 * - Sends reminders 3 days and 1 day before ex-dividend dates
 * - Includes dividend plan, record date, and ex-dividend date
 * - Caches dividend data to avoid excessive API calls
 * - Persists reminder state to avoid duplicate notifications
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import type { QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";

// ── Types ────────────────────────────────────────────────────────

export interface DividendRecord {
  /** Dividend plan description */
  plan: string;
  /** 股权登记日 (record date), YYYY-MM-DD or null */
  recordDate: string | null;
  /** 除权除息日 (ex-dividend date), YYYY-MM-DD or null */
  exDate: string | null;
  /** Progress/status */
  status?: string;
}

export interface DividendReminderState {
  /** Map of "symbol:exDate:daysBeforeKey" -> ISO timestamp when notified */
  notified: Record<string, string>;
  /** Last data fetch timestamp (ISO) */
  lastFetchAt: string | null;
  /** Cached dividend data: symbol -> records */
  cache: Record<string, DividendRecord[]>;
}

export interface DividendReminderConfig {
  /** How often to check for upcoming dividends, in ms (default: 6 hours) */
  checkIntervalMs?: number;
  /** How often to refresh dividend data from AKShare, in ms (default: 24 hours) */
  dataRefreshIntervalMs?: number;
  /** Days before ex-date to send reminders (default: [3, 1]) */
  reminderDaysBefore?: number[];
  /** Enable/disable (default: true) */
  enabled?: boolean;
}

const DEFAULTS = {
  checkIntervalMs: 6 * 60 * 60 * 1000, // 6 hours
  dataRefreshIntervalMs: 24 * 60 * 60 * 1000, // 24 hours
  reminderDaysBefore: [3, 1],
  enabled: true,
};

// ── Service ──────────────────────────────────────────────────────

export function createDividendReminderService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  opts?: DividendReminderConfig,
): OpenClawPluginService {
  const checkIntervalMs = opts?.checkIntervalMs ?? DEFAULTS.checkIntervalMs;
  const dataRefreshIntervalMs = opts?.dataRefreshIntervalMs ?? DEFAULTS.dataRefreshIntervalMs;
  const reminderDays = opts?.reminderDaysBefore ?? DEFAULTS.reminderDaysBefore;
  const enabled = opts?.enabled ?? DEFAULTS.enabled;

  const dataDir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const statePath = path.join(dataDir, "dividend-reminder-state.json");
  const watchlistPath = path.join(dataDir, "watchlist.json");
  const portfolioPath = path.join(dataDir, "portfolio-paper.json");

  let timer: ReturnType<typeof setInterval> | null = null;
  let state: DividendReminderState = { notified: {}, lastFetchAt: null, cache: {} };

  // ── State persistence ─────────────────────────────────────────

  function loadState(): void {
    try {
      if (existsSync(statePath)) {
        const raw = readFileSync(statePath, "utf-8");
        state = JSON.parse(raw) as DividendReminderState;
      }
    } catch {
      state = { notified: {}, lastFetchAt: null, cache: {} };
    }
  }

  function saveState(): void {
    try {
      const dir = path.dirname(statePath);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(statePath, JSON.stringify(state, null, 2), "utf-8");
    } catch {
      // best-effort
    }
  }

  // ── Gather target symbols ─────────────────────────────────────

  async function getTargetSymbols(): Promise<string[]> {
    const symbols = new Set<string>();

    // From watchlist
    try {
      const raw = await fs.readFile(watchlistPath, "utf-8");
      const list = JSON.parse(raw) as string[];
      for (const code of list) symbols.add(code.trim().toUpperCase());
    } catch {
      // No watchlist
    }

    // From paper portfolio positions
    try {
      const raw = await fs.readFile(portfolioPath, "utf-8");
      const portfolio = JSON.parse(raw) as { positions?: Array<{ symbol: string }> };
      if (portfolio.positions) {
        for (const pos of portfolio.positions) {
          symbols.add(pos.symbol.trim().toUpperCase());
        }
      }
    } catch {
      // No portfolio
    }

    return [...symbols];
  }

  // ── Fetch dividend data via Python bridge ─────────────────────

  async function fetchDividendData(symbols: string[]): Promise<Record<string, DividendRecord[]>> {
    if (symbols.length === 0) return {};

    try {
      const { runPythonScript } = await import("../bridge/python-bridge.js");

      // Batch symbols in groups of 10 to avoid overloading
      const batchSize = 10;
      const allResults: Record<string, DividendRecord[]> = {};

      for (let i = 0; i < symbols.length; i += batchSize) {
        const batch = symbols.slice(i, i + batchSize);
        const result = await runPythonScript<{ data: Record<string, DividendRecord[]> }>(
          "engines/dividend_engine.py",
          { action: "query", symbols: batch },
          config,
          { timeoutMs: 60_000 },
        );

        if (result.ok && result.data?.data) {
          for (const [sym, records] of Object.entries(result.data.data)) {
            allResults[sym] = records.filter(
              (r: DividendRecord & { error?: string }) => !r.error,
            );
          }
        }
      }

      return allResults;
    } catch {
      return {};
    }
  }

  // ── Check and send reminders ──────────────────────────────────

  function checkReminders(): void {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayMs = today.getTime();
    const msPerDay = 24 * 60 * 60 * 1000;

    const reminders: Array<{
      symbol: string;
      daysBefore: number;
      record: DividendRecord;
    }> = [];

    for (const [symbol, records] of Object.entries(state.cache)) {
      for (const record of records) {
        if (!record.exDate) continue;

        const exDate = new Date(record.exDate);
        exDate.setHours(0, 0, 0, 0);
        const diffDays = Math.round((exDate.getTime() - todayMs) / msPerDay);

        for (const daysBefore of reminderDays) {
          if (diffDays === daysBefore) {
            const notifyKey = `${symbol}:${record.exDate}:d-${daysBefore}`;
            if (!state.notified[notifyKey]) {
              reminders.push({ symbol, daysBefore, record });
              state.notified[notifyKey] = new Date().toISOString();
            }
          }
        }
      }
    }

    if (reminders.length > 0) {
      const lines: string[] = ["📋 分红除权提醒\n"];

      for (const { symbol, daysBefore, record } of reminders) {
        const urgency = daysBefore <= 1 ? "⚠️" : "📅";
        lines.push(
          `${urgency} ${symbol} — ${daysBefore === 0 ? "今日" : `${daysBefore}天后`}除权除息`,
        );
        lines.push(`   方案: ${record.plan}`);
        if (record.recordDate) lines.push(`   股权登记日: ${record.recordDate}`);
        if (record.exDate) lines.push(`   除权除息日: ${record.exDate}`);
        if (record.status) lines.push(`   进度: ${record.status}`);
        lines.push("");
      }

      void tryNotify(api, lines.join("\n"));
      saveState();
    }
  }

  // ── Main tick ─────────────────────────────────────────────────

  async function tick(): Promise<void> {
    try {
      const symbols = await getTargetSymbols();
      if (symbols.length === 0) return;

      // Check if we need to refresh data
      const now = Date.now();
      const lastFetch = state.lastFetchAt ? new Date(state.lastFetchAt).getTime() : 0;
      const needsRefresh = now - lastFetch > dataRefreshIntervalMs;

      if (needsRefresh) {
        const data = await fetchDividendData(symbols);
        if (Object.keys(data).length > 0) {
          state.cache = data;
          state.lastFetchAt = new Date().toISOString();
          // Clean up old notifications (older than 30 days)
          const cutoff = new Date(now - 30 * 24 * 60 * 60 * 1000).toISOString();
          for (const [key, ts] of Object.entries(state.notified)) {
            if (ts < cutoff) delete state.notified[key];
          }
          saveState();
        }
      }

      checkReminders();
    } catch {
      // Best-effort — do not crash the service
    }
  }

  // ── Service interface ─────────────────────────────────────────

  return {
    id: "dividend-reminder",
    async start() {
      if (!enabled) return;
      loadState();
      // Run first check after a short delay to not block startup
      setTimeout(() => void tick(), 30_000);
      timer = setInterval(() => void tick(), checkIntervalMs);
    },
    async stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      saveState();
    },
  };
}
