import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  InMemoryStorage,
  createStorage,
  CURRENT_SCHEMA_VERSION,
  SCHEMA_SQL,
} from "./sqlite-storage.js";
import type { KlineRow, SignalRow, OrderRow, TradeRow, EquityRow } from "./sqlite-storage.js";

function tmpDir(): string {
  return path.join(os.tmpdir(), `qt-storage-${Date.now()}-${Math.random().toString(36).slice(2)}`);
}

describe("InMemoryStorage", () => {
  let storage: InMemoryStorage;
  let dataDir: string;

  beforeEach(async () => {
    dataDir = tmpDir();
    storage = new InMemoryStorage(path.join(dataDir, "test-storage.json"));
    await storage.initialize();
  });

  afterEach(async () => {
    await storage.close();
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  // ── K-line data ───────────────────────────────────────────

  describe("klines", () => {
    const sampleKlines: KlineRow[] = [
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 1800,
        high: 1850,
        low: 1790,
        close: 1830,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-03",
        interval: "1d",
        open: 1830,
        high: 1860,
        low: 1820,
        close: 1850,
        volume: 12000,
      },
      {
        symbol: "600519",
        date: "2024-01-04",
        interval: "1d",
        open: 1850,
        high: 1870,
        low: 1840,
        close: 1860,
        volume: 11000,
      },
    ];

    it("upserts and retrieves klines", async () => {
      await storage.upsertKlines(sampleKlines);
      const result = await storage.getKlines("600519", "1d", "2024-01-02", "2024-01-04");
      expect(result).toHaveLength(3);
      expect(result[0]!.close).toBe(1830);
      expect(result[2]!.close).toBe(1860);
    });

    it("filters klines by date range", async () => {
      await storage.upsertKlines(sampleKlines);
      const result = await storage.getKlines("600519", "1d", "2024-01-03", "2024-01-03");
      expect(result).toHaveLength(1);
      expect(result[0]!.close).toBe(1850);
    });

    it("updates existing kline on upsert", async () => {
      await storage.upsertKlines(sampleKlines);
      await storage.upsertKlines([
        {
          symbol: "600519",
          date: "2024-01-02",
          interval: "1d",
          open: 1800,
          high: 1855,
          low: 1790,
          close: 1840,
          volume: 10500,
        },
      ]);
      const result = await storage.getKlines("600519", "1d", "2024-01-02", "2024-01-02");
      expect(result[0]!.close).toBe(1840);
      expect(result[0]!.volume).toBe(10500);
    });

    it("counts klines", async () => {
      await storage.upsertKlines(sampleKlines);
      expect(await storage.getKlineCount()).toBe(3);
      expect(await storage.getKlineCount("600519")).toBe(3);
      expect(await storage.getKlineCount("000001")).toBe(0);
    });

    it("gets kline date range", async () => {
      await storage.upsertKlines(sampleKlines);
      const range = await storage.getKlineDateRange("600519", "1d");
      expect(range).toEqual({ min: "2024-01-02", max: "2024-01-04" });
      expect(await storage.getKlineDateRange("999999", "1d")).toBeNull();
    });

    it("deletes klines by symbol and interval", async () => {
      const extraKlines = [{ ...sampleKlines[0]!, symbol: "000001", interval: "1d" as const }];
      await storage.upsertKlines([...sampleKlines, ...extraKlines]);
      expect(await storage.getKlineCount()).toBe(4);

      const deleted = await storage.deleteKlines("600519", "1d");
      expect(deleted).toBe(3);
      expect(await storage.getKlineCount()).toBe(1);
      expect(await storage.getKlineCount("000001")).toBe(1);
    });

    it("deletes klines by symbol only", async () => {
      await storage.upsertKlines(sampleKlines);
      const deleted = await storage.deleteKlines("600519");
      expect(deleted).toBe(3);
      expect(await storage.getKlineCount()).toBe(0);
    });

    it("deletes all klines when no filter", async () => {
      await storage.upsertKlines(sampleKlines);
      const deleted = await storage.deleteKlines();
      expect(deleted).toBe(3);
      expect(await storage.getKlineCount()).toBe(0);
    });

    it("returns 0 when deleting non-existent data", async () => {
      expect(await storage.deleteKlines("999999", "1d")).toBe(0);
    });
  });

  // ── Signals ───────────────────────────────────────────────

  describe("signals", () => {
    it("inserts and retrieves signals", async () => {
      const id = await storage.insertSignal({
        timestamp: Date.now(),
        symbol: "600519",
        direction: "buy",
        price: 1850,
        quantity: 100,
        strategyId: "dual-ma",
      });
      expect(id).toBe(1);
      const signals = await storage.getSignals();
      expect(signals).toHaveLength(1);
      expect(signals[0]!.symbol).toBe("600519");
    });

    it("filters signals by symbol and strategyId", async () => {
      await storage.insertSignal({ timestamp: 1, symbol: "600519", direction: "buy", price: 1800 });
      await storage.insertSignal({
        timestamp: 2,
        symbol: "000001",
        direction: "sell",
        price: 3400,
        strategyId: "rsi",
      });
      await storage.insertSignal({
        timestamp: 3,
        symbol: "600519",
        direction: "sell",
        price: 1900,
        strategyId: "rsi",
      });

      expect(await storage.getSignals({ symbol: "600519" })).toHaveLength(2);
      expect(await storage.getSignals({ strategyId: "rsi" })).toHaveLength(2);
      expect(await storage.getSignals({ symbol: "600519", strategyId: "rsi" })).toHaveLength(1);
    });

    it("limits signal count", async () => {
      for (let i = 0; i < 5; i++) {
        await storage.insertSignal({
          timestamp: i,
          symbol: "600519",
          direction: "buy",
          price: 1800 + i,
        });
      }
      expect(await storage.getSignals({ limit: 3 })).toHaveLength(3);
    });
  });

  // ── Orders ────────────────────────────────────────────────

  describe("orders", () => {
    it("upserts and retrieves orders", async () => {
      const order: OrderRow = {
        orderId: "ord-001",
        symbol: "600519",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 1850,
        status: "pending",
        createdAt: new Date().toISOString(),
      };
      await storage.upsertOrder(order);
      const orders = await storage.getOrders();
      expect(orders).toHaveLength(1);
      expect(orders[0]!.orderId).toBe("ord-001");
    });

    it("filters orders by status", async () => {
      await storage.upsertOrder({
        orderId: "o1",
        symbol: "600519",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        status: "pending",
      });
      await storage.upsertOrder({
        orderId: "o2",
        symbol: "600519",
        side: "sell",
        orderType: "limit",
        quantity: 100,
        status: "filled",
      });
      await storage.upsertOrder({
        orderId: "o3",
        symbol: "000001",
        side: "buy",
        orderType: "market",
        quantity: 200,
        status: "pending",
      });

      expect(await storage.getOrders({ status: ["pending"] })).toHaveLength(2);
      expect(await storage.getOrders({ status: ["filled"] })).toHaveLength(1);
    });

    it("updates order status", async () => {
      await storage.upsertOrder({
        orderId: "o1",
        symbol: "600519",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        status: "pending",
      });
      await storage.updateOrderStatus("o1", "filled", 100, 1855);
      const orders = await storage.getOrders();
      expect(orders[0]!.status).toBe("filled");
      expect(orders[0]!.filledQty).toBe(100);
      expect(orders[0]!.filledPrice).toBe(1855);
    });
  });

  // ── Trades ────────────────────────────────────────────────

  describe("trades", () => {
    it("inserts and retrieves trades", async () => {
      const id = await storage.insertTrade({
        symbol: "600519",
        side: "buy",
        entryPrice: 1800,
        exitPrice: 1850,
        quantity: 100,
        entryTime: "2024-01-02T09:30:00",
        exitTime: "2024-01-05T14:30:00",
        netPnl: 5000,
        pnlPct: 2.78,
      });
      expect(id).toBe(1);
      const trades = await storage.getTrades();
      expect(trades).toHaveLength(1);
      expect(trades[0]!.netPnl).toBe(5000);
    });

    it("filters trades by symbol", async () => {
      await storage.insertTrade({
        symbol: "600519",
        side: "buy",
        entryPrice: 1800,
        exitPrice: 1850,
        quantity: 100,
        netPnl: 5000,
      });
      await storage.insertTrade({
        symbol: "000001",
        side: "buy",
        entryPrice: 3400,
        exitPrice: 3500,
        quantity: 100,
        netPnl: 10000,
      });
      expect(await storage.getTrades({ symbol: "600519" })).toHaveLength(1);
    });
  });

  // ── Equity snapshots ──────────────────────────────────────

  describe("equity", () => {
    it("upserts and retrieves equity snapshots", async () => {
      await storage.upsertEquitySnapshot({
        date: "2024-01-02",
        equity: 100000,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: 500,
        positionCount: 2,
      });
      const snaps = await storage.getEquitySnapshots();
      expect(snaps).toHaveLength(1);
      expect(snaps[0]!.equity).toBe(100000);
    });

    it("updates same-date snapshot (upsert)", async () => {
      await storage.upsertEquitySnapshot({
        date: "2024-01-02",
        equity: 100000,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: 0,
        positionCount: 2,
      });
      await storage.upsertEquitySnapshot({
        date: "2024-01-02",
        equity: 105000,
        cash: 85000,
        marketValue: 20000,
        dailyPnl: 5000,
        positionCount: 2,
      });
      const snaps = await storage.getEquitySnapshots();
      expect(snaps).toHaveLength(1);
      expect(snaps[0]!.equity).toBe(105000);
    });

    it("filters equity by date range", async () => {
      for (let d = 1; d <= 5; d++) {
        await storage.upsertEquitySnapshot({
          date: `2024-01-0${d}`,
          equity: 100000 + d * 1000,
          cash: 80000,
          marketValue: 20000 + d * 1000,
          dailyPnl: d * 100,
          positionCount: d,
        });
      }
      const range = await storage.getEquitySnapshots({ start: "2024-01-02", end: "2024-01-04" });
      expect(range).toHaveLength(3);
      expect(range[0]!.date).toBe("2024-01-02");
    });

    it("limits equity snapshots", async () => {
      for (let d = 1; d <= 10; d++) {
        await storage.upsertEquitySnapshot({
          date: `2024-01-${String(d).padStart(2, "0")}`,
          equity: 100000 + d * 1000,
          cash: 80000,
          marketValue: 20000,
          dailyPnl: 0,
          positionCount: 0,
        });
      }
      const recent = await storage.getEquitySnapshots({ limit: 3 });
      expect(recent).toHaveLength(3);
      expect(recent[0]!.date).toBe("2024-01-08"); // last 3
    });
  });

  // ── Persistence ───────────────────────────────────────────

  describe("persistence", () => {
    it("saves and loads data correctly", async () => {
      await storage.upsertKlines([
        {
          symbol: "600519",
          date: "2024-01-02",
          interval: "1d",
          open: 1800,
          high: 1850,
          low: 1790,
          close: 1830,
          volume: 10000,
        },
      ]);
      await storage.insertSignal({ timestamp: 1, symbol: "600519", direction: "buy", price: 1830 });
      await storage.upsertOrder({
        orderId: "o1",
        symbol: "600519",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        status: "filled",
      });
      await storage.upsertEquitySnapshot({
        date: "2024-01-02",
        equity: 100000,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: 0,
        positionCount: 1,
      });
      await storage.close();

      const storage2 = new InMemoryStorage(path.join(dataDir, "test-storage.json"));
      await storage2.initialize();
      expect(await storage2.getKlineCount()).toBe(1);
      expect(await storage2.getSignals()).toHaveLength(1);
      expect(await storage2.getOrders()).toHaveLength(1);
      expect(await storage2.getEquitySnapshots()).toHaveLength(1);
      await storage2.close();
    });
  });
});

// ── Schema constants ────────────────────────────────────────

describe("Schema", () => {
  it("has current version", () => {
    expect(CURRENT_SCHEMA_VERSION).toBe(1);
  });

  it("SQL contains all expected tables", () => {
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS kline_cache");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS signals");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS orders");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS trades");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS equity_snapshots");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS alerts");
    expect(SCHEMA_SQL).toContain("CREATE TABLE IF NOT EXISTS schema_version");
  });

  it("SQL has proper indexes", () => {
    expect(SCHEMA_SQL).toContain("idx_kline_symbol_date");
    expect(SCHEMA_SQL).toContain("idx_signals_symbol");
    expect(SCHEMA_SQL).toContain("idx_orders_status");
    expect(SCHEMA_SQL).toContain("idx_trades_symbol");
    expect(SCHEMA_SQL).toContain("idx_alerts_severity");
  });
});

// ── Factory ─────────────────────────────────────────────────

describe("createStorage", () => {
  it("falls back to InMemoryStorage when SQLite unavailable", async () => {
    const dir = tmpDir();
    // better-sqlite3 is not installed in test env, so should fall back
    const storage = await createStorage(dir, true);
    expect(storage).toBeInstanceOf(InMemoryStorage);
    await storage.close();
    await fs.rm(dir, { recursive: true, force: true });
  });

  it("creates InMemoryStorage when preferSqlite is false", async () => {
    const dir = tmpDir();
    const storage = await createStorage(dir, false);
    expect(storage).toBeInstanceOf(InMemoryStorage);
    await storage.close();
    await fs.rm(dir, { recursive: true, force: true });
  });

  it("creates InMemoryStorage without persistence when no dataDir", async () => {
    const storage = await createStorage(undefined, false);
    expect(storage).toBeInstanceOf(InMemoryStorage);
    await storage.close();
  });
});
