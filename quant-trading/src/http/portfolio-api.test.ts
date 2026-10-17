/**
 * Unit tests for HTTP API handlers (portfolio-api.ts).
 *
 * Tests the pure handler functions directly — no HTTP server needed.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";
import {
  handlePortfolioApi,
  handleLiveStatusApi,
  handleOrdersApi,
  handlePnlApi,
  handlePlaceOrderApi,
  handleSignalsApi,
  handleGetStocklistApi,
  handleSetStocklistApi,
  handleCancelOrderApi,
  appendPlanHistory,
  handleGetPlanHistoryApi,
  handleGetPlanByIdApi,
  handleDeletePlanApi,
  handleUpdatePlanLabelApi,
} from "./portfolio-api.js";

let tmpDir: string;
let config: QuantTradingConfig;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-http-test-"));
  config = { dataDir: tmpDir, pythonPath: "python3" } as QuantTradingConfig;
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("handlePortfolioApi", () => {
  it("returns default portfolio when file missing", async () => {
    const result = await handlePortfolioApi(config);
    expect(result.status).toBe("ok");
    const data = result.data as Record<string, unknown>;
    expect(data.cash).toBe(100000);
  });

  it("reads portfolio from file", async () => {
    const pf = { id: "test", cash: 50000, positions: [{ symbol: "600519", qty: 100 }] };
    await fs.writeFile(path.join(tmpDir, "portfolio.json"), JSON.stringify(pf));
    const result = await handlePortfolioApi(config);
    expect(result.status).toBe("ok");
    expect((result.data as Record<string, unknown>).cash).toBe(50000);
  });
});

describe("handleLiveStatusApi", () => {
  it("returns stopped when no engine", () => {
    const result = handleLiveStatusApi(() => null);
    expect(result.status).toBe("ok");
    expect((result.data as Record<string, unknown>).engineStatus).toBe("stopped");
  });

  it("returns engine status when available", () => {
    const engine = { getStatus: vi.fn(() => "running") } as unknown as LiveEngine;
    const result = handleLiveStatusApi(() => engine);
    expect(result.status).toBe("ok");
    expect(result.data).toBe("running");
  });
});

describe("handleOrdersApi", () => {
  it("returns empty array when no engine", async () => {
    const result = await handleOrdersApi(() => null);
    expect(result.data).toEqual([]);
  });

  it("returns orders from engine", async () => {
    const orders = [{ orderId: "o1", symbol: "600519", status: "filled" }];
    const engine = {
      orderManager: { getOrders: vi.fn().mockResolvedValue(orders) },
    } as unknown as LiveEngine;
    const result = await handleOrdersApi(() => engine);
    expect(result.data).toEqual(orders);
  });

  it("filters by status", async () => {
    const orders = [
      { orderId: "o1", symbol: "600519", status: "filled" },
      { orderId: "o2", symbol: "000001", status: "pending" },
    ];
    const engine = {
      orderManager: { getOrders: vi.fn().mockResolvedValue(orders) },
    } as unknown as LiveEngine;
    const result = await handleOrdersApi(() => engine, "filled");
    expect((result.data as unknown[]).length).toBe(1);
  });
});

describe("handlePnlApi", () => {
  it("returns null when no engine", async () => {
    const result = await handlePnlApi(() => null);
    expect(result.data).toBeNull();
  });

  it("returns PnL from engine", async () => {
    const engine = {
      orderManager: { getDailyPnl: vi.fn().mockResolvedValue(1234.56) },
    } as unknown as LiveEngine;
    const result = await handlePnlApi(() => engine);
    const data = result.data as Record<string, unknown>;
    expect(data.netPnl).toBe(1234.56);
    expect(data.date).toMatch(/\d{4}-\d{2}-\d{2}/);
  });
});

describe("handlePlaceOrderApi", () => {
  it("returns error when engine is null", async () => {
    const result = await handlePlaceOrderApi(() => null, {
      symbol: "600519",
      side: "buy",
      quantity: 100,
    });
    expect(result.status).toBe("error");
  });

  it("returns error when missing required params", async () => {
    const engine = {
      orderManager: { submit: vi.fn() },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(() => engine, { symbol: "600519" });
    expect(result.status).toBe("error");
    expect(result.message).toContain("缺少");
  });

  it("places order successfully", async () => {
    const order = { orderId: "o1", symbol: "600519" };
    const engine = {
      orderManager: { submit: vi.fn().mockResolvedValue({ ok: true, order }) },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(() => engine, {
      symbol: "600519",
      side: "buy",
      quantity: 100,
    });
    expect(result.status).toBe("ok");
    expect(result.data).toEqual(order);
  });

  it("returns reason on risk rejection", async () => {
    const engine = {
      orderManager: { submit: vi.fn().mockResolvedValue({ ok: false, reason: "超过限额" }) },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(() => engine, {
      symbol: "600519",
      side: "buy",
      quantity: 100,
    });
    expect(result.status).toBe("error");
    expect(result.message).toBe("超过限额");
  });

  it("rejects direct QMT HTTP orders without a target execution plan", async () => {
    const engine = {
      broker: { brokerType: "qmt" },
      orderManager: { submit: vi.fn() },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(
      () => engine,
      { symbol: "600519", side: "buy", quantity: 100 },
      { tradingAccount: { mode: "live", broker: "qmt" } } as any,
    );
    expect(result.status).toBe("error");
    expect(result.code).toBe("EXECUTION_PLAN_REQUIRED");
    expect(engine.orderManager!.submit).not.toHaveBeenCalled();
  });

  it("rejects invalid side value", async () => {
    const engine = {
      orderManager: { submit: vi.fn() },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(() => engine, {
      symbol: "600519",
      side: "drop_table",
      quantity: 100,
    });
    expect(result.status).toBe("error");
    expect(result.message).toContain("无效");
    expect(engine.orderManager!.submit).not.toHaveBeenCalled();
  });

  it("rejects invalid orderType value", async () => {
    const engine = {
      orderManager: { submit: vi.fn() },
    } as unknown as LiveEngine;
    const result = await handlePlaceOrderApi(() => engine, {
      symbol: "600519",
      side: "buy",
      quantity: 100,
      orderType: "stop_limit",
    });
    expect(result.status).toBe("error");
    expect(result.message).toContain("无效");
    expect(engine.orderManager!.submit).not.toHaveBeenCalled();
  });
});

describe("handleSignalsApi", () => {
  it("returns empty array when file missing", async () => {
    const result = await handleSignalsApi(config, {});
    expect(result.data).toEqual([]);
  });

  it("reads and filters signals", async () => {
    const signals = [
      { strategyId: "macd", symbol: "600519", direction: "buy" },
      { strategyId: "rsi", symbol: "000001", direction: "sell" },
      { strategyId: "macd", symbol: "000001", direction: "buy" },
    ];
    await fs.writeFile(path.join(tmpDir, "signals.json"), JSON.stringify(signals));

    const byStrategy = await handleSignalsApi(config, { strategyId: "macd" });
    expect((byStrategy.data as unknown[]).length).toBe(2);

    const bySymbol = await handleSignalsApi(config, { symbol: "000001" });
    expect((bySymbol.data as unknown[]).length).toBe(2);

    const limited = await handleSignalsApi(config, { limit: 1 });
    expect((limited.data as unknown[]).length).toBe(1);
  });
});

describe("handleStocklistApi", () => {
  it("returns empty array when file missing", async () => {
    const result = await handleGetStocklistApi(config);
    expect(result.data).toEqual([]);
  });

  it("sets and gets stocklist", async () => {
    const codes = ["600519", "000001", "000858"];
    const setResult = await handleSetStocklistApi(config, codes);
    expect(setResult.status).toBe("ok");
    expect(setResult.count).toBe(3);

    const getResult = await handleGetStocklistApi(config);
    expect(getResult.data).toEqual(codes);
  });

  it("rejects non-array body", async () => {
    const result = await handleSetStocklistApi(config, "not an array");
    expect(result.status).toBe("error");
  });
});

describe("handleCancelOrderApi", () => {
  it("returns error when engine is null", async () => {
    const result = await handleCancelOrderApi(() => null, "o1");
    expect(result.status).toBe("error");
  });

  it("cancels order successfully", async () => {
    const engine = {
      orderManager: { cancel: vi.fn().mockResolvedValue(undefined) },
    } as unknown as LiveEngine;
    const result = await handleCancelOrderApi(() => engine, "o1");
    expect(result.status).toBe("ok");
    expect((result.data as Record<string, unknown>).cancelled).toBe("o1");
  });
});

// ── Plan History Version Management ──────────────────────────────

const samplePlan = {
  plan: {
    strategyLabel: "综合评分",
    riskLevel: "中",
    feasibility: "可行",
    requiredReturn: 20,
    initialCapital: 500000,
    targetCapital: 600000,
  },
  summary: { totalPositions: 2, cashReservePct: 10 },
  portfolio: [
    { symbol: "600519", name: "贵州茅台", score: 85, price: 1800, shares: 100 },
    { symbol: "000001", name: "平安银行", score: 72, price: 12, shares: 500 },
  ],
  rules: {},
  monitoring: {},
};

const sampleParams = {
  initial_capital: 500000,
  target_capital: 600000,
  strategy_preference: "auto",
};

describe("Plan History — version management", () => {
  it("appends plan with date-based version", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    const result = await handleGetPlanHistoryApi(config);
    expect(result.status).toBe("ok");
    expect(result.count).toBe(1);
    const entries = result.entries as Array<Record<string, unknown>>;
    expect(entries[0].version).toMatch(/^\d{4}-\d{2}-\d{2}-v1$/);
  });

  it("increments version number for same-day plans", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    await appendPlanHistory(config, samplePlan, sampleParams);
    const result = await handleGetPlanHistoryApi(config);
    const entries = result.entries as Array<Record<string, unknown>>;
    // Most recent first
    expect(entries[0].version).toMatch(/-v2$/);
    expect(entries[1].version).toMatch(/-v1$/);
  });

  it("get plan by ID returns full plan data", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    const list = await handleGetPlanHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    const planId = entries[0].id as string;

    const detail = await handleGetPlanByIdApi(config, planId);
    expect(detail.status).toBe("ok");
    const entry = detail.entry as Record<string, unknown>;
    expect(entry.fullPlan).toBeTruthy();
    const fp = entry.fullPlan as Record<string, unknown>;
    expect((fp.portfolio as unknown[]).length).toBe(2);
  });

  it("list endpoint strips fullPlan to reduce payload", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    const list = await handleGetPlanHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    expect(entries[0].fullPlan).toBeUndefined();
  });

  it("delete plan removes it from history", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    const list = await handleGetPlanHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    const planId = entries[0].id as string;

    const delResult = await handleDeletePlanApi(config, planId);
    expect(delResult.status).toBe("ok");

    const afterDel = await handleGetPlanHistoryApi(config);
    expect(afterDel.count).toBe(0);
  });

  it("delete non-existent plan returns error", async () => {
    const result = await handleDeletePlanApi(config, "non-existent-id");
    expect(result.status).toBe("error");
  });

  it("update plan label", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    const list = await handleGetPlanHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    const planId = entries[0].id as string;

    const upResult = await handleUpdatePlanLabelApi(config, planId, "三月投资计划");
    expect(upResult.status).toBe("ok");

    const detail = await handleGetPlanByIdApi(config, planId);
    expect((detail.entry as Record<string, unknown>).label).toBe("三月投资计划");
  });

  it("get non-existent plan returns error", async () => {
    const result = await handleGetPlanByIdApi(config, "does-not-exist");
    expect(result.status).toBe("error");
  });

  it("tracks stock frequency across plans", async () => {
    await appendPlanHistory(config, samplePlan, sampleParams);
    await appendPlanHistory(config, samplePlan, sampleParams);
    const result = await handleGetPlanHistoryApi(config);
    const topStocks = result.topStocks as Array<Record<string, unknown>>;
    expect(topStocks.length).toBeGreaterThan(0);
    expect(topStocks[0].count).toBe(2);
  });
});
