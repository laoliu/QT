/**
 * fee_tax_report tool 66transaction cost summary, tax reporting, trade export.
 *
 * Actions:
 *   cost_summary     66aggregate commission, stamp duty, transfer fee by period
 *   daily_costs      66daily breakdown of fees
 *   tax_report       66tax-relevant trade summary for annual filing
 *   export_trades    66export trades as CSV-formatted text for external tools
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { TradeRecord } from "../types.js";

export function createFeeTaxReportTool(
  api: QuantTradingRuntime,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "fee_tax_report",
    description:
      "00711104030005080708007905110604" +
      "action=cost_summary 04? action=daily_costs 070306, " +
      "action=tax_report 0071, action=export_trades 0604",
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
        symbol: { type: "string", description: "010705" },
        year: { type: "string", description: "02tax_report0000080164" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const engine = getEngine();
      if (!engine) return { error: "ā03" };
      const mgr = engine.orderManager;
      if (!mgr) return { error: "OrderManager 未初始化，请先启动引" };

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
            totalCommission: `06${totalCommission.toFixed(2)}`,
            estimatedStampDuty: `06${estimatedStampDuty.toFixed(2)}`,
            estimatedTransferFee: `06${estimatedTransferFee.toFixed(2)}`,
            totalCost: `06${totalCost.toFixed(2)}`,
            totalGrossPnl: `06${totalGrossPnl.toFixed(2)}`,
            totalNetPnl: `06${totalNetPnl.toFixed(2)}`,
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
              commission: `06${d.commission.toFixed(2)}`,
              totalCost: `06${(d.grossPnl - d.netPnl).toFixed(2)}`,
              netPnl: `06${d.netPnl.toFixed(2)}`,
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
              netPnl: `06${d.netPnl.toFixed(2)}`,
            }));

          return {
            year,
            totalTrades: realized.length,
            totalGain: `06${totalGain.toFixed(2)}`,
            totalLoss: `06${totalLoss.toFixed(2)}`,
            netPnl: `06${(totalGain + totalLoss).toFixed(2)}`,
            totalCommission: `06${totalCommission.toFixed(2)}`,
            bySymbol: symbolSummary.slice(0, 30),
            note: "A05041507040011ρ0400050206",
          };
        }

        case "export_trades": {
          const header =
            "交易ID,标的,方向,买入时间,卖出时间,买入仓卖出仓数量,05,04,净分策略";
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
          return { error: `ā04: ${action}` };
      }
    },
  };
}
