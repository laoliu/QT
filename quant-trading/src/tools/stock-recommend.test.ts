/**
 * Unit tests for stock_recommend tool.
 *
 * Covers both classic and predictive modes.
 * Mocks the Python bridge so no real Python process is needed.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { QuantTradingConfig } from "../types.js";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createStockRecommendTool } from "./stock-recommend.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

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

function makeTool(scanHistoryStore?: unknown) {
  return createStockRecommendTool(fakeApi, makeConfig(), scanHistoryStore as never);
}

// Helper: mock screener result
function mockScreenerResult(codes: string[]) {
  return {
    ok: true,
    data: {
      screenerId: "volume_price",
      screenerName: "量价异动筛选",
      count: codes.length,
      totalScreened: 300,
      results: codes.map((code, i) => ({
        stock_id: code,
        name: `Stock${code}`,
        score: 5 + i * 0.5,
        rank: i + 1,
        factors: {
          volume_ratio: 2.0 + i * 0.3,
          accumulation_score: 1.0 + i * 0.1,
          divergence_score: 0.5 + i * 0.2,
          bottom_reversal_score: i === 0 ? 1.5 : 0.3,
          signals: ["放量阳线", "量价底背离"],
        },
      })),
    },
  };
}

// Helper: mock chip accumulation result
function mockChipResult(symbols: string[]) {
  return {
    ok: true,
    data: {
      action: "scan",
      count: symbols.length,
      stocks: symbols.map((s, i) => ({
        symbol: s,
        name: `Stock${s}`,
        price: 20 + i,
        total_score: 60 + i * 5,
        level: i === 0 ? "strong" : "moderate",
        conclusion: i === 0 ? "明显吸筹" : "温和吸筹",
        dimension_scores: { fund_flow: 15, chip: 14, volume_price: 12 },
      })),
    },
  };
}

// Helper: mock institutional flow result
function mockInstFlowResult(score: number) {
  return {
    ok: true,
    data: {
      score,
      conclusion: score > 60 ? "机构信心强" : "机构态度中性",
    },
  };
}

describe("stock_recommend tool", () => {
  beforeEach(() => {
    vi.mocked(runPythonScript).mockReset();
  });

  it("exposes mode and horizon parameters", () => {
    const tool = makeTool();
    expect(tool.name).toBe("stock_recommend");
    const props = tool.parameters.properties;
    expect(props.mode).toBeDefined();
    expect(props.mode.enum).toContain("classic");
    expect(props.mode.enum).toContain("predictive");
    expect(props.horizon).toBeDefined();
    expect(props.horizon.enum).toEqual(["short", "mid", "long", "all"]);
  });

  it("description mentions predictive mode", () => {
    const tool = makeTool();
    expect(tool.description).toContain("predictive");
    expect(tool.description).toContain("前瞻式选股");
  });

  describe("mode=classic (default)", () => {
    it("runs classic pipeline when mode is not specified", async () => {
      const mockPy = vi.mocked(runPythonScript);
      // Screener call
      mockPy.mockResolvedValueOnce({
        ok: true,
        data: {
          results: [
            { code: "600001", name: "TestA", score: 7 },
            { code: "600002", name: "TestB", score: 6 },
          ],
          totalScreened: 100,
        },
      });
      // Strategy router call
      mockPy.mockResolvedValueOnce({
        ok: true,
        data: {
          results: [
            {
              symbol: "600001",
              name: "TestA",
              ok: true,
              backtest: {
                totalReturn: 25,
                sharpeRatio: 1.5,
                maxDrawdown: -10,
                winRate: 60,
                totalTrades: 30,
              },
            },
          ],
        },
      });

      const tool = makeTool();
      const result = (await tool.execute({ count: 1 })) as Record<string, unknown>;
      expect(result.ok).toBe(true);
      expect(result.pipeline).toContain("stock_screen");
      expect(result.pipeline).toContain("strategy_router");
    });
  });

  describe("mode=predictive", () => {
    it("runs predictive pipeline with multi-signal enrichment", async () => {
      const mockPy = vi.mocked(runPythonScript);

      // Call 1: volume_price screener
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001", "600002", "600003"]));

      // Call 2: chip accumulation batch
      mockPy.mockResolvedValueOnce(mockChipResult(["600001", "600002", "600003"]));

      // Call 3: sector ranking
      mockPy.mockResolvedValueOnce({
        ok: true,
        data: {
          sectors: [
            { name: "半导体", code: "BK0474", change_pct: 3.2, leader: "中芯国际" },
            { name: "新能源", code: "BK0478", change_pct: 2.1, leader: "宁德时代" },
          ],
        },
      });

      // Calls 4-6: institutional flow (one per top candidate)
      mockPy.mockResolvedValueOnce(mockInstFlowResult(75));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(45));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(60));

      const tool = makeTool();
      const result = (await tool.execute({ mode: "predictive", count: 3 })) as Record<
        string,
        unknown
      >;

      expect(result.ok).toBe(true);
      expect(result.mode).toBe("predictive");
      expect(result.pipeline).toContain("volume_price");
      expect(result.pipeline).toContain("chip_accumulation");
      expect(result.pipeline).toContain("institutional_flow");
      expect(result.enrichment).toBeDefined();
      expect(result.disclaimer).toBeDefined();

      const recs = result.recommendations as Array<Record<string, unknown>>;
      expect(recs.length).toBeGreaterThan(0);
      expect(recs[0]).toHaveProperty("predictiveScore");
      expect(recs[0]).toHaveProperty("horizon");
      expect(recs[0]).toHaveProperty("horizonLabel");
      expect(recs[0]).toHaveProperty("horizonReason");
      expect(recs[0]).toHaveProperty("scoringBreakdown");
    });

    it("classifies first candidate as short-term when bottom reversal is strong", async () => {
      const mockPy = vi.mocked(runPythonScript);
      // volume_price screener — first stock has bottom_reversal_score=1.5
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001", "600002"]));
      // chip_accumulation
      mockPy.mockResolvedValueOnce(mockChipResult(["600001", "600002"]));
      // sector ranking
      mockPy.mockResolvedValueOnce({ ok: true, data: { sectors: [] } });
      // institutional flow
      mockPy.mockResolvedValueOnce(mockInstFlowResult(50));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(30));

      const tool = makeTool();
      const result = (await tool.execute({
        mode: "predictive",
        count: 2,
      })) as Record<string, unknown>;

      const recs = result.recommendations as Array<Record<string, unknown>>;
      // First candidate has bottom_reversal_score=1.5 → should be "short"
      const shortRecs = recs.filter((r) => r.horizon === "short");
      expect(shortRecs.length).toBeGreaterThanOrEqual(1);
    });

    it("filters by horizon when specified", async () => {
      const mockPy = vi.mocked(runPythonScript);
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001", "600002", "600003"]));
      mockPy.mockResolvedValueOnce(mockChipResult(["600001", "600002", "600003"]));
      mockPy.mockResolvedValueOnce({ ok: true, data: { sectors: [] } });
      mockPy.mockResolvedValueOnce(mockInstFlowResult(70));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(20));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(30));

      const tool = makeTool();
      const result = (await tool.execute({
        mode: "predictive",
        horizon: "short",
        count: 5,
      })) as Record<string, unknown>;

      const recs = result.recommendations as Array<Record<string, unknown>>;
      for (const r of recs) {
        expect(r.horizon).toBe("short");
      }
    });

    it("returns error gracefully when screener returns empty", async () => {
      // runScreenWithFallback tries multiple universe tiers, mock all with empty
      vi.mocked(runPythonScript).mockResolvedValue({
        ok: true,
        data: { results: [], totalScreened: 300 },
      });

      const tool = makeTool();
      const result = (await tool.execute({ mode: "predictive" })) as Record<string, unknown>;

      expect(result.ok).toBe(false);
      expect(result.mode).toBe("predictive");
      expect(result.step).toBe("screening");
    });

    it("continues when chip accumulation engine fails", async () => {
      const mockPy = vi.mocked(runPythonScript);
      // screener succeeds
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001"]));
      // chip fails
      mockPy.mockResolvedValueOnce({ ok: false, error: "python not available" });
      // sector fails likewise
      mockPy.mockResolvedValueOnce({ ok: false, error: "python not available" });
      // institutional flow fails
      mockPy.mockResolvedValueOnce({ ok: false, error: "python not available" });

      const tool = makeTool();
      const result = (await tool.execute({
        mode: "predictive",
        count: 1,
      })) as Record<string, unknown>;

      // Should still return results (with lower scores due to missing enrichment)
      expect(result.ok).toBe(true);
      expect(result.mode).toBe("predictive");
      const recs = result.recommendations as Array<Record<string, unknown>>;
      expect(recs.length).toBe(1);
    });

    it("includes horizon distribution in output", async () => {
      const mockPy = vi.mocked(runPythonScript);
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001", "600002", "600003", "600004"]));
      mockPy.mockResolvedValueOnce(mockChipResult(["600001", "600002", "600003", "600004"]));
      mockPy.mockResolvedValueOnce({ ok: true, data: { sectors: [] } });
      mockPy.mockResolvedValueOnce(mockInstFlowResult(80));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(40));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(30));
      mockPy.mockResolvedValueOnce(mockInstFlowResult(20));

      const tool = makeTool();
      const result = (await tool.execute({
        mode: "predictive",
        count: 4,
      })) as Record<string, unknown>;

      const dist = result.horizonDistribution as Record<string, number>;
      expect(dist).toBeDefined();
      expect(typeof dist.short).toBe("number");
      expect(typeof dist.mid).toBe("number");
      expect(typeof dist.long).toBe("number");
      expect(dist.short + dist.mid + dist.long).toBe(4);
    });

    it("records to scan history when store is provided", async () => {
      const mockPy = vi.mocked(runPythonScript);
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001"]));
      mockPy.mockResolvedValueOnce(mockChipResult(["600001"]));
      mockPy.mockResolvedValueOnce({ ok: true, data: { sectors: [] } });
      mockPy.mockResolvedValueOnce(mockInstFlowResult(50));

      const mockStore = { save: vi.fn().mockResolvedValue("scan-123") };
      const tool = makeTool(mockStore);
      await tool.execute({ mode: "predictive", count: 1 });

      expect(mockStore.save).toHaveBeenCalledWith(
        "predictive",
        expect.any(String),
        "cn_stock",
        expect.any(Number),
        expect.arrayContaining([expect.objectContaining({ code: "600001" })]),
        expect.objectContaining({
          tags: expect.arrayContaining(["predictive"]),
        }),
      );
    });

    it("scoring breakdown adds up to predictive score", async () => {
      const mockPy = vi.mocked(runPythonScript);
      mockPy.mockResolvedValueOnce(mockScreenerResult(["600001"]));
      mockPy.mockResolvedValueOnce(mockChipResult(["600001"]));
      mockPy.mockResolvedValueOnce({ ok: true, data: { sectors: [] } });
      mockPy.mockResolvedValueOnce(mockInstFlowResult(70));

      const tool = makeTool();
      const result = (await tool.execute({
        mode: "predictive",
        count: 1,
      })) as Record<string, unknown>;

      const recs = result.recommendations as Array<Record<string, unknown>>;
      expect(recs.length).toBe(1);

      const rec = recs[0];
      const breakdown = rec.scoringBreakdown as Record<string, string>;
      // Parse numeric values from "12.3 (30%)" format
      const values = Object.values(breakdown).map((v) => parseFloat(v));
      const sum = values.reduce((a, b) => a + b, 0);
      expect(Math.abs(sum - (rec.predictiveScore as number))).toBeLessThan(0.2);
    });
  });
});
