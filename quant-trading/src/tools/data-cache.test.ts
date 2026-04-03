/**
 * Unit tests for the data_cache AI tool.
 *
 * Mocks runPythonScript to verify parameter forwarding and error handling.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";

// ── Mock python bridge ──────────────────────────────────────────
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataCacheTool } from "./data-cache.js";

const mockRun = vi.mocked(runPythonScript);
const fakeApi = {} as never;

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-quant-test",
  pythonPath: "python3",
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("data_cache tool", () => {
  it("has correct name and required parameters", () => {
    const tool = createDataCacheTool(fakeApi, config);
    expect(tool.name).toBe("data_cache");
    expect(tool.parameters.required).toContain("action");
  });

  describe("action=stats", () => {
    it("returns cache statistics from python", async () => {
      const stats = {
        db_path: "/tmp/cache.db",
        db_size_mb: 12.5,
        stock_list: { unique_stocks: 100, cached_dates: 5 },
        fundamentals: { unique_stocks: 80 },
        klines: { total_bars: 50000, unique_symbols: 200 },
        code_name_map: { entries: 4000 },
      };
      mockRun.mockResolvedValue({ ok: true, data: stats });

      const tool = createDataCacheTool(fakeApi, config);
      const result = await tool.execute({ action: "stats" });
      expect(result).toEqual(stats);
      expect(mockRun).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        expect.objectContaining({ action: "stats" }),
        config,
      );
    });
  });

  describe("action=clear", () => {
    it("passes table parameter when specified", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { status: "cleared", cleared: "kline_data" } });

      const tool = createDataCacheTool(fakeApi, config);
      const result = await tool.execute({ action: "clear", table: "kline_data" });
      expect(result).toHaveProperty("status", "cleared");
      expect(mockRun).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        expect.objectContaining({ action: "clear", table: "kline_data" }),
        config,
      );
    });

    it("clears all tables when table not specified", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { status: "cleared", cleared: "all" } });

      const tool = createDataCacheTool(fakeApi, config);
      await tool.execute({ action: "clear" });
      expect(mockRun).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        expect.objectContaining({ action: "clear", table: undefined }),
        config,
      );
    });
  });

  describe("action=cleanup", () => {
    it("uses default 30 days when days not specified", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { status: "cleaned" } });

      const tool = createDataCacheTool(fakeApi, config);
      await tool.execute({ action: "cleanup" });
      expect(mockRun).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        expect.objectContaining({ action: "cleanup", days: 30 }),
        config,
      );
    });

    it("passes custom days value", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { status: "cleaned" } });

      const tool = createDataCacheTool(fakeApi, config);
      await tool.execute({ action: "cleanup", days: 7 });
      expect(mockRun).toHaveBeenCalledWith(
        "cache/stock_cache.py",
        expect.objectContaining({ days: 7 }),
        config,
      );
    });
  });

  describe("error handling", () => {
    it("returns error message when python call fails", async () => {
      mockRun.mockResolvedValue({ ok: false, error: "SQLite locked" });

      const tool = createDataCacheTool(fakeApi, config);
      const result = (await tool.execute({ action: "stats" })) as { error: string };
      expect(result.error).toContain("缓存操作失败");
      expect(result.error).toContain("SQLite locked");
    });

    it("returns generic error when no error message", async () => {
      mockRun.mockResolvedValue({ ok: false, data: null });

      const tool = createDataCacheTool(fakeApi, config);
      const result = (await tool.execute({ action: "stats" })) as { error: string };
      expect(result.error).toBe("缓存操作失败");
    });
  });
});
