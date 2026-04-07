/**
 * trade_history tool — AI-callable interface for comprehensive trade history analysis.
 *
 * Actions:
 *   list           — list trade records with optional filters (symbol, date range, strategy)
 *   statistics     — win rate, avg P&L, holding period distribution, Sharpe, etc.
 *   best_worst     — show best & worst trades
 *   monthly_summary — monthly P&L breakdown
 *   by_strategy    — performance grouped by strategy
 *   export         — export trade records as JSON/CSV format text
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { TradeRecord, QuantTradingConfig } from "../types.js";

export function createTradeHistoryTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  async function getTrades(symbol?: string): Promise<TradeRecord[]> {
    const engine = getEngine();
    if (!engine?.orderManager) return [];
    return engine.orderManager.getTrades(symbol);
  }

  function filterTrades(
    trades: TradeRecord[],
    args: Record<string, unknown>,
  ): TradeRecord[] {
    let result = trades;
    const symbol = args.symbol as string | undefined;
    if (symbol) result = result.filter((t) => t.symbol === symbol);

    const strategyId = args.strategy_id as string | undefined;
    if (strategyId) result = result.filter((t) => t.strategyId === strategyId);

    const startDate = args.start_date as string | undefined;
    if (startDate) result = result.filter((t) => t.entryTime >= startDate);

    const endDate = args.end_date as string | undefined;
    if (endDate) result = result.filter((t) => t.entryTime <= endDate);

    const direction = args.direction as string | undefined;
    if (direction) result = result.filter((t) => t.side === direction);

    return result;
  }

  return {
    name: "trade_history",
    description:
      "交易历史分析工具。查看完整交易记录，分析胜率、盈亏、持仓周期、月度汇总、策略表现。\n" +
      "action=list 交易列表, action=statistics 统计分析, action=best_worst 最佳/最差交易, " +
      "action=monthly_summary 月度汇总, action=by_strategy 按策略分析, action=export 导出记录。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "list",
            "statistics",
            "best_worst",
            "monthly_summary",
            "by_strategy",
            "export",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "按标的过滤 (可选)" },
        strategy_id: { type: "string", description: "按策略ID过滤 (可选)" },
        start_date: { type: "string", description: "起始日期 YYYY-MM-DD (可选)" },
        end_date: { type: "string", description: "截止日期 YYYY-MM-DD (可选)" },
        direction: {
          type: "string",
          enum: ["buy", "sell", "short", "cover"],
          description: "按方向过滤 (可选)",
        },
        limit: { type: "number", description: "返回条数限制 (默认50)" },
        format: {
          type: "string",
          enum: ["json", "csv"],
          description: "导出格式 (默认json)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const allTrades = await getTrades();
      if (allTrades.length === 0) {
        return { message: "暂无交易记录。请先通过实盘引擎执行交易。" };
      }
      const trades = filterTrades(allTrades, args);

      switch (action) {
        case "list": {
          const limit = (args.limit as number) || 50;
          const sorted = [...trades].sort(
            (a, b) => new Date(b.entryTime).getTime() - new Date(a.entryTime).getTime(),
          );
          return {
            total: trades.length,
            showing: Math.min(limit, trades.length),
            trades: sorted.slice(0, limit).map((t) => ({
              tradeId: t.tradeId,
              symbol: t.symbol,
              name: t.name ?? t.symbol,
              side: t.side,
              quantity: t.quantity,
              entryPrice: `¥${t.entryPrice.toFixed(3)}`,
              exitPrice: t.exitPrice != null ? `¥${t.exitPrice.toFixed(3)}` : "持仓中",
              netPnl: `¥${t.netPnl.toFixed(2)}`,
              netPnlPct: t.exitPrice != null
                ? `${(((t.exitPrice - t.entryPrice) / t.entryPrice) * 100).toFixed(2)}%`
                : "-",
              holdingDays: t.holdingDays ?? "-",
              strategyId: t.strategyId ?? "-",
              entryTime: t.entryTime,
              exitTime: t.exitTime ?? "-",
            })),
          };
        }

        case "statistics": {
          const closed = trades.filter((t) => t.exitTime != null);
          if (closed.length === 0) return { message: "暂无已平仓交易记录" };

          const wins = closed.filter((t) => t.netPnl > 0);
          const losses = closed.filter((t) => t.netPnl < 0);
          const totalPnl = closed.reduce((s, t) => s + t.netPnl, 0);
          const totalCommission = closed.reduce((s, t) => s + t.commission, 0);
          const avgWin = wins.length > 0 ? wins.reduce((s, t) => s + t.netPnl, 0) / wins.length : 0;
          const avgLoss = losses.length > 0 ? losses.reduce((s, t) => s + t.netPnl, 0) / losses.length : 0;
          const holdingDays = closed
            .filter((t) => t.holdingDays != null)
            .map((t) => t.holdingDays!);
          const avgHolding = holdingDays.length > 0
            ? holdingDays.reduce((s, d) => s + d, 0) / holdingDays.length
            : 0;

          const pnlArray = closed.map((t) => t.netPnl);
          const avgPnl = totalPnl / closed.length;
          const stdDev = Math.sqrt(
            pnlArray.reduce((s, p) => s + (p - avgPnl) ** 2, 0) / closed.length,
          );

          let maxDrawdown = 0;
          let peak = 0;
          let cumPnl = 0;
          for (const t of closed) {
            cumPnl += t.netPnl;
            if (cumPnl > peak) peak = cumPnl;
            const dd = peak - cumPnl;
            if (dd > maxDrawdown) maxDrawdown = dd;
          }

          return {
            总交易数: closed.length,
            盈利笔数: wins.length,
            亏损笔数: losses.length,
            持平笔数: closed.length - wins.length - losses.length,
            胜率: `${((wins.length / closed.length) * 100).toFixed(1)}%`,
            总盈亏: `¥${totalPnl.toFixed(2)}`,
            总手续费: `¥${totalCommission.toFixed(2)}`,
            平均盈利: `¥${avgWin.toFixed(2)}`,
            平均亏损: `¥${avgLoss.toFixed(2)}`,
            盈亏比: avgLoss !== 0 ? (Math.abs(avgWin / avgLoss)).toFixed(2) : "∞",
            平均持仓天数: avgHolding.toFixed(1),
            最大回撤: `¥${maxDrawdown.toFixed(2)}`,
            盈亏标准差: `¥${stdDev.toFixed(2)}`,
            期望值: `¥${avgPnl.toFixed(2)}`,
          };
        }

        case "best_worst": {
          const closed = trades.filter((t) => t.exitTime != null);
          if (closed.length === 0) return { message: "暂无已平仓交易记录" };

          const sorted = [...closed].sort((a, b) => b.netPnl - a.netPnl);
          const top5 = sorted.slice(0, 5);
          const bottom5 = sorted.slice(-5).reverse();

          const fmt = (t: TradeRecord) => ({
            symbol: t.symbol,
            name: t.name ?? t.symbol,
            side: t.side,
            entryPrice: `¥${t.entryPrice.toFixed(3)}`,
            exitPrice: t.exitPrice != null ? `¥${t.exitPrice.toFixed(3)}` : "-",
            netPnl: `¥${t.netPnl.toFixed(2)}`,
            holdingDays: t.holdingDays ?? "-",
            strategyId: t.strategyId ?? "-",
            entryTime: t.entryTime,
          });

          return {
            最佳交易: top5.map(fmt),
            最差交易: bottom5.map(fmt),
          };
        }

        case "monthly_summary": {
          const closed = trades.filter((t) => t.exitTime != null);
          if (closed.length === 0) return { message: "暂无已平仓交易记录" };

          const monthMap = new Map<string, { pnl: number; count: number; wins: number }>();
          for (const t of closed) {
            const month = (t.exitTime ?? t.entryTime).slice(0, 7); // YYYY-MM
            const entry = monthMap.get(month) ?? { pnl: 0, count: 0, wins: 0 };
            entry.pnl += t.netPnl;
            entry.count += 1;
            if (t.netPnl > 0) entry.wins += 1;
            monthMap.set(month, entry);
          }

          const months = [...monthMap.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([month, data]) => ({
              月份: month,
              交易笔数: data.count,
              胜率: `${((data.wins / data.count) * 100).toFixed(1)}%`,
              月盈亏: `¥${data.pnl.toFixed(2)}`,
            }));

          const totalPnl = closed.reduce((s, t) => s + t.netPnl, 0);
          return {
            月度汇总: months,
            累计盈亏: `¥${totalPnl.toFixed(2)}`,
          };
        }

        case "by_strategy": {
          const closed = trades.filter((t) => t.exitTime != null);
          if (closed.length === 0) return { message: "暂无已平仓交易记录" };

          const stratMap = new Map<string, TradeRecord[]>();
          for (const t of closed) {
            const key = t.strategyId ?? "手动交易";
            const arr = stratMap.get(key) ?? [];
            arr.push(t);
            stratMap.set(key, arr);
          }

          const strategies = [...stratMap.entries()].map(([name, strades]) => {
            const wins = strades.filter((t) => t.netPnl > 0).length;
            const totalPnl = strades.reduce((s, t) => s + t.netPnl, 0);
            const avgPnl = totalPnl / strades.length;
            return {
              策略: name,
              交易笔数: strades.length,
              胜率: `${((wins / strades.length) * 100).toFixed(1)}%`,
              总盈亏: `¥${totalPnl.toFixed(2)}`,
              平均盈亏: `¥${avgPnl.toFixed(2)}`,
            };
          });

          return { 策略表现: strategies };
        }

        case "export": {
          const format = (args.format as string) || "json";
          const limit = (args.limit as number) || 500;
          const sorted = [...trades]
            .sort((a, b) => new Date(b.entryTime).getTime() - new Date(a.entryTime).getTime())
            .slice(0, limit);

          if (format === "csv") {
            const header = "tradeId,symbol,side,quantity,entryPrice,exitPrice,netPnl,holdingDays,strategyId,entryTime,exitTime";
            const rows = sorted.map((t) =>
              [
                t.tradeId,
                t.symbol,
                t.side,
                t.quantity,
                t.entryPrice.toFixed(3),
                t.exitPrice?.toFixed(3) ?? "",
                t.netPnl.toFixed(2),
                t.holdingDays ?? "",
                t.strategyId ?? "",
                t.entryTime,
                t.exitTime ?? "",
              ].join(","),
            );
            return { format: "csv", data: [header, ...rows].join("\n") };
          }

          return {
            format: "json",
            count: sorted.length,
            data: sorted,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
