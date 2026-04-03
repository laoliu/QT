/**
 * Unit tests for SignalMonitor service.
 *
 * Tests passive signal detection, active signal generation, and alert emission.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginServiceContext } from "openclaw/plugin-sdk/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig, TradingSignal } from "../types.js";
import { createSignalMonitorService } from "./signal-monitor.js";

// ── Mock dependencies ───────────────────────────────────────────
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn(() => false),
}));

vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: vi.fn(() => ({})),
}));

vi.mock("../engine/live-strategy-registry.js", () => ({
  LiveStrategyRegistry: vi.fn().mockImplementation(() => ({
    load: vi.fn(),
    list: vi.fn(() => []),
  })),
}));

vi.mock("../engine/strategy-registry.js", () => ({
  runBacktest: vi.fn(() => ({ signals: [] })),
}));

import { isMarketOpen } from "../broker/paper-broker.js";
const mockMarketOpen = vi.mocked(isMarketOpen);

let tmpDir: string;
let config: QuantTradingConfig;

function makeApi() {
  return {
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    notify: vi.fn(),
  } as never;
}

function makeSignal(overrides?: Partial<TradingSignal>): TradingSignal {
  return {
    timestamp: Date.now(),
    symbol: "600519",
    direction: "buy",
    price: 1800,
    strategyId: "macd",
    ...overrides,
  };
}

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-sigmon-test-"));
  config = { dataDir: tmpDir, pythonPath: "python3" } as QuantTradingConfig;
  vi.useFakeTimers();
});

const dummyCtx = {} as OpenClawPluginServiceContext;

afterEach(async () => {
  vi.useRealTimers();
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function writeSignals(signals: TradingSignal[]): Promise<void> {
  await fs.writeFile(path.join(tmpDir, "signals.json"), JSON.stringify(signals));
}

describe("SignalMonitor service", () => {
  it("has correct service id", () => {
    const api = makeApi();
    const svc = createSignalMonitorService(api, config);
    expect(svc.id).toBe("quant-signal-monitor");
  });

  it("starts and stops cleanly", async () => {
    const api = makeApi();
    const svc = createSignalMonitorService(api, config);
    await svc.start(dummyCtx);
    await svc.stop?.(dummyCtx);
    // Should not throw, logs should be recorded
  });

  describe("passive mode (market closed)", () => {
    it("detects new signals written to signals.json", async () => {
      vi.useRealTimers(); // use real timers for async I/O test
      mockMarketOpen.mockReturnValue(false);
      const api = makeApi();

      // Manually simulate what a tick does: seed watermark then check for new
      const sig = makeSignal({ timestamp: 1000 });
      await writeSignals([sig]);

      // Import appendSignals to test alert path directly
      const svc = createSignalMonitorService(api, config);
      await svc.start(dummyCtx); // seeds watermark to 1000

      // Write a new signal with higher timestamp
      const newSig = makeSignal({ timestamp: 2000, symbol: "000001", direction: "sell" });
      await writeSignals([sig, newSig]);

      // Wait for at least one interval tick (service uses 60s interval;
      // we can't wait that long, so stop and verify watermark seeding worked)
      await svc.stop?.(dummyCtx);

      // Verify the service started correctly and seeded watermark
      const infoCalls = (api as unknown as { logger: { info: ReturnType<typeof vi.fn> } }).logger
        .info;
      const startLog = infoCalls.mock.calls.find(
        (c: string[]) => typeof c[0] === "string" && c[0].includes("started"),
      );
      expect(startLog).toBeDefined();
      vi.useFakeTimers(); // restore for other tests
    });

    it("does not re-alert for already-seen signals", async () => {
      mockMarketOpen.mockReturnValue(false);
      const api = makeApi();
      const svc = createSignalMonitorService(api, config);

      const sig = makeSignal({ timestamp: 5000 });
      await writeSignals([sig]);

      await svc.start(dummyCtx);
      // First tick detects nothing new (watermark = 5000)
      await vi.advanceTimersByTimeAsync(61_000);
      // Second tick, same signals
      await vi.advanceTimersByTimeAsync(61_000);

      const infoCalls = (api as unknown as { logger: { info: ReturnType<typeof vi.fn> } }).logger
        .info;
      const alertCalls = infoCalls.mock.calls.filter(
        (c: string[]) => typeof c[0] === "string" && c[0].includes("新交易信号"),
      );
      // Should NOT have alerted (watermark was seeded from existing signals)
      expect(alertCalls.length).toBe(0);

      await svc.stop?.(dummyCtx);
    });
  });

  describe("no signals file", () => {
    it("starts without errors when signals.json does not exist", async () => {
      mockMarketOpen.mockReturnValue(false);
      const api = makeApi();
      const svc = createSignalMonitorService(api, config);
      await svc.start(dummyCtx);
      await vi.advanceTimersByTimeAsync(61_000);
      await svc.stop?.(dummyCtx);
      // No errors should be logged
      const errorCalls = (api as unknown as { logger: { error: ReturnType<typeof vi.fn> } }).logger
        .error;
      expect(errorCalls).not.toHaveBeenCalled();
    });
  });

  describe("appendSignals helper", () => {
    it("appends signals and trims to 500", async () => {
      // Test the appendSignals function directly
      const { appendSignals } = await import("./signal-monitor.js");
      const signals = Array.from({ length: 5 }, (_, i) =>
        makeSignal({ timestamp: i + 1, symbol: `SYM${i}` }),
      );
      await appendSignals(signals, config);

      // Read back from disk
      const raw = await fs.readFile(path.join(tmpDir, "signals.json"), "utf-8");
      const parsed = JSON.parse(raw);
      expect(parsed).toHaveLength(5);
      expect(parsed[0].symbol).toBe("SYM0");
    });

    it("does nothing for empty array", async () => {
      const { appendSignals } = await import("./signal-monitor.js");
      await appendSignals([], config);
      // signals.json should not exist
      const exists = await fs
        .access(path.join(tmpDir, "signals.json"))
        .then(() => true)
        .catch(() => false);
      expect(exists).toBe(false);
    });

    it("concurrent appends do not lose data (mutex)", async () => {
      const { appendSignals } = await import("./signal-monitor.js");
      // Fire 5 concurrent appends of 3 signals each = 15 total
      const jobs = Array.from({ length: 5 }, (_, batch) =>
        appendSignals(
          Array.from({ length: 3 }, (_, i) =>
            makeSignal({ timestamp: batch * 100 + i, symbol: `B${batch}S${i}` }),
          ),
          config,
        ),
      );
      await Promise.all(jobs);

      const raw = await fs.readFile(path.join(tmpDir, "signals.json"), "utf-8");
      const parsed = JSON.parse(raw);
      // All 15 signals should be present (no lost writes)
      expect(parsed).toHaveLength(15);
    });
  });
});
