import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { runCliLive } from "./live-cli.js";

const stubConfig: QuantTradingConfig = { dataProvider: "demo" };

// Helpers to build mock fetch responses
function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

describe("live-cli", () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let logSpy: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let errorSpy: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let exitSpy: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchSpy: any;

  beforeEach(() => {
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    exitSpy = vi.spyOn(process, "exit").mockImplementation(() => undefined as never);
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ status: "ok", data: {} }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("start action", () => {
    it("requires --strategy flag", async () => {
      await runCliLive("start", {}, stubConfig);
      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("--strategy"));
      expect(exitSpy).toHaveBeenCalledWith(1);
    });

    it("sends engine start then add strategy", async () => {
      fetchSpy
        .mockResolvedValueOnce(jsonResponse({ status: "ok" }))
        .mockResolvedValueOnce(jsonResponse({ status: "ok" }));

      await runCliLive(
        "start",
        { strategy: "dual-ma-crossover", symbols: "600519,000001" },
        stubConfig,
      );

      expect(fetchSpy).toHaveBeenCalledTimes(2);
      // First call: engine start
      const [url1, opts1] = fetchSpy.mock.calls[0]!;
      expect(url1).toContain("/quant/api/engine/start");
      expect(opts1.method).toBe("POST");
      // Second call: add strategy
      const [url2, opts2] = fetchSpy.mock.calls[1]!;
      expect(url2).toContain("/quant/api/strategies");
      expect(JSON.parse(opts2.body)).toMatchObject({
        strategyId: "dual-ma-crossover",
        symbols: ["600519", "000001"],
      });
    });

    it("exits on engine start failure", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ message: "error" }, 500));

      await runCliLive("start", { strategy: "test" }, stubConfig);

      expect(exitSpy).toHaveBeenCalledWith(1);
    });
  });

  describe("stop action", () => {
    it("sends stop request", async () => {
      await runCliLive("stop", {}, stubConfig);
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/quant/api/engine/stop"),
        expect.objectContaining({ method: "POST" }),
      );
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("已停止"));
    });
  });

  describe("pause action", () => {
    it("sends pause request", async () => {
      await runCliLive("pause", {}, stubConfig);
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/quant/api/engine/pause"),
        expect.objectContaining({ method: "POST" }),
      );
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("暂停"));
    });
  });

  describe("resume action", () => {
    it("sends resume request", async () => {
      await runCliLive("resume", {}, stubConfig);
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/quant/api/engine/resume"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  describe("status action", () => {
    it("shows engine status", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          status: "ok",
          data: {
            status: "running",
            mode: "paper",
            broker: "paper",
            activeStrategies: 2,
            totalStrategies: 3,
            positionCount: 5,
            account: { totalAssets: 100000, cash: 50000, profit: 5000, profitPct: "5%" },
          },
        }),
      );

      await runCliLive("status", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls).toContainEqual(expect.stringContaining("running"));
      expect(calls).toContainEqual(expect.stringContaining("paper"));
    });

    it("shows not running when no data", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ status: "ok", data: null }));

      await runCliLive("status", {}, stubConfig);

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("未运行"));
    });
  });

  describe("orders action", () => {
    it("shows orders list", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          status: "ok",
          data: [
            {
              orderId: "ord-abc-123456",
              symbol: "600519",
              side: "buy",
              quantity: 100,
              filledQty: 50,
              status: "partial",
            },
          ],
        }),
      );

      await runCliLive("orders", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("600519"))).toBe(true);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("买"))).toBe(true);
    });

    it("shows no orders message when empty", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ status: "ok", data: [] }));

      await runCliLive("orders", {}, stubConfig);

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("暂无订单"));
    });

    it("passes status filter as query param", async () => {
      await runCliLive("orders", { status: "filled" }, stubConfig);

      const [url] = fetchSpy.mock.calls[0]!;
      expect(url).toContain("?status=filled");
    });
  });

  describe("pnl action", () => {
    it("shows pnl data", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          status: "ok",
          data: { date: "2025-01-01", netPnl: 1234.56 },
        }),
      );

      await runCliLive("pnl", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("1234.56"))).toBe(true);
    });

    it("shows no data message when null", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ status: "ok", data: null }));

      await runCliLive("pnl", {}, stubConfig);

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("暂无盈亏"));
    });
  });

  describe("strategies action", () => {
    it("shows active strategies", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          status: "ok",
          data: [{ id: "s1", strategyId: "dual-ma-crossover", enabled: true, symbols: ["600519"] }],
        }),
      );

      await runCliLive("strategies", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(
        calls.some((c: unknown) => typeof c === "string" && c.includes("dual-ma-crossover")),
      ).toBe(true);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("运行"))).toBe(true);
    });

    it("shows no strategies message when empty", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ status: "ok", data: [] }));

      await runCliLive("strategies", {}, stubConfig);

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("暂无活跃策略"));
    });
  });

  describe("connection error", () => {
    it("shows gateway not-running message on ECONNREFUSED", async () => {
      fetchSpy.mockRejectedValue(new Error("fetch failed: ECONNREFUSED"));

      await runCliLive("status", {}, stubConfig);

      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("无法连接到网关"));
      expect(exitSpy).toHaveBeenCalledWith(1);
    });
  });

  describe("custom port", () => {
    it("uses --port option", async () => {
      await runCliLive("status", { port: 9999 }, stubConfig);

      const [url] = fetchSpy.mock.calls[0]!;
      expect(url).toContain("http://127.0.0.1:9999");
    });
  });

  describe("default action", () => {
    it("shows usage for unknown action", async () => {
      await runCliLive("xyz", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Unknown live action"));
    });
  });
});
