/**
 * dividend_tracker tool — AI-callable for dividend and corporate action tracking.
 *
 * Actions:
 *   record          — Record a dividend or corporate action
 *   delete          — Delete a record
 *   query           — Query records by symbol or date range
 *   upcoming        — View upcoming dividends for held positions
 *   income_summary  — Dividend income summary
 *   cost_basis      — View cost basis adjustments from splits/bonus
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { DividendTracker, CorporateActionType } from "../services/dividend-tracker.js";

export function createDividendTrackerTool(
  _api: OpenClawPluginApi,
  tracker: DividendTracker,
) {
  return {
    name: "dividend_tracker",
    description:
      "分红/公司行动追踪工具。记录现金分红、送股、配股、拆股等。\n" +
      "• action=record — 记录分红或公司行动\n" +
      "• action=delete — 删除记录\n" +
      "• action=query — 查询记录（按symbol或日期范围）\n" +
      "• action=upcoming — 查看持仓即将到来的分红\n" +
      "• action=income_summary — 分红收入汇总\n" +
      "• action=cost_basis — 查看拆股/送股的成本调整",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["record", "delete", "query", "upcoming", "income_summary", "cost_basis"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码" },
        record_id: { type: "string", description: "记录ID (delete)" },
        record_date: { type: "string", description: "股权登记日 YYYY-MM-DD" },
        ex_date: { type: "string", description: "除权除息日 YYYY-MM-DD" },
        pay_date: { type: "string", description: "派息日 YYYY-MM-DD" },
        action_type: {
          type: "string",
          enum: [
            "cash_dividend",
            "stock_dividend",
            "stock_split",
            "rights_issue",
            "bonus_issue",
            "reverse_split",
          ],
          description:
            "类型: cash_dividend=现金分红, stock_dividend=送股, stock_split=拆股, " +
            "rights_issue=配股, bonus_issue=红股, reverse_split=缩股",
        },
        amount_per_share: { type: "number", description: "每股派息金额" },
        total_amount: { type: "number", description: "总分红金额" },
        shares_held: { type: "number", description: "持有股数" },
        ratio: { type: "number", description: "拆分/送股比例（如10送2填2）" },
        new_shares: { type: "number", description: "获得新股数" },
        subscription_price: { type: "number", description: "配股价 (rights_issue)" },
        exercised: { type: "boolean", description: "是否行使配股权" },
        notes: { type: "string", description: "备注" },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "模式: paper=模拟, live=实盘",
        },
        from_date: { type: "string", description: "起始日期" },
        to_date: { type: "string", description: "截止日期" },
        limit: { type: "number", description: "返回数量" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "record": {
          const symbol = args.symbol as string;
          const recordDate = args.record_date as string;
          const exDate = args.ex_date as string;
          const actionType = args.action_type as CorporateActionType;
          if (!symbol || !recordDate || !exDate || !actionType) {
            return { error: "请提供 symbol, record_date, ex_date, action_type" };
          }
          const id = tracker.addRecord({
            symbol: symbol.trim().toUpperCase(),
            recordDate,
            exDate,
            payDate: args.pay_date as string | undefined,
            actionType,
            amountPerShare: args.amount_per_share as number | undefined,
            totalAmount: args.total_amount as number | undefined,
            sharesHeld: args.shares_held as number | undefined,
            ratio: args.ratio as number | undefined,
            newShares: args.new_shares as number | undefined,
            subscriptionPrice: args.subscription_price as number | undefined,
            exercised: args.exercised as boolean | undefined,
            notes: args.notes as string | undefined,
            mode: (args.mode as "paper" | "live") ?? "paper",
          });
          return { message: "记录已保存", id };
        }

        case "delete": {
          const recordId = args.record_id as string;
          if (!recordId) return { error: "请提供 record_id" };
          const ok = tracker.deleteRecord(recordId);
          return ok ? { message: `记录 ${recordId} 已删除` } : { error: `未找到记录 ${recordId}` };
        }

        case "query": {
          const symbol = args.symbol as string;
          const fromDate = args.from_date as string;
          const toDate = args.to_date as string;

          if (symbol) {
            const limit = (args.limit as number) ?? 50;
            const records = tracker.getBySymbol(symbol.trim().toUpperCase(), limit);
            return { count: records.length, records };
          }
          if (fromDate && toDate) {
            const records = tracker.getByDateRange(fromDate, toDate);
            return { count: records.length, records };
          }
          return { error: "请提供 symbol 或 from_date + to_date" };
        }

        case "upcoming": {
          const upcoming = tracker.getUpcoming();
          return { count: upcoming.length, upcoming };
        }

        case "income_summary": {
          const fromDate = args.from_date as string | undefined;
          const toDate = args.to_date as string | undefined;
          const mode = args.mode as "paper" | "live" | undefined;
          return tracker.getIncomeSummary(fromDate, toDate, mode);
        }

        case "cost_basis": {
          const symbol = args.symbol as string;
          if (!symbol) return { error: "请提供 symbol" };
          return tracker.getCostBasisAdjustment(symbol.trim().toUpperCase());
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
