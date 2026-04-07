/**
 * intraday_t0 tool — AI-callable interface for A-share T+0 intraday round-trip tracking.
 *
 * A-share market enforces T+1 (cannot sell shares bought today). However, if a trader
 * already holds shares from a previous day, they can:
 *   1. Sell existing shares (from yesterday or earlier) today
 *   2. Buy the same stock today
 * This is effectively a T+0 round-trip on the existing position.
 *
 * This tool helps traders:
 *   - Check which positions are available for T+0 trading (has available shares from prior days)
 *   - Plan intraday sell-high-buy-low (正T) or buy-low-sell-high (反T) operations
 *   - Track T+0 round-trip P&L for the day
 *
 * Actions:
 *   available    — List positions eligible for T+0 (has available qty from prior days)
 *   plan_normal  — Plan a "正T" (sell first, buy back lower): sell existing → buy back at lower price
 *   plan_reverse — Plan a "反T" (buy first, sell existing): buy new → sell existing at higher price
 *   record       — Record a completed T+0 round-trip
 *   today        — Show today's T+0 round-trip records and P&L
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

interface T0Record {
  id: string;
  date: string;
  symbol: string;
  name?: string;
  type: "normal" | "reverse";
  sellPrice: number;
  buyPrice: number;
  quantity: number;
  pnl: number;
  pnlPct: number;
  commission: number;
  netPnl: number;
  notes?: string;
  createdAt: string;
}

function getT0Path(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "t0-records.json");
}

async function loadRecords(config: QuantTradingConfig): Promise<T0Record[]> {
  try {
    const raw = await fs.readFile(getT0Path(config), "utf-8");
    return JSON.parse(raw) as T0Record[];
  } catch {
    return [];
  }
}

async function saveRecords(records: T0Record[], config: QuantTradingConfig): Promise<void> {
  const filePath = getT0Path(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(records, null, 2));
}

let t0Counter = 0;

export function createIntradayT0Tool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
) {
  return {
    name: "intraday_t0",
    description:
      "日内T+0交易助手。A股T+1规则下，利用已有持仓进行日内高抛低吸。\n" +
      "• action=available — 查看可做T的持仓（有昨日以前的可卖数量）\n" +
      "• action=plan_normal — 计划正T操作（先卖后买，高卖低买回）\n" +
      "• action=plan_reverse — 计划反T操作（先买后卖，低买高卖出）\n" +
      "• action=record — 记录一笔T+0成交\n" +
      "• action=today — 查看今日T+0记录和盈亏",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["available", "plan_normal", "plan_reverse", "record", "today"],
          description: "操作类型",
        },
        symbol: { type: "string", description: "股票代码" },
        name: { type: "string", description: "股票名称（可选）" },
        quantity: { type: "number", description: "T+0操作数量（股）" },
        sell_price: { type: "number", description: "卖出价格" },
        buy_price: { type: "number", description: "买入价格" },
        current_price: {
          type: "number",
          description: "当前价格（plan操作时用于计算预期收益）",
        },
        target_price: {
          type: "number",
          description: "目标价格（plan操作时的预期买回/卖出价格）",
        },
        notes: { type: "string", description: "备注" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "available": {
          // Check live engine positions for available (T+0 eligible) shares
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const positions = engine.getPositions();
            const t0Eligible = positions
              .filter((p) => (p.availableQty ?? 0) > 0)
              .map((p) => ({
                symbol: p.symbol,
                name: p.name,
                totalQty: p.quantity,
                availableQty: p.availableQty ?? 0,
                frozenQty: p.frozenQty ?? 0,
                todayBuyQty: p.todayBuyQty ?? 0,
                avgCost: `¥${p.avgCost.toFixed(3)}`,
                currentPrice: p.currentPrice != null ? `¥${p.currentPrice.toFixed(3)}` : "N/A",
                unrealizedPnlPct:
                  p.unrealizedPnlPct != null
                    ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%`
                    : "N/A",
                maxT0Qty: p.availableQty ?? 0,
                note: "可卖数量即为可做T的数量",
              }));
            return {
              source: "live",
              total: t0Eligible.length,
              positions: t0Eligible,
              tip: "正T操作: 先卖出可卖股（高点），再买回同等数量（低点）。反T操作: 先买入新股（低点），待原有可卖股可以卖出时卖出（高点）。",
            };
          }

          // Paper mode: all positions are available (no T+1 tracking in paper portfolio)
          const portfolioPath = path.join(
            config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading"),
            "portfolio-paper.json",
          );
          try {
            const raw = await fs.readFile(portfolioPath, "utf-8");
            const portfolio = JSON.parse(raw);
            const positions = (portfolio.positions ?? []).filter(
              (p: { quantity: number }) => p.quantity > 0,
            );
            return {
              source: "paper",
              total: positions.length,
              positions: positions.map(
                (p: { symbol: string; name?: string; quantity: number; avgCost: number }) => ({
                  symbol: p.symbol,
                  name: p.name,
                  totalQty: p.quantity,
                  availableQty: p.quantity,
                  avgCost: `¥${p.avgCost.toFixed(2)}`,
                  maxT0Qty: p.quantity,
                }),
              ),
              tip: "模拟盘下所有持仓均可做T（无T+1限制）",
            };
          } catch {
            return { source: "paper", total: 0, positions: [], tip: "无持仓" };
          }
        }

        case "plan_normal": {
          // 正T: sell existing shares at current/high price, plan to buy back at target/lower price
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const currentPrice = args.current_price as number;
          const targetPrice = args.target_price as number;
          if (!symbol || !quantity || !currentPrice || !targetPrice) {
            return { error: "请提供 symbol, quantity, current_price (当前/卖出价), target_price (预期买回价)" };
          }
          if (targetPrice >= currentPrice) {
            return { error: "正T操作: target_price (买回价) 应低于 current_price (卖出价)" };
          }

          const spreadPct = ((currentPrice - targetPrice) / currentPrice) * 100;
          const grossProfit = (currentPrice - targetPrice) * quantity;
          // A-share commissions: buy 0.025% + sell 0.025% + stamp duty 0.1% (sell only)
          const buyCommission = targetPrice * quantity * 0.00025;
          const sellCommission = currentPrice * quantity * 0.00025;
          const stampDuty = currentPrice * quantity * 0.001;
          const totalCost = buyCommission + sellCommission + stampDuty;
          const netProfit = grossProfit - totalCost;

          return {
            type: "正T (先卖后买)",
            symbol,
            quantity,
            step1: `卖出 ${quantity} 股 @ ¥${currentPrice.toFixed(2)} (获得 ¥${(currentPrice * quantity).toFixed(2)})`,
            step2: `买回 ${quantity} 股 @ ¥${targetPrice.toFixed(2)} (花费 ¥${(targetPrice * quantity).toFixed(2)})`,
            spread: `¥${(currentPrice - targetPrice).toFixed(2)} (${spreadPct.toFixed(2)}%)`,
            grossProfit: `¥${grossProfit.toFixed(2)}`,
            commission: `¥${totalCost.toFixed(2)} (佣金 ¥${(buyCommission + sellCommission).toFixed(2)} + 印花税 ¥${stampDuty.toFixed(2)})`,
            netProfit: `¥${netProfit.toFixed(2)}`,
            breakEvenSpread: `¥${(totalCost / quantity).toFixed(3)} (${((totalCost / quantity / currentPrice) * 100).toFixed(3)}%)`,
            note: "正T适合判断股票短期见顶，先卖出锁定利润，等回落后买回",
          };
        }

        case "plan_reverse": {
          // 反T: buy new shares at current/low price, plan to sell existing at target/higher price
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const currentPrice = args.current_price as number;
          const targetPrice = args.target_price as number;
          if (!symbol || !quantity || !currentPrice || !targetPrice) {
            return { error: "请提供 symbol, quantity, current_price (当前/买入价), target_price (预期卖出价)" };
          }
          if (targetPrice <= currentPrice) {
            return { error: "反T操作: target_price (卖出价) 应高于 current_price (买入价)" };
          }

          const spreadPct = ((targetPrice - currentPrice) / currentPrice) * 100;
          const grossProfit = (targetPrice - currentPrice) * quantity;
          const buyCommission = currentPrice * quantity * 0.00025;
          const sellCommission = targetPrice * quantity * 0.00025;
          const stampDuty = targetPrice * quantity * 0.001;
          const totalCost = buyCommission + sellCommission + stampDuty;
          const netProfit = grossProfit - totalCost;

          return {
            type: "反T (先买后卖)",
            symbol,
            quantity,
            step1: `买入 ${quantity} 股 @ ¥${currentPrice.toFixed(2)} (花费 ¥${(currentPrice * quantity).toFixed(2)})`,
            step2: `卖出已有 ${quantity} 股 @ ¥${targetPrice.toFixed(2)} (获得 ¥${(targetPrice * quantity).toFixed(2)})`,
            spread: `¥${(targetPrice - currentPrice).toFixed(2)} (${spreadPct.toFixed(2)}%)`,
            grossProfit: `¥${grossProfit.toFixed(2)}`,
            commission: `¥${totalCost.toFixed(2)} (佣金 ¥${(buyCommission + sellCommission).toFixed(2)} + 印花税 ¥${stampDuty.toFixed(2)})`,
            netProfit: `¥${netProfit.toFixed(2)}`,
            breakEvenSpread: `¥${(totalCost / quantity).toFixed(3)} (${((totalCost / quantity / currentPrice) * 100).toFixed(3)}%)`,
            note: "反T适合判断股票短期见底，先买入摊低成本，等反弹后卖出原有持仓",
          };
        }

        case "record": {
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const sellPrice = args.sell_price as number;
          const buyPrice = args.buy_price as number;
          const notes = args.notes as string | undefined;
          const name = args.name as string | undefined;

          if (!symbol || !quantity || !sellPrice || !buyPrice) {
            return { error: "请提供 symbol, quantity, sell_price, buy_price" };
          }

          const type = sellPrice > buyPrice ? "normal" : "reverse";
          const grossPnl = (sellPrice - buyPrice) * quantity;
          const buyComm = buyPrice * quantity * 0.00025;
          const sellComm = sellPrice * quantity * 0.00025;
          const stampDuty = sellPrice * quantity * 0.001;
          const commission = buyComm + sellComm + stampDuty;
          const netPnl = grossPnl - commission;
          const pnlPct = ((sellPrice - buyPrice) / buyPrice) * 100;

          const record: T0Record = {
            id: `t0-${Date.now()}-${++t0Counter}`,
            date: new Date().toISOString().slice(0, 10),
            symbol,
            name,
            type,
            sellPrice,
            buyPrice,
            quantity,
            pnl: grossPnl,
            pnlPct,
            commission,
            netPnl,
            notes,
            createdAt: new Date().toISOString(),
          };

          const records = await loadRecords(config);
          records.push(record);
          await saveRecords(records, config);

          return {
            message: `T+0记录已保存`,
            id: record.id,
            type: type === "normal" ? "正T" : "反T",
            symbol,
            sellPrice: `¥${sellPrice.toFixed(2)}`,
            buyPrice: `¥${buyPrice.toFixed(2)}`,
            quantity,
            grossPnl: `¥${grossPnl.toFixed(2)}`,
            commission: `¥${commission.toFixed(2)}`,
            netPnl: `¥${netPnl.toFixed(2)}`,
            pnlPct: `${pnlPct.toFixed(2)}%`,
          };
        }

        case "today": {
          const records = await loadRecords(config);
          const today = new Date().toISOString().slice(0, 10);
          const todayRecords = records.filter((r) => r.date === today);

          if (todayRecords.length === 0) {
            return { date: today, total: 0, records: [], message: "今日无T+0记录" };
          }

          const totalNetPnl = todayRecords.reduce((s, r) => s + r.netPnl, 0);
          const totalGrossPnl = todayRecords.reduce((s, r) => s + r.pnl, 0);
          const totalCommission = todayRecords.reduce((s, r) => s + r.commission, 0);
          const winCount = todayRecords.filter((r) => r.netPnl > 0).length;

          return {
            date: today,
            total: todayRecords.length,
            winRate: `${((winCount / todayRecords.length) * 100).toFixed(0)}%`,
            totalGrossPnl: `¥${totalGrossPnl.toFixed(2)}`,
            totalCommission: `¥${totalCommission.toFixed(2)}`,
            totalNetPnl: `¥${totalNetPnl.toFixed(2)}`,
            records: todayRecords.map((r) => ({
              id: r.id,
              symbol: r.symbol,
              name: r.name,
              type: r.type === "normal" ? "正T" : "反T",
              sellPrice: `¥${r.sellPrice.toFixed(2)}`,
              buyPrice: `¥${r.buyPrice.toFixed(2)}`,
              quantity: r.quantity,
              netPnl: `¥${r.netPnl.toFixed(2)}`,
              notes: r.notes,
            })),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
