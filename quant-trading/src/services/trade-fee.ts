/**
 * TradeFeeCalculator — 010602
 *
 * 01 A 0400:
 *  - 04: 成交金额 × 费率 (083), 最低 5 06
 *  - 0300: 卖出金额 × 0.0005 (万5, 0200)
 *  - 过户费: 成交金额 × 0.00001 (万0.1, 仅沪市)
 *
 * 0501081904 (futu):
 *  - 04: 成交金额 × 费率 (083), 最低 3 HKD
 *  - 交易征费: 成交金额 × 0.00278%
 *  - 03: 成交金额 × 0.002% (最低 2 HKD)
 *  - 0300: 成交金额 × 0.13% (买卖双向)
 */

import type { MarketType } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface TradeFee {
  /** 04 (Commission) */
  commission: number;
  /** 0300 (Stamp duty) */
  stampDuty: number;
  /** 过户费 (Transfer fee, 仅沪市) */
  transferFee: number;
  /** 0003 */
  totalFee: number;
  /** 保本价 (050709) */
  breakEvenPrice: number;
}

export interface FeeConfig {
  /** 佣金费率 (08 0.0003 = 万3) */
  commissionRate?: number;
  /** 最低佣金 (08 5 06 for A 股, 3 HKD for 19) */
  minCommission?: number;
  /** 0300 (08 0.0005 = 万5, A 股卖出收取) */
  stampDutyRate?: number;
  /** 过户费费率 (08 0.00001 = 万0.1, 仅沪市) */
  transferFeeRate?: number;
}

export interface FeeCalculationInput {
  /** 0018 */
  price: number;
  /** 成交数量 */
  quantity: number;
  /** 买卖方向 */
  side: "buy" | "sell";
  /** г (0105) */
  market?: MarketType;
  /** 交易所 (沪市 SH / 深市 SZ, 用于过户费判断) */
  exchange?: "SH" | "SZ";
}

// ── Default Fee Rates ────────────────────────────────────────

const CN_DEFAULTS: Required<FeeConfig> = {
  commissionRate: 0.0003,     // 万3
  minCommission: 5,           // 最低506
  stampDutyRate: 0.0005,      // 万5 (卖出)
  transferFeeRate: 0.00001,   // 万0.1 (沪市)
};

const HK_DEFAULTS: Required<FeeConfig> = {
  commissionRate: 0.0003,     // 万3
  minCommission: 3,           // 最低306
  stampDutyRate: 0.0013,      // 0.13% (05)
  transferFeeRate: 0,         // 1911 (包含在交收费中)
};

const US_DEFAULTS: Required<FeeConfig> = {
  commissionRate: 0,          // 美股通常零佣金
  minCommission: 0,
  stampDutyRate: 0,
  transferFeeRate: 0,
};

// ── Calculator ───────────────────────────────────────────────

export class TradeFeeCalculator {
  private feeConfig: Required<FeeConfig>;
  private market: MarketType;

  constructor(market: MarketType = "cn_stock", overrides?: FeeConfig) {
    this.market = market;
    const defaults = market === "hk_stock" ? HK_DEFAULTS
                   : market === "us_stock" ? US_DEFAULTS
                   : CN_DEFAULTS;
    this.feeConfig = { ...defaults, ...overrides };
  }

  /** 100401 */
  calculate(input: FeeCalculationInput): TradeFee {
    const { price, quantity, side, exchange } = input;
    const market = input.market ?? this.market;
    const amount = price * quantity;

    // Pick the right defaults for this specific trade's market
    const cfg = market === this.market
      ? this.feeConfig
      : market === "hk_stock" ? { ...HK_DEFAULTS }
      : market === "us_stock" ? { ...US_DEFAULTS }
      : { ...CN_DEFAULTS };

    // 04: max(成交金额 × 费率, 最低佣金)
    const commission = Math.max(amount * cfg.commissionRate, cfg.minCommission);

    // 0300: A0000; 190500
    let stampDuty = 0;
    if (market === "cn_stock") {
      if (side === "sell") {
        stampDuty = amount * cfg.stampDutyRate;
      }
    } else if (market === "hk_stock") {
      // 1905, 向上取整至最近的 1 HKD
      stampDuty = Math.ceil(amount * cfg.stampDutyRate);
    }

    // 过户费: A0000
    let transferFee = 0;
    if (market === "cn_stock") {
      const isShanghai = exchange === "SH" || (!exchange && this.guessExchange(input) === "SH");
      if (isShanghai) {
        transferFee = amount * cfg.transferFeeRate;
      }
    }

    const totalFee = commission + stampDuty + transferFee;

    // 保本价计算: 从买入到卖出的总费用平衡
    const breakEvenPrice = this.calculateBreakEvenPrice(price, quantity, side, market, cfg, exchange);

    return {
      commission: Math.round(commission * 100) / 100,
      stampDuty: Math.round(stampDuty * 100) / 100,
      transferFee: Math.round(transferFee * 100) / 100,
      totalFee: Math.round(totalFee * 100) / 100,
      breakEvenPrice: Math.round(breakEvenPrice * 1000) / 1000,
    };
  }

  /** 批量计算多笔交易费用 */
  calculateBatch(inputs: FeeCalculationInput[]): { fees: TradeFee[]; totalFees: number } {
    const fees = inputs.map((input) => this.calculate(input));
    const totalFees = fees.reduce((sum, f) => sum + f.totalFee, 0);
    return { fees, totalFees: Math.round(totalFees * 100) / 100 };
  }

  /**
   * 计算保本价 — 08071415
   * 04: 买入总费用 + 卖出总费用 = 卖出金额 - 买入金额
   */
  private calculateBreakEvenPrice(
    price: number,
    quantity: number,
    side: "buy" | "sell",
    market: MarketType,
    cfg: Required<FeeConfig>,
    exchange?: "SH" | "SZ",
  ): number {
    if (side === "sell") {
      // 卖出时保本价就是"08020508 + 卖出费用"的逆算, 8909
      return price;
    }

    // 买入保本价: 需要卖出价 P_sell 00:
    // P_sell × qty - 买入费用 - 卖出费用(P_sell) = price × qty
    const buyAmount = price * quantity;
    const buyCommission = Math.max(buyAmount * cfg.commissionRate, cfg.minCommission);

    if (market === "cn_stock") {
      const isShanghai = exchange === "SH";
      const buyTransfer = isShanghai ? buyAmount * cfg.transferFeeRate : 0;
      const buyTotal = buyCommission + buyTransfer;

      // 卖出费用是 P_sell × qty 02:
      // sellFee = max(P_sell*qty*commRate, minComm) + P_sell*qty*stampRate + transferFee_sell
      // 简化: 假设卖出金额远大于最低佣金
      const sellFeeRate = cfg.commissionRate + cfg.stampDutyRate + (isShanghai ? cfg.transferFeeRate : 0);
      // P_sell × qty × (1 - sellFeeRate) = buyAmount + buyTotal
      const breakEven = (buyAmount + buyTotal) / (quantity * (1 - sellFeeRate));
      return breakEven;
    }

    if (market === "hk_stock") {
      const buyStamp = Math.ceil(buyAmount * cfg.stampDutyRate);
      const buyTotal = buyCommission + buyStamp;
      const sellFeeRate = cfg.commissionRate + cfg.stampDutyRate;
      const breakEven = (buyAmount + buyTotal) / (quantity * (1 - sellFeeRate));
      return breakEven;
    }

    // US market — usually zero fees
    return price;
  }

  /** 17070595 (沪市: 605; 深市: 0/305) */
  private guessExchange(input: FeeCalculationInput): "SH" | "SZ" {
    const code = input.price ? "" : ""; // placeholder
    // Try to extract numeric code from symbol
    const match = /^(\d{6})/.exec(String(input));
    if (match) {
      return match[1].startsWith("6") ? "SH" : "SZ";
    }
    return "SZ"; // default to 深市 (no transfer fee, conservative)
  }
}

/** Convenience: create a calculator with default A-stock settings */
export function createCNFeeCalculator(overrides?: FeeConfig): TradeFeeCalculator {
  return new TradeFeeCalculator("cn_stock", overrides);
}

/** Convenience: create a calculator with default HK-stock settings */
export function createHKFeeCalculator(overrides?: FeeConfig): TradeFeeCalculator {
  return new TradeFeeCalculator("hk_stock", overrides);
}
