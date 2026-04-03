import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { StrategyDefinition } from "../types.js";
import {
  STRATEGY_TEMPLATES,
  UserStrategyStore,
  validateParameters,
  mergeWithDefaults,
  type StrategyTemplate,
  type UserStrategy,
} from "./strategy-template.js";

// ── Helpers ──────────────────────────────────────────────────

const NUM_PARAM = (name: string, def: number, min?: number, max?: number, step?: number) => ({
  name,
  type: "number" as const,
  default: def,
  min,
  max,
  step,
});

const BOOL_PARAM = (name: string, def: boolean) => ({
  name,
  type: "boolean" as const,
  default: def,
});

const STR_PARAM = (name: string, def: string) => ({ name, type: "string" as const, default: def });

const MOCK_STRATEGY: StrategyDefinition = {
  id: "test-strat",
  name: "Test Strategy",
  description: "test",
  version: "1.0.0",
  engine: "builtin",
  entryFile: "test.ts",
  parameters: [
    NUM_PARAM("period", 20, 2, 200, 1),
    NUM_PARAM("threshold", 0.5, 0, 1),
    BOOL_PARAM("useStop", true),
    STR_PARAM("mode", "normal"),
  ],
};

// ── validateParameters ───────────────────────────────────────

describe("validateParameters", () => {
  it("returns no errors for valid params", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: 20, threshold: 0.5 });
    expect(errors).toHaveLength(0);
  });

  it("detects unknown parameters", () => {
    const errors = validateParameters(MOCK_STRATEGY, { unknown: 42 });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.param).toBe("unknown");
    expect(errors[0]!.message).toContain("Unknown parameter");
  });

  it("detects wrong type (number expected)", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: "abc" });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.message).toContain("must be a number");
  });

  it("detects NaN", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: NaN });
    expect(errors).toHaveLength(1);
  });

  it("detects value below min", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: 1 });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.message).toContain(">= 2");
  });

  it("detects value above max", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: 999 });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.message).toContain("<= 200");
  });

  it("validates boolean params", () => {
    const errors = validateParameters(MOCK_STRATEGY, { useStop: "yes" });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.message).toContain("must be a boolean");
  });

  it("validates string params", () => {
    const errors = validateParameters(MOCK_STRATEGY, { mode: 123 });
    expect(errors).toHaveLength(1);
    expect(errors[0]!.message).toContain("must be a string");
  });

  it("allows boundary values", () => {
    const errors = validateParameters(MOCK_STRATEGY, { period: 2, threshold: 0 });
    expect(errors).toHaveLength(0);
  });

  it("returns multiple errors at once", () => {
    const errors = validateParameters(MOCK_STRATEGY, {
      period: -1,
      threshold: "bad",
      unknown: true,
    });
    expect(errors.length).toBeGreaterThanOrEqual(3);
  });
});

// ── mergeWithDefaults ────────────────────────────────────────

describe("mergeWithDefaults", () => {
  it("fills missing params with defaults", () => {
    const merged = mergeWithDefaults(MOCK_STRATEGY, {});
    expect(merged.period).toBe(20);
    expect(merged.threshold).toBe(0.5);
    expect(merged.useStop).toBe(true);
    expect(merged.mode).toBe("normal");
  });

  it("overrides defaults with provided values", () => {
    const merged = mergeWithDefaults(MOCK_STRATEGY, { period: 50 });
    expect(merged.period).toBe(50);
    expect(merged.threshold).toBe(0.5); // still default
  });

  it("keeps extra keys from overrides", () => {
    const merged = mergeWithDefaults(MOCK_STRATEGY, { extra: "x" });
    expect(merged.extra).toBe("x");
  });
});

// ── STRATEGY_TEMPLATES catalog ───────────────────────────────

describe("STRATEGY_TEMPLATES catalog", () => {
  it("has at least 5 templates", () => {
    expect(STRATEGY_TEMPLATES.length).toBeGreaterThanOrEqual(5);
  });

  it("all templates have required fields", () => {
    for (const t of STRATEGY_TEMPLATES) {
      expect(t.id).toBeTruthy();
      expect(t.name).toBeTruthy();
      expect(t.baseStrategyId).toBeTruthy();
      expect(t.category).toBeTruthy();
    }
  });

  it("template IDs are unique", () => {
    const ids = STRATEGY_TEMPLATES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("templates cover multiple categories", () => {
    const categories = new Set(STRATEGY_TEMPLATES.map((t) => t.category));
    expect(categories.size).toBeGreaterThanOrEqual(3);
  });
});

// ── UserStrategyStore ────────────────────────────────────────

describe("UserStrategyStore", () => {
  let tmpDir: string;
  let store: UserStrategyStore;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "us-store-"));
    store = new UserStrategyStore(tmpDir);
    await store.load();
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("starts empty", () => {
    expect(store.list()).toHaveLength(0);
  });

  it("creates a basic strategy", async () => {
    const { strategy, errors } = await store.create({
      name: "My MA",
      strategyId: "dual-ma-crossover",
      parameters: { fastPeriod: 10, slowPeriod: 30 },
      symbols: ["000300"],
    });
    expect(errors).toBeUndefined();
    expect(strategy).toBeDefined();
    expect(strategy!.name).toBe("My MA");
    expect(strategy!.strategyId).toBe("dual-ma-crossover");
    expect(strategy!.symbols).toEqual(["000300"]);
  });

  it("rejects unknown base strategy", async () => {
    const { errors } = await store.create({
      name: "Bad",
      strategyId: "nonexistent-strategy-xyz",
    });
    expect(errors).toBeDefined();
    expect(errors![0]!.param).toBe("strategyId");
  });

  it("validates parameters on create", async () => {
    const { errors } = await store.create({
      name: "Bad Params",
      strategyId: "dual-ma-crossover",
      parameters: { fastPeriod: -100 },
    });
    expect(errors).toBeDefined();
    expect(errors!.some((e) => e.param === "fastPeriod")).toBe(true);
  });

  it("persists and reloads strategies", async () => {
    await store.create({
      name: "Persistent",
      strategyId: "dual-ma-crossover",
    });

    const store2 = new UserStrategyStore(tmpDir);
    await store2.load();
    expect(store2.list()).toHaveLength(1);
    expect(store2.list()[0]!.name).toBe("Persistent");
  });

  it("gets strategy by ID", async () => {
    const { strategy } = await store.create({
      name: "Find Me",
      strategyId: "dual-ma-crossover",
    });
    const found = store.get(strategy!.id);
    expect(found).toBeDefined();
    expect(found!.name).toBe("Find Me");
  });

  it("returns undefined for unknown ID", () => {
    expect(store.get("nonexistent")).toBeUndefined();
  });

  it("updates strategy name and description", async () => {
    const { strategy } = await store.create({
      name: "Original",
      strategyId: "dual-ma-crossover",
    });
    const { strategy: updated } = await store.update(strategy!.id, {
      name: "Updated",
      description: "new desc",
    });
    expect(updated!.name).toBe("Updated");
    expect(updated!.description).toBe("new desc");
  });

  it("validates parameters on update", async () => {
    const { strategy } = await store.create({
      name: "Will Update",
      strategyId: "dual-ma-crossover",
    });
    const { errors } = await store.update(strategy!.id, {
      parameters: { fastPeriod: -1 },
    });
    expect(errors).toBeDefined();
  });

  it("returns error for updating nonexistent", async () => {
    const { errors } = await store.update("bad-id", { name: "X" });
    expect(errors).toBeDefined();
  });

  it("deletes strategy", async () => {
    const { strategy } = await store.create({
      name: "To Delete",
      strategyId: "dual-ma-crossover",
    });
    expect(await store.delete(strategy!.id)).toBe(true);
    expect(store.list()).toHaveLength(0);
  });

  it("delete returns false for nonexistent", async () => {
    expect(await store.delete("bad-id")).toBe(false);
  });

  it("filters by tag", async () => {
    await store.create({ name: "A", strategyId: "dual-ma-crossover", tags: ["trend"] });
    await store.create({ name: "B", strategyId: "mean-reversion", tags: ["range"] });
    expect(store.list({ tag: "trend" })).toHaveLength(1);
    expect(store.list({ tag: "range" })).toHaveLength(1);
    expect(store.list({ tag: "other" })).toHaveLength(0);
  });

  it("filters by strategyId", async () => {
    await store.create({ name: "A", strategyId: "dual-ma-crossover" });
    await store.create({ name: "B", strategyId: "mean-reversion" });
    expect(store.list({ strategyId: "dual-ma-crossover" })).toHaveLength(1);
  });

  it("filters by starred", async () => {
    const { strategy } = await store.create({ name: "Star", strategyId: "dual-ma-crossover" });
    await store.update(strategy!.id, { starred: true });
    expect(store.list({ starred: true })).toHaveLength(1);
    expect(store.list({ starred: false })).toHaveLength(0);
  });

  it("createFromTemplate creates with template params", async () => {
    const { strategy, errors } = await store.createFromTemplate("conservative-trend");
    expect(errors).toBeUndefined();
    expect(strategy).toBeDefined();
    expect(strategy!.strategyId).toBe("dual-ma-crossover");
    expect(strategy!.parameters.fastPeriod).toBe(20);
    expect(strategy!.parameters.slowPeriod).toBe(60);
    expect(strategy!.tags).toContain("trend");
  });

  it("createFromTemplate allows param overrides", async () => {
    const { strategy } = await store.createFromTemplate("conservative-trend", {
      paramOverrides: { fastPeriod: 25 },
    });
    expect(strategy!.parameters.fastPeriod).toBe(25);
  });

  it("createFromTemplate rejects unknown template", async () => {
    const { errors } = await store.createFromTemplate("nonexistent-template");
    expect(errors).toBeDefined();
    expect(errors![0]!.param).toBe("templateId");
  });

  it("createFromTemplate allows custom name", async () => {
    const { strategy } = await store.createFromTemplate("conservative-trend", {
      name: "My Custom",
    });
    expect(strategy!.name).toBe("My Custom");
  });

  it("supports symbols on createFromTemplate", async () => {
    const { strategy } = await store.createFromTemplate("conservative-trend", {
      symbols: ["600519"],
    });
    expect(strategy!.symbols).toEqual(["600519"]);
  });
});
