import { randomUUID } from "node:crypto";
/**
 * LiveStrategyRegistry — persists the set of active live trading strategies.
 *
 * Each entry specifies which strategy (by ID), which symbols, and what
 * parameters to use for the live run. The registry is written to
 * ~/.openclaw/quant-trading/live-strategies.json so it survives restarts.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { ActiveLiveStrategy, KlineInterval, QuantTradingConfig } from "../types.js";

function registryPath(cfg: QuantTradingConfig): string {
  const dir = cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "live-strategies.json");
}

async function saveAll(strategies: ActiveLiveStrategy[], cfg: QuantTradingConfig): Promise<void> {
  const p = registryPath(cfg);
  await fs.mkdir(path.dirname(p), { recursive: true });
  await fs.writeFile(p, JSON.stringify(strategies, null, 2));
}

export class LiveStrategyRegistry {
  private cfg: QuantTradingConfig;
  private strategies: ActiveLiveStrategy[] = [];

  constructor(cfg: QuantTradingConfig) {
    this.cfg = cfg;
  }

  /** Load persisted strategies from disk. Call once on startup. */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(registryPath(this.cfg), "utf-8");
      this.strategies = JSON.parse(raw) as ActiveLiveStrategy[];
    } catch {
      this.strategies = [];
    }
  }

  /** Return all registered strategies (can filter by enabled). */
  list(enabledOnly = false): ActiveLiveStrategy[] {
    return enabledOnly ? this.strategies.filter((s) => s.enabled) : [...this.strategies];
  }

  /** Add a new live strategy entry. */
  async add(
    strategyId: string,
    symbols: string[],
    options?: {
      parameters?: Record<string, unknown>;
      interval?: KlineInterval;
      maxPositionPct?: number;
      maxOrderValue?: number;
      timeWindows?: string[];
      confirmationBars?: number;
      regimes?: string[];
    },
  ): Promise<ActiveLiveStrategy> {
    const entry: ActiveLiveStrategy = {
      id: `ls-${randomUUID().slice(0, 8)}`,
      strategyId,
      symbols,
      parameters: options?.parameters,
      interval: options?.interval,
      enabled: true,
      maxPositionPct: options?.maxPositionPct,
      maxOrderValue: options?.maxOrderValue,
      addedAt: new Date().toISOString(),
      timeWindows: options?.timeWindows,
      confirmationBars: options?.confirmationBars,
      regimes: options?.regimes,
    };
    this.strategies.push(entry);
    await saveAll(this.strategies, this.cfg);
    return entry;
  }

  /** Enable or disable a live strategy without removing it. */
  async setEnabled(id: string, enabled: boolean): Promise<boolean> {
    const entry = this.strategies.find((s) => s.id === id);
    if (!entry) return false;
    entry.enabled = enabled;
    await saveAll(this.strategies, this.cfg);
    return true;
  }

  /** Remove a live strategy permanently. */
  async remove(id: string): Promise<boolean> {
    const before = this.strategies.length;
    this.strategies = this.strategies.filter((s) => s.id !== id);
    if (this.strategies.length < before) {
      await saveAll(this.strategies, this.cfg);
      return true;
    }
    return false;
  }
}
