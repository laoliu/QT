/**
 * price_alert tool — AI-callable for condition-based price alerts (条件预警).
 *
 * Actions:
 *   add    — Add a new alert condition
 *   list   — List active alerts
 *   remove — Remove an alert by ID
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { PriceAlertService, AlertConditionType } from "../services/price-alert.js";
import type { QuantTradingConfig } from "../types.js";

export function createPriceAlertTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  alertService: PriceAlertService,
) {
  return {
    name: "price_alert",
    description:
      "条件预警工具。设置价格/涨跌幅/成交量条件预警，触发后自动推送通知。\n" +
      "• action=add — 新增预警条件\n" +
      "  支持类型: price_above(突破价格), price_below(跌破价格), " +
      "change_pct_above(涨幅超过), change_pct_below(跌幅超过), " +
      "volume_above(成交量超过), volume_spike(成交量异动)\n" +
      "  可选: 附带 order_action 自动下单（触发后自动提交买卖委托）\n" +
      "• action=list — 查看当前预警列表\n" +
      "• action=remove — 删除预警",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["add", "list", "remove"],
          description: "操作类型: add=新增预警, list=查看预警, remove=删除预警",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=add时必填），如 600519",
        },
        conditionType: {
          type: "string",
          enum: [
            "price_above",
            "price_below",
            "change_pct_above",
            "change_pct_below",
            "volume_above",
            "volume_spike",
          ],
          description:
            "预警条件类型（action=add时必填）：" +
            "price_above=突破某价格, price_below=跌破某价格, " +
            "change_pct_above=涨幅超过N%, change_pct_below=跌幅超过N%, " +
            "volume_above=成交量超过, volume_spike=成交量异动",
        },
        threshold: {
          type: "number",
          description:
            "阈值（action=add时必填）。价格类型=价格值，涨跌幅类型=百分比数值，成交量=股数",
        },
        alertId: {
          type: "string",
          description: "预警ID（action=remove时必填）",
        },
        repeating: {
          type: "boolean",
          description: "是否持续监控（默认false=触发一次后自动停用）",
        },
        description: {
          type: "string",
          description: "自定义预警描述",
        },
        order_side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "自动下单方向（可选，设置后预警触发自动下单）",
        },
        order_type: {
          type: "string",
          enum: ["market", "limit"],
          description: "自动下单类型: market=市价, limit=限价",
        },
        order_quantity: {
          type: "number",
          description: "自动下单数量（股）",
        },
        order_limit_price: {
          type: "number",
          description: "限价委托价格（order_type=limit时必填）",
        },
        order_strategy_id: {
          type: "string",
          description: "关联的策略ID（可选）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";

      switch (action) {
        case "add": {
          const symbol = args.symbol as string;
          const conditionType = args.conditionType as AlertConditionType;
          const threshold = args.threshold as number;
          const repeating = (args.repeating as boolean) ?? false;
          const description = args.description as string | undefined;

          // Build optional orderAction from order_* params
          const orderSide = args.order_side as "buy" | "sell" | undefined;
          const orderType = args.order_type as "market" | "limit" | undefined;
          const orderQuantity = args.order_quantity as number | undefined;
          const orderLimitPrice = args.order_limit_price as number | undefined;
          const orderStrategyId = args.order_strategy_id as string | undefined;

          let orderAction: import("../services/price-alert.js").AlertOrderAction | undefined;
          if (orderSide && orderQuantity) {
            orderAction = {
              side: orderSide,
              orderType: orderType ?? "market",
              quantity: orderQuantity,
              limitPrice: orderLimitPrice,
              strategyId: orderStrategyId,
            };
          }

          if (!symbol) return { error: "请提供股票代码 (symbol)" };
          if (!conditionType) return { error: "请提供预警条件类型 (conditionType)" };
          if (threshold == null || typeof threshold !== "number") {
            return { error: "请提供阈值 (threshold)" };
          }

          try {
            const id = alertService.addAlert({
              symbol,
              type: conditionType,
              threshold,
              description,
              repeating,
              orderAction,
            });

            return {
              action: "add",
              alertId: id,
              symbol,
              conditionType,
              threshold,
              repeating,
              hasOrderAction: !!orderAction,
              message: `✅ 预警已设置: ${alertService.listAlerts().find((a) => a.id === id)?.description ?? id}${orderAction ? " (触发后自动下单)" : ""}`,
              activeCount: alertService.activeCount,
            };
          } catch (err) {
            return { error: err instanceof Error ? err.message : String(err) };
          }
        }

        case "list": {
          const activeOnly = (args.activeOnly as boolean) ?? false;
          const alerts = alertService.listAlerts(activeOnly);

          return {
            action: "list",
            count: alerts.length,
            activeCount: alertService.activeCount,
            alerts: alerts.map((a) => ({
              id: a.id,
              symbol: a.symbol,
              type: a.type,
              threshold: a.threshold,
              description: a.description,
              triggered: a.triggered,
              repeating: a.repeating,
              createdAt: a.createdAt,
              lastTriggeredAt: a.lastTriggeredAt,
            })),
          };
        }

        case "remove": {
          const alertId = args.alertId as string;
          if (!alertId) return { error: "请提供预警ID (alertId)" };

          const removed = alertService.removeAlert(alertId);
          if (!removed) {
            return { error: `未找到预警 ${alertId}` };
          }

          return {
            action: "remove",
            alertId,
            message: `🗑️ 预警 ${alertId} 已删除`,
            activeCount: alertService.activeCount,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
