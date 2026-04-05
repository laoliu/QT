/**
 * AccountContext — maintains the "current active account" state.
 *
 * All downstream modules (AI tools, commands, HTTP routes) obtain the
 * active broker, order manager, and risk gate through this context.
 */
import type { IBrokerAdapter } from "../broker/adapter.js";
import type { OrderManager } from "../services/order-manager.js";
import type { RiskGate } from "../services/risk-gate.js";
import type { AccountManager } from "./account-manager.js";

export class AccountContext {
  private activeAccountId: string;
  private manager: AccountManager;

  constructor(manager: AccountManager, defaultAccountId: string) {
    this.manager = manager;
    this.activeAccountId = defaultAccountId;
  }

  get current(): string {
    return this.activeAccountId;
  }

  /**
   * Switch to a different account.
   * @param accountId Target account
   * @param confirm If true, skip confirmation check (for programmatic use)
   */
  async switch(accountId: string, confirm = false): Promise<void> {
    if (!this.manager.hasAccount(accountId)) {
      throw new Error(`Account '${accountId}' is not registered`);
    }

    // Check for pending orders on current account
    const currentState = this.manager.getState(this.activeAccountId);
    if (!confirm && currentState && currentState.pendingOrderCount > 0) {
      throw new Error(
        `Cannot switch: account '${this.activeAccountId}' has ${currentState.pendingOrderCount} pending orders. ` +
        `Resolve them first or pass confirm=true to force switch.`,
      );
    }

    this.activeAccountId = accountId;
  }

  /** Convenience accessor: broker adapter for the current account. */
  get broker(): IBrokerAdapter {
    return this.manager.getBroker(this.activeAccountId);
  }

  /** Convenience accessor: order manager for the current account. */
  get orderManager(): OrderManager {
    return this.manager.getOrderManager(this.activeAccountId);
  }

  /** Convenience accessor: risk gate for the current account. */
  get riskGate(): RiskGate {
    return this.manager.getRiskGate(this.activeAccountId);
  }

  get accountManager(): AccountManager {
    return this.manager;
  }
}
