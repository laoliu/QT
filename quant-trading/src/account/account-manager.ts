/**
 * AccountManager — manages multiple trading account lifecycles.
 *
 * Handles registration, connection, disconnection, switching,
 * and aggregation of accounts across different broker types.
 */
import type { IBrokerAdapter } from "../broker/adapter.js";
import type { BrokerAccount } from "../types.js";
import type { OrderManager } from "../services/order-manager.js";
import type { RiskGate } from "../services/risk-gate.js";
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

/** Factory that creates per-account broker/order-manager/risk-gate instances. */
export interface AccountInstanceFactory {
  createBroker(config: AccountConfig): IBrokerAdapter;
  createOrderManager(broker: IBrokerAdapter, config: AccountConfig): OrderManager;
  createRiskGate(config: AccountConfig): RiskGate;
}

// ── Manager ─────────────────────────────────────────────────

export class AccountManager {
  private store: AccountStore;
  private states: Map<string, AccountState> = new Map();
  private brokers: Map<string, IBrokerAdapter> = new Map();
  private orderManagers: Map<string, OrderManager> = new Map();
  private riskGates: Map<string, RiskGate> = new Map();
  private factory?: AccountInstanceFactory;

  constructor(store: AccountStore, factory?: AccountInstanceFactory) {
    this.store = store;
    this.factory = factory;
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
    this.brokers.delete(accountId);
    this.orderManagers.delete(accountId);
    this.riskGates.delete(accountId);
  }

  async connect(accountId: string): Promise<void> {
    if (!this.store.has(accountId)) {
      throw new Error(`Account '${accountId}' not found`);
    }
    const state = this.states.get(accountId);
    if (state?.connected) return; // idempotent

    const config = this.store.get(accountId);
    if (config && this.factory) {
      const broker = this.factory.createBroker(config);
      await broker.connect();
      this.brokers.set(accountId, broker);
      this.orderManagers.set(accountId, this.factory.createOrderManager(broker, config));
      this.riskGates.set(accountId, this.factory.createRiskGate(config));
    }

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

    const broker = this.brokers.get(accountId);
    if (broker?.connected) {
      await broker.disconnect();
    }

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

  getBroker(accountId: string): IBrokerAdapter {
    const broker = this.brokers.get(accountId);
    if (!broker) throw new Error(`No broker instance for account '${accountId}'. Is it connected?`);
    return broker;
  }

  getOrderManager(accountId: string): OrderManager {
    const om = this.orderManagers.get(accountId);
    if (!om) throw new Error(`No order manager for account '${accountId}'. Is it connected?`);
    return om;
  }

  getRiskGate(accountId: string): RiskGate {
    const rg = this.riskGates.get(accountId);
    if (!rg) throw new Error(`No risk gate for account '${accountId}'. Is it connected?`);
    return rg;
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
