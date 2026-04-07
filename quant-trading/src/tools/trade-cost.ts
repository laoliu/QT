/**
 * trade_cost tool — AI-callable interface for trade cost & tax analytics.
 *
 * Actions:
 *   summary    — total trade cost breakdown (commission + stamp duty + transfer fee)
 *   by_stock   — trade cost grouped by symbol
 *   by_period  — trade cost grouped by day/week/month/year
 *   cost_ratio — cost-to-PnL ratio analysis
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { Fill, QuantTradingConfig, TradeRecord } from "../types.js";

// ── Fee defaults (A-share retail) ────────────────────────────────

interface FeeRates {
  commissionRate: number; // both sides (default 万3 = 0.0003)
  stampDutyRate: number; // sell side only (default 0.05% = 0.0005)
  transferFeeRate: number; // both sides, SSE (default 0.002% = 0.00002)
  minCommission: number; // ¥5 per leg
}

function getFeeRates(config: QuantTradingConfig): FeeRates {
  const tc = config.tradeCost;
  return {
    commissionRate: tc?.commissionRate ?? 0.0003,
    stampDutyRate: tc?.stampDutyRate ?? 0.0005,
    transferFeeRate: tc?.transferFeeRate ?? 0.00002,
    minCommission: tc?.minCommission ?? 5,
  };
}

// ── Data loading ─────────────────────────────────────────────────

function dataDir(config: QuantTradingConfig): string {
  return config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  try {
    return JSON.parse(
      await fs.readFile(path.join(dataDir(config), "trades.json"), "utf-8"),
    ) as TradeRecord[];
  } catch {
    return [];
  }
}

async function loadFills(config: QuantTradingConfig): Promise<Fill[]> {
  try {
    return JSON.parse(
      await fs.readFile(path.join(dataDir(config), "fills.json"), "utf-8"),
    ) as Fill[];
  } catch {
    return [];
  }
}

// ── Cost estimation ──────────────────────────────────────────────

interface TradeCostEntry {
  symbol: string;
  side: string;
  amount: number; // price * quantity
  commission: number;
  stampDuty: number;
  transferFee: number;
  totalCost: number;
  date: string; // YYYY-MM-DD
  grossPnl: number;
  netPnl: number;
}

/**
 * Build cost entries from TradeRecord + Fill data.
 *
 * If Fill records are available, commission & tax come directly from broker data.
 * Otherwise, costs are estimated from TradeRecord.commission and configured fee rates.
 */
function buildCostEntries(
  trades: TradeRecord[],
  fills: Fill[],
  rates: FeeRates,
): TradeCostEntry[] {
  const fillByOrder = new Map<string, Fill[]>();
  for (const f of fills) {
    const list = fillByOrder.get(f.orderId) ?? [];
    list.push(f);
    fillByOrder.set(f.orderId, list);
  }

  const entries: TradeCostEntry[] = [];

  for (const t of trades) {
    // entry leg
    const entryAmount = t.entryPrice * t.quantity;
    const exitAmount = (t.exitPrice ?? t.entryPrice) * t.quantity;

    const entryFills = fillByOrder.get(t.entryOrderId) ?? [];
    const exitFills = t.exitOrderId ? (fillByOrder.get(t.exitOrderId) ?? []) : [];

    let commission: number;
    let stampDuty: number;
    let transferFee: number;

    if (entryFills.length > 0 || exitFills.length > 0) {
      // Use actual broker-reported costs
      commission = [...entryFills, ...exitFills].reduce((s, f) => s + f.commission, 0);
      stampDuty = [...entryFills, ...exitFills].reduce((s, f) => s + (f.tax ?? 0), 0);
      transferFee = 0; // included in commission from broker
    } else if (t.commission > 0) {
      // TradeRecord has a total commission field — use it and estimate breakdown
      commission = t.commission;
      const isSell = t.side === "sell" || t.side === "cover";
      stampDuty = isSell ? exitAmount * rates.stampDutyRate : 0;
      transferFee = (entryAmount + exitAmount) * rates.transferFeeRate;
      // If the recorded commission already covers everything, don't double-count
      if (commission >= stampDuty + transferFee) {
        // commission field is the total; decompose it
        const pureCommission = commission - stampDuty - transferFee;
        commission = Math.max(pureCommission, 0);
      }
    } else {
      // Estimate all costs from rates
      const entryComm = Math.max(entryAmount * rates.commissionRate, rates.minCommission);
      const exitComm = Math.max(exitAmount * rates.commissionRate, rates.minCommission);
      commission = entryComm + exitComm;
      const isSell = t.side === "sell" || t.side === "cover";
      stampDuty = isSell ? exitAmount * rates.stampDutyRate : 0;
      transferFee = (entryAmount + exitAmount) * rates.transferFeeRate;
    }

    const totalCost = commission + stampDuty + transferFee;
    const date = (t.exitTime ?? t.entryTime).slice(0, 10);

    entries.push({
      symbol: t.symbol,
      side: t.side,
      amount: entryAmount + exitAmount,
      commission,
      stampDuty,
      transferFee,
      totalCost,
      date,
      grossPnl: t.grossPnl,
      netPnl: t.netPnl,
    });
  }

  return entries;
}

// ── Period helpers ────────────────────────────────────────────────

function getWeekKey(dateStr: string): string {
  const d = new Date(dateStr);
  const jan1 = new Date(d.getFullYear(), 0, 1);
  const week = Math.ceil(((d.getTime() - jan1.getTime()) / 86400000 + jan1.getDay() + 1) / 7);
  return `${d.getFullYear()}-W${String(week).padStart(2, "0")}`;
}

function getPeriodKey(dateStr: string, periodType: string): string {
  switch (periodType) {
    case "daily":
      return dateStr; // YYYY-MM-DD
    case "weekly":
      return getWeekKey(dateStr);
    case "monthly":
      return dateStr.slice(0, 7); // YYYY-MM
    case "yearly":
      return dateStr.slice(0, 4); // YYYY
    default:
      return dateStr.slice(0, 7);
  }
}

// ── Filter helper ────────────────────────────────────────────────

function filterByDateRange(
  entries: TradeCostEntry[],
  from?: string,
  to?: string,
): TradeCostEntry[] {
  let result = entries;
  if (from) result = result.filter((e) => e.date >= from);
  if (to) result = result.filter((e) => e.date <= to);
  return result;
}

// ── Formatting helpers ───────────────────────────────────────────

function yuan(n: number): string {
  return `¥${n.toFixed(2)}`;
}

function pct(n: number): string {
  return `${(n * 100).toFixed(2)}%`;
}

// ── Tool factory ─────────────────────────────────────────────────

export function createTradeCostTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "trade_cost",
    description:
      "交易成本与税务统计。汇总佣金、印花税、过户费等交易费用。" +
      "action=summary 总体汇总, action=by_stock 按个股汇总, " +
      "action=by_period 按日/周/月/年汇总, action=cost_ratio 成本占盈亏比分析。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["summary", "by_stock", "by_period", "cost_ratio"],
          description: "操作类型",
        },
        from: { type: "string", description: "起始日期 YYYY-MM-DD" },
        to: { type: "string", description: "截止日期 YYYY-MM-DD" },
        symbol: { type: "string", description: "标的代码 (by_stock 可选筛选)" },
        period_type: {
          type: "string",
          enum: ["daily", "weekly", "monthly", "yearly"],
          description: "时间粒度 (by_period 操作，默认 monthly)",
        },
        limit: { type: "number", description: "返回前N项 (默认 20)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const from = args.from as string | undefined;
      const to = args.to as string | undefined;
      const limit = (args.limit as number) ?? 20;
      const rates = getFeeRates(config);

      const [trades, fills] = await Promise.all([loadTrades(config), loadFills(config)]);
      if (trades.length === 0) return { error: "暂无交易记录" };

      const allEntries = buildCostEntries(trades, fills, rates);
      const entries = filterByDateRange(allEntries, from, to);
      if (entries.length === 0) return { error: "指定日期范围内无交易记录" };

      switch (action) {
        // ── summary ────────────────────────────────────────────────
        case "summary": {
          const totalCommission = entries.reduce((s, e) => s + e.commission, 0);
          const totalStampDuty = entries.reduce((s, e) => s + e.stampDuty, 0);
          const totalTransferFee = entries.reduce((s, e) => s + e.transferFee, 0);
          const totalCost = entries.reduce((s, e) => s + e.totalCost, 0);
          const totalAmount = entries.reduce((s, e) => s + e.amount, 0);
          const totalGrossPnl = entries.reduce((s, e) => s + e.grossPnl, 0);
          const totalNetPnl = entries.reduce((s, e) => s + e.netPnl, 0);

          return {
            dateRange: { from: entries[0].date, to: entries[entries.length - 1].date },
            totalTrades: entries.length,
            totalTurnover: yuan(totalAmount),
            costBreakdown: {
              commission: yuan(totalCommission),
              stampDuty: yuan(totalStampDuty),
              transferFee: yuan(totalTransferFee),
              totalCost: yuan(totalCost),
            },
            costPct: pct(totalAmount > 0 ? totalCost / totalAmount : 0),
            pnl: {
              grossPnl: yuan(totalGrossPnl),
              netPnl: yuan(totalNetPnl),
              costErosion: yuan(totalGrossPnl - totalNetPnl),
            },
            feeRatesUsed: {
              commissionRate: pct(rates.commissionRate),
              stampDutyRate: pct(rates.stampDutyRate),
              transferFeeRate: pct(rates.transferFeeRate),
              minCommission: yuan(rates.minCommission),
            },
          };
        }

        // ── by_stock ───────────────────────────────────────────────
        case "by_stock": {
          const symbolFilter = args.symbol as string | undefined;
          const filtered = symbolFilter
            ? entries.filter((e) => e.symbol === symbolFilter)
            : entries;
          if (filtered.length === 0) return { error: `未找到标的 ${symbolFilter} 的交易记录` };

          const bySymbol = new Map<
            string,
            { commission: number; stampDuty: number; transferFee: number; totalCost: number; amount: number; grossPnl: number; netPnl: number; trades: number }
          >();

          for (const e of filtered) {
            const prev = bySymbol.get(e.symbol) ?? {
              commission: 0, stampDuty: 0, transferFee: 0, totalCost: 0,
              amount: 0, grossPnl: 0, netPnl: 0, trades: 0,
            };
            prev.commission += e.commission;
            prev.stampDuty += e.stampDuty;
            prev.transferFee += e.transferFee;
            prev.totalCost += e.totalCost;
            prev.amount += e.amount;
            prev.grossPnl += e.grossPnl;
            prev.netPnl += e.netPnl;
            prev.trades += 1;
            bySymbol.set(e.symbol, prev);
          }

          const sorted = [...bySymbol.entries()]
            .sort((a, b) => b[1].totalCost - a[1].totalCost)
            .slice(0, limit);

          return {
            totalSymbols: bySymbol.size,
            stocks: sorted.map(([symbol, d]) => ({
              symbol,
              trades: d.trades,
              turnover: yuan(d.amount),
              commission: yuan(d.commission),
              stampDuty: yuan(d.stampDuty),
              transferFee: yuan(d.transferFee),
              totalCost: yuan(d.totalCost),
              costPct: pct(d.amount > 0 ? d.totalCost / d.amount : 0),
              grossPnl: yuan(d.grossPnl),
              netPnl: yuan(d.netPnl),
            })),
          };
        }

        // ── by_period ──────────────────────────────────────────────
        case "by_period": {
          const periodType = (args.period_type as string) ?? "monthly";
          const byPeriod = new Map<
            string,
            { commission: number; stampDuty: number; transferFee: number; totalCost: number; amount: number; grossPnl: number; netPnl: number; trades: number }
          >();

          for (const e of entries) {
            const key = getPeriodKey(e.date, periodType);
            const prev = byPeriod.get(key) ?? {
              commission: 0, stampDuty: 0, transferFee: 0, totalCost: 0,
              amount: 0, grossPnl: 0, netPnl: 0, trades: 0,
            };
            prev.commission += e.commission;
            prev.stampDuty += e.stampDuty;
            prev.transferFee += e.transferFee;
            prev.totalCost += e.totalCost;
            prev.amount += e.amount;
            prev.grossPnl += e.grossPnl;
            prev.netPnl += e.netPnl;
            prev.trades += 1;
            byPeriod.set(key, prev);
          }

          const sorted = [...byPeriod.entries()].sort((a, b) => a[0].localeCompare(b[0]));
          let cumCost = 0;

          return {
            periodType,
            periods: sorted.slice(-limit).map(([period, d]) => {
              cumCost += d.totalCost;
              return {
                period,
                trades: d.trades,
                turnover: yuan(d.amount),
                commission: yuan(d.commission),
                stampDuty: yuan(d.stampDuty),
                transferFee: yuan(d.transferFee),
                totalCost: yuan(d.totalCost),
                cumCost: yuan(cumCost),
                costPct: pct(d.amount > 0 ? d.totalCost / d.amount : 0),
              };
            }),
          };
        }

        // ── cost_ratio ─────────────────────────────────────────────
        case "cost_ratio": {
          const totalCost = entries.reduce((s, e) => s + e.totalCost, 0);
          const totalGrossPnl = entries.reduce((s, e) => s + e.grossPnl, 0);
          const totalNetPnl = entries.reduce((s, e) => s + e.netPnl, 0);
          const totalAmount = entries.reduce((s, e) => s + e.amount, 0);

          // Per-stock cost ratio
          const bySymbol = new Map<
            string,
            { totalCost: number; grossPnl: number; netPnl: number; amount: number; trades: number }
          >();
          for (const e of entries) {
            const prev = bySymbol.get(e.symbol) ?? {
              totalCost: 0, grossPnl: 0, netPnl: 0, amount: 0, trades: 0,
            };
            prev.totalCost += e.totalCost;
            prev.grossPnl += e.grossPnl;
            prev.netPnl += e.netPnl;
            prev.amount += e.amount;
            prev.trades += 1;
            bySymbol.set(e.symbol, prev);
          }

          // Sort by cost ratio descending (worst first)
          const ranked = [...bySymbol.entries()]
            .map(([symbol, d]) => ({
              symbol,
              trades: d.trades,
              totalCost: yuan(d.totalCost),
              grossPnl: yuan(d.grossPnl),
              netPnl: yuan(d.netPnl),
              costToGrossPnl:
                d.grossPnl !== 0
                  ? pct(Math.abs(d.totalCost / d.grossPnl))
                  : "N/A",
              costToTurnover: pct(d.amount > 0 ? d.totalCost / d.amount : 0),
            }))
            .sort((a, b) => {
              const aVal = a.costToGrossPnl === "N/A" ? Infinity : parseFloat(a.costToGrossPnl);
              const bVal = b.costToGrossPnl === "N/A" ? Infinity : parseFloat(b.costToGrossPnl);
              return bVal - aVal;
            })
            .slice(0, limit);

          return {
            overview: {
              totalTrades: entries.length,
              totalCost: yuan(totalCost),
              grossPnl: yuan(totalGrossPnl),
              netPnl: yuan(totalNetPnl),
              costToGrossPnl:
                totalGrossPnl !== 0
                  ? pct(Math.abs(totalCost / totalGrossPnl))
                  : "N/A",
              costToTurnover: pct(totalAmount > 0 ? totalCost / totalAmount : 0),
              costErosion: yuan(totalGrossPnl - totalNetPnl),
              costErosionPct:
                totalGrossPnl !== 0
                  ? pct(Math.abs((totalGrossPnl - totalNetPnl) / totalGrossPnl))
                  : "N/A",
            },
            byStock: ranked,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
