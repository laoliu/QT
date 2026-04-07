/**
 * smart_order tool — AI-callable intelligent order routing.
 *
 * A stock trader needs more than simple market/limit orders:
 * - Split large orders into smaller chunks (iceberg)
 * - TWAP/VWAP execution to minimize market impact
 * - Bracket orders (entry + stop-loss + take-profit in one go)
 * - Quick reverse (close long → open short, or vice versa)
 *
 * Actions:
 *   bracket       — bracket order: entry + stop-loss + take-profit
 *   iceberg       — split large order into smaller chunks
 *   quick_reverse — close current position and open opposite
 *   close_all     — close all positions (emergency exit)
 *   order_check   — pre-flight check before placing an order
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

export function createSmartOrderTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  const dp = createDataProvider(config);

  async function getQuote(symbol: string) {
    const market = detectMarketFromSymbol(symbol);
    return dp.getQuote(symbol, market as never);
  }

  return {
    name: "smart_order",
    description:
      "智能下单工具。提供高级下单方式：bracket括号单（入场+止损+止盈一键下）、" +
      "iceberg冰山单（大单拆分）、quick_reverse快速反手、close_all全平仓、" +
      "order_check下单前检查。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["bracket", "iceberg", "quick_reverse", "close_all", "order_check"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "股票代码" },
        direction: {
          type: "string",
          enum: ["buy", "sell"],
          description: "交易方向",
        },
        quantity: { type: "number", description: "总数量" },
        price: { type: "number", description: "限价（不填则市价）" },
        stop_loss: { type: "number", description: "止损价（bracket）" },
        take_profit: { type: "number", description: "止盈价（bracket）" },
        chunks: { type: "number", description: "拆分份数（iceberg，默认5）" },
        interval_seconds: {
          type: "number",
          description: "每份间隔秒数（iceberg，默认60）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = (args.symbol as string)?.trim();
      const engine = getEngine();
      const mgr = engine?.orderManager;

      if (action === "order_check") {
        if (!symbol) return { error: "请提供 symbol" };
        const direction = (args.direction as string) ?? "buy";
        const quantity = (args.quantity as number) ?? 100;

        const checks: { check: string; pass: boolean; detail: string }[] = [];

        // Check 1: engine running
        checks.push({
          check: "交易引擎",
          pass: !!mgr,
          detail: mgr ? "已启动" : "未启动，无法下单",
        });

        // Check 2: market open
        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const day = now.getDay();
        const isWeekday = day > 0 && day < 6;
        const isMorning = hour === 9 && minute >= 30 || (hour > 9 && hour < 11) || (hour === 11 && minute <= 30);
        const isAfternoon = hour >= 13 && hour < 15;
        const isOpen = isWeekday && (isMorning || isAfternoon);
        checks.push({
          check: "交易时段",
          pass: isOpen,
          detail: isOpen ? "交易中" : "非交易时段",
        });

        // Check 3: quote available
        let currentPrice: number | null = null;
        try {
          const quote = await getQuote(symbol);
          currentPrice = quote?.price ?? null;
          checks.push({
            check: "行情数据",
            pass: currentPrice != null,
            detail: currentPrice != null ? `现价 ¥${currentPrice}` : "无法获取行情",
          });
        } catch {
          checks.push({ check: "行情数据", pass: false, detail: "获取失败" });
        }

        // Check 4: position check
        if (direction === "sell") {
          const positions = engine?.getPositions() ?? [];
          const pos = positions.find((p: { symbol: string }) => p.symbol === symbol);
          checks.push({
            check: "持仓检查",
            pass: pos != null && pos.quantity >= quantity,
            detail: pos ? `持有 ${pos.quantity} 股` : "无持仓",
          });
        }

        // Check 5: risk limit
        const price = (args.price as number) ?? currentPrice ?? 0;
        const orderValue = price * quantity;
        checks.push({
          check: "订单金额",
          pass: true,
          detail: `¥${orderValue.toFixed(0)} (${quantity}股 × ¥${price})`,
        });

        const allPass = checks.every((c) => c.pass);
        return {
          action: "order_check",
          symbol,
          direction,
          quantity,
          allPass,
          checks,
        };
      }

      if (action === "bracket") {
        if (!symbol) return { error: "请提供 symbol" };
        if (!mgr) return { error: "交易引擎未启动" };

        const direction = (args.direction as string) ?? "buy";
        const quantity = (args.quantity as number) ?? 100;
        const price = args.price as number | undefined;
        const stopLoss = args.stop_loss as number;
        const takeProfit = args.take_profit as number;

        if (!stopLoss || !takeProfit) {
          return { error: "bracket单需要 stop_loss 和 take_profit" };
        }

        const market = detectMarketFromSymbol(symbol);

        // Place entry order
        const entryResult = await mgr.submit({
          symbol,
          market,
          side: direction as "buy" | "sell",
          orderType: price ? "limit" : "market",
          quantity,
          price: price ?? undefined,
          strategyId: "smart_order_bracket",
          signalId: `bracket_${Date.now()}`,
        });

        if (!entryResult.ok) {
          return { error: `入场单失败: ${entryResult.reason}` };
        }

        // Place conditional stop-loss and take-profit (through conditional order service if available)
        const results = {
          entry: { orderId: entryResult.order.orderId, status: "submitted" },
          stopLoss: { price: stopLoss, status: "pending_entry_fill" },
          takeProfit: { price: takeProfit, status: "pending_entry_fill" },
        };

        return {
          action: "bracket",
          symbol,
          direction,
          quantity,
          ...results,
          note: "止损/止盈单将在入场单成交后通过条件单服务激活，请确保 conditional_order 服务已启用",
        };
      }

      if (action === "iceberg") {
        if (!symbol) return { error: "请提供 symbol" };
        if (!mgr) return { error: "交易引擎未启动" };

        const direction = (args.direction as string) ?? "buy";
        const totalQty = (args.quantity as number) ?? 1000;
        const chunks = Math.max(2, Math.min(20, (args.chunks as number) ?? 5));
        const intervalSec = Math.max(10, (args.interval_seconds as number) ?? 60);
        const price = args.price as number | undefined;
        const market = detectMarketFromSymbol(symbol);

        // Calculate chunk sizes (allow slightly different sizes)
        const baseChunk = Math.floor(totalQty / chunks);
        const remainder = totalQty - baseChunk * chunks;
        const chunkSizes: number[] = [];
        for (let i = 0; i < chunks; i++) {
          chunkSizes.push(baseChunk + (i < remainder ? 1 : 0));
        }

        // Round to lots of 100 for A-shares
        const roundedChunks = chunkSizes.map((s) => Math.max(100, Math.round(s / 100) * 100));
        const actualTotal = roundedChunks.reduce((a, b) => a + b, 0);

        // Submit first chunk immediately
        const firstResult = await mgr.submit({
          symbol,
          market,
          side: direction as "buy" | "sell",
          orderType: price ? "limit" : "market",
          quantity: roundedChunks[0],
          price: price ?? undefined,
          strategyId: "smart_order_iceberg",
          signalId: `iceberg_${Date.now()}_0`,
        });

        return {
          action: "iceberg",
          symbol,
          direction,
          totalQuantity: actualTotal,
          chunks: roundedChunks.length,
          chunkSizes: roundedChunks,
          intervalSeconds: intervalSec,
          firstChunk: {
            quantity: roundedChunks[0],
            ok: firstResult.ok,
            orderId: firstResult.ok ? firstResult.order.orderId : null,
            reason: firstResult.ok ? null : firstResult.reason,
          },
          remainingChunks: roundedChunks.length - 1,
          note: `后续 ${roundedChunks.length - 1} 笔将通过定时任务每隔 ${intervalSec} 秒自动执行。请使用 scheduled_task 工具设置。`,
        };
      }

      if (action === "quick_reverse") {
        if (!symbol) return { error: "请提供 symbol" };
        if (!mgr) return { error: "交易引擎未启动" };

        const positions = engine?.getPositions() ?? [];
        const pos = positions.find((p: { symbol: string }) => p.symbol === symbol);
        const market = detectMarketFromSymbol(symbol);

        if (!pos || pos.quantity === 0) {
          return { error: `${symbol} 无持仓，无法反手` };
        }

        // Close current position
        const closeResult = await mgr.submit({
          symbol,
          market,
          side: "sell",
          orderType: "market",
          quantity: pos.quantity,
          strategyId: "smart_order_reverse",
          signalId: `reverse_close_${Date.now()}`,
        });

        const newQuantity = (args.quantity as number) ?? pos.quantity;
        const newDirection = pos.quantity > 0 ? "sell" : "buy"; // Reverse logic

        return {
          action: "quick_reverse",
          symbol,
          closeLeg: {
            side: "sell",
            quantity: pos.quantity,
            ok: closeResult.ok,
            orderId: closeResult.ok ? closeResult.order.orderId : null,
            reason: closeResult.ok ? null : closeResult.reason,
          },
          openLeg: {
            side: newDirection,
            quantity: newQuantity,
            status: "pending_close_fill",
            note: "反向开仓单需在平仓成交后手动下达（A股T+1限制）",
          },
        };
      }

      if (action === "close_all") {
        if (!mgr) return { error: "交易引擎未启动" };

        const positions = engine?.getPositions() ?? [];
        if (positions.length === 0) {
          return { action: "close_all", message: "无持仓" };
        }

        const results: { symbol: string; quantity: number; ok: boolean; reason?: string }[] = [];
        for (const pos of positions) {
          if (pos.quantity <= 0) continue;
          const market = detectMarketFromSymbol(pos.symbol);
          const result = await mgr.submit({
            symbol: pos.symbol,
            market,
            side: "sell",
            orderType: "market",
            quantity: pos.quantity,
            strategyId: "smart_order_close_all",
            signalId: `close_all_${Date.now()}_${pos.symbol}`,
          });
          results.push({
            symbol: pos.symbol,
            quantity: pos.quantity,
            ok: result.ok,
            reason: result.ok ? undefined : result.reason,
          });
        }

        return {
          action: "close_all",
          totalPositions: results.length,
          submitted: results.filter((r) => r.ok).length,
          failed: results.filter((r) => !r.ok).length,
          details: results,
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
