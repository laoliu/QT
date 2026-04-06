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
 *  - FR-7.10: Watchlist/favorites export
 *  - FR-7.11: Delete exported files
 *  - FR-7.12: Clean all exported files
 *  - FR-7.13: Batch export (multi-type)
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
  WATCHLIST_COLUMNS,
  ALERT_COLUMNS,
  HITRATE_COLUMNS,
  PERFORMANCE_COLUMNS,
  FUND_FLOW_COLUMNS,
  SECTOR_COLUMNS,
  INSTITUTIONAL_COLUMNS,
  CONDITIONAL_ORDER_COLUMNS,
  DCA_PLAN_COLUMNS,
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
    getWatchlist: async () => [
      { code: "600519", name: "贵州茅台", category: "watchlist", priority: 5, priceTarget: 2000, convictionScore: 9, tags: "白酒;消费", notes: "龙头", addedAt: "2026-01-01T00:00:00Z", updatedAt: "2026-03-01T00:00:00Z" },
      { code: "000858", name: "五粮液", category: "watchlist", priority: 3, priceTarget: 160, convictionScore: 7, tags: "白酒", notes: "", addedAt: "2026-02-01T00:00:00Z", updatedAt: "2026-03-01T00:00:00Z" },
    ],
    getAlerts: async () => [
      { id: "alert_abc1", timestamp: "2026-03-15T10:00:00Z", severity: "warning", source: "pnl_monitor", message: "日内亏损超过2%", acknowledged: "否" },
      { id: "alert_abc2", timestamp: "2026-03-15T14:30:00Z", severity: "critical", source: "risk_gate", message: "持仓集中度超限", acknowledged: "是" },
    ],
    getHitRateRecords: async () => [
      { signalTimestamp: "2026-03-01T09:31:00Z", symbol: "600519", direction: "buy", signalPrice: 1800, strategyId: "macd", reason: "MACD金叉", confidence: 0.85, "T+1_result": "hit", "T+1_price": 1820, "T+3_result": "hit", "T+3_price": 1850, "T+5_result": "miss", "T+5_price": 1790, "T+10_result": "pending", "T+10_price": "" },
    ],
    getPerformanceSummary: async () => [
      { metric: "统计区间", value: "2026-01-01 ~ 2026-03-31" },
      { metric: "总交易笔数", value: 15 },
      { metric: "盈利笔数", value: 10 },
      { metric: "亏损笔数", value: 5 },
      { metric: "胜率", value: "66.7%" },
      { metric: "盈亏比 (Profit Factor)", value: "2.50" },
      { metric: "总净盈亏", value: "25000.00" },
      { metric: "总佣金", value: "750.00" },
      { metric: "最大单笔盈利", value: "8000.00" },
      { metric: "最大单笔亏损", value: "-3000.00" },
      { metric: "累计收益率", value: "25.00%" },
      { metric: "最大回撤", value: "8.50%" },
    ],
    getFundFlow: async () => [
      { symbol: "600519", name: "贵州茅台", changePct: 2.5, mainNetInflow: 150000000, superLargeInflow: 80000000, largeInflow: 70000000, mediumInflow: -20000000, smallInflow: -30000000, date: "2026-03-15" },
      { symbol: "000858", name: "五粮液", changePct: 1.8, mainNetInflow: 80000000, superLargeInflow: 50000000, largeInflow: 30000000, mediumInflow: -10000000, smallInflow: -15000000, date: "2026-03-15" },
    ],
    getSectorAnalysis: async () => [
      { rank: 1, sector: "半导体", changePct: 3.5, turnover: 8.2, amount: 5200000000, leadingStock: "中芯国际", leadingChangePct: 7.8, stockCount: 120, upCount: 95, downCount: 25 },
      { rank: 2, sector: "白酒", changePct: 2.1, turnover: 3.5, amount: 3800000000, leadingStock: "贵州茅台", leadingChangePct: 2.5, stockCount: 35, upCount: 30, downCount: 5 },
    ],
    getInstitutionalFlow: async () => [
      { symbol: "600519", name: "贵州茅台", instType: "fund", holdShares: 85000000, holdRatio: 6.77, changeShares: 2000000, changeRatio: 2.41, reportDate: "2025-12-31" },
      { symbol: "000858", name: "五粮液", instType: "fund", holdShares: 120000000, holdRatio: 3.09, changeShares: -5000000, changeRatio: -4.00, reportDate: "2025-12-31" },
    ],
    getConditionalOrders: async (activeOnly) => {
      const all = [
        { id: "co-001", symbol: "600519", type: "stop_loss", entryPrice: 1800, triggerValue: 1700, quantity: 100, strategyId: "macd", description: "茅台止损单", createdAt: "2026-03-01T09:30:00Z", triggered: false, triggeredAt: "", triggerResult: "" },
        { id: "co-002", symbol: "000858", type: "take_profit", entryPrice: 130, triggerValue: 160, quantity: 200, strategyId: "", description: "五粮液止盈单", createdAt: "2026-03-01T10:00:00Z", triggered: true, triggeredAt: "2026-03-10T14:50:00Z", triggerResult: "已成交" },
      ];
      return activeOnly ? all.filter((o) => !o.triggered) : all;
    },
    getDcaPlans: async (status) => {
      const all = [
        { id: "dca-001", symbol: "600519", amount: 5000, frequency: "weekly", dayOfPeriod: 1, hour: 9, status: "active", executionCount: 8, totalInvested: 40000, lastExecutedAt: "2026-03-31T09:00:00Z", nextExecutionAt: "2026-04-07T09:00:00Z", maxExecutions: "", createdAt: "2026-02-01T00:00:00Z", description: "每周定投茅台 ¥5000" },
        { id: "dca-002", symbol: "300750", amount: 3000, frequency: "monthly", dayOfPeriod: 15, hour: 9, status: "paused", executionCount: 3, totalInvested: 9000, lastExecutedAt: "2026-03-15T09:00:00Z", nextExecutionAt: "", maxExecutions: 12, createdAt: "2026-01-01T00:00:00Z", description: "每月定投宁德时代 ¥3000" },
      ];
      return status ? all.filter((p) => p.status === status) : all;
    },
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

  it("exports watchlist/favorites to CSV (FR-7.10)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "watchlist" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^watchlist-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of WATCHLIST_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("贵州茅台");
    expect(content).toContain("600519");
    expect(content).toContain("白酒");
  });

  it("exports watchlist filtered by category", async () => {
    const svc = new DataExportService({
      dataSource: mockDataSource({
        getWatchlist: async (category) => {
          const all = [
            { code: "600519", name: "贵州茅台", category: "watchlist", addedAt: "2026-01-01", updatedAt: "2026-03-01" },
            { code: "300750", name: "宁德时代", category: "ideas", addedAt: "2026-02-01", updatedAt: "2026-03-01" },
          ];
          return category ? all.filter((s) => s.category === category) : all;
        },
      }),
      exportDir: tmpDir,
    });

    const result = await svc.export({ type: "watchlist", category: "ideas" });
    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("宁德时代");
    expect(content).not.toContain("贵州茅台");
  });

  it("deletes a single exported file (FR-7.11)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "positions" });

    const filesBefore = await svc.listExports();
    expect(filesBefore).toHaveLength(1);

    const deleted = await svc.deleteExport(result.filename);
    expect(deleted).toBe(true);

    const filesAfter = await svc.listExports();
    expect(filesAfter).toHaveLength(0);
  });

  it("deleteExport returns false for non-existent file", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const deleted = await svc.deleteExport("non-existent.csv");
    expect(deleted).toBe(false);
  });

  it("cleans all exported files (FR-7.12)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    await svc.export({ type: "positions" });
    await svc.export({ type: "trades" });
    await svc.export({ type: "signals" }, "json");

    const filesBefore = await svc.listExports();
    expect(filesBefore).toHaveLength(3);

    const count = await svc.deleteAllExports();
    expect(count).toBe(3);

    const filesAfter = await svc.listExports();
    expect(filesAfter).toHaveLength(0);
  });

  it("batch exports multiple datasets (FR-7.13)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const results = await svc.exportBatch([
      { type: "positions" },
      { type: "trades" },
      { type: "signals" },
    ]);

    expect(results).toHaveLength(3);
    expect(results[0].filename).toMatch(/^positions-/);
    expect(results[1].filename).toMatch(/^trades-/);
    expect(results[2].filename).toMatch(/^signals-/);

    const files = await svc.listExports();
    expect(files).toHaveLength(3);
  });

  it("batch exports with JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const results = await svc.exportBatch([
      { type: "positions" },
      { type: "watchlist" },
    ], "json");

    expect(results).toHaveLength(2);
    for (const r of results) {
      expect(r.format).toBe("json");
      expect(r.filename).toMatch(/\.json$/);
    }
  });

  it("exports alerts history to CSV", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "alerts" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^alerts-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of ALERT_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("pnl_monitor");
    expect(content).toContain("risk_gate");
    expect(content).toContain("日内亏损超过2%");
  });

  it("exports alerts to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "alerts" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].severity).toBe("warning");
  });

  it("exports signal hit-rate records to CSV", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "hitrate" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(1);
    expect(result.filename).toMatch(/^hitrate-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    expect(header).toContain("signalTimestamp");
    expect(header).toContain("T+1_result");
    expect(header).toContain("T+10_price");
    expect(content).toContain("600519");
    expect(content).toContain("macd");
    expect(content).toContain("hit");
  });

  it("exports performance summary to CSV", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "performance" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(12);
    expect(result.filename).toMatch(/^performance-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of PERFORMANCE_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("胜率");
    expect(content).toContain("最大回撤");
    expect(content).toContain("66.7%");
  });

  it("exports performance summary to JSON", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "performance" }, "json");

    expect(result.format).toBe("json");
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(12);
    expect(parsed[0].metric).toBe("统计区间");
  });

  it("batch exports include new types (alerts, performance)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const results = await svc.exportBatch([
      { type: "alerts" },
      { type: "hitrate" },
      { type: "performance" },
    ]);

    expect(results).toHaveLength(3);
    expect(results[0].filename).toMatch(/^alerts-/);
    expect(results[1].filename).toMatch(/^hitrate-/);
    expect(results[2].filename).toMatch(/^performance-/);
  });

  it("exports fund flow data to CSV (FR-7.14)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "fund_flow" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^fund_flow-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of FUND_FLOW_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("贵州茅台");
    expect(content).toContain("600519");
    expect(content).toContain("150000000");
  });

  it("exports fund flow to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "fund_flow" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].symbol).toBe("600519");
  });

  it("exports sector analysis data to CSV (FR-7.15)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "sector" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^sector-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of SECTOR_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("半导体");
    expect(content).toContain("白酒");
    expect(content).toContain("中芯国际");
  });

  it("exports sector analysis to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "sector" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].sector).toBe("半导体");
  });

  it("exports institutional flow data to CSV (FR-7.16)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "institutional" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^institutional-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of INSTITUTIONAL_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("贵州茅台");
    expect(content).toContain("600519");
    expect(content).toContain("fund");
    expect(content).toContain("2025-12-31");
  });

  it("exports institutional flow to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "institutional" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].instType).toBe("fund");
  });

  it("batch exports include market data types (fund_flow, sector, institutional)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const results = await svc.exportBatch([
      { type: "fund_flow" },
      { type: "sector" },
      { type: "institutional" },
    ]);

    expect(results).toHaveLength(3);
    expect(results[0].filename).toMatch(/^fund_flow-/);
    expect(results[1].filename).toMatch(/^sector-/);
    expect(results[2].filename).toMatch(/^institutional-/);
    expect(results[0].rowCount).toBe(2);
    expect(results[1].rowCount).toBe(2);
    expect(results[2].rowCount).toBe(2);
  });

  it("exports conditional orders to CSV (FR-7.17)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "conditional_orders" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^conditional_orders-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of CONDITIONAL_ORDER_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("600519");
    expect(content).toContain("stop_loss");
    expect(content).toContain("take_profit");
    expect(content).toContain("茅台止损单");
  });

  it("exports conditional orders with activeOnly filter", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "conditional_orders", activeOnly: true });

    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("stop_loss");
    expect(content).not.toContain("take_profit");
  });

  it("exports conditional orders to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "conditional_orders" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].type).toBe("stop_loss");
  });

  it("exports DCA plans to CSV (FR-7.18)", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "dca_plans" });

    expect(result.format).toBe("csv");
    expect(result.rowCount).toBe(2);
    expect(result.filename).toMatch(/^dca_plans-/);

    const content = await fs.readFile(result.path, "utf-8");
    const header = content.split("\n")[0].replace("\uFEFF", "");
    for (const col of DCA_PLAN_COLUMNS) {
      expect(header).toContain(col);
    }
    expect(content).toContain("600519");
    expect(content).toContain("weekly");
    expect(content).toContain("每周定投茅台");
  });

  it("exports DCA plans with status filter", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "dca_plans", status: "active" });

    expect(result.rowCount).toBe(1);
    const content = await fs.readFile(result.path, "utf-8");
    expect(content).toContain("600519");
    expect(content).not.toContain("300750");
  });

  it("exports DCA plans to JSON format", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const result = await svc.export({ type: "dca_plans" }, "json");

    expect(result.format).toBe("json");
    expect(result.rowCount).toBe(2);
    const content = await fs.readFile(result.path, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].frequency).toBe("weekly");
  });

  it("batch exports include conditional_orders and dca_plans", async () => {
    const svc = new DataExportService({ dataSource: mockDataSource(), exportDir: tmpDir });
    const results = await svc.exportBatch([
      { type: "conditional_orders" },
      { type: "dca_plans" },
    ]);

    expect(results).toHaveLength(2);
    expect(results[0].filename).toMatch(/^conditional_orders-/);
    expect(results[1].filename).toMatch(/^dca_plans-/);
    expect(results[0].rowCount).toBe(2);
    expect(results[1].rowCount).toBe(2);
  });
});
