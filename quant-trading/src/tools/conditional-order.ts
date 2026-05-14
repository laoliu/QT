/**
 * conditional_order tool — AI-callable interface for conditional stop/take-profit orders.
 *
 * Actions:
 *   add_stop_loss      — set a stop-loss price for a position
 *   add_take_profit    — set a take-profit target for a position
 *   add_trailing_stop  — set a trailing stop (percentage)
 *   add_trailing_atr   — set an ATR-based trailing stop (dynamic distance)
 *   add_staged_tp      — set staged (multi-tier) take-profit
 *   add_indicator_stop — set MA-based stop-loss (sell when price breaks below MA)
 *   list               — list all conditional orders
 *   remove             — remove a conditional order by ID
 *   check_now          — manually trigger a price check cycle
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { ConditionalOrderService } from "../services/conditional-order.js";

export function createConditionalOrderTool(
  _api: OpenClawPluginApi,
  condOrderService: ConditionalOrderService,
) {
  return {
    name: "conditional_order",
    description:
      "管理条件单（止损/止盈/跟踪止损/ATR跟踪止损/移动止盈/分批止盈/均线止损）。" +
      "action=add_stop_loss 设置止损, action=add_take_profit 设置止盈, " +
      "action=add_trailing_stop 设置跟踪止损(百分比), action=add_trailing_atr 设置ATR跟踪止损, " +
      "action=add_trailing_tp 设置移动止盈(涨到激活价后跟踪回撤卖出), " +
      "action=add_staged_tp 设置分批止盈(多档位), action=add_indicator_stop 设置均线止损(跌破MA卖出), " +
      "action=list 查看条件单, action=remove 删除条件单, action=update 修改已有条件单(如加持股保护), action=check_now 立即检查。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "add_stop_loss",
            "add_take_profit",
            "add_trailing_stop",
            "add_trailing_atr",
            "add_trailing_tp",
            "add_staged_tp",
            "add_indicator_stop",
            "list",
            "remove",
            "update",
            "batch_set_hold_days",
            "check_now",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码 (e.g. 600519)" },
        entry_price: { type: "number", description: "建仓价格 (必填添加时)" },
        stop_price: { type: "number", description: "止损触发价 (add_stop_loss)" },
        target_price: { type: "number", description: "止盈目标价 (add_take_profit)" },
        trail_pct: {
          type: "number",
          description: "跟踪止损回撤百分比 (0-100, e.g. 5 表示 5%), add_trailing_stop",
        },
        atr_value: {
          type: "number",
          description: "ATRֵ (add_trailing_atr, 可从 market_data indicators 获取 atr14)",
        },
        atr_multiplier: {
          type: "number",
          description: "ATR倍数 (add_trailing_atr, 默认1.0, 初始止损=入场价-ATR×倍数)",
        },
        ratchet_up_atr: {
          type: "number",
          description: "价格每上涨多少ATR触发止损上移 (默认0.8, 即价格上涨0.8×ATR时止损跟上)",
        },
        ratchet_move_atr: {
          type: "number",
          description: "每次止损上移多少ATR (默认0.5, 即止损上移0.5×ATR)",
        },
        tiers: {
          type: "array",
          items: {
            type: "object",
            properties: {
              gain_pct: { type: "number", description: "目标涨幅百分比 (如8表示+8%)" },
              sell_ratio: {
                type: "number",
                description: "卖出剩余仓位的比例 (0-100, 如50表示卖出剩余的50%)",
              },
            },
          },
          description:
            "分批止盈档位 (add_staged_tp), 如 [{gain_pct:8, sell_ratio:50}, {gain_pct:12, sell_ratio:100}]",
        },
        ma_period: {
          type: "number",
          description: "均线周期 (add_indicator_stop, 如 10/20/60, 默认20)",
        },
        ma_type: {
          type: "string",
          enum: ["sma", "ema"],
          description: "均线类型 (add_indicator_stop, 默认sma)",
        },
        ma_buffer_pct: {
          type: "number",
          description:
            "均线缓冲百分比 (add_indicator_stop, 0-100, 如2表示跌破MA×0.98才触发, 默认0)",
        },
        activation_pct: {
          type: "number",
          description: "移动止盈激活涨幅百分比 (add_trailing_tp, 0-100, 如10表示涨10%激活)",
        },
        callback_pct: {
          type: "number",
          description: "移动止盈回撤百分比 (add_trailing_tp, 0-100, 如3表示从峰值回撤3%卖出)",
        },
        quantity: { type: "number", description: "委托数量（股）" },
        strategy_id: { type: "string", description: "策略ID（可选）" },
        order_id: { type: "string", description: "条件单ID (remove 操作)" },
        active_only: { type: "boolean", description: "仅显示活跃条件单 (list 操作)" },
        min_hold_days: {
          type: "number",
          description:
            "最小持股保护天数 (交易日, 如2表示买入后2个交易日内不触发止损, 适用于add_stop_loss/add_trailing_stop/add_trailing_atr)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "add_stop_loss": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const stopPrice = args.stop_price as number;
          const quantity = args.quantity as number;
          if (!symbol || !entryPrice || !stopPrice || !quantity) {
            return { error: "请提供 symbol, entry_price, stop_price, quantity" };
          }
          if (stopPrice >= entryPrice) {
            return { error: "止损价应低于建仓价" };
          }
          const id = condOrderService.addStopLoss({
            symbol,
            entryPrice,
            stopPrice,
            quantity,
            strategyId: args.strategy_id as string | undefined,
            minHoldDays: args.min_hold_days as number | undefined,
          });
          const holdNote = args.min_hold_days ? `, 持股保护 ${args.min_hold_days as number} 个交易日` : "";
          return {
            message: `止损单已设置${holdNote}`,
            id,
            symbol,
            stopPrice: `¥${stopPrice.toFixed(2)}`,
            lossPct: `${(((entryPrice - stopPrice) / entryPrice) * 100).toFixed(1)}%`,
            ...(args.min_hold_days ? { minHoldDays: args.min_hold_days } : {}),
          };
        }

        case "add_take_profit": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const targetPrice = args.target_price as number;
          const quantity = args.quantity as number;
          if (!symbol || !entryPrice || !targetPrice || !quantity) {
            return { error: "请提供 symbol, entry_price, target_price, quantity" };
          }
          if (targetPrice <= entryPrice) {
            return { error: "止盈价应高于建仓价" };
          }
          const id = condOrderService.addTakeProfit({
            symbol,
            entryPrice,
            targetPrice,
            quantity,
            strategyId: args.strategy_id as string | undefined,
          });
          return {
            message: `止盈单已设置`,
            id,
            symbol,
            targetPrice: `¥${targetPrice.toFixed(2)}`,
            profitPct: `${(((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%`,
          };
        }

        case "add_trailing_stop": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const trailPctRaw = args.trail_pct as number;
          const quantity = args.quantity as number;
          if (!symbol || !entryPrice || !trailPctRaw || !quantity) {
            return { error: "请提供 symbol, entry_price, trail_pct, quantity" };
          }
          // Normalize: user provides 5 for 5%, we store 0.05
          const trailPct = trailPctRaw > 1 ? trailPctRaw / 100 : trailPctRaw;
          if (trailPct <= 0 || trailPct >= 1) {
            return { error: "trail_pct 应在 0-100 之间 (例如 5 表示 5%)" };
          }
          const id = condOrderService.addTrailingStop({
            symbol,
            entryPrice,
            trailPct,
            quantity,
            strategyId: args.strategy_id as string | undefined,
            minHoldDays: args.min_hold_days as number | undefined,
          });
          const holdNote = args.min_hold_days ? `, 持股保护 ${args.min_hold_days as number} 个交易日` : "";
          return {
            message: `跟踪止损单已设置${holdNote}`,
            id,
            symbol,
            trailPct: `${(trailPct * 100).toFixed(1)}%`,
            currentStopPrice: `¥${(entryPrice * (1 - trailPct)).toFixed(2)}`,
            ...(args.min_hold_days ? { minHoldDays: args.min_hold_days } : {}),
          };
        }

        case "add_trailing_atr": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const atrValue = args.atr_value as number;
          const quantity = args.quantity as number;
          if (!symbol || !entryPrice || !atrValue || !quantity) {
            return { error: "请提供 symbol, entry_price, atr_value, quantity" };
          }
          const atrMultiplier = (args.atr_multiplier as number | undefined) ?? 1.0;
          const ratchetUpAtr = (args.ratchet_up_atr as number | undefined) ?? 0.8;
          const ratchetMoveAtr = (args.ratchet_move_atr as number | undefined) ?? 0.5;
          const initialStop = entryPrice - atrMultiplier * atrValue;
          const id = condOrderService.addTrailingAtr({
            symbol,
            entryPrice,
            atrValue,
            atrMultiplier,
            ratchetUpAtr,
            ratchetMoveAtr,
            quantity,
            strategyId: args.strategy_id as string | undefined,
            minHoldDays: args.min_hold_days as number | undefined,
          });
          const holdNote = args.min_hold_days ? `, 持股保护 ${args.min_hold_days as number} 个交易日` : "";
          return {
            message: `ATR跟踪止损单已设置${holdNote}`,
            id,
            symbol,
            atr: atrValue.toFixed(2),
            atrMultiplier,
            initialStopPrice: `¥${initialStop.toFixed(2)}`,
            ratchetRule: `价格每上涨 ${ratchetUpAtr}×ATR(¥${(ratchetUpAtr * atrValue).toFixed(2)})，止损上移 ${ratchetMoveAtr}×ATR(¥${(ratchetMoveAtr * atrValue).toFixed(2)})`,
          };
        }

        case "add_trailing_tp": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const quantity = args.quantity as number;
          const activationPctRaw = args.activation_pct as number | undefined;
          const callbackPctRaw = args.callback_pct as number | undefined;
          if (!symbol || !entryPrice || !quantity) {
            return { error: "请提供 symbol, entry_price, quantity" };
          }
          if (!activationPctRaw || activationPctRaw <= 0) {
            return { error: "请提供 activation_pct (激活涨幅百分比, 如 10 表示涨10%后激活)" };
          }
          if (!callbackPctRaw || callbackPctRaw <= 0) {
            return { error: "请提供 callback_pct (回撤百分比, 如 3 表示从峰值回撤3%卖出)" };
          }
          const activationPct = activationPctRaw > 1 ? activationPctRaw / 100 : activationPctRaw;
          const callbackPct = callbackPctRaw > 1 ? callbackPctRaw / 100 : callbackPctRaw;
          const activationPrice = entryPrice * (1 + activationPct);
          const ordId = condOrderService.addTrailingTakeProfit({
            symbol,
            entryPrice,
            activationPct,
            callbackPct,
            quantity,
            strategyId: args.strategy_id as string | undefined,
          });
          return {
            message: `移动止盈单已设置`,
            id: ordId,
            symbol,
            activationPrice: `¥${activationPrice.toFixed(2)} (+${(activationPct * 100).toFixed(0)}%)`,
            callbackPct: `${(callbackPct * 100).toFixed(1)}%`,
            description: `涨到 ¥${activationPrice.toFixed(2)} 后激活, 回撤 ${(callbackPct * 100).toFixed(1)}% 自动卖出 ${String(quantity)} 股`,
          };
        }

        case "add_staged_tp": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const quantity = args.quantity as number;
          const rawTiers = args.tiers as { gain_pct: number; sell_ratio: number }[] | undefined;
          if (!symbol || !entryPrice || !quantity || !rawTiers || rawTiers.length === 0) {
            return {
              error:
                "请提供 symbol, entry_price, quantity, tiers. " +
                "tiers 示例: [{gain_pct:8, sell_ratio:50}, {gain_pct:12, sell_ratio:100}]",
            };
          }

          // Normalize: gain_pct 8 → 0.08, sell_ratio 50 → 0.5
          const tiers = rawTiers.map((t) => ({
            gainPct: t.gain_pct > 1 ? t.gain_pct / 100 : t.gain_pct,
            sellRatio: t.sell_ratio > 1 ? t.sell_ratio / 100 : t.sell_ratio,
          }));

          const id = condOrderService.addStagedTakeProfit({
            symbol,
            entryPrice,
            totalQuantity: quantity,
            tiers,
            strategyId: args.strategy_id as string | undefined,
          });

          const order = condOrderService.listOrders().find((o) => o.id === id);
          return {
            message: `分批止盈单已设置`,
            id,
            symbol,
            tiers:
              order?.tiers?.map((t) => ({
                target: `+${(t.gainPct * 100).toFixed(0)}%`,
                triggerPrice: `¥${t.triggerPrice.toFixed(2)}`,
                sellQuantity: t.quantity,
              })) ?? [],
          };
        }

        case "add_indicator_stop": {
          const symbol = args.symbol as string;
          const entryPrice = args.entry_price as number;
          const quantity = args.quantity as number;
          const maPeriod = (args.ma_period as number | undefined) ?? 20;
          const maType = (args.ma_type as "sma" | "ema" | undefined) ?? "sma";
          const maBufferRaw = (args.ma_buffer_pct as number | undefined) ?? 0;
          if (!symbol || !entryPrice || !quantity) {
            return { error: "请提供 symbol, entry_price, quantity" };
          }
          const maBufferPct = maBufferRaw > 1 ? maBufferRaw / 100 : maBufferRaw;
          const id = condOrderService.addIndicatorStop({
            symbol,
            entryPrice,
            quantity,
            maPeriod,
            maType,
            maBufferPct,
            strategyId: args.strategy_id as string | undefined,
          });
          return {
            message: `均线止损单已设置`,
            id,
            symbol,
            indicator: `${maType.toUpperCase()}${maPeriod}`,
            bufferPct: `${(maBufferPct * 100).toFixed(1)}%`,
            note: `当价格跌破 ${maType.toUpperCase()}${maPeriod}${maBufferPct > 0 ? ` ×${(1 - maBufferPct).toFixed(2)}` : ""} 时自动卖出 ${String(quantity)} 股`,
          };
        }

        case "list": {
          const activeOnly = (args.active_only as boolean) ?? false;
          const orders = condOrderService.listOrders(activeOnly);
          return {
            total: orders.length,
            active: condOrderService.activeCount,
            orders: orders.map((o) => {
              const base: Record<string, unknown> = {
                id: o.id,
                symbol: o.symbol,
                type: o.type,
                entryPrice: `¥${o.entryPrice.toFixed(2)}`,
                quantity: o.quantity,
                triggered: o.triggered,
                triggeredAt: o.triggeredAt,
                triggerResult: o.triggerResult,
                description: o.description,
                createdAt: o.createdAt,
              };

              switch (o.type) {
                case "stop_loss":
                case "take_profit":
                  base.triggerPrice = `¥${o.triggerValue.toFixed(2)}`;
                  break;
                case "trailing_stop":
                  base.trailPct = `${(o.triggerValue * 100).toFixed(1)}%`;
                  base.peakPrice = o.peakPrice ? `¥${o.peakPrice.toFixed(2)}` : undefined;
                  base.currentStopPrice = o.trailingStopPrice
                    ? `¥${o.trailingStopPrice.toFixed(2)}`
                    : undefined;
                  break;
                case "trailing_atr":
                  base.atr = o.atrValue?.toFixed(2);
                  base.atrMultiplier = o.atrMultiplier;
                  base.peakPrice = o.peakPrice ? `¥${o.peakPrice.toFixed(2)}` : undefined;
                  base.currentStopPrice = o.trailingStopPrice
                    ? `¥${o.trailingStopPrice.toFixed(2)}`
                    : undefined;
                  base.ratchetRule = `上涨${o.ratchetUpAtr ?? 0.8}×ATR→止损上移${o.ratchetMoveAtr ?? 0.5}×ATR`;
                  break;
                case "trailing_tp":
                  base.activationPrice = `¥${o.triggerValue.toFixed(2)}`;
                  base.activated = o.tpActivated ?? false;
                  base.callbackPct = o.callbackPct
                    ? `${(o.callbackPct * 100).toFixed(1)}%`
                    : undefined;
                  base.peakPrice = o.peakPrice ? `¥${o.peakPrice.toFixed(2)}` : undefined;
                  base.currentTpPrice = o.trailingTpPrice
                    ? `¥${o.trailingTpPrice.toFixed(2)}`
                    : undefined;
                  break;
                case "staged_tp":
                  base.tiers = o.tiers?.map((t) => ({
                    target: `+${(t.gainPct * 100).toFixed(0)}%`,
                    triggerPrice: `¥${t.triggerPrice.toFixed(2)}`,
                    sellQuantity: t.quantity,
                    triggered: t.triggered,
                  }));
                  break;
                case "indicator_stop":
                  base.maPeriod = o.maPeriod;
                  base.maType = o.maType ?? "sma";
                  base.maBufferPct = o.maBufferPct ? `${(o.maBufferPct * 100).toFixed(1)}%` : "0%";
                  break;
              }

              return base;
            }),
          };
        }

        case "remove": {
          const orderId = args.order_id as string;
          if (!orderId) return { error: "请提供 order_id" };
          const removed = condOrderService.removeOrder(orderId);
          return removed
            ? { message: `条件单 ${orderId} 已删除` }
            : { error: `未找到条件单 ${orderId}` };
        }

        case "update": {
          const orderId = args.order_id as string;
          if (!orderId) return { error: "请提供 order_id" };
          const updates: { minHoldDays?: number; triggerValue?: number; quantity?: number } = {};
          if (args.min_hold_days !== undefined) updates.minHoldDays = args.min_hold_days as number;
          if (args.stop_price !== undefined) updates.triggerValue = args.stop_price as number;
          if (args.target_price !== undefined) updates.triggerValue = args.target_price as number;
          if (args.quantity !== undefined) updates.quantity = args.quantity as number;
          if (Object.keys(updates).length === 0) {
            return { error: "请提供要修改的字段 (min_hold_days, stop_price, target_price, quantity)" };
          }
          const updated = condOrderService.updateOrder(orderId, updates);
          if (!updated) return { error: `未找到活跃条件单 ${orderId}` };
          return {
            message: `条件单 ${orderId} 已更新`,
            id: updated.id,
            symbol: updated.symbol,
            type: updated.type,
            ...(updated.minHoldDays ? { minHoldDays: updated.minHoldDays } : {}),
            triggerValue: updated.triggerValue,
          };
        }

        case "batch_set_hold_days": {
          const minHoldDays = args.min_hold_days as number;
          if (!minHoldDays || minHoldDays < 0) {
            return { error: "请提供 min_hold_days (交易日数, 如 2)" };
          }
          const filter: { types?: ("stop_loss" | "trailing_stop" | "trailing_atr" | "indicator_stop")[]; symbol?: string } = {};
          filter.types = ["stop_loss", "trailing_stop", "trailing_atr", "indicator_stop"];
          if (args.symbol) filter.symbol = args.symbol as string;
          const count = condOrderService.batchSetMinHoldDays(minHoldDays, filter);
          return {
            message: `已为 ${count} 个活跃止损类条件单设置持股保护 ${minHoldDays} 个交易日`,
            updated: count,
            minHoldDays,
            ...(args.symbol ? { symbol: args.symbol } : { scope: "全部止损类条件单" }),
          };
        }

        case "check_now": {
          const triggered = await condOrderService.checkAll();
          return {
            checked: condOrderService.activeCount,
            triggered: triggered.length,
            triggeredIds: triggered,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
