/**
 * AccountManager — multi-account registry and active-account selector.
 *
 * Manages a list of named trading accounts and tracks which one is active.
 * Provides backward compatibility: if only the legacy `tradingAccount` field
 * is configured (no `tradingAccounts` array), it wraps it as a single
 * account named "default".
 *
 * Active account state is persisted to a local JSON file so it survives
 * gateway restarts.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { NamedTradingAccount, QuantTradingConfig, TradingAccountConfig } from "../types.js";

/** Persisted state for account manager */
type AccountManagerState = {
  activeAccount: string;
};

export class AccountManager {
  private accounts: NamedTradingAccount[];
  private activeAccountName: string;
  private statePath: string;

  constructor(config: QuantTradingConfig) {
    // Build account list from config
    if (config.tradingAccounts && config.tradingAccounts.length > 0) {
      this.accounts = config.tradingAccounts;
    } else {
      // Backward compat: wrap single tradingAccount as "default"
      const legacy: TradingAccountConfig = config.tradingAccount ?? { mode: "paper" };
      this.accounts = [{ ...legacy, name: "default", label: "默认账户" }];
    }

    // Determine initial active account
    const configActive = config.activeAccount;
    if (configActive && this.accounts.some((a) => a.name === configActive)) {
      this.activeAccountName = configActive;
    } else {
      this.activeAccountName = this.accounts[0].name;
    }

    const dataDir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.statePath = path.join(dataDir, "account-manager-state.json");
  }

  /**
   * Load persisted active account state from disk.
   * Call once during initialization.
   */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.statePath, "utf-8");
      const state = JSON.parse(raw) as AccountManagerState;
      if (state.activeAccount && this.accounts.some((a) => a.name === state.activeAccount)) {
        this.activeAccountName = state.activeAccount;
      }
    } catch {
      // No persisted state — use config default
    }
  }

  /** Persist active account selection to disk. */
  private async save(): Promise<void> {
    try {
      await fs.mkdir(path.dirname(this.statePath), { recursive: true });
      const state: AccountManagerState = { activeAccount: this.activeAccountName };
      await fs.writeFile(this.statePath, JSON.stringify(state, null, 2));
    } catch {
      // Best-effort persistence
    }
  }

  /** Return all configured accounts. */
  listAccounts(): NamedTradingAccount[] {
    return this.accounts;
  }

  /** Return the currently active account. */
  getActiveAccount(): NamedTradingAccount {
    return this.accounts.find((a) => a.name === this.activeAccountName) ?? this.accounts[0];
  }

  /** Return the active account name. */
  getActiveAccountName(): string {
    return this.activeAccountName;
  }

  /** Look up an account by name. Returns undefined if not found. */
  getAccountByName(name: string): NamedTradingAccount | undefined {
    return this.accounts.find((a) => a.name === name);
  }

  /**
   * Switch the active account. Persists the choice to disk.
   * Returns true if the account was found and switched, false if not found.
   */
  async setActiveAccount(name: string): Promise<boolean> {
    const account = this.accounts.find((a) => a.name === name);
    if (!account) return false;
    this.activeAccountName = name;
    await this.save();
    return true;
  }

  /**
   * Build a QuantTradingConfig-compatible object with `tradingAccount`
   * set to the active account's config. This allows all existing code
   * that reads `config.tradingAccount` to work unchanged.
   */
  resolveConfig(baseConfig: QuantTradingConfig): QuantTradingConfig {
    const active = this.getActiveAccount();
    // Strip multi-account fields to produce a clean single-account config
    const { name: _name, label: _label, ...accountConfig } = active;
    return { ...baseConfig, tradingAccount: accountConfig };
  }

  /**
   * Resolve config for a specific named account (not necessarily active).
   * Useful for querying data from non-active accounts.
   */
  resolveConfigForAccount(
    baseConfig: QuantTradingConfig,
    accountName: string,
  ): QuantTradingConfig | null {
    const account = this.getAccountByName(accountName);
    if (!account) return null;
    const { name: _name, label: _label, ...accountConfig } = account;
    return { ...baseConfig, tradingAccount: accountConfig };
  }

  /** Whether multi-account mode is active (more than one account configured). */
  get isMultiAccount(): boolean {
    return this.accounts.length > 1;
  }
}
