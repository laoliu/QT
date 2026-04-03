import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock strategy-registry before import
vi.mock("../engine/strategy-registry.js", () => ({
  listStrategies: vi.fn(),
  runBacktest: vi.fn(),
}));

import { listStrategies, runBacktest } from "../engine/strategy-registry.js";
import type { QuantTradingConfig, BacktestResult, StrategyDefinition } from "../types.js";
import { runCliBacktest } from "./backtest-cli.js";

const mockListStrategies = listStrategies as ReturnType<typeof vi.fn>;
const mockRunBacktest = runBacktest as ReturnType<typeof vi.fn>;

const stubConfig: QuantTradingConfig = { dataProvider: "demo" };

const stubStrategies: StrategyDefinition[] = [
  {
    id: "dual-ma-crossover",
    name: "Dual MA Crossover",
    description: "test",
    version: "1.0.0",
    engine: "builtin",
    entryFile: "",
    parameters: [],
  },
  {
    id: "mean-reversion",
    name: "Mean Reversion",
    description: "test",
    version: "1.0.0",
    engine: "builtin",
    entryFile: "",
    parameters: [],
  },
];

const stubResult: BacktestResult = {
  strategyId: "dual-ma-crossover",
  symbols: ["600519"],
  startDate: "2024-01-01",
  endDate: "2024-12-31",
  initialCash: 100000,
  totalReturn: 15.5,
  annualizedReturn: 12.3,
  maxDrawdown: -8.2,
  sharpeRatio: 1.234,
  winRate: 55.5,
  totalTrades: 42,
  finalValue: 115500,
  signals: [],
  equityCurve: [],
};

describe("backtest-cli", () => {
  let logSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockListStrategies.mockReturnValue(stubStrategies);
    mockRunBacktest.mockResolvedValue(stubResult);
  });

  it("prints error for unknown strategy", async () => {
    await runCliBacktest("unknown-strat", {}, stubConfig);

    expect(logSpy).toHaveBeenCalledWith("Unknown strategy: unknown-strat");
    expect(logSpy).toHaveBeenCalledWith("Available strategies:");
    expect(mockRunBacktest).not.toHaveBeenCalled();
  });

  it("lists available strategies when not found", async () => {
    await runCliBacktest("nope", {}, stubConfig);

    const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
    expect(calls).toContainEqual(expect.stringContaining("dual-ma-crossover"));
    expect(calls).toContainEqual(expect.stringContaining("mean-reversion"));
  });

  it("runs backtest and prints results", async () => {
    await runCliBacktest("dual-ma-crossover", { cash: "200000", symbols: "600519" }, stubConfig);

    expect(mockRunBacktest).toHaveBeenCalledTimes(1);
    const [request] = mockRunBacktest.mock.calls[0]!;
    expect(request.strategyId).toBe("dual-ma-crossover");
    expect(request.symbols).toEqual(["600519"]);
    expect(request.initialCash).toBe(200000);

    const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
    expect(calls).toContainEqual(expect.stringContaining("15.50%"));
    expect(calls).toContainEqual(expect.stringContaining("1.234"));
    expect(calls).toContainEqual(expect.stringContaining("42"));
  });

  it("prints pre-run info", async () => {
    await runCliBacktest(
      "dual-ma-crossover",
      { symbols: "000001", engine: "backtrader" },
      stubConfig,
    );

    const calls = logSpy.mock.calls.map((c: unknown[]) => c[0]);
    expect(calls).toContainEqual(expect.stringContaining("000001"));
    expect(calls).toContainEqual(expect.stringContaining("backtrader"));
  });

  it("handles backtest error gracefully", async () => {
    mockRunBacktest.mockRejectedValue(new Error("Python crashed"));
    await runCliBacktest("dual-ma-crossover", {}, stubConfig);

    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("Python crashed"));
  });

  it("passes multiple symbols", async () => {
    await runCliBacktest("dual-ma-crossover", { symbols: "600519,000001,000300" }, stubConfig);

    const [request] = mockRunBacktest.mock.calls[0]!;
    expect(request.symbols).toEqual(["600519", "000001", "000300"]);
  });

  it("uses default cash when not specified", async () => {
    await runCliBacktest("dual-ma-crossover", {}, stubConfig);

    const [request] = mockRunBacktest.mock.calls[0]!;
    expect(request.initialCash).toBe(100000);
  });
});
