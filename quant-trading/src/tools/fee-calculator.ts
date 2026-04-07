/**
 * fee_calculator tool — AI-callable for pre-trade transaction cost estimation.
 *
 * Actions:
 *   estimate   — Estimate fees for a planned trade
 *   round_trip — Calculate round-trip costs (buy + sell)
 *   summary    — Portfolio-level fee summary from trade history
 *
 * Uses A-share standard fee schedule:
 *   - Commission: configurable, default 万2.5 (0.025%) each way, min ¥5
 *   - Stamp duty: 万5 (0.05%) sell-side only (reduced from 0.1% since 2023/08)
 *   - Transfer fee: 万0.2 (0.002%) each way (沪市+深市统一)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

interface FeeSchedule {
  /** Commission rate (both buy and sell), default 0.00025 (万2.5) */
  commissionRate: number;
  /** Minimum commission per trade, default 5 */
  minCommission: number;
  /** Stamp duty rate (sell-side only), default 0.0005 (万5) */
  stampDutyRate: number;
  /** Transfer fee rate (both sides), default 0.00002 (万0.2) */
  transferFeeRate: number;
}

const DEFAULT_FEES: FeeSchedule = {
  commissionRate: 0.00025,
  minCommission: 5,
  stampDutyRate: 0.0005,
  transferFeeRate: 0.00002,
};

function calcFees(
  price: number,
  quantity: number,
  side: "buy" | "sell",
  fees: FeeSchedule = DEFAULT_FEES,
) {
  const amount = price * quantity;

  // Commission
  const rawCommission = amount * fees.commissionRate;
  const commission = Math.max(rawCommission, fees.minCommission);

  // Stamp duty (sell-side only)
  const stampDuty = side === "sell" ? amount * fees.stampDutyRate : 0;

  // Transfer fee
  const transferFee = amount * fees.transferFeeRate;

  const totalFee = commission + stampDuty + transferFee;

  return {
    amount: Math.round(amount * 100) / 100,
    commission: Math.round(commission * 100) / 100,
    stampDuty: Math.round(stampDuty * 100) / 100,
    transferFee: Math.round(transferFee * 100) / 100,
    totalFee: Math.round(totalFee * 100) / 100,
    feePct: Math.round((totalFee / amount) * 10000) / 100,
  };
}

export function createFeeCalculatorTool(_api: OpenClawPluginApi, _config: QuantTradingConfig) {
  return {
    name: "fee_calculator",
    description:
      "交易费用计算器。计算A股买卖手续费（佣金+印花税+过户费）。\n" +
      "• action=estimate — 单笔交易费用估算\n" +
      "• action=round_trip — 买入+卖出往返费用（含盈亏保本价计算）\n" +
      "• action=summary — 费率说明和自定义费率设置",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["estimate", "round_trip", "summary"],
          description: "操作类型: estimate=单笔费用, round_trip=往返费用, summary=费率说明",
        },
        price: {
          type: "number",
          description: "股价（元）",
        },
        quantity: {
          type: "number",
          description: "股数",
        },
        side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "交易方向: buy=买入, sell=卖出（仅action=estimate需要）",
        },
        sell_price: {
          type: "number",
          description: "卖出价格（仅action=round_trip时使用，计算盈亏）",
        },
        commission_rate: {
          type: "number",
          description: "自定义佣金费率（万分之X），如 2.5 表示万2.5。默认万2.5",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "estimate";
      const customRate = args.commission_rate as number | undefined;

      const fees: FeeSchedule = {
        ...DEFAULT_FEES,
        ...(customRate ? { commissionRate: customRate / 10000 } : {}),
      };

      switch (action) {
        case "estimate": {
          const price = args.price as number;
          const quantity = args.quantity as number;
          const side = (args.side as "buy" | "sell") ?? "buy";

          if (!price || !quantity) {
            return { error: "请提供 price（股价）和 quantity（股数）参数" };
          }

          const result = calcFees(price, quantity, side, fees);

          return {
            side: side === "buy" ? "买入" : "卖出",
            price,
            quantity,
            ...result,
            breakdown: {
              佣金: `${result.commission}元 (费率${fees.commissionRate * 10000}万分之, 最低${fees.minCommission}元)`,
              印花税:
                side === "sell"
                  ? `${result.stampDuty}元 (卖出收取, 费率万${fees.stampDutyRate * 10000})`
                  : "0元 (买入不收)",
              过户费: `${result.transferFee}元 (费率万${fees.transferFeeRate * 10000})`,
            },
          };
        }

        case "round_trip": {
          const buyPrice = args.price as number;
          const quantity = args.quantity as number;
          const sellPrice = args.sell_price as number | undefined;

          if (!buyPrice || !quantity) {
            return { error: "请提供 price（买入价）和 quantity（股数）参数" };
          }

          const buyFees = calcFees(buyPrice, quantity, "buy", fees);
          const sellFees = sellPrice
            ? calcFees(sellPrice, quantity, "sell", fees)
            : null;

          const totalCost = buyFees.totalFee + (sellFees?.totalFee ?? 0);

          // Calculate break-even sell price
          const buyTotalCost = buyPrice * quantity + buyFees.totalFee;
          // Solve: sellPrice * quantity - sellFees(sellPrice) = buyTotalCost
          // sellPrice * qty * (1 - commRate - stampRate - transferRate) - minCommAdjust = buyTotalCost
          const sellFeeRate = fees.commissionRate + fees.stampDutyRate + fees.transferFeeRate;
          const breakEvenPrice = Math.ceil(
            (buyTotalCost + fees.minCommission) / (quantity * (1 - sellFeeRate)) * 100,
          ) / 100;

          const result: Record<string, unknown> = {
            buyPrice,
            quantity,
            buyAmount: buyFees.amount,
            buyFee: buyFees.totalFee,
            totalRoundTripCost: Math.round(totalCost * 100) / 100,
            breakEvenPrice,
            breakEvenChangePct: Math.round(((breakEvenPrice / buyPrice - 1) * 100) * 100) / 100,
          };

          if (sellFees && sellPrice) {
            const grossPnl = (sellPrice - buyPrice) * quantity;
            const netPnl = grossPnl - totalCost;

            result.sellPrice = sellPrice;
            result.sellAmount = sellFees.amount;
            result.sellFee = sellFees.totalFee;
            result.grossPnl = Math.round(grossPnl * 100) / 100;
            result.netPnl = Math.round(netPnl * 100) / 100;
            result.netPnlPct =
              Math.round((netPnl / (buyPrice * quantity)) * 10000) / 100;
            result.feeImpact = `手续费占毛利润比例: ${grossPnl > 0 ? Math.round((totalCost / grossPnl) * 10000) / 100 : "N/A"}%`;
          }

          return result;
        }

        case "summary": {
          return {
            title: "A股交易费用说明（2023年8月后标准）",
            fees: {
              佣金: `${fees.commissionRate * 10000}万分之，买卖双向收取，最低${fees.minCommission}元/笔`,
              印花税: `${fees.stampDutyRate * 10000}万分之（${fees.stampDutyRate * 100}%），仅卖出时收取`,
              过户费: `${fees.transferFeeRate * 10000}万分之，买卖双向收取，沪深统一`,
            },
            examples: {
              "10万元买入": calcFees(10, 10000, "buy", fees),
              "10万元卖出": calcFees(10, 10000, "sell", fees),
            },
            note: "佣金费率因券商而异，一般可协商至万1.5-万3。可通过 commission_rate 参数自定义。",
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
