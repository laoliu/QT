import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({ runPythonScript: vi.fn() }));
vi.mock("../engine/strategy-registry.js", async (original) => ({
  ...await original<Record<string, unknown>>(), runBacktest: vi.fn(),
  registerComposedStrategy: vi.fn(), unregisterComposedStrategy: vi.fn(),
}));
vi.mock("../services/batch-backtest.js", () => ({ runBatchBacktest: vi.fn() }));

import { runPythonScript } from "../bridge/python-bridge.js";
import { runBacktest, registerComposedStrategy, unregisterComposedStrategy } from "../engine/strategy-registry.js";
import { runBatchBacktest } from "../services/batch-backtest.js";
import { createChatTools } from "../services/chat-tool-policy.js";
import { createBacktestTool } from "./backtest.js";
import { createStockScreenTool } from "./stock-screen.js";
import { createStockRecommendTool } from "./stock-recommend.js";
import { createStrategyExplorerTool } from "./strategy-explorer.js";

beforeEach(() => vi.resetAllMocks());

describe("chat analysis side effects through real tool implementations", () => {
  it("runs a backtest without saving a business record, preserving ordinary save behavior", async () => {
    vi.mocked(runBacktest).mockResolvedValue({ initialCash: 100_000, finalValue: 110_000, totalReturn: 10, annualizedReturn: 10, maxDrawdown: 5, sharpeRatio: 1, winRate: 60, totalTrades: 20, signals: [] } as any);
    const store = { save: vi.fn().mockResolvedValue("saved") };
    const raw = createBacktestTool({} as any, {} as any, store as any);
    const chat = createChatTools([raw]).get("backtest")!;
    expect(await chat.execute({ action: "run", strategyId: "dual-ma-crossover", persist_results: true })).toHaveProperty("summary");
    expect(store.save).not.toHaveBeenCalled();
    await raw.execute({ action: "run", strategyId: "dual-ma-crossover" });
    expect(store.save).toHaveBeenCalledOnce();
  });

  it("screens without saving scan history", async () => {
    vi.mocked(runPythonScript).mockImplementation(async () => ({ ok: true, data: { results: [{ code: "600519", score: 8 }], totalScreened: 300 } }) as any);
    const store = { save: vi.fn().mockResolvedValue("saved") };
    const raw = createStockScreenTool({} as any, {} as any, store as any);
    await createChatTools([raw]).get("stock_screen")!.execute({ action: "run_screener", screenerId: "fscore" });
    expect(store.save).not.toHaveBeenCalled();
    await raw.execute({ action: "run_screener", screenerId: "fscore" });
    expect(store.save).toHaveBeenCalledOnce();
  });

  it.each(["classic", "predictive"])("does not write scan/tracker records during %s recommendation", async (mode) => {
    vi.mocked(runPythonScript).mockImplementation(async (script) => {
      if (script.includes("screener")) return { ok: true, data: { results: [{ code: "600519", stock_id: "600519", score: 8, factors: {} }], totalScreened: 300 } } as any;
      if (script.includes("strategy_router")) return { ok: true, data: { results: [{ symbol: "600519", ok: true, backtest: { totalReturn: 20, sharpeRatio: 1.5, maxDrawdown: 10, winRate: 60, totalTrades: 30 } }] } } as any;
      return { ok: true, data: {} } as any;
    });
    const store = { save: vi.fn() }, tracker = { record: vi.fn() };
    const raw = createStockRecommendTool({} as any, {} as any, store as any, undefined, tracker as any);
    const result = await createChatTools([raw]).get("stock_recommend")!.execute({ mode, count: 1 });
    expect(result).toMatchObject({ ok: true });
    expect(store.save).not.toHaveBeenCalled();
    expect(tracker.record).not.toHaveBeenCalled();
  });

  it("releases every temporary strategy after analysis and after a batch failure", async () => {
    vi.mocked(runBatchBacktest).mockImplementation(async (items) => ({ results: [], ranked: [{ label: items[0].label }], successCount: 1, failCount: 0 }) as any);
    const store = { upsert: vi.fn() };
    const raw = createStrategyExplorerTool({} as any, {} as any, store as any);
    const chat = createChatTools([raw]).get("strategy_explorer")!;
    const result = await chat.execute({ action: "explore", symbols: "600519", candidateCount: 2 });
    expect(result).toMatchObject({ ok: true, saved: false });
    expect(store.upsert).not.toHaveBeenCalled();
    for (const [composition] of vi.mocked(registerComposedStrategy).mock.calls) expect(unregisterComposedStrategy).toHaveBeenCalledWith(composition.id);
    vi.mocked(unregisterComposedStrategy).mockClear(); vi.mocked(registerComposedStrategy).mockClear();
    vi.mocked(runBatchBacktest).mockRejectedValueOnce(new Error("batch failed"));
    await expect(chat.execute({ action: "explore", symbols: "600519", candidateCount: 2 })).rejects.toThrow("batch failed");
    expect(vi.mocked(registerComposedStrategy).mock.calls.length).toBeGreaterThan(0);
    for (const [composition] of vi.mocked(registerComposedStrategy).mock.calls) expect(unregisterComposedStrategy).toHaveBeenCalledWith(composition.id);
  });
});
