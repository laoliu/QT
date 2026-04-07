import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * portfolio tool — AI-callable for portfolio management.
 * Supports both paper portfolio (local JSON) and live broker positions.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { Portfolio, PortfolioPosition, QuantTradingConfig } from "../types.js";

interface BatchSellResult {
  symbol: string;
  quantity: number;
  success: boolean;
  message: string;
}

interface RebalanceTradeItem {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  reason: string;
}

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
) {
  return {
    name: "portfolio",
    description:
      "管理投资组合（模拟 & 实盘）。" +
      "action=show 查看持仓, action=buy 买入, action=sell 卖出, " +
      "action=add_cash 追加资金, action=summary 组合摘要, " +
      "action=live_sync 同步实盘持仓, action=live_orders 查看实盘订单, " +
      "action=live_trades 查看成交记录, action=daily_pnl 当日盈亏, " +
      "action=batch_sell 批量卖出(一键清仓或按条件卖出亏损持仓), " +
      "action=batch_rebalance 按目标权重一键调仓。",
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
            "live_sync",
            "live_orders",
            "live_trades",
            "cancel_order",
            "daily_pnl",
            "batch_sell",
            "batch_rebalance",
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
        sell_mode: {
          type: "string",
          enum: ["clear_all", "by_condition"],
          description: "批量卖出模式: clear_all=一键清仓, by_condition=按条件卖出(如亏损超过阈值)",
        },
        loss_threshold: {
          type: "number",
          description: "亏损阈值(百分比, 如 5 表示亏损>5%的持仓将被卖出, 仅 sell_mode=by_condition 时使用)",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "指定批量卖出的股票代码列表(可选, 不提供则作用于全部持仓)",
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
          description: "调仓目标配置: [{symbol, weight}, ...] (用于 batch_rebalance)",
        },
        cash_reserve_pct: {
          type: "number",
          description: "保留现金比例(0-100, 默认5 = 5%, 用于 batch_rebalance)",
        },
        dry_run: {
          type: "boolean",
          description: "是否仅预览不执行(默认 false)",
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

        case "batch_sell": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动，请先执行 start_engine" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "订单管理器未就绪" };

          const sellMode = (args.sell_mode as string) ?? "clear_all";
          const lossThreshold = args.loss_threshold as number | undefined;
          const symbolFilter = args.symbols as string[] | undefined;
          const dryRun = (args.dry_run as boolean) ?? false;

          if (sellMode === "by_condition" && (lossThreshold == null || lossThreshold <= 0)) {
            return { error: "sell_mode=by_condition 时需要提供 loss_threshold (正数, 如 5 表示亏损>5%)" };
          }

          // Get current positions from broker
          let positions: { symbol: string; name?: string; quantity: number; availableQty: number; unrealizedPnlPct?: number | null }[];
          try {
            const pos = await mgr.broker.queryPositions();
            positions = pos.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              availableQty: p.availableQty ?? p.quantity,
              unrealizedPnlPct: p.unrealizedPnlPct,
            }));
          } catch (err) {
            return { error: `无法获取持仓信息: ${(err as Error).message}` };
          }

          // Filter by symbols if specified
          if (symbolFilter && symbolFilter.length > 0) {
            positions = positions.filter((p) => symbolFilter.includes(p.symbol));
          }

          // Filter by condition
          let toSell: typeof positions;
          if (sellMode === "clear_all") {
            toSell = positions.filter((p) => p.availableQty > 0);
          } else {
            // by_condition: sell positions with loss exceeding threshold
            const threshold = -(lossThreshold! / 100); // e.g. 5 -> -0.05
            toSell = positions.filter(
              (p) =>
                p.availableQty > 0 &&
                p.unrealizedPnlPct != null &&
                p.unrealizedPnlPct < threshold,
            );
          }

          if (toSell.length === 0) {
            return {
              message: sellMode === "clear_all"
                ? "没有可卖出的持仓"
                : `没有满足条件（亏损>${lossThreshold}%）的持仓`,
              positionCount: positions.length,
            };
          }

          // Dry run — preview only
          if (dryRun) {
            return {
              mode: sellMode,
              dryRun: true,
              ...(sellMode === "by_condition" && { lossThreshold: `${lossThreshold}%` }),
              toSell: toSell.map((p) => ({
                symbol: p.symbol,
                name: p.name,
                quantity: p.availableQty,
                pnlPct: p.unrealizedPnlPct != null ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%` : "N/A",
              })),
              message: `预览: 将卖出 ${toSell.length} 只持仓, 使用 dry_run=false 执行`,
            };
          }

          // Execute: submit sell orders one by one through OrderManager (risk gate + confirmation)
          const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");
          const results: BatchSellResult[] = [];
          for (const p of toSell) {
            const market = detectMarketFromSymbol(p.symbol);
            const res = await mgr.submit({
              symbol: p.symbol,
              market,
              side: "sell",
              orderType: "market",
              quantity: p.availableQty,
              strategyId: "batch_sell",
              signalId: `batch_sell_${Date.now()}_${p.symbol}`,
            });
            results.push({
              symbol: p.symbol,
              quantity: p.availableQty,
              success: res.ok,
              message: res.ok
                ? `卖出 ${p.availableQty} 股 — 已提交 (订单号: ${res.order.orderId})`
                : `卖出失败: ${res.reason}`,
            });
          }

          const successCount = results.filter((r) => r.success).length;
          return {
            mode: sellMode,
            ...(sellMode === "by_condition" && { lossThreshold: `${lossThreshold}%` }),
            total: results.length,
            succeeded: successCount,
            failed: results.length - successCount,
            results,
          };
        }

        case "batch_rebalance": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动，请先执行 start_engine" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "订单管理器未就绪" };

          const rawTargets = args.targets as { symbol: string; weight: number }[] | undefined;
          const cashReservePctRaw = (args.cash_reserve_pct as number) ?? 5;
          const cashReservePct = cashReservePctRaw > 1 ? cashReservePctRaw / 100 : cashReservePctRaw;
          const dryRun = (args.dry_run as boolean) ?? false;

          if (!rawTargets || rawTargets.length === 0) {
            return { error: "请提供 targets (目标配置, 如 [{symbol:'600519',weight:30},{symbol:'000001',weight:20}])" };
          }

          // Normalize weights (handle both % and decimal)
          const targets = rawTargets.map((t) => ({
            symbol: t.symbol,
            weight: t.weight > 1 ? t.weight / 100 : t.weight,
          }));

          // Validate total weight
          const totalWeight = targets.reduce((sum, t) => sum + t.weight, 0);
          if (Math.abs(totalWeight - 1.0) > 0.05) {
            return {
              error: `目标权重总和为 ${(totalWeight * 100).toFixed(1)}%, 应接近100%。请调整后重试。`,
            };
          }

          // Get current account and positions
          let totalAssets: number;
          let currentPositions: { symbol: string; quantity: number; marketValue: number }[];
          try {
            const [account, pos] = await Promise.all([
              mgr.broker.queryAccount(),
              mgr.broker.queryPositions(),
            ]);
            totalAssets =
              account.totalAssets || account.cash + pos.reduce((s, p) => s + (p.marketValue ?? 0), 0);
            currentPositions = pos.map((p) => ({
              symbol: p.symbol,
              quantity: p.quantity ?? 0,
              marketValue: p.marketValue ?? 0,
            }));
          } catch (err) {
            return { error: `无法获取账户信息: ${(err as Error).message}` };
          }

          const investableAssets = totalAssets * (1 - cashReservePct);

          // Get current quotes for target symbols
          const { detectMarketFromSymbol, createDataProvider } =
            await import("../data/market-data-provider.js");
          const provider = createDataProvider(config);

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
          const trades: RebalanceTradeItem[] = [];

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
            const currentPos = currentPositions.find((p) => p.symbol === target.symbol);
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

          // Positions not in targets should be sold
          for (const pos of currentPositions) {
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
            return { message: "当前持仓已接近目标配置，无需调仓" };
          }

          const sells = trades.filter((t) => t.side === "sell" && t.quantity > 0);
          const buys = trades.filter((t) => t.side === "buy" && t.quantity > 0);
          const skipped = trades.filter((t) => t.quantity === 0);

          // Dry run — preview only
          if (dryRun) {
            return {
              dryRun: true,
              totalAssets: `¥${totalAssets.toLocaleString()}`,
              investableAssets: `¥${investableAssets.toLocaleString()}`,
              cashReservePct: `${(cashReservePct * 100).toFixed(0)}%`,
              sells: sells.map((t) => ({ symbol: t.symbol, quantity: t.quantity, reason: t.reason })),
              buys: buys.map((t) => ({ symbol: t.symbol, quantity: t.quantity, reason: t.reason })),
              skipped: skipped.map((t) => ({ symbol: t.symbol, reason: t.reason })),
              message: `预览: ${sells.length} 笔卖出, ${buys.length} 笔买入, 使用 dry_run=false 执行`,
            };
          }

          // Execute: sell first (to free cash), then buy — each order goes through risk gate + confirmation
          const orderResults: { symbol: string; side: string; quantity: number; success: boolean; message: string }[] = [];

          for (const t of sells) {
            const market = detectMarketFromSymbol(t.symbol);
            const res = await mgr.submit({
              symbol: t.symbol,
              market,
              side: "sell",
              orderType: "market",
              quantity: t.quantity,
              strategyId: "batch_rebalance",
              signalId: `rebal_${Date.now()}_${t.symbol}`,
            });
            orderResults.push({
              symbol: t.symbol,
              side: "sell",
              quantity: t.quantity,
              success: res.ok,
              message: res.ok
                ? `卖出 ${t.quantity} 股 — 已提交 (订单号: ${res.order.orderId})`
                : `卖出失败: ${res.reason}`,
            });
          }

          for (const t of buys) {
            const market = detectMarketFromSymbol(t.symbol);
            const res = await mgr.submit({
              symbol: t.symbol,
              market,
              side: "buy",
              orderType: "market",
              quantity: t.quantity,
              strategyId: "batch_rebalance",
              signalId: `rebal_${Date.now()}_${t.symbol}`,
            });
            orderResults.push({
              symbol: t.symbol,
              side: "buy",
              quantity: t.quantity,
              success: res.ok,
              message: res.ok
                ? `买入 ${t.quantity} 股 — 已提交 (订单号: ${res.order.orderId})`
                : `买入失败: ${res.reason}`,
            });
          }

          const successCount = orderResults.filter((r) => r.success).length;
          return {
            totalAssets: `¥${totalAssets.toLocaleString()}`,
            cashReservePct: `${(cashReservePct * 100).toFixed(0)}%`,
            total: orderResults.length,
            succeeded: successCount,
            failed: orderResults.length - successCount,
            results: orderResults,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
