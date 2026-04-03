/**
 * Unit tests for stock_screen AI tool.
 *
 * Mocks the Python bridge so no real Python process is needed.
 * Covers all 5 action types: screen, rank, rps, run_screener, list_screeners.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { QuantTradingConfig } from "../types.js";

// Mock Python bridge
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createStockScreenTool } from "./stock-screen.js";

const fakeApi = {} as never;

function makeConfig(): QuantTradingConfig {
  return {
    dataDir: "/tmp/oc-quant-test",
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
  } as QuantTradingConfig;
}

describe("stock_screen tool", () => {
  beforeEach(() => {
    vi.mocked(runPythonScript).mockReset();
  });

  describe("action=list_screeners", () => {
    it("returns screener list from Python engine", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: {
          screeners: [
            { id: "fscore", name: "Piotroski F-Score" },
            { id: "canslim", name: "CAN SLIM" },
          ],
        },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({ action: "list_screeners" })) as any;
      expect(result.screeners).toHaveLength(2);
      expect(result.screeners[0].id).toBe("fscore");
    });

    it("returns fallback when Python is unavailable", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: false,
        error: "Python not found",
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({ action: "list_screeners" })) as any;
      expect(result.count).toBe(0);
      expect(result.note).toContain("未就绪");
    });
  });

  describe("action=run_screener", () => {
    it("passes correct params to Python engine", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: {
          screenerId: "fscore",
          screenerName: "Piotroski F-Score",
          results: [
            { code: "600519", name: "贵州茅台", score: 8 },
            { code: "000858", name: "五粮液", score: 7 },
          ],
          count: 2,
          totalScreened: 4000,
        },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "run_screener",
        screenerId: "fscore",
        universe: "hs300",
        limit: 10,
      })) as any;

      expect(result.count).toBe(2);
      expect(result.results).toHaveLength(2);

      // Verify Python was called with correct parameters
      expect(runPythonScript).toHaveBeenCalledWith(
        "engines/screener_engine.py",
        expect.objectContaining({
          action: "run_screener",
          screenerId: "fscore",
          universe: "hs300",
          limit: 10,
        }),
        expect.anything(),
      );
    });

    it("parses codes parameter as JSON array", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: { results: [], count: 0 },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      await tool.execute({
        action: "run_screener",
        screenerId: "technical",
        codes: '["600519","000858"]',
      });

      expect(runPythonScript).toHaveBeenCalledWith(
        "engines/screener_engine.py",
        expect.objectContaining({
          codes: ["600519", "000858"],
        }),
        expect.anything(),
      );
    });

    it("returns error on invalid codes JSON", async () => {
      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "run_screener",
        screenerId: "fscore",
        codes: "not-json",
      })) as any;
      expect(result.error).toContain("格式错误");
    });

    it("adds displayHint for large result sets", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: {
          results: Array.from({ length: 10 }, (_, i) => ({ code: `00000${i}` })),
          count: 10,
        },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "run_screener",
        screenerId: "technical",
      })) as any;
      expect(result.displayHint).toBeDefined();
      expect(result.displayHint).toContain("10");
    });
  });

  describe("action=screen (factor filter)", () => {
    it("passes criteria to factor_filter Python engine", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: {
          results: [{ stock_id: "600519", name: "贵州茅台", total_rank: 1 }],
          count: 1,
        },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "screen",
        criteria: '[{"field":"pe","operator":"lt","value":20}]',
        market: "cn_stock",
      })) as any;

      expect(result.count).toBe(1);
      expect(runPythonScript).toHaveBeenCalledWith(
        "engines/factor_filter.py",
        expect.objectContaining({
          action: "screen",
          criteria: [{ field: "pe", operator: "lt", value: 20 }],
        }),
        expect.anything(),
      );
    });

    it("returns error on invalid criteria JSON", async () => {
      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "screen",
        criteria: "{bad json",
      })) as any;
      expect(result.error).toContain("格式错误");
    });
  });

  describe("action=rank", () => {
    it("passes factors list to Python engine", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: { results: [], count: 0 },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      await tool.execute({
        action: "rank",
        factors: '["pe","roe"]',
        limit: 5,
      });

      expect(runPythonScript).toHaveBeenCalledWith(
        "engines/factor_filter.py",
        expect.objectContaining({
          action: "rank",
          factors: ["pe", "roe"],
          limit: 5,
        }),
        expect.anything(),
      );
    });

    it("returns error on invalid factors JSON", async () => {
      const tool = createStockScreenTool(fakeApi, makeConfig());
      const result = (await tool.execute({
        action: "rank",
        factors: "not-an-array",
      })) as any;
      expect(result.error).toContain("格式错误");
    });
  });

  describe("action=rps", () => {
    it("uses default rpsPeriod of 120", async () => {
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: { results: [], count: 0 },
      });

      const tool = createStockScreenTool(fakeApi, makeConfig());
      await tool.execute({ action: "rps" });

      expect(runPythonScript).toHaveBeenCalledWith(
        "engines/factor_filter.py",
        expect.objectContaining({ rpsPeriod: 120 }),
        expect.anything(),
      );
    });
  });

  describe("tool metadata", () => {
    it("has correct tool name and required parameters", () => {
      const tool = createStockScreenTool(fakeApi, makeConfig());
      expect(tool.name).toBe("stock_screen");
      expect(tool.parameters.required).toContain("action");
    });
  });
});
