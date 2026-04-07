/**
 * AccountManager & AccountContext — multi-account management.
 *
 * Manages multiple trading accounts and provides a context for the
 * currently active account. Supports listing all accounts with their
 * status and an aggregate view across accounts.
 *
 * Works with both single-account (`tradingAccount`) and multi-account
 * (`tradingAccounts`) configurations.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { IBrokerAdapter } from "../broker/adapter.js";
import { createBrokerAdapter } from "../broker/index.js";
import type {
  BrokerAccount,
  LivePosition,
  QuantTradingConfig,
  TradingAccountConfig,
} from "../types.js";

// ── Types ───────────────────────────────────────────────────────

export type AccountEntry = {
  id: string;
  label: string;
  config: TradingAccountConfig;
  broker: IBrokerAdapter;
};

export type AccountStatus = {
  id: string;
  label: string;
  mode: "paper" | "live";
  brokerType: string;
  connected: boolean;
  account: BrokerAccount | null;
  positions: LivePosition[];
  pendingOrders: number;
};

export type AggregateView = {
  totalCash: number;
  totalMarketValue: number;
  totalAssets: number;
  totalProfit: number;
  accountCount: number;
};

// ── AccountContext ────────────────────────────────────────────────

export class AccountContext {
  private currentId: string;
  private manager: AccountManager;

  constructor(manager: AccountManager, defaultId: string) {
    this.manager = manager;
    this.currentId = defaultId;
  }

  get activeAccountId(): string {
    return this.currentId;
  }

  getActiveEntry(): AccountEntry | undefined {
    return this.manager.getAccount(this.currentId);
  }

  /**
   * Switch to a different account.
   * Returns the switched-to account entry, or null if not found.
   */
  switch(accountId: string): AccountEntry | null {
    const entry = this.manager.getAccount(accountId);
    if (!entry) return null;
    this.currentId = accountId;
    return entry;
  }
}

// ── AccountManager ───────────────────────────────────────────────

export class AccountManager {
  private accounts: Map<string, AccountEntry> = new Map();
  private api: OpenClawPluginApi;

  constructor(api: OpenClawPluginApi, config: QuantTradingConfig) {
    this.api = api;

    // Build account entries from config
    if (config.tradingAccounts && Object.keys(config.tradingAccounts).length > 0) {
      for (const [id, acctCfg] of Object.entries(config.tradingAccounts)) {
        const mergedConfig: QuantTradingConfig = { ...config, tradingAccount: acctCfg };
        this.accounts.set(id, {
          id,
          label: acctCfg.label ?? id,
          config: acctCfg,
          broker: createBrokerAdapter(mergedConfig),
        });
      }
    } else if (config.tradingAccount) {
      // Single account fallback
      this.accounts.set("default", {
        id: "default",
        label: "默认账户",
        config: config.tradingAccount,
        broker: createBrokerAdapter(config),
      });
    } else {
      // No account configured — create a paper account
      const paperCfg: TradingAccountConfig = { mode: "paper" };
      const mergedConfig: QuantTradingConfig = { ...config, tradingAccount: paperCfg };
      this.accounts.set("default", {
        id: "default",
        label: "模拟账户",
        config: paperCfg,
        broker: createBrokerAdapter(mergedConfig),
      });
    }
  }

  getAccount(id: string): AccountEntry | undefined {
    return this.accounts.get(id);
  }

  listAccountIds(): string[] {
    return [...this.accounts.keys()];
  }

  listAccounts(): AccountEntry[] {
    return [...this.accounts.values()];
  }

  /**
   * Query status for all accounts (positions, cash, pending orders).
   */
  async getAccountStatuses(): Promise<AccountStatus[]> {
    const results: AccountStatus[] = [];
    for (const entry of this.accounts.values()) {
      let account: BrokerAccount | null = null;
      let positions: LivePosition[] = [];
      let pendingOrders = 0;
      try {
        if (entry.broker.connected) {
          account = await entry.broker.queryAccount();
          positions = await entry.broker.queryPositions();
          const orders = await entry.broker.queryOrders({
            status: ["submitted", "partial"],
          });
          pendingOrders = orders.length;
        }
      } catch (err) {
        this.api.logger.warn(
          `[account-manager] Failed to query account ${entry.id}: ${err}`,
        );
      }
      results.push({
        id: entry.id,
        label: entry.label,
        mode: entry.config.mode,
        brokerType: entry.config.broker ?? "paper",
        connected: entry.broker.connected,
        account,
        positions,
        pendingOrders,
      });
    }
    return results;
  }

  /**
   * Aggregate view across all connected accounts.
   */
  async getAggregate(): Promise<AggregateView> {
    const statuses = await this.getAccountStatuses();
    let totalCash = 0;
    let totalMarketValue = 0;
    let totalAssets = 0;
    let totalProfit = 0;
    let accountCount = 0;
    for (const s of statuses) {
      if (s.account) {
        totalCash += s.account.cash;
        totalMarketValue += s.account.marketValue;
        totalAssets += s.account.totalAssets;
        totalProfit += s.account.profit;
        accountCount++;
      }
    }
    return { totalCash, totalMarketValue, totalAssets, totalProfit, accountCount };
  }

  /**
   * Get pending (unfilled) order count for a specific account.
   */
  async getPendingOrderCount(accountId: string): Promise<number> {
    const entry = this.accounts.get(accountId);
    if (!entry || !entry.broker.connected) return 0;
    try {
      const orders = await entry.broker.queryOrders({
        status: ["submitted", "partial"],
      });
      return orders.length;
    } catch {
      return 0;
    }
  }
}
