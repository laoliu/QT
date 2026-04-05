/**
 * Tests for DataExportService — CSV/JSON export with UTF-8 BOM.
 *
 * Covers:
 *  - FR-7.1: Position/order/trade export to CSV
 *  - FR-7.2: Backtest result export
 *  - FR-7.3: Screen result export
 *  - FR-7.4: Correct file generation
 *  - FR-7.5: Signal history export
 *  - FR-7.6: Journal/trade notes export
 *  - FR-7.7: K-line data export
 *  - FR-7.8: JSON format export
 *  - FR-7.9: List exported files
 *  - NFR-10: UTF-8 BOM for Chinese Excel compatibility
 *  - NFR-3: Export under 5s (for 10k rows)
 *  - AC-5: CSV columns match specification
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  DataExportService,
  toCsv,
  POSITION_COLUMNS,
  ORDER_COLUMNS,
  TRADE_COLUMNS,
  SIGNAL_COLUMNS,
  JOURNAL_COLUMNS,
  KLINE_COLUMNS,
  EQUITY_COLUMNS,
  type DataSource,
  type ExportableRow,
} from "./data-export.js";

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-export-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

// ── Mock data source ────────────────────────────────────────

function mockDataSource(overrides?: Partial<DataSource>): DataSource {
  return {
    getPositions: async () => [
      { symbol: "600519", name: "贵州茅台", market: "cn_stock", quantity: 100, avgCost: 1800, currentPrice: 1850, marketValue: 185000, unrealizedPnl: 5000, unrealizedPnlPct: 0.0278 },
      { symbol: "000858", name: "五粮液", market: "cn_stock", quantity: 200, avgCost: 130, currentPrice: 135, marketValue: 27000, unrealizedPnl: 1000, unrealizedPnlPct: 0.0385 },
    ],
    getOrders: async () => [
      { orderId: "O-001", symbol: "600519", side: "buy", orderType: "market", price: null, quantity: 100, filledQty: 100, avgFillPrice: 1850, status: "filled", createdAt: "2026-03-01T09:30:00Z", filledAt: "2026-03-01T09:30:01Z" },
    ],
    getTrades: async () => [
      { tradeId: "T-001", symbol: "600519", name: "贵州茅台", side: "buy", entryTime: "2026-03-01", exitTime: "2026-03-15", entryPrice: 1800, exitPrice: 1850, quantity: 100, grossPnl: 5000, commission: 92.5, netPnl: 4907.5, holdingDays: 14, strategyId: "macd" },
    ],
    getSignals: async () => [
      { date: "2026-03-01", timestamp: "2026-03-01T09:31:00Z", symbol: "600519", direction: "buy", price: 1800, quantity: 100, reason: "MACD金叉", confidence: 0.85, strategyId: "macd" },
      { date: "2026-03-15", timestamp: "2026-03-15T14:50:00Z", symbol: "600519", direction: "sell", price: 1850, quantity: 100, reason: "止盈触发", confidence: 0.9, strategyId: "macd" },
    ],
    getJournalNotes: async () => [
      { id: "J-001", date: "2026-03-15", symbol: "600519", direction: "buy", entryPrice: 1800, exitPrice: 1850, pnl: 4907.5, rating: 4, tags: "macd;趋势", content: "MACD金叉买入，趋势向上确认", lesson: "等待确认信号再入场", strategyId: "macd", createdAt: "2026-03-15T20:00:00Z" },
    ],
    getKlines: async () => [
      { symbol: "600519", date: "2026-03-01", interval: "1d", open: 1795, high: 1860, low: 1790, close: 1850, volume: 15000, amount: 27750000 },
      { symbol: "600519", date: "2026-03-02", interval: "1d", open: 1850, high: 1870, low: 1840, close: 1865, volume: 12000, amount: 22380000 },
    ],
    getBacktestResult: async () => [
      { trade: 1, symbol: "600519", entry: "2026-01-01", exit: "2026-01-15", pnl: 3000 },
    ],
    getScreenResult: async () => [
      { rank: 1, symbol: "600519", name: "贵州茅台", score: 95 },
    ],
    getEquityHistory: async () => [
      { date: "2026-03-01", equity: 100000, cash: 50000, marketValue: 50000, dailyPnl: 500, positionCount: 2, benchmark: 0.012 },
    ],
    ...overrides,
  };
}

// ── CSV helper tests ────────────────────────────────────────

describe("toCsv()", () => {
  it("produces BOM-prefixed CSV", () => {
    const csv = toCsv([{ a: 1, b: "hello" }]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
  });

  it("handles empty rows", () => {
    const csv = toCsv([]);
    expect(csv).toBe("\uFEFF");
  });

  it("escapes commas and quotes in fields", () => {
    const csv = toCsv([{ name: 'say "hello"', value: "a,b,c" }]);
    expect(csv).toContain('"say ""hello"""');
    expect(csv).toContain('"a,b,c"');
  });

  it("handles null and undefined values", () => {
    const csv = toCsv([{ a: null, b: undefined, c: 0 }]);
    expect(csv).toContain(",,0");
  });

  it("respects custom column order", () => {
    const csv = toCsv([{ a: 1, b: 2, c: 3 }], ["c", "a"]);
    const lines = csv.split("\n");
    expect(lines[0]).toBe("\uFEFFc,a");
    expect(lines[1]).toBe("3,1");
  });

  it("handles Chinese characters", () => {
    const csv = toCsv([{ symbol: "600519", name: "贵州茅台" }]);
    expect(csv).toContain("贵州茅台");
  });
});

// ── DataExportService tests ─────────────────────────────────

describe("DataExportService", () => {
  it("exports positions to CSV file", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "positions" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^positions-\d{4}-\d{2}-\d{2}-\d+\.csv$/);

    const content = await fs.readFile(result.path, "utf-8");
    expect(content.startsWith("\uFEFF")).toBe(true); // BOM
    expect(content).toContain("symbol");
    expect(content).toContain("600519");
    expect(content).toContain("贵州茅台");
  });

  it("exports orders to CSV file", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "orders" });

    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("O-001");
    expect(content).toContain("filled");
  });

  it("exports trades to CSV with all required columns (AC-5)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "trades" });

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    const cols = header.split(",");

    // AC-5: 时间、代码、名称、方向、价格、数量、金额、佣金、盈亏
    expect(cols).toContain("symbol");
    expect(cols).toContain("name");
    expect(cols).toContain("side");
    expect(cols).toContain("entryPrice");
    expect(cols).toContain("quantity");
    expect(cols).toContain("commission");
    expect(cols).toContain("netPnl");
  });

  it("exports backtest results", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "backtest", backtestId: "bt-001" });

    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("600519");
  });

  it("exports screen results", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "screen", screenId: "sc-001" });

    expect(result.rowCount).toBe(1);
  });

  it("exports equity history", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "equity" });

    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("equity");
    expect(content).toContain("100000");
    expect(content).toContain("benchmark");
  });

  it("exports signals to CSV (FR-7.5)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "signals" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^signals-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of SIGNAL_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("MACD金叉");
    expect(content).toContain("600519");
  });

  it("exports journal notes to CSV (FR-7.6)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "journal" });

    expect(result.rowCount).toBe(1);
    expect(result.filename).toMatch(/^journal-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of JOURNAL_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("MACD金叉买入");
    expect(content).toContain("等待确认信号再入场");
  });

  it("exports klines to CSV (FR-7.7)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "klines", symbol: "600519" });

    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^klines_600519-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of KLINE_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("1860"); // high
    expect(content).toContain("27750000"); // amount
  });

  it("exports to JSON format (FR-7.8)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "positions" }, "json");

    expect(result.format).toBe("json");
    expect(result.filename).toMatch(/\.json$/);
    expect(result.rowCount).toBe(2);

    const content = await fs.readFile(result.path, "utf-8");
    expect(content.startsWith("\uFEFF")).toBe(false); // JSON does not need BOM
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].symbol).toBe("600519");
  });

  it("lists exported files (FR-7.9)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });

    // Export two files first
    await svc.export({ type: "positions" });
    await svc.export({ type: "trades" }, "json");

    const files = await svc.listExports();
    expect(files).toHaveLength(2);
    expect(files[0].filename).toBeDefined();
    expect(files[0].size).toBeGreaterThan(0);
    expect(files[0].sizeLabel).toBeDefined();
    expect(files[0].createdAt).toBeDefined();
  });

  it("listExports returns empty array for non-existent directory", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: path.join(tmpDir, "nope") });
    const files = await svc.listExports();
    expect(files).toEqual([]);
  });

  it("creates export directory if it doesn't exist", async () => {
    const nested = path.join(tmpDir, "nested", "exports");
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: nested });
    const result = await svc.export({ type: "positions" });
    expect(result.path.startsWith(nested)).toBe(true);
  });

  it("handles large dataset efficiently (NFR-3: <5s for 10k rows)", async () => {
    const bigData: ExportableRow[] = Array.from({ length: 10000 }, (_, i) => ({
      symbol: `${600000 + i}`,
      name: `股票${i}`,
      market: "cn_stock",
      quantity: 100 + i,
      avgCost: 10 + Math.random() * 100,
      currentPrice: 10 + Math.random() * 100,
      marketValue: 10000 + i * 10,
      unrealizedPnl: Math.random() * 1000 - 500,
      unrealizedPnlPct: Math.random() * 0.1 - 0.05,
    }));

    const svc = new DataExportService({
      dataSource: mockDataSource({ getPositions: async () => bigData }),
      exportDir: tmpDir,
    });

    const start = Date.now();
    const result = await svc.export({ type: "positions" });
    const elapsed = Date.now() - start;

    expect(result.rowCount).toBe(10000);
    expect(elapsed).toBeLessThan(5000); // NFR-3
  });
});
