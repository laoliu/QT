import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { InMemoryStorage } from "./sqlite-storage.js";
import {
  DataExportService,
  type ExportFormat,
  type ExportResult,
} from "./data-export.js";

// ── Helpers ──────────────────────────────────────────────────

function makeKlines(symbol: string, count: number, startDate = "2024-01-02") {
  const [y, m, d] = startDate.split("-").map(Number);
  return Array.from({ length: count }, (_, i) => {
    const date = new Date(Date.UTC(y!, m! - 1, d! + i));
    const ds = date.toISOString().slice(0, 10);
    return {
      symbol,
      date: ds,
      interval: "1d",
      open: 100 + i,
      high: 105 + i,
      low: 98 + i,
      close: 103 + i,
      volume: 10000 + i * 100,
      amount: 1000000 + i * 10000,
    };
  });
}

function makeSignals(count: number, symbol = "600519") {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: Date.UTC(2024, 0, 2 + i),
    date: `2024-01-${String(2 + i).padStart(2, "0")}`,
    symbol,
    direction: i % 2 === 0 ? "buy" : "sell",
    price: 1800 + i * 10,
    quantity: 100,
    reason: `Signal reason ${i}`,
    confidence: 0.8 + i * 0.02,
    strategyId: "dual-ma-crossover",
  }));
}

function makeOrders(count: number, symbol = "600519") {
  return Array.from({ length: count }, (_, i) => ({
    orderId: `order-${i}`,
    symbol,
    market: "cn_stock",
    side: i % 2 === 0 ? "buy" : "sell",
    orderType: "limit",
    quantity: 100,
    price: 1800 + i * 10,
    status: i < count / 2 ? "filled" : "pending",
    strategyId: "dual-ma-crossover",
    filledQty: i < count / 2 ? 100 : 0,
    filledPrice: i < count / 2 ? 1800 + i * 10 : undefined,
    createdAt: `2024-01-${String(2 + i).padStart(2, "0")}T09:30:00Z`,
    updatedAt: `2024-01-${String(2 + i).padStart(2, "0")}T10:00:00Z`,
  }));
}

function makeTrades(count: number, symbol = "600519") {
  return Array.from({ length: count }, (_, i) => ({
    symbol,
    side: "buy",
    entryPrice: 1800 + i * 10,
    exitPrice: 1850 + i * 10,
    quantity: 100,
    entryTime: `2024-01-${String(2 + i).padStart(2, "0")}T09:30:00Z`,
    exitTime: `2024-01-${String(3 + i).padStart(2, "0")}T14:30:00Z`,
    netPnl: 5000 - i * 200,
    pnlPct: 0.028 - i * 0.001,
    commission: 10.8,
    strategyId: "dual-ma-crossover",
    entryOrder: `order-${i * 2}`,
    exitOrder: `order-${i * 2 + 1}`,
  }));
}

function makeEquity(count: number) {
  const [y, m, d] = [2024, 1, 2];
  return Array.from({ length: count }, (_, i) => {
    const date = new Date(Date.UTC(y, m - 1, d + i));
    return {
      date: date.toISOString().slice(0, 10),
      equity: 100000 + i * 500,
      cash: 50000 - i * 200,
      marketValue: 50000 + i * 700,
      dailyPnl: 500 + (i % 3 === 0 ? -800 : 200),
      positionCount: 3 + (i % 2),
      benchmark: 100000 + i * 300,
    };
  });
}

// ── Tests ────────────────────────────────────────────────────

describe("DataExportService", () => {
  let storage: InMemoryStorage;
  let service: DataExportService;

  beforeEach(async () => {
    storage = new InMemoryStorage(null);
    await storage.initialize();
    service = new DataExportService(storage);
  });

  afterEach(async () => {
    await storage.close();
  });

  // ════════════════════════════════════════════════════════════
  // CSV Format Tests
  // ════════════════════════════════════════════════════════════

  describe("CSV export", () => {
    it("exports klines as CSV with header", async () => {
      await storage.upsertKlines(makeKlines("600519", 3));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );

      expect(result.format).toBe("csv");
      expect(result.dataType).toBe("klines");
      expect(result.rowCount).toBe(3);
      expect(result.extension).toBe(".csv");
      expect(result.mimeType).toContain("text/csv");

      const lines = result.content.split("\n");
      expect(lines[0]).toBe("symbol,date,interval,open,high,low,close,volume,amount");
      expect(lines).toHaveLength(4); // header + 3 rows
      expect(lines[1]).toContain("600519");
      expect(lines[1]).toContain("2024-01-02");
    });

    it("exports signals as CSV", async () => {
      for (const s of makeSignals(3)) {
        await storage.insertSignal(s);
      }

      const result = await service.exportSignals({}, { format: "csv" });
      expect(result.rowCount).toBe(3);
      expect(result.dataType).toBe("signals");

      const lines = result.content.split("\n");
      expect(lines[0]).toContain("symbol");
      expect(lines[0]).toContain("direction");
      expect(lines[0]).toContain("price");
    });

    it("exports orders as CSV", async () => {
      for (const o of makeOrders(4)) {
        await storage.upsertOrder(o);
      }

      const result = await service.exportOrders({}, { format: "csv" });
      expect(result.rowCount).toBe(4);
      expect(result.dataType).toBe("orders");
    });

    it("exports trades as CSV", async () => {
      for (const t of makeTrades(3)) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({}, { format: "csv" });
      expect(result.rowCount).toBe(3);
      expect(result.dataType).toBe("trades");

      const lines = result.content.split("\n");
      expect(lines[0]).toContain("entryPrice");
      expect(lines[0]).toContain("netPnl");
    });

    it("exports equity snapshots as CSV", async () => {
      for (const e of makeEquity(5)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity({}, { format: "csv" });
      expect(result.rowCount).toBe(5);
      expect(result.dataType).toBe("equity");

      const lines = result.content.split("\n");
      expect(lines[0]).toContain("equity");
      expect(lines[0]).toContain("cash");
      expect(lines[0]).toContain("dailyPnl");
    });

    it("omits header when includeHeader=false", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv", includeHeader: false },
      );

      const lines = result.content.split("\n");
      expect(lines).toHaveLength(2); // no header
      expect(lines[0]).not.toContain("symbol");
      expect(lines[0]).toContain("600519");
    });

    it("escapes commas in values", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        date: "2024-01-02",
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "MACD crossover, volume surge",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      // Value with comma should be quoted
      expect(result.content).toContain('"MACD crossover, volume surge"');
    });

    it("escapes double quotes in values", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        date: "2024-01-02",
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: 'Price broke "resistance"',
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      // Quotes in value should be doubled and the field quoted
      expect(result.content).toContain('"Price broke ""resistance"""');
    });

    it("uses custom delimiter", async () => {
      await storage.upsertKlines(makeKlines("600519", 1));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv", delimiter: "|" },
      );

      const lines = result.content.split("\n");
      expect(lines[0]).toBe("symbol|date|interval|open|high|low|close|volume|amount");
    });
  });

  // ════════════════════════════════════════════════════════════
  // TSV Format Tests
  // ════════════════════════════════════════════════════════════

  describe("TSV export", () => {
    it("exports klines as TSV (tab-separated)", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "tsv" },
      );

      expect(result.format).toBe("tsv");
      expect(result.extension).toBe(".tsv");
      expect(result.mimeType).toContain("tab-separated");

      const lines = result.content.split("\n");
      expect(lines[0]!.split("\t")).toHaveLength(9); // 9 kline columns
    });

    it("escapes tabs in values for TSV", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        date: "2024-01-02",
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "Price\tbreak",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "tsv" });
      // Tab in value should be quoted
      expect(result.content).toContain('"Price\tbreak"');
    });
  });

  // ════════════════════════════════════════════════════════════
  // JSON Format Tests
  // ════════════════════════════════════════════════════════════

  describe("JSON export", () => {
    it("exports klines as pretty JSON", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );

      expect(result.format).toBe("json");
      expect(result.extension).toBe(".json");
      expect(result.mimeType).toContain("application/json");

      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(2);
      expect(parsed[0].symbol).toBe("600519");
      expect(parsed[0].open).toBe(100);
      expect(parsed[0].close).toBe(103);
      // Pretty-printed should contain newlines
      expect(result.content).toContain("\n");
    });

    it("exports compact JSON when prettyJson=false", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "json", prettyJson: false },
      );

      // Compact JSON is one line
      expect(result.content.split("\n")).toHaveLength(1);
      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(2);
    });

    it("omits empty/null fields in JSON", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        // no date, reason, confidence, etc.
      });

      const result = await service.exportSignals({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed[0].symbol).toBe("600519");
      // Empty fields should not appear
      expect(parsed[0]).not.toHaveProperty("reason");
    });

    it("exports trades as JSON with numeric precision", async () => {
      for (const t of makeTrades(2)) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(2);
      expect(typeof parsed[0].entryPrice).toBe("number");
      expect(typeof parsed[0].netPnl).toBe("number");
    });

    it("exports equity as JSON array", async () => {
      for (const e of makeEquity(3)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(3);
      expect(parsed[0].date).toBe("2024-01-02");
      expect(typeof parsed[0].equity).toBe("number");
    });
  });

  // ════════════════════════════════════════════════════════════
  // Filter Tests
  // ════════════════════════════════════════════════════════════

  describe("filtering", () => {
    it("filters klines by date range", async () => {
      await storage.upsertKlines(makeKlines("600519", 10));

      const result = await service.exportKlines({
        symbol: "600519",
        startDate: "2024-01-04",
        endDate: "2024-01-06",
      });

      expect(result.rowCount).toBe(3);
    });

    it("filters klines by interval", async () => {
      await storage.upsertKlines(makeKlines("600519", 3));
      // Add weekly klines
      await storage.upsertKlines([{
        symbol: "600519",
        date: "2024-01-02",
        interval: "1w",
        open: 100, high: 110, low: 95, close: 108, volume: 50000,
      }]);

      const dailyResult = await service.exportKlines({
        symbol: "600519",
        interval: "1d",
      });
      expect(dailyResult.rowCount).toBe(3);

      const weeklyResult = await service.exportKlines({
        symbol: "600519",
        interval: "1w",
      });
      expect(weeklyResult.rowCount).toBe(1);
    });

    it("filters signals by symbol", async () => {
      for (const s of makeSignals(3, "600519")) {
        await storage.insertSignal(s);
      }
      for (const s of makeSignals(2, "000001")) {
        await storage.insertSignal(s);
      }

      const result = await service.exportSignals({ symbol: "600519" });
      expect(result.rowCount).toBe(3);
    });

    it("filters signals by strategyId", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        strategyId: "macd",
      });
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 3),
        symbol: "600519",
        direction: "sell",
        price: 1850,
        strategyId: "rsi",
      });

      const result = await service.exportSignals({ strategyId: "macd" });
      expect(result.rowCount).toBe(1);
    });

    it("filters orders by status", async () => {
      for (const o of makeOrders(4)) {
        await storage.upsertOrder(o);
      }

      const result = await service.exportOrders({ status: ["filled"] });
      expect(result.rowCount).toBeGreaterThan(0);
      const lines = result.content.split("\n");
      // Every data line should contain "filled"
      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]).toContain("filled");
      }
    });

    it("filters trades by symbol", async () => {
      for (const t of makeTrades(2, "600519")) {
        await storage.insertTrade(t);
      }
      for (const t of makeTrades(1, "000001")) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({ symbol: "600519" });
      expect(result.rowCount).toBe(2);
    });

    it("filters equity by date range", async () => {
      for (const e of makeEquity(10)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity({
        startDate: "2024-01-05",
        endDate: "2024-01-08",
      });
      expect(result.rowCount).toBeLessThanOrEqual(4);
      expect(result.rowCount).toBeGreaterThan(0);
    });
  });

  // ════════════════════════════════════════════════════════════
  // maxRows Limit Tests
  // ════════════════════════════════════════════════════════════

  describe("maxRows limiting", () => {
    it("limits kline export rows", async () => {
      await storage.upsertKlines(makeKlines("600519", 20));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { maxRows: 5 },
      );
      expect(result.rowCount).toBe(5);
    });

    it("limits signal export rows", async () => {
      for (const s of makeSignals(10)) {
        await storage.insertSignal(s);
      }

      const result = await service.exportSignals({}, { maxRows: 3 });
      expect(result.rowCount).toBe(3);
    });

    it("does not pad when fewer rows than maxRows", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { maxRows: 100 },
      );
      expect(result.rowCount).toBe(2);
    });

    it("returns 0 rows when maxRows=0", async () => {
      await storage.upsertKlines(makeKlines("600519", 5));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { maxRows: 0 },
      );
      expect(result.rowCount).toBe(0);
    });
  });

  // ════════════════════════════════════════════════════════════
  // Empty Data / Boundary Conditions
  // ════════════════════════════════════════════════════════════

  describe("boundary conditions", () => {
    it("exports empty CSV with only header for no data", async () => {
      const result = await service.exportKlines(
        { symbol: "NONEXISTENT" },
        { format: "csv" },
      );

      expect(result.rowCount).toBe(0);
      expect(result.content).toBe("symbol,date,interval,open,high,low,close,volume,amount");
    });

    it("exports empty JSON array for no data", async () => {
      const result = await service.exportKlines(
        { symbol: "NONEXISTENT" },
        { format: "json" },
      );

      expect(result.rowCount).toBe(0);
      expect(JSON.parse(result.content)).toEqual([]);
    });

    it("exports empty CSV without header when includeHeader=false and no data", async () => {
      const result = await service.exportKlines(
        { symbol: "NONEXISTENT" },
        { format: "csv", includeHeader: false },
      );

      expect(result.rowCount).toBe(0);
      expect(result.content).toBe("");
    });

    it("handles single row export", async () => {
      await storage.upsertKlines(makeKlines("600519", 1));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );

      expect(result.rowCount).toBe(1);
      const lines = result.content.split("\n");
      expect(lines).toHaveLength(2); // header + 1 row
    });

    it("handles klines with missing optional fields", async () => {
      await storage.upsertKlines([{
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
        // no amount
      }]);

      const csvResult = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );
      // Should not crash, amount column should be empty
      const lines = csvResult.content.split("\n");
      expect(lines).toHaveLength(2);

      const jsonResult = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );
      const parsed = JSON.parse(jsonResult.content);
      expect(parsed[0].symbol).toBe("600519");
      // amount should not appear in JSON since it's undefined
      expect(parsed[0]).not.toHaveProperty("amount");
    });

    it("handles signals with all optional fields empty", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
      });

      const result = await service.exportSignals({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(1);
      expect(parsed[0].symbol).toBe("600519");
      expect(parsed[0].price).toBe(1800);
    });

    it("handles equity without benchmark", async () => {
      await storage.upsertEquitySnapshot({
        date: "2024-01-02",
        equity: 100000,
        cash: 50000,
        marketValue: 50000,
        dailyPnl: 500,
        positionCount: 3,
        // no benchmark
      });

      const result = await service.exportEquity({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed[0]).not.toHaveProperty("benchmark");
    });
  });

  // ════════════════════════════════════════════════════════════
  // Large Data Volume Tests
  // ════════════════════════════════════════════════════════════

  describe("large data volume", () => {
    it("exports 1000 kline rows as CSV", async () => {
      await storage.upsertKlines(makeKlines("600519", 1000, "2020-01-01"));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );

      expect(result.rowCount).toBe(1000);
      const lines = result.content.split("\n");
      expect(lines).toHaveLength(1001); // header + 1000 rows
    });

    it("exports 1000 kline rows as JSON", async () => {
      await storage.upsertKlines(makeKlines("600519", 1000, "2020-01-01"));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );

      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(1000);
      expect(result.rowCount).toBe(1000);
    });

    it("exports 500 signals", async () => {
      for (let i = 0; i < 500; i++) {
        await storage.insertSignal({
          timestamp: Date.UTC(2024, 0, 1) + i * 86400000,
          symbol: `stock-${i % 10}`,
          direction: i % 2 === 0 ? "buy" : "sell",
          price: 100 + i,
          strategyId: "bulk-test",
        });
      }

      const result = await service.exportSignals({}, { format: "csv" });
      expect(result.rowCount).toBe(500);
    });

    it("handles maxRows with large dataset efficiently", async () => {
      await storage.upsertKlines(makeKlines("600519", 1000, "2020-01-01"));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv", maxRows: 10 },
      );

      expect(result.rowCount).toBe(10);
      const lines = result.content.split("\n");
      expect(lines).toHaveLength(11); // header + 10 rows
    });

    it("exports compact JSON for large dataset", async () => {
      await storage.upsertKlines(makeKlines("600519", 500, "2020-01-01"));

      const pretty = await service.exportKlines(
        { symbol: "600519" },
        { format: "json", prettyJson: true },
      );
      const compact = await service.exportKlines(
        { symbol: "600519" },
        { format: "json", prettyJson: false },
      );

      // Compact should be significantly shorter
      expect(compact.content.length).toBeLessThan(pretty.content.length);
      // Both should parse to the same data
      expect(JSON.parse(compact.content)).toEqual(JSON.parse(pretty.content));
    });
  });

  // ════════════════════════════════════════════════════════════
  // Format Metadata Tests
  // ════════════════════════════════════════════════════════════

  describe("format metadata", () => {
    it("returns correct metadata for CSV", async () => {
      const result = await service.exportKlines(
        { symbol: "X" },
        { format: "csv" },
      );
      expect(result.extension).toBe(".csv");
      expect(result.mimeType).toContain("text/csv");
    });

    it("returns correct metadata for TSV", async () => {
      const result = await service.exportKlines(
        { symbol: "X" },
        { format: "tsv" },
      );
      expect(result.extension).toBe(".tsv");
      expect(result.mimeType).toContain("tab-separated");
    });

    it("returns correct metadata for JSON", async () => {
      const result = await service.exportKlines(
        { symbol: "X" },
        { format: "json" },
      );
      expect(result.extension).toBe(".json");
      expect(result.mimeType).toContain("application/json");
    });
  });

  // ════════════════════════════════════════════════════════════
  // All Formats x All Data Types (cross-product)
  // ════════════════════════════════════════════════════════════

  describe("all formats x all data types", () => {
    beforeEach(async () => {
      await storage.upsertKlines(makeKlines("600519", 3));
      for (const s of makeSignals(3)) await storage.insertSignal(s);
      for (const o of makeOrders(3)) await storage.upsertOrder(o);
      for (const t of makeTrades(3)) await storage.insertTrade(t);
      for (const e of makeEquity(3)) await storage.upsertEquitySnapshot(e);
    });

    const formats: ExportFormat[] = ["csv", "json", "tsv"];

    for (const fmt of formats) {
      it(`exports klines as ${fmt}`, async () => {
        const r = await service.exportKlines({ symbol: "600519" }, { format: fmt });
        expect(r.rowCount).toBe(3);
        expect(r.format).toBe(fmt);
        expect(r.content.length).toBeGreaterThan(0);
      });

      it(`exports signals as ${fmt}`, async () => {
        const r = await service.exportSignals({}, { format: fmt });
        expect(r.rowCount).toBe(3);
        expect(r.format).toBe(fmt);
      });

      it(`exports orders as ${fmt}`, async () => {
        const r = await service.exportOrders({}, { format: fmt });
        expect(r.rowCount).toBe(3);
        expect(r.format).toBe(fmt);
      });

      it(`exports trades as ${fmt}`, async () => {
        const r = await service.exportTrades({}, { format: fmt });
        expect(r.rowCount).toBe(3);
        expect(r.format).toBe(fmt);
      });

      it(`exports equity as ${fmt}`, async () => {
        const r = await service.exportEquity({}, { format: fmt });
        expect(r.rowCount).toBe(3);
        expect(r.format).toBe(fmt);
      });
    }
  });

  // ════════════════════════════════════════════════════════════
  // Default Options Tests
  // ════════════════════════════════════════════════════════════

  describe("default options", () => {
    it("defaults to CSV format", async () => {
      await storage.upsertKlines(makeKlines("600519", 1));
      const result = await service.exportKlines({ symbol: "600519" });
      expect(result.format).toBe("csv");
    });

    it("defaults to including header", async () => {
      await storage.upsertKlines(makeKlines("600519", 1));
      const result = await service.exportKlines({ symbol: "600519" });
      const lines = result.content.split("\n");
      expect(lines[0]).toContain("symbol");
    });

    it("defaults kline interval to 1d", async () => {
      await storage.upsertKlines(makeKlines("600519", 2));
      const result = await service.exportKlines({ symbol: "600519" });
      expect(result.rowCount).toBe(2);
    });
  });

  // ════════════════════════════════════════════════════════════
  // CSV Round-trip (parse back)
  // ════════════════════════════════════════════════════════════

  describe("CSV round-trip integrity", () => {
    it("CSV can be parsed back to original kline values", async () => {
      const klines = makeKlines("600519", 3);
      await storage.upsertKlines(klines);

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );

      const lines = result.content.split("\n");
      const header = lines[0]!.split(",");
      for (let i = 1; i < lines.length; i++) {
        const cols = lines[i]!.split(",");
        const row: Record<string, string> = {};
        header.forEach((h, j) => { row[h] = cols[j]!; });

        const original = klines[i - 1]!;
        expect(row.symbol).toBe(original.symbol);
        expect(row.date).toBe(original.date);
        expect(Number(row.open)).toBe(original.open);
        expect(Number(row.close)).toBe(original.close);
        expect(Number(row.volume)).toBe(original.volume);
      }
    });

    it("JSON round-trip preserves numeric types", async () => {
      const klines = makeKlines("600519", 3);
      await storage.upsertKlines(klines);

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );

      const parsed = JSON.parse(result.content);
      for (let i = 0; i < parsed.length; i++) {
        expect(typeof parsed[i].open).toBe("number");
        expect(typeof parsed[i].close).toBe("number");
        expect(typeof parsed[i].volume).toBe("number");
        expect(parsed[i].open).toBe(klines[i]!.open);
        expect(parsed[i].close).toBe(klines[i]!.close);
      }
    });
  });

  // ════════════════════════════════════════════════════════════
  // Special Characters & Edge Cases in Data
  // ════════════════════════════════════════════════════════════

  describe("special characters in data", () => {
    it("handles newlines in signal reason", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "Line1\nLine2",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      // Newline in value should be quoted
      expect(result.content).toContain('"Line1\nLine2"');
      expect(result.rowCount).toBe(1);
    });

    it("handles empty string fields gracefully", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "",
        strategyId: "",
      });

      const csvResult = await service.exportSignals({}, { format: "csv" });
      expect(csvResult.rowCount).toBe(1);

      const jsonResult = await service.exportSignals({}, { format: "json" });
      expect(jsonResult.rowCount).toBe(1);
    });

    it("handles zero values correctly", async () => {
      await storage.upsertKlines([{
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 0,
        high: 0,
        low: 0,
        close: 0,
        volume: 0,
        amount: 0,
      }]);

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );
      const parsed = JSON.parse(result.content);
      expect(parsed[0].open).toBe(0);
      expect(parsed[0].volume).toBe(0);
    });

    it("handles very large numbers", async () => {
      await storage.upsertKlines([{
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 999999.99,
        high: 999999.99,
        low: 999999.99,
        close: 999999.99,
        volume: 999999999,
        amount: 9999999999.99,
      }]);

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );
      expect(result.content).toContain("999999.99");
      expect(result.content).toContain("999999999");
    });

    it("handles negative PnL values in trades", async () => {
      await storage.insertTrade({
        symbol: "600519",
        side: "buy",
        entryPrice: 1900,
        exitPrice: 1800,
        quantity: 100,
        netPnl: -10000,
        pnlPct: -0.0526,
        commission: 10.8,
      });

      const csvResult = await service.exportTrades({}, { format: "csv" });
      expect(csvResult.content).toContain("-10000");

      const jsonResult = await service.exportTrades({}, { format: "json" });
      const parsed = JSON.parse(jsonResult.content);
      expect(parsed[0].netPnl).toBe(-10000);
    });
  });

  // ════════════════════════════════════════════════════════════
  // Integration: Warehouse → Export pipeline
  // ════════════════════════════════════════════════════════════

  describe("integration: storage → export pipeline", () => {
    it("exports data populated through multiple storage operations", async () => {
      // Simulate a trading day: klines, signals, orders, trades, equity
      await storage.upsertKlines(makeKlines("600519", 5));
      await storage.upsertKlines(makeKlines("000001", 5));

      for (const s of makeSignals(3, "600519")) await storage.insertSignal(s);
      for (const o of makeOrders(2, "600519")) await storage.upsertOrder(o);
      for (const t of makeTrades(1, "600519")) await storage.insertTrade(t);
      for (const e of makeEquity(5)) await storage.upsertEquitySnapshot(e);

      // Export everything
      const klineExport = await service.exportKlines({ symbol: "600519" });
      const signalExport = await service.exportSignals({ symbol: "600519" });
      const orderExport = await service.exportOrders({ symbol: "600519" });
      const tradeExport = await service.exportTrades({ symbol: "600519" });
      const equityExport = await service.exportEquity({});

      expect(klineExport.rowCount).toBe(5);
      expect(signalExport.rowCount).toBe(3);
      expect(orderExport.rowCount).toBe(2);
      expect(tradeExport.rowCount).toBe(1);
      expect(equityExport.rowCount).toBe(5);
    });

    it("exports are isolated by symbol", async () => {
      await storage.upsertKlines(makeKlines("600519", 5));
      await storage.upsertKlines(makeKlines("000001", 3));

      const r1 = await service.exportKlines({ symbol: "600519" });
      const r2 = await service.exportKlines({ symbol: "000001" });

      expect(r1.rowCount).toBe(5);
      expect(r2.rowCount).toBe(3);
    });

    it("re-export after data update reflects changes", async () => {
      await storage.upsertKlines(makeKlines("600519", 3));
      const first = await service.exportKlines({ symbol: "600519" });
      expect(first.rowCount).toBe(3);

      // Add more data
      await storage.upsertKlines(makeKlines("600519", 5, "2024-01-10"));
      const second = await service.exportKlines({ symbol: "600519" });
      expect(second.rowCount).toBe(8);
    });
  });

  // ════════════════════════════════════════════════════════════
  // Large Data Volume — Orders, Trades, Equity
  // ════════════════════════════════════════════════════════════

  describe("large data volume — orders, trades, equity", () => {
    it("exports 500 orders across CSV, JSON, TSV", async () => {
      for (let i = 0; i < 500; i++) {
        await storage.upsertOrder({
          orderId: `order-${i}`,
          symbol: `stock-${i % 10}`,
          market: "cn_stock",
          side: i % 2 === 0 ? "buy" : "sell",
          orderType: "limit",
          quantity: 100 + i,
          price: 10 + i * 0.1,
          status: i % 3 === 0 ? "filled" : "pending",
          strategyId: "bulk-test",
        });
      }

      const csvResult = await service.exportOrders({}, { format: "csv" });
      expect(csvResult.rowCount).toBe(500);
      expect(csvResult.content.split("\n")).toHaveLength(501);

      const jsonResult = await service.exportOrders({}, { format: "json" });
      const parsed = JSON.parse(jsonResult.content);
      expect(parsed).toHaveLength(500);

      const tsvResult = await service.exportOrders({}, { format: "tsv" });
      expect(tsvResult.rowCount).toBe(500);
    });

    it("exports 500 trades", async () => {
      for (let i = 0; i < 500; i++) {
        await storage.insertTrade({
          symbol: `stock-${i % 10}`,
          side: "buy",
          entryPrice: 100 + i,
          exitPrice: 105 + i,
          quantity: 100,
          netPnl: 500 - i,
          pnlPct: 0.05 - i * 0.0001,
          commission: 5.4,
          strategyId: "bulk-test",
        });
      }

      const csvResult = await service.exportTrades({}, { format: "csv" });
      expect(csvResult.rowCount).toBe(500);

      const jsonResult = await service.exportTrades({}, { format: "json" });
      expect(JSON.parse(jsonResult.content)).toHaveLength(500);
    });

    it("exports 500 equity snapshots", async () => {
      for (let i = 0; i < 500; i++) {
        const date = new Date(Date.UTC(2022, 0, 1 + i));
        await storage.upsertEquitySnapshot({
          date: date.toISOString().slice(0, 10),
          equity: 100000 + i * 100,
          cash: 50000 - i * 50,
          marketValue: 50000 + i * 150,
          dailyPnl: 100 - (i % 5 === 0 ? 500 : 0),
          positionCount: 3 + (i % 4),
          benchmark: 100000 + i * 80,
        });
      }

      const csvResult = await service.exportEquity({}, { format: "csv" });
      expect(csvResult.rowCount).toBe(500);

      const jsonResult = await service.exportEquity({}, { format: "json" });
      expect(JSON.parse(jsonResult.content)).toHaveLength(500);
    });
  });

  // ════════════════════════════════════════════════════════════
  // maxRows — Orders, Trades, Equity
  // ════════════════════════════════════════════════════════════

  describe("maxRows — orders, trades, equity", () => {
    it("limits order export rows", async () => {
      for (const o of makeOrders(20)) {
        await storage.upsertOrder(o);
      }

      const result = await service.exportOrders({}, { maxRows: 5 });
      expect(result.rowCount).toBe(5);
    });

    it("limits trade export rows", async () => {
      for (const t of makeTrades(15)) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({}, { maxRows: 7 });
      expect(result.rowCount).toBe(7);
    });

    it("limits equity export rows", async () => {
      for (const e of makeEquity(20)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity({}, { maxRows: 3 });
      expect(result.rowCount).toBe(3);
    });

    it("maxRows=1 returns exactly one row", async () => {
      for (const t of makeTrades(5)) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({}, { format: "csv", maxRows: 1 });
      expect(result.rowCount).toBe(1);
      const lines = result.content.split("\n");
      expect(lines).toHaveLength(2); // header + 1 row
    });
  });

  // ════════════════════════════════════════════════════════════
  // Unicode / CJK Characters
  // ════════════════════════════════════════════════════════════

  describe("Unicode / CJK characters", () => {
    it("handles Chinese characters in signal reason (CSV)", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "突破前高，放量上涨",
        strategyId: "龙虎榜策略",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      expect(result.rowCount).toBe(1);
      expect(result.content).toContain("突破前高，放量上涨");
      expect(result.content).toContain("龙虎榜策略");
    });

    it("handles Chinese characters in signal reason (JSON)", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "MACD金叉 + 成交量放大",
        strategyId: "技术指标策略",
      });

      const result = await service.exportSignals({}, { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(parsed[0].reason).toBe("MACD金叉 + 成交量放大");
      expect(parsed[0].strategyId).toBe("技术指标策略");
    });

    it("CSV-escapes Chinese text containing commas", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "突破前高,放量上涨",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      // Chinese text with comma should be quoted
      expect(result.content).toContain('"突破前高,放量上涨"');
    });
  });

  // ════════════════════════════════════════════════════════════
  // CSV Column Count Consistency
  // ════════════════════════════════════════════════════════════

  describe("CSV column count consistency", () => {
    it("all kline CSV rows have the same column count as header", async () => {
      await storage.upsertKlines(makeKlines("600519", 10));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "csv" },
      );
      const lines = result.content.split("\n");
      const headerColCount = lines[0]!.split(",").length;
      expect(headerColCount).toBe(9); // KLINE_COLUMNS length

      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]!.split(",").length).toBe(headerColCount);
      }
    });

    it("all order CSV rows have consistent column count", async () => {
      for (const o of makeOrders(5)) {
        await storage.upsertOrder(o);
      }

      const result = await service.exportOrders({}, { format: "csv" });
      const lines = result.content.split("\n");
      const headerColCount = lines[0]!.split(",").length;
      expect(headerColCount).toBe(13); // ORDER_COLUMNS length

      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]!.split(",").length).toBe(headerColCount);
      }
    });

    it("all trade CSV rows have consistent column count", async () => {
      for (const t of makeTrades(5)) {
        await storage.insertTrade(t);
      }

      const result = await service.exportTrades({}, { format: "csv" });
      const lines = result.content.split("\n");
      const headerColCount = lines[0]!.split(",").length;
      expect(headerColCount).toBe(12); // TRADE_COLUMNS length

      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]!.split(",").length).toBe(headerColCount);
      }
    });

    it("all equity CSV rows have consistent column count", async () => {
      for (const e of makeEquity(5)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity({}, { format: "csv" });
      const lines = result.content.split("\n");
      const headerColCount = lines[0]!.split(",").length;
      expect(headerColCount).toBe(7); // EQUITY_COLUMNS length

      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]!.split(",").length).toBe(headerColCount);
      }
    });

    it("TSV column count matches header", async () => {
      await storage.upsertKlines(makeKlines("600519", 5));

      const result = await service.exportKlines(
        { symbol: "600519" },
        { format: "tsv" },
      );
      const lines = result.content.split("\n");
      const headerColCount = lines[0]!.split("\t").length;

      for (let i = 1; i < lines.length; i++) {
        expect(lines[i]!.split("\t").length).toBe(headerColCount);
      }
    });
  });

  // ════════════════════════════════════════════════════════════
  // Carriage Return Handling
  // ════════════════════════════════════════════════════════════

  describe("carriage return handling", () => {
    it("escapes carriage return in CSV values", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "Line1\r\nLine2",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      // \r\n in value should be quoted
      expect(result.content).toContain('"Line1\r\nLine2"');
      expect(result.rowCount).toBe(1);
    });

    it("escapes standalone carriage return in CSV values", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        reason: "Line1\rLine2",
        strategyId: "test",
      });

      const result = await service.exportSignals({}, { format: "csv" });
      expect(result.content).toContain('"Line1\rLine2"');
    });
  });

  // ════════════════════════════════════════════════════════════
  // Concurrent Export Operations
  // ════════════════════════════════════════════════════════════

  describe("concurrent export operations", () => {
    it("handles multiple simultaneous exports without interference", async () => {
      await storage.upsertKlines(makeKlines("600519", 10));
      for (const s of makeSignals(5)) await storage.insertSignal(s);
      for (const o of makeOrders(4)) await storage.upsertOrder(o);
      for (const t of makeTrades(3)) await storage.insertTrade(t);
      for (const e of makeEquity(7)) await storage.upsertEquitySnapshot(e);

      // Fire all exports concurrently
      const [klines, signals, orders, trades, equity] = await Promise.all([
        service.exportKlines({ symbol: "600519" }, { format: "csv" }),
        service.exportSignals({}, { format: "json" }),
        service.exportOrders({}, { format: "tsv" }),
        service.exportTrades({}, { format: "csv" }),
        service.exportEquity({}, { format: "json" }),
      ]);

      expect(klines.rowCount).toBe(10);
      expect(klines.format).toBe("csv");
      expect(signals.rowCount).toBe(5);
      expect(signals.format).toBe("json");
      expect(orders.rowCount).toBe(4);
      expect(orders.format).toBe("tsv");
      expect(trades.rowCount).toBe(3);
      expect(trades.format).toBe("csv");
      expect(equity.rowCount).toBe(7);
      expect(equity.format).toBe("json");
    });

    it("concurrent exports of same data type in different formats produce consistent counts", async () => {
      await storage.upsertKlines(makeKlines("600519", 20));

      const [csv, json, tsv] = await Promise.all([
        service.exportKlines({ symbol: "600519" }, { format: "csv" }),
        service.exportKlines({ symbol: "600519" }, { format: "json" }),
        service.exportKlines({ symbol: "600519" }, { format: "tsv" }),
      ]);

      expect(csv.rowCount).toBe(20);
      expect(json.rowCount).toBe(20);
      expect(tsv.rowCount).toBe(20);
    });
  });

  // ════════════════════════════════════════════════════════════
  // Combined Filters
  // ════════════════════════════════════════════════════════════

  describe("combined filters", () => {
    it("applies both symbol filter and maxRows on orders", async () => {
      for (const o of makeOrders(10, "600519")) await storage.upsertOrder(o);
      for (let i = 0; i < 5; i++) {
        await storage.upsertOrder({
          orderId: `other-${i}`,
          symbol: "000001",
          side: "buy",
          orderType: "limit",
          quantity: 100,
          price: 10,
          status: "filled",
        });
      }

      const result = await service.exportOrders(
        { symbol: "600519" },
        { format: "json", maxRows: 3 },
      );
      expect(result.rowCount).toBe(3);
      const parsed = JSON.parse(result.content);
      for (const row of parsed) {
        expect(row.symbol).toBe("600519");
      }
    });

    it("applies both date filter and maxRows on equity", async () => {
      for (const e of makeEquity(30)) {
        await storage.upsertEquitySnapshot(e);
      }

      const result = await service.exportEquity(
        { startDate: "2024-01-10", endDate: "2024-01-25" },
        { maxRows: 5 },
      );
      expect(result.rowCount).toBe(5);
    });

    it("symbol filter + strategyId filter on signals", async () => {
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 2),
        symbol: "600519",
        direction: "buy",
        price: 1800,
        strategyId: "macd",
      });
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 3),
        symbol: "600519",
        direction: "sell",
        price: 1850,
        strategyId: "rsi",
      });
      await storage.insertSignal({
        timestamp: Date.UTC(2024, 0, 4),
        symbol: "000001",
        direction: "buy",
        price: 10,
        strategyId: "macd",
      });

      const result = await service.exportSignals(
        { symbol: "600519", strategyId: "macd" },
        { format: "json" },
      );
      expect(result.rowCount).toBe(1);
      const parsed = JSON.parse(result.content);
      expect(parsed[0].symbol).toBe("600519");
      expect(parsed[0].strategyId).toBe("macd");
    });
  });

  // ════════════════════════════════════════════════════════════
  // JSON Array Structure Validation
  // ════════════════════════════════════════════════════════════

  describe("JSON structure validation", () => {
    it("JSON export always produces valid JSON array", async () => {
      // Empty data
      const empty = await service.exportKlines(
        { symbol: "NONE" },
        { format: "json" },
      );
      expect(() => JSON.parse(empty.content)).not.toThrow();
      expect(Array.isArray(JSON.parse(empty.content))).toBe(true);

      // Single row
      await storage.upsertKlines(makeKlines("600519", 1));
      const single = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );
      expect(Array.isArray(JSON.parse(single.content))).toBe(true);

      // Multiple rows
      await storage.upsertKlines(makeKlines("600519", 10));
      const multi = await service.exportKlines(
        { symbol: "600519" },
        { format: "json" },
      );
      expect(Array.isArray(JSON.parse(multi.content))).toBe(true);
    });

    it("JSON keys match expected column names for each data type", async () => {
      await storage.upsertKlines(makeKlines("600519", 1));
      for (const s of makeSignals(1)) await storage.insertSignal(s);
      for (const o of makeOrders(1)) await storage.upsertOrder(o);
      for (const t of makeTrades(1)) await storage.insertTrade(t);
      for (const e of makeEquity(1)) await storage.upsertEquitySnapshot(e);

      const klineJson = JSON.parse(
        (await service.exportKlines({ symbol: "600519" }, { format: "json" })).content,
      );
      expect(Object.keys(klineJson[0])).toEqual(
        expect.arrayContaining(["symbol", "date", "open", "high", "low", "close", "volume"]),
      );

      const signalJson = JSON.parse(
        (await service.exportSignals({}, { format: "json" })).content,
      );
      expect(Object.keys(signalJson[0])).toEqual(
        expect.arrayContaining(["symbol", "direction", "price"]),
      );

      const orderJson = JSON.parse(
        (await service.exportOrders({}, { format: "json" })).content,
      );
      expect(Object.keys(orderJson[0])).toEqual(
        expect.arrayContaining(["orderId", "symbol", "side", "status"]),
      );

      const tradeJson = JSON.parse(
        (await service.exportTrades({}, { format: "json" })).content,
      );
      expect(Object.keys(tradeJson[0])).toEqual(
        expect.arrayContaining(["symbol", "entryPrice", "exitPrice", "netPnl"]),
      );

      const equityJson = JSON.parse(
        (await service.exportEquity({}, { format: "json" })).content,
      );
      expect(Object.keys(equityJson[0])).toEqual(
        expect.arrayContaining(["date", "equity", "cash", "marketValue"]),
      );
    });
  });
});
