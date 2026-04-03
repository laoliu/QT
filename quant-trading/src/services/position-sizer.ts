/**
 * PositionSizer — computes optimal order quantity based on sizing method.
 *
 * Methods:
 *   fixed_amount  — buy a fixed CNY amount (e.g. ¥10000 per trade)
 *   fixed_pct     — buy a fixed % of total equity (e.g. 10%)
 *   risk_pct      — risk N% of equity per trade (qty = riskAmt / stopDistance)
 *   kelly         — Kelly criterion: f* = (winRate × avgWin/avgLoss - lossRate) / (avgWin/avgLoss)
 *   atr_vol       — volatility-adjusted: qty = riskAmt / (atrMultiplier × ATR)
 *
 * All methods auto-round to A-share lot size (100 shares).
 */

export type SizingMethod = "fixed_amount" | "fixed_pct" | "risk_pct" | "kelly" | "atr_vol";

export interface SizingParams {
  method: SizingMethod;
  /** Total account equity (cash + positions). */
  equity: number;
  /** Current price of the target stock. */
  price: number;

  // ── Method-specific params ──
  /** CNY amount per trade (fixed_amount). */
  amount?: number;
  /** Percentage of equity (fixed_pct: 0-1, e.g. 0.1 = 10%). */
  pct?: number;
  /** Risk percentage of equity per trade (risk_pct/kelly/atr_vol: 0-1, e.g. 0.02 = 2%). */
  riskPct?: number;
  /** Stop-loss distance in CNY (risk_pct). E.g. stopDistance=2 means stop is ¥2 below entry. */
  stopDistance?: number;
  /** Stop-loss percentage (risk_pct alternative). E.g. stopPct=0.05 means 5% stop. */
  stopPct?: number;

  // ── Kelly params ──
  /** Historical win rate (0-1). */
  winRate?: number;
  /** Average win amount / Average loss amount ratio. */
  winLossRatio?: number;
  /** Kelly fraction divisor for half/quarter Kelly (default 1 = full Kelly). */
  kellyDivisor?: number;

  // ── ATR volatility params ──
  /** Current ATR value. */
  atrValue?: number;
  /** ATR multiplier for position sizing (default 2). */
  atrMultiplier?: number;

  // ── Limits ──
  /** Maximum position value as fraction of equity (0-1, default 0.3). */
  maxPositionPct?: number;
  /** Maximum shares to buy (absolute cap). */
  maxQuantity?: number;
  /** A-share lot size (default 100). */
  lotSize?: number;
}

export interface SizingResult {
  /** Computed quantity (rounded to lot size). */
  quantity: number;
  /** Estimated position value = quantity × price. */
  positionValue: number;
  /** Risk amount per trade (if applicable). */
  riskAmount?: number;
  /** Kelly fraction (if method=kelly). */
  kellyFraction?: number;
  /** Sizing method used. */
  method: SizingMethod;
  /** Human-readable explanation. */
  explanation: string;
}

/**
 * Compute optimal position size based on the given method and parameters.
 */
export function computePositionSize(params: SizingParams): SizingResult {
  const lotSize = params.lotSize ?? 100;
  const maxPosPct = params.maxPositionPct ?? 0.3;
  const maxValue = params.equity * maxPosPct;

  let rawQty: number;
  let explanation: string;
  let riskAmount: number | undefined;
  let kellyFraction: number | undefined;

  switch (params.method) {
    case "fixed_amount": {
      const amount = params.amount ?? 10000;
      rawQty = amount / params.price;
      explanation = `固定金额 ¥${amount.toFixed(0)} / ¥${params.price.toFixed(2)} = ${Math.floor(rawQty)} 股`;
      break;
    }

    case "fixed_pct": {
      const pct = params.pct ?? 0.1;
      const amount = params.equity * pct;
      rawQty = amount / params.price;
      explanation = `固定比例 ${(pct * 100).toFixed(1)}% × ¥${params.equity.toFixed(0)} = ¥${amount.toFixed(0)} / ¥${params.price.toFixed(2)}`;
      break;
    }

    case "risk_pct": {
      const riskPct = params.riskPct ?? 0.02;
      riskAmount = params.equity * riskPct;
      const stopDist =
        params.stopDistance ??
        (params.stopPct ? params.price * params.stopPct : params.price * 0.05);
      if (stopDist <= 0) {
        rawQty = 0;
        explanation = "止损距离为零，无法计算仓位";
        break;
      }
      rawQty = riskAmount / stopDist;
      explanation =
        `风险比例 ${(riskPct * 100).toFixed(1)}% × ¥${params.equity.toFixed(0)} = 风险 ¥${riskAmount.toFixed(0)}, ` +
        `止损距离 ¥${stopDist.toFixed(2)} → ${Math.floor(rawQty)} 股`;
      break;
    }

    case "kelly": {
      const winRate = params.winRate ?? 0.5;
      const wlRatio = params.winLossRatio ?? 1.5;
      const divisor = params.kellyDivisor ?? 2; // half-Kelly by default (safer)
      // Kelly formula: f = (p × b - q) / b where p=winRate, q=1-p, b=win/loss ratio
      const f = (winRate * wlRatio - (1 - winRate)) / wlRatio;
      kellyFraction = Math.max(0, f / divisor);
      const amount = params.equity * kellyFraction;
      riskAmount = amount;
      rawQty = amount / params.price;
      explanation =
        `Kelly公式: f*=${f.toFixed(3)} / ${divisor} = ${(kellyFraction * 100).toFixed(1)}%, ` +
        `¥${amount.toFixed(0)} / ¥${params.price.toFixed(2)} (胜率${(winRate * 100).toFixed(0)}%, 盈亏比${wlRatio.toFixed(1)})`;
      break;
    }

    case "atr_vol": {
      const riskPct = params.riskPct ?? 0.02;
      riskAmount = params.equity * riskPct;
      const atr = params.atrValue ?? params.price * 0.03;
      const mult = params.atrMultiplier ?? 2;
      const riskPerShare = atr * mult;
      if (riskPerShare <= 0) {
        rawQty = 0;
        explanation = "ATR为零，无法计算仓位";
        break;
      }
      rawQty = riskAmount / riskPerShare;
      explanation = `ATR波动率: 风险 ¥${riskAmount.toFixed(0)} / (ATR ¥${atr.toFixed(2)} × ${mult}) = ${Math.floor(rawQty)} 股`;
      break;
    }

    default:
      rawQty = 0;
      explanation = `未知仓位管理方法: ${params.method}`;
  }

  // Round down to lot size (with epsilon to handle floating-point edge cases)
  let quantity = Math.floor((rawQty + 1e-9) / lotSize) * lotSize;

  // Apply absolute max
  if (params.maxQuantity && quantity > params.maxQuantity) {
    quantity = Math.floor(params.maxQuantity / lotSize) * lotSize;
  }

  // Apply max position value cap
  const posValue = quantity * params.price;
  if (posValue > maxValue) {
    quantity = Math.floor(maxValue / params.price / lotSize) * lotSize;
  }

  // Final floor at 0
  quantity = Math.max(0, quantity);

  return {
    quantity,
    positionValue: quantity * params.price,
    riskAmount,
    kellyFraction,
    method: params.method,
    explanation,
  };
}
