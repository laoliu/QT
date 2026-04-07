/**
 * trading_dashboard tool — consolidated real-time trading status snapshot.
 *
 * Actions:
 *   snapshot      — full dashboard: positions + open orders + conditional orders + P&L
 *   open_orders   — all pending/submitted orders across paper & live
 *   risk_summary  — combined risk metrics snapshot
 *   activity_log  — recent trading activity (orders, fills, signals)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { detectMarketFromSymbol, createDataProvider } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { ConditionalOrderService } from "../services/conditional-order.js";
import type { PriceAlertService } from "../services/price-alert.js";
import type { QuantTradingConfig, Portfolio } from "../types.js";

export function createTradingDashboardTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  deps: {
    loadPortfolio: () => Promise<Portfolio>;
    condOrderService: ConditionalOrderService;
    priceAlertService: PriceAlertService;
  },
) {
  return {
    name: "trading_dashboard",
    description:
      "交易仪表盘。一站式查看当前交易状态：持仓、挂单、条件单、预警、盈亏。\n" +
      "action=snapshot 完整快照, action=open_orders 未成交订单, " +
      "action=risk_summary 风控摘要, action=activity_log 近期活动。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["snapshot", "open_orders", "risk_summary", "activity_log"],
          description: "操作类型",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const engine = getEngine();

      switch (action) {
        case "snapshot": {
          const portfolio = await deps.loadPortfolio();
          const dp = createDataProvider(config);

          // Get live positions if engine is running
          let livePositions: Array<{
            symbol: string;
            name?: string;
            quantity: number;
            avgCost: number;
            currentPrice?: number;
            unrealizedPnl?: string;
            changePct?: string;
          }> = [];

          const isRunning = engine?.getStatus?.() === "running";
          if (isRunning && engine!.broker) {
            try {
              const positions = await engine!.broker.queryPositions();
              for (const pos of positions) {
                livePositions.push({
                  symbol: pos.symbol,
                  name: pos.name,
                  quantity: pos.quantity,
                  avgCost: pos.avgCost,
                  currentPrice: pos.currentPrice,
                  unrealizedPnl:
                    pos.unrealizedPnl != null ? `¥${pos.unrealizedPnl.toFixed(2)}` : undefined,
                  changePct:
                    pos.unrealizedPnlPct != null
                      ? `${pos.unrealizedPnlPct >= 0 ? "+" : ""}${pos.unrealizedPnlPct.toFixed(2)}%`
                      : undefined,
                });
              }
            } catch {
              // Engine running but query failed — use paper portfolio as fallback
            }
          }

          // Fallback to paper portfolio
          if (livePositions.length === 0 && portfolio.positions.length > 0) {
            for (const pos of portfolio.positions) {
              try {
                const market = detectMarketFromSymbol(pos.symbol);
                const quote = await dp.getQuote(pos.symbol, market as never);
                const curPrice = quote?.price;
                const pnl = curPrice != null ? (curPrice - pos.avgCost) * pos.quantity : undefined;
                const pnlPct = curPrice != null
                  ? ((curPrice - pos.avgCost) / pos.avgCost) * 100
                  : undefined;
                livePositions.push({
                  symbol: pos.symbol,
                  name: pos.name,
                  quantity: pos.quantity,
                  avgCost: pos.avgCost,
                  currentPrice: curPrice,
                  unrealizedPnl: pnl != null ? `¥${pnl.toFixed(2)}` : undefined,
                  changePct: pnlPct != null
                    ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%`
                    : undefined,
                });
              } catch {
                livePositions.push({
                  symbol: pos.symbol,
                  name: pos.name,
                  quantity: pos.quantity,
                  avgCost: pos.avgCost,
                });
              }
            }
          }

          // Open orders
          let openOrders: Array<{ orderId: string; symbol: string; side: string; quantity: number; price?: number; status: string }> = [];
          if (engine?.orderManager) {
            const orders = await engine.orderManager.getOrders({
              status: ["submitted", "pending", "partial"],
            });
            openOrders = orders.map((o) => ({
              orderId: o.orderId,
              symbol: o.symbol,
              side: o.side,
              quantity: o.quantity,
              price: o.price,
              status: o.status,
            }));
          }

          // Conditional orders
          const condOrders = deps.condOrderService.listOrders();

          // Price alerts count
          const alerts = deps.priceAlertService.listAlerts();

          // Account summary
          let accountSummary: {
            cash: string;
            totalAssets: string;
            todayPnl?: string;
          } | null = null;
          if (isRunning && engine!.broker) {
            try {
              const account = await engine!.broker.queryAccount();
              accountSummary = {
                cash: `¥${(account.cash ?? 0).toFixed(2)}`,
                totalAssets: `¥${(account.totalAssets ?? 0).toFixed(2)}`,
                todayPnl: account.todayProfit != null
                  ? `¥${account.todayProfit.toFixed(2)}`
                  : undefined,
              };
            } catch {
              // ignore
            }
          }
          if (!accountSummary) {
            accountSummary = {
              cash: `¥${portfolio.cash.toFixed(2)}`,
              totalAssets: `¥${portfolio.totalValue.toFixed(2)}`,
            };
          }

          return {
            时间: new Date().toISOString(),
            引擎状态: isRunning ? "运行中" : (engine?.getStatus?.() ?? "未启动"),
            账户: accountSummary,
            持仓: {
              数量: livePositions.length,
              明细: livePositions,
            },
            挂单: {
              数量: openOrders.length,
              明细: openOrders,
            },
            条件单: {
              数量: condOrders.length,
              明细: condOrders.slice(0, 10).map((o: any) => ({
                id: o.id,
                symbol: o.symbol,
                type: o.type,
                triggerValue: o.triggerValue,
                triggered: o.triggered,
                description: o.description,
              })),
            },
            价格预警: {
              数量: alerts.length,
            },
          };
        }

        case "open_orders": {
          if (!engine?.orderManager) {
            return { message: "引擎未启动，无挂单记录" };
          }
          const orders = await engine.orderManager.getOrders({
            status: ["submitted", "pending", "partial"],
          });
          return {
            总数: orders.length,
            订单: orders.map((o) => ({
              orderId: o.orderId,
              symbol: o.symbol,
              side: o.side,
              orderType: o.orderType,
              quantity: o.quantity,
              filledQty: o.filledQty,
              price: o.price != null ? `¥${o.price.toFixed(3)}` : "市价",
              status: o.status,
              createdAt: o.createdAt,
            })),
          };
        }

        case "risk_summary": {
          const portfolio = await deps.loadPortfolio();
          const posCount = portfolio.positions.length;
          const totalValue = portfolio.totalValue;
          const cashPct = totalValue > 0 ? ((portfolio.cash / totalValue) * 100) : 100;

          // Concentration analysis
          let maxPct = 0;
          let maxSymbol = "";
          for (const pos of portfolio.positions) {
            const posValue = pos.quantity * pos.avgCost;
            const pct = totalValue > 0 ? (posValue / totalValue) * 100 : 0;
            if (pct > maxPct) {
              maxPct = pct;
              maxSymbol = pos.symbol;
            }
          }

          const condOrders = deps.condOrderService.listOrders();
          const hasStopLoss = condOrders.some(
            (o) => o.type === "stop_loss" || o.type === "trailing_stop" || o.type === "trailing_atr",
          );

          return {
            持仓数: posCount,
            总资产: `¥${totalValue.toFixed(2)}`,
            现金占比: `${cashPct.toFixed(1)}%`,
            最大单票占比: maxPct > 0 ? `${maxSymbol}: ${maxPct.toFixed(1)}%` : "-",
            止损保护: hasStopLoss ? `已设置 (${condOrders.length}个条件单)` : "⚠️ 未设置止损",
            风险提示: [
              ...(cashPct < 10 ? ["现金比例过低 (<10%)，建议保留部分现金"] : []),
              ...(maxPct > 30 ? [`${maxSymbol} 单票占比超过30%，注意集中度风险`] : []),
              ...(!hasStopLoss && posCount > 0 ? ["建议为持仓设置止损条件单"] : []),
              ...(posCount === 0 ? ["当前空仓"] : []),
            ],
          };
        }

        case "activity_log": {
          if (!engine?.orderManager) {
            return { message: "引擎未启动，无活动记录" };
          }

          // Recent orders (last 20)
          const allOrders = await engine.orderManager.getOrders();
          const recentOrders = allOrders
            .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
            .slice(0, 20)
            .map((o) => ({
              time: o.updatedAt,
              type: "order" as const,
              symbol: o.symbol,
              side: o.side,
              quantity: o.quantity,
              price: o.avgFillPrice ?? o.price,
              status: o.status,
            }));

          // Recent trades (last 10)
          const trades = await engine.orderManager.getTrades();
          const recentTrades = trades
            .sort((a, b) => new Date(b.entryTime).getTime() - new Date(a.entryTime).getTime())
            .slice(0, 10)
            .map((t) => ({
              time: t.exitTime ?? t.entryTime,
              type: "trade" as const,
              symbol: t.symbol,
              side: t.side,
              netPnl: `¥${t.netPnl.toFixed(2)}`,
            }));

          return {
            近期订单: recentOrders,
            近期成交: recentTrades,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
