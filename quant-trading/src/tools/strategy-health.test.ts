import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrategyHealthMonitor } from "../services/strategy-health.js";
import { createStrategyHealthTool } from "./strategy-health.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

describe("strategy_health tool", () => {
  let tmpDir: string;
  let monitor: StrategyHealthMonitor;
  let tool: ReturnType<typeof createStrategyHealthTool>;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "health-tool-"));
    monitor = new StrategyHealthMonitor(tmpDir);
    tool = createStrategyHealthTool(fakeApi, {} as never, monitor);
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  // ── check_all (empty) ──

  it("check_all with no data", async () => {
    const raw = await tool.execute({ action: "check_all" });
    const res = JSON.parse(raw as string);
    expect(res.message).toContain("暂无");
  });

  // ── record signals then check_all ──

  it("check_all after recording signals", async () => {
    monitor.recordTrade("s1", 500, new Date().toISOString());
    const raw = await tool.execute({ action: "check_all" });
    const res = JSON.parse(raw as string);
    expect(res.strategies.length).toBe(1);
    expect(res.strategies[0].strategyId).toBe("s1");
  });

  // ── check single strategy ──

  it("check a specific strategy", async () => {
    monitor.recordTrade("s1", 100, new Date().toISOString());
    const raw = await tool.execute({ action: "check", strategyId: "s1" });
    const res = JSON.parse(raw as string);
    expect(res.strategyId).toBe("s1");
    expect(res.level).toBeDefined();
  });

  it("check unknown strategy returns error", async () => {
    const raw = await tool.execute({ action: "check", strategyId: "nope" });
    const res = JSON.parse(raw as string);
    expect(res.error).toContain("不在追踪");
  });

  it("check without strategyId returns error", async () => {
    const raw = await tool.execute({ action: "check" });
    const res = JSON.parse(raw as string);
    expect(res.error).toBeDefined();
  });

  // ── list ──

  it("list tracked strategies", async () => {
    monitor.recordTrade("s1", 100, new Date().toISOString());
    const raw = await tool.execute({ action: "list" });
    const res = JSON.parse(raw as string);
    expect(res.count).toBe(1);
    expect(res.strategies).toContain("s1");
  });

  // ── remove ──

  it("removes a tracked strategy", async () => {
    monitor.recordTrade("s1", 100, new Date().toISOString());
    const raw = await tool.execute({ action: "remove", strategyId: "s1" });
    const res = JSON.parse(raw as string);
    expect(res.success).toBe(true);
    expect(res.message).toContain("停止追踪");
  });

  it("remove non-existent strategy", async () => {
    const raw = await tool.execute({ action: "remove", strategyId: "nope" });
    const res = JSON.parse(raw as string);
    expect(res.success).toBe(false);
  });

  it("remove without strategyId returns error", async () => {
    const raw = await tool.execute({ action: "remove" });
    const res = JSON.parse(raw as string);
    expect(res.error).toBeDefined();
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const raw = await tool.execute({ action: "invalid" });
    const res = JSON.parse(raw as string);
    expect(res.error).toContain("未知操作");
  });
});
