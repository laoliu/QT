import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { directPost } from "../bridge/windows-bridge-client.js";
import type { QuantTradingConfig } from "../types.js";
import { TickIngressService } from "./tick-ingress-service.js";

vi.mock("../bridge/windows-bridge-client.js", () => ({
  WindowsBridgeEventPoller: class {
    start(): void {}
    stop(): void {}
  },
  directPost: vi.fn(async () => ({ status: "ok" })),
}));

const mockedDirectPost = vi.mocked(directPost);

function createReceiverConfig(): QuantTradingConfig {
  return {
    tickData: {
      enabled: true,
      source: "receiver-proxy",
      pollIntervalMs: 20,
      aggregateIntervals: ["1m"],
    },
    receiver: {
      enabled: true,
      port: 58090,
    },
  };
}

describe("Tick ingress receiver-proxy integration", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedDirectPost.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("consumes quote events, advances afterSeq, and syncs subscriptions", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            {
              seq: 10,
              event: "quote",
              data: {
                stock_code: "600519.SH",
                last_price: 100,
                last_close: 99,
                volume: 1000,
                amount: 100000,
                time: 1_000,
              },
            },
          ],
          latestSeq: 10,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 10 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const service = new TickIngressService(createReceiverConfig());
    const seenSymbols: string[] = [];
    service.onTick((tick) => seenSymbols.push(tick.symbol.code));

    await service.start();
    await Promise.resolve();
    await Promise.resolve();

    expect(seenSymbols).toEqual(["600519"]);

    await vi.advanceTimersByTimeAsync(25);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("afterSeq=10");

    await service.syncSubscriptions(["600519.SH", "000001"]);
    await service.syncSubscriptions(["600519", "300750.SZ"]);

    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["600519.SH", "000001.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["300750.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["000001.SZ"] },
      },
      10_000,
    );

    await service.stop();
  });

  it("ignores non-quote events from receiver stream", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        events: [
          {
            seq: 1,
            event: "order",
            data: { stock_code: "600519.SH", last_price: 100, time: 1_000 },
          },
        ],
        latestSeq: 1,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const service = new TickIngressService(createReceiverConfig());
    const ticks: string[] = [];
    service.onTick((tick) => ticks.push(tick.symbol.code));

    await service.start();
    await Promise.resolve();
    await Promise.resolve();

    expect(ticks).toEqual([]);
    expect(service.getStatus().lastEventAt).toBeNull();

    await service.stop();
  });

  it("retries and converges subscriptions after transient subscribe failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latestSeq: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    mockedDirectPost
      .mockRejectedValueOnce(new Error("temporary subscribe failure"))
      .mockResolvedValue({ status: "ok" });

    const service = new TickIngressService(createReceiverConfig());

    await service.start();
    await Promise.resolve();

    await expect(service.syncSubscriptions(["600519.SH", "000001"])).rejects.toThrow(
      "temporary subscribe failure",
    );
    expect(service.getStatus().subscribedSymbols).toEqual([]);

    await service.syncSubscriptions(["600519.SH", "000001"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);

    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["600519.SH", "000001.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["600519.SH", "000001.SZ"] },
      },
      10_000,
    );

    await service.stop();
  });

  it("keeps status stable across repeated empty polls", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            {
              seq: 9,
              event: "quote",
              data: {
                stock_code: "600519.SH",
                last_price: 100,
                last_close: 99,
                volume: 1000,
                amount: 100000,
                time: 1_000,
              },
            },
          ],
          latestSeq: 9,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 9 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const service = new TickIngressService(createReceiverConfig());
    const ticks: string[] = [];
    service.onTick((tick) => ticks.push(tick.symbol.code));

    await service.start();
    await Promise.resolve();
    await Promise.resolve();

    expect(ticks).toEqual(["600519"]);
    const firstStatus = service.getStatus();
    expect(firstStatus.lastEventAt).not.toBeNull();

    await vi.advanceTimersByTimeAsync(80);

    const steadyStatus = service.getStatus();
    expect(steadyStatus.lastEventAt).toBe(firstStatus.lastEventAt);
    expect(steadyStatus.subscribedSymbols).toEqual([]);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("afterSeq=9");
    expect(String(fetchMock.mock.calls[2]?.[0])).toContain("afterSeq=9");

    await service.stop();
  });

  it("retries and converges after transient unsubscribe failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latestSeq: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    mockedDirectPost
      .mockResolvedValueOnce({ status: "ok" })
      .mockRejectedValueOnce(new Error("temporary unsubscribe failure"))
      .mockResolvedValue({ status: "ok" });

    const service = new TickIngressService(createReceiverConfig());

    await service.start();
    await Promise.resolve();

    await service.syncSubscriptions(["600519.SH", "000001"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);

    await expect(service.syncSubscriptions(["600519.SH"])).rejects.toThrow(
      "temporary unsubscribe failure",
    );
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);

    await service.syncSubscriptions(["600519.SH"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["600519"]);

    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["600519.SH", "000001.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["000001.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["000001.SZ"] },
      },
      10_000,
    );

    await service.stop();
  });

  it("converges after mixed sync where subscribe succeeds but unsubscribe fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latestSeq: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    mockedDirectPost
      .mockResolvedValueOnce({ status: "ok" })
      .mockResolvedValueOnce({ status: "ok" })
      .mockRejectedValueOnce(new Error("temporary unsubscribe failure"))
      .mockResolvedValue({ status: "ok" });

    const service = new TickIngressService(createReceiverConfig());

    await service.start();
    await Promise.resolve();

    await service.syncSubscriptions(["600519.SH", "000001.SZ"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);

    await expect(service.syncSubscriptions(["300750.SZ", "600519.SH"])).rejects.toThrow(
      "temporary unsubscribe failure",
    );
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "300750", "600519"]);

    await service.syncSubscriptions(["300750.SZ", "600519.SH"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["300750", "600519"]);

    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["300750.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["000001.SZ"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      4,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["000001.SZ"] },
      },
      10_000,
    );

    await service.stop();
  });

  it("stops polling after service stop", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latestSeq: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const service = new TickIngressService(createReceiverConfig());

    await service.start();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await service.stop();
    await vi.advanceTimersByTimeAsync(100);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(service.getStatus().running).toBe(false);
  });

  it("does not resend requests when sync target is unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ events: [], latestSeq: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const service = new TickIngressService(createReceiverConfig());

    await service.start();
    await Promise.resolve();

    await service.syncSubscriptions(["600519.SH", "000001"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);
    expect(mockedDirectPost).toHaveBeenCalledTimes(1);

    await service.syncSubscriptions(["600519", "000001.SZ"]);
    expect(service.getStatus().subscribedSymbols).toEqual(["000001", "600519"]);
    expect(mockedDirectPost).toHaveBeenCalledTimes(1);

    await service.stop();
  });
});
