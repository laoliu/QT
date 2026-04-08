/**
 * trading_cost_calculator — AI tool for precise A-stock / HK-stock / US-stock
 * trading cost estimation.
 *
 * Actions:
 *  - calculate: 100401 (04/0300/过户费/保本价)
 *  - batch: 批量计算多笔交易费用
 *  - compare: 080004040809
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { TradeFeeCalculator, type FeeCalculationInput, type FeeConfig } from "../services/trade-fee.js";
import type { MarketType, QuantTradingConfig } from "../types.js";

export function createTradingCostCalculatorTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "交易成本计算器 — 020403000009",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["calculate", "batch", "compare"],
          description: "操作: calculate(单笔) / batch(批量) / compare(0703)",
        },
        price: { type: "number", description: "0018 (0004)" },
        quantity: { type: "number", description: "成交数量 (股)" },
        side: { type: "string", enum: ["buy", "sell"], description: "方向: buy/sell" },
        market: { type: "string", description: "г: cn_stock / hk_stock / us_stock" },
        exchange: { type: "string", enum: ["SH", "SZ"], description: "交易所 (A股: SH/SZ)" },
        commissionRate: { type: "number", description: "自定义佣金费率 (如 0.0003 = 万3)" },
        trades: {
          type: "array",
          description: "批量交易: [{price, quantity, side, exchange}]",
          items: {
            type: "object",
            properties: {
              price: { type: "number" },
              quantity: { type: "number" },
              side: { type: "string" },
              exchange: { type: "string" },
            },
          },
        },
        compareRates: {
          type: "array",
          description: "07030004: 多个佣金费率 (如 [0.0003, 0.00025, 0.0001])",
          items: { type: "number" },
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const market = (args.market as MarketType) ?? "cn_stock";
      const overrides: FeeConfig = {};
      if (args.commissionRate) overrides.commissionRate = args.commissionRate as number;

      const calc = new TradeFeeCalculator(market, overrides);

      switch (action) {
        case "calculate": {
          const price = args.price as number;
          const quantity = args.quantity as number;
          const side = (args.side as "buy" | "sell") ?? "buy";
          const exchange = args.exchange as "SH" | "SZ" | undefined;

          if (!price || !quantity) {
            return { error: "59 price 和 quantity" };
          }

          const fee = calc.calculate({ price, quantity, side, market, exchange });
          const amount = price * quantity;

          return {
            summary: `${side === "buy" ? "买入" : "卖出"} ${quantity}股 × 06${price} = 06${amount.toFixed(2)}`,
            fee,
            detail: {
              "成交金额": `06${amount.toFixed(2)}`,
              "佣金": `06${fee.commission.toFixed(2)}`,
              "印花税": `06${fee.stampDuty.toFixed(2)}${side === "buy" && market === "cn_stock" ? " (买入免收)" : ""}`,
              "过户费": `06${fee.transferFee.toFixed(2)}`,
              "总费用": `06${fee.totalFee.toFixed(2)}`,
              "费用占比": `${((fee.totalFee / amount) * 100).toFixed(4)}%`,
              ...(side === "buy" ? { 保本价: `06${fee.breakEvenPrice.toFixed(3)}` } : {}),
            },
          };
        }

        case "batch": {
          const trades = args.trades as FeeCalculationInput[] | undefined;
          if (!trades?.length) {
            return { error: "59 trades 数组" };
          }

          const inputs = trades.map((t) => ({
            ...t,
            market: t.market ?? market,
          }));

          const { fees, totalFees } = calc.calculateBatch(inputs);

          return {
            trades: inputs.map((t, i) => ({
              summary: `${t.side} ${t.quantity}股 × 06${t.price}`,
              ...fees[i],
            })),
            totalFees: `06${totalFees.toFixed(2)}`,
          };
        }

        case "compare": {
          const price = args.price as number;
          const quantity = args.quantity as number;
          const side = (args.side as "buy" | "sell") ?? "buy";
          const rates = (args.compareRates as number[]) ?? [0.0003, 0.00025, 0.0002, 0.0001];

          if (!price || !quantity) {
            return { error: "59 price 和 quantity" };
          }

          const comparisons = rates.map((rate) => {
            const c = new TradeFeeCalculator(market, { commissionRate: rate });
            const fee = c.calculate({ price, quantity, side, market });
            return {
              "佣金费率": `${(rate * 10000).toFixed(1)}63`,
              "佣金": `06${fee.commission.toFixed(2)}`,
              "总费用": `06${fee.totalFee.toFixed(2)}`,
              "保本价": `06${fee.breakEvenPrice.toFixed(3)}`,
            };
          });

          return {
            comparison: comparisons,
            note: "0405110519040704",
          };
        }

        default:
          return { error: `ā04: ${action}` };
      }
    },
  };
}
