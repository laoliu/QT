/**
 * basket_order tool — AI-callable interface for batch/basket order execution.
 *
 * Actions:
 *   execute       — Execute a basket of orders with retry, timeout, and rollback
 *   status        — Query the status of a basket order
 *   list          — List recent basket orders
 *   cancel        — Cancel all pending/submitted legs in a basket
 *   rollback      — Cancel + reverse all filled legs in a basket
 *   resume        — Resume a partially-executed basket (re-runs failed/pending legs)
 *
 * Example: "帮我同时买入 600519 200股, 000001 300股, 510300 500股"
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BasketExecutionMode, BasketExecutionReport, BasketRollbackDetail, OrderSide, OrderType, QuantTradingConfig } from "../types.js";
import { BasketOrderService, type BasketLegInput } from "../services/basket-order.js";

export function createBasketOrderTool(
  api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "basket_order",
    description:
      "篮子交易工具。批量下单，支持单腿失败重试、超时处理、篮子级别撤单回滚、执行结果汇总。\n" +
      "action=execute 执行篮子交易, action=status 查询篮子状态, action=list 列出历史篮子\n" +
      "action=cancel 撤销篮子(仅取消未成交), action=rollback 撤销并回滚(取消+反向平仓), " +
      "action=resume 恢复执行(重新执行失败的腿)\n\n" +
      "legs 格式: [{symbol:'600519',side:'buy',quantity:200}, {symbol:'000001',side:'sell',quantity:100}]\n" +
      "可选参数: name(篮子名称), max_retries(最大重试次数,默认3), " +
      "leg_timeout_ms(单腿超时毫秒,默认30000), rollback_on_failure(失败时回滚,默认true), " +
      "execution_mode(执行模式: sequential顺序/parallel并行,默认sequential), " +
      "basket_timeout_ms(篮子整体超时毫秒,0=不限制,默认0)",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["execute", "status", "list", "cancel", "rollback", "resume"],
          description: "操作类型",
        },
        legs: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              side: {
                type: "string",
                enum: ["buy", "sell", "short", "cover", "buy_open", "sell_close", "buy_close", "sell_open"],
                description: "交易方向",
              },
              quantity: { type: "number", description: "数量" },
              order_type: { type: "string", enum: ["market", "limit"], description: "订单类型(默认market)" },
              price: { type: "number", description: "限价单价格" },
            },
          },
          description: "交易腿列表",
        },
        name: { type: "string", description: "篮子名称" },
        max_retries: { type: "number", description: "最大重试次数(默认3)" },
        leg_timeout_ms: { type: "number", description: "单腿超时毫秒(默认30000)" },
        rollback_on_failure: { type: "boolean", description: "失败时回滚(默认true)" },
        execution_mode: { type: "string", enum: ["sequential", "parallel"], description: "执行模式: sequential顺序执行(先卖后买), parallel并行执行(默认sequential)" },
        basket_timeout_ms: { type: "number", description: "篮子整体超时毫秒, 0=不限制(默认0)" },
        strategy_id: { type: "string", description: "关联策略ID" },
        basket_id: { type: "string", description: "篮子ID (用于status/cancel/rollback/resume)" },
        limit: { type: "number", description: "列表数量(默认20)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;

      const engine = getEngine();
      if (!engine) return "引擎未初始化，请先启动实盘引擎";
      const orderMgr = engine.orderManager;
      if (!orderMgr) return "交易引擎未就绪";

      const service = new BasketOrderService(
        _config,
        orderMgr,
        (msg) => api.logger.info(`[BasketOrder] ${msg}`),
      );

      switch (action) {
        case "execute": {
          const rawLegs = args.legs as Array<{
            symbol: string;
            side: string;
            quantity: number;
            order_type?: string;
            price?: number;
          }> | undefined;

          if (!rawLegs || rawLegs.length === 0) {
            return "请提供 legs (交易腿列表)，每笔包含 symbol、side、quantity";
          }

          const legs: BasketLegInput[] = rawLegs.map((l) => ({
            symbol: l.symbol,
            side: l.side as OrderSide,
            quantity: l.quantity,
            orderType: (l.order_type as OrderType) ?? "market",
            price: l.price,
          }));

          const report = await service.execute(legs, {
            name: args.name as string | undefined,
            maxRetries: args.max_retries as number | undefined,
            legTimeoutMs: args.leg_timeout_ms as number | undefined,
            rollbackOnFailure: args.rollback_on_failure as boolean | undefined,
            strategyId: args.strategy_id as string | undefined,
            executionMode: args.execution_mode as BasketExecutionMode | undefined,
            basketTimeoutMs: args.basket_timeout_ms as number | undefined,
          });

          return formatReport(report);
        }

        case "status": {
          const basketId = args.basket_id as string | undefined;
          if (!basketId) return "请提供 basket_id";

          const basket = await service.getBasket(basketId);
          if (!basket) return `篮子 ${basketId} 未找到`;

          const lines = [
            `篮子 ${basket.basketId.slice(0, 8)} — ${basket.name ?? "未命名"}`,
            `状态: ${statusLabel(basket.status)}`,
            `创建: ${basket.createdAt}`,
            "",
            "交易腿:",
          ];

          for (const leg of basket.legs) {
            const price = leg.avgFillPrice ? `@ ¥${leg.avgFillPrice.toFixed(2)}` : "";
            lines.push(
              `  ${legStatusIcon(leg.status)} ${leg.symbol} ${leg.side} ` +
                `${leg.filledQty}/${leg.quantity} ${price} ` +
                `${leg.errorMsg ? `(${leg.errorMsg})` : ""}`,
            );
          }

          if (basket.rollbackDetails && basket.rollbackDetails.length > 0) {
            lines.push("", "回滚明细:");
            for (const d of basket.rollbackDetails) {
              lines.push(`  ${d.success ? "✅" : "❌"} ${d.symbol} ${d.action}` +
                (d.reverseSide ? ` ${d.reverseSide} ${d.reverseQty}股` : "") +
                (d.reverseFilled ? " 已成交" : d.action === "reverse" && d.success ? " 待成交" : "") +
                (d.errorMsg ? ` — ${d.errorMsg}` : ""));
            }
          }

          return lines.join("\n");
        }

        case "list": {
          const limit = (args.limit as number) ?? 20;
          const baskets = await service.listBaskets(limit);

          if (baskets.length === 0) return "暂无篮子交易记录";

          const lines = ["近期篮子交易:\n"];
          for (const b of baskets.slice().reverse()) {
            const filled = b.legs.filter((l) => l.status === "filled" || l.status === "partial_filled").length;
            lines.push(
              `  ${statusIcon(b.status)} ${b.basketId.slice(0, 8)} ${b.name ?? ""} — ` +
                `${filled}/${b.legs.length} 笔成功 — ${b.createdAt.slice(0, 16)}`,
            );
          }
          return lines.join("\n");
        }

        case "cancel": {
          const basketId = args.basket_id as string | undefined;
          if (!basketId) return "请提供 basket_id";

          try {
            const report = await service.cancelBasket(basketId);
            return formatReport(report);
          } catch (err) {
            return `撤销失败: ${(err as Error).message}`;
          }
        }

        case "rollback": {
          const basketId = args.basket_id as string | undefined;
          if (!basketId) return "请提供 basket_id";

          try {
            const report = await service.cancelAndRollback(basketId);
            return formatReport(report);
          } catch (err) {
            return `回滚失败: ${(err as Error).message}`;
          }
        }

        case "resume": {
          const basketId = args.basket_id as string | undefined;
          if (!basketId) return "请提供 basket_id";

          try {
            const report = await service.resume(basketId);
            return formatReport(report);
          } catch (err) {
            return `恢复执行失败: ${(err as Error).message}`;
          }
        }

        default:
          return `未知操作: ${action}。支持: execute, status, list, cancel, rollback, resume`;
      }
    },
  };
}

// ── Formatting helpers ──────────────────────────────────────────

function formatReport(report: BasketExecutionReport): string {
  const lines = [
    `📦 篮子交易执行报告`,
    `━━━━━━━━━━━━━━━━━━━━━━━━━━`,
    `篮子ID: ${report.basketId.slice(0, 8)}${report.name ? ` (${report.name})` : ""}`,
    `状态: ${statusLabel(report.status)}`,
    `总笔数: ${report.totalLegs} | 成功: ${report.filledLegs} | 失败: ${report.failedLegs} | 撤销: ${report.cancelledLegs}`,
    `总成交金额: ¥${report.totalValue.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`,
    `耗时: ${report.durationMs}ms`,
    `时间: ${report.startTime.slice(0, 19)} → ${report.endTime.slice(0, 19)}`,
    "",
    "明细:",
  ];

  for (const leg of report.legs) {
    const price = leg.avgFillPrice ? `@ ¥${leg.avgFillPrice.toFixed(2)}` : "";
    const retry = leg.retryCount > 0 ? ` (重试${leg.retryCount}次)` : "";
    const error = leg.errorMsg ? ` — ${leg.errorMsg}` : "";
    lines.push(
      `  ${legStatusIcon(leg.status)} ${leg.symbol} ${leg.side} ` +
        `${leg.filledQty}/${leg.requestedQty} ${price}${retry}${error}`,
    );
  }

  // Rollback details
  if (report.rollbackDetails && report.rollbackDetails.length > 0) {
    lines.push("", "回滚明细:");
    for (const d of report.rollbackDetails) {
      const icon = d.success ? "✅" : "❌";
      const actionLabel = d.action === "cancel" ? "撤单" : "反向平仓";
      const detail = d.reverseSide ? ` ${d.reverseSide} ${d.reverseQty}股` : "";
      const fillStatus = d.reverseFilled
        ? " 已成交"
        : d.action === "reverse" && d.success
          ? " 待成交"
          : "";
      const pnl = d.rollbackPnl !== undefined
        ? ` 损益: ${d.rollbackPnl >= 0 ? "+" : ""}¥${d.rollbackPnl.toFixed(2)}`
        : "";
      const error = d.errorMsg ? ` — ${d.errorMsg}` : "";
      lines.push(`  ${icon} ${d.symbol} ${actionLabel}${detail}${fillStatus}${pnl}${error}`);
    }

    // Rollback total P&L
    if (report.rollbackPnl !== undefined) {
      lines.push(
        `  ──────────────────────`,
        `  回滚总损益: ${report.rollbackPnl >= 0 ? "+" : ""}¥${report.rollbackPnl.toFixed(2)}`,
      );
    }
  }

  return lines.join("\n");
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待执行",
    executing: "执行中",
    partial: "部分成功",
    completed: "全部完成",
    rolling_back: "回滚中",
    rolled_back: "已回滚",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function statusIcon(status: string): string {
  const icons: Record<string, string> = {
    completed: "✅",
    partial: "⚠️",
    failed: "❌",
    rolled_back: "↩️",
    executing: "⏳",
    pending: "⏸️",
    rolling_back: "↩️",
  };
  return icons[status] ?? "•";
}

function legStatusIcon(status: string): string {
  const icons: Record<string, string> = {
    filled: "✅",
    partial_filled: "⚠️",
    failed: "❌",
    cancelled: "🚫",
    timeout: "⏰",
    submitted: "⏳",
    pending: "⏸️",
    retrying: "🔄",
  };
  return icons[status] ?? "•";
}
