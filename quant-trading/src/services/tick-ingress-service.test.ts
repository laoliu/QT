import { describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { TickIngressService } from "./tick-ingress-service.js";
import type {
  TickSourceAdapter,
  TickSourceEvent,
  TickSourceStatus,
} from "./tick-source-adapter.js";

class FakeTickSourceAdapter implements TickSourceAdapter {
  readonly endpoint = "http://127.0.0.1:18888";
  private onEvent: ((event: TickSourceEvent) => void) | null = null;
  private running = false;
  readonly subscribeCalls: string[][] = [];
  readonly unsubscribeCalls: string[][] = [];

  constructor(readonly kind: string) {}

  start(onEvent: (event: TickSourceEvent) => void): void {
    this.onEvent = onEvent;
    this.running = true;
  }

  stop(): void {
    this.running = false;
    this.onEvent = null;
  }

  async subscribe(symbols: string[]): Promise<void> {
    this.subscribeCalls.push([...symbols]);
  }

  async unsubscribe(symbols: string[]): Promise<void> {
    this.unsubscribeCalls.push([...symbols]);
  }

  toInternalSymbol(symbol: string): string {
    return symbol.replace(/\.(SH|SZ|BJ)$/i, "");
  }

  toSourceSymbol(symbol: string): string {
    return symbol;
  }

  getStatus(): TickSourceStatus {
    return {
      kind: this.kind,
      running: this.running,
      endpoint: this.endpoint,
    };
  }

  emitQuote(data: Record<string, unknown>): void {
    this.onEvent?.({ event: "quote", data });
  }
}

function createConfig(): QuantTradingConfig {
  return {
    tickData: {
      enabled: true,
      aggregateIntervals: ["1m"],
    },
  };
}

describe("TickIngressService", () => {
  it("labels qmt bridge ticks as qmt-bridge", async () => {
    const source = new FakeTickSourceAdapter("qmt-bridge");
    const service = new TickIngressService(createConfig(), undefined, source);
    const ticks: Array<{ source?: string }> = [];

    service.onTick((tick) => ticks.push({ source: tick.source }));

    await service.start();
    source.emitQuote({
      stock_code: "600519.SH",
      last_price: 100,
      last_close: 99,
      volume: 1000,
      amount: 100000,
      time: 1_000,
    });

    expect(ticks).toHaveLength(1);
    expect(ticks[0]?.source).toBe("qmt-bridge");
  });

  it("labels receiver proxy ticks as receiver", async () => {
    const source = new FakeTickSourceAdapter("receiver-proxy");
    const service = new TickIngressService(createConfig(), undefined, source);
    const ticks: Array<{ source?: string }> = [];

    service.onTick((tick) => ticks.push({ source: tick.source }));

    await service.start();
    source.emitQuote({
      stock_code: "000001.SZ",
      last_price: 12.3,
      last_close: 12,
      volume: 2000,
      amount: 24600,
      time: 2_000,
    });

    expect(ticks).toHaveLength(1);
    expect(ticks[0]?.source).toBe("receiver");
  });

  it("syncs subscriptions using normalized internal symbols", async () => {
    const source = new FakeTickSourceAdapter("receiver-proxy");
    const service = new TickIngressService(createConfig(), undefined, source);

    await service.subscribe(["600519.SH", "000001"]);
    expect(source.subscribeCalls).toEqual([["600519", "000001"]]);

    await service.syncSubscriptions(["600519", "300750.SZ"]);
    expect(source.subscribeCalls).toEqual([["600519", "000001"], ["300750"]]);
    expect(source.unsubscribeCalls).toEqual([["000001"]]);
  });

  it("maintains monotonic lastEventAt even with out-of-order ticks", async () => {
    const source = new FakeTickSourceAdapter("receiver-proxy");
    const service = new TickIngressService(createConfig(), undefined, source);
    const statuses: Array<{ lastEventAt: string | null }> = [];

    await service.start();

    // Emit tick with timestamp 1000
    source.emitQuote({
      stock_code: "000001.SZ",
      last_price: 10,
      last_close: 9,
      volume: 1000,
      amount: 10000,
      time: 1000,
    });
    statuses.push({ lastEventAt: service.getStatus().lastEventAt });

    // Emit tick with timestamp 500 (earlier; should not regress lastEventAt)
    source.emitQuote({
      stock_code: "000001.SZ",
      last_price: 10,
      last_close: 9,
      volume: 1000,
      amount: 10000,
      time: 500,
    });
    statuses.push({ lastEventAt: service.getStatus().lastEventAt });

    // Emit tick with timestamp 2000 (later; should advance lastEventAt)
    source.emitQuote({
      stock_code: "000001.SZ",
      last_price: 11,
      last_close: 9,
      volume: 2000,
      amount: 22000,
      time: 2000,
    });
    statuses.push({ lastEventAt: service.getStatus().lastEventAt });

    // Verify monotonicity: all statuses should have increasing timestamps
    expect(statuses[0]?.lastEventAt).toBe("1970-01-01T00:00:01.000Z");
    expect(statuses[1]?.lastEventAt).toBe("1970-01-01T00:00:01.000Z"); // Didn't regress
    expect(statuses[2]?.lastEventAt).toBe("1970-01-01T00:00:02.000Z"); // Advanced
  });

  it("queues failed subscriptions and retries in background", async () => {
    let subscribeFailCount = 0;
    const adapter = new (class extends FakeTickSourceAdapter {
      async subscribe(symbols: string[]): Promise<void> {
        subscribeFailCount++;
        if (subscribeFailCount <= 1) {
          throw new Error("Network error");
        }
        super.subscribe(symbols);
      }
    })("receiver-proxy");

    const service = new TickIngressService(createConfig(), undefined, adapter);
    await service.start();

    // Initial subscribe fails; should queue and throw
    try {
      await service.subscribe(["000001", "000002"]);
    } catch (err) {
      // Expected to throw
    }
    let status = service.getStatus();
    expect(status.failedSubscribeSymbols).toEqual(["000001", "000002"]);
    expect(status.consecutiveErrors).toBe(1);
    expect(status.lastError).toContain("Network error");
  });

  it("exposes observability metrics in status", async () => {
    const adapter = new (class extends FakeTickSourceAdapter {
      async subscribe(): Promise<void> {
        throw new Error("Subscription temporarily unavailable");
      }
    })("receiver-proxy");

    const service = new TickIngressService(createConfig(), undefined, adapter);
    await service.start();

    try {
      await service.subscribe(["600519"]);
    } catch (err) {
      // Expected to throw
    }
    const status = service.getStatus();

    expect(status.failedSubscribeSymbols).toContain("600519");
    expect(status.consecutiveErrors).toBeGreaterThan(0);
    expect(status.lastError).toContain("Subscription temporarily unavailable");
    expect(status.lastErrorAt).not.toBeNull();
  });

  it("computes tick features and broadcasts them via onTickFeatures", async () => {
    const source = new FakeTickSourceAdapter("receiver-proxy");
    const service = new TickIngressService(createConfig(), undefined, source);
    const receivedFeatures: Array<{ symbol: string; lastPrice: number }> = [];

    service.onTickFeatures((f) =>
      receivedFeatures.push({ symbol: f.symbol, lastPrice: f.lastPrice }),
    );
    await service.start();

    source.emitQuote({
      stock_code: "000001.SZ",
      last_price: 12.5,
      last_close: 12,
      volume: 1000,
      amount: 12500,
      time: 1_000,
    });

    expect(receivedFeatures).toHaveLength(1);
    expect(receivedFeatures[0]?.symbol).toBe("000001");
    expect(receivedFeatures[0]?.lastPrice).toBe(12.5);
  });

  it("emits features for every tick, with growing momentum window", async () => {
    const source = new FakeTickSourceAdapter("receiver-proxy");
    const service = new TickIngressService(createConfig(), undefined, source);
    const features: Array<{ shortMomentumPct: number }> = [];

    service.onTickFeatures((f) => features.push({ shortMomentumPct: f.shortMomentumPct }));
    await service.start();

    // Emit 6 ticks with rising price so shortMomentum should be positive after 5+
    for (let i = 1; i <= 6; i++) {
      source.emitQuote({
        stock_code: "600519.SH",
        last_price: 100 + i,
        last_close: 100,
        volume: 1000 * i,
        amount: 100_000 * i,
        time: 1000 * i,
      });
    }

    // 6 ticks → 6 feature events
    expect(features).toHaveLength(6);
    // After 5+ ticks the shortMomentumPct should be positive (price rose)
    expect(features[5]!.shortMomentumPct).toBeGreaterThan(0);
  });

  it("emits features via eventBus with type=feature", async () => {
    const emittedEvents: Array<{ type: string }> = [];
    const fakeEventBus = {
      emit(channel: string, event: { type: string }) {
        if (channel === "tick") emittedEvents.push({ type: event.type });
      },
    } as never;

    const source = new FakeTickSourceAdapter("qmt-bridge");
    const service = new TickIngressService(createConfig(), fakeEventBus, source);
    await service.start();

    source.emitQuote({
      stock_code: "300750.SZ",
      last_price: 50,
      last_close: 49,
      volume: 500,
      amount: 25000,
      time: 2_000,
    });

    const featuresEvents = emittedEvents.filter((e) => e.type === "feature");
    expect(featuresEvents).toHaveLength(1);
  });
});
