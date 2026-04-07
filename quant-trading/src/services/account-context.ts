/**
 * AccountContext — provides a scoped view of configuration and data paths
 * for a specific trading account.
 *
 * When the system needs to resolve file paths or broker settings for a
 * particular account, it uses the AccountContext rather than the raw
 * QuantTradingConfig. This ensures per-account isolation.
 */
import type { QuantTradingConfig, TradingAccountConfig, BrokerType } from "../types.js";
import type { AccountStore } from "./account-store.js";

export interface AccountInfo {
  /** Unique account identifier, e.g. "paper-default", "qmt-8888000001" */
  id: string;
  /** Human-readable display name */
  name: string;
  /** Broker type for this account */
  broker: BrokerType;
  /** Paper or live */
  mode: "paper" | "live";
  /** Whether this is the currently active account */
  active: boolean;
  /** Connection status */
  connected: boolean;
}

export class AccountContext {
  readonly accountId: string;
  readonly store: AccountStore;
  readonly tradingConfig: TradingAccountConfig;
  private readonly baseConfig: QuantTradingConfig;

  constructor(
    accountId: string,
    store: AccountStore,
    tradingConfig: TradingAccountConfig,
    baseConfig: QuantTradingConfig,
  ) {
    this.accountId = accountId;
    this.store = store;
    this.tradingConfig = tradingConfig;
    this.baseConfig = baseConfig;
  }

  /** Display name for this account */
  get displayName(): string {
    return this.tradingConfig.accountId
      ? `${this.broker}:${this.tradingConfig.accountId}`
      : `${this.broker}:${this.accountId}`;
  }

  /** Broker type */
  get broker(): BrokerType {
    return this.tradingConfig.broker ?? "paper";
  }

  /** Paper or live mode */
  get mode(): "paper" | "live" {
    return this.tradingConfig.mode;
  }

  /**
   * Build a QuantTradingConfig that redirects dataDir to this account's store
   * and uses this account's tradingAccount settings. Other config values
   * (dataProviders, pythonPath, etc.) are inherited from the base config.
   */
  toScopedConfig(): QuantTradingConfig {
    return {
      ...this.baseConfig,
      dataDir: this.store.dir,
      tradingAccount: this.tradingConfig,
    };
  }

  /** Build an AccountInfo snapshot */
  toInfo(active: boolean, connected: boolean): AccountInfo {
    return {
      id: this.accountId,
      name: this.displayName,
      broker: this.broker,
      mode: this.mode,
      active,
      connected,
    };
  }
}
