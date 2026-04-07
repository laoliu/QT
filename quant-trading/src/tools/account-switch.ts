/**
 * account_switch tool — AI-callable for switching the active trading account.
 *
 * Before switching, checks for pending (unfilled) orders on the current account
 * and requires explicit confirmation if any exist.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type {
  AccountContext,
  AccountManager,
} from "../services/account-manager.js";

export function createAccountSwitchTool(
  api: OpenClawPluginApi,
  accountManager: AccountManager,
  accountContext: AccountContext,
) {
  return {
    name: "account_switch",
    description:
      "切换当前活跃交易账户。切换前会检查当前账户是否有未完成订单，" +
      "如有则需要 confirm=true 才能切换。\n" +
      "• accountId — 目标账户ID（必需）\n" +
      "• confirm — 当存在未完成订单时，设为 true 确认切换",
    parameters: {
      type: "object" as const,
      properties: {
        accountId: {
          type: "string",
          description: "要切换到的目标账户ID",
        },
        confirm: {
          type: "boolean",
          description: "确认切换（当存在未完成订单时必需）",
        },
      },
      required: ["accountId"] as const,
    },
    async execute(args: Record<string, unknown>) {
      const targetId = args.accountId as string;
      const confirm = args.confirm as boolean | undefined;

      // Validate target account exists
      const targetEntry = accountManager.getAccount(targetId);
      if (!targetEntry) {
        return {
          success: false,
          error: `未找到账户: ${targetId}`,
          availableAccounts: accountManager.listAccountIds(),
        };
      }

      // Already on this account?
      const currentId = accountContext.activeAccountId;
      if (currentId === targetId) {
        return {
          success: true,
          message: `当前已在账户 "${targetEntry.label}" (${targetId})`,
          accountId: targetId,
        };
      }

      // Check for pending orders on current account
      const pendingCount = await accountManager.getPendingOrderCount(currentId);
      if (pendingCount > 0 && !confirm) {
        return {
          success: false,
          requiresConfirmation: true,
          currentAccountId: currentId,
          pendingOrders: pendingCount,
          message:
            `当前账户 "${currentId}" 有 ${pendingCount} 笔未完成订单。` +
            `请设置 confirm=true 确认切换，或先处理完挂单。`,
          targetAccountId: targetId,
        };
      }

      // Perform the switch
      const switched = accountContext.switch(targetId);
      if (!switched) {
        return {
          success: false,
          error: "切换失败：目标账户不可用",
        };
      }

      api.logger.info(
        `[account-switch] Switched from "${currentId}" to "${targetId}" (${switched.label})` +
          (pendingCount > 0 ? ` — ${pendingCount} pending orders on previous account` : ""),
      );

      return {
        success: true,
        previousAccountId: currentId,
        accountId: targetId,
        label: switched.label,
        mode: switched.config.mode,
        brokerType: switched.config.broker ?? "paper",
        message: `已切换到账户 "${switched.label}" (${targetId})`,
        ...(pendingCount > 0
          ? { warning: `原账户 "${currentId}" 仍有 ${pendingCount} 笔未完成订单` }
          : {}),
      };
    },
  };
}
