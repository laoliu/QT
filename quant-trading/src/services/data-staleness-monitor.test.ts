/**
 * Tests for DataStalenessMonitor — quote freshness tracking and alerting.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataStalenessMonitor } from "./data-staleness-monitor.js";
import type { StalenessReport } from "./data-staleness-monitor.js";

function makeLogger() {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}

function makeNotifier() {
  return { notify: vi.fn() };
}

describe("DataStalenessMonitor", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Lifecycle ─────────────────────────────────────────────────

  describe("lifecycle", () => {
    it("starts and stops without error", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.start();
      mon.stop();
    });

    it("does not start when disabled", () => {
      const logger = makeLogger();
      const mon = new DataStalenessMonitor({ enabled: false }, logger);
      mon.start();
      // No "started" log when disabled
      expect(logger.info).not.toHaveBeenCalled();
      mon.stop();
    });

    it("is idempotent on double start", () => {
      const logger = makeLogger();
      const mon = new DataStalenessMonitor({}, logger);
      mon.start();
      mon.start(); // second call is no-op
      expect(logger.info).toHaveBeenCalledTimes(1); // only one "started" message
      mon.stop();
    });

    it("runs periodic checks at configured interval", () => {
      const mon = new DataStalenessMonitor({ checkIntervalMs: 5000 }, makeLogger());
      const checkSpy = vi.spyOn(mon, "check");
      mon.start();

      vi.advanceTimersByTime(5000);
      expect(checkSpy).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(5000);
      expect(checkSpy).toHaveBeenCalledTimes(2);

      mon.stop();
    });
  });

  // ── recordQuote ───────────────────────────────────────────────

  describe("recordQuote", () => {
    it("records quote timestamp for symbol", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.recordQuote("600519");
      const report = mon.getReport();
      expect(report.totalSymbols).toBe(1);
      expect(report.symbols[0]!.symbol).toBe("600519");
      expect(report.symbols[0]!.status).toBe("fresh");
    });

    it("updates timestamp on subsequent quotes", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.recordQuote("AAPL");

      // Advance 10s, record again
      vi.advanceTimersByTime(10_000);
      mon.recordQuote("AAPL");

      const report = mon.getReport();
      expect(report.symbols[0]!.ageMs).toBeLessThan(1000);
    });

    it("clears fired alerts on fresh data", () => {
      const logger = makeLogger();
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000, checkIntervalMs: 1000 },
        logger,
        notifier,
      );

      mon.recordQuote("SH600519");
      mon.start();

      // Let it go stale past warning
      vi.advanceTimersByTime(6000);
      // Check fires a warning
      expect(notifier.notify).toHaveBeenCalledTimes(1);

      // Fresh quote arrives — clears the fired alert
      mon.recordQuote("SH600519");
      expect(logger.info).toHaveBeenCalledWith("Data feed restored for SH600519");

      // Keep sending quotes frequently so it stays fresh
      notifier.notify.mockClear();
      for (let i = 0; i < 6; i++) {
        vi.advanceTimersByTime(1000);
        mon.recordQuote("SH600519");
      }
      // Data stayed fresh → no new alert
      expect(notifier.notify).not.toHaveBeenCalled();

      mon.stop();
    });
  });

  // ── registerSymbols ───────────────────────────────────────────

  describe("registerSymbols", () => {
    it("pre-registers symbols with no data", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.registerSymbols(["AAPL", "GOOG"]);

      const report = mon.getReport();
      expect(report.totalSymbols).toBe(2);
      // Never received → ageMs = Infinity (never-received sentinel)
      expect(report.symbols[0]!.ageMs).toBe(Infinity);
      // Since never received, age is infinite → critical
      expect(report.symbols[0]!.status).toBe("critical");
    });

    it("does not overwrite existing timestamps", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.recordQuote("AAPL");
      mon.registerSymbols(["AAPL", "GOOG"]);

      const report = mon.getReport();
      const aapl = report.symbols.find((s) => s.symbol === "AAPL")!;
      expect(aapl.status).toBe("fresh"); // existing ts preserved
      const goog = report.symbols.find((s) => s.symbol === "GOOG")!;
      expect(goog.status).toBe("critical"); // new, never received
    });
  });

  // ── getReport ─────────────────────────────────────────────────

  describe("getReport", () => {
    it("returns no_data when no symbols tracked", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      const report = mon.getReport();
      expect(report.overallStatus).toBe("no_data");
      expect(report.totalSymbols).toBe(0);
    });

    it("marks symbols as fresh when recently received", () => {
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 60_000, criticalThresholdMs: 300_000 },
        makeLogger(),
      );
      mon.recordQuote("A");
      mon.recordQuote("B");

      vi.advanceTimersByTime(5000); // 5s later
      const report = mon.getReport();
      expect(report.overallStatus).toBe("fresh");
      expect(report.freshCount).toBe(2);
      expect(report.warningCount).toBe(0);
    });

    it("detects warning-level staleness", () => {
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 10_000, criticalThresholdMs: 60_000 },
        makeLogger(),
      );
      mon.recordQuote("A");

      vi.advanceTimersByTime(15_000); // 15s > 10s warning
      const report = mon.getReport();
      expect(report.symbols[0]!.status).toBe("warning");
      expect(report.overallStatus).toBe("warning");
    });

    it("detects critical-level staleness", () => {
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 10_000, criticalThresholdMs: 30_000 },
        makeLogger(),
      );
      mon.recordQuote("A");

      vi.advanceTimersByTime(35_000); // 35s > 30s critical
      const report = mon.getReport();
      expect(report.symbols[0]!.status).toBe("critical");
      expect(report.overallStatus).toBe("critical");
    });

    it("reports mixed statuses correctly", () => {
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 10_000, criticalThresholdMs: 30_000 },
        makeLogger(),
      );

      mon.recordQuote("FRESH");
      vi.advanceTimersByTime(5000);

      // B was received 15s ago (warning)
      mon.recordQuote("STALE_WARN");
      vi.advanceTimersByTime(10_000); // now FRESH is 15s old, STALE_WARN is 10s
      // Actually at this point FRESH is 15s (warning), STALE_WARN is 10s (warning)
      // Let's record FRESH again to keep it fresh
      mon.recordQuote("FRESH");

      vi.advanceTimersByTime(15_000); // STALE_WARN is 25s (warning), FRESH is 15s (warning)
      mon.recordQuote("FRESH"); // keep FRESH fresh

      const report = mon.getReport();
      const fresh = report.symbols.find((s) => s.symbol === "FRESH");
      const stale = report.symbols.find((s) => s.symbol === "STALE_WARN");
      expect(fresh!.status).toBe("fresh");
      expect(stale!.status).toBe("warning");
      expect(report.overallStatus).toBe("warning"); // worst is warning
    });
  });

  // ── check & alerting ──────────────────────────────────────────

  describe("check & alerting", () => {
    it("fires warning alert on stale symbol", () => {
      const logger = makeLogger();
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 30_000 },
        logger,
        notifier,
      );

      mon.recordQuote("X");
      vi.advanceTimersByTime(6000);
      mon.check();

      expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("数据延迟"));
      expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("X"));
      expect(notifier.notify).toHaveBeenCalledTimes(1);
    });

    it("fires critical alert on very stale symbol", () => {
      const logger = makeLogger();
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000 },
        logger,
        notifier,
      );

      mon.recordQuote("Y");
      vi.advanceTimersByTime(11_000);
      mon.check();

      expect(logger.error).toHaveBeenCalledWith(expect.stringContaining("数据中断"));
      expect(notifier.notify).toHaveBeenCalledWith(expect.stringContaining("Y"));
    });

    it("deduplicates repeated alerts for the same symbol", () => {
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 30_000 },
        makeLogger(),
        notifier,
      );

      mon.recordQuote("Z");
      vi.advanceTimersByTime(6000);
      mon.check();
      mon.check();
      mon.check();

      // Only one warning alert despite 3 checks
      expect(notifier.notify).toHaveBeenCalledTimes(1);
    });

    it("fires all-critical alert when every symbol is critical", () => {
      const logger = makeLogger();
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000 },
        logger,
        notifier,
      );

      mon.recordQuote("A");
      mon.recordQuote("B");
      vi.advanceTimersByTime(11_000);
      mon.check();

      // Per-symbol critical alerts + all-critical alert
      expect(notifier.notify).toHaveBeenCalledWith(expect.stringContaining("所有行情数据源中断"));
    });

    it("clears all-critical when some symbols become fresh", () => {
      const logger = makeLogger();
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000 },
        logger,
        notifier,
      );

      mon.recordQuote("A");
      mon.recordQuote("B");
      vi.advanceTimersByTime(11_000);
      mon.check();

      // all_critical was fired
      expect(notifier.notify).toHaveBeenCalledWith(expect.stringContaining("所有行情数据源中断"));

      // Now A gets fresh data
      mon.recordQuote("A");
      notifier.notify.mockClear();
      vi.advanceTimersByTime(11_000);
      mon.check();

      // all_critical should NOT re-fire since A was fresh (but may become stale again)
      // The new check will find A critical again, but the per-symbol alert for A
      // was already cleared by recordQuote, so it fires again. But all_critical
      // was cleared when A got fresh data.
      const allCritCalls = notifier.notify.mock.calls.filter((c: string[]) =>
        c[0].includes("所有行情数据源中断"),
      );
      expect(allCritCalls).toHaveLength(1); // re-fired because both critical again
    });

    it("works without notifier", () => {
      const logger = makeLogger();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000 },
        logger,
        // no notifier
      );

      mon.recordQuote("X");
      vi.advanceTimersByTime(6000);
      // Should not throw
      expect(() => mon.check()).not.toThrow();
      expect(logger.warn).toHaveBeenCalled();
    });
  });

  // ── Edge cases ────────────────────────────────────────────────

  describe("edge cases", () => {
    it("handles empty symbol list", () => {
      const mon = new DataStalenessMonitor({}, makeLogger());
      mon.registerSymbols([]);
      const report = mon.getReport();
      expect(report.overallStatus).toBe("no_data");
    });

    it("handles registered-but-never-received symbols as critical", () => {
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 10_000 },
        makeLogger(),
      );
      mon.registerSymbols(["NEVER"]);
      const report = mon.getReport();
      expect(report.symbols[0]!.status).toBe("critical");
      expect(report.symbols[0]!.ageMs).toBe(Infinity);
    });

    it("stop clears fired alerts so fresh start re-alerts", () => {
      const notifier = makeNotifier();
      const mon = new DataStalenessMonitor(
        { warningThresholdMs: 5000, criticalThresholdMs: 30_000 },
        makeLogger(),
        notifier,
      );

      mon.recordQuote("R");
      vi.advanceTimersByTime(6000);
      mon.check();
      expect(notifier.notify).toHaveBeenCalledTimes(1);

      // Stop clears state
      mon.stop();
      notifier.notify.mockClear();

      // After restart, same staleness should re-alert
      mon.start();
      vi.advanceTimersByTime(1000);
      mon.check();
      expect(notifier.notify).toHaveBeenCalledTimes(1); // fires again
      mon.stop();
    });
  });
});
