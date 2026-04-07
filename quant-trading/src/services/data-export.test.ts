import { describe, it, expect } from "vitest";
import type { TradeRecord, LivePosition, BacktestResult } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { PerformanceReport } from "./performance-metrics.js";
import type { TradeNote } from "./trade-journal.js";
import {
  exportTrades,
  exportPositions,
  exportEquityHistory,
  exportPerformanceReport,
  exportTradeJournal,
  exportBacktestResult,
  type ExportOptions,
  type ExportResult,
} from "./data-export.js";

// ── Test Data Factories ─────────────────────────────────────

function fakeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: "t-001",
    symbol: "600519",
    name: "贵州茅台",
    market: "cn-a",
    side: "buy",
    entryTime: "2024-06-01T09:30:00Z",
    exitTime: "2024-06-05T14:30:00Z",
    entryPrice: 1800,
    exitPrice: 1850,
    quantity: 100,
    grossPnl: 5000,
    commission: 50,
    netPnl: 4950,
    holdingDays: 4,
    strategyId: "dual-ma-crossover",
    entryOrderId: "ord-001",
    exitOrderId: "ord-002",
    ...overrides,
  };
}

function fakePosition(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    symbol: "000858",
    name: "五粮液",
    market: "cn-a",
    quantity: 200,
    availableQty: 200,
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 150.5,
    currentPrice: 162.3,
    marketValue: 32460,
    unrealizedPnl: 2360,
    unrealizedPnlPct: 0.0784,
    source: "broker",
    lastSyncAt: "2024-06-10T15:00:00Z",
    ...overrides,
  };
}

function fakeEquitySnapshot(overrides: Partial<EquitySnapshot> = {}): EquitySnapshot {
  return {
    date: "2024-06-01",
    equity: 100000,
    cash: 50000,
    marketValue: 50000,
    dailyPnl: 500,
    positionCount: 3,
    ...overrides,
  };
}

function fakePerformanceReport(): PerformanceReport {
  return {
    dateRange: { from: "2024-01-01", to: "2024-06-30" },
    periodDays: 181,
    returns: {
      totalReturn: 0.15,
      annualizedReturn: 0.32,
      annualizedVolatility: 0.18,
      monthlyReturns: [
        { month: "2024-01", return: 0.03 },
        { month: "2024-02", return: -0.01 },
      ],
      weeklyReturns: [],
    },
    drawdown: {
      maxDrawdown: 0.08,
      maxDrawdownDuration: 15,
      maxDrawdownStart: "2024-03-01",
      maxDrawdownEnd: "2024-03-20",
      currentDrawdown: 0.02,
    },
    riskAdjusted: {
      sharpeRatio: 1.78,
      sortinoRatio: 2.15,
      calmarRatio: 4.0,
      recoveryFactor: 1.875,
      var95: 0.025,
    },
    tradeAnalysis: {
      totalTrades: 45,
      winningTrades: 27,
      losingTrades: 18,
      winRate: 0.6,
      profitFactor: 2.1,
      payoffRatio: 1.4,
      expectancy: 220,
      avgTradeNetPnl: 110,
      avgWin: 500,
      avgLoss: 350,
      largestWin: 2500,
      largestLoss: -1200,
      avgHoldingDays: 5.2,
      maxConsecutiveWins: 7,
      maxConsecutiveLosses: 3,
      kellyCriterion: 0.314,
      totalCommission: 1350,
    },
    tradeFrequency: {
      tradesPerDay: 1.2,
      tradesPerWeek: 3.5,
      tradesPerMonth: 7.5,
      activeTradingDays: 38,
    },
    capital: {
      initialCapital: 100000,
      finalValue: 115000,
      netProfit: 15000,
      avgCapitalUtilization: 0.72,
    },
  };
}

function fakeTradeNote(overrides: Partial<TradeNote> = {}): TradeNote {
  return {
    id: "tn-00001",
    symbol: "600519",
    date: "2024-06-01",
    content: "茅台突破前高，放量上涨",
    tags: ["突破", "放量"],
    rating: 4,
    direction: "buy",
    entryPrice: 1800,
    exitPrice: 1850,
    pnl: 4950,
    lesson: "顺势交易，不要过早止盈",
    strategyId: "dual-ma-crossover",
    createdAt: "2024-06-01T10:00:00Z",
    updatedAt: "2024-06-01T10:00:00Z",
    ...overrides,
  };
}

function fakeBacktestResult(): BacktestResult {
  return {
    strategyId: "dual-ma-crossover",
    symbols: ["600519"],
    startDate: "2024-01-01",
    endDate: "2024-06-30",
    initialCash: 100000,
    finalValue: 115000,
    totalReturn: 0.15,
    annualizedReturn: 0.32,
    maxDrawdown: 0.08,
    sharpeRatio: 1.78,
    winRate: 0.6,
    totalTrades: 45,
    signals: [],
    equityCurve: [
      { date: "2024-01-01", value: 100000 },
      { date: "2024-03-01", value: 105000 },
      { date: "2024-06-30", value: 115000 },
    ],
  };
}

// ── Helper to parse CSV output ──────────────────────────────

function parseCsvContent(content: string): { headers: string[]; rows: string[][] } {
  // Strip BOM if present
  const clean = content.replace(/^\uFEFF/, "");
  const lines = clean.split("\n").filter((l) => l.length > 0);
  const headers = lines[0]?.split(",") ?? [];
  const rows = lines.slice(1).map((l) => l.split(","));
  return { headers, rows };
}

function parseTsvContent(content: string): { headers: string[]; rows: string[][] } {
  const clean = content.replace(/^\uFEFF/, "");
  const lines = clean.split("\n").filter((l) => l.length > 0);
  const headers = lines[0]?.split("\t") ?? [];
  const rows = lines.slice(1).map((l) => l.split("\t"));
  return { headers, rows };
}

// ═══════════════════════════════════════════════════════════════
// Unit Tests
// ═══════════════════════════════════════════════════════════════

// ── exportTrades ────────────────────────────────────────────

describe("exportTrades", () => {
  describe("CSV format", () => {
    it("exports single trade with correct headers", () => {
      const result = exportTrades([fakeTrade()]);
      expect(result.filename).toBe("trades.csv");
      expect(result.mimeType).toBe("text/csv");
      expect(result.rowCount).toBe(1);

      const { headers } = parseCsvContent(result.content);
      expect(headers).toContain("交易ID");
      expect(headers).toContain("代码");
      expect(headers).toContain("净利润");
    });

    it("includes UTF-8 BOM by default", () => {
      const result = exportTrades([fakeTrade()]);
      expect(result.content.charCodeAt(0)).toBe(0xfeff);
    });

    it("excludes BOM when includeBom=false", () => {
      const result = exportTrades([fakeTrade()], { includeBom: false });
      expect(result.content.charCodeAt(0)).not.toBe(0xfeff);
    });

    it("exports multiple trades", () => {
      const trades = [
        fakeTrade({ tradeId: "t-001" }),
        fakeTrade({ tradeId: "t-002", symbol: "000858" }),
        fakeTrade({ tradeId: "t-003", symbol: "300750" }),
      ];
      const result = exportTrades(trades);
      expect(result.rowCount).toBe(3);
      const { rows } = parseCsvContent(result.content);
      expect(rows).toHaveLength(3);
    });

    it("handles empty trades array", () => {
      const result = exportTrades([]);
      expect(result.rowCount).toBe(0);
      const { headers, rows } = parseCsvContent(result.content);
      expect(headers.length).toBeGreaterThan(0); // header row still present
      expect(rows).toHaveLength(0);
    });

    it("escapes commas in fields", () => {
      const trade = fakeTrade({ name: "贵州茅台,酒业" });
      const result = exportTrades([trade]);
      // The name field should be quoted
      expect(result.content).toContain('"贵州茅台,酒业"');
    });

    it("escapes double quotes in fields", () => {
      const trade = fakeTrade({ name: '贵州"茅台"' });
      const result = exportTrades([trade]);
      expect(result.content).toContain('"贵州""茅台"""');
    });

    it("escapes newlines in fields", () => {
      const trade = fakeTrade({ name: "贵州\n茅台" });
      const result = exportTrades([trade]);
      expect(result.content).toContain('"贵州\n茅台"');
    });
  });

  describe("TSV format", () => {
    it("exports as tab-separated", () => {
      const result = exportTrades([fakeTrade()], { format: "tsv" });
      expect(result.filename).toBe("trades.tsv");
      expect(result.mimeType).toBe("text/tab-separated-values");

      const { headers, rows } = parseTsvContent(result.content);
      expect(headers.length).toBeGreaterThan(0);
      expect(rows).toHaveLength(1);
    });

    it("replaces tabs in field values", () => {
      const trade = fakeTrade({ name: "贵州\t茅台" });
      const result = exportTrades([trade], { format: "tsv" });
      // Tab in value should be replaced with space
      const { rows } = parseTsvContent(result.content);
      const nameCol = rows[0]?.[1]; // name is second column
      expect(nameCol).not.toContain("\t");
    });
  });

  describe("JSON format", () => {
    it("exports as formatted JSON", () => {
      const trades = [fakeTrade()];
      const result = exportTrades(trades, { format: "json" });
      expect(result.filename).toBe("trades.json");
      expect(result.mimeType).toBe("application/json");
      expect(result.rowCount).toBe(1);

      const parsed = JSON.parse(result.content);
      expect(parsed).toHaveLength(1);
      expect(parsed[0].tradeId).toBe("t-001");
    });

    it("preserves original data types in JSON", () => {
      const result = exportTrades([fakeTrade()], { format: "json" });
      const parsed = JSON.parse(result.content);
      expect(typeof parsed[0].entryPrice).toBe("number");
      expect(typeof parsed[0].quantity).toBe("number");
    });
  });

  describe("date range filter", () => {
    it("filters trades by date range", () => {
      const trades = [
        fakeTrade({ tradeId: "t-001", entryTime: "2024-01-15T09:30:00Z" }),
        fakeTrade({ tradeId: "t-002", entryTime: "2024-03-15T09:30:00Z" }),
        fakeTrade({ tradeId: "t-003", entryTime: "2024-06-15T09:30:00Z" }),
      ];
      const result = exportTrades(trades, {
        dateRange: { from: "2024-02-01", to: "2024-04-30" },
      });
      expect(result.rowCount).toBe(1);
    });

    it("filters with only from date", () => {
      const trades = [
        fakeTrade({ tradeId: "t-001", entryTime: "2024-01-15T09:30:00Z" }),
        fakeTrade({ tradeId: "t-002", entryTime: "2024-06-15T09:30:00Z" }),
      ];
      const result = exportTrades(trades, {
        dateRange: { from: "2024-03-01" },
      });
      expect(result.rowCount).toBe(1);
    });

    it("filters with only to date", () => {
      const trades = [
        fakeTrade({ tradeId: "t-001", entryTime: "2024-01-15T09:30:00Z" }),
        fakeTrade({ tradeId: "t-002", entryTime: "2024-06-15T09:30:00Z" }),
      ];
      const result = exportTrades(trades, {
        dateRange: { to: "2024-03-01" },
      });
      expect(result.rowCount).toBe(1);
    });
  });

  describe("custom columns", () => {
    it("exports only specified columns", () => {
      const result = exportTrades([fakeTrade()], {
        columns: ["tradeId", "symbol", "netPnl"],
      });
      const { headers } = parseCsvContent(result.content);
      expect(headers).toHaveLength(3);
      expect(headers).toContain("交易ID");
      expect(headers).toContain("代码");
      expect(headers).toContain("净利润");
    });

    it("applies custom column headers", () => {
      const result = exportTrades([fakeTrade()], {
        columns: ["tradeId", "symbol"],
        columnHeaders: { tradeId: "Trade ID", symbol: "Stock Code" },
      });
      const { headers } = parseCsvContent(result.content);
      expect(headers).toContain("Trade ID");
      expect(headers).toContain("Stock Code");
    });
  });

  describe("precision", () => {
    it("respects custom precision", () => {
      const trade = fakeTrade({ entryPrice: 1800.12345 });
      const result = exportTrades([trade], { precision: 2 });
      expect(result.content).toContain("1800.12");
    });
  });

  describe("boundary conditions", () => {
    it("handles trade with missing optional fields", () => {
      const trade = fakeTrade({
        name: undefined,
        exitTime: undefined,
        exitPrice: undefined,
        holdingDays: undefined,
        strategyId: undefined,
      });
      const result = exportTrades([trade]);
      expect(result.rowCount).toBe(1);
      // Should not throw
    });

    it("handles zero values", () => {
      const trade = fakeTrade({
        entryPrice: 0,
        exitPrice: 0,
        quantity: 0,
        grossPnl: 0,
        netPnl: 0,
        commission: 0,
      });
      const result = exportTrades([trade]);
      expect(result.rowCount).toBe(1);
    });

    it("handles negative PnL values", () => {
      const trade = fakeTrade({
        grossPnl: -5000,
        netPnl: -5050,
      });
      const result = exportTrades([trade]);
      expect(result.content).toContain("-5050");
    });
  });
});

// ── exportPositions ─────────────────────────────────────────

describe("exportPositions", () => {
  it("exports positions as CSV with Chinese headers", () => {
    const result = exportPositions([fakePosition()]);
    expect(result.filename).toBe("positions.csv");
    expect(result.rowCount).toBe(1);

    const { headers } = parseCsvContent(result.content);
    expect(headers).toContain("代码");
    expect(headers).toContain("持仓量");
    expect(headers).toContain("浮动盈亏");
  });

  it("exports as JSON", () => {
    const positions = [fakePosition()];
    const result = exportPositions(positions, { format: "json" });
    expect(result.mimeType).toBe("application/json");
    const parsed = JSON.parse(result.content);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].symbol).toBe("000858");
  });

  it("exports multiple positions", () => {
    const positions = [
      fakePosition({ symbol: "600519" }),
      fakePosition({ symbol: "000858" }),
      fakePosition({ symbol: "300750" }),
    ];
    const result = exportPositions(positions);
    expect(result.rowCount).toBe(3);
  });

  it("handles empty positions", () => {
    const result = exportPositions([]);
    expect(result.rowCount).toBe(0);
  });

  it("handles position with missing optional fields", () => {
    const pos = fakePosition({
      name: undefined,
      currentPrice: undefined,
      marketValue: undefined,
      unrealizedPnl: undefined,
      unrealizedPnlPct: undefined,
    });
    const result = exportPositions([pos]);
    expect(result.rowCount).toBe(1);
  });

  it("formats percentage correctly", () => {
    const pos = fakePosition({ unrealizedPnlPct: 0.0784 });
    const result = exportPositions([pos]);
    expect(result.content).toContain("7.84%");
  });
});

// ── exportEquityHistory ─────────────────────────────────────

describe("exportEquityHistory", () => {
  it("exports equity snapshots as CSV", () => {
    const snapshots = [
      fakeEquitySnapshot({ date: "2024-06-01" }),
      fakeEquitySnapshot({ date: "2024-06-02", equity: 100500 }),
    ];
    const result = exportEquityHistory(snapshots);
    expect(result.filename).toBe("equity-history.csv");
    expect(result.rowCount).toBe(2);

    const { headers } = parseCsvContent(result.content);
    expect(headers).toContain("日期");
    expect(headers).toContain("总权益");
  });

  it("exports as JSON", () => {
    const snapshots = [fakeEquitySnapshot()];
    const result = exportEquityHistory(snapshots, { format: "json" });
    const parsed = JSON.parse(result.content);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].equity).toBe(100000);
  });

  it("filters by date range", () => {
    const snapshots = [
      fakeEquitySnapshot({ date: "2024-01-01" }),
      fakeEquitySnapshot({ date: "2024-03-15" }),
      fakeEquitySnapshot({ date: "2024-06-30" }),
    ];
    const result = exportEquityHistory(snapshots, {
      dateRange: { from: "2024-02-01", to: "2024-05-01" },
    });
    expect(result.rowCount).toBe(1);
  });

  it("handles empty array", () => {
    const result = exportEquityHistory([]);
    expect(result.rowCount).toBe(0);
  });

  it("uses precision=2 by default", () => {
    const snap = fakeEquitySnapshot({ equity: 100000.5678 });
    const result = exportEquityHistory([snap]);
    expect(result.content).toContain("100000.57");
  });
});

// ── exportPerformanceReport ─────────────────────────────────

describe("exportPerformanceReport", () => {
  it("exports as JSON by default", () => {
    const report = fakePerformanceReport();
    const result = exportPerformanceReport(report);
    expect(result.filename).toBe("performance-report.json");
    expect(result.mimeType).toBe("application/json");
    expect(result.rowCount).toBe(1);

    const parsed = JSON.parse(result.content);
    expect(parsed.returns.totalReturn).toBe(0.15);
    expect(parsed.tradeAnalysis.winRate).toBe(0.6);
  });

  it("exports as CSV with flattened key-value rows", () => {
    const report = fakePerformanceReport();
    const result = exportPerformanceReport(report, { format: "csv" });
    expect(result.filename).toBe("performance-report.csv");
    expect(result.rowCount).toBeGreaterThan(10); // many metrics

    const { headers } = parseCsvContent(result.content);
    expect(headers).toContain("分类");
    expect(headers).toContain("指标");
    expect(headers).toContain("值");

    // Check that key metrics are present
    expect(result.content).toContain("夏普比率");
    expect(result.content).toContain("胜率");
    expect(result.content).toContain("最大回撤");
  });

  it("exports as TSV", () => {
    const result = exportPerformanceReport(fakePerformanceReport(), { format: "tsv" });
    expect(result.filename).toBe("performance-report.tsv");
    const { headers } = parseTsvContent(result.content);
    expect(headers).toHaveLength(3);
  });
});

// ── exportTradeJournal ──────────────────────────────────────

describe("exportTradeJournal", () => {
  it("exports notes as CSV", () => {
    const result = exportTradeJournal([fakeTradeNote()]);
    expect(result.filename).toBe("trade-journal.csv");
    expect(result.rowCount).toBe(1);

    const { headers } = parseCsvContent(result.content);
    expect(headers).toContain("笔记ID");
    expect(headers).toContain("内容");
    expect(headers).toContain("经验教训");
  });

  it("joins tags with semicolons in CSV", () => {
    const note = fakeTradeNote({ tags: ["突破", "放量", "龙头"] });
    const result = exportTradeJournal([note]);
    expect(result.content).toContain("突破; 放量; 龙头");
  });

  it("exports as JSON preserving tags as array", () => {
    const note = fakeTradeNote({ tags: ["突破", "放量"] });
    const result = exportTradeJournal([note], { format: "json" });
    const parsed = JSON.parse(result.content);
    expect(Array.isArray(parsed[0].tags)).toBe(true);
    expect(parsed[0].tags).toEqual(["突破", "放量"]);
  });

  it("filters by date range", () => {
    const notes = [
      fakeTradeNote({ id: "tn-1", date: "2024-01-15" }),
      fakeTradeNote({ id: "tn-2", date: "2024-06-15" }),
    ];
    const result = exportTradeJournal(notes, {
      dateRange: { from: "2024-03-01" },
    });
    expect(result.rowCount).toBe(1);
  });

  it("handles note with missing optional fields", () => {
    const note = fakeTradeNote({
      direction: undefined,
      entryPrice: undefined,
      exitPrice: undefined,
      pnl: undefined,
      rating: undefined,
      lesson: undefined,
      strategyId: undefined,
    });
    const result = exportTradeJournal([note]);
    expect(result.rowCount).toBe(1);
  });

  it("handles content with special characters", () => {
    const note = fakeTradeNote({
      content: '涨停板打开,量能不足,"观望"为主',
    });
    const result = exportTradeJournal([note]);
    expect(result.rowCount).toBe(1);
    // Should be properly escaped
    expect(result.content).toContain('""观望""');
  });

  it("handles empty notes array", () => {
    const result = exportTradeJournal([]);
    expect(result.rowCount).toBe(0);
  });
});

// ── exportBacktestResult ────────────────────────────────────

describe("exportBacktestResult", () => {
  it("exports as JSON by default", () => {
    const result = exportBacktestResult(fakeBacktestResult());
    expect(result.filename).toBe("backtest-result.json");
    expect(result.mimeType).toBe("application/json");

    const parsed = JSON.parse(result.content);
    expect(parsed.strategyId).toBe("dual-ma-crossover");
    expect(parsed.totalReturn).toBe(0.15);
  });

  it("exports equity curve as CSV", () => {
    const result = exportBacktestResult(fakeBacktestResult(), { format: "csv" });
    expect(result.filename).toBe("backtest-result.csv");
    expect(result.rowCount).toBe(3); // 3 equity curve points

    const { headers } = parseCsvContent(result.content);
    expect(headers).toContain("日期");
    expect(headers).toContain("净值");
  });

  it("handles empty equity curve", () => {
    const bt = fakeBacktestResult();
    bt.equityCurve = [];
    const result = exportBacktestResult(bt, { format: "csv" });
    expect(result.rowCount).toBe(0);
  });

  it("handles undefined equity curve", () => {
    const bt = fakeBacktestResult();
    (bt as { equityCurve?: unknown }).equityCurve = undefined;
    const result = exportBacktestResult(bt, { format: "csv" });
    expect(result.rowCount).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════
// Integration / Large Data Tests
// ═══════════════════════════════════════════════════════════════

describe("large data volume", () => {
  it("exports 10,000 trades without error", () => {
    const trades: TradeRecord[] = [];
    for (let i = 0; i < 10_000; i++) {
      trades.push(
        fakeTrade({
          tradeId: `t-${String(i).padStart(5, "0")}`,
          symbol: `${600000 + (i % 1000)}`,
          entryPrice: 10 + Math.random() * 100,
          exitPrice: 10 + Math.random() * 100,
          netPnl: (Math.random() - 0.5) * 10000,
          entryTime: `2024-${String(1 + (i % 12)).padStart(2, "0")}-${String(1 + (i % 28)).padStart(2, "0")}T09:30:00Z`,
        }),
      );
    }

    const csvResult = exportTrades(trades);
    expect(csvResult.rowCount).toBe(10_000);

    const jsonResult = exportTrades(trades, { format: "json" });
    expect(jsonResult.rowCount).toBe(10_000);
    const parsed = JSON.parse(jsonResult.content);
    expect(parsed).toHaveLength(10_000);

    const tsvResult = exportTrades(trades, { format: "tsv" });
    expect(tsvResult.rowCount).toBe(10_000);
  });

  it("exports 5,000 equity snapshots", () => {
    const snapshots: EquitySnapshot[] = [];
    let equity = 100000;
    for (let i = 0; i < 5_000; i++) {
      const pnl = (Math.random() - 0.48) * 2000;
      equity += pnl;
      const date = new Date(2020, 0, 1);
      date.setDate(date.getDate() + i);
      snapshots.push(
        fakeEquitySnapshot({
          date: date.toISOString().slice(0, 10),
          equity,
          cash: equity * 0.3,
          marketValue: equity * 0.7,
          dailyPnl: pnl,
        }),
      );
    }

    const result = exportEquityHistory(snapshots);
    expect(result.rowCount).toBe(5_000);
  });

  it("exports 2,000 journal notes", () => {
    const notes: TradeNote[] = [];
    for (let i = 0; i < 2_000; i++) {
      notes.push(
        fakeTradeNote({
          id: `tn-${String(i).padStart(5, "0")}`,
          symbol: `${600000 + (i % 500)}`,
          date: `2024-${String(1 + (i % 12)).padStart(2, "0")}-${String(1 + (i % 28)).padStart(2, "0")}`,
          content: `交易笔记 #${i}: 今天市场表现${i % 2 === 0 ? "强势" : "弱势"}`,
        }),
      );
    }

    const result = exportTradeJournal(notes);
    expect(result.rowCount).toBe(2_000);
  });
});

describe("cross-format consistency", () => {
  it("CSV and JSON export the same number of records", () => {
    const trades = [
      fakeTrade({ tradeId: "t-1" }),
      fakeTrade({ tradeId: "t-2" }),
      fakeTrade({ tradeId: "t-3" }),
    ];

    const csvResult = exportTrades(trades, { format: "csv" });
    const jsonResult = exportTrades(trades, { format: "json" });
    const tsvResult = exportTrades(trades, { format: "tsv" });

    expect(csvResult.rowCount).toBe(jsonResult.rowCount);
    expect(csvResult.rowCount).toBe(tsvResult.rowCount);
  });

  it("date range filter applies consistently across formats", () => {
    const trades = [
      fakeTrade({ tradeId: "t-1", entryTime: "2024-01-15T09:30:00Z" }),
      fakeTrade({ tradeId: "t-2", entryTime: "2024-06-15T09:30:00Z" }),
    ];
    const opts: ExportOptions = { dateRange: { from: "2024-03-01" } };

    const csv = exportTrades(trades, { ...opts, format: "csv" });
    const json = exportTrades(trades, { ...opts, format: "json" });
    const tsv = exportTrades(trades, { ...opts, format: "tsv" });

    expect(csv.rowCount).toBe(1);
    expect(json.rowCount).toBe(1);
    expect(tsv.rowCount).toBe(1);
  });
});

describe("end-to-end export scenarios", () => {
  it("trader exports monthly trade summary", () => {
    // Simulate a trader who wants to export their June trades
    const trades = [
      fakeTrade({
        tradeId: "t-1",
        symbol: "600519",
        entryTime: "2024-05-28T09:30:00Z",
        exitTime: "2024-06-03T14:30:00Z",
        netPnl: 3000,
      }),
      fakeTrade({
        tradeId: "t-2",
        symbol: "000858",
        entryTime: "2024-06-10T09:30:00Z",
        exitTime: "2024-06-15T14:30:00Z",
        netPnl: -1500,
      }),
      fakeTrade({
        tradeId: "t-3",
        symbol: "300750",
        entryTime: "2024-06-20T09:30:00Z",
        exitTime: "2024-06-25T14:30:00Z",
        netPnl: 5000,
      }),
      fakeTrade({
        tradeId: "t-4",
        symbol: "002594",
        entryTime: "2024-07-01T09:30:00Z",
        netPnl: 0,
      }),
    ];

    const result = exportTrades(trades, {
      format: "csv",
      dateRange: { from: "2024-06-01", to: "2024-06-30" },
      columns: ["tradeId", "symbol", "side", "entryTime", "exitTime", "netPnl"],
    });

    expect(result.rowCount).toBe(2); // only t-2 and t-3 in June
    const { headers } = parseCsvContent(result.content);
    expect(headers).toHaveLength(6);
  });

  it("trader exports current portfolio for advisor", () => {
    const positions = [
      fakePosition({ symbol: "600519", name: "贵州茅台", quantity: 100, unrealizedPnl: 15000 }),
      fakePosition({ symbol: "000858", name: "五粮液", quantity: 200, unrealizedPnl: -3000 }),
      fakePosition({ symbol: "300750", name: "宁德时代", quantity: 50, unrealizedPnl: 8000 }),
    ];

    const result = exportPositions(positions, {
      format: "csv",
      columns: ["symbol", "name", "quantity", "avgCost", "currentPrice", "unrealizedPnl"],
    });

    expect(result.rowCount).toBe(3);
    const { headers } = parseCsvContent(result.content);
    expect(headers).toHaveLength(6);
    expect(result.content).toContain("贵州茅台");
    expect(result.content).toContain("宁德时代");
  });

  it("trader exports performance for compliance report", () => {
    const report = fakePerformanceReport();
    const csvResult = exportPerformanceReport(report, { format: "csv" });

    expect(csvResult.rowCount).toBeGreaterThan(15);
    expect(csvResult.content).toContain("总收益率");
    expect(csvResult.content).toContain("夏普比率");
    expect(csvResult.content).toContain("最大回撤");
  });

  it("trader exports backtest for strategy comparison", () => {
    const result = exportBacktestResult(fakeBacktestResult(), { format: "json" });
    const parsed = JSON.parse(result.content);

    expect(parsed.strategyId).toBe("dual-ma-crossover");
    expect(parsed.totalReturn).toBe(0.15);
    expect(parsed.equityCurve).toHaveLength(3);
  });

  it("trader exports journal for yearly review", () => {
    const notes = [
      fakeTradeNote({ id: "tn-1", date: "2024-03-15", content: "上涨趋势确认", tags: ["趋势"] }),
      fakeTradeNote({ id: "tn-2", date: "2024-06-20", content: "止损执行", tags: ["止损"] }),
      fakeTradeNote({
        id: "tn-3",
        date: "2024-09-10",
        content: "反弹行情",
        tags: ["反弹"],
        lesson: "逆势交易风险大",
      }),
    ];

    const result = exportTradeJournal(notes, { format: "csv" });
    expect(result.rowCount).toBe(3);
    expect(result.content).toContain("止损执行");
    expect(result.content).toContain("逆势交易风险大");
  });
});
