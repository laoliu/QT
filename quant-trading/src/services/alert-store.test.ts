/**
 * Tests for AlertStore — persistent, queryable alert storage.
 */
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AlertStore } from "./alert-store.js";
import type { Alert, AlertQuery, AlertSeverity, AlertSource } from "./alert-store.js";

describe("AlertStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Basic CRUD ────────────────────────────────────────────────

  describe("add & query", () => {
    it("adds alerts and returns ids", () => {
      const store = new AlertStore();
      const id1 = store.add("info", "system", "Test alert 1");
      const id2 = store.add("warning", "pnl_monitor", "Test alert 2");

      expect(id1).toMatch(/^alert_/);
      expect(id2).toMatch(/^alert_/);
      expect(id1).not.toBe(id2);
      expect(store.size).toBe(2);
    });

    it("queries all alerts in newest-first order", () => {
      const store = new AlertStore();
      store.add("info", "system", "First");
      vi.advanceTimersByTime(1000);
      store.add("warning", "system", "Second");

      const results = store.query();
      expect(results).toHaveLength(2);
      expect(results[0]!.message).toBe("Second");
      expect(results[1]!.message).toBe("First");
    });

    it("stores metadata", () => {
      const store = new AlertStore();
      store.add("info", "pnl_monitor", "PnL alert", { pnlPct: -0.03, ticker: "600519" });

      const alert = store.query()[0]!;
      expect(alert.metadata).toEqual({ pnlPct: -0.03, ticker: "600519" });
    });

    it("gets alert by id", () => {
      const store = new AlertStore();
      const id = store.add("critical", "risk_gate", "Risk breach");

      const alert = store.getById(id);
      expect(alert).toBeDefined();
      expect(alert!.severity).toBe("critical");
      expect(alert!.source).toBe("risk_gate");
    });

    it("returns undefined for unknown id", () => {
      const store = new AlertStore();
      expect(store.getById("nonexistent")).toBeUndefined();
    });
  });

  // ── Acknowledge ───────────────────────────────────────────────

  describe("acknowledge", () => {
    it("acknowledges a single alert", () => {
      const store = new AlertStore();
      const id = store.add("warning", "system", "Test");

      expect(store.acknowledge(id)).toBe(true);
      expect(store.getById(id)!.acknowledged).toBe(true);
    });

    it("returns false for unknown id", () => {
      const store = new AlertStore();
      expect(store.acknowledge("nonexistent")).toBe(false);
    });

    it("acknowledges all alerts", () => {
      const store = new AlertStore();
      store.add("info", "system", "A");
      store.add("warning", "system", "B");
      store.add("critical", "system", "C");

      const count = store.acknowledgeAll();
      expect(count).toBe(3);

      const all = store.query();
      expect(all.every((a) => a.acknowledged)).toBe(true);
    });

    it("acknowledgeAll returns 0 when all already acknowledged", () => {
      const store = new AlertStore();
      store.add("info", "system", "A");
      store.acknowledgeAll();

      expect(store.acknowledgeAll()).toBe(0);
    });
  });

  // ── Query filters ─────────────────────────────────────────────

  describe("query filters", () => {
    let store: AlertStore;

    beforeEach(() => {
      store = new AlertStore();
      // Add alerts with different severities and sources
      store.add("info", "system", "Info system");
      vi.advanceTimersByTime(1000);
      store.add("warning", "pnl_monitor", "PnL warning");
      vi.advanceTimersByTime(1000);
      store.add("critical", "connection_health", "Connection lost");
      vi.advanceTimersByTime(1000);
      store.add("warning", "data_staleness", "Data stale");
      vi.advanceTimersByTime(1000);
      store.add("info", "order_manager", "Order filled");
    });

    it("filters by single severity", () => {
      const results = store.query({ severity: "warning" });
      expect(results).toHaveLength(2);
      expect(results.every((a) => a.severity === "warning")).toBe(true);
    });

    it("filters by multiple severities", () => {
      const results = store.query({ severity: ["warning", "critical"] });
      expect(results).toHaveLength(3);
    });

    it("filters by single source", () => {
      const results = store.query({ source: "pnl_monitor" });
      expect(results).toHaveLength(1);
      expect(results[0]!.source).toBe("pnl_monitor");
    });

    it("filters by multiple sources", () => {
      const results = store.query({ source: ["system", "order_manager"] });
      expect(results).toHaveLength(2);
    });

    it("filters by time range (after)", () => {
      const all = store.query();
      const thirdAlert = all[2]!; // middle alert
      const results = store.query({ after: thirdAlert.timestamp });
      expect(results.length).toBeGreaterThanOrEqual(3);
    });

    it("filters by acknowledged state", () => {
      const id = store.query()[0]!.id;
      store.acknowledge(id);

      const acked = store.query({ acknowledged: true });
      expect(acked).toHaveLength(1);

      const unacked = store.query({ acknowledged: false });
      expect(unacked).toHaveLength(4);
    });

    it("supports pagination with limit and offset", () => {
      const page1 = store.query({ limit: 2, offset: 0 });
      expect(page1).toHaveLength(2);

      const page2 = store.query({ limit: 2, offset: 2 });
      expect(page2).toHaveLength(2);

      const page3 = store.query({ limit: 2, offset: 4 });
      expect(page3).toHaveLength(1);

      // No overlap
      const allIds = [...page1, ...page2, ...page3].map((a) => a.id);
      expect(new Set(allIds).size).toBe(5);
    });

    it("combines multiple filters", () => {
      const results = store.query({
        severity: "warning",
        source: "data_staleness",
      });
      expect(results).toHaveLength(1);
      expect(results[0]!.message).toBe("Data stale");
    });
  });

  // ── Summary ───────────────────────────────────────────────────

  describe("getSummary", () => {
    it("returns correct aggregate counts", () => {
      const store = new AlertStore();
      store.add("info", "system", "A");
      store.add("info", "system", "B");
      store.add("warning", "pnl_monitor", "C");
      store.add("critical", "connection_health", "D");
      store.acknowledge(store.query()[0]!.id);

      const summary = store.getSummary();
      expect(summary.total).toBe(4);
      expect(summary.unacknowledged).toBe(3);
      expect(summary.bySeverity).toEqual({ info: 2, warning: 1, critical: 1 });
      expect(summary.bySource).toEqual({
        system: 2,
        pnl_monitor: 1,
        connection_health: 1,
      });
    });

    it("handles empty store", () => {
      const store = new AlertStore();
      const summary = store.getSummary();
      expect(summary.total).toBe(0);
      expect(summary.unacknowledged).toBe(0);
    });
  });

  // ── Pruning ───────────────────────────────────────────────────

  describe("pruning", () => {
    it("prunes oldest alerts when exceeding maxAlerts", () => {
      const store = new AlertStore({ maxAlerts: 3 });
      store.add("info", "system", "1");
      vi.advanceTimersByTime(100);
      store.add("info", "system", "2");
      vi.advanceTimersByTime(100);
      store.add("info", "system", "3");
      vi.advanceTimersByTime(100);
      store.add("info", "system", "4"); // triggers pruning

      expect(store.size).toBe(3);
      // Oldest ("1") should be removed
      const messages = store.query().map((a) => a.message);
      expect(messages).not.toContain("1");
      expect(messages).toContain("4");
    });

    it("prunes alerts older than maxAgeMs", () => {
      const store = new AlertStore({ maxAgeMs: 5000 });
      store.add("info", "system", "Old");
      vi.advanceTimersByTime(6000);
      store.add("info", "system", "New"); // triggers prune

      expect(store.size).toBe(1);
      expect(store.query()[0]!.message).toBe("New");
    });
  });

  // ── Clear ─────────────────────────────────────────────────────

  describe("clear", () => {
    it("removes all alerts", () => {
      const store = new AlertStore();
      store.add("info", "system", "A");
      store.add("warning", "system", "B");
      store.clear();

      expect(store.size).toBe(0);
      expect(store.query()).toHaveLength(0);
    });
  });

  // ── Persistence ───────────────────────────────────────────────

  describe("persistence", () => {
    const persistPath = join(tmpdir(), `alert-store-test-${Date.now()}.json`);

    afterEach(() => {
      try {
        if (existsSync(persistPath)) unlinkSync(persistPath);
      } catch {
        // ignore
      }
    });

    it("saves and loads alerts across instances", () => {
      vi.useRealTimers(); // need real FS

      const store1 = new AlertStore({ persistPath, autoSaveIntervalMs: 0 });
      store1.start();
      store1.add("warning", "pnl_monitor", "PnL drop");
      store1.add("critical", "connection_health", "Disconnected");
      store1.stop(); // triggers save

      // New instance loads from disk
      const store2 = new AlertStore({ persistPath, autoSaveIntervalMs: 0 });
      store2.start();
      expect(store2.size).toBe(2);

      const results = store2.query();
      expect(results.map((a) => a.message)).toContain("PnL drop");
      expect(results.map((a) => a.message)).toContain("Disconnected");
      store2.stop();
    });

    it("handles corrupt persistence file gracefully", () => {
      vi.useRealTimers();

      const corruptPath = join(tmpdir(), `alert-store-corrupt-${Date.now()}.json`);
      // Write garbage
      const { writeFileSync } = require("node:fs");
      writeFileSync(corruptPath, "not valid json{{{", "utf-8");

      const store = new AlertStore({ persistPath: corruptPath, autoSaveIntervalMs: 0 });
      store.start();
      expect(store.size).toBe(0); // gracefully starts empty
      store.stop();

      try {
        unlinkSync(corruptPath);
      } catch {
        // ignore
      }
    });

    it("creates directory if not exists", () => {
      vi.useRealTimers();

      const deepPath = join(tmpdir(), `alert-store-deep-${Date.now()}`, "sub", "alerts.json");
      const store = new AlertStore({ persistPath: deepPath, autoSaveIntervalMs: 0 });
      store.start();
      store.add("info", "system", "Test");
      store.stop(); // triggers save — should create dirs

      expect(existsSync(deepPath)).toBe(true);

      // Cleanup
      const { rmSync } = require("node:fs");
      rmSync(join(tmpdir(), `alert-store-deep-${Date.now()}`), { recursive: true, force: true });
    });

    it("does not save when nothing changed", () => {
      vi.useRealTimers();

      const store = new AlertStore({ persistPath, autoSaveIntervalMs: 0 });
      store.start();
      store.stop(); // nothing added, dirty=false

      // File should not be created
      expect(existsSync(persistPath)).toBe(false);
    });
  });
});
