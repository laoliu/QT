import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { TradeRecord, PortfolioPosition, LivePosition, Order, Fill } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { AttributionResult } from "./performance-attribution.js";
import type { FavoriteStock } from "./favorites-store.js";
import type { DcaPlan } from "./dca-schedule.js";
import {
  TRADE_RECORD_FIELDS,
  POSITION_FIELDS,
  PERFORMANCE_SUMMARY_FIELDS,
  STRATEGY_ATTRIBUTION_FIELDS,
  SYMBOL_ATTRIBUTION_FIELDS,
  PERIOD_PNL_FIELDS,
  EQUITY_CURVE_FIELDS,
  ORDER_FIELDS,
  FILL_FIELDS,
  WATCHLIST_FIELDS,
  DCA_PLAN_FIELDS,
  toTradeRecordExport,
  toPositionExportFromPortfolio,
  toPositionExportFromLive,
  toPerformanceExport,
  toOrderExport,
  toFillExport,
  toWatchlistExport,
  toDcaPlanExport,
} from "./export-models.js";
import type {
  ExportFieldMeta,
  ExportMeta,
  TradeRecordExportRow,
  PositionExportRow,
  OrderExportRow,
  FillExportRow,
  WatchlistExportRow,
  DcaPlanExportRow,
  PerformanceExport,
} from "./export-models.js";

// ── Helpers ────────────────────────────────────────────────────

function makeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
    tradeId: "T001",
    symbol: "600519",
    name: "贵州茅台",
    market: "cn_stock",
    side: "buy",
    entryTime: "2024-01-15T09:30:00",
    exitTime: "2024-01-20T14:30:00",
    entryPrice: 1800,
    exitPrice: 1850,
    quantity: 100,
    grossPnl: 5000,
    commission: 50,
    netPnl: 4950,
    holdingDays: 5,
    strategyId: "momentum-1",
    entryOrderId: "O001",
    exitOrderId: "O002",
    ...overrides,
  };
}

function makePortfolioPosition(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    symbol: "600519",
    name: "贵州茅台",
    market: "cn_stock",
    quantity: 100,
    avgCost: 1800,
    currentPrice: 1850,
    marketValue: 185000,
    unrealizedPnl: 5000,
    ...overrides,
  };
}

function makeLivePosition(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    symbol: "600519",
    name: "贵州茅台",
    market: "cn_stock",
    quantity: 100,
    availableQty: 80,
    frozenQty: 20,
    todayBuyQty: 0,
    avgCost: 1800,
    currentPrice: 1850,
    marketValue: 185000,
    unrealizedPnl: 5000,
    source: "broker",
    lastSyncAt: "2024-01-20T15:00:00",
    ...overrides,
  };
}

function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
    orderId: "O001",
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    orderType: "limit",
    price: 1800,
    quantity: 100,
    filledQty: 100,
    avgFillPrice: 1800,
    status: "filled",
    brokerType: "simulated" as any,
    createdAt: "2024-01-15T09:30:00",
    updatedAt: "2024-01-15T09:31:00",
    submittedAt: "2024-01-15T09:30:01",
    filledAt: "2024-01-15T09:31:00",
    ...overrides,
  };
}

function makeFill(overrides: Partial<Fill> = {}): Fill {
  return {
    fillId: "F001",
    orderId: "O001",
    symbol: "600519",
    side: "buy",
    price: 1800,
    quantity: 100,
    commission: 25,
    tax: 0,
    timestamp: "2024-01-15T09:31:00",
    ...overrides,
  };
}

function makeFavoriteStock(overrides: Partial<FavoriteStock> = {}): FavoriteStock {
  return {
    code: "600519",
    name: "贵州茅台",
    notes: "白酒龙头",
    tags: ["blue-chip", "consumer"],
    category: "watchlist",
    priority: 5,
    priceTarget: 2000,
    convictionScore: 8,
    addedAt: "2024-01-01T00:00:00",
    updatedAt: "2024-01-10T12:00:00",
    ...overrides,
  };
}

function makeDcaPlan(overrides: Partial<DcaPlan> = {}): DcaPlan {
  return {
    id: "DCA001",
    symbol: "510300",
    amount: 1000,
    frequency: "monthly",
    status: "active",
    executionCount: 6,
    totalInvested: 6000,
    lastExecutedAt: "2024-06-01T09:00:00",
    nextExecutionAt: "2024-07-01T09:00:00",
    createdAt: "2024-01-01T00:00:00",
    description: "沪深300定投",
    ...overrides,
  };
}

function makeAttribution(overrides: Partial<AttributionResult> = {}): AttributionResult {
  return {
    dateRange: { from: "2024-01-01", to: "2024-06-30" },
    totalTrades: 50,
    totalNetPnl: 25000,
    totalGrossPnl: 30000,
    totalCommission: 5000,
    overallWinRate: 60,
    avgWin: 1500,
    avgLoss: -800,
    profitFactor: 1.875,
    byStrategy: [
      {
        strategyId: "momentum-1",
        trades: 30,
        wins: 20,
        losses: 10,
        winRate: 66.67,
        grossPnl: 20000,
        netPnl: 17000,
        avgWin: 1500,
        avgLoss: -600,
        largestWin: 5000,
        largestLoss: -2000,
        profitFactor: 2.5,
        contributionPct: 68,
      },
    ],
    bySymbol: [
      {
        symbol: "600519",
        trades: 10,
        netPnl: 8000,
        avgPnl: 800,
        winRate: 70,
        holdingDaysAvg: 5.5,
        contributionPct: 32,
      },
    ],
    byDay: [{ period: "2024-01-15", trades: 2, netPnl: 500, cumPnl: 500, wins: 1, losses: 1 }],
    byWeek: [{ period: "2024-W03", trades: 5, netPnl: 1200, cumPnl: 1200, wins: 3, losses: 2 }],
    byMonth: [{ period: "2024-01", trades: 12, netPnl: 3000, cumPnl: 3000, wins: 8, losses: 4 }],
    ...overrides,
  };
}

function makeEquitySnapshot(overrides: Partial<EquitySnapshot> = {}): EquitySnapshot {
  return {
    date: "2024-01-15",
    equity: 1000000,
    cash: 500000,
    marketValue: 500000,
    dailyPnl: 2000,
    positionCount: 5,
    benchmark: 4000,
    ...overrides,
  };
}

// ── Field Metadata Tests ───────────────────────────────────────

describe("Field metadata arrays", () => {
  function validateFieldMeta(fields: ExportFieldMeta[], name: string) {
    it(`${name} has non-empty fields`, () => {
      expect(fields.length).toBeGreaterThan(0);
    });

    it(`${name} fields have unique keys`, () => {
      const keys = fields.map((f) => f.key);
      expect(new Set(keys).size).toBe(keys.length);
    });

    it(`${name} every field has required properties`, () => {
      for (const f of fields) {
        expect(f.key).toBeTruthy();
        expect(f.label).toBeTruthy();
        expect(["string", "number", "boolean", "date", "datetime"]).toContain(f.type);
      }
    });

    it(`${name} numeric fields have precision defined`, () => {
      for (const f of fields) {
        if (f.type === "number") {
          expect(f.precision).toBeDefined();
          expect(f.precision).toBeGreaterThanOrEqual(0);
        }
      }
    });

    it(`${name} every field has a width`, () => {
      for (const f of fields) {
        expect(f.width).toBeDefined();
        expect(f.width).toBeGreaterThan(0);
      }
    });
  }

  validateFieldMeta(TRADE_RECORD_FIELDS, "TRADE_RECORD_FIELDS");
  validateFieldMeta(POSITION_FIELDS, "POSITION_FIELDS");
  validateFieldMeta(PERFORMANCE_SUMMARY_FIELDS, "PERFORMANCE_SUMMARY_FIELDS");
  validateFieldMeta(STRATEGY_ATTRIBUTION_FIELDS, "STRATEGY_ATTRIBUTION_FIELDS");
  validateFieldMeta(SYMBOL_ATTRIBUTION_FIELDS, "SYMBOL_ATTRIBUTION_FIELDS");
  validateFieldMeta(PERIOD_PNL_FIELDS, "PERIOD_PNL_FIELDS");
  validateFieldMeta(EQUITY_CURVE_FIELDS, "EQUITY_CURVE_FIELDS");
  validateFieldMeta(ORDER_FIELDS, "ORDER_FIELDS");
  validateFieldMeta(FILL_FIELDS, "FILL_FIELDS");
  validateFieldMeta(WATCHLIST_FIELDS, "WATCHLIST_FIELDS");
  validateFieldMeta(DCA_PLAN_FIELDS, "DCA_PLAN_FIELDS");

  it("TRADE_RECORD_FIELDS has bilingual labels (labelZh)", () => {
    for (const f of TRADE_RECORD_FIELDS) {
      expect(f.labelZh).toBeTruthy();
    }
  });

  it("datetime fields have format defined", () => {
    const allFields = [
      ...TRADE_RECORD_FIELDS,
      ...ORDER_FIELDS,
      ...FILL_FIELDS,
      ...EQUITY_CURVE_FIELDS,
    ];
    for (const f of allFields) {
      if (f.type === "datetime") {
        expect(f.format).toBeTruthy();
      }
    }
  });
});

// ── toTradeRecordExport ────────────────────────────────────────

describe("toTradeRecordExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a single trade correctly", () => {
    const trade = makeTrade();
    const result = toTradeRecordExport([trade]);

    expect(result.fields).toBe(TRADE_RECORD_FIELDS);
    expect(result.data).toHaveLength(1);
    expect(result.meta?.title).toBe("交易记录导出");
    expect(result.meta?.totalRecords).toBe(1);

    const row = result.data[0];
    expect(row.tradeId).toBe("T001");
    expect(row.symbol).toBe("600519");
    expect(row.name).toBe("贵州茅台");
    expect(row.market).toBe("cn_stock");
    expect(row.side).toBe("buy");
    expect(row.entryTime).toBe("2024-01-15T09:30:00");
    expect(row.exitTime).toBe("2024-01-20T14:30:00");
    expect(row.entryPrice).toBe(1800);
    expect(row.exitPrice).toBe(1850);
    expect(row.quantity).toBe(100);
    expect(row.grossPnl).toBe(5000);
    expect(row.commission).toBe(50);
    expect(row.netPnl).toBe(4950);
    expect(row.holdingDays).toBe(5);
    expect(row.strategyId).toBe("momentum-1");
  });

  it("computes netPnlPct correctly", () => {
    const trade = makeTrade({ entryPrice: 100, quantity: 200, netPnl: 500 });
    const result = toTradeRecordExport([trade]);
    // (500 / (100 * 200)) * 100 = 2.5
    expect(result.data[0].netPnlPct).toBeCloseTo(2.5);
  });

  it("sets netPnlPct to 0 when entryPrice is 0", () => {
    const trade = makeTrade({ entryPrice: 0 });
    const result = toTradeRecordExport([trade]);
    expect(result.data[0].netPnlPct).toBe(0);
  });

  it("handles missing optional fields with defaults", () => {
    const trade = makeTrade({
      name: undefined,
      exitTime: undefined,
      exitPrice: undefined,
      holdingDays: undefined,
      strategyId: undefined,
    });
    const result = toTradeRecordExport([trade]);
    const row = result.data[0];
    expect(row.name).toBe("");
    expect(row.exitTime).toBe("");
    expect(row.exitPrice).toBe(0);
    expect(row.holdingDays).toBe(0);
    expect(row.strategyId).toBe("");
  });

  it("filters by from date", () => {
    const trades = [
      makeTrade({ tradeId: "T1", entryTime: "2024-01-10T00:00:00" }),
      makeTrade({ tradeId: "T2", entryTime: "2024-01-15T00:00:00" }),
      makeTrade({ tradeId: "T3", entryTime: "2024-01-20T00:00:00" }),
    ];
    const result = toTradeRecordExport(trades, { from: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(2);
    expect(result.data.map((d) => d.tradeId)).toEqual(["T2", "T3"]);
  });

  it("filters by to date", () => {
    const trades = [
      makeTrade({ tradeId: "T1", entryTime: "2024-01-10T00:00:00" }),
      makeTrade({ tradeId: "T2", entryTime: "2024-01-15T00:00:00" }),
      makeTrade({ tradeId: "T3", entryTime: "2024-01-20T00:00:00" }),
    ];
    const result = toTradeRecordExport(trades, { to: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(2);
    expect(result.data.map((d) => d.tradeId)).toEqual(["T1", "T2"]);
  });

  it("filters by both from and to dates", () => {
    const trades = [
      makeTrade({ tradeId: "T1", entryTime: "2024-01-10T00:00:00" }),
      makeTrade({ tradeId: "T2", entryTime: "2024-01-15T00:00:00" }),
      makeTrade({ tradeId: "T3", entryTime: "2024-01-20T00:00:00" }),
    ];
    const result = toTradeRecordExport(trades, {
      from: "2024-01-11T00:00:00",
      to: "2024-01-19T00:00:00",
    });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].tradeId).toBe("T2");
  });

  it("returns empty data when all trades are filtered out", () => {
    const trades = [makeTrade({ entryTime: "2024-01-10T00:00:00" })];
    const result = toTradeRecordExport(trades, { from: "2024-02-01T00:00:00" });
    expect(result.data).toHaveLength(0);
    expect(result.meta?.totalRecords).toBe(0);
  });

  it("handles empty input", () => {
    const result = toTradeRecordExport([]);
    expect(result.data).toHaveLength(0);
    expect(result.meta?.totalRecords).toBe(0);
  });

  it("includes dateRange in meta when opts provided", () => {
    const result = toTradeRecordExport([], { from: "2024-01-01", to: "2024-06-30" });
    expect(result.meta?.dateRange).toEqual({ from: "2024-01-01", to: "2024-06-30" });
  });

  it("sets exportedAt timestamp", () => {
    const result = toTradeRecordExport([]);
    expect(result.meta?.exportedAt).toBe("2024-07-01T00:00:00.000Z");
  });

  it("converts multiple trades preserving order", () => {
    const trades = [
      makeTrade({ tradeId: "T1" }),
      makeTrade({ tradeId: "T2" }),
      makeTrade({ tradeId: "T3" }),
    ];
    const result = toTradeRecordExport(trades);
    expect(result.data.map((d) => d.tradeId)).toEqual(["T1", "T2", "T3"]);
  });
});

// ── toPositionExportFromPortfolio ──────────────────────────────

describe("toPositionExportFromPortfolio", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a portfolio position correctly", () => {
    const pos = makePortfolioPosition();
    const result = toPositionExportFromPortfolio([pos]);

    expect(result.fields).toBe(POSITION_FIELDS);
    expect(result.data).toHaveLength(1);
    expect(result.meta?.title).toBe("持仓导出");

    const row = result.data[0];
    expect(row.symbol).toBe("600519");
    expect(row.source).toBe("paper");
    expect(row.availableQty).toBe(100); // paper = fully available
  });

  it("computes marketValue from currentPrice when marketValue is undefined", () => {
    const pos = makePortfolioPosition({
      marketValue: undefined,
      currentPrice: 1900,
      quantity: 100,
      avgCost: 1800,
    });
    const result = toPositionExportFromPortfolio([pos]);
    expect(result.data[0].marketValue).toBe(190000);
  });

  it("falls back to avgCost when currentPrice is also undefined", () => {
    const pos = makePortfolioPosition({
      marketValue: undefined,
      currentPrice: undefined,
      avgCost: 1800,
      quantity: 100,
    });
    const result = toPositionExportFromPortfolio([pos]);
    expect(result.data[0].currentPrice).toBe(1800);
    expect(result.data[0].marketValue).toBe(180000);
  });

  it("computes unrealizedPnl when not provided", () => {
    const pos = makePortfolioPosition({
      unrealizedPnl: undefined,
      currentPrice: 1900,
      avgCost: 1800,
      quantity: 100,
      marketValue: undefined,
    });
    const result = toPositionExportFromPortfolio([pos]);
    // mv = 1900 * 100 = 190000, cost = 1800 * 100 = 180000, pnl = 10000
    expect(result.data[0].unrealizedPnl).toBe(10000);
  });

  it("computes unrealizedPnlPct correctly", () => {
    const pos = makePortfolioPosition({
      unrealizedPnl: 5000,
      avgCost: 1800,
      quantity: 100,
    });
    const result = toPositionExportFromPortfolio([pos]);
    // (5000 / (1800 * 100)) * 100 ≈ 2.7778
    expect(result.data[0].unrealizedPnlPct).toBeCloseTo(2.7778, 2);
  });

  it("sets unrealizedPnlPct to 0 when cost is 0", () => {
    const pos = makePortfolioPosition({ avgCost: 0, quantity: 100 });
    const result = toPositionExportFromPortfolio([pos]);
    expect(result.data[0].unrealizedPnlPct).toBe(0);
  });

  it("computes weight using provided totalValue", () => {
    const pos = makePortfolioPosition({ marketValue: 100000 });
    const result = toPositionExportFromPortfolio([pos], 500000);
    // (100000 / 500000) * 100 = 20
    expect(result.data[0].weight).toBeCloseTo(20);
  });

  it("computes weight using sum of positions when totalValue not provided", () => {
    const positions = [
      makePortfolioPosition({ symbol: "A", marketValue: 60000 }),
      makePortfolioPosition({ symbol: "B", marketValue: 40000 }),
    ];
    const result = toPositionExportFromPortfolio(positions);
    expect(result.data[0].weight).toBeCloseTo(60);
    expect(result.data[1].weight).toBeCloseTo(40);
  });

  it("sets weight to 0 when total is 0", () => {
    const pos = makePortfolioPosition({ marketValue: 0, avgCost: 0, quantity: 0 });
    const result = toPositionExportFromPortfolio([pos], 0);
    expect(result.data[0].weight).toBe(0);
  });

  it("handles empty positions", () => {
    const result = toPositionExportFromPortfolio([]);
    expect(result.data).toHaveLength(0);
    expect(result.meta?.totalRecords).toBe(0);
  });

  it("defaults name to empty string when undefined", () => {
    const pos = makePortfolioPosition({ name: undefined });
    const result = toPositionExportFromPortfolio([pos]);
    expect(result.data[0].name).toBe("");
  });
});

// ── toPositionExportFromLive ───────────────────────────────────

describe("toPositionExportFromLive", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a live position with correct source", () => {
    const pos = makeLivePosition();
    const result = toPositionExportFromLive([pos]);
    expect(result.data[0].source).toBe("live");
    expect(result.data[0].availableQty).toBe(80);
  });

  it("computes marketValue from currentPrice when undefined", () => {
    const pos = makeLivePosition({
      marketValue: undefined,
      currentPrice: 1900,
      quantity: 100,
    });
    const result = toPositionExportFromLive([pos]);
    expect(result.data[0].marketValue).toBe(190000);
  });

  it("falls back to avgCost when currentPrice undefined", () => {
    const pos = makeLivePosition({
      marketValue: undefined,
      currentPrice: undefined,
      avgCost: 1800,
      quantity: 100,
    });
    const result = toPositionExportFromLive([pos]);
    expect(result.data[0].currentPrice).toBe(1800);
    expect(result.data[0].marketValue).toBe(180000);
  });

  it("uses provided totalValue for weight computation", () => {
    const pos = makeLivePosition({ marketValue: 185000 });
    const result = toPositionExportFromLive([pos], 1000000);
    expect(result.data[0].weight).toBeCloseTo(18.5);
  });

  it("handles empty input", () => {
    const result = toPositionExportFromLive([]);
    expect(result.data).toHaveLength(0);
  });
});

// ── toOrderExport ──────────────────────────────────────────────

describe("toOrderExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a single order correctly", () => {
    const order = makeOrder();
    const result = toOrderExport([order]);

    expect(result.fields).toBe(ORDER_FIELDS);
    expect(result.data).toHaveLength(1);
    expect(result.meta?.title).toBe("委托记录导出");

    const row = result.data[0];
    expect(row.orderId).toBe("O001");
    expect(row.symbol).toBe("600519");
    expect(row.status).toBe("filled");
    expect(row.price).toBe(1800);
  });

  it("handles optional fields with defaults", () => {
    const order = makeOrder({
      brokerOrderId: undefined,
      price: undefined,
      avgFillPrice: undefined,
      strategyId: undefined,
      errorMsg: undefined,
      submittedAt: undefined,
      filledAt: undefined,
    });
    const result = toOrderExport([order]);
    const row = result.data[0];
    expect(row.brokerOrderId).toBe("");
    expect(row.price).toBe(0);
    expect(row.avgFillPrice).toBe(0);
    expect(row.strategyId).toBe("");
    expect(row.errorMsg).toBe("");
    expect(row.submittedAt).toBe("");
    expect(row.filledAt).toBe("");
  });

  it("filters by from date on createdAt", () => {
    const orders = [
      makeOrder({ orderId: "O1", createdAt: "2024-01-10T00:00:00" }),
      makeOrder({ orderId: "O2", createdAt: "2024-01-20T00:00:00" }),
    ];
    const result = toOrderExport(orders, { from: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].orderId).toBe("O2");
  });

  it("filters by to date on createdAt", () => {
    const orders = [
      makeOrder({ orderId: "O1", createdAt: "2024-01-10T00:00:00" }),
      makeOrder({ orderId: "O2", createdAt: "2024-01-20T00:00:00" }),
    ];
    const result = toOrderExport(orders, { to: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].orderId).toBe("O1");
  });

  it("handles empty input", () => {
    const result = toOrderExport([]);
    expect(result.data).toHaveLength(0);
    expect(result.meta?.totalRecords).toBe(0);
  });
});

// ── toFillExport ───────────────────────────────────────────────

describe("toFillExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a single fill correctly", () => {
    const fill = makeFill();
    const result = toFillExport([fill]);

    expect(result.fields).toBe(FILL_FIELDS);
    expect(result.meta?.title).toBe("成交记录导出");

    const row = result.data[0];
    expect(row.fillId).toBe("F001");
    expect(row.orderId).toBe("O001");
    expect(row.price).toBe(1800);
    expect(row.quantity).toBe(100);
    expect(row.amount).toBe(180000); // price * quantity
    expect(row.commission).toBe(25);
    expect(row.tax).toBe(0);
    expect(row.totalCost).toBe(25); // commission + tax
  });

  it("computes amount as price * quantity", () => {
    const fill = makeFill({ price: 50.5, quantity: 200 });
    const result = toFillExport([fill]);
    expect(result.data[0].amount).toBe(10100);
  });

  it("computes totalCost as commission + tax", () => {
    const fill = makeFill({ commission: 30, tax: 18 });
    const result = toFillExport([fill]);
    expect(result.data[0].totalCost).toBe(48);
  });

  it("defaults tax to 0 when undefined", () => {
    const fill = makeFill({ tax: undefined, commission: 25 });
    const result = toFillExport([fill]);
    expect(result.data[0].tax).toBe(0);
    expect(result.data[0].totalCost).toBe(25);
  });

  it("filters by from date on timestamp", () => {
    const fills = [
      makeFill({ fillId: "F1", timestamp: "2024-01-10T00:00:00" }),
      makeFill({ fillId: "F2", timestamp: "2024-01-20T00:00:00" }),
    ];
    const result = toFillExport(fills, { from: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].fillId).toBe("F2");
  });

  it("filters by to date on timestamp", () => {
    const fills = [
      makeFill({ fillId: "F1", timestamp: "2024-01-10T00:00:00" }),
      makeFill({ fillId: "F2", timestamp: "2024-01-20T00:00:00" }),
    ];
    const result = toFillExport(fills, { to: "2024-01-15T00:00:00" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].fillId).toBe("F1");
  });

  it("handles empty input", () => {
    const result = toFillExport([]);
    expect(result.data).toHaveLength(0);
  });
});

// ── toWatchlistExport ──────────────────────────────────────────

describe("toWatchlistExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a favorite stock correctly", () => {
    const fav = makeFavoriteStock();
    const result = toWatchlistExport([fav]);

    expect(result.fields).toBe(WATCHLIST_FIELDS);
    expect(result.meta?.title).toBe("自选股导出");

    const row = result.data[0];
    expect(row.code).toBe("600519");
    expect(row.name).toBe("贵州茅台");
    expect(row.category).toBe("watchlist");
    expect(row.priority).toBe(5);
    expect(row.priceTarget).toBe(2000);
    expect(row.convictionScore).toBe(8);
    expect(row.tags).toBe("blue-chip, consumer");
    expect(row.notes).toBe("白酒龙头");
  });

  it("handles missing optional fields", () => {
    const fav = makeFavoriteStock({
      name: undefined,
      notes: undefined,
      tags: undefined,
      category: undefined,
      priority: undefined,
      priceTarget: undefined,
      convictionScore: undefined,
    });
    const result = toWatchlistExport([fav]);
    const row = result.data[0];
    expect(row.name).toBe("");
    expect(row.notes).toBe("");
    expect(row.tags).toBe("");
    expect(row.category).toBe("watchlist");
    expect(row.priority).toBe(0);
    expect(row.priceTarget).toBe(0);
    expect(row.convictionScore).toBe(0);
  });

  it("filters by category", () => {
    const favs = [
      makeFavoriteStock({ code: "A", category: "watchlist" }),
      makeFavoriteStock({ code: "B", category: "ideas" }),
      makeFavoriteStock({ code: "C", category: "watchlist" }),
    ];
    const result = toWatchlistExport(favs, { category: "ideas" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].code).toBe("B");
  });

  it("returns all when no category filter", () => {
    const favs = [
      makeFavoriteStock({ code: "A", category: "watchlist" }),
      makeFavoriteStock({ code: "B", category: "ideas" }),
    ];
    const result = toWatchlistExport(favs);
    expect(result.data).toHaveLength(2);
  });

  it("handles empty tags array", () => {
    const fav = makeFavoriteStock({ tags: [] });
    const result = toWatchlistExport([fav]);
    expect(result.data[0].tags).toBe("");
  });

  it("handles empty input", () => {
    const result = toWatchlistExport([]);
    expect(result.data).toHaveLength(0);
  });
});

// ── toDcaPlanExport ────────────────────────────────────────────

describe("toDcaPlanExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("converts a DCA plan correctly", () => {
    const plan = makeDcaPlan();
    const result = toDcaPlanExport([plan]);

    expect(result.fields).toBe(DCA_PLAN_FIELDS);
    expect(result.meta?.title).toBe("定投计划导出");

    const row = result.data[0];
    expect(row.id).toBe("DCA001");
    expect(row.symbol).toBe("510300");
    expect(row.amount).toBe(1000);
    expect(row.frequency).toBe("monthly");
    expect(row.status).toBe("active");
    expect(row.executionCount).toBe(6);
    expect(row.totalInvested).toBe(6000);
    // avgCostPerExecution = 6000 / 6 = 1000
    expect(row.avgCostPerExecution).toBe(1000);
    expect(row.description).toBe("沪深300定投");
  });

  it("computes avgCostPerExecution correctly", () => {
    const plan = makeDcaPlan({ executionCount: 3, totalInvested: 3600 });
    const result = toDcaPlanExport([plan]);
    expect(result.data[0].avgCostPerExecution).toBe(1200);
  });

  it("sets avgCostPerExecution to 0 when executionCount is 0", () => {
    const plan = makeDcaPlan({ executionCount: 0, totalInvested: 0 });
    const result = toDcaPlanExport([plan]);
    expect(result.data[0].avgCostPerExecution).toBe(0);
  });

  it("handles optional datetime fields", () => {
    const plan = makeDcaPlan({
      lastExecutedAt: undefined,
      nextExecutionAt: undefined,
    });
    const result = toDcaPlanExport([plan]);
    expect(result.data[0].lastExecutedAt).toBe("");
    expect(result.data[0].nextExecutionAt).toBe("");
  });

  it("filters by status=active", () => {
    const plans = [
      makeDcaPlan({ id: "D1", status: "active" }),
      makeDcaPlan({ id: "D2", status: "paused" }),
      makeDcaPlan({ id: "D3", status: "cancelled" }),
    ];
    const result = toDcaPlanExport(plans, { status: "active" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("D1");
  });

  it("filters by status=paused", () => {
    const plans = [
      makeDcaPlan({ id: "D1", status: "active" }),
      makeDcaPlan({ id: "D2", status: "paused" }),
    ];
    const result = toDcaPlanExport(plans, { status: "paused" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("D2");
  });

  it("filters by status=cancelled", () => {
    const plans = [
      makeDcaPlan({ id: "D1", status: "active" }),
      makeDcaPlan({ id: "D2", status: "cancelled" }),
    ];
    const result = toDcaPlanExport(plans, { status: "cancelled" });
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("D2");
  });

  it("returns all when no status filter", () => {
    const plans = [
      makeDcaPlan({ id: "D1", status: "active" }),
      makeDcaPlan({ id: "D2", status: "paused" }),
    ];
    const result = toDcaPlanExport(plans);
    expect(result.data).toHaveLength(2);
  });

  it("handles empty input", () => {
    const result = toDcaPlanExport([]);
    expect(result.data).toHaveLength(0);
  });
});

// ── toPerformanceExport ────────────────────────────────────────

describe("toPerformanceExport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("produces all required sections", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.summary).toBeDefined();
    expect(result.byStrategy).toBeDefined();
    expect(result.bySymbol).toBeDefined();
    expect(result.byPeriod).toBeDefined();
    expect(result.equityCurve).toBeDefined();
    expect(result.meta).toBeDefined();
  });

  it("summary section has correct fields and data", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.summary.fields).toBe(PERFORMANCE_SUMMARY_FIELDS);
    expect(result.summary.data).toHaveLength(1);

    const row = result.summary.data[0];
    expect(row.dateFrom).toBe("2024-01-01");
    expect(row.dateTo).toBe("2024-06-30");
    expect(row.totalTrades).toBe(50);
    expect(row.totalNetPnl).toBe(25000);
    expect(row.totalGrossPnl).toBe(30000);
    expect(row.totalCommission).toBe(5000);
    expect(row.overallWinRate).toBe(60);
    expect(row.avgWin).toBe(1500);
    expect(row.avgLoss).toBe(-800);
    expect(row.profitFactor).toBe(1.875);
  });

  it("byStrategy section maps correctly", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.byStrategy.fields).toBe(STRATEGY_ATTRIBUTION_FIELDS);
    expect(result.byStrategy.data).toHaveLength(1);
    expect(result.byStrategy.data[0].strategyId).toBe("momentum-1");
    expect(result.byStrategy.data[0].trades).toBe(30);
    expect(result.byStrategy.data[0].profitFactor).toBe(2.5);
  });

  it("bySymbol section maps correctly", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.bySymbol.fields).toBe(SYMBOL_ATTRIBUTION_FIELDS);
    expect(result.bySymbol.data).toHaveLength(1);
    expect(result.bySymbol.data[0].symbol).toBe("600519");
    expect(result.bySymbol.data[0].holdingDaysAvg).toBe(5.5);
  });

  it("defaults to monthly period granularity", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.byPeriod.fields).toBe(PERIOD_PNL_FIELDS);
    expect(result.byPeriod.data).toHaveLength(1);
    expect(result.byPeriod.data[0].period).toBe("2024-01");
  });

  it("uses daily period granularity when specified", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr, undefined, { periodGranularity: "daily" });
    expect(result.byPeriod.data[0].period).toBe("2024-01-15");
  });

  it("uses weekly period granularity when specified", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr, undefined, { periodGranularity: "weekly" });
    expect(result.byPeriod.data[0].period).toBe("2024-W03");
  });

  it("includes equity curve from snapshots", () => {
    const attr = makeAttribution();
    const snapshots = [
      makeEquitySnapshot({ date: "2024-01-15" }),
      makeEquitySnapshot({ date: "2024-01-16", equity: 1002000, dailyPnl: 2000 }),
    ];
    const result = toPerformanceExport(attr, snapshots);

    expect(result.equityCurve.fields).toBe(EQUITY_CURVE_FIELDS);
    expect(result.equityCurve.data).toHaveLength(2);
    expect(result.equityCurve.data[0].date).toBe("2024-01-15");
    expect(result.equityCurve.data[0].equity).toBe(1000000);
    expect(result.equityCurve.data[0].benchmark).toBe(4000);
  });

  it("defaults benchmark to 0 when undefined in snapshot", () => {
    const attr = makeAttribution();
    const snapshots = [makeEquitySnapshot({ benchmark: undefined })];
    const result = toPerformanceExport(attr, snapshots);
    expect(result.equityCurve.data[0].benchmark).toBe(0);
  });

  it("produces empty equity curve when no snapshots provided", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);
    expect(result.equityCurve.data).toHaveLength(0);
  });

  it("produces empty equity curve when snapshots is undefined", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr, undefined);
    expect(result.equityCurve.data).toHaveLength(0);
  });

  it("meta has correct properties", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.meta.title).toBe("绩效分析导出");
    expect(result.meta.exportedAt).toBe("2024-07-01T00:00:00.000Z");
    expect(result.meta.totalRecords).toBe(50);
    expect(result.meta.dateRange).toEqual({ from: "2024-01-01", to: "2024-06-30" });
  });

  it("all sections share the same meta object", () => {
    const attr = makeAttribution();
    const result = toPerformanceExport(attr);

    expect(result.summary.meta).toBe(result.meta);
    expect(result.byStrategy.meta).toBe(result.meta);
    expect(result.bySymbol.meta).toBe(result.meta);
    expect(result.byPeriod.meta).toBe(result.meta);
    expect(result.equityCurve.meta).toBe(result.meta);
  });

  it("handles attribution with empty arrays", () => {
    const attr = makeAttribution({
      byStrategy: [],
      bySymbol: [],
      byDay: [],
      byWeek: [],
      byMonth: [],
    });
    const result = toPerformanceExport(attr);
    expect(result.byStrategy.data).toHaveLength(0);
    expect(result.bySymbol.data).toHaveLength(0);
    expect(result.byPeriod.data).toHaveLength(0);
  });
});

// ── ExportableRecord generic structure ─────────────────────────

describe("ExportableRecord structure consistency", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2024-07-01T00:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("all export functions produce fields-data-meta structure", () => {
    const tradeExport = toTradeRecordExport([makeTrade()]);
    const posExport = toPositionExportFromPortfolio([makePortfolioPosition()]);
    const liveExport = toPositionExportFromLive([makeLivePosition()]);
    const orderExport = toOrderExport([makeOrder()]);
    const fillExport = toFillExport([makeFill()]);
    const watchExport = toWatchlistExport([makeFavoriteStock()]);
    const dcaExport = toDcaPlanExport([makeDcaPlan()]);

    for (const exp of [tradeExport, posExport, liveExport, orderExport, fillExport, watchExport, dcaExport]) {
      expect(exp.fields).toBeDefined();
      expect(Array.isArray(exp.fields)).toBe(true);
      expect(exp.data).toBeDefined();
      expect(Array.isArray(exp.data)).toBe(true);
      expect(exp.meta).toBeDefined();
      expect(exp.meta?.exportedAt).toBe("2024-07-01T00:00:00.000Z");
      expect(typeof exp.meta?.totalRecords).toBe("number");
    }
  });

  it("field keys match data object keys", () => {
    const tradeExport = toTradeRecordExport([makeTrade()]);
    const fieldKeys = tradeExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(tradeExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });

  it("position export field keys match data keys", () => {
    const posExport = toPositionExportFromPortfolio([makePortfolioPosition()]);
    const fieldKeys = posExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(posExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });

  it("order export field keys match data keys", () => {
    const orderExport = toOrderExport([makeOrder()]);
    const fieldKeys = orderExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(orderExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });

  it("fill export field keys match data keys", () => {
    const fillExport = toFillExport([makeFill()]);
    const fieldKeys = fillExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(fillExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });

  it("watchlist export field keys match data keys", () => {
    const watchExport = toWatchlistExport([makeFavoriteStock()]);
    const fieldKeys = watchExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(watchExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });

  it("dca plan export field keys match data keys", () => {
    const dcaExport = toDcaPlanExport([makeDcaPlan()]);
    const fieldKeys = dcaExport.fields.map((f) => f.key);
    const dataKeys = Object.keys(dcaExport.data[0]);
    for (const key of fieldKeys) {
      expect(dataKeys).toContain(key);
    }
  });
});
