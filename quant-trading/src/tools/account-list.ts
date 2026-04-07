/**
 * account_list tool — AI-callable for viewing all trading accounts and aggregate status.
 *
 * Actions:
 *   list      — List all accounts with status (cash, positions, pending orders)
 *   aggregate — Get aggregate view across all accounts (total cash, assets, profit)
 *   detail    — Get detailed status for a specific account
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type {
  AccountContext,
  AccountManager,
} from "../services/account-manager.js";

export function createAccountListTool(
  _api: OpenClawPluginApi,
  accountManager: AccountManager,
  accountContext: AccountContext,
) {
  return {
    name: "account_list",
    description:
      "查看所有交易账户状态和聚合视图。\n" +
      "• action=list — 列出所有账户及其状态（资金、持仓、挂单）\n" +
      "• action=aggregate — 聚合视图（总资金、总资产、总盈亏）\n" +
      "• action=detail — 查看指定账户详情（需提供 accountId）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "aggregate", "detail"],
          description: "操作类型",
        },
        accountId: {
          type: "string",
          description: "账户ID（action=detail 时必需）",
        },
      },
      required: ["action"] as const,
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "list": {
          const statuses = await accountManager.getAccountStatuses();
          const activeId = accountContext.activeAccountId;
          return {
            activeAccountId: activeId,
            accounts: statuses.map((s) => ({
              ...s,
              isActive: s.id === activeId,
            })),
          };
        }

        case "aggregate": {
          const aggregate = await accountManager.getAggregate();
          const statuses = await accountManager.getAccountStatuses();
          return {
            aggregate,
            activeAccountId: accountContext.activeAccountId,
            accountSummaries: statuses.map((s) => ({
              id: s.id,
              label: s.label,
              mode: s.mode,
              totalAssets: s.account?.totalAssets ?? 0,
              profit: s.account?.profit ?? 0,
              positionCount: s.positions.length,
            })),
          };
        }

        case "detail": {
          const accountId = args.accountId as string | undefined;
          if (!accountId) {
            return { error: "请提供 accountId 参数" };
          }
          const statuses = await accountManager.getAccountStatuses();
          const status = statuses.find((s) => s.id === accountId);
          if (!status) {
            return {
              error: `未找到账户: ${accountId}`,
              availableAccounts: accountManager.listAccountIds(),
            };
          }
          return {
            ...status,
            isActive: accountId === accountContext.activeAccountId,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
