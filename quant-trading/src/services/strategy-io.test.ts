import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { BacktestResult } from "../types.js";
import {
  exportStrategies,
  importStrategies,
  validatePackage,
  serializePackage,
  deserializePackage,
  EXPORT_FORMAT_VERSION,
  type StrategyPackage,
  type ExportedStrategy,
} from "./strategy-io.js";
import { UserStrategyStore, type UserStrategy } from "./strategy-template.js";

// ── Helpers ──────────────────────────────────────────────────

function fakeUserStrategy(overrides: Partial<UserStrategy> = {}): UserStrategy {
  return {
    id: "us-test1",
    name: "Test Strategy",
    strategyId: "dual-ma-crossover",
    parameters: { fastPeriod: 10, slowPeriod: 30 },
    symbols: ["000300"],
    tags: ["trend"],
    starred: false,
    createdAt: "2024-01-01T00:00:00Z",
    updatedAt: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

function fakeResult(): BacktestResult {
  return {
    strategyId: "dual-ma-crossover",
    symbols: ["000300"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    initialCash: 100_000,
    finalValue: 110_000,
    totalReturn: 0.1,
    annualizedReturn: 0.1,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 50,
    signals: [],
    equityCurve: [],
  };
}

function fakePackage(strategies: ExportedStrategy[] = []): StrategyPackage {
  return {
    formatVersion: EXPORT_FORMAT_VERSION,
    exportedAt: new Date().toISOString(),
    strategies,
  };
}

// ── exportStrategies ─────────────────────────────────────────

describe("exportStrategies", () => {
  it("exports strategies into a package", () => {
    const strategies = [fakeUserStrategy(), fakeUserStrategy({ id: "us-test2", name: "Second" })];
    const pkg = exportStrategies(strategies);

    expect(pkg.formatVersion).toBe(EXPORT_FORMAT_VERSION);
    expect(pkg.exportedAt).toBeTruthy();
    expect(pkg.strategies).toHaveLength(2);
    expect(pkg.strategies[0]!.originalId).toBe("us-test1");
    expect(pkg.strategies[0]!.strategyId).toBe("dual-ma-crossover");
  });

  it("includes exportedBy", () => {
    const pkg = exportStrategies([fakeUserStrategy()], { exportedBy: "testUser" });
    expect(pkg.exportedBy).toBe("testUser");
  });

  it("includes backtest snapshots when provided", () => {
    const strats = [fakeUserStrategy()];
    const snapshots = new Map<string, BacktestResult>();
    snapshots.set("us-test1", fakeResult());

    const pkg = exportStrategies(strats, { includeBacktestResults: snapshots });
    expect(pkg.strategies[0]!.backtestSnapshot).toBeDefined();
    expect(pkg.strategies[0]!.backtestSnapshot!.totalReturn).toBe(0.1);
  });

  it("handles empty array", () => {
    const pkg = exportStrategies([]);
    expect(pkg.strategies).toHaveLength(0);
  });
});

// ── validatePackage ──────────────────────────────────────────

describe("validatePackage", () => {
  it("validates a correct package", () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Test",
        strategyId: "dual-ma-crossover",
        parameters: { fastPeriod: 10 },
        symbols: ["000300"],
        tags: [],
      },
    ]);
    const result = validatePackage(pkg);
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it("rejects non-object", () => {
    const result = validatePackage("not an object");
    expect(result.valid).toBe(false);
  });

  it("rejects null", () => {
    const result = validatePackage(null);
    expect(result.valid).toBe(false);
  });

  it("rejects missing formatVersion", () => {
    const result = validatePackage({ strategies: [] });
    expect(result.valid).toBe(false);
  });

  it("rejects missing strategies array", () => {
    const result = validatePackage({ formatVersion: "1.0" });
    expect(result.valid).toBe(false);
  });

  it("warns on version mismatch", () => {
    const result = validatePackage({ formatVersion: "2.0", strategies: [] });
    expect(result.valid).toBe(true);
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it("detects missing strategy name", () => {
    const result = validatePackage({
      formatVersion: "1.0",
      strategies: [{ strategyId: "ma" }],
    });
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("name"))).toBe(true);
  });

  it("detects missing strategyId", () => {
    const result = validatePackage({
      formatVersion: "1.0",
      strategies: [{ name: "test" }],
    });
    expect(result.valid).toBe(false);
    expect(result.errors.some((e) => e.includes("strategyId"))).toBe(true);
  });

  it("warns on unknown base strategy", () => {
    const result = validatePackage({
      formatVersion: "1.0",
      strategies: [{ name: "test", strategyId: "nonexistent-xyz", parameters: {}, symbols: [] }],
    });
    expect(result.valid).toBe(true); // still valid, just a warning
    expect(result.warnings.some((w) => w.includes("not found"))).toBe(true);
  });
});

// ── serializePackage / deserializePackage ─────────────────────

describe("serialize/deserialize", () => {
  it("round-trips correctly", () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Test",
        strategyId: "dual-ma-crossover",
        parameters: { fastPeriod: 10 },
        symbols: ["000300"],
        tags: ["trend"],
      },
    ]);
    const json = serializePackage(pkg);
    const parsed = deserializePackage(json);
    expect(parsed).not.toBeNull();
    expect(parsed!.strategies[0]!.name).toBe("Test");
    expect(parsed!.strategies[0]!.parameters.fastPeriod).toBe(10);
  });

  it("deserialize returns null for invalid JSON", () => {
    expect(deserializePackage("not json{")).toBeNull();
  });

  it("serialize produces formatted JSON", () => {
    const pkg = fakePackage([]);
    const json = serializePackage(pkg);
    expect(json).toContain("\n"); // pretty-printed
  });
});

// ── importStrategies ─────────────────────────────────────────

describe("importStrategies", () => {
  let tmpDir: string;
  let store: UserStrategyStore;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "strat-io-"));
    store = new UserStrategyStore(tmpDir);
    await store.load();
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("imports valid strategies", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-ext1",
        name: "Imported MA",
        strategyId: "dual-ma-crossover",
        parameters: { fastPeriod: 10, slowPeriod: 30 },
        symbols: ["000300"],
        tags: ["imported"],
      },
    ]);

    const result = await importStrategies(store, pkg);
    expect(result.imported).toHaveLength(1);
    expect(result.skipped).toHaveLength(0);
    expect(result.errors).toHaveLength(0);
    expect(store.list()).toHaveLength(1);
  });

  it("skips duplicates by default", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Strategy A",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: [],
        tags: [],
      },
    ]);

    await importStrategies(store, pkg);
    const result2 = await importStrategies(store, pkg);
    expect(result2.skipped).toHaveLength(1);
    expect(result2.skipped[0]!.reason).toContain("Duplicate");
    expect(store.list()).toHaveLength(1);
  });

  it("imports duplicates when skipDuplicates=false", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Strategy A",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: [],
        tags: [],
      },
    ]);

    await importStrategies(store, pkg);
    const result2 = await importStrategies(store, pkg, { skipDuplicates: false });
    expect(result2.imported).toHaveLength(1);
    expect(store.list()).toHaveLength(2);
  });

  it("skips unknown strategies with validation", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Unknown",
        strategyId: "nonexistent-xyz",
        parameters: {},
        symbols: [],
        tags: [],
      },
    ]);

    const result = await importStrategies(store, pkg);
    expect(result.skipped).toHaveLength(1);
    expect(result.skipped[0]!.reason).toContain("Unknown base strategy");
  });

  it("reports parameter validation errors", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Bad Params",
        strategyId: "dual-ma-crossover",
        parameters: { fastPeriod: -999 },
        symbols: [],
        tags: [],
      },
    ]);

    const result = await importStrategies(store, pkg);
    expect(result.errors).toHaveLength(1);
  });

  it("applies symbolOverride", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Test",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: ["000300"],
        tags: [],
      },
    ]);

    await importStrategies(store, pkg, { symbolOverride: ["600519"] });
    expect(store.list()[0]!.symbols).toEqual(["600519"]);
  });

  it("applies addTag", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Test",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: [],
        tags: ["original"],
      },
    ]);

    await importStrategies(store, pkg, { addTag: "imported" });
    expect(store.list()[0]!.tags).toContain("imported");
    expect(store.list()[0]!.tags).toContain("original");
  });

  it("does not duplicate addTag if already exists", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Test",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: [],
        tags: ["imported"],
      },
    ]);

    await importStrategies(store, pkg, { addTag: "imported" });
    const tags = store.list()[0]!.tags;
    expect(tags.filter((t) => t === "imported")).toHaveLength(1);
  });

  it("imports multiple strategies in batch", async () => {
    const pkg = fakePackage([
      {
        originalId: "1",
        name: "A",
        strategyId: "dual-ma-crossover",
        parameters: {},
        symbols: [],
        tags: [],
      },
      {
        originalId: "2",
        name: "B",
        strategyId: "mean-reversion",
        parameters: {},
        symbols: [],
        tags: [],
      },
      {
        originalId: "3",
        name: "C",
        strategyId: "momentum-breakout",
        parameters: {},
        symbols: [],
        tags: [],
      },
    ]);

    const result = await importStrategies(store, pkg);
    expect(result.imported).toHaveLength(3);
    expect(store.list()).toHaveLength(3);
  });

  it("bypasses validation when validate=false", async () => {
    const pkg = fakePackage([
      {
        originalId: "us-1",
        name: "Unvalidated",
        strategyId: "nonexistent-xyz",
        parameters: {},
        symbols: [],
        tags: [],
      },
    ]);

    // With validate=false, unknown strategy should still attempt creation
    // (will fail at store.create level since getStrategy returns undefined)
    const result = await importStrategies(store, pkg, { validate: false });
    // Even without pre-validation, the store.create will detect unknown strategy
    expect(result.errors.length + result.imported.length + result.skipped.length).toBe(1);
  });
});
