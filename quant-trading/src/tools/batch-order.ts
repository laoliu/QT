/**
 * batch_order tool — AI-callable interface for batch buy/sell operations.
 *
 * Use cases:
 *   - Build a portfolio by buying multiple stocks at once
 *   - Liquidate multiple positions in one go
 *   - Batch adjust positions (partial sells / adds)
 *
 * Actions:
 *   execute  — Submit a batch of buy/sell orders
 *   preview  — Dry-run: show what orders would be placed, validate cash, positions
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";

interface BatchOrderItem {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  name?: string;
}

function getPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio-paper.json");
}

function getLegacyPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio.json");
}

async function loadPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
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

export function createBatchOrderTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
) {
  return {
    name: "batch_order",
    description:
      "批量买卖工具。一次性提交多个买入/卖出委托，用于快速建仓、调仓或清仓。\n" +
      "• action=preview — 预览批量委托（校验资金/持仓，不实际执行）\n" +
      "• action=execute — 执行批量委托\n" +
      "orders 格式: [{symbol, side, quantity, price, name?}, ...]",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["preview", "execute"],
          description: "操作类型: preview=预览, execute=执行",
        },
        orders: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              side: { type: "string", enum: ["buy", "sell"], description: "买卖方向" },
              quantity: { type: "number", description: "数量（股）" },
              price: { type: "number", description: "价格" },
              name: { type: "string", description: "标的名称（可选）" },
            },
            required: ["symbol", "side", "quantity", "price"],
          },
          description: "批量委托列表",
        },
      },
      required: ["action", "orders"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const rawOrders = args.orders as BatchOrderItem[];

      if (!rawOrders || rawOrders.length === 0) {
        return { error: "请提供至少一个委托 (orders)" };
      }

      // Validate each order
      const errors: string[] = [];
      for (let i = 0; i < rawOrders.length; i++) {
        const o = rawOrders[i];
        if (!o.symbol) errors.push(`#${i + 1}: 缺少 symbol`);
        if (!o.side || !["buy", "sell"].includes(o.side)) errors.push(`#${i + 1}: side 必须是 buy 或 sell`);
        if (!o.quantity || o.quantity <= 0) errors.push(`#${i + 1}: quantity 必须大于 0`);
        if (!o.price || o.price <= 0) errors.push(`#${i + 1}: price 必须大于 0`);
      }
      if (errors.length > 0) {
        return { error: "委托校验失败", details: errors };
      }

      // Check if live engine is running — use live broker
      const engine = getEngine?.();
      if (engine && engine.getStatus() === "running" && engine.orderManager) {
        return await executeLiveBatch(engine, rawOrders, action === "preview");
      }

      // Paper mode
      return await executePaperBatch(config, rawOrders, action === "preview");
    },
  };
}

async function executeLiveBatch(
  engine: LiveEngine,
  orders: BatchOrderItem[],
  previewOnly: boolean,
): Promise<unknown> {
  const results: Array<{
    symbol: string;
    side: string;
    quantity: number;
    price: number;
    status: string;
    orderId?: string;
    error?: string;
  }> = [];

  // Preview: just validate
  if (previewOnly) {
    const account = engine.getAccount();
    const positions = engine.getPositions();
    let availableCash = account?.cash ?? 0;

    // Process sells first (free up cash), then buys
    const sells = orders.filter((o) => o.side === "sell");
    const buys = orders.filter((o) => o.side === "buy");

    for (const o of sells) {
      const pos = positions.find((p) => p.symbol === o.symbol);
      const available = pos?.availableQty ?? pos?.quantity ?? 0;
      if (available < o.quantity) {
        results.push({
          symbol: o.symbol, side: "sell", quantity: o.quantity, price: o.price,
          status: "error", error: `可卖数量不足: 可卖 ${available} 股`,
        });
      } else {
        const proceeds = o.quantity * o.price;
        availableCash += proceeds;
        results.push({
          symbol: o.symbol, side: "sell", quantity: o.quantity, price: o.price,
          status: "ok",
        });
      }
    }

    for (const o of buys) {
      const cost = o.quantity * o.price;
      if (cost > availableCash) {
        results.push({
          symbol: o.symbol, side: "buy", quantity: o.quantity, price: o.price,
          status: "error", error: `资金不足: 需要 ¥${cost.toFixed(2)}，可用 ¥${availableCash.toFixed(2)}`,
        });
      } else {
        availableCash -= cost;
        results.push({
          symbol: o.symbol, side: "buy", quantity: o.quantity, price: o.price,
          status: "ok",
        });
      }
    }

    const succeeded = results.filter((r) => r.status === "ok").length;
    const failed = results.filter((r) => r.status === "error").length;
    return {
      mode: "live",
      preview: true,
      total: orders.length,
      succeeded,
      failed,
      results,
    };
  }

  // Execute: submit orders via broker (sells first, then buys)
  const sells = orders.filter((o) => o.side === "sell");
  const buys = orders.filter((o) => o.side === "buy");

  for (const o of [...sells, ...buys]) {
    try {
      const result = await engine.orderManager!.submit({
        symbol: o.symbol,
        market: "cn_stock",
        side: o.side,
        orderType: "limit",
        quantity: o.quantity,
        price: o.price,
        strategyId: "batch_order",
      });
      if (result.ok) {
        results.push({
          symbol: o.symbol, side: o.side, quantity: o.quantity, price: o.price,
          status: "submitted", orderId: result.order.orderId,
        });
      } else {
        results.push({
          symbol: o.symbol, side: o.side, quantity: o.quantity, price: o.price,
          status: "error", error: result.reason,
        });
      }
    } catch (err) {
      results.push({
        symbol: o.symbol, side: o.side, quantity: o.quantity, price: o.price,
        status: "error", error: (err as Error).message,
      });
    }
  }

  const succeeded = results.filter((r) => r.status === "submitted").length;
  const failed = results.filter((r) => r.status === "error").length;
  return {
    mode: "live",
    preview: false,
    total: orders.length,
    succeeded,
    failed,
    results,
  };
}

async function executePaperBatch(
  config: QuantTradingConfig,
  orders: BatchOrderItem[],
  previewOnly: boolean,
): Promise<unknown> {
  const portfolio = await loadPortfolio(config);
  const results: Array<{
    symbol: string;
    side: string;
    quantity: number;
    price: number;
    status: string;
    error?: string;
  }> = [];

  // Process sells first, then buys
  const sells = orders.filter((o) => o.side === "sell");
  const buys = orders.filter((o) => o.side === "buy");

  let cash = portfolio.cash;

  for (const o of sells) {
    const pos = portfolio.positions.find((p) => p.symbol === o.symbol);
    if (!pos || pos.quantity < o.quantity) {
      results.push({
        symbol: o.symbol, side: "sell", quantity: o.quantity, price: o.price,
        status: "error", error: `持仓不足: ${o.symbol} 持有 ${pos?.quantity ?? 0} 股`,
      });
      continue;
    }
    if (!previewOnly) {
      const proceeds = o.quantity * o.price;
      cash += proceeds;
      pos.quantity -= o.quantity;
      if (pos.quantity === 0) {
        portfolio.positions = portfolio.positions.filter((p) => p.symbol !== o.symbol);
      }
    }
    results.push({
      symbol: o.symbol, side: "sell", quantity: o.quantity, price: o.price,
      status: "ok",
    });
  }

  for (const o of buys) {
    const cost = o.quantity * o.price;
    if (cost > cash) {
      results.push({
        symbol: o.symbol, side: "buy", quantity: o.quantity, price: o.price,
        status: "error", error: `资金不足: 需要 ¥${cost.toFixed(2)}，可用 ¥${cash.toFixed(2)}`,
      });
      continue;
    }
    if (!previewOnly) {
      cash -= cost;
      const existing = portfolio.positions.find((p) => p.symbol === o.symbol);
      if (existing) {
        const totalQty = existing.quantity + o.quantity;
        existing.avgCost =
          (existing.avgCost * existing.quantity + o.price * o.quantity) / totalQty;
        existing.quantity = totalQty;
      } else {
        portfolio.positions.push({
          symbol: o.symbol,
          name: o.name ?? o.symbol,
          market: "cn_stock",
          quantity: o.quantity,
          avgCost: o.price,
        });
      }
    } else {
      cash -= cost; // Deduct for preview validation of subsequent buys
    }
    results.push({
      symbol: o.symbol, side: "buy", quantity: o.quantity, price: o.price,
      status: "ok",
    });
  }

  if (!previewOnly) {
    portfolio.cash = cash;
    portfolio.totalValue =
      cash +
      portfolio.positions.reduce((s, p) => s + p.quantity * (p.currentPrice ?? p.avgCost), 0);
    await savePortfolio(portfolio, config);
  }

  const succeeded = results.filter((r) => r.status === "ok").length;
  const failed = results.filter((r) => r.status === "error").length;
  return {
    mode: "paper",
    preview: previewOnly,
    total: orders.length,
    succeeded,
    failed,
    cashAfter: previewOnly ? `¥${cash.toFixed(2)}` : `¥${portfolio.cash.toFixed(2)}`,
    results,
  };
}
