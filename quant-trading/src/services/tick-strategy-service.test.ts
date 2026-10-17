import type { QuantTradingRuntime } from "../runtime.js";
import { describe, expect, it } from "vitest";
import type { KlineBar, TickAggregationInterval, TickData, TickFeatures } from "../types.js";
import type { EventBus, EventCategory, QuantEvent } from "./event-bus.js";
import type { TickIngressService } from "./tick-ingress-service.js";
import { TickStrategyService } from "./tick-strategy-service.js";

type FeatureHandler = (features: TickFeatures) => void;
type BarHandler = (symbol: string, bar: KlineBar, interval: TickAggregationInterval) => void;

class FakeTickIngress implements Pick<
  TickIngressService,
  "onTickFeatures" | "onBar" | "getLatestTick"
> {
  private featureHandlers = new Set<FeatureHandler>();
  private barHandlers = new Set<BarHandler>();
  private latestTickBySymbol = new Map<string, TickData>();

  onTickFeatures(handler: FeatureHandler): () => void {
    this.featureHandlers.add(handler);
    return () => this.featureHandlers.delete(handler);
  }

  onBar(handler: BarHandler): () => void {
    this.barHandlers.add(handler);
    return () => this.barHandlers.delete(handler);
  }

  getLatestTick(symbol: string): TickData | null {
    return this.latestTickBySymbol.get(symbol) ?? null;
  }

  setLatestTick(tick: TickData): void {
    this.latestTickBySymbol.set(tick.symbol.code, tick);
  }

  emitFeatures(features: TickFeatures): void {
    for (const handler of this.featureHandlers) handler(features);
  }

  emitBar(symbol: string, bar: KlineBar, interval: TickAggregationInterval): void {
    for (const handler of this.barHandlers) handler(symbol, bar, interval);
  }
}

class FakeEventBus implements Pick<EventBus, "emit"> {
  readonly events: Array<{ category: string; payload: unknown }> = [];

  emit(category: EventCategory, payload: unknown): QuantEvent {
    this.events.push({ category, payload });
    return { id: 0, category, timestamp: new Date().toISOString(), data: payload };
  }
}

function createApiStub(): QuantTradingRuntime {
  return {
    logger: {
      info: () => {},
      warn: () => {},
      error: () => {},
      debug: () => {},
    },
  } as unknown as QuantTradingRuntime;
}

function makeTick(symbol = "600519", price = 100, timestamp = 1_000): TickData {
  return {
    symbol: { code: symbol, name: symbol, market: "cn_stock" },
    timestamp,
    price,
    volume: 1_000,
    amount: price * 1_000,
    source: "qmt-bridge",
  };
}

function makeFeatures(symbol = "600519", overrides: Partial<TickFeatures> = {}): TickFeatures {
  return {
    symbol,
    timestamp: 1_000,
    lastPrice: 100,
    microVwap: 100,
    shortMomentumPct: 0.1,
    mediumMomentumPct: 0.15,
    spreadBps: 8,
    orderBookImbalance: 0.2,
    realizedVolBps: 4,
    volumeDelta: 100,
    amountDelta: 10_000,
    ...overrides,
  };
}

describe("TickStrategyService", () => {
  it("emits signal events for configured strategies", async () => {
    const ingress = new FakeTickIngress();
    const eventBus = new FakeEventBus();
    const config = {
      tickData: {
        strategies: [
          {
            id: "tick-vwap-reversion" as const,
            enabled: true,
            symbols: ["600519"],
            submitOrders: false,
          },
        ],
      },
    };

    const service = new TickStrategyService(
      config,
      createApiStub(),
      ingress as unknown as TickIngressService,
      eventBus as unknown as EventBus,
      () => null,
    );

    await service.start();
    ingress.setLatestTick(makeTick("600519", 99.7, 1_000));
    ingress.emitFeatures(
      makeFeatures("600519", {
        timestamp: 1_000,
        lastPrice: 99.7,
        microVwap: 100,
        shortMomentumPct: -0.01,
        mediumMomentumPct: -0.02,
        orderBookImbalance: 0,
      }),
    );

    expect(eventBus.events.some((event) => event.category === "signal")).toBe(true);
  });

  it("routes completed 1m bars into 1m-confirm strategy state", async () => {
    const ingress = new FakeTickIngress();
    const eventBus = new FakeEventBus();
    const config = {
      tickData: {
        strategies: [
          {
            id: "tick-momentum-1m-confirm" as const,
            enabled: true,
            symbols: ["600519"],
            submitOrders: false,
          },
        ],
      },
    };

    const service = new TickStrategyService(
      config,
      createApiStub(),
      ingress as unknown as TickIngressService,
      eventBus as unknown as EventBus,
      () => null,
    );

    await service.start();
    ingress.setLatestTick(makeTick("600519", 101, 1_000));

    const entryFeatures = makeFeatures("600519", {
      timestamp: 1_000,
      lastPrice: 101,
      microVwap: 100.5,
      shortMomentumPct: 0.1,
      mediumMomentumPct: 0.16,
      orderBookImbalance: 0.2,
      spreadBps: 8,
    });

    ingress.emitFeatures(entryFeatures);
    const beforeBarSignals = eventBus.events.filter((event) => event.category === "signal").length;

    ingress.emitBar(
      "600519",
      {
        timestamp: 1_000,
        open: 100,
        high: 101.5,
        low: 99.9,
        close: 101.2,
        volume: 2_000,
        amount: 202_400,
      },
      "1m",
    );
    ingress.emitFeatures({ ...entryFeatures, timestamp: 2_000 });

    const afterBarSignals = eventBus.events.filter((event) => event.category === "signal").length;
    expect(afterBarSignals).toBeGreaterThan(beforeBarSignals);
  });

  it("suppresses duplicate signals within dedup window (100ms)", async () => {
    const ingress = new FakeTickIngress();
    const eventBus = new FakeEventBus();
    const config = {
      tickData: {
        strategies: [
          {
            id: "tick-vwap-reversion" as const,
            enabled: true,
            symbols: ["600519"],
            submitOrders: false,
          },
        ],
      },
    };

    const service = new TickStrategyService(
      config,
      createApiStub(),
      ingress as unknown as TickIngressService,
      eventBus as unknown as EventBus,
      () => null,
    );

    await service.start();

    ingress.setLatestTick(makeTick("600519", 99.7, 1_000));
    const features = makeFeatures("600519", {
      timestamp: 1_000,
      lastPrice: 99.7,
      microVwap: 100,
      shortMomentumPct: -0.01,
      mediumMomentumPct: -0.02,
    });

    ingress.emitFeatures(features);
    const signalsAfterFirst = eventBus.events.filter((event) => event.category === "signal").length;
    expect(signalsAfterFirst).toBeGreaterThan(0);

    ingress.emitFeatures({
      ...features,
      timestamp: 1_050,
    });
    const signalsAfterSecond = eventBus.events.filter(
      (event) => event.category === "signal",
    ).length;
    expect(signalsAfterSecond).toBe(signalsAfterFirst);
  });
});
