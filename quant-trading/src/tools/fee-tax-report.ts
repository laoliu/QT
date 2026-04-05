/**
 * fee_tax_report tool — transaction cost summary, tax reporting, trade export.
 *
 * Actions:
 *   cost_summary     — aggregate commission, stamp duty, transfer fee by period
 *   daily_costs      — daily breakdown of fees
 *   tax_report       — tax-relevant trade summary for annual filing
 *   export_trades    — export trades as CSV-formatted text for external tools
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { TradeRecord } from "../types.js";

export function createFeeTaxReportTool(
  api: OpenClawPluginApi,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "fee_tax_report",
    description:
      "费用与税务报表工具。汇总佣金、印花税、过户费等交易成本；生成税务申报所需交易汇总；导出交易记录。" +
      "action=cost_summary 费用汇总, action=daily_costs 每日费用明细, " +
      "action=tax_report 税务报表, action=export_trades 导出交易记录。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["cost_summary", "daily_costs", "tax_report", "export_trades"],
          description: "操作类型",
        },
        start_date: { type: "string", description: "起始日期 YYYY-MM-DD" },
        end_date: { type: "string", description: "结束日期 YYYY-MM-DD" },
        symbol: { type: "string", description: "按标的过滤（可选）" },
        year: { type: "string", description: "年度（tax_report使用，默认今年）" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const engine = getEngine();
      if (!engine) return { error: "引擎未初始化" };
      const mgr = engine.orderManager;
      if (!mgr) return { error: "OrderManager 未初始化，请先启动引擎" };

      const symbolFilter = args.symbol as string | undefined;
      const allTrades = await mgr.getTrades(symbolFilter);

      const startDate = args.start_date as string | undefined;
      const endDate = args.end_date as string | undefined;

      const filtered = allTrades.filter((t) => {
        if (!t.exitTime) return false;
        const d = t.exitTime.slice(0, 10);
        if (startDate && d < startDate) return false;
        if (endDate && d > endDate) return false;
        return true;
      });

      switch (action) {
        case "cost_summary": {
          const totalCommission = filtered.reduce((s, t) => s + t.commission, 0);
          const totalGrossPnl = filtered.reduce((s, t) => s + t.grossPnl, 0);
          const totalNetPnl = filtered.reduce((s, t) => s + t.netPnl, 0);
          const totalCost = totalGrossPnl - totalNetPnl;
          // Estimate stamp duty: for A-share sells, 0.05% of sell value
          const estimatedStampDuty = filtered.reduce((s, t) => {
            if (t.side === "sell" || t.side === "sell_close") {
              return s + (t.exitPrice ?? 0) * t.quantity * 0.0005;
            }
            return s;
          }, 0);
          const estimatedTransferFee = filtered.reduce((s, t) => {
            return s + (t.exitPrice ?? 0) * t.quantity * 0.00001 * 2; // buy + sell side
          }, 0);

          return {
            period: {
              from: startDate ?? filtered[0]?.exitTime?.slice(0, 10) ?? "N/A",
              to: endDate ?? filtered[filtered.length - 1]?.exitTime?.slice(0, 10) ?? "N/A",
            },
            tradeCount: filtered.length,
            totalCommission: `¥${totalCommission.toFixed(2)}`,
            estimatedStampDuty: `¥${estimatedStampDuty.toFixed(2)}`,
            estimatedTransferFee: `¥${estimatedTransferFee.toFixed(2)}`,
            totalCost: `¥${totalCost.toFixed(2)}`,
            totalGrossPnl: `¥${totalGrossPnl.toFixed(2)}`,
            totalNetPnl: `¥${totalNetPnl.toFixed(2)}`,
            costPctOfTurnover:
              filtered.length > 0
                ? `${((totalCost / filtered.reduce((s, t) => s + (t.exitPrice ?? 0) * t.quantity, 0)) * 100).toFixed(3)}%`
                : "N/A",
          };
        }

        case "daily_costs": {
          const byDay = new Map<
            string,
            { commission: number; trades: number; grossPnl: number; netPnl: number }
          >();
          for (const t of filtered) {
            const day = t.exitTime!.slice(0, 10);
            const prev = byDay.get(day) ?? { commission: 0, trades: 0, grossPnl: 0, netPnl: 0 };
            prev.commission += t.commission;
            prev.trades += 1;
            prev.grossPnl += t.grossPnl;
            prev.netPnl += t.netPnl;
            byDay.set(day, prev);
          }
          const days = [...byDay.entries()]
            .sort((a, b) => b[0].localeCompare(a[0]))
            .map(([date, d]) => ({
              date,
              trades: d.trades,
              commission: `¥${d.commission.toFixed(2)}`,
              totalCost: `¥${(d.grossPnl - d.netPnl).toFixed(2)}`,
              netPnl: `¥${d.netPnl.toFixed(2)}`,
            }));
          return { totalDays: days.length, daily: days.slice(0, 60) };
        }

        case "tax_report": {
          const year = (args.year as string) ?? new Date().getFullYear().toString();
          const yearTrades = allTrades.filter(
            (t) => t.exitTime && t.exitTime.startsWith(year),
          );
          const realized = yearTrades.filter((t) => t.exitTime);
          const totalGain = realized.filter((t) => t.netPnl > 0).reduce((s, t) => s + t.netPnl, 0);
          const totalLoss = realized.filter((t) => t.netPnl < 0).reduce((s, t) => s + t.netPnl, 0);
          const totalCommission = realized.reduce((s, t) => s + t.commission, 0);

          // Group by symbol
          const bySymbol = new Map<string, { trades: number; netPnl: number }>();
          for (const t of realized) {
            const prev = bySymbol.get(t.symbol) ?? { trades: 0, netPnl: 0 };
            prev.trades += 1;
            prev.netPnl += t.netPnl;
            bySymbol.set(t.symbol, prev);
          }
          const symbolSummary = [...bySymbol.entries()]
            .sort((a, b) => b[1].netPnl - a[1].netPnl)
            .map(([symbol, d]) => ({
              symbol,
              trades: d.trades,
              netPnl: `¥${d.netPnl.toFixed(2)}`,
            }));

          return {
            year,
            totalTrades: realized.length,
            totalGain: `¥${totalGain.toFixed(2)}`,
            totalLoss: `¥${totalLoss.toFixed(2)}`,
            netPnl: `¥${(totalGain + totalLoss).toFixed(2)}`,
            totalCommission: `¥${totalCommission.toFixed(2)}`,
            bySymbol: symbolSummary.slice(0, 30),
            note: "A股个人投资者股票买卖暂免征收个人所得税。以上数据仅供参考，请以券商对账单为准。",
          };
        }

        case "export_trades": {
          const header =
            "交易ID,标的,方向,买入时间,卖出时间,买入价,卖出价,数量,毛利,佣金,净利,策略";
          const rows = filtered.map((t) =>
            [
              t.tradeId.slice(0, 8),
              t.symbol,
              t.side,
              t.entryTime,
              t.exitTime ?? "",
              t.entryPrice.toFixed(2),
              (t.exitPrice ?? 0).toFixed(2),
              t.quantity,
              t.grossPnl.toFixed(2),
              t.commission.toFixed(2),
              t.netPnl.toFixed(2),
              t.strategyId ?? "",
            ].join(","),
          );
          return {
            format: "CSV",
            tradeCount: filtered.length,
            csv: [header, ...rows].join("\n"),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
