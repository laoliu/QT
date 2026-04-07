/**
 * batch_order tool — AI-callable interface for placing multiple orders at once.
 *
 * Actions:
 *   batch_buy    — buy multiple stocks in a single operation
 *   batch_sell   — sell multiple stocks in a single operation
 *   equal_weight — buy a list of stocks with equal weight allocation
 *   preview      — preview batch order details without executing
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderRequest } from "../broker/adapter.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

interface BatchItem {
  symbol: string;
  quantity?: number;
  price?: number;
  amount?: number; // CNY amount for auto-quantity calculation
}

export function createBatchOrderTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "batch_order",
    description:
      "批量下单工具。一次买入/卖出多只股票，支持等权配置。\n" +
      "action=batch_buy 批量买入, action=batch_sell 批量卖出, " +
      "action=equal_weight 等权买入(指定总金额和股票列表), " +
      "action=preview 预览订单(不执行)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["batch_buy", "batch_sell", "equal_weight", "preview"],
          description: "操作类型",
        },
        orders: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              quantity: { type: "number", description: "数量(股)" },
              price: { type: "number", description: "限价(可选，不填为市价)" },
              amount: { type: "number", description: "金额(元)，自动计算数量" },
            },
            required: ["symbol"],
          },
          description: "订单列表 (batch_buy/batch_sell 时使用)",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "股票代码列表 (equal_weight 时使用)",
        },
        total_amount: {
          type: "number",
          description: "总投资金额 (equal_weight 时使用)",
        },
        order_type: {
          type: "string",
          enum: ["market", "limit"],
          description: "订单类型 (默认limit)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const engine = getEngine();

      if (action !== "preview" && !engine) {
        return { error: "实盘引擎未启动，请先启动引擎 (live_trading action=start_engine)" };
      }
      if (action !== "preview" && !engine?.orderManager) {
        return { error: "订单管理器未就绪" };
      }

      const orderType = (args.order_type as string) || "limit";

      switch (action) {
        case "batch_buy":
        case "batch_sell": {
          const items = args.orders as BatchItem[] | undefined;
          if (!items || items.length === 0) {
            return { error: "请提供 orders 数组" };
          }
          if (items.length > 20) {
            return { error: "单次批量下单最多20只股票" };
          }

          const side = action === "batch_buy" ? "buy" : "sell";
          const results: Array<{ symbol: string; status: string; orderId?: string; error?: string }> = [];

          for (const item of items) {
            try {
              let qty = item.quantity;

              // If amount is given instead of quantity, we need a price to calculate
              if (!qty && item.amount && item.price) {
                qty = Math.floor(item.amount / item.price / 100) * 100; // round down to lot of 100
                if (qty <= 0) {
                  results.push({
                    symbol: item.symbol,
                    status: "skipped",
                    error: `金额 ¥${item.amount} 不足以买入1手(100股) @ ¥${item.price}`,
                  });
                  continue;
                }
              }

              if (!qty || qty <= 0) {
                results.push({
                  symbol: item.symbol,
                  status: "skipped",
                  error: "未指定数量且无法自动计算",
                });
                continue;
              }

              const market = detectMarketFromSymbol(item.symbol);
              const req: OrderRequest = {
                symbol: item.symbol,
                market,
                side,
                orderType: orderType as "market" | "limit",
                quantity: qty,
                price: item.price,
                strategyId: "batch_order",
                signalId: `batch_${Date.now()}_${item.symbol}`,
              };

              const result = await engine!.orderManager!.submit(req);
              if (result.ok) {
                results.push({
                  symbol: item.symbol,
                  status: "submitted",
                  orderId: result.order.orderId,
                });
              } else {
                results.push({
                  symbol: item.symbol,
                  status: "rejected",
                  error: result.reason,
                });
              }
            } catch (e) {
              results.push({
                symbol: item.symbol,
                status: "failed",
                error: (e as Error).message,
              });
            }
          }

          const submitted = results.filter((r) => r.status === "submitted").length;
          const failed = results.filter((r) => r.status !== "submitted").length;
          return {
            action: side,
            总数: items.length,
            成功提交: submitted,
            失败或跳过: failed,
            明细: results,
          };
        }

        case "equal_weight": {
          const symbols = args.symbols as string[] | undefined;
          const totalAmount = args.total_amount as number | undefined;
          if (!symbols || symbols.length === 0) return { error: "请提供 symbols 数组" };
          if (!totalAmount || totalAmount <= 0) return { error: "请提供 total_amount (总金额)" };
          if (symbols.length > 20) return { error: "等权配置最多20只股票" };

          const perStock = totalAmount / symbols.length;
          const results: Array<{
            symbol: string;
            allocatedAmount: string;
            quantity: number;
            price?: number;
            status: string;
            orderId?: string;
            error?: string;
          }> = [];

          // Get current prices via data provider
          const { createDataProvider } = await import("../data/market-data-provider.js");
          const dp = createDataProvider(config);

          for (const sym of symbols) {
            try {
              const market = detectMarketFromSymbol(sym);
              const quote = await dp.getQuote(sym, market as never);
              if (!quote) {
                results.push({
                  symbol: sym,
                  allocatedAmount: `¥${perStock.toFixed(2)}`,
                  quantity: 0,
                  status: "failed",
                  error: "无法获取报价",
                });
                continue;
              }

              const qty = Math.floor(perStock / quote.price / 100) * 100;
              if (qty <= 0) {
                results.push({
                  symbol: sym,
                  allocatedAmount: `¥${perStock.toFixed(2)}`,
                  quantity: 0,
                  price: quote.price,
                  status: "skipped",
                  error: `分配金额 ¥${perStock.toFixed(0)} 不足以买入1手 @ ¥${quote.price.toFixed(2)}`,
                });
                continue;
              }

              const req: OrderRequest = {
                symbol: sym,
                market,
                side: "buy",
                orderType: orderType as "market" | "limit",
                quantity: qty,
                price: quote.price,
                strategyId: "equal_weight",
                signalId: `eqw_${Date.now()}_${sym}`,
              };

              const result = await engine!.orderManager!.submit(req);
              if (result.ok) {
                results.push({
                  symbol: sym,
                  allocatedAmount: `¥${perStock.toFixed(2)}`,
                  quantity: qty,
                  price: quote.price,
                  status: "submitted",
                  orderId: result.order.orderId,
                });
              } else {
                results.push({
                  symbol: sym,
                  allocatedAmount: `¥${perStock.toFixed(2)}`,
                  quantity: qty,
                  price: quote.price,
                  status: "rejected",
                  error: result.reason,
                });
              }
            } catch (e) {
              results.push({
                symbol: sym,
                allocatedAmount: `¥${perStock.toFixed(2)}`,
                quantity: 0,
                status: "failed",
                error: (e as Error).message,
              });
            }
          }

          const submitted = results.filter((r) => r.status === "submitted").length;
          return {
            action: "equal_weight_buy",
            总金额: `¥${totalAmount.toFixed(2)}`,
            每只分配: `¥${perStock.toFixed(2)}`,
            股票数: symbols.length,
            成功提交: submitted,
            明细: results,
          };
        }

        case "preview": {
          const items = args.orders as BatchItem[] | undefined;
          const symbols = args.symbols as string[] | undefined;
          const totalAmount = args.total_amount as number | undefined;

          if (symbols && totalAmount) {
            // Preview equal weight
            const perStock = totalAmount / symbols.length;
            return {
              模式: "等权配置预览",
              总金额: `¥${totalAmount.toFixed(2)}`,
              每只分配: `¥${perStock.toFixed(2)}`,
              股票: symbols,
              提示: "实际下单数量将根据当前价格按100股整数倍计算",
            };
          }

          if (items) {
            return {
              模式: "批量订单预览",
              订单数: items.length,
              订单: items.map((it) => ({
                symbol: it.symbol,
                quantity: it.quantity ?? `按金额 ¥${it.amount} 计算`,
                price: it.price ?? "市价",
              })),
            };
          }

          return { error: "请提供 orders 或 symbols + total_amount" };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
