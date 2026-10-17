/**
 * Custom (Rule-based) Strategy Store — persist user-defined rule strategies to disk.
 *
 * Follows the same pattern as ComposedStrategyStore / LiveStrategyRegistry.
 * Saves to `~/data/custom-strategies.json`.
 * On load, registers each saved rule strategy into the strategy registry.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { RuleStrategyConfig } from "../engine/rule-strategy.js";
import { registerRuleStrategy, unregisterRuleStrategy } from "../engine/strategy-registry.js";
import { nowCSTISO } from "../utils/cst-time.js";

export class CustomStrategyStore {
  private strategies = new Map<string, RuleStrategyConfig>();
  private filePath: string;

  constructor(dataDir?: string) {
    const base = dataDir ?? path.join(process.cwd(), "data");
    this.filePath = path.join(base, "custom-strategies.json");
  }

  /** Load persisted custom strategies and register them in the strategy registry. */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      const arr = JSON.parse(raw) as RuleStrategyConfig[];
      for (const config of arr) {
        this.strategies.set(config.id, config);
        registerRuleStrategy(config);
      }
    } catch {
      // File doesn't exist yet — start empty
    }
  }

  /** Save current strategies to disk. */
  private async save(): Promise<void> {
    const arr = [...this.strategies.values()];
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(arr, null, 2));
  }

  /** Create or update a custom strategy. */
  async upsert(config: RuleStrategyConfig): Promise<void> {
    const now = nowCSTISO();
    const existing = this.strategies.get(config.id);
    config.createdAt = existing?.createdAt ?? now;
    config.updatedAt = now;
    this.strategies.set(config.id, config);
    registerRuleStrategy(config);
    await this.save();
  }

  /** Delete a custom strategy. */
  async delete(id: string): Promise<boolean> {
    const had = this.strategies.delete(id);
    if (had) {
      unregisterRuleStrategy(id);
      await this.save();
    }
    return had;
  }

  /** Get a single strategy config. */
  get(id: string): RuleStrategyConfig | undefined {
    return this.strategies.get(id);
  }

  /** List all custom strategies. */
  list(): RuleStrategyConfig[] {
    return [...this.strategies.values()];
  }
}
