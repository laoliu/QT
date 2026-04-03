import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock dependencies
vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: vi.fn(),
}));
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";
import { runCliData } from "./data-cli.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- vi.fn mock interop
const mockCreateDataProvider = createDataProvider as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- vi.fn mock interop
const mockRunPythonScript = runPythonScript as any;

const stubConfig: QuantTradingConfig = { dataProvider: "demo" };

describe("data-cli", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockCreateDataProvider.mockReturnValue({
      name: "demo",
      getQuote: vi.fn(),
      getKlines: vi.fn(),
    });
    mockRunPythonScript.mockResolvedValue({ ok: false, error: "Python not found" });
  });

  describe("fetch action", () => {
    it("shows usage when no symbol specified", async () => {
      await runCliData("fetch", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Usage:"));
    });

    it("displays quote data for a symbol", async () => {
      const provider = mockCreateDataProvider();
      provider.getQuote.mockResolvedValue({
        symbol: { code: "600519", name: "贵州茅台" },
        price: 1800.5,
        changePct: 2.35,
      });

      await runCliData("fetch", { symbol: "600519" }, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls).toContainEqual(expect.stringContaining("600519"));
      expect(calls).toContainEqual(expect.stringContaining("1800.50"));
      expect(calls).toContainEqual(expect.stringContaining("+2.35%"));
    });

    it("handles negative change", async () => {
      const provider = mockCreateDataProvider();
      provider.getQuote.mockResolvedValue({
        symbol: { code: "000001", name: "平安银行" },
        price: 12.0,
        changePct: -1.5,
      });

      await runCliData("fetch", { symbol: "000001" }, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls).toContainEqual(expect.stringContaining("-1.50%"));
    });

    it("shows no data message when quote is null", async () => {
      const provider = mockCreateDataProvider();
      provider.getQuote.mockResolvedValue(null);

      await runCliData("fetch", { symbol: "999999" }, stubConfig);

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("No data found"));
    });
  });

  describe("list action", () => {
    it("shows active provider name", async () => {
      await runCliData("list", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls).toContainEqual(expect.stringContaining("demo"));
      expect(calls).toContainEqual(expect.stringContaining("Available:"));
    });
  });

  describe("clean action", () => {
    it("shows success when Python cache script works", async () => {
      mockRunPythonScript.mockResolvedValue({
        ok: true,
        data: { status: "Cleared 3 tables", cleared: "klines, quotes, info" },
      });

      await runCliData("clean", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      expect(calls).toContainEqual(expect.stringContaining("Cleared 3 tables"));
    });

    it("passes table option to Python script", async () => {
      mockRunPythonScript.mockResolvedValue({
        ok: true,
        data: { status: "Done" },
      });

      await runCliData("clean", { table: "klines" }, stubConfig);

      expect(mockRunPythonScript).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        { action: "clear", table: "klines" },
        stubConfig,
      );
    });

    it("shows error from Python cache script", async () => {
      mockRunPythonScript.mockResolvedValue({
        ok: true,
        data: { error: "permission denied" },
      });

      await runCliData("clean", {}, stubConfig);

      expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("permission denied"));
    });

    it("falls back to directory deletion when Python fails", async () => {
      mockRunPythonScript.mockResolvedValue({ ok: false, error: "not found" });

      // The fallback will try fs.rm, which won't find the directory.
      // Just verify it doesn't throw.
      await runCliData("clean", {}, stubConfig);

      const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
      // Should see either "已清理" or "不存在"
      expect(
        calls.some(
          (c: unknown) => typeof c === "string" && (c.includes("已清理") || c.includes("不存在")),
        ),
      ).toBe(true);
    });
  });

  describe("unknown action", () => {
    it("shows usage for unknown action", async () => {
      await runCliData("bogus", {}, stubConfig);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Unknown action: bogus"));
    });
  });
});
