/**
 * portfolio_rebalance tool — AI-callable tool for executing portfolio rebalancing.
 *
 * Given a target allocation (symbol→weight), it computes the difference from
 * current positions and submits the required buy/sell orders to rebalance.
 *
 * Actions:
 *   plan    — Show what trades would be needed (dry run)
 *   execute — Actually submit the rebalancing orders
 *
 * Example: "帮我把组合调整为 600519:30%, 000001:20%, 510300:50%"
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

interface RebalanceTarget {
  symbol: string;
  /** Target weight (0-1, e.g. 0.3 = 30%). */
  weight: number;
}

interface RebalanceTrade {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  reason: string;
}

export function createPortfolioRebalanceTool(
  _api: QuantTradingRuntime,
  _config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "portfolio_rebalance",
    description:
      "组合再平衡工具。根据目标权重分配，自动计算并执行调仓交易。\n" +
      "action=plan 查看调仓计划(不下单), action=execute 执行调仓交易\n\n" +
      "targets 格式: [{symbol:'600519',weight:30},{symbol:'000001',weight:20},{symbol:'510300',weight:50}]\n" +
      "weight 可以是百分比(30)或小数(0.3), 总和应为100%。\n" +
      "cash_reserve_pct: 保留现金比例(默认5%)",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["plan", "execute"],
          description: "plan=查看调仓计划, execute=执行调仓",
        },
        targets: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              weight: { type: "number", description: "目标权重(百分比如30, 或小数如0.3)" },
            },
          },
          description: "目标配置: [{symbol, weight}, ...]",
        },
        cash_reserve_pct: {
          type: "number",
          description: "保留现金比例(0-100, 默认5 = 5%)",
        },
      },
      required: ["action", "targets"],
    },
    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;
      const rawTargets = args.targets as { symbol: string; weight: number }[] | undefined;
      const cashReservePctRaw = (args.cash_reserve_pct as number) ?? 5;
      const cashReservePct = cashReservePctRaw > 1 ? cashReservePctRaw / 100 : cashReservePctRaw;

      if (!rawTargets || rawTargets.length === 0) {
        return "请提供 targets (目标配置)";
      }

      // Normalize weights (handle both % and decimal)
      const targets: RebalanceTarget[] = rawTargets.map((t) => ({
        symbol: t.symbol,
        weight: t.weight > 1 ? t.weight / 100 : t.weight,
      }));

      // Validate total weight
      const totalWeight = targets.reduce((sum, t) => sum + t.weight, 0);
      if (Math.abs(totalWeight - 1.0) > 0.05) {
        return `目标权重总和为 ${(totalWeight * 100).toFixed(1)}%, 应接近100%。请调整后重试。`;
      }

      // Get engine and broker
      const engine = getEngine();
      if (!engine) return "引擎未初始化，请先启动实盘引擎";
      const mgr = engine.orderManager;
      if (!mgr) return "交易引擎未就绪";

      // Get current account and positions
      let totalAssets: number;
      let positions: { symbol: string; quantity: number; marketValue: number }[];
      try {
        const [account, pos] = await Promise.all([
          mgr.broker.queryAccount(),
          mgr.broker.queryPositions(),
        ]);
        totalAssets =
          account.totalAssets || account.cash + pos.reduce((s, p) => s + (p.marketValue ?? 0), 0);
        positions = pos.map((p) => ({
          symbol: p.symbol,
          quantity: p.quantity ?? 0,
          marketValue: p.marketValue ?? 0,
        }));
      } catch (err) {
        return `无法获取账户信息: ${(err as Error).message}`;
      }

      const investableAssets = totalAssets * (1 - cashReservePct);

      // Get current quotes for target symbols
      const { detectMarketFromSymbol, createDataProvider } =
        await import("../data/market-data-provider.js");
      const provider = createDataProvider(_config);

      const prices = new Map<string, number>();
      for (const t of targets) {
        try {
          const market = detectMarketFromSymbol(t.symbol);
          const quote = await provider.getQuote(t.symbol, market as never);
          if (quote?.price) prices.set(t.symbol, quote.price);
        } catch {
          // Skip symbols we can't price
        }
      }

      // Compute rebalancing trades
      const trades: RebalanceTrade[] = [];

      for (const target of targets) {
        const price = prices.get(target.symbol);
        if (!price || price <= 0) {
          trades.push({
            symbol: target.symbol,
            side: "buy",
            quantity: 0,
            reason: `无法获取 ${target.symbol} 的价格，跳过`,
          });
          continue;
        }

        const targetValue = investableAssets * target.weight;
        const currentPos = positions.find((p) => p.symbol === target.symbol);
        const currentValue = currentPos?.marketValue ?? 0;
        const diff = targetValue - currentValue;

        // Calculate quantity (round to 100 shares for A-shares)
        const rawQty = Math.abs(diff) / price;
        const quantity = Math.floor(rawQty / 100) * 100;

        if (quantity >= 100) {
          trades.push({
            symbol: target.symbol,
            side: diff > 0 ? "buy" : "sell",
            quantity,
            reason: `目标 ${(target.weight * 100).toFixed(0)}% (¥${targetValue.toFixed(0)}) vs 当前 ¥${currentValue.toFixed(0)}, ${diff > 0 ? "加仓" : "减仓"} ${quantity} 股 @ ¥${price.toFixed(2)}`,
          });
        }
      }

      // Check for positions not in targets (should sell)
      for (const pos of positions) {
        if (pos.quantity > 0 && !targets.some((t) => t.symbol === pos.symbol)) {
          trades.push({
            symbol: pos.symbol,
            side: "sell",
            quantity: pos.quantity,
            reason: `不在目标配置中, 清仓 ${pos.quantity} 股`,
          });
        }
      }

      if (trades.length === 0 || trades.every((t) => t.quantity === 0)) {
        return "当前持仓已接近目标配置，无需调仓";
      }

      // Plan mode — just show the plan
      if (action === "plan") {
        const lines = [
          `📊 调仓计划 (总资产 ¥${totalAssets.toLocaleString()}, 可投资 ¥${investableAssets.toLocaleString()}, 现金保留 ${(cashReservePct * 100).toFixed(0)}%):\n`,
        ];
        // Sell first, then buy
        const sells = trades.filter((t) => t.side === "sell" && t.quantity > 0);
        const buys = trades.filter((t) => t.side === "buy" && t.quantity > 0);
        const skipped = trades.filter((t) => t.quantity === 0);
        if (sells.length > 0) {
          lines.push("卖出:");
          for (const t of sells) lines.push(`  🔴 ${t.symbol} 卖出 ${t.quantity} 股 — ${t.reason}`);
        }
        if (buys.length > 0) {
          lines.push("买入:");
          for (const t of buys) lines.push(`  🟢 ${t.symbol} 买入 ${t.quantity} 股 — ${t.reason}`);
        }
        if (skipped.length > 0) {
          lines.push("跳过:");
          for (const t of skipped) lines.push(`  ⚪ ${t.symbol} — ${t.reason}`);
        }
        lines.push("\n使用 action=execute 执行调仓");
        return lines.join("\n");
      }

      // Execute mode — submit orders (sell first to free up cash)
      const results: string[] = [`📊 执行调仓 (总资产 ¥${totalAssets.toLocaleString()}):\n`];

      // Sell first
      const sellTrades = trades.filter((t) => t.side === "sell" && t.quantity > 0);
      for (const t of sellTrades) {
        const market = detectMarketFromSymbol(t.symbol);
        const res = await mgr.submit({
          symbol: t.symbol,
          market,
          side: "sell",
          orderType: "market",
          quantity: t.quantity,
          strategyId: "rebalance",
          signalId: `rebal_${Date.now()}_${t.symbol}`,
        });
        results.push(
          res.ok
            ? `✅ ${t.symbol} 卖出 ${t.quantity} 股 — 已提交`
            : `❌ ${t.symbol} 卖出失败: ${res.reason}`,
        );
      }

      // Then buy
      const buyTrades = trades.filter((t) => t.side === "buy" && t.quantity > 0);
      for (const t of buyTrades) {
        const market = detectMarketFromSymbol(t.symbol);
        const res = await mgr.submit({
          symbol: t.symbol,
          market,
          side: "buy",
          orderType: "market",
          quantity: t.quantity,
          strategyId: "rebalance",
          signalId: `rebal_${Date.now()}_${t.symbol}`,
        });
        results.push(
          res.ok
            ? `✅ ${t.symbol} 买入 ${t.quantity} 股 — 已提交`
            : `❌ ${t.symbol} 买入失败: ${res.reason}`,
        );
      }

      return results.join("\n");
    },
  };
}
