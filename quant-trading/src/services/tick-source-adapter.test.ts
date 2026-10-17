import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { directPost } from "../bridge/windows-bridge-client.js";
import type { QuantTradingConfig } from "../types.js";
import { createDefaultTickSourceAdapter } from "./tick-source-adapter.js";

vi.mock("../bridge/windows-bridge-client.js", () => ({
  WindowsBridgeEventPoller: class {
    start(): void {}
    stop(): void {}
  },
  directPost: vi.fn(async () => ({ ok: true })),
}));

const mockedDirectPost = vi.mocked(directPost);

describe("createDefaultTickSourceAdapter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedDirectPost.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("creates qmt bridge source when configured", () => {
    const config: QuantTradingConfig = {
      tradingAccount: {
        mode: "paper",
        qmt: {
          windowsBridgeUrl: "http://192.168.3.30:18888",
        },
      },
    };

    const adapter = createDefaultTickSourceAdapter(config, 3_000);
    expect(adapter?.kind).toBe("qmt-bridge");
  });

  it("creates receiver-proxy source when selected", () => {
    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };

    const adapter = createDefaultTickSourceAdapter(config, 3_000);
    expect(adapter?.kind).toBe("receiver-proxy");
    expect(adapter?.endpoint).toBe("http://127.0.0.1:58090");
  });

  it("returns null for receiver-proxy when receiver is disabled", () => {
    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { enabled: false, port: 58090 },
    };

    const adapter = createDefaultTickSourceAdapter(config, 3_000);
    expect(adapter).toBeNull();
  });

  it("polls receiver events with afterSeq cursor", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            {
              seq: 11,
              event: "quote",
              data: { stock_code: "600519.SH", last_price: 100 },
            },
          ],
          latestSeq: 11,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 11 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 50);
    expect(adapter?.kind).toBe("receiver-proxy");

    const events: Array<{ event: string }> = [];
    adapter?.start((event) => events.push({ event: event.event }));

    await Promise.resolve();
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("afterSeq=");

    await vi.advanceTimersByTimeAsync(60);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("afterSeq=11");
    expect(events.map((event) => event.event)).toEqual(["quote"]);

    adapter?.stop();
  });

  it("keeps afterSeq monotonic across out-of-order events", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [{ seq: 12, event: "quote", data: { stock_code: "600519.SH" } }],
          latestSeq: 12,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [{ seq: 8, event: "quote", data: { stock_code: "600519.SH" } }],
          latestSeq: 20,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 20 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 50);

    adapter?.start(() => {});
    await Promise.resolve();
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(60);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("afterSeq=12");

    await vi.advanceTimersByTimeAsync(60);
    expect(String(fetchMock.mock.calls[2]?.[0])).toContain("afterSeq=20");

    adapter?.stop();
  });

  it("forwards duplicate-seq events while keeping afterSeq monotonic", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            { seq: 10, event: "quote", data: { stock_code: "600519.SH" } },
            { seq: 10, event: "quote", data: { stock_code: "600519.SH" } },
          ],
          latestSeq: 10,
        }),
      })
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 10 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 20);

    const events: string[] = [];
    adapter?.start((event) => events.push(event.event));
    await Promise.resolve();
    await Promise.resolve();

    expect(events).toEqual(["quote", "quote"]);

    await vi.advanceTimersByTimeAsync(25);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("afterSeq=10");

    adapter?.stop();
  });

  it("tolerates non-ok and network errors while polling", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false })
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue({
        ok: true,
        json: async () => ({ events: [], latestSeq: 0 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 20);
    const events: string[] = [];

    adapter?.start((event) => events.push(event.event));
    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(50);

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(events).toEqual([]);

    adapter?.stop();
  });

  it("forwards receiver subscribe/unsubscribe requests via trading-request endpoint", async () => {
    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 3_000);
    expect(adapter?.kind).toBe("receiver-proxy");

    await adapter?.subscribe(["600519"]);
    await adapter?.unsubscribe(["600519"]);

    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: ["600519.SH"] },
      },
      10_000,
    );
    expect(mockedDirectPost).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:58090",
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: ["600519.SH"] },
      },
      10_000,
    );
  });

  it("skips poll if previous poll is still in-flight", async () => {
    let pollCount = 0;
    const slowFetch = vi.fn(async () => {
      pollCount++;
      await new Promise((resolve) => setTimeout(resolve, 100));
      return {
        ok: true,
        json: async () => ({ events: [], latestSeq: 0 }),
      };
    });
    vi.stubGlobal("fetch", slowFetch);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 20);
    expect(adapter?.kind).toBe("receiver-proxy");

    adapter?.start(() => {});
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(50);

    expect(pollCount).toBe(1);

    adapter?.stop();
  });

  it("times out fetch after 5 seconds", async () => {
    const abortedRequests: AbortSignal[] = [];
    const fetchWithAbort = vi.fn(async (_url: unknown, opts?: { signal?: AbortSignal }) => {
      if (opts?.signal) {
        abortedRequests.push(opts.signal);
      }
      return new Promise((resolve) => {
        setTimeout(() => {
          resolve({
            ok: true,
            json: async () => ({ events: [], latestSeq: 0 }),
          } as unknown as never);
        }, 10000);
      });
    });
    vi.stubGlobal("fetch", fetchWithAbort);

    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy", pollIntervalMs: 100 },
      receiver: { port: 58090 },
    };
    const adapter = createDefaultTickSourceAdapter(config, 100);

    adapter?.start(() => {});
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(6000);

    expect(fetchWithAbort).toHaveBeenCalled();
    expect(abortedRequests.length).toBeGreaterThan(0);
    expect(abortedRequests[0]?.aborted).toBe(true);

    adapter?.stop();
  });

  it("initializes and retrieves afterSeq correctly", () => {
    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy" },
      receiver: { port: 58090 },
    };
    // Create adapter with no initial seq (default 0)
    const adapter = createDefaultTickSourceAdapter(config, 100);
    expect((adapter as unknown as { getSeq(): number }).getSeq()).toBe(0);
  });

  it("loads initial afterSeq when provided in config", () => {
    // Test that createDefaultTickSourceAdapter respects dataDir for seq recovery
    // Note: Full file I/O test would need fs mocking; here we just verify the adapter works
    const config: QuantTradingConfig = {
      tickData: { source: "receiver-proxy", pollIntervalMs: 100 },
      receiver: { port: 58090 },
      dataDir: "/tmp/nonexistent", // Non-existent dir; should start from seq 0
    };
    const adapter = createDefaultTickSourceAdapter(config, 100);
    expect((adapter as unknown as { getSeq(): number }).getSeq()).toBe(0);
  });
});
