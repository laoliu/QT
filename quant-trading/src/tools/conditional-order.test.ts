import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConditionalOrderService } from "../services/conditional-order.js";
import { createConditionalOrderTool } from "./conditional-order.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

describe("conditional_order tool", () => {
  let tmpDir: string;
  let service: ConditionalOrderService;
  let tool: ReturnType<typeof createConditionalOrderTool>;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "cond-order-tool-"));
    service = new ConditionalOrderService({
      dataDir: tmpDir,
      checkIntervalMs: 999999, // no auto-check
      maxOrders: 100,
    });
    tool = createConditionalOrderTool(fakeApi, service);
  });

  afterEach(() => {
    service.stop();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  // ── add_stop_loss ──

  it("adds a stop loss order", async () => {
    const res = (await tool.execute({
      action: "add_stop_loss",
      symbol: "600519",
      entry_price: 1800,
      stop_price: 1700,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.message).toContain("止损");
    expect(res.id).toBeDefined();
    expect(res.symbol).toBe("600519");
  });

  it("rejects stop_loss when stop >= entry", async () => {
    const res = (await tool.execute({
      action: "add_stop_loss",
      symbol: "600519",
      entry_price: 1800,
      stop_price: 1900,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.error).toContain("低于");
  });

  it("requires all fields for stop_loss", async () => {
    const res = (await tool.execute({
      action: "add_stop_loss",
      symbol: "600519",
    })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── add_take_profit ──

  it("adds a take profit order", async () => {
    const res = (await tool.execute({
      action: "add_take_profit",
      symbol: "600519",
      entry_price: 1800,
      target_price: 2000,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.message).toContain("止盈");
    expect(res.id).toBeDefined();
  });

  it("rejects take_profit when target <= entry", async () => {
    const res = (await tool.execute({
      action: "add_take_profit",
      symbol: "600519",
      entry_price: 1800,
      target_price: 1700,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.error).toContain("高于");
  });

  // ── add_trailing_stop ──

  it("adds a trailing stop order (percentage auto-normalized)", async () => {
    const res = (await tool.execute({
      action: "add_trailing_stop",
      symbol: "600519",
      entry_price: 1800,
      trail_pct: 5,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.message).toContain("跟踪止损");
    expect(res.trailPct).toBe("5.0%");
  });

  it("rejects trail_pct out of range", async () => {
    const res = (await tool.execute({
      action: "add_trailing_stop",
      symbol: "600519",
      entry_price: 1800,
      trail_pct: 0,
      quantity: 100,
    })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── list ──

  it("lists orders", async () => {
    await tool.execute({
      action: "add_stop_loss",
      symbol: "600519",
      entry_price: 1800,
      stop_price: 1700,
      quantity: 100,
    });
    const res = (await tool.execute({ action: "list" })) as Record<string, unknown>;
    expect(res.total).toBe(1);
    expect((res.orders as unknown[]).length).toBe(1);
  });

  // ── remove ──

  it("removes an order by ID", async () => {
    const addRes = (await tool.execute({
      action: "add_stop_loss",
      symbol: "600519",
      entry_price: 1800,
      stop_price: 1700,
      quantity: 100,
    })) as Record<string, unknown>;
    const res = (await tool.execute({
      action: "remove",
      order_id: addRes.id,
    })) as Record<string, unknown>;
    expect(res.message).toContain("已删除");
  });

  it("returns error for missing order_id", async () => {
    const res = (await tool.execute({ action: "remove" })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── check_now ──

  it("runs manual check cycle", async () => {
    const res = (await tool.execute({ action: "check_now" })) as Record<string, unknown>;
    expect(res.checked).toBe(0);
    expect(res.triggered).toBe(0);
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const res = (await tool.execute({ action: "invalid" })) as Record<string, unknown>;
    expect(res.error).toContain("未知操作");
  });
});
