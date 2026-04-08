/**
 * Unit tests for RecommendationTracker service and recommend_tracker tool.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { RecommendationTracker } from "../services/recommendation-tracker.js";
import { createRecommendTrackerTool } from "./recommend-tracker.js";

vi.mock("openclaw/plugin-sdk", () => ({}));

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

function makeTracker() {
  return new RecommendationTracker({ maxRecords: 100 });
}

async function seedTracker(tracker: RecommendationTracker) {
  const batchId = "batch_test_001";
  await tracker.record({
    symbol: "600519",
    name: "贵州茅台",
    mode: "classic",
    screenerId: "fscore",
    rank: 1,
    score: 85,
    priceAtRecommend: 1800,
    horizon: "mid",
    stopLoss: 1700,
    takeProfit: 2000,
    backtestMetrics: { totalReturn: 25, sharpeRatio: 1.5, maxDrawdown: -10, winRate: 65 },
    strategy: "F-Score价值",
    batchId,
  });
  await tracker.record({
    symbol: "000858",
    name: "五粮液",
    mode: "classic",
    screenerId: "fscore",
    rank: 2,
    score: 78,
    priceAtRecommend: 150,
    horizon: "mid",
    stopLoss: 140,
    takeProfit: 175,
    strategy: "F-Score价值",
    batchId,
  });
  await tracker.record({
    symbol: "300750",
    name: "宁德时代",
    mode: "predictive",
    screenerId: "volume_price",
    rank: 1,
    score: 72,
    priceAtRecommend: 200,
    horizon: "short",
    batchId: "batch_test_002",
  });
  return { batchId };
}

describe("RecommendationTracker", () => {
  let tracker: RecommendationTracker;

  beforeEach(() => {
    tracker = makeTracker();
    tracker.start();
  });

  it("records recommendations and returns ids", async () => {
    const id = await tracker.record({
      symbol: "600519",
      name: "贵州茅台",
      mode: "classic",
      screenerId: "fscore",
      rank: 1,
      score: 85,
      priceAtRecommend: 1800,
      horizon: "mid",
      batchId: "b1",
    });
    expect(id).toMatch(/^rec_/);
    expect(tracker.size).toBe(1);
  });

  it("queries by status, mode, horizon", async () => {
    await seedTracker(tracker);

    const all = tracker.query();
    expect(all.length).toBe(3);

    const classic = tracker.query({ mode: "classic" });
    expect(classic.length).toBe(2);

    const predictive = tracker.query({ mode: "predictive" });
    expect(predictive.length).toBe(1);
    expect(predictive[0].symbol).toBe("300750");

    const shortTerm = tracker.query({ horizon: "short" });
    expect(shortTerm.length).toBe(1);
  });

  it("computes stats correctly", async () => {
    await seedTracker(tracker);

    // Simulate price updates and resolution
    await tracker.updatePrices(async (symbol) => {
      if (symbol === "600519") return { price: 2050 }; // above take-profit
      if (symbol === "000858") return { price: 138 }; // below stop-loss
      if (symbol === "300750") return { price: 210 }; // still active
      return null;
    });

    const stats = tracker.computeStats();
    expect(stats.total).toBe(3);
    expect(stats.resolved).toBe(2); // 600519 win, 000858 stopped
    expect(stats.active).toBe(1); // 300750
    expect(stats.wins).toBe(1);
    expect(stats.losses).toBe(1);
    expect(stats.winRate).toBe(50);
  });

  it("resolves take-profit correctly", async () => {
    await seedTracker(tracker);

    await tracker.updatePrices(async (symbol) => {
      if (symbol === "600519") return { price: 2050 };
      return null;
    });

    const rec = tracker.query({ symbol: "600519" })[0];
    expect(rec.status).toBe("win");
    expect(rec.resolution).toContain("止盈");
  });

  it("resolves stop-loss correctly", async () => {
    await seedTracker(tracker);

    await tracker.updatePrices(async (symbol) => {
      if (symbol === "000858") return { price: 135 };
      return null;
    });

    const rec = tracker.query({ symbol: "000858" })[0];
    expect(rec.status).toBe("stopped");
    expect(rec.resolution).toContain("止损");
  });

  it("tracks max and min prices", async () => {
    await seedTracker(tracker);

    await tracker.updatePrices(async (symbol) => {
      if (symbol === "300750") return { price: 220 };
      return null;
    });
    await tracker.updatePrices(async (symbol) => {
      if (symbol === "300750") return { price: 195 };
      return null;
    });

    const rec = tracker.query({ symbol: "300750" })[0];
    expect(rec.maxPrice).toBe(220);
    expect(rec.minPrice).toBe(195);
  });

  it("prunes old records when exceeding maxRecords", async () => {
    const small = new RecommendationTracker({ maxRecords: 3 });
    small.start();

    for (let i = 0; i < 5; i++) {
      await small.record({
        symbol: `00000${i}`,
        name: `Stock${i}`,
        mode: "classic",
        screenerId: "test",
        rank: 1,
        score: 50,
        priceAtRecommend: 10,
        horizon: "mid",
        batchId: `b${i}`,
      });
    }

    expect(small.size).toBe(3);
  });
});

describe("recommend_tracker tool", () => {
  let tracker: RecommendationTracker;

  beforeEach(async () => {
    tracker = makeTracker();
    tracker.start();
    await seedTracker(tracker);
  });

  it("list action returns recommendations", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({ action: "list" })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    expect((result.recommendations as unknown[]).length).toBe(3);
  });

  it("stats action returns summary", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({ action: "stats" })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    const summary = result.summary as Record<string, unknown>;
    expect(summary["总推荐数"]).toBe(3);
  });

  it("detail action returns single record", async () => {
    const records = tracker.query();
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({ action: "detail", id: records[0].id })) as Record<
      string,
      unknown
    >;
    expect(result.ok).toBe(true);
    expect(result.recommendation).toBeDefined();
  });

  it("review action lists batches when no batchId", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({ action: "review" })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    expect((result.batches as unknown[]).length).toBeGreaterThan(0);
  });

  it("review action shows batch detail", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({
      action: "review",
      batchId: "batch_test_001",
    })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    expect(result["总推荐数"]).toBe(2);
  });

  it("refresh action works with data provider", async () => {
    const mockProvider = {
      getQuote: vi.fn().mockResolvedValue({ price: 1850 }),
      getKlines: vi.fn(),
    };
    const tool = createRecommendTrackerTool(fakeApi, tracker, () => mockProvider as never);
    const result = (await tool.execute({ action: "refresh" })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    expect(result.updated).toBeGreaterThan(0);
  });

  it("refresh action fails gracefully without data provider", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({ action: "refresh" })) as Record<string, unknown>;
    expect(result.ok).toBe(false);
  });

  it("list with filters works", async () => {
    const tool = createRecommendTrackerTool(fakeApi, tracker);
    const result = (await tool.execute({
      action: "list",
      mode: "predictive",
    })) as Record<string, unknown>;
    expect(result.ok).toBe(true);
    expect((result.recommendations as unknown[]).length).toBe(1);
  });
});
