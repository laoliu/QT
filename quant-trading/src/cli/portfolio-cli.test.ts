import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { runCliPortfolio } from "./portfolio-cli.js";

const stubConfig: QuantTradingConfig = { dataProvider: "demo" };

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response;
}

describe("portfolio-cli", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ status: "ok", data: {} }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("show action", () => {
    it("shows portfolio with positions", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          data: {
            cash: 50000,
            totalValue: 150000,
            positions: [
              {
                symbol: "600519",
                quantity: 100,
                avgCost: 1700,
                currentPrice: 1800,
                marketValue: 180000,
                unrealizedPnl: 10000,
              },
            ],
          },
        }),
      );

      await runCliPortfolio("show", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("50000.00"))).toBe(
        true,
      );
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("600519"))).toBe(true);
    });

    it("shows empty portfolio", async () => {
      fetchSpy.mockResolvedValueOnce(
        jsonResponse({
          data: { cash: 100000, totalValue: 100000, positions: [] },
        }),
      );

      await runCliPortfolio("show", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("无持仓"))).toBe(true);
    });

    it("shows error on API failure", async () => {
      fetchSpy.mockRejectedValueOnce(new Error("fetch failed: ECONNREFUSED"));

      await runCliPortfolio("show", {}, stubConfig);

      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("无法连接到网关"));
    });
  });

  describe("add action", () => {
    it("requires --symbol", async () => {
      await runCliPortfolio("add", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Usage:"));
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("sends buy order with defaults", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ data: { orderId: "ord-123" } }));

      await runCliPortfolio("add", { symbol: "600519" }, stubConfig);

      const [, opts] = fetchSpy.mock.calls[0]!;
      const body = JSON.parse(opts.body);
      expect(body.direction).toBe("buy");
      expect(body.quantity).toBe(100);
      expect(body.orderType).toBe("market");
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("ord-123"));
    });

    it("sends limit order with price", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ data: { orderId: "ord-456" } }));

      await runCliPortfolio("add", { symbol: "600519", quantity: 200, price: 1800 }, stubConfig);

      const [, opts] = fetchSpy.mock.calls[0]!;
      const body = JSON.parse(opts.body);
      expect(body.orderType).toBe("limit");
      expect(body.price).toBe(1800);
      expect(body.quantity).toBe(200);
    });

    it("handles order failure", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ message: "insufficient funds" }, 400));

      await runCliPortfolio("add", { symbol: "600519" }, stubConfig);

      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("下单失败"));
    });
  });

  describe("remove action", () => {
    it("requires --symbol", async () => {
      await runCliPortfolio("remove", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Usage:"));
    });

    it("sends sell order", async () => {
      fetchSpy.mockResolvedValueOnce(jsonResponse({ data: { orderId: "ord-789" } }));

      await runCliPortfolio("remove", { symbol: "600519", quantity: 50 }, stubConfig);

      const [, opts] = fetchSpy.mock.calls[0]!;
      const body = JSON.parse(opts.body);
      expect(body.direction).toBe("sell");
      expect(body.quantity).toBe(50);
    });
  });

  describe("rebalance action", () => {
    it("shows agent suggestion", async () => {
      await runCliPortfolio("rebalance", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls.some((c: unknown) => typeof c === "string" && c.includes("AI Agent"))).toBe(
        true,
      );
    });
  });

  describe("unknown action", () => {
    it("shows usage", async () => {
      await runCliPortfolio("xyz", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Unknown action"));
    });
  });

  describe("custom port", () => {
    it("uses --port option", async () => {
      await runCliPortfolio("show", { port: 9999 }, stubConfig);

      const [url] = fetchSpy.mock.calls[0]!;
      expect(url).toContain("http://127.0.0.1:9999");
    });
  });
});
