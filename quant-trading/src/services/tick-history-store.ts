import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type { AppService } from "../runtime.js";
import type { KlineBar, TickAggregationInterval, TickData, TickFeatures } from "../types.js";

type TickHistoryStoreConfig = {
  persistPath?: string | null;
  autoSaveIntervalMs?: number;
  maxTicksPerSymbol?: number;
  maxBarsPerSeries?: number;
  maxFeaturesPerSymbol?: number;
};

type TimeRange = {
  from?: number;
  to?: number;
};

type PersistedState = {
  ticksBySymbol: Record<string, TickData[]>;
  barsBySeries: Record<string, KlineBar[]>;
  featuresBySymbol: Record<string, TickFeatures[]>;
};

const DEFAULTS: Required<TickHistoryStoreConfig> = {
  persistPath: null,
  autoSaveIntervalMs: 60_000,
  maxTicksPerSymbol: 2000,
  maxBarsPerSeries: 2000,
  maxFeaturesPerSymbol: 2000,
};

function filterByTime<T>(rows: T[], getTs: (row: T) => number, range?: TimeRange): T[] {
  if (!range || (range.from == null && range.to == null)) return rows;
  const from = range.from ?? Number.NEGATIVE_INFINITY;
  const to = range.to ?? Number.POSITIVE_INFINITY;
  return rows.filter((row) => {
    const ts = getTs(row);
    return ts >= from && ts <= to;
  });
}

export class TickHistoryStore implements AppService {
  readonly id = "tick-history-store";

  private readonly config: Required<TickHistoryStoreConfig>;
  private readonly ticksBySymbol = new Map<string, TickData[]>();
  private readonly barsBySeries = new Map<string, KlineBar[]>();
  private readonly featuresBySymbol = new Map<string, TickFeatures[]>();
  private interval: ReturnType<typeof setInterval> | null = null;
  private dirty = false;

  constructor(config?: TickHistoryStoreConfig) {
    this.config = { ...DEFAULTS, ...(config ?? {}) };
  }

  async start(): Promise<void> {
    this.loadFromDisk();
    if (this.config.persistPath && this.config.autoSaveIntervalMs > 0) {
      this.interval = setInterval(() => this.saveToDisk(), this.config.autoSaveIntervalMs);
    }
  }

  async stop(): Promise<void> {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
    this.saveToDisk();
  }

  appendTick(tick: TickData): void {
    const key = tick.symbol.code;
    const list = this.ticksBySymbol.get(key) ?? [];
    list.push(tick);
    if (list.length > this.config.maxTicksPerSymbol) {
      list.splice(0, list.length - this.config.maxTicksPerSymbol);
    }
    this.ticksBySymbol.set(key, list);
    this.dirty = true;
  }

  appendBar(symbol: string, interval: TickAggregationInterval, bar: KlineBar): void {
    const key = `${symbol}:${interval}`;
    const list = this.barsBySeries.get(key) ?? [];
    list.push(bar);
    if (list.length > this.config.maxBarsPerSeries) {
      list.splice(0, list.length - this.config.maxBarsPerSeries);
    }
    this.barsBySeries.set(key, list);
    this.dirty = true;
  }

  appendFeature(feature: TickFeatures): void {
    const key = feature.symbol;
    const list = this.featuresBySymbol.get(key) ?? [];
    list.push(feature);
    if (list.length > this.config.maxFeaturesPerSymbol) {
      list.splice(0, list.length - this.config.maxFeaturesPerSymbol);
    }
    this.featuresBySymbol.set(key, list);
    this.dirty = true;
  }

  getRecentTicks(symbol: string, limit = 200, range?: TimeRange): TickData[] {
    const rows = this.ticksBySymbol.get(symbol) ?? [];
    const filtered = filterByTime(rows, (row) => row.timestamp, range);
    return filtered.slice(-Math.max(1, Math.floor(limit)));
  }

  getRecentBars(
    symbol: string,
    interval: TickAggregationInterval,
    limit = 200,
    range?: TimeRange,
  ): KlineBar[] {
    const rows = this.barsBySeries.get(`${symbol}:${interval}`) ?? [];
    const filtered = filterByTime(rows, (row) => row.timestamp, range);
    return filtered.slice(-Math.max(1, Math.floor(limit)));
  }

  getRecentFeatures(symbol: string, limit = 200, range?: TimeRange): TickFeatures[] {
    const rows = this.featuresBySymbol.get(symbol) ?? [];
    const filtered = filterByTime(rows, (row) => row.timestamp, range);
    return filtered.slice(-Math.max(1, Math.floor(limit)));
  }

  getLatestFeatures(limit = 200, range?: TimeRange): TickFeatures[] {
    const latest: TickFeatures[] = [];
    for (const rows of this.featuresBySymbol.values()) {
      if (rows.length === 0) continue;
      const list = filterByTime(rows, (row) => row.timestamp, range);
      if (list.length > 0) latest.push(list[list.length - 1]!);
    }
    latest.sort((a, b) => a.timestamp - b.timestamp);
    return latest.slice(-Math.max(1, Math.floor(limit)));
  }

  private loadFromDisk(): void {
    if (!this.config.persistPath) return;
    try {
      if (!existsSync(this.config.persistPath)) return;
      const raw = readFileSync(this.config.persistPath, "utf-8");
      const state = JSON.parse(raw) as Partial<PersistedState>;

      this.ticksBySymbol.clear();
      this.barsBySeries.clear();
      this.featuresBySymbol.clear();

      for (const [symbol, rows] of Object.entries(state.ticksBySymbol ?? {})) {
        this.ticksBySymbol.set(symbol, Array.isArray(rows) ? rows : []);
      }
      for (const [key, rows] of Object.entries(state.barsBySeries ?? {})) {
        this.barsBySeries.set(key, Array.isArray(rows) ? rows : []);
      }
      for (const [symbol, rows] of Object.entries(state.featuresBySymbol ?? {})) {
        this.featuresBySymbol.set(symbol, Array.isArray(rows) ? rows : []);
      }

      this.dirty = false;
    } catch {
      // ignore bad history file; continue with empty state
    }
  }

  private saveToDisk(): void {
    if (!this.config.persistPath || !this.dirty) return;
    try {
      mkdirSync(dirname(this.config.persistPath), { recursive: true });
      const state: PersistedState = {
        ticksBySymbol: Object.fromEntries(this.ticksBySymbol.entries()),
        barsBySeries: Object.fromEntries(this.barsBySeries.entries()),
        featuresBySymbol: Object.fromEntries(this.featuresBySymbol.entries()),
      };
      writeFileSync(this.config.persistPath, JSON.stringify(state));
      this.dirty = false;
    } catch {
      // keep dirty=true, retry next cycle
    }
  }
}
