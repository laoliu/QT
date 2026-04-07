/**
 * AccountManager — multi-account orchestration layer.
 *
 * Manages multiple trading accounts (paper + live broker accounts).
 * Each account gets its own AccountStore (isolated data directory),
 * AccountContext (scoped config), and optionally a dedicated LiveEngine
 * or broker adapter.
 *
 * Responsibilities:
 *  - Register / unregister accounts from config
 *  - Track which account is "active" (the one the UI / AI tools operate on)
 *  - Connect / disconnect all accounts at startup / shutdown
 *  - Provide the active AccountContext to routes and services
 *  - Handle legacy data migration on first run
 */
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { IBrokerAdapter } from "../broker/adapter.js";
import { createBrokerAdapter } from "../broker/index.js";
import type {
  AccountConfig,
  BrokerType,
  QuantTradingConfig,
  TradingAccountConfig,
} from "../types.js";
import { AccountContext, type AccountInfo } from "./account-context.js";
import { AccountStore } from "./account-store.js";

export class AccountManager {
  private accounts = new Map<string, AccountContext>();
  private brokers = new Map<string, IBrokerAdapter>();
  private activeId: string;
  private readonly config: QuantTradingConfig;
  private readonly api: OpenClawPluginApi;

  constructor(config: QuantTradingConfig, api: OpenClawPluginApi) {
    this.config = config;
    this.api = api;
    this.activeId = config.activeAccountId ?? "paper-default";
  }

  /** The currently active account ID */
  get activeAccountId(): string {
    return this.activeId;
  }

  /** Get the active AccountContext */
  get active(): AccountContext | undefined {
    return this.accounts.get(this.activeId);
  }

  /** Get all registered account IDs */
  get accountIds(): string[] {
    return [...this.accounts.keys()];
  }

  /** Switch the active account */
  switchTo(accountId: string): boolean {
    if (!this.accounts.has(accountId)) return false;
    this.activeId = accountId;
    this.api.logger.info(`[AccountManager] 已切换到账户: ${accountId}`);
    return true;
  }

  /**
   * Register a single account.
   * Creates an AccountStore + AccountContext and stores them.
   */
  register(accountId: string, tradingConfig: TradingAccountConfig): AccountContext {
    const baseDir = this.config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    const store = new AccountStore({ accountId, baseDataDir: baseDir });
    const ctx = new AccountContext(accountId, store, tradingConfig, this.config);
    this.accounts.set(accountId, ctx);
    this.api.logger.info(`[AccountManager] 已注册账户: ${accountId} (${ctx.displayName})`);
    return ctx;
  }

  /** Unregister an account */
  unregister(accountId: string): boolean {
    const existed = this.accounts.delete(accountId);
    this.brokers.delete(accountId);
    if (accountId === this.activeId && this.accounts.size > 0) {
      this.activeId = this.accounts.keys().next().value!;
    }
    return existed;
  }

  /**
   * Register all accounts from config.accounts[].
   * If no accounts are configured, creates a default paper account
   * from the legacy tradingAccount config.
   */
  registerFromConfig(): void {
    const cfgAccounts = this.config.accounts;
    if (cfgAccounts && cfgAccounts.length > 0) {
      for (const acct of cfgAccounts) {
        this.register(acct.id, acct);
      }
    } else {
      // Fallback: create default paper account from legacy tradingAccount
      const legacyConfig: TradingAccountConfig = this.config.tradingAccount ?? { mode: "paper" };
      this.register("paper-default", legacyConfig);

      // If legacy config has a live broker, also register it
      if (legacyConfig.mode === "live" && legacyConfig.broker && legacyConfig.broker !== "paper") {
        const liveId = `${legacyConfig.broker}-${legacyConfig.accountId ?? "default"}`;
        this.register(liveId, legacyConfig);
      }
    }

    // Set active account from config
    if (this.config.activeAccountId && this.accounts.has(this.config.activeAccountId)) {
      this.activeId = this.config.activeAccountId;
    }
  }

  /**
   * Create and connect broker adapters for all registered accounts.
   * Logs errors per-account but does not throw (best-effort connect).
   */
  async connectAll(): Promise<void> {
    const promises = [...this.accounts.entries()].map(async ([id, ctx]) => {
      try {
        const scopedConfig = ctx.toScopedConfig();
        const broker = createBrokerAdapter(scopedConfig);
        await broker.connect();
        this.brokers.set(id, broker);
        this.api.logger.info(`[AccountManager] 账户 ${id} 已连接 (${broker.brokerType})`);
      } catch (err) {
        this.api.logger.warn(
          `[AccountManager] 账户 ${id} 连接失败: ${(err as Error).message}`,
        );
      }
    });
    await Promise.allSettled(promises);
  }

  /** Disconnect all broker adapters */
  async disconnectAll(): Promise<void> {
    const promises = [...this.brokers.entries()].map(async ([id, broker]) => {
      try {
        await broker.disconnect();
        this.api.logger.info(`[AccountManager] 账户 ${id} 已断开`);
      } catch (err) {
        this.api.logger.warn(
          `[AccountManager] 账户 ${id} 断开失败: ${(err as Error).message}`,
        );
      }
    });
    await Promise.allSettled(promises);
    this.brokers.clear();
  }

  /** Get the broker adapter for a specific account */
  getBroker(accountId: string): IBrokerAdapter | undefined {
    return this.brokers.get(accountId);
  }

  /** Get the broker adapter for the active account */
  get activeBroker(): IBrokerAdapter | undefined {
    return this.brokers.get(this.activeId);
  }

  /** Get an AccountContext by ID */
  getContext(accountId: string): AccountContext | undefined {
    return this.accounts.get(accountId);
  }

  /** List all accounts as AccountInfo snapshots */
  listAccounts(): AccountInfo[] {
    return [...this.accounts.entries()].map(([id, ctx]) => {
      const broker = this.brokers.get(id);
      return ctx.toInfo(id === this.activeId, broker?.connected ?? false);
    });
  }

  /**
   * Migrate legacy data files into per-account directories.
   * Called once at startup. Only migrates if the target directory is empty.
   */
  async migrateLegacyData(): Promise<void> {
    const baseDir = this.config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");

    for (const [id, ctx] of this.accounts) {
      const store = ctx.store;
      let migrationFiles: Array<{ source: string; targetName: string }>;

      if (ctx.mode === "paper") {
        migrationFiles = AccountStore.paperMigrationFiles(baseDir);
      } else {
        migrationFiles = AccountStore.liveMigrationFiles(baseDir);
      }

      const migrated = await store.migrateLegacyFiles(migrationFiles);
      if (migrated.length > 0) {
        this.api.logger.info(
          `[AccountManager] 账户 ${id} 已迁移 ${migrated.length} 个旧数据文件`,
        );
        for (const m of migrated) {
          this.api.logger.info(`  ${m}`);
        }
      }
    }
  }
}
