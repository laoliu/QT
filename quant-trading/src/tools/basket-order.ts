/**
 * basket_order tool — AI-callable interface for batch order management.
 *
 * Actions:
 *   create_basket     — create a new basket of orders (draft)
 *   add_items         — add items to an existing draft basket
 *   remove_item       — remove an item from a draft basket
 *   submit_basket     — submit all orders in a basket
 *   cancel_basket     — cancel a basket and its open orders
 *   get_basket        — get basket details and progress
 *   list_baskets      — list all baskets
 *   refresh_basket    — refresh order statuses from broker
 *   delete_basket     — delete a draft/completed/cancelled basket
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BasketOrderService } from "../services/basket-order.js";
import type { OrderSide, OrderType, QuantTradingConfig } from "../types.js";

export function createBasketOrderTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  basketService: BasketOrderService,
) {
  return {
    name: "basket_order",
    description:
      "篮子订单管理 — 批量下单多个标的。" +
      "action=create_basket 创建篮子, action=add_items 添加标的, " +
      "action=remove_item 移除标的, action=submit_basket 提交篮子, " +
      "action=cancel_basket 取消篮子, action=get_basket 查看篮子, " +
      "action=list_baskets 列出所有篮子, action=refresh_basket 刷新状态, " +
      "action=delete_basket 删除篮子。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "create_basket",
            "add_items",
            "remove_item",
            "submit_basket",
            "cancel_basket",
            "get_basket",
            "list_baskets",
            "refresh_basket",
            "delete_basket",
          ],
          description: "操作类型",
        },
        // For create_basket
        name: { type: "string", description: "篮子名称 (e.g. '板块轮动组合', '调仓卖出')" },
        description: { type: "string", description: "篮子描述" },
        items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "标的代码 (e.g. '600519')" },
              side: {
                type: "string",
                enum: ["buy", "sell", "buy_open", "sell_close", "buy_close", "sell_open"],
                description: "买卖方向",
              },
              order_type: {
                type: "string",
                enum: ["market", "limit"],
                description: "订单类型 (默认 limit)",
              },
              quantity: { type: "number", description: "数量（股）" },
              price: { type: "number", description: "价格（limit order 必填）" },
              note: { type: "string", description: "备注" },
            },
            required: ["symbol", "side", "quantity"],
          },
          description: "订单列表",
        },
        inter_order_delay_ms: {
          type: "number",
          description: "订单间隔毫秒数 (默认 500ms)",
        },
        // For operations on existing baskets
        basket_id: { type: "string", description: "篮子ID" },
        item_id: { type: "string", description: "标的项ID (用于 remove_item)" },
        // For list_baskets filter
        status: {
          type: "string",
          enum: ["draft", "submitting", "active", "completed", "cancelled"],
          description: "按状态过滤",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "create_basket": {
          const name = args.name as string;
          const items = args.items as
            | Array<{
                symbol: string;
                side: string;
                order_type?: string;
                quantity: number;
                price?: number;
                note?: string;
              }>
            | undefined;

          if (!name) return { error: "请提供篮子名称 name" };
          if (!items || items.length === 0) return { error: "请提供至少一个标的 items" };

          try {
            const basket = await basketService.create({
              name,
              description: args.description as string | undefined,
              items: items.map((i) => ({
                symbol: i.symbol,
                side: i.side as OrderSide,
                orderType: (i.order_type ?? "limit") as OrderType,
                quantity: i.quantity,
                price: i.price,
                note: i.note,
              })),
              interOrderDelayMs: args.inter_order_delay_ms as number | undefined,
            });
            return {
              message: `篮子「${basket.name}」已创建`,
              basketId: basket.basketId,
              totalItems: basket.totalItems,
              status: basket.status,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "add_items": {
          const basketId = args.basket_id as string;
          const items = args.items as
            | Array<{
                symbol: string;
                side: string;
                order_type?: string;
                quantity: number;
                price?: number;
                note?: string;
              }>
            | undefined;

          if (!basketId) return { error: "请提供 basket_id" };
          if (!items || items.length === 0) return { error: "请提供至少一个标的 items" };

          try {
            const basket = await basketService.addItems(
              basketId,
              items.map((i) => ({
                symbol: i.symbol,
                side: i.side as OrderSide,
                orderType: (i.order_type ?? "limit") as OrderType,
                quantity: i.quantity,
                price: i.price,
                note: i.note,
              })),
            );
            return {
              message: `已添加 ${items.length} 个标的到篮子「${basket.name}」`,
              totalItems: basket.totalItems,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "remove_item": {
          const basketId = args.basket_id as string;
          const itemId = args.item_id as string;
          if (!basketId) return { error: "请提供 basket_id" };
          if (!itemId) return { error: "请提供 item_id" };

          try {
            const basket = await basketService.removeItem(basketId, itemId);
            return {
              message: `已移除标的，剩余 ${basket.totalItems} 个`,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "submit_basket": {
          const basketId = args.basket_id as string;
          if (!basketId) return { error: "请提供 basket_id" };

          try {
            const basket = await basketService.submit(basketId);
            return {
              message: `篮子「${basket.name}」提交完成`,
              status: basket.status,
              totalItems: basket.totalItems,
              submitted: basket.submittedCount,
              filled: basket.filledCount,
              failed: basket.failedCount,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "cancel_basket": {
          const basketId = args.basket_id as string;
          if (!basketId) return { error: "请提供 basket_id" };

          try {
            const basket = await basketService.cancel(basketId);
            return {
              message: `篮子「${basket.name}」已取消`,
              status: basket.status,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "get_basket": {
          const basketId = args.basket_id as string;
          if (!basketId) return { error: "请提供 basket_id" };

          const basket = basketService.get(basketId);
          if (!basket) return { error: `篮子 ${basketId.slice(0, 8)} 不存在` };

          return {
            basketId: basket.basketId,
            name: basket.name,
            description: basket.description,
            status: basket.status,
            totalItems: basket.totalItems,
            submitted: basket.submittedCount,
            filled: basket.filledCount,
            failed: basket.failedCount,
            createdAt: basket.createdAt,
            submittedAt: basket.submittedAt,
            completedAt: basket.completedAt,
            items: basket.items.map(formatItem),
          };
        }

        case "list_baskets": {
          const status = args.status as string | undefined;
          const baskets = basketService.list(status as never);
          return {
            total: baskets.length,
            baskets: baskets.map((b) => ({
              basketId: b.basketId,
              name: b.name,
              status: b.status,
              totalItems: b.totalItems,
              filled: b.filledCount,
              failed: b.failedCount,
              createdAt: b.createdAt,
            })),
          };
        }

        case "refresh_basket": {
          const basketId = args.basket_id as string;
          if (!basketId) return { error: "请提供 basket_id" };

          try {
            const basket = await basketService.refresh(basketId);
            return {
              message: `篮子「${basket.name}」状态已刷新`,
              status: basket.status,
              totalItems: basket.totalItems,
              submitted: basket.submittedCount,
              filled: basket.filledCount,
              failed: basket.failedCount,
              items: basket.items.map(formatItem),
            };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        case "delete_basket": {
          const basketId = args.basket_id as string;
          if (!basketId) return { error: "请提供 basket_id" };

          try {
            await basketService.delete(basketId);
            return { message: `篮子已删除` };
          } catch (err) {
            return { error: (err as Error).message };
          }
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Formatting helpers ──────────────────────────────────────────

function formatItem(item: {
  itemId: string;
  symbol: string;
  side: string;
  orderType: string;
  quantity: number;
  price?: number;
  status: string;
  orderId?: string;
  errorMsg?: string;
  note?: string;
}) {
  return {
    itemId: item.itemId,
    symbol: item.symbol,
    side: item.side,
    orderType: item.orderType,
    quantity: item.quantity,
    price: item.price ?? "市价",
    status: item.status,
    orderId: item.orderId?.slice(0, 8),
    error: item.errorMsg,
    note: item.note,
  };
}
