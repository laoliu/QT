/**
 * basket_order tool — AI-callable tool for creating and managing
 * basket (batch) orders.
 *
 * Actions:
 *   create  — Create and execute a basket order (batch of legs)
 *   list    — List all basket orders
 *   get     — Get details of a specific basket order
 *   cancel  — Cancel a basket order
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BasketOrderService, CreateBasketRequest } from "../services/basket-order.js";
import type { BasketLeg, BasketOrder, BasketOrderStatus } from "../types.js";

const STATUS_LABEL: Record<BasketOrderStatus, string> = {
  pending: "待执行",
  executing: "执行中",
  partial: "部分执行",
  completed: "全部成交",
  failed: "全部失败",
  cancelled: "已取消",
  partial_filled: "部分成交",
};

function formatLeg(leg: BasketLeg): string {
  const price = leg.price ? `@${leg.price}` : "@市价";
  const fill =
    leg.filledQty > 0
      ? ` (成交${leg.filledQty}/${leg.quantity}${leg.avgFillPrice ? `, 均价${leg.avgFillPrice.toFixed(2)}` : ""})`
      : "";
  const err = leg.errorMsg ? ` [${leg.errorMsg}]` : "";
  return `  #${leg.index} ${leg.side} ${leg.symbol} ${leg.quantity}${price} — ${leg.status}${fill}${err}`;
}

function formatBasket(basket: BasketOrder, detail: boolean): string {
  const name = basket.name ?? basket.basketId.slice(0, 8);
  const header =
    `篮子: ${name} (${basket.basketId.slice(0, 8)})\n` +
    `状态: ${STATUS_LABEL[basket.status]}\n` +
    `腿数: ${basket.legs.length}, 并发: ${basket.concurrency}, 失败策略: ${basket.failurePolicy}\n` +
    `创建: ${basket.createdAt}` +
    (basket.completedAt ? `, 完成: ${basket.completedAt}` : "");

  if (!detail) return header;

  const legs = basket.legs.map(formatLeg).join("\n");
  return `${header}\n\n腿明细:\n${legs}`;
}

export function createBasketOrderTool(
  _api: OpenClawPluginApi,
  basketService: BasketOrderService,
) {
  return {
    name: "basket_order",
    description:
      "篮子订单工具。批量下单、管理篮子(一组股票订单)的创建与执行。\n" +
      "action=create 创建并执行篮子订单, action=list 列出篮子, " +
      "action=get 查询篮子详情, action=cancel 取消篮子\n\n" +
      "示例: 同时买入3只股票 → create, legs=[{symbol:'600519',side:'buy',order_type:'limit',quantity:100,price:1800},{symbol:'000858',side:'buy',order_type:'market',quantity:200},...], concurrency=3",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["create", "list", "get", "cancel"],
          description: "操作类型",
        },
        // create params
        name: { type: "string", description: "篮子名称 (create, 可选)" },
        legs: {
          type: "array",
          description:
            "订单腿列表 (create)。每条腿: {symbol, side, order_type, quantity, price?}",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              side: {
                type: "string",
                enum: ["buy", "sell", "short", "cover"],
                description: "买卖方向",
              },
              order_type: {
                type: "string",
                enum: ["market", "limit"],
                description: "订单类型",
              },
              quantity: { type: "number", description: "数量" },
              price: { type: "number", description: "限价 (limit单必填)" },
            },
            required: ["symbol", "side", "order_type", "quantity"],
          },
        },
        concurrency: {
          type: "number",
          description: "并发下单数 (create, 默认5)",
        },
        failure_policy: {
          type: "string",
          enum: ["continue", "cancel_remaining"],
          description: "单腿失败策略 (create, 默认continue)",
        },
        strategy_id: {
          type: "string",
          description: "关联策略ID (create, 可选)",
        },
        // get/cancel
        basket_id: { type: "string", description: "篮子ID (get/cancel)" },
        // list filter
        status: {
          type: "string",
          enum: [
            "pending",
            "executing",
            "partial",
            "completed",
            "failed",
            "cancelled",
            "partial_filled",
          ],
          description: "过滤状态 (list)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;

      switch (action) {
        case "create": {
          const legs = args.legs as
            | Array<{
                symbol: string;
                side: string;
                order_type: string;
                quantity: number;
                price?: number;
              }>
            | undefined;
          if (!legs || legs.length === 0) return "请提供订单腿列表 (legs)";

          const req: CreateBasketRequest = {
            name: args.name as string | undefined,
            legs: legs.map((l) => ({
              symbol: l.symbol,
              side: l.side as "buy" | "sell" | "short" | "cover",
              orderType: (l.order_type ?? "market") as "market" | "limit",
              quantity: l.quantity,
              price: l.price,
            })),
            concurrency: args.concurrency as number | undefined,
            failurePolicy: args.failure_policy as
              | "continue"
              | "cancel_remaining"
              | undefined,
            strategyId: args.strategy_id as string | undefined,
          };

          const basket = await basketService.createAndExecute(req);
          return (
            `✅ 篮子订单已创建并开始执行:\n` +
            `  ID: ${basket.basketId}\n` +
            `  名称: ${basket.name ?? "-"}\n` +
            `  腿数: ${basket.legs.length}\n` +
            `  并发: ${basket.concurrency}\n` +
            `  失败策略: ${basket.failurePolicy}\n` +
            `  状态: ${STATUS_LABEL[basket.status]}`
          );
        }

        case "list": {
          const status = args.status as BasketOrderStatus | undefined;
          const baskets = basketService.listBaskets(status);
          if (baskets.length === 0)
            return status
              ? `没有${STATUS_LABEL[status] ?? status}状态的篮子订单`
              : "暂无篮子订单";

          const lines = baskets.map((b) => formatBasket(b, false));
          return `篮子订单列表 (${baskets.length}):\n\n${lines.join("\n\n")}`;
        }

        case "get": {
          const id = args.basket_id as string;
          if (!id) return "请提供篮子ID (basket_id)";
          const basket = basketService.getBasket(id);
          if (!basket) return `篮子 ${id} 不存在`;
          return formatBasket(basket, true);
        }

        case "cancel": {
          const id = args.basket_id as string;
          if (!id) return "请提供篮子ID (basket_id)";
          const ok = await basketService.cancelBasket(id);
          return ok
            ? `✅ 篮子订单 ${id.slice(0, 8)} 已取消`
            : `❌ 取消失败: 篮子不存在或已处于终态`;
        }

        default:
          return `未知操作: ${action}。支持: create, list, get, cancel`;
      }
    },
  };
}
