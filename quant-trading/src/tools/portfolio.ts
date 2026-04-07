import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * portfolio tool — AI-callable for portfolio management.
 * Supports both paper portfolio (local JSON) and live broker positions.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { MarketDataProvider } from "../data/market-data-provider.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import { computeBatchStopAdvice } from "../services/stop-advisor.js";
import type { KlineBar, Portfolio, PortfolioPosition, QuantTradingConfig } from "../types.js";

function getPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio-paper.json");
}

/** Legacy path — used only for migration fallback */
function getLegacyPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio.json");
}

async function loadPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
  // Try mode-specific file first, then legacy
  for (const filePath of [getPortfolioPath(config), getLegacyPortfolioPath(config)]) {
    try {
      const raw = await fs.readFile(filePath, "utf-8");
      return JSON.parse(raw) as Portfolio;
    } catch {
      continue;
    }
  }
  return {
    id: "default",
    name: "默认组合",
    cash: 100000,
    totalValue: 100000,
    positions: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

async function savePortfolio(portfolio: Portfolio, config: QuantTradingConfig): Promise<void> {
  const filePath = getPortfolioPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  portfolio.updatedAt = new Date().toISOString();
  await fs.writeFile(filePath, JSON.stringify(portfolio, null, 2));
}

export function createPortfolioTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  getDataProvider?: () => MarketDataProvider,
) {
  return {
    name: "portfolio",
    description:
      "管理投资组合（模拟 & 实盘）。" +
      "action=show 查看持仓, action=buy 买入, action=sell 卖出, " +
      "action=add_cash 追加资金, action=summary 组合摘要, " +
      "action=diagnose 持仓诊断(含智能止盈止损建议), " +
      "action=live_sync 同步实盘持仓, action=live_orders 查看实盘订单, " +
      "action=live_trades 查看成交记录, action=daily_pnl 当日盈亏。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "show",
            "buy",
            "sell",
            "add_cash",
            "summary",
            "diagnose",
            "live_sync",
            "live_orders",
            "live_trades",
            "cancel_order",
            "daily_pnl",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码" },
        name: { type: "string", description: "标的名称(可选)" },
        quantity: { type: "number", description: "数量（股/手/份）" },
        price: { type: "number", description: "价格" },
        amount: { type: "number", description: "金额（用于 add_cash）" },
        order_id: { type: "string", description: "订单ID（用于 cancel_order）" },
        status_filter: {
          type: "string",
          description: "订单状态过滤 (submitted|filled|cancelled|rejected)",
        },
        style: {
          type: "string",
          enum: ["short", "mid", "long"],
          description: "交易风格（仅 action=diagnose）: short=短线, mid=中线(默认), long=长线",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "show":
        case "view": {
          // Prefer live broker data when engine is running
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const account = engine.getAccount();
            const positions = engine.getPositions();
            if (account) {
              return {
                name: account.brokerType === "paper" ? "模拟组合" : "实盘组合",
                source: account.brokerType,
                cash: `¥${account.cash.toFixed(2)}`,
                totalValue: `¥${account.totalAssets.toFixed(2)}`,
                marketValue: `¥${account.marketValue.toFixed(2)}`,
                profit: `¥${account.profit.toFixed(2)}`,
                profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                positions: positions.map((p) => ({
                  symbol: p.symbol,
                  name: p.name,
                  quantity: p.quantity,
                  availableQty: p.availableQty,
                  avgCost: `¥${p.avgCost.toFixed(3)}`,
                  currentPrice: p.currentPrice != null ? `¥${p.currentPrice.toFixed(3)}` : "N/A",
                  marketValue: p.marketValue != null ? `¥${p.marketValue.toFixed(2)}` : "N/A",
                  pnl: p.unrealizedPnl != null ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
                  pnlPct:
                    p.unrealizedPnlPct != null
                      ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%`
                      : "N/A",
                })),
              };
            }
          }
          // Fallback to paper portfolio file
          const portfolio = await loadPortfolio(config);
          return {
            name: portfolio.name,
            source: "paper",
            cash: `¥${portfolio.cash.toFixed(2)}`,
            totalValue: `¥${portfolio.totalValue.toFixed(2)}`,
            positions: portfolio.positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              avgCost: `¥${p.avgCost.toFixed(2)}`,
              marketValue: p.marketValue ? `¥${p.marketValue.toFixed(2)}` : "N/A",
              pnl: p.unrealizedPnl ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
              pnlPct: p.unrealizedPnlPct ? `${p.unrealizedPnlPct.toFixed(2)}%` : "N/A",
            })),
          };
        }

        case "buy": {
          const portfolio = await loadPortfolio(config);
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const price = args.price as number;
          if (!symbol || !quantity || !price) {
            return { error: "请提供 symbol, quantity, price" };
          }
          const cost = quantity * price;
          if (cost > portfolio.cash) {
            return {
              error: `资金不足。需要 ¥${cost.toFixed(2)}，可用 ¥${portfolio.cash.toFixed(2)}`,
            };
          }

          portfolio.cash -= cost;
          const existing = portfolio.positions.find((p) => p.symbol === symbol);
          if (existing) {
            const totalQty = existing.quantity + quantity;
            existing.avgCost = (existing.avgCost * existing.quantity + price * quantity) / totalQty;
            existing.quantity = totalQty;
          } else {
            portfolio.positions.push({
              symbol,
              name: (args.name as string) ?? symbol,
              market: "cn_stock",
              quantity,
              avgCost: price,
            });
          }
          portfolio.totalValue =
            portfolio.cash +
            portfolio.positions.reduce((s, p) => s + p.quantity * (p.currentPrice ?? p.avgCost), 0);
          await savePortfolio(portfolio, config);
          return {
            success: true,
            message: `买入 ${symbol} ${quantity}股 @ ¥${price}`,
            cash: `¥${portfolio.cash.toFixed(2)}`,
          };
        }

        case "sell": {
          const portfolio = await loadPortfolio(config);
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const price = args.price as number;
          if (!symbol || !quantity || !price) {
            return { error: "请提供 symbol, quantity, price" };
          }
          const pos = portfolio.positions.find((p) => p.symbol === symbol);
          if (!pos || pos.quantity < quantity) {
            return { error: `持仓不足: ${symbol} 当前持有 ${pos?.quantity ?? 0}股` };
          }

          const proceeds = quantity * price;
          portfolio.cash += proceeds;
          pos.quantity -= quantity;
          if (pos.quantity === 0) {
            portfolio.positions = portfolio.positions.filter((p) => p.symbol !== symbol);
          }
          portfolio.totalValue =
            portfolio.cash +
            portfolio.positions.reduce((s, p) => s + p.quantity * (p.currentPrice ?? p.avgCost), 0);
          await savePortfolio(portfolio, config);
          return {
            success: true,
            message: `卖出 ${symbol} ${quantity}股 @ ¥${price}`,
            cash: `¥${portfolio.cash.toFixed(2)}`,
          };
        }

        case "add_cash": {
          const portfolio = await loadPortfolio(config);
          const amount = args.amount as number;
          if (!amount || amount <= 0) return { error: "请提供正数金额" };
          portfolio.cash += amount;
          portfolio.totalValue += amount;
          await savePortfolio(portfolio, config);
          return { success: true, cash: `¥${portfolio.cash.toFixed(2)}` };
        }

        case "summary": {
          // Prefer live broker data when engine is running
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const account = engine.getAccount();
            const positions = engine.getPositions();
            if (account) {
              return {
                name: account.brokerType === "paper" ? "模拟组合" : "实盘组合",
                source: account.brokerType,
                cash: `¥${account.cash.toFixed(2)}`,
                marketValue: `¥${account.marketValue.toFixed(2)}`,
                totalValue: `¥${account.totalAssets.toFixed(2)}`,
                profit: `¥${account.profit.toFixed(2)}`,
                profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                positionCount: positions.length,
              };
            }
          }
          const portfolio = await loadPortfolio(config);
          const totalCost = portfolio.positions.reduce((s, p) => s + p.quantity * p.avgCost, 0);
          return {
            name: portfolio.name,
            source: "paper",
            cash: `¥${portfolio.cash.toFixed(2)}`,
            stockValue: `¥${totalCost.toFixed(2)}`,
            totalValue: `¥${(portfolio.cash + totalCost).toFixed(2)}`,
            positionCount: portfolio.positions.length,
          };
        }

        case "live_sync": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动，请先执行 start_engine" };
          // Auto-start engine if it's not running yet
          const status = engine.getStatus();
          if (status === "stopped" || status === "error") {
            try {
              await engine.start();
            } catch (err) {
              return {
                error: `实盘引擎启动失败: ${(err as Error).message}。请检查 tradingAccount 配置（broker/mode/qmt 等）`,
              };
            }
          }
          // Force a fresh broker sync instead of reading possibly-stale cache
          const { account, positions } = await engine.syncNow();
          if (!account) {
            return {
              error:
                '未能获取实盘账户数据。请检查：1) tradingAccount.broker 是否设为 "qmt"；' +
                '2) tradingAccount.mode 是否设为 "live"；3) miniQMT 是否正在运行。' +
                `当前引擎状态: ${engine.getStatus()}`,
            };
          }
          return {
            account: account
              ? {
                  cash: `¥${account.cash.toFixed(2)}`,
                  frozenCash: `¥${account.frozenCash.toFixed(2)}`,
                  totalAssets: `¥${account.totalAssets.toFixed(2)}`,
                  marketValue: `¥${account.marketValue.toFixed(2)}`,
                  profit: `¥${account.profit.toFixed(2)}`,
                  profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                }
              : null,
            positions: positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              availableQty: p.availableQty,
              avgCost: `¥${p.avgCost.toFixed(3)}`,
              currentPrice: p.currentPrice != null ? `¥${p.currentPrice.toFixed(3)}` : "N/A",
              marketValue: p.marketValue != null ? `¥${p.marketValue.toFixed(2)}` : "N/A",
              unrealizedPnl: p.unrealizedPnl != null ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
              unrealizedPnlPct:
                p.unrealizedPnlPct != null ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%` : "N/A",
            })),
          };
        }

        case "live_orders": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const statusFilter = args.status_filter as string | undefined;
          const symbol = args.symbol as string | undefined;
          const orders = await engine.orderManager.getOrders({
            ...(symbol && { symbol }),
          });
          const filtered = statusFilter ? orders.filter((o) => o.status === statusFilter) : orders;
          return {
            count: filtered.length,
            orders: filtered.slice(0, 50).map((o) => ({
              orderId: o.orderId,
              symbol: o.symbol,
              side: o.side,
              type: o.orderType,
              qty: o.quantity,
              filledQty: o.filledQty,
              price: o.price ?? "市价",
              avgFillPrice: o.avgFillPrice ?? "-",
              status: o.status,
              createdAt: o.createdAt,
            })),
          };
        }

        case "live_trades": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const sym = args.symbol as string | undefined;
          const trades = await engine.orderManager.getTrades(sym);
          return {
            count: trades.length,
            trades: trades.slice(0, 50).map((t) => ({
              symbol: t.symbol,
              entryPrice: `¥${t.entryPrice.toFixed(3)}`,
              exitPrice: t.exitPrice != null ? `¥${t.exitPrice.toFixed(3)}` : "持仓中",
              quantity: t.quantity,
              netPnl: `¥${t.netPnl.toFixed(2)}`,
              exitTime: t.exitTime ?? "-",
            })),
          };
        }

        case "cancel_order": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const orderId = args.order_id as string | undefined;
          if (!orderId) return { error: "请提供 order_id" };
          try {
            await engine.orderManager.cancel(orderId);
            return { success: true, message: `订单 ${orderId} 已请求撤单` };
          } catch (e) {
            return { error: (e as Error).message };
          }
        }

        case "daily_pnl": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const netPnl = await engine.orderManager.getDailyPnl();
          return {
            date: new Date().toISOString().slice(0, 10),
            netPnl: `¥${netPnl.toFixed(2)}`,
          };
        }

        case "diagnose": {
          // Gather positions from live engine or paper portfolio
          let positions: Array<{ symbol: string; name?: string; avgCost: number; currentPrice?: number; quantity: number }> = [];
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const livePositions = engine.getPositions();
            positions = livePositions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              avgCost: p.avgCost,
              currentPrice: p.currentPrice ?? undefined,
              quantity: p.quantity,
            }));
          } else {
            const portfolio = await loadPortfolio(config);
            positions = portfolio.positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              avgCost: p.avgCost,
              currentPrice: p.currentPrice ?? undefined,
              quantity: p.quantity,
            }));
          }

          if (positions.length === 0) {
            return { ok: true, message: "当前无持仓，无需诊断" };
          }

          if (!getDataProvider) {
            return { error: "数据源不可用，无法计算止盈止损建议" };
          }

          const provider = getDataProvider();
          const klEndDate = new Date().toISOString().slice(0, 10);
          const klStartDate = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
          const style = (args.style as "short" | "mid" | "long") ?? "mid";

          // Fetch klines for all positions in parallel
          const positionsWithBars = await Promise.all(
            positions.map(async (pos) => {
              const market = detectMarketFromSymbol(pos.symbol);
              let bars: KlineBar[] = [];
              let currentPrice = pos.currentPrice ?? pos.avgCost;
              try {
                bars = await provider.getKlines(pos.symbol, "1d", klStartDate, klEndDate, market);
                if (bars.length > 0 && !pos.currentPrice) {
                  currentPrice = bars[bars.length - 1].close;
                }
              } catch {
                // Will return error for this position
              }
              try {
                const quote = await provider.getQuote(pos.symbol, market);
                if (quote) currentPrice = quote.price;
              } catch {
                // Use bar close as fallback
              }
              return { symbol: pos.symbol, entryPrice: pos.avgCost, currentPrice, bars };
            }),
          );

          const results = computeBatchStopAdvice(positionsWithBars, { style });
          const diagnostics = results.map((r, idx) => {
            const pos = positions[idx];
            if ("error" in r) {
              return {
                symbol: pos.symbol,
                name: pos.name,
                quantity: pos.quantity,
                error: r.error,
              };
            }
            const pnlPct = ((r.currentPrice - r.entryPrice) / r.entryPrice * 100).toFixed(2);
            const rec = r.recommended;
            const needStopLoss = r.currentPrice < rec.stopLoss;
            return {
              symbol: pos.symbol,
              name: pos.name,
              quantity: pos.quantity,
              avgCost: `¥${r.entryPrice}`,
              currentPrice: `¥${r.currentPrice}`,
              unrealizedPnlPct: `${pnlPct}%`,
              alert: needStopLoss ? "⚠️ 已跌破建议止损位" : null,
              recommended: {
                stopLoss: `¥${rec.stopLoss}`,
                takeProfit: `¥${rec.takeProfit}`,
                stopLossPct: `${(rec.stopLossPct * 100).toFixed(1)}%`,
                takeProfitPct: `${(rec.takeProfitPct * 100).toFixed(1)}%`,
                rewardRiskRatio: `${rec.rewardRiskRatio}:1`,
                method: rec.method,
                description: rec.description,
              },
              marketStats: {
                ATR14: `¥${r.marketStats.atr14}`,
                年化波动率: `${(r.marketStats.volatility20 * 100).toFixed(1)}%`,
                MA20: `¥${r.marketStats.ma20}`,
              },
            };
          });

          const alerts = diagnostics.filter((d) => "alert" in d && d.alert);

          return {
            ok: true,
            style: style === "short" ? "短线" : style === "mid" ? "中线" : "长线",
            totalPositions: positions.length,
            alerts: alerts.length > 0
              ? `${alerts.length}只持仓需要关注止损`
              : "所有持仓在安全范围内",
            positions: diagnostics,
            disclaimer: "以上为量化模型计算的参考建议，不构成投资建议，请结合自身判断决策。",
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
