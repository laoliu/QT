import type { TickData, TickFeatures } from "../types.js";

type TickFeatureStoreOptions = {
  maxTicksPerSymbol?: number;
  maxFeatureHistoryPerSymbol?: number;
  shortMomentumTicks?: number;
  mediumMomentumTicks?: number;
  vwapWindowTicks?: number;
  volatilityWindowTicks?: number;
};

type TickAccumulator = {
  ticks: TickData[];
  featureHistory: TickFeatures[];
  lastCumulativeVolume: number | null;
  lastCumulativeAmount: number | null;
  lastFeatures: TickFeatures | null;
};

function clampWindow(length: number, preferred: number): number {
  return Math.max(1, Math.min(length, preferred));
}

function calcMomentumPct(ticks: TickData[], lookback: number): number {
  if (ticks.length < 2) return 0;
  const window = clampWindow(ticks.length - 1, lookback);
  const latest = ticks[ticks.length - 1]!;
  const anchor = ticks[ticks.length - 1 - window]!;
  return anchor.price > 0 ? ((latest.price - anchor.price) / anchor.price) * 100 : 0;
}

function calcMicroVwap(ticks: TickData[], windowSize: number): number {
  const start = Math.max(0, ticks.length - windowSize);
  let sumPV = 0;
  let sumVolume = 0;
  for (let index = start; index < ticks.length; index++) {
    const tick = ticks[index]!;
    const prev = index > start ? ticks[index - 1] : undefined;
    const volumeDelta =
      prev && tick.volume >= prev.volume ? tick.volume - prev.volume : Math.max(1, tick.volume);
    sumPV += tick.price * volumeDelta;
    sumVolume += volumeDelta;
  }
  return sumVolume > 0 ? sumPV / sumVolume : ticks[ticks.length - 1]!.price;
}

function calcSpreadBps(tick: TickData): number | undefined {
  const bid = tick.bidLevels?.[0]?.price;
  const ask = tick.askLevels?.[0]?.price;
  if (!bid || !ask || bid <= 0 || ask <= 0) return undefined;
  const mid = (bid + ask) / 2;
  return mid > 0 ? ((ask - bid) / mid) * 10_000 : undefined;
}

function calcOrderBookImbalance(tick: TickData): number | undefined {
  const bidVolume = tick.bidLevels?.reduce((sum, level) => sum + level.volume, 0) ?? 0;
  const askVolume = tick.askLevels?.reduce((sum, level) => sum + level.volume, 0) ?? 0;
  const total = bidVolume + askVolume;
  return total > 0 ? (bidVolume - askVolume) / total : undefined;
}

function calcRealizedVolBps(ticks: TickData[], lookback: number): number | undefined {
  const window = clampWindow(ticks.length - 1, lookback);
  if (window < 2) return undefined;

  const returns: number[] = [];
  for (let index = ticks.length - window; index < ticks.length; index++) {
    const prev = ticks[index - 1];
    const current = ticks[index];
    if (!prev || !current || prev.price <= 0 || current.price <= 0) continue;
    returns.push(Math.log(current.price / prev.price));
  }
  if (returns.length < 2) return undefined;

  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const variance =
    returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(Math.max(0, variance)) * 10_000;
}

export class TickFeatureStore {
  private readonly maxTicksPerSymbol: number;
  private readonly maxFeatureHistoryPerSymbol: number;
  private readonly shortMomentumTicks: number;
  private readonly mediumMomentumTicks: number;
  private readonly vwapWindowTicks: number;
  private readonly volatilityWindowTicks: number;
  private readonly bySymbol = new Map<string, TickAccumulator>();

  constructor(opts?: TickFeatureStoreOptions) {
    this.maxTicksPerSymbol = opts?.maxTicksPerSymbol ?? 256;
    this.maxFeatureHistoryPerSymbol = opts?.maxFeatureHistoryPerSymbol ?? 256;
    this.shortMomentumTicks = opts?.shortMomentumTicks ?? 5;
    this.mediumMomentumTicks = opts?.mediumMomentumTicks ?? 20;
    this.vwapWindowTicks = opts?.vwapWindowTicks ?? 32;
    this.volatilityWindowTicks = opts?.volatilityWindowTicks ?? 24;
  }

  recordTick(tick: TickData): TickFeatures {
    const entry = this.bySymbol.get(tick.symbol.code) ?? {
      ticks: [],
      featureHistory: [],
      lastCumulativeVolume: null,
      lastCumulativeAmount: null,
      lastFeatures: null,
    };

    entry.ticks.push(tick);
    if (entry.ticks.length > this.maxTicksPerSymbol) {
      entry.ticks.splice(0, entry.ticks.length - this.maxTicksPerSymbol);
    }

    const volumeDelta =
      entry.lastCumulativeVolume != null && tick.volume >= entry.lastCumulativeVolume
        ? tick.volume - entry.lastCumulativeVolume
        : tick.volume;
    const amountDelta =
      entry.lastCumulativeAmount != null && tick.amount >= entry.lastCumulativeAmount
        ? tick.amount - entry.lastCumulativeAmount
        : tick.amount;

    entry.lastCumulativeVolume = tick.volume;
    entry.lastCumulativeAmount = tick.amount;

    const features: TickFeatures = {
      symbol: tick.symbol.code,
      timestamp: tick.timestamp,
      lastPrice: tick.price,
      microVwap: calcMicroVwap(entry.ticks, this.vwapWindowTicks),
      shortMomentumPct: calcMomentumPct(entry.ticks, this.shortMomentumTicks),
      mediumMomentumPct: calcMomentumPct(entry.ticks, this.mediumMomentumTicks),
      spreadBps: calcSpreadBps(tick),
      orderBookImbalance: calcOrderBookImbalance(tick),
      realizedVolBps: calcRealizedVolBps(entry.ticks, this.volatilityWindowTicks),
      volumeDelta,
      amountDelta,
    };

    entry.lastFeatures = features;
    entry.featureHistory.push(features);
    if (entry.featureHistory.length > this.maxFeatureHistoryPerSymbol) {
      entry.featureHistory.splice(0, entry.featureHistory.length - this.maxFeatureHistoryPerSymbol);
    }
    this.bySymbol.set(tick.symbol.code, entry);
    return features;
  }

  getFeatures(symbol: string): TickFeatures | null {
    return this.bySymbol.get(symbol)?.lastFeatures ?? null;
  }

  getAllFeatures(): TickFeatures[] {
    const result: TickFeatures[] = [];
    for (const acc of this.bySymbol.values()) {
      if (acc.lastFeatures) result.push(acc.lastFeatures);
    }
    return result;
  }

  getRecentFeatures(symbol: string, limit = 100): TickFeatures[] {
    const entry = this.bySymbol.get(symbol);
    if (!entry || entry.featureHistory.length === 0) return [];
    const n = Math.max(1, Math.floor(limit));
    return entry.featureHistory.slice(-n);
  }
}
