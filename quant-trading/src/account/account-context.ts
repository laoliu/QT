/**
 * AccountContext — maintains the "current active account" state.
 *
 * All downstream modules (AI tools, commands, HTTP routes) obtain the
 * active broker, order manager, and risk gate through this context.
 */
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

  get accountManager(): AccountManager {
    return this.manager;
  }
}
