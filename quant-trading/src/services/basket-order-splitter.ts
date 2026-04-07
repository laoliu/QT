/**
 * BasketOrderSplitter — splits a basket order into individual leg orders.
 *
 * Strategies:
 *   equal       — equal weight across all symbols (1/N)
 *   market_cap  — weight by market capitalisation (requires market cap data)
 *   custom      — user-provided weights (must sum to ~100%)
 *   by_amount   — split total cash by ratio into fixed CNY amounts per symbol
 *   by_quantity — allocate fixed share quantity per symbol
 *
 * All strategies auto-round to A-share lot size (default 100 shares).
 */
import { randomUUID } from "node:crypto";
import type {
  BasketLeg,
  BasketOrderRequest,
  BasketSplitResult,
  BasketSymbolInput,
  BasketWeightStrategy,
} from "../types.js";

/** Price lookup callback — returns current price for a symbol, or null if unavailable. */
export type PriceLookup = (symbol: string) => Promise<number | null>;

/** Market cap lookup callback — returns market cap in CNY, or null if unavailable. */
export type MarketCapLookup = (symbol: string) => Promise<number | null>;

export interface BasketSplitterDeps {
  getPrice: PriceLookup;
  getMarketCap?: MarketCapLookup;
}

/**
 * Split a basket order request into individual legs.
 */
export async function splitBasketOrder(
  req: BasketOrderRequest,
  deps: BasketSplitterDeps,
): Promise<BasketSplitResult> {
  const basketId = req.basketId ?? randomUUID();
  const lotSize = req.lotSize ?? 100;
  const cashReservePct = req.cashReservePct ?? 0;
  const totalAmount = req.totalAmount ?? 0;
  const investableAmount = totalAmount * (1 - cashReservePct);

  const legs: BasketLeg[] = [];
  const skipped: Array<{ symbol: string; reason: string }> = [];

  // Fetch prices for all symbols
  const priceMap = new Map<string, number>();
  for (const s of req.symbols) {
    if (s.price && s.price > 0) {
      priceMap.set(s.symbol, s.price);
    } else {
      const p = await deps.getPrice(s.symbol);
      if (p && p > 0) {
        priceMap.set(s.symbol, p);
      }
    }
  }

  // Filter out symbols without prices (unless by_quantity with explicit qty)
  const validSymbols: BasketSymbolInput[] = [];
  for (const s of req.symbols) {
    if (req.strategy === "by_quantity" && s.quantity && s.quantity > 0) {
      validSymbols.push(s);
    } else if (priceMap.has(s.symbol)) {
      validSymbols.push(s);
    } else {
      skipped.push({ symbol: s.symbol, reason: "无法获取价格" });
    }
  }

  if (validSymbols.length === 0) {
    return {
      basketId,
      strategy: req.strategy,
      totalAmount,
      investableAmount,
      legs: [],
      skipped,
      summary: "所有标的均无法获取价格，无法拆分",
    };
  }

  // Compute weights based on strategy
  const weights = await computeWeights(req.strategy, validSymbols, deps, investableAmount);

  // Generate legs
  for (let i = 0; i < validSymbols.length; i++) {
    const sym = validSymbols[i];
    const weight = weights[i];
    const price = priceMap.get(sym.symbol) ?? 0;

    let quantity: number;

    if (req.strategy === "by_quantity") {
      // by_quantity: use explicit quantity or distribute totalQuantity equally
      if (sym.quantity && sym.quantity > 0) {
        quantity = roundToLot(sym.quantity, lotSize);
      } else if (req.totalQuantity && req.totalQuantity > 0) {
        const perSymbol = req.totalQuantity / validSymbols.length;
        quantity = roundToLot(perSymbol, lotSize);
      } else {
        quantity = lotSize; // fallback: 1 lot
      }
    } else {
      // Weight-based strategies: allocate investableAmount by weight
      if (sym.quantity && sym.quantity > 0) {
        // Explicit quantity override
        quantity = roundToLot(sym.quantity, lotSize);
      } else if (price > 0) {
        const allocatedAmount = investableAmount * weight;
        quantity = roundToLot(allocatedAmount / price, lotSize);
      } else {
        quantity = 0;
      }
    }

    if (quantity <= 0) {
      skipped.push({ symbol: sym.symbol, reason: "计算后数量为零" });
      continue;
    }

    const estimatedValue = quantity * (price > 0 ? price : 0);

    legs.push({
      symbol: sym.symbol,
      side: req.side,
      quantity,
      estimatedValue,
      price: price > 0 ? price : 0,
      weight,
      reason: buildLegReason(req.strategy, weight, quantity, price, estimatedValue),
    });
  }

  // Compute actual total weight for normalised display
  const totalAllocated = legs.reduce((s, l) => s + l.estimatedValue, 0);

  const summary = buildSummary(req.strategy, investableAmount, totalAllocated, legs, skipped);

  return {
    basketId,
    strategy: req.strategy,
    totalAmount,
    investableAmount,
    legs,
    skipped,
    summary,
  };
}

// ── Weight computation ──────────────────────────────────────────

async function computeWeights(
  strategy: BasketWeightStrategy,
  symbols: BasketSymbolInput[],
  deps: BasketSplitterDeps,
  _investableAmount: number,
): Promise<number[]> {
  switch (strategy) {
    case "equal":
      return equalWeights(symbols.length);

    case "market_cap":
      return marketCapWeights(symbols, deps);

    case "custom":
      return customWeights(symbols);

    case "by_amount":
      return byAmountWeights(symbols);

    case "by_quantity":
      // Weight is computed post-hoc for display; equal placeholder here
      return equalWeights(symbols.length);

    default:
      return equalWeights(symbols.length);
  }
}

/** Equal weight: 1/N */
function equalWeights(n: number): number[] {
  const w = 1 / n;
  return Array.from({ length: n }, () => w);
}

/** Market cap weighted: weight_i = marketCap_i / sum(marketCap) */
async function marketCapWeights(
  symbols: BasketSymbolInput[],
  deps: BasketSplitterDeps,
): Promise<number[]> {
  if (!deps.getMarketCap) {
    // Fallback to equal weight if no market cap provider
    return equalWeights(symbols.length);
  }

  const caps: number[] = [];
  for (const s of symbols) {
    const cap = await deps.getMarketCap(s.symbol);
    caps.push(cap ?? 0);
  }

  const totalCap = caps.reduce((s, c) => s + c, 0);
  if (totalCap <= 0) {
    return equalWeights(symbols.length);
  }

  return caps.map((c) => c / totalCap);
}

/**
 * Custom weights: use user-provided weight values.
 * Normalises to sum=1 if they don't already.
 */
function customWeights(symbols: BasketSymbolInput[]): number[] {
  const raw = symbols.map((s) => {
    if (s.weight == null || s.weight <= 0) return 0;
    // Accept both percentage (30) and decimal (0.3)
    return s.weight > 1 ? s.weight / 100 : s.weight;
  });

  const total = raw.reduce((s, w) => s + w, 0);
  if (total <= 0) {
    return equalWeights(symbols.length);
  }

  // Normalise
  return raw.map((w) => w / total);
}

/**
 * By-amount: if custom weights are provided, use them as ratios.
 * Otherwise, equal allocation.
 */
function byAmountWeights(symbols: BasketSymbolInput[]): number[] {
  const hasCustom = symbols.some((s) => s.weight != null && s.weight > 0);
  if (hasCustom) {
    return customWeights(symbols);
  }
  return equalWeights(symbols.length);
}

// ── Helpers ─────────────────────────────────────────────────────

function roundToLot(qty: number, lotSize: number): number {
  return Math.max(0, Math.floor((qty + 1e-9) / lotSize) * lotSize);
}

function buildLegReason(
  strategy: BasketWeightStrategy,
  weight: number,
  quantity: number,
  price: number,
  value: number,
): string {
  const pctStr = (weight * 100).toFixed(1);
  const priceStr = price > 0 ? `¥${price.toFixed(2)}` : "N/A";
  const valueStr = value > 0 ? `¥${value.toLocaleString()}` : "N/A";

  switch (strategy) {
    case "equal":
      return `等权 ${pctStr}%, ${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
    case "market_cap":
      return `市值加权 ${pctStr}%, ${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
    case "custom":
      return `自定义权重 ${pctStr}%, ${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
    case "by_amount":
      return `按资金比例 ${pctStr}%, ${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
    case "by_quantity":
      return `按股数分配 ${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
    default:
      return `${quantity}股 @ ${priceStr} ≈ ${valueStr}`;
  }
}

function buildSummary(
  strategy: BasketWeightStrategy,
  investable: number,
  totalAllocated: number,
  legs: BasketLeg[],
  skipped: Array<{ symbol: string; reason: string }>,
): string {
  const strategyNames: Record<BasketWeightStrategy, string> = {
    equal: "等权分配",
    market_cap: "市值加权",
    custom: "自定义权重",
    by_amount: "按资金分配",
    by_quantity: "按股数分配",
  };

  const lines: string[] = [
    `策略: ${strategyNames[strategy] ?? strategy}`,
    `可投资金额: ¥${investable.toLocaleString()}`,
    `实际分配: ¥${totalAllocated.toLocaleString()} (${investable > 0 ? ((totalAllocated / investable) * 100).toFixed(1) : 0}%)`,
    `拆分腿数: ${legs.length}`,
  ];

  if (skipped.length > 0) {
    lines.push(`跳过: ${skipped.map((s) => `${s.symbol}(${s.reason})`).join(", ")}`);
  }

  return lines.join("\n");
}
