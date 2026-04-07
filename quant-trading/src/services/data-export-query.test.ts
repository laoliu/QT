import { describe, expect, it } from "vitest";
import type { Order, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { TradeNote } from "./trade-journal.js";
import {
  queryExportDataset,
  type ExportableOrder,
  type ExportableTrade,
  type ExportablePosition,
  type ExportableEquitySnapshot,
  type ExportablePeriodPnl,
  type ExportableTradeNote,
  type ExportableAccountSummary,
  type ExportableAttributionSummary,
  type ExportableStrategyAttribution,
  type ExportableSymbolAttribution,
  type ExportDataset,
  type ExportQueryOptions,
  ORDER_COLUMNS,
  TRADE_COLUMNS,
  POSITION_COLUMNS,
  EQUITY_COLUMNS,
  PERIOD_PNL_COLUMNS,
} from "./data-export-query.js";

// ── Type-level tests: verify all exportable types are flat ──────

describe("ExportableOrder", () => {
  it("has only primitive fields", () => {
    const order: ExportableOrder = {
      orderId: "o-1",
      symbol: "600519",
      market: "cn_stock",
      side: "buy",
      orderType: "limit",
      price: 1800,
      quantity: 100,
      filledQty: 100,
      avgFillPrice: 1800,
      status: "filled",
      strategyId: "dual-ma",
      brokerType: "paper",
      errorMsg: "",
      createdAt: "2025-01-10T09:30:00Z",
      updatedAt: "2025-01-10T09:31:00Z",
      submittedAt: "2025-01-10T09:30:00Z",
      filledAt: "2025-01-10T09:31:00Z",
    };
    expect(order.orderId).toBe("o-1");
    expect(order.price).toBe(1800);
  });
});

describe("ExportableTrade", () => {
  it("has only primitive fields", () => {
    const trade: ExportableTrade = {
      tradeId: "t-1",
      symbol: "600519",
      name: "贵州茅台",
      market: "cn_stock",
      side: "sell",
      entryTime: "2025-01-10T09:30:00Z",
      exitTime: "2025-01-15T14:30:00Z",
      entryPrice: 1800,
      exitPrice: 1900,
      quantity: 100,
      grossPnl: 10000,
      commission: 50,
      netPnl: 9950,
      holdingDays: 5,
      strategyId: "dual-ma",
      entryOrderId: "o-1",
      exitOrderId: "o-2",
    };
    expect(trade.netPnl).toBe(9950);
  });
});

describe("ExportablePosition", () => {
  it("supports paper and live sources", () => {
    const paper: ExportablePosition = {
      symbol: "600519",
      name: "贵州茅台",
      market: "cn_stock",
      quantity: 100,
      availableQty: null,
      avgCost: 1800,
      currentPrice: 1900,
      marketValue: 190000,
      unrealizedPnl: 10000,
      unrealizedPnlPct: 5.56,
      weight: 0.5,
      source: "paper",
    };
    expect(paper.source).toBe("paper");

    const live: ExportablePosition = {
      ...paper,
      availableQty: 100,
      weight: null,
      source: "live",
    };
    expect(live.source).toBe("live");
    expect(live.availableQty).toBe(100);
  });
});

describe("ExportableEquitySnapshot", () => {
  it("has flat structure with nullable benchmark", () => {
    const snap: ExportableEquitySnapshot = {
      date: "2025-01-10",
      equity: 1000000,
      cash: 500000,
      marketValue: 500000,
      dailyPnl: 5000,
      positionCount: 3,
      benchmark: null,
    };
    expect(snap.benchmark).toBeNull();
  });
});

describe("Column definitions", () => {
  it("ORDER_COLUMNS has bilingual labels", () => {
    expect(ORDER_COLUMNS.length).toBeGreaterThan(0);
    for (const col of ORDER_COLUMNS) {
      expect(col.key).toBeTruthy();
      expect(col.label).toBeTruthy();
      expect(col.labelCn).toBeTruthy();
    }
  });

  it("TRADE_COLUMNS includes key trade fields", () => {
    const keys = TRADE_COLUMNS.map((c) => c.key);
    expect(keys).toContain("tradeId");
    expect(keys).toContain("netPnl");
    expect(keys).toContain("entryPrice");
    expect(keys).toContain("exitPrice");
  });

  it("POSITION_COLUMNS includes unrealizedPnl", () => {
    const keys = POSITION_COLUMNS.map((c) => c.key);
    expect(keys).toContain("unrealizedPnl");
    expect(keys).toContain("symbol");
  });

  it("EQUITY_COLUMNS includes dailyPnl", () => {
    const keys = EQUITY_COLUMNS.map((c) => c.key);
    expect(keys).toContain("dailyPnl");
    expect(keys).toContain("equity");
  });

  it("PERIOD_PNL_COLUMNS includes granularity", () => {
    const keys = PERIOD_PNL_COLUMNS.map((c) => c.key);
    expect(keys).toContain("granularity");
    expect(keys).toContain("cumPnl");
  });
});

describe("ExportDataset structure", () => {
  it("has correct shape with all sections", () => {
    const ds: ExportDataset = {
      meta: {
        exportedAt: new Date().toISOString(),
        mode: "paper",
      },
      orders: [],
      trades: [],
      positions: [],
      equityHistory: [],
      attributionSummary: null,
      strategyAttribution: [],
      symbolAttribution: [],
      periodPnl: [],
      tradeNotes: [],
      accountSummary: null,
    };
    expect(ds.meta.mode).toBe("paper");
    expect(ds.orders).toEqual([]);
    expect(ds.attributionSummary).toBeNull();
  });
});
