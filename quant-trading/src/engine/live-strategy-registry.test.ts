/**
 * Unit tests for LiveStrategyRegistry.
 *
 * Tests persistence, CRUD operations, and enable/disable logic.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { LiveStrategyRegistry } from "./live-strategy-registry.js";

let tmpDir: string;
let config: QuantTradingConfig;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-lsreg-test-"));
  config = { dataDir: tmpDir } as QuantTradingConfig;
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("LiveStrategyRegistry", () => {
  it("starts with empty list when no file exists", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();
    expect(reg.list()).toEqual([]);
  });

  it("adds a strategy and persists to disk", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    const entry = await reg.add("macd", ["600519", "000001"]);
    expect(entry.strategyId).toBe("macd");
    expect(entry.symbols).toEqual(["600519", "000001"]);
    expect(entry.enabled).toBe(true);
    expect(entry.id).toMatch(/^ls-/);

    // Verify persistence
    const raw = await fs.readFile(path.join(tmpDir, "live-strategies.json"), "utf-8");
    const parsed = JSON.parse(raw);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].strategyId).toBe("macd");
  });

  it("adds strategy with options", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    const entry = await reg.add("bollinger", ["600519"], {
      parameters: { period: 30 },
      interval: "5m",
      maxPositionPct: 0.2,
      maxOrderValue: 100000,
    });

    expect(entry.parameters).toEqual({ period: 30 });
    expect(entry.interval).toBe("5m");
    expect(entry.maxPositionPct).toBe(0.2);
    expect(entry.maxOrderValue).toBe(100000);
  });

  it("lists all strategies", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    await reg.add("macd", ["600519"]);
    await reg.add("rsi", ["000001"]);

    expect(reg.list()).toHaveLength(2);
  });

  it("lists only enabled strategies", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    const a = await reg.add("macd", ["600519"]);
    await reg.add("rsi", ["000001"]);
    await reg.setEnabled(a.id, false);

    const enabled = reg.list(true);
    expect(enabled).toHaveLength(1);
    expect(enabled[0].strategyId).toBe("rsi");
  });

  it("enables and disables a strategy", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    const entry = await reg.add("macd", ["600519"]);
    expect(entry.enabled).toBe(true);

    const result = await reg.setEnabled(entry.id, false);
    expect(result).toBe(true);

    const list = reg.list();
    expect(list[0].enabled).toBe(false);

    // Returns false for non-existent ID
    const bad = await reg.setEnabled("nonexistent", true);
    expect(bad).toBe(false);
  });

  it("removes a strategy", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();

    const entry = await reg.add("macd", ["600519"]);
    await reg.add("rsi", ["000001"]);

    const removed = await reg.remove(entry.id);
    expect(removed).toBe(true);
    expect(reg.list()).toHaveLength(1);

    // Returns false for non-existent
    const bad = await reg.remove("nonexistent");
    expect(bad).toBe(false);
  });

  it("survives reload from disk", async () => {
    const reg1 = new LiveStrategyRegistry(config);
    await reg1.load();
    await reg1.add("macd", ["600519"], { parameters: { fast: 10 } });
    await reg1.add("rsi", ["000001"]);

    // Create new instance and load from disk
    const reg2 = new LiveStrategyRegistry(config);
    await reg2.load();
    expect(reg2.list()).toHaveLength(2);
    expect(reg2.list()[0].strategyId).toBe("macd");
    expect(reg2.list()[0].parameters).toEqual({ fast: 10 });
  });

  it("returns independent copies from list()", async () => {
    const reg = new LiveStrategyRegistry(config);
    await reg.load();
    await reg.add("macd", ["600519"]);

    const list1 = reg.list();
    list1.push({ id: "fake", strategyId: "fake", symbols: [], enabled: false, addedAt: "" });

    // Original should be unaffected
    expect(reg.list()).toHaveLength(1);
  });
});
