/**
 * Exploration History Store — persist AI strategy exploration results to disk.
 *
 * Saves to `<dataDir>/exploration-history.json` (default `~/data/`).
 * Keeps the most recent N explorations (default 50) and auto-prunes older ones.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { BatchBacktestResultItem } from "./batch-backtest.js";

export interface ExplorationRecord {
  id: string;
  createdAt: string;
  /** Input parameters */
  params: {
    symbols: string[];
    startDate: string;
    endDate: string;
    initialCash: number;
    candidateCount: number;
    minStrategies: number;
    maxStrategies: number;
  };
  /** Whether AI or fallback was used */
  source: "ai" | "fallback";
  /** AI candidate info (name, method, rationale, sub-strategies) */
  candidates: Array<{
    name: string;
    method: string;
    rationale?: string;
    strategies: Array<{ strategyId: string; weight?: number; role?: string }>;
    minVotes?: number;
  }>;
  /** Backtest summary */
  totalRuns: number;
  successCount: number;
  failCount: number;
  ranked: BatchBacktestResultItem[];
  results: BatchBacktestResultItem[];
  bestStrategy: string | null;
  bestCompositeScore: number;
}

const MAX_RECORDS = 50;

export class ExplorationHistoryStore {
  private records: ExplorationRecord[] = [];
  private filePath: string;

  constructor(dataDir?: string) {
    const base = dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.filePath = path.join(base, "exploration-history.json");
  }

  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      this.records = JSON.parse(raw) as ExplorationRecord[];
    } catch {
      // File doesn't exist yet — start empty
    }
  }

  private async save(): Promise<void> {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(this.records, null, 2));
  }

  /** Add a new exploration record and prune old ones. */
  async add(record: ExplorationRecord): Promise<void> {
    this.records.unshift(record);
    if (this.records.length > MAX_RECORDS) {
      this.records = this.records.slice(0, MAX_RECORDS);
    }
    await this.save();
  }

  /** List all records (newest first). */
  list(): ExplorationRecord[] {
    return this.records;
  }

  /** Get a single record by ID. */
  get(id: string): ExplorationRecord | undefined {
    return this.records.find((r) => r.id === id);
  }

  /** Delete a record by ID. */
  async delete(id: string): Promise<boolean> {
    const idx = this.records.findIndex((r) => r.id === id);
    if (idx < 0) return false;
    this.records.splice(idx, 1);
    await this.save();
    return true;
  }
}
