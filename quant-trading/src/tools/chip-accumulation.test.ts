import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createChipAccumulationTool } from "./chip-accumulation.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createChipAccumulationTool(api, {} as never);
}

describe("chip_accumulation tool", () => {
  it("supports ambush_scan action in schema", () => {
    const tool = makeTool();
    expect(tool.name).toBe("chip_accumulation");
    expect(tool.parameters.required).toContain("action");
    expect(tool.parameters.properties.action.enum).toContain("ambush_scan");
  });

  it("forwards ambush_scan parameters to python engine", async () => {
    const mockRunPython = vi.mocked(runPythonScript);
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "ambush_scan",
        count: 1,
        stocks: [
          {
            symbol: "600001",
            signal_type: "潜伏",
            reasons: ["前期放量命中 2 次"],
            risk_flags: ["主升段确认不足，当前仅观察"],
            score_components: {
              base: 40,
              pre_accum_hits: 8,
              main_force_zljc: 6,
              main_force_var6: 4,
              ma_deviation: 5,
              day_change: 3,
            },
          },
        ],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "ambush_scan",
      symbols: "600001,600002",
      lookback: "35",
      min_confidence: "30",
      as_of_date: "2026-03-20",
      ambush_volume_ratio: "1.6",
      ambush_pre_days: "18",
      ambush_pre_hits: "2",
      ambush_change_max: "6",
      ambush_change_min: "-3",
      ambush_turnover_min: "0.5",
      ambush_turnover_max: "15",
      ambush_ma_period: "25",
      ambush_ma_deviation_max: "10",
      ambush_shrink_ratio: "bad-value",
      ambush_stop_volatility: "1.2",
      ambush_near_low_max: "7",
      ambush_box_days: "12",
      ambush_box_amplitude: "20",
      ambush_ma_convergence: "4",
    })) as Record<string, unknown>;

    expect(result.action).toBe("ambush_scan");
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/chip_accumulation.py",
      expect.objectContaining({
        action: "ambush_scan",
        symbols: "600001,600002",
        lookback: 35,
        min_confidence: 30,
        as_of_date: "2026-03-20",
        ambush_volume_ratio: 1.6,
        ambush_pre_days: 18,
        ambush_pre_hits: 2,
        ambush_change_max: 6,
        ambush_change_min: -3,
        ambush_turnover_min: 0.5,
        ambush_turnover_max: 15,
        ambush_ma_period: 25,
        ambush_ma_deviation_max: 10,
        ambush_shrink_ratio: undefined,
        ambush_stop_volatility: 1.2,
        ambush_near_low_max: 7,
        ambush_box_days: 12,
        ambush_box_amplitude: 20,
        ambush_ma_convergence: 4,
      }),
      expect.anything(),
    );
  });

  it("adds display hint for ambush_scan when count is large", async () => {
    const mockRunPython = vi.mocked(runPythonScript);
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "ambush_scan",
        count: 12,
        stocks: Array.from({ length: 12 }, (_, i) => ({ symbol: `6000${i}` })),
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "ambush_scan" })) as Record<string, unknown>;
    expect(result.displayHint).toBeTypeOf("string");
    expect(String(result.displayHint)).toContain("吸筹候选");
  });

  it("returns python error as-is when engine reports error", async () => {
    const mockRunPython = vi.mocked(runPythonScript);
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "ambush_scan",
        error: "数据源异常",
        errors: ["akshare timeout"],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "ambush_scan" })) as Record<string, unknown>;
    expect(result.error).toBe("数据源异常");
    expect(result.errors).toEqual(["akshare timeout"]);
  });
});
