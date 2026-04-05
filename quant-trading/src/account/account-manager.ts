/**
 * AccountManager — manages multiple trading account lifecycles.
 *
 * Handles registration, connection, disconnection, switching,
 * and aggregation of accounts across different broker types.
 */
import type { BrokerAccount } from "../types.js";
import type { AccountConfig, AccountStore } from "./account-store.js";

// ── Types ────────────────────────────────────────────────────

export interface AccountState {
  accountId: string;
  connected: boolean;
  lastSyncAt?: string;
  account?: BrokerAccount;
  positionCount: number;
  pendingOrderCount: number;
}

export interface AggregateView {
  totalAssets: number;
  totalCash: number;
  totalMarketValue: number;
  totalProfit: number;
  accounts: AccountState[];
}

// ── Manager ─────────────────────────────────────────────────

export class AccountManager {
  private store: AccountStore;
  private states: Map<string, AccountState> = new Map();

  constructor(store: AccountStore) {
    this.store = store;
  }

  register(config: AccountConfig): void {
    this.store.register(config);
    this.states.set(config.accountId, {
      accountId: config.accountId,
      connected: false,
      positionCount: 0,
      pendingOrderCount: 0,
    });
  }

  unregister(accountId: string): void {
    const state = this.states.get(accountId);
    if (state?.connected) {
      throw new Error(`Cannot unregister connected account '${accountId}'. Disconnect first.`);
    }
    this.store.unregister(accountId);
    this.states.delete(accountId);
  }

  async connect(accountId: string): Promise<void> {
    if (!this.store.has(accountId)) {
      throw new Error(`Account '${accountId}' not found`);
    }
    const state = this.states.get(accountId);
    if (state?.connected) return; // idempotent

    // In a real implementation, this would create a broker instance and connect.
    // For now we just mark it as connected.
    this.states.set(accountId, {
      accountId,
      connected: true,
      lastSyncAt: new Date().toISOString(),
      positionCount: 0,
      pendingOrderCount: 0,
    });
  }

  async disconnect(accountId: string): Promise<void> {
    const state = this.states.get(accountId);
    if (!state) throw new Error(`Account '${accountId}' not found`);
    if (!state.connected) return; // idempotent

    this.states.set(accountId, {
      ...state,
      connected: false,
    });
  }

  async connectAll(): Promise<void> {
    const accounts = this.store.list();
    await Promise.all(accounts.map((a) => this.connect(a.accountId)));
  }

  listAccounts(): AccountConfig[] {
    return this.store.list();
  }

  getState(accountId: string): AccountState | undefined {
    return this.states.get(accountId);
  }

  hasAccount(accountId: string): boolean {
    return this.store.has(accountId);
  }

  getAggregate(): AggregateView {
    const states = [...this.states.values()];
    let totalAssets = 0;
    let totalCash = 0;
    let totalMarketValue = 0;
    let totalProfit = 0;

    for (const state of states) {
      if (state.account) {
        totalAssets += state.account.totalAssets;
        totalCash += state.account.cash;
        totalMarketValue += state.account.marketValue;
        totalProfit += state.account.profit;
      }
    }

    return { totalAssets, totalCash, totalMarketValue, totalProfit, accounts: states };
  }

  updateAccountState(accountId: string, update: Partial<AccountState>): void {
    const state = this.states.get(accountId);
    if (!state) throw new Error(`Account '${accountId}' not found`);
    this.states.set(accountId, { ...state, ...update });
  }

  get size(): number {
    return this.store.size;
  }
}
