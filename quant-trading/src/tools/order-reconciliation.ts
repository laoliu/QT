/**
 * order_reconciliation tool — AI-callable interface for checking order/position
 * consistency between local state and the broker.
 *
 * Actions:
 *   check         — run a full reconciliation check now and return issues
 *   last_issues   — return the last detected issues without re-scanning
 *   stuck_orders  — check only for stuck/stale orders
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderReconciliationServiceInstance } from "../services/order-reconciliation.js";

export function createOrderReconciliationTool(
  _api: OpenClawPluginApi,
  reconciliationService: OrderReconciliationServiceInstance,
) {
  return {
    name: "order_reconciliation",
    description:
      "订单对账与持仓一致性检查工具。" +
      "action=check 立即执行对账检查（比对本地与券商的订单和持仓状态），" +
      "action=last_issues 查看最近一次检查发现的问题，" +
      "action=stuck_orders 仅检查卡住的订单。" +
      "可以检测：卡单（submitted/partial状态停留过久）、持仓不一致（本地与券商数量不符）、" +
      "未知持仓（券商有但本地未记录）、滞留pending订单。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["check", "last_issues", "stuck_orders"],
          description: "操作类型",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "check": {
          const issues = await reconciliationService.reconcile();
          if (issues.length === 0) {
            return {
              status: "ok",
              message: "对账完成，未发现异常",
              issues: [],
              checkedAt: new Date().toISOString(),
            };
          }
          return {
            status: "issues_found",
            message: `发现 ${issues.length} 个异常`,
            issues,
            summary: {
              stuck_orders: issues.filter((i) => i.type === "stuck_order").length,
              position_mismatches: issues.filter((i) => i.type === "position_mismatch").length,
              unexpected_positions: issues.filter((i) => i.type === "unexpected_position").length,
              stale_pending: issues.filter((i) => i.type === "stale_pending").length,
            },
            checkedAt: new Date().toISOString(),
          };
        }

        case "last_issues": {
          const issues = reconciliationService.getLastIssues();
          if (issues.length === 0) {
            return {
              status: "ok",
              message: "最近一次检查未发现异常（或尚未执行过检查）",
              issues: [],
            };
          }
          return {
            status: "issues_found",
            message: `最近一次检查发现 ${issues.length} 个异常`,
            issues,
          };
        }

        case "stuck_orders": {
          const allIssues = await reconciliationService.reconcile();
          const stuckIssues = allIssues.filter(
            (i) => i.type === "stuck_order" || i.type === "stale_pending",
          );
          if (stuckIssues.length === 0) {
            return {
              status: "ok",
              message: "未发现卡单或滞留订单",
              issues: [],
            };
          }
          return {
            status: "issues_found",
            message: `发现 ${stuckIssues.length} 个订单异常`,
            issues: stuckIssues,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
