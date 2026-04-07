/**
 * transaction_cost tool — AI-callable for transaction cost analysis.
 *
 * Actions:
 *   estimate  — Estimate costs for a planned trade (commission, stamp tax, transfer fee)
 *   history   — Analyze historical transaction costs from trade records
 *   optimize  — Suggest ways to reduce transaction costs
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

/** A-share fee schedule (standard defaults, configurable) */
interface FeeSchedule {
  /** Commission rate (both buy and sell), default 0.025% (万2.5) */
  commissionRate: number;
  /** Minimum commission per trade (CNY) */
  minCommission: number;
  /** Stamp tax rate (sell only), 0.05% since 2023-08-28 */
  stampTaxRate: number;
  /** Transfer fee rate (both sides), 0.001% */
  transferFeeRate: number;
}

const DEFAULT_FEES: FeeSchedule = {
  commissionRate: 0.00025,
  minCommission: 5,
  stampTaxRate: 0.0005,
  transferFeeRate: 0.00001,
};

export function createTransactionCostTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine,
) {
  const tradingAccountAny = config.tradingAccount as Record<string, unknown> | undefined;
  const fees: FeeSchedule = {
    commissionRate:
      (typeof tradingAccountAny?.commissionRate === "number"
        ? tradingAccountAny.commissionRate
        : null) ?? DEFAULT_FEES.commissionRate,
    minCommission:
      (typeof tradingAccountAny?.minCommission === "number"
        ? tradingAccountAny.minCommission
        : null) ?? DEFAULT_FEES.minCommission,
    stampTaxRate: DEFAULT_FEES.stampTaxRate,
    transferFeeRate: DEFAULT_FEES.transferFeeRate,
  };

  function computeCost(
    price: number,
    quantity: number,
    side: "buy" | "sell",
  ): {
    commission: number;
    stampTax: number;
    transferFee: number;
    total: number;
    turnover: number;
  } {
    const turnover = price * quantity;
    const commission = Math.max(turnover * fees.commissionRate, fees.minCommission);
    const stampTax = side === "sell" ? turnover * fees.stampTaxRate : 0;
    const transferFee = turnover * fees.transferFeeRate;
    const total = commission + stampTax + transferFee;
    return {
      commission: Math.round(commission * 100) / 100,
      stampTax: Math.round(stampTax * 100) / 100,
      transferFee: Math.round(transferFee * 100) / 100,
      total: Math.round(total * 100) / 100,
      turnover: Math.round(turnover * 100) / 100,
    };
  }

  return {
    name: "transaction_cost",
    description:
      "A股交易成本分析工具。计算佣金、印花税、过户费，分析交易摩擦成本对收益的影响。\n" +
      "• action=estimate — 预估某笔交易的成本明细（需指定price, quantity, side）\n" +
      "• action=round_trip — 计算一次完整买卖的往返成本\n" +
      "• action=breakeven — 计算保本卖出价格（需指定买入价和数量）\n" +
      "• action=history — 分析当前持仓的累计交易成本\n" +
      "• action=optimize — 交易成本优化建议",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["estimate", "round_trip", "breakeven", "history", "optimize"],
          description:
            "操作类型: estimate=单笔预估, round_trip=往返成本, breakeven=保本价, history=历史成本, optimize=优化建议",
        },
        price: { type: "number", description: "股票价格（元）" },
        quantity: { type: "number", description: "股数（手×100）" },
        side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "买/卖方向（estimate时使用）",
        },
        sellPrice: { type: "number", description: "计划卖出价（round_trip时使用）" },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "estimate";

      if (action === "estimate") {
        const price = args.price as number;
        const quantity = args.quantity as number;
        const side = (args.side as "buy" | "sell") ?? "buy";
        if (!price || !quantity) return { error: "请提供 price 和 quantity" };

        const cost = computeCost(price, quantity, side);
        const costRatio = cost.turnover > 0 ? (cost.total / cost.turnover) * 100 : 0;
        return {
          action: "estimate",
          side,
          price,
          quantity,
          ...cost,
          costRatio: `${costRatio.toFixed(4)}%`,
          feeSchedule: {
            commissionRate: `${(fees.commissionRate * 10000).toFixed(1)}‱ (万${(fees.commissionRate * 10000).toFixed(1)})`,
            stampTaxRate: side === "sell" ? `${(fees.stampTaxRate * 1000).toFixed(1)}‰` : "N/A (仅卖出)",
            transferFeeRate: `${(fees.transferFeeRate * 100000).toFixed(1)}‱‱`,
          },
        };
      }

      if (action === "round_trip") {
        const buyPrice = args.price as number;
        const quantity = args.quantity as number;
        const sellPrice = (args.sellPrice as number) ?? buyPrice;
        if (!buyPrice || !quantity) return { error: "请提供 price 和 quantity" };

        const buyCost = computeCost(buyPrice, quantity, "buy");
        const sellCost = computeCost(sellPrice, quantity, "sell");
        const totalCost = buyCost.total + sellCost.total;
        const grossProfit = (sellPrice - buyPrice) * quantity;
        const netProfit = grossProfit - totalCost;

        return {
          action: "round_trip",
          buyPrice,
          sellPrice,
          quantity,
          buyCost,
          sellCost,
          totalRoundTripCost: Math.round(totalCost * 100) / 100,
          grossProfit: Math.round(grossProfit * 100) / 100,
          netProfit: Math.round(netProfit * 100) / 100,
          costEatsProfitPct:
            grossProfit > 0
              ? `${((totalCost / grossProfit) * 100).toFixed(1)}%`
              : "N/A",
          note: grossProfit > 0 && totalCost / grossProfit > 0.3
            ? "⚠️ 交易成本占毛利超30%，建议扩大盈利空间或降低交易频率"
            : undefined,
        };
      }

      if (action === "breakeven") {
        const buyPrice = args.price as number;
        const quantity = args.quantity as number;
        if (!buyPrice || !quantity) return { error: "请提供买入 price 和 quantity" };

        const buyCost = computeCost(buyPrice, quantity, "buy");
        const totalBuyOutlay = buyPrice * quantity + buyCost.total;

        // Solve: sellPrice * quantity - sellCost(sellPrice) = totalBuyOutlay
        // sellPrice * Q - max(sellPrice*Q*commRate, minComm) - sellPrice*Q*stampRate - sellPrice*Q*transferRate = totalBuyOutlay
        // Iterative for simplicity
        let lo = buyPrice * 0.9;
        let hi = buyPrice * 1.5;
        for (let i = 0; i < 50; i++) {
          const mid = (lo + hi) / 2;
          const sc = computeCost(mid, quantity, "sell");
          const netProceeds = mid * quantity - sc.total;
          if (netProceeds < totalBuyOutlay) lo = mid;
          else hi = mid;
        }
        const breakevenPrice = Math.ceil(((lo + hi) / 2) * 100) / 100;
        const spreadPct = ((breakevenPrice - buyPrice) / buyPrice) * 100;

        return {
          action: "breakeven",
          buyPrice,
          quantity,
          buyCost: buyCost.total,
          breakevenSellPrice: breakevenPrice,
          spreadPct: `${spreadPct.toFixed(2)}%`,
          note: `至少涨 ${spreadPct.toFixed(2)}% 才能保本（含佣金+印花税+过户费）`,
        };
      }

      if (action === "history") {
        const engine = getEngine();
        const positions = engine.getPositions();
        if (positions.length === 0)
          return { action: "history", note: "当前无持仓", positions: [] };

        const positionCosts = positions.map(
          (p: { symbol: string; quantity: number; avgCost?: number; marketValue?: number }) => {
            const cost = computeCost(p.avgCost ?? 0, p.quantity, "buy");
            return {
              symbol: p.symbol,
              quantity: p.quantity,
              avgCost: p.avgCost,
              entryCost: cost.total,
              estimatedExitCost: computeCost(
                p.marketValue ? p.marketValue / p.quantity : p.avgCost ?? 0,
                p.quantity,
                "sell",
              ).total,
            };
          },
        );

        const totalEntryCost = positionCosts.reduce(
          (s: number, p: { entryCost: number }) => s + p.entryCost,
          0,
        );
        const totalExitCost = positionCosts.reduce(
          (s: number, p: { estimatedExitCost: number }) => s + p.estimatedExitCost,
          0,
        );

        return {
          action: "history",
          positionCount: positions.length,
          totalEntryCost: Math.round(totalEntryCost * 100) / 100,
          estimatedExitCost: Math.round(totalExitCost * 100) / 100,
          totalRoundTripCost: Math.round((totalEntryCost + totalExitCost) * 100) / 100,
          positions: positionCosts,
        };
      }

      if (action === "optimize") {
        const tips: string[] = [
          `当前佣金费率: 万${(fees.commissionRate * 10000).toFixed(1)}，如高于万1.5可联系券商降佣`,
          "印花税(卖出千0.5)为国家统一征收，无法降低",
          "减少短线频繁交易可有效降低年化摩擦成本",
          "单笔交易金额过低时，最低佣金(5元)的影响显著，建议单笔不低于2万元",
          "ETF交易免印花税，可用ETF替代个股实现板块配置",
          "可转债交易佣金可低至万0.5，且无印花税",
        ];

        return {
          action: "optimize",
          currentFees: {
            commission: `万${(fees.commissionRate * 10000).toFixed(1)}`,
            stampTax: "千0.5 (卖出)",
            transferFee: "十万分之一",
            minCommission: `${fees.minCommission}元/笔`,
          },
          tips,
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
