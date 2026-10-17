/**
 * Composed Strategy Store — persist dynamically composed strategies to disk.
 *
 * Saves to `~/data/composed-strategies.json`.
 * On load, re-registers all saved compositions into the strategy registry.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { CompositionConfig } from "../engine/dynamic-composer.js";
import {
  registerComposedStrategy,
  unregisterComposedStrategy,
} from "../engine/strategy-registry.js";
import { nowCSTISO } from "../utils/cst-time.js";

export class ComposedStrategyStore {
  private compositions = new Map<string, CompositionConfig>();
  private filePath: string;

  constructor(dataDir?: string) {
    const base = dataDir ?? path.join(process.cwd(), "data");
    this.filePath = path.join(base, "composed-strategies.json");
  }

  /** Load persisted compositions and register them in the strategy registry. */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      const arr = JSON.parse(raw) as CompositionConfig[];
      for (const config of arr) {
        this.compositions.set(config.id, config);
        registerComposedStrategy(config);
      }
    } catch {
      // File doesn't exist yet — start empty
    }
  }

  /** Save current compositions to disk. */
  private async save(): Promise<void> {
    const arr = [...this.compositions.values()];
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(arr, null, 2));
  }

  /** Create or update a composed strategy. */
  async upsert(config: CompositionConfig): Promise<void> {
    const now = nowCSTISO();
    const existing = this.compositions.get(config.id);
    config.createdAt = existing?.createdAt ?? now;
    config.updatedAt = now;
    this.compositions.set(config.id, config);
    registerComposedStrategy(config);
    await this.save();
  }

  /** Delete a composed strategy. */
  async delete(id: string): Promise<boolean> {
    const had = this.compositions.delete(id);
    if (had) {
      unregisterComposedStrategy(id);
      await this.save();
    }
    return had;
  }

  /** Get a single composition. */
  get(id: string): CompositionConfig | undefined {
    return this.compositions.get(id);
  }

  /** List all compositions. */
  list(): CompositionConfig[] {
    return [...this.compositions.values()];
  }
}
