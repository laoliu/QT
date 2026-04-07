/**
 * basket_order tool — AI-callable tool for basket (一篮子) order management.
 *
 * Splits a multi-symbol order into individual legs based on a weight strategy,
 * then optionally submits them via the OrderManager.
 *
 * Actions:
 *   plan    — Compute the split and show the plan (dry run)
 *   execute — Compute the split and submit all legs as orders
 *
 * Strategies:
 *   equal       — Equal weight (1/N)
 *   market_cap  — Market-cap weighted
 *   custom      — User-provided weights
 *   by_amount   — Split total cash by ratio
 *   by_quantity — Fixed share quantity per symbol
 *
 * Example:
 *   "帮我用10万元等权买入 600519, 000001, 510300"
 *   → action=plan, side=buy, symbols=[{symbol:"600519"},{symbol:"000001"},{symbol:"510300"}],
 *     strategy=equal, total_amount=100000
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type {
  BasketLeg,
  BasketOrderRequest,
  BasketWeightStrategy,
  QuantTradingConfig,
} from "../types.js";
import { splitBasketOrder } from "../services/basket-order-splitter.js";

export function createBasketOrderTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "basket_order",
    description:
      "篮子订单工具。将多只股票的买卖订单按权重策略拆分为单腿订单，支持等权、市值加权、自定义权重等策略。\n\n" +
      "action=plan 查看拆分计划(不下单), action=execute 执行篮子订单\n\n" +
      "策略 strategy: equal(等权), market_cap(市值加权), custom(自定义权重), by_amount(按资金), by_quantity(按股数)\n\n" +
      "symbols 格式: [{symbol:'600519',weight:30},{symbol:'000001',weight:20}]\n" +
      "  weight: 自定义权重(百分比如30, 或小数0.3), 仅 custom/by_amount 策略需要\n" +
      "  quantity: 固定股数(仅 by_quantity 策略时的每个标的覆盖)\n" +
      "  price: 限价(可选, 不填则使用市价)\n\n" +
      "total_amount: 投入总金额(元), total_quantity: 总股数(by_quantity策略), " +
      "cash_reserve_pct: 现金保留比例(0-100, 默认0)",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["plan", "execute"],
          description: "plan=查看拆分计划, execute=执行篮子订单",
        },
        side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "买卖方向 (默认buy)",
        },
        symbols: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              weight: { type: "number", description: "自定义权重(百分比或小数)" },
              quantity: { type: "number", description: "固定股数" },
              price: { type: "number", description: "限价" },
            },
          },
          description: "标的列表: [{symbol, weight?, quantity?, price?}, ...]",
        },
        strategy: {
          type: "string",
          enum: ["equal", "market_cap", "custom", "by_amount", "by_quantity"],
          description: "权重策略 (默认equal)",
        },
        total_amount: {
          type: "number",
          description: "投入总金额(元)",
        },
        total_quantity: {
          type: "number",
          description: "总股数(by_quantity策略)",
        },
        cash_reserve_pct: {
          type: "number",
          description: "现金保留比例(0-100, 默认0)",
        },
        order_type: {
          type: "string",
          enum: ["market", "limit"],
          description: "订单类型(默认market)",
        },
      },
      required: ["action", "symbols"],
    },
    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;
      const side = (args.side as string) ?? "buy";
      const rawSymbols = args.symbols as
        | Array<{ symbol: string; weight?: number; quantity?: number; price?: number }>
        | undefined;
      const strategy = (args.strategy as BasketWeightStrategy) ?? "equal";
      const totalAmount = args.total_amount as number | undefined;
      const totalQuantity = args.total_quantity as number | undefined;
      const cashReservePctRaw = (args.cash_reserve_pct as number) ?? 0;
      const cashReservePct = cashReservePctRaw > 1 ? cashReservePctRaw / 100 : cashReservePctRaw;
      const orderType = (args.order_type as "market" | "limit") ?? "market";

      if (!rawSymbols || rawSymbols.length === 0) {
        return "请提供 symbols (标的列表)";
      }

      // Validate: weight-based strategies need totalAmount
      if (
        strategy !== "by_quantity" &&
        (!totalAmount || totalAmount <= 0)
      ) {
        // Try to get from account
        const engine = getEngine();
        if (!engine) {
          return "请提供 total_amount (投入总金额), 或先启动实盘引擎以自动获取账户资金";
        }
        const mgr = engine.orderManager;
        if (!mgr) return "交易引擎未就绪";
        try {
          const account = await mgr.broker.queryAccount();
          const autoAmount = account.cash > 0 ? account.cash : account.totalAssets;
          if (autoAmount <= 0) {
            return "请提供 total_amount (投入总金额), 账户可用资金为零";
          }
          // Re-run with auto amount
          return this.execute({
            ...args,
            total_amount: autoAmount,
          });
        } catch {
          return "请提供 total_amount (投入总金额)";
        }
      }

      // Build basket request
      const basketReq: BasketOrderRequest = {
        side: side as "buy" | "sell",
        symbols: rawSymbols.map((s) => ({
          symbol: s.symbol,
          weight: s.weight,
          quantity: s.quantity,
          price: s.price,
        })),
        strategy,
        totalAmount: totalAmount ?? 0,
        totalQuantity,
        cashReservePct,
        orderType,
      };

      // Setup price lookup
      const { detectMarketFromSymbol, createDataProvider } = await import(
        "../data/market-data-provider.js"
      );
      const provider = createDataProvider(config);

      const getPrice = async (symbol: string): Promise<number | null> => {
        try {
          const market = detectMarketFromSymbol(symbol);
          const quote = await provider.getQuote(symbol, market as never);
          return quote?.price ?? null;
        } catch {
          return null;
        }
      };

      // Market cap lookup (best-effort via data provider)
      const getMarketCap = async (symbol: string): Promise<number | null> => {
        try {
          const market = detectMarketFromSymbol(symbol);
          const quote = await provider.getQuote(symbol, market as never);
          // Approximate: if the provider includes amount or volume info, use it.
          // Most providers don't return market cap directly, so fallback to null.
          if (quote && "marketCap" in quote) {
            return (quote as unknown as { marketCap: number }).marketCap;
          }
          return null;
        } catch {
          return null;
        }
      };

      // Split the basket
      const result = await splitBasketOrder(basketReq, { getPrice, getMarketCap });

      if (result.legs.length === 0) {
        const msg = ["篮子订单拆分结果: 无有效腿"];
        if (result.skipped.length > 0) {
          msg.push("跳过:");
          for (const s of result.skipped) msg.push(`  - ${s.symbol}: ${s.reason}`);
        }
        return msg.join("\n");
      }

      // Plan mode — show the split
      if (action === "plan") {
        return formatPlan(result.legs, result.skipped, result.investableAmount, strategy);
      }

      // Execute mode — submit orders
      const engine = getEngine();
      if (!engine) return "引擎未初始化，请先启动实盘引擎";
      const mgr = engine.orderManager;
      if (!mgr) return "交易引擎未就绪";

      const lines: string[] = [
        `📦 执行篮子订单 (${strategyLabel(strategy)}, 共 ${result.legs.length} 腿):\n`,
      ];

      // Sell legs first (free up cash), then buy legs
      const sells = result.legs.filter((l) => l.side === "sell");
      const buys = result.legs.filter((l) => l.side === "buy");
      const orderedLegs = [...sells, ...buys];

      for (const leg of orderedLegs) {
        const market = detectMarketFromSymbol(leg.symbol);
        const res = await mgr.submit({
          symbol: leg.symbol,
          market,
          side: leg.side,
          orderType,
          quantity: leg.quantity,
          price: orderType === "limit" ? leg.price : undefined,
          strategyId: basketReq.strategyId ?? `basket_${result.basketId.slice(0, 8)}`,
          signalId: `basket_${result.basketId.slice(0, 8)}_${leg.symbol}`,
        });
        const sideLabel = leg.side === "buy" ? "买入" : "卖出";
        lines.push(
          res.ok
            ? `✅ ${leg.symbol} ${sideLabel} ${leg.quantity}股 @ ¥${leg.price.toFixed(2)} ≈ ¥${leg.estimatedValue.toLocaleString()} (权重${(leg.weight * 100).toFixed(1)}%)`
            : `❌ ${leg.symbol} ${sideLabel}失败: ${res.reason}`,
        );
      }

      if (result.skipped.length > 0) {
        lines.push("\n跳过:");
        for (const s of result.skipped) lines.push(`  - ${s.symbol}: ${s.reason}`);
      }

      return lines.join("\n");
    },
  };
}

// ── Formatting helpers ──────────────────────────────────────────

function strategyLabel(s: BasketWeightStrategy): string {
  const labels: Record<BasketWeightStrategy, string> = {
    equal: "等权分配",
    market_cap: "市值加权",
    custom: "自定义权重",
    by_amount: "按资金分配",
    by_quantity: "按股数分配",
  };
  return labels[s] ?? s;
}

function formatPlan(
  legs: BasketLeg[],
  skipped: Array<{ symbol: string; reason: string }>,
  investable: number,
  strategy: BasketWeightStrategy,
): string {
  const totalAllocated = legs.reduce((s, l) => s + l.estimatedValue, 0);
  const lines: string[] = [
    `📦 篮子订单拆分计划 (${strategyLabel(strategy)})`,
    `可投资金额: ¥${investable.toLocaleString()}`,
    `实际分配: ¥${totalAllocated.toLocaleString()} (${investable > 0 ? ((totalAllocated / investable) * 100).toFixed(1) : "-"}%)`,
    "",
  ];

  // Group by side
  const sells = legs.filter((l) => l.side === "sell");
  const buys = legs.filter((l) => l.side === "buy");

  if (sells.length > 0) {
    lines.push("卖出:");
    for (const l of sells) {
      lines.push(`  🔴 ${l.symbol} ${l.quantity}股 @ ¥${l.price.toFixed(2)} ≈ ¥${l.estimatedValue.toLocaleString()} (权重${(l.weight * 100).toFixed(1)}%)`);
    }
  }
  if (buys.length > 0) {
    lines.push("买入:");
    for (const l of buys) {
      lines.push(`  🟢 ${l.symbol} ${l.quantity}股 @ ¥${l.price.toFixed(2)} ≈ ¥${l.estimatedValue.toLocaleString()} (权重${(l.weight * 100).toFixed(1)}%)`);
    }
  }

  if (skipped.length > 0) {
    lines.push("\n跳过:");
    for (const s of skipped) lines.push(`  ⚪ ${s.symbol}: ${s.reason}`);
  }

  lines.push("\n使用 action=execute 执行篮子订单");
  return lines.join("\n");
}
