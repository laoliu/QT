/**
 * Unit tests for the futures_straddle AI tool.
 *
 * Mocks runPythonScript to verify parameter mapping and result handling.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createFuturesStraddleTool } from "./futures-straddle.js";

const mockRun = vi.mocked(runPythonScript);
const fakeApi = {} as never;

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-quant-test",
  pythonPath: "python3",
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("futures_straddle tool", () => {
  it("has correct name and required parameters", () => {
    const tool = createFuturesStraddleTool(fakeApi, config);
    expect(tool.name).toBe("futures_straddle");
    expect(tool.parameters.required).toContain("action");
  });

  describe("action=butterfly", () => {
    it("passes near/mid/far prices to python", async () => {
      mockRun.mockResolvedValue({
        ok: true,
        data: { spread: 50, pnl: 5000, spreadPerPoint: 200 },
      });

      const tool = createFuturesStraddleTool(fakeApi, config);
      const result = await tool.execute({
        action: "butterfly",
        product: "IC",
        nearPrice: 6000,
        midPrice: 6020,
        farPrice: 6050,
        lots: 2,
      });

      expect(result).toMatchObject({ action: "butterfly", product: "IC", spread: 50 });
      expect(mockRun).toHaveBeenCalledWith(
        "engines/futures_straddle.py",
        expect.objectContaining({
          action: "butterfly",
          product: "IC",
          nearPrice: 6000,
          midPrice: 6020,
          farPrice: 6050,
          lots: 2,
        }),
        config,
      );
    });

    it("includes exit prices for PnL calculation", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { pnl: 10000 } });

      const tool = createFuturesStraddleTool(fakeApi, config);
      await tool.execute({
        action: "butterfly",
        product: "IF",
        nearPrice: 4000,
        midPrice: 4010,
        farPrice: 4030,
        nearExitPrice: 4005,
        midExitPrice: 4008,
        farExitPrice: 4035,
      });

      expect(mockRun).toHaveBeenCalledWith(
        "engines/futures_straddle.py",
        expect.objectContaining({
          nearExitPrice: 4005,
          midExitPrice: 4008,
          farExitPrice: 4035,
        }),
        config,
      );
    });
  });

  describe("action=premium", () => {
    it("passes futures and spot prices", async () => {
      mockRun.mockResolvedValue({
        ok: true,
        data: { basis: -30, basisPct: -0.5, signal: "贴水收敛机会" },
      });

      const tool = createFuturesStraddleTool(fakeApi, config);
      const result = await tool.execute({
        action: "premium",
        product: "IF",
        futuresPrice: 3970,
        spotPrice: 4000,
      });

      expect(result).toMatchObject({ action: "premium", product: "IF", basis: -30 });
      expect(mockRun).toHaveBeenCalledWith(
        "engines/futures_straddle.py",
        expect.objectContaining({ action: "premium", futuresPrice: 3970, spotPrice: 4000 }),
        config,
      );
    });
  });

  describe("action=basis", () => {
    it("maps basis action to basis_check for python", async () => {
      mockRun.mockResolvedValue({
        ok: true,
        data: { basis: 15, basisPct: 0.3 },
      });

      const tool = createFuturesStraddleTool(fakeApi, config);
      const result = await tool.execute({
        action: "basis",
        product: "IH",
        futuresPrice: 2815,
        spotPrice: 2800,
      });

      expect(result).toMatchObject({ product: "IH" });
      // The action should be mapped to "basis_check" for Python
      expect(mockRun).toHaveBeenCalledWith(
        "engines/futures_straddle.py",
        expect.objectContaining({ action: "basis_check" }),
        config,
      );
    });
  });

  describe("defaults", () => {
    it("defaults product to IC and lots to 1", async () => {
      mockRun.mockResolvedValue({ ok: true, data: {} });

      const tool = createFuturesStraddleTool(fakeApi, config);
      await tool.execute({ action: "butterfly" });

      expect(mockRun).toHaveBeenCalledWith(
        "engines/futures_straddle.py",
        expect.objectContaining({ product: "IC", lots: 1 }),
        config,
      );
    });
  });

  describe("error handling", () => {
    it("returns error with hint when python fails", async () => {
      mockRun.mockResolvedValue({ ok: false, error: "engine crash" });

      const tool = createFuturesStraddleTool(fakeApi, config);
      const result = (await tool.execute({ action: "butterfly" })) as {
        error: string;
        hint: string;
      };
      expect(result.error).toContain("engine crash");
      expect(result.hint).toBeDefined();
    });

    it("returns generic error when no message", async () => {
      mockRun.mockResolvedValue({ ok: false, data: null });

      const tool = createFuturesStraddleTool(fakeApi, config);
      const result = (await tool.execute({ action: "basis" })) as { error: string };
      expect(result.error).toContain("未知错误");
    });
  });
});
