/**
 * AccountStore — per-account isolated data directory.
 *
 * Each trading account gets its own data directory under `{dataDir}/accounts/{accountId}/`.
 * This store manages file path resolution and ensures the directory exists.
 * It also handles migration from legacy flat-file layout to the new per-account layout.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";

export interface AccountStoreOptions {
  /** Account identifier, e.g. "paper-default", "qmt-8888000001" */
  accountId: string;
  /** Base data directory (typically config.dataDir) */
  baseDataDir?: string;
}

export class AccountStore {
  readonly accountId: string;
  private readonly baseDir: string;
  private initialized = false;

  constructor(opts: AccountStoreOptions) {
    this.accountId = opts.accountId;
    this.baseDir =
      opts.baseDataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  }

  /** Root directory for this account: `{baseDir}/accounts/{accountId}/` */
  get dir(): string {
    return path.join(this.baseDir, "accounts", this.accountId);
  }

  /** Resolve a file path within this account's directory */
  resolve(filename: string): string {
    return path.join(this.dir, filename);
  }

  /** Ensure the account directory exists */
  async ensureDir(): Promise<void> {
    if (this.initialized) return;
    await fs.mkdir(this.dir, { recursive: true });
    this.initialized = true;
  }

  /** Orders file path for this account */
  get ordersPath(): string {
    return this.resolve("orders.json");
  }

  /** Trades file path for this account */
  get tradesPath(): string {
    return this.resolve("trades.json");
  }

  /** Portfolio file path for this account */
  get portfolioPath(): string {
    return this.resolve("portfolio.json");
  }

  /** Signals file path for this account */
  get signalsPath(): string {
    return this.resolve("signals.json");
  }

  /** Equity history file path for this account */
  get equityHistoryPath(): string {
    return this.resolve("equity-history.json");
  }

  /**
   * Migrate legacy flat files into this account directory.
   * Only migrates if the legacy file exists and the target does not.
   *
   * @param legacyFiles Map of legacy file paths to target filenames within the account dir
   * @returns List of files that were migrated
   */
  async migrateLegacyFiles(
    legacyFiles: Array<{ source: string; targetName: string }>,
  ): Promise<string[]> {
    await this.ensureDir();
    const migrated: string[] = [];
    for (const { source, targetName } of legacyFiles) {
      const target = this.resolve(targetName);
      try {
        await fs.access(target);
        // Target already exists — skip
        continue;
      } catch {
        // Target doesn't exist — check source
      }
      try {
        await fs.access(source);
        await fs.copyFile(source, target);
        migrated.push(`${source} → ${target}`);
      } catch {
        // Source doesn't exist — skip
      }
    }
    return migrated;
  }

  /**
   * Build legacy file migration list for the default paper account.
   * Maps old flat files (orders-paper.json, portfolio-paper.json etc.)
   * to new per-account files.
   */
  static paperMigrationFiles(baseDataDir: string): Array<{ source: string; targetName: string }> {
    return [
      { source: path.join(baseDataDir, "orders-paper.json"), targetName: "orders.json" },
      { source: path.join(baseDataDir, "trades-paper.json"), targetName: "trades.json" },
      { source: path.join(baseDataDir, "portfolio-paper.json"), targetName: "portfolio.json" },
      { source: path.join(baseDataDir, "portfolio.json"), targetName: "portfolio.json" },
      { source: path.join(baseDataDir, "signals.json"), targetName: "signals.json" },
      { source: path.join(baseDataDir, "equity-paper.json"), targetName: "equity-history.json" },
    ];
  }

  /** Build legacy file migration list for the default live account. */
  static liveMigrationFiles(baseDataDir: string): Array<{ source: string; targetName: string }> {
    return [
      { source: path.join(baseDataDir, "orders-live.json"), targetName: "orders.json" },
      { source: path.join(baseDataDir, "trades-live.json"), targetName: "trades.json" },
      { source: path.join(baseDataDir, "equity-live.json"), targetName: "equity-history.json" },
    ];
  }
}
