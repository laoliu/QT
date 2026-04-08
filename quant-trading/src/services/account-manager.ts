/**
 * AccountManager 66multi-account registry and active-account selector.
 *
 * Manages a list of named trading accounts and tracks which one is active.
 * Provides backward compatibility: if only the legacy `tradingAccount` field
 * is configured (no `tradingAccounts` array), it wraps it as a single
 * account named "default".
 *
 * Supports runtime CRUD: accounts can be added, updated, and removed while
 * the gateway is running. Changes are persisted to the state file.
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
  /** Runtime-added accounts (not in original config). Persisted for restart survival. */
  runtimeAccounts?: NamedTradingAccount[];
};

/** Result of an add/update/remove operation. */
export type AccountMutationResult = { ok: true } | { ok: false; reason: string };

export class AccountManager {
  private accounts: NamedTradingAccount[];
  private activeAccountName: string;
  private statePath: string;
  /** Account names that came from config (cannot be removed at runtime). */
  private configAccountNames: Set<string>;

  constructor(config: QuantTradingConfig) {
    // Build account list from config
    if (config.tradingAccounts && config.tradingAccounts.length > 0) {
      this.accounts = [...config.tradingAccounts];
    } else {
      // Backward compat: wrap single tradingAccount as "default"
      const legacy: TradingAccountConfig = config.tradingAccount ?? { mode: "paper" };
      this.accounts = [{ ...legacy, name: "default", label: "0801" }];
    }

    this.configAccountNames = new Set(this.accounts.map((a) => a.name));

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
   * Also restores runtime-added accounts from previous sessions.
   * Call once during initialization.
   */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.statePath, "utf-8");
      const state = JSON.parse(raw) as AccountManagerState;

      // Restore runtime-added accounts
      if (state.runtimeAccounts) {
        for (const ra of state.runtimeAccounts) {
          if (!this.accounts.some((a) => a.name === ra.name)) {
            this.accounts.push(ra);
          }
        }
      }

      if (state.activeAccount && this.accounts.some((a) => a.name === state.activeAccount)) {
        this.activeAccountName = state.activeAccount;
      }
    } catch {
      // No persisted state 66use config default
    }
  }

  /** Persist active account selection and runtime accounts to disk. */
  private async save(): Promise<void> {
    try {
      await fs.mkdir(path.dirname(this.statePath), { recursive: true });
      // Collect runtime-added accounts (those not in the original config)
      const runtimeAccounts = this.accounts.filter((a) => !this.configAccountNames.has(a.name));
      const state: AccountManagerState = {
        activeAccount: this.activeAccountName,
        ...(runtimeAccounts.length > 0 ? { runtimeAccounts } : {}),
      };
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

  // ── Runtime CRUD ──────────────────────────────────────────────────

  /**
   * Add a new account at runtime. The account is persisted to survive restarts.
   * Name must be unique, non-empty, and alphanumeric with hyphens/underscores.
   */
  async addAccount(account: NamedTradingAccount): Promise<AccountMutationResult> {
    if (!account.name || !/^[\w-]+$/.test(account.name)) {
      return { ok: false, reason: "账户名称无效，仅允许字母、数字、连字符和下划线" };
    }
    if (this.accounts.some((a) => a.name === account.name)) {
      return { ok: false, reason: `01 "${account.name}" 04` };
    }
    if (!account.mode) {
      return { ok: false, reason: "必须指定 mode (paper 成live)" };
    }
    this.accounts.push({ ...account });
    await this.save();
    return { ok: true };
  }

  /**
   * Update an existing account's configuration.
   * Cannot rename the account (use remove + add instead).
   * Cannot update the currently active account if the engine is running
   * 66caller should stop the engine first.
   */
  async updateAccount(
    name: string,
    updates: Partial<Omit<NamedTradingAccount, "name">>,
  ): Promise<AccountMutationResult> {
    const idx = this.accounts.findIndex((a) => a.name === name);
    if (idx === -1) {
      return { ok: false, reason: `01 "${name}" 不存在` };
    }
    this.accounts[idx] = { ...this.accounts[idx], ...updates, name };
    await this.save();
    return { ok: true };
  }

  /**
   * Remove an account. Cannot remove the currently active account
   * or the last remaining account.
   */
  async removeAccount(name: string): Promise<AccountMutationResult> {
    if (!this.accounts.some((a) => a.name === name)) {
      return { ok: false, reason: `01 "${name}" 不存在` };
    }
    if (this.accounts.length <= 1) {
      return { ok: false, reason: "不能删除最后一个账" };
    }
    if (name === this.activeAccountName) {
      return { ok: false, reason: "010206011101" };
    }
    this.accounts = this.accounts.filter((a) => a.name !== name);
    await this.save();
    return { ok: true };
  }

  // ── Config resolution ─────────────────────────────────────────────

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

  // ── Cross-account queries ─────────────────────────────────────────

  /**
   * Return a summary view of all accounts with their configuration details.
   * Live data (positions, P&L) must be fetched separately by the caller
   * since only the active account's engine is running.
   */
  getAccountsSummary(): Array<{
    name: string;
    label: string;
    mode: "paper" | "live";
    broker: string;
    accountId?: string;
    isActive: boolean;
    isFromConfig: boolean;
    riskLimits?: NamedTradingAccount["riskLimits"];
    positionSizing?: NamedTradingAccount["positionSizing"];
  }> {
    return this.accounts.map((a) => ({
      name: a.name,
      label: a.label ?? a.name,
      mode: a.mode,
      broker: a.broker ?? "paper",
      accountId: a.accountId,
      isActive: a.name === this.activeAccountName,
      isFromConfig: this.configAccountNames.has(a.name),
      riskLimits: a.riskLimits,
      positionSizing: a.positionSizing,
    }));
  }

  /** Return all account names (useful for iterating cross-account data). */
  getAllAccountNames(): string[] {
    return this.accounts.map((a) => a.name);
  }

  /** Whether multi-account mode is active (more than one account configured). */
  get isMultiAccount(): boolean {
    return this.accounts.length > 1;
  }
}
