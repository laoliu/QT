import type {
  KlineBar,
  TickAggregationInterval,
  TickData,
  TickFeatures,
  TickStrategyId,
  TradingSignal,
} from "../types.js";

export type TickStrategyInput = {
  tick: TickData;
  features: TickFeatures;
};

export type TickDrivenStrategy = {
  id: TickStrategyId;
  name: string;
  onTick(input: TickStrategyInput): TradingSignal | null;
  /** Called whenever the TickAggregator emits a completed bar. */
  onBar?(bar: KlineBar, interval: TickAggregationInterval): void;
  reset?(): void;
};

export type TickStrategyFactoryParams = {
  symbol: string;
  quantity?: number;
  parameters?: Record<string, unknown>;
};

type StatefulPosition = {
  inPosition: boolean;
  entryPrice: number;
};

function readNumber(
  params: Record<string, unknown> | undefined,
  key: string,
  fallback: number,
): number {
  const value = params?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function buySignal(
  strategyId: TickStrategyId,
  symbol: string,
  price: number,
  quantity: number,
  timestamp: number,
  reason: string,
): TradingSignal {
  return {
    timestamp,
    symbol,
    direction: "buy",
    price,
    quantity,
    reason,
    strategyId,
  };
}

function sellSignal(
  strategyId: TickStrategyId,
  symbol: string,
  price: number,
  quantity: number,
  timestamp: number,
  reason: string,
): TradingSignal {
  return {
    timestamp,
    symbol,
    direction: "sell",
    price,
    quantity,
    reason,
    strategyId,
  };
}

export function createTickMomentumImbalanceStrategy(
  params: TickStrategyFactoryParams,
): TickDrivenStrategy {
  const quantity = params.quantity ?? 100;
  const state: StatefulPosition = { inPosition: false, entryPrice: 0 };
  const entryMomentumPct = readNumber(params.parameters, "entryMomentumPct", 0.08);
  const mediumMomentumPct = readNumber(params.parameters, "mediumMomentumPct", 0.12);
  const minImbalance = readNumber(params.parameters, "minImbalance", 0.12);
  const maxSpreadBps = readNumber(params.parameters, "maxSpreadBps", 12);
  const takeProfitPct = readNumber(params.parameters, "takeProfitPct", 0.45);
  const stopLossPct = readNumber(params.parameters, "stopLossPct", 0.25);
  const exitMomentumPct = readNumber(params.parameters, "exitMomentumPct", -0.02);
  const exitImbalance = readNumber(params.parameters, "exitImbalance", -0.08);

  return {
    id: "tick-momentum-imbalance",
    name: `TickMomentumImbalance(${params.symbol})`,
    onTick({ tick, features }) {
      if (tick.symbol.code !== params.symbol) return null;

      const spreadBps = features.spreadBps ?? Number.POSITIVE_INFINITY;
      const imbalance = features.orderBookImbalance ?? 0;
      const inMomentumRegime =
        features.shortMomentumPct >= entryMomentumPct &&
        features.mediumMomentumPct >= mediumMomentumPct;

      if (
        !state.inPosition &&
        inMomentumRegime &&
        imbalance >= minImbalance &&
        spreadBps <= maxSpreadBps &&
        features.lastPrice >= features.microVwap
      ) {
        state.inPosition = true;
        state.entryPrice = features.lastPrice;
        return buySignal(
          "tick-momentum-imbalance",
          params.symbol,
          features.lastPrice,
          quantity,
          features.timestamp,
          `Tick动量突破: 短动量 ${features.shortMomentumPct.toFixed(3)}%, ` +
            `中动量 ${features.mediumMomentumPct.toFixed(3)}%, ` +
            `盘口失衡 ${imbalance.toFixed(2)}`,
        );
      }

      if (!state.inPosition || state.entryPrice <= 0) return null;

      const pnlPct = ((features.lastPrice - state.entryPrice) / state.entryPrice) * 100;
      const shouldExit =
        pnlPct >= takeProfitPct ||
        pnlPct <= -stopLossPct ||
        features.shortMomentumPct <= exitMomentumPct ||
        imbalance <= exitImbalance;

      if (!shouldExit) return null;

      state.inPosition = false;
      const reason =
        pnlPct >= takeProfitPct
          ? `Tick止盈: ${pnlPct.toFixed(3)}%`
          : pnlPct <= -stopLossPct
            ? `Tick止损: ${pnlPct.toFixed(3)}%`
            : `Tick动量衰减: 短动量 ${features.shortMomentumPct.toFixed(3)}%, 盘口失衡 ${imbalance.toFixed(2)}`;
      state.entryPrice = 0;
      return sellSignal(
        "tick-momentum-imbalance",
        params.symbol,
        features.lastPrice,
        quantity,
        features.timestamp,
        reason,
      );
    },
    reset() {
      state.inPosition = false;
      state.entryPrice = 0;
    },
  };
}

export function createTickVwapReversionStrategy(
  params: TickStrategyFactoryParams,
): TickDrivenStrategy {
  const quantity = params.quantity ?? 100;
  const state: StatefulPosition = { inPosition: false, entryPrice: 0 };
  const entryDeviationPct = readNumber(params.parameters, "entryDeviationPct", 0.12);
  const exitDeviationPct = readNumber(params.parameters, "exitDeviationPct", 0.03);
  const minImbalance = readNumber(params.parameters, "minImbalance", -0.05);
  const maxSpreadBps = readNumber(params.parameters, "maxSpreadBps", 15);
  const takeProfitPct = readNumber(params.parameters, "takeProfitPct", 0.35);
  const stopLossPct = readNumber(params.parameters, "stopLossPct", 0.22);

  return {
    id: "tick-vwap-reversion",
    name: `TickVwapReversion(${params.symbol})`,
    onTick({ tick, features }) {
      if (tick.symbol.code !== params.symbol) return null;

      const deviationPct =
        features.microVwap > 0
          ? ((features.lastPrice - features.microVwap) / features.microVwap) * 100
          : 0;
      const spreadBps = features.spreadBps ?? Number.POSITIVE_INFINITY;
      const imbalance = features.orderBookImbalance ?? 0;

      if (
        !state.inPosition &&
        deviationPct <= -entryDeviationPct &&
        spreadBps <= maxSpreadBps &&
        imbalance >= minImbalance
      ) {
        state.inPosition = true;
        state.entryPrice = features.lastPrice;
        return buySignal(
          "tick-vwap-reversion",
          params.symbol,
          features.lastPrice,
          quantity,
          features.timestamp,
          `Tick均值回归: 偏离VWAP ${deviationPct.toFixed(3)}%, 盘口失衡 ${imbalance.toFixed(2)}`,
        );
      }

      if (!state.inPosition || state.entryPrice <= 0) return null;

      const pnlPct = ((features.lastPrice - state.entryPrice) / state.entryPrice) * 100;
      const reverted = deviationPct >= -exitDeviationPct;
      const shouldExit = reverted || pnlPct >= takeProfitPct || pnlPct <= -stopLossPct;
      if (!shouldExit) return null;

      state.inPosition = false;
      const reason = reverted
        ? `Tick回归VWAP: 偏离收敛到 ${deviationPct.toFixed(3)}%`
        : pnlPct >= takeProfitPct
          ? `Tick回归止盈: ${pnlPct.toFixed(3)}%`
          : `Tick回归止损: ${pnlPct.toFixed(3)}%`;
      state.entryPrice = 0;
      return sellSignal(
        "tick-vwap-reversion",
        params.symbol,
        features.lastPrice,
        quantity,
        features.timestamp,
        reason,
      );
    },
    reset() {
      state.inPosition = false;
      state.entryPrice = 0;
    },
  };
}

export function createTickDrivenStrategy(
  strategyId: TickStrategyId,
  params: TickStrategyFactoryParams,
): TickDrivenStrategy {
  if (strategyId === "tick-momentum-imbalance") {
    return createTickMomentumImbalanceStrategy(params);
  }
  if (strategyId === "tick-momentum-1m-confirm") {
    return createTickMomentum1mConfirmStrategy(params);
  }
  return createTickVwapReversionStrategy(params);
}

export { createTickMomentum1mConfirmStrategy };

// ── Bar ring buffer helper ────────────────────────────────────────

class BarRingBuffer {
  private readonly bars: KlineBar[] = [];
  constructor(private readonly maxSize: number = 5) {}

  push(bar: KlineBar): void {
    this.bars.push(bar);
    if (this.bars.length > this.maxSize) this.bars.shift();
  }

  last(): KlineBar | null {
    return this.bars.length > 0 ? this.bars[this.bars.length - 1]! : null;
  }

  closeMa(n: number): number | null {
    if (this.bars.length < n) return null;
    const window = this.bars.slice(-n);
    return window.reduce((sum, b) => sum + b.close, 0) / n;
  }
}

// ── tick-momentum-1m-confirm ──────────────────────────────────────

/**
 * Hybrid strategy: tick features drive entry timing, but the most recently
 * completed 1m bar must be bullish (close > open AND close > 3-bar SMA)
 * before any buy fires. Exit is tick-only (TP/SL/momentum decay).
 *
 * Parameters:
 *   entryMomentumPct   — tick short-momentum threshold (default 0.06 %)
 *   minImbalance       — OBI threshold (default 0.10)
 *   maxSpreadBps       — max bid-ask spread (default 15 bps)
 *   barConfirmMaBars   — SMA period for bar confirmation (default 3)
 *   takeProfitPct      — TP vs entry (default 0.40 %)
 *   stopLossPct        — SL vs entry (default 0.22 %)
 *   exitMomentumPct    — tick-momentum exit threshold (default -0.03 %)
 */
function createTickMomentum1mConfirmStrategy(
  params: TickStrategyFactoryParams,
): TickDrivenStrategy {
  const quantity = params.quantity ?? 100;
  const state: StatefulPosition = { inPosition: false, entryPrice: 0 };
  const entryMomentumPct = readNumber(params.parameters, "entryMomentumPct", 0.06);
  const minImbalance = readNumber(params.parameters, "minImbalance", 0.1);
  const maxSpreadBps = readNumber(params.parameters, "maxSpreadBps", 15);
  const barConfirmMaBars = Math.max(
    1,
    Math.round(readNumber(params.parameters, "barConfirmMaBars", 3)),
  );
  const takeProfitPct = readNumber(params.parameters, "takeProfitPct", 0.4);
  const stopLossPct = readNumber(params.parameters, "stopLossPct", 0.22);
  const exitMomentumPct = readNumber(params.parameters, "exitMomentumPct", -0.03);

  const bars1m = new BarRingBuffer(barConfirmMaBars + 2);

  function isBullishBar(): boolean {
    const last = bars1m.last();
    if (!last) return false;
    const bullishCandle = last.close > last.open;
    const ma = bars1m.closeMa(barConfirmMaBars);
    const aboveMa = ma === null || last.close > ma;
    return bullishCandle && aboveMa;
  }

  return {
    id: "tick-momentum-1m-confirm",
    name: `TickMomentum1mConfirm(${params.symbol})`,

    onBar(bar, interval) {
      if (interval === "1m" && bar.close > 0) {
        bars1m.push(bar);
      }
    },

    onTick({ tick, features }) {
      if (tick.symbol.code !== params.symbol) return null;

      const spreadBps = features.spreadBps ?? Number.POSITIVE_INFINITY;
      const imbalance = features.orderBookImbalance ?? 0;

      if (
        !state.inPosition &&
        features.shortMomentumPct >= entryMomentumPct &&
        imbalance >= minImbalance &&
        spreadBps <= maxSpreadBps &&
        features.lastPrice >= features.microVwap &&
        isBullishBar()
      ) {
        state.inPosition = true;
        state.entryPrice = features.lastPrice;
        return buySignal(
          "tick-momentum-1m-confirm",
          params.symbol,
          features.lastPrice,
          quantity,
          features.timestamp,
          `Tick+1m确认买入: 短动量 ${features.shortMomentumPct.toFixed(3)}%, ` +
            `盘口失衡 ${imbalance.toFixed(2)}, 1m阳线确认`,
        );
      }

      if (!state.inPosition || state.entryPrice <= 0) return null;

      const pnlPct = ((features.lastPrice - state.entryPrice) / state.entryPrice) * 100;
      const shouldExit =
        pnlPct >= takeProfitPct ||
        pnlPct <= -stopLossPct ||
        features.shortMomentumPct <= exitMomentumPct;

      if (!shouldExit) return null;

      state.inPosition = false;
      const reason =
        pnlPct >= takeProfitPct
          ? `Tick+1m止盈: ${pnlPct.toFixed(3)}%`
          : pnlPct <= -stopLossPct
            ? `Tick+1m止损: ${pnlPct.toFixed(3)}%`
            : `Tick动量衰减退出: ${features.shortMomentumPct.toFixed(3)}%`;
      state.entryPrice = 0;
      return sellSignal(
        "tick-momentum-1m-confirm",
        params.symbol,
        features.lastPrice,
        quantity,
        features.timestamp,
        reason,
      );
    },

    reset() {
      state.inPosition = false;
      state.entryPrice = 0;
    },
  };
}
