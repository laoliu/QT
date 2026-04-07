/**
 * trade_cost_calculator tool — calculate transaction costs for A-share trades.
 *
 * A-share fee structure:
 *   - Commission: negotiated rate (default 万3 = 0.03%), min ¥5, both sides
 *   - Stamp duty: 0.05% (sell side only, post 2023-08-28 rate)
 *   - Transfer fee: 0.002% (both sides, SSE/SZSE)
 *   - Regulation fee: 0.002% (both sides)
 *
 * Use case: short-term traders need to know exact cost to calculate break-even
 * price and actual profit/loss.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

// ── A-share default fee rates ──────────────────────────────────────
interface FeeConfig {
  /** Commission rate per side (default 0.0003 = 万3) */
  commissionRate: number;
  /** Minimum commission per trade in CNY (default 5) */
  minCommission: number;
  /** Stamp duty rate — sell side only (default 0.0005 = 0.05%) */
  stampDutyRate: number;
  /** Transfer fee rate — both sides (default 0.00002 = 0.002%) */
  transferFeeRate: number;
  /** Regulation fee rate — both sides (default 0.00002 = 0.002%) */
  regulationFeeRate: number;
}

const DEFAULT_FEES: FeeConfig = {
  commissionRate: 0.0003,
  minCommission: 5,
  stampDutyRate: 0.0005,
  transferFeeRate: 0.00002,
  regulationFeeRate: 0.00002,
};

export function createTradeCostCalculatorTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  const userFees: FeeConfig = {
    ...DEFAULT_FEES,
    ...((config as Record<string, unknown>).tradingFees as Partial<FeeConfig> | undefined),
  };

  return {
    name: "trade_cost_calculator",
    description:
      "A股交易成本计算器 — 计算买卖手续费、印花税、过户费，以及保本价。\n" +
      "action=cost 计算单笔交易费用, " +
      "action=round_trip 计算一买一卖完整交易成本, " +
      "action=breakeven 计算保本卖出价, " +
      "action=compare 对比不同价格/数量的成本。\n" +
      "默认费率: 佣金万3(最低5元) + 印花税0.05%(卖出) + 过户费0.002%。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["cost", "round_trip", "breakeven", "compare"],
          description:
            "操作: cost=单笔费用, round_trip=买卖全程费用, breakeven=保本价, compare=费用对比",
        },
        price: {
          type: "number",
          description: "股票价格（元）",
        },
        quantity: {
          type: "number",
          description: "股数（A股最小100股/手）",
        },
        side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "方向（action=cost 时使用），默认 buy",
        },
        sell_price: {
          type: "number",
          description: "卖出价格（action=round_trip/breakeven 时使用）",
        },
        commission_rate: {
          type: "number",
          description: "自定义佣金费率（如0.0003=万3），不填使用默认",
        },
        prices: {
          type: "string",
          description: "多个价格，逗号分隔（action=compare 时使用）",
        },
        quantities: {
          type: "string",
          description: "多个数量，逗号分隔（action=compare 时使用）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const price = args.price as number;
      const quantity = args.quantity as number;
      const side = (args.side as string) ?? "buy";
      const sellPrice = args.sell_price as number;
      const customRate = args.commission_rate as number | undefined;

      const fees = customRate ? { ...userFees, commissionRate: customRate } : userFees;

      switch (action) {
        case "cost": {
          if (!price || !quantity) return { error: "请提供 price 和 quantity" };
          const result = calculateCost(price, quantity, side as "buy" | "sell", fees);
          return { action: "cost", side, price, quantity, ...result };
        }

        case "round_trip": {
          if (!price || !quantity) return { error: "请提供 price 和 quantity" };
          const buyPrice = price;
          const sp = sellPrice ?? price;
          const buyCost = calculateCost(buyPrice, quantity, "buy", fees);
          const sellCost = calculateCost(sp, quantity, "sell", fees);
          const totalCost = buyCost.totalFee + sellCost.totalFee;
          const investAmount = buyPrice * quantity;
          const sellAmount = sp * quantity;
          const grossPnl = sellAmount - investAmount;
          const netPnl = grossPnl - totalCost;
          const netPnlPct = (netPnl / investAmount) * 100;
          const costPct = (totalCost / investAmount) * 100;

          return {
            action: "round_trip",
            buyPrice,
            sellPrice: sp,
            quantity,
            buy: buyCost,
            sell: sellCost,
            totalCost: round2(totalCost),
            totalCostPct: round4(costPct),
            grossPnl: round2(grossPnl),
            netPnl: round2(netPnl),
            netPnlPct: round4(netPnlPct),
            investAmount: round2(investAmount),
          };
        }

        case "breakeven": {
          if (!price || !quantity) return { error: "请提供 price（买入价）和 quantity" };
          const breakeven = calculateBreakeven(price, quantity, fees);
          return {
            action: "breakeven",
            buyPrice: price,
            quantity,
            breakevenPrice: breakeven.breakevenPrice,
            breakevenPct: breakeven.breakevenPct,
            buyCost: breakeven.buyCost,
            feeRates: {
              commission: `万${fees.commissionRate * 10000}`,
              stampDuty: `${fees.stampDutyRate * 100}%`,
              transferFee: `${fees.transferFeeRate * 100}%`,
            },
            tip: `需涨 ${breakeven.breakevenPct}% 才能保本（含全部费用）`,
          };
        }

        case "compare": {
          const pricesStr = args.prices as string;
          const quantitiesStr = args.quantities as string;
          if (!pricesStr && !quantitiesStr)
            return { error: "请提供 prices 或 quantities（逗号分隔）" };

          const priceList = pricesStr
            ? pricesStr.split(",").map((p) => parseFloat(p.trim()))
            : [price];
          const qtyList = quantitiesStr
            ? quantitiesStr.split(",").map((q) => parseInt(q.trim(), 10))
            : [quantity || 100];

          const comparisons: Array<Record<string, unknown>> = [];
          for (const p of priceList) {
            for (const q of qtyList) {
              if (isNaN(p) || isNaN(q)) continue;
              const rt = calculateRoundTrip(p, p, q, fees);
              comparisons.push({
                price: p,
                quantity: q,
                investAmount: round2(p * q),
                totalCost: rt.totalCost,
                costPct: rt.costPct,
                breakevenPct: rt.breakevenPct,
              });
            }
          }

          return { action: "compare", comparisons };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

function calculateCost(
  price: number,
  quantity: number,
  side: "buy" | "sell",
  fees: FeeConfig,
): { commission: number; stampDuty: number; transferFee: number; regulationFee: number; totalFee: number; amount: number } {
  const amount = price * quantity;

  // Commission — both sides, with floor
  const commission = Math.max(amount * fees.commissionRate, fees.minCommission);

  // Stamp duty — sell side only
  const stampDuty = side === "sell" ? amount * fees.stampDutyRate : 0;

  // Transfer fee — both sides
  const transferFee = amount * fees.transferFeeRate;

  // Regulation fee — both sides
  const regulationFee = amount * fees.regulationFeeRate;

  const totalFee = commission + stampDuty + transferFee + regulationFee;

  return {
    amount: round2(amount),
    commission: round2(commission),
    stampDuty: round2(stampDuty),
    transferFee: round2(transferFee),
    regulationFee: round2(regulationFee),
    totalFee: round2(totalFee),
  };
}

function calculateBreakeven(
  buyPrice: number,
  quantity: number,
  fees: FeeConfig,
): { breakevenPrice: number; breakevenPct: number; buyCost: number } {
  const buyCost = calculateCost(buyPrice, quantity, "buy", fees);
  // Solve for sellPrice where: sellPrice * qty - sellCost(sellPrice, qty) = buyPrice * qty + buyCost
  // sellPrice * qty * (1 - stampDutyRate - transferFeeRate - regulationFeeRate) - commission = buyPrice * qty + buyCost.totalFee
  const totalBuy = buyPrice * quantity + buyCost.totalFee;
  const sellDeductionRate = fees.stampDutyRate + fees.transferFeeRate + fees.regulationFeeRate;
  // sellPrice * qty * (1 - sellDeductionRate) - max(sellPrice * qty * commissionRate, minCommission) = totalBuy
  // Approximate: assume commission > minCommission (true for most trades)
  const effectiveRate = 1 - sellDeductionRate - fees.commissionRate;
  let breakevenPrice = totalBuy / (quantity * effectiveRate);

  // Verify and adjust if commission falls below minimum
  const sellAmount = breakevenPrice * quantity;
  if (sellAmount * fees.commissionRate < fees.minCommission) {
    breakevenPrice = (totalBuy + fees.minCommission) / (quantity * (1 - sellDeductionRate));
  }

  const breakevenPct = ((breakevenPrice - buyPrice) / buyPrice) * 100;

  return {
    breakevenPrice: round4(breakevenPrice),
    breakevenPct: round4(breakevenPct),
    buyCost: buyCost.totalFee,
  };
}

function calculateRoundTrip(
  buyPrice: number,
  sellPrice: number,
  quantity: number,
  fees: FeeConfig,
): { totalCost: number; costPct: number; breakevenPct: number } {
  const buyCost = calculateCost(buyPrice, quantity, "buy", fees);
  const sellCost = calculateCost(sellPrice, quantity, "sell", fees);
  const totalCost = round2(buyCost.totalFee + sellCost.totalFee);
  const costPct = round4((totalCost / (buyPrice * quantity)) * 100);
  const breakeven = calculateBreakeven(buyPrice, quantity, fees);
  return { totalCost, costPct, breakevenPct: breakeven.breakevenPct };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}
