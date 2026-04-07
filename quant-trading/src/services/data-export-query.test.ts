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
  type ExportableSignalRecord,
  type ExportableConfirmation,
  type ExportableFavorite,
  type ExportableScanIndex,
  type ExportableOptimizationIndex,
  type ExportableInstitutionActivity,
  type ExportableDailyPick,
  type ExportableUsMover,
  type ExportablePbtEntry,
  type ExportDataset,
  type ExportQueryOptions,
  ORDER_COLUMNS,
  TRADE_COLUMNS,
  POSITION_COLUMNS,
  EQUITY_COLUMNS,
  PERIOD_PNL_COLUMNS,
  SIGNAL_RECORD_COLUMNS,
  CONFIRMATION_COLUMNS,
  FAVORITE_COLUMNS,
  SCAN_INDEX_COLUMNS,
  OPTIMIZATION_INDEX_COLUMNS,
  INSTITUTION_ACTIVITY_COLUMNS,
  DAILY_PICK_COLUMNS,
  US_MOVER_COLUMNS,
  PBT_ENTRY_COLUMNS,
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

  it("SIGNAL_RECORD_COLUMNS includes hit-rate horizons", () => {
    const keys = SIGNAL_RECORD_COLUMNS.map((c) => c.key);
    expect(keys).toContain("signalDate");
    expect(keys).toContain("hitT1");
    expect(keys).toContain("hitT10");
    expect(keys).toContain("priceT1");
    expect(keys).toContain("strategyId");
  });

  it("CONFIRMATION_COLUMNS includes audit fields", () => {
    const keys = CONFIRMATION_COLUMNS.map((c) => c.key);
    expect(keys).toContain("confirmId");
    expect(keys).toContain("orderId");
    expect(keys).toContain("status");
    expect(keys).toContain("respondedAt");
  });
});

describe("ExportableSignalRecord", () => {
  it("has only primitive fields with checkpoint results flattened", () => {
    const signal: ExportableSignalRecord = {
      signalDate: "2025-01-10T09:30:00.000Z",
      symbol: "600519",
      direction: "buy",
      signalPrice: 1800,
      strategyId: "dual-ma",
      reason: "MA crossover",
      confidence: 0.85,
      hitT1: "hit",
      hitT3: "miss",
      hitT5: "pending",
      hitT10: "pending",
      priceT1: 1820,
      priceT3: 1780,
      priceT5: null,
      priceT10: null,
    };
    expect(signal.hitT1).toBe("hit");
    expect(signal.confidence).toBe(0.85);
    expect(signal.priceT5).toBeNull();
  });
});

describe("ExportableConfirmation", () => {
  it("has flat structure with all audit fields", () => {
    const confirm: ExportableConfirmation = {
      confirmId: "a3f9",
      orderId: "o-1",
      channel: "discord",
      userId: "user-123",
      status: "confirmed",
      expiresAt: "2025-01-10T09:32:00Z",
      respondedAt: "2025-01-10T09:31:00Z",
      respondedBy: "user-123",
      messageText: "买入 600519 100股",
    };
    expect(confirm.status).toBe("confirmed");
    expect(confirm.confirmId).toBe("a3f9");
  });
});

describe("ExportableFavorite", () => {
  it("has only primitive fields", () => {
    const fav: ExportableFavorite = {
      code: "600519",
      name: "贵州茅台",
      category: "watchlist",
      priority: 5,
      priceTarget: 2000,
      convictionScore: 8,
      notes: "白酒龙头",
      tags: "blue-chip, consumer",
      addedAt: "2025-01-10T09:00:00Z",
      updatedAt: "2025-01-15T10:00:00Z",
    };
    expect(fav.code).toBe("600519");
    expect(fav.convictionScore).toBe(8);
  });
});

describe("ExportableScanIndex", () => {
  it("has flat structure with screener info", () => {
    const scan: ExportableScanIndex = {
      id: "scan_001",
      createdAt: "2025-01-10T09:00:00Z",
      date: "2025-01-10",
      screenerId: "fscore",
      universe: "hs300",
      market: "cn_stock",
      totalScreened: 300,
      resultCount: 15,
      topCodes: "600519, 000858, 002714",
      tags: "value",
      notes: "",
    };
    expect(scan.screenerId).toBe("fscore");
    expect(scan.resultCount).toBe(15);
  });
});

describe("ExportableOptimizationIndex", () => {
  it("has flat structure with best combo metrics", () => {
    const opt: ExportableOptimizationIndex = {
      id: "opt_001",
      createdAt: "2025-01-10T09:00:00Z",
      capital: 1000000,
      startDate: "2024-01-01",
      endDate: "2024-12-31",
      autoScreen: true,
      totalRounds: 48,
      bestPoolLabel: "沪深300精选",
      bestStrategyLabel: "双均线",
      bestReturn: 35.5,
      bestSharpe: 1.8,
      bestMaxDrawdown: 12.3,
      bestWinRate: 65,
      bestSymbolCount: 10,
      bestCompositeScore: 82.5,
      tags: "auto",
      starred: true,
      notes: "",
    };
    expect(opt.bestReturn).toBe(35.5);
    expect(opt.autoScreen).toBe(true);
  });
});

describe("ExportableInstitutionActivity", () => {
  it("has flat structure with institution scan results", () => {
    const inst: ExportableInstitutionActivity = {
      id: "inst_001",
      timestamp: "2025-01-10T09:00:00Z",
      asOfDate: "2025-01-10",
      candidateCount: 20,
      leaderCount: 5,
      baitCount: 3,
      marketTone: "偏多",
      northboundNetIn: 5000000000,
      candidateSymbols: "600519, 000858",
      leaderSymbols: "600519",
      baitSymbols: "002714",
      topLeaderSymbols: "600519",
    };
    expect(inst.marketTone).toBe("偏多");
    expect(inst.leaderCount).toBe(5);
  });
});

describe("ExportableDailyPick", () => {
  it("has flat structure with pick summary", () => {
    const pick: ExportableDailyPick = {
      id: "pick_001",
      timestamp: "2025-01-10T09:00:00Z",
      date: "2025-01-10",
      threshold: 3,
      triggered: true,
      triggeredCount: 2,
      candidateCount: 50,
      pickCount: 5,
      topPicks: "600519(贵州茅台), 000858(五粮液)",
      triggeredUSSymbols: "AAPL, NVDA",
    };
    expect(pick.triggered).toBe(true);
    expect(pick.pickCount).toBe(5);
  });
});

describe("ExportableUsMover", () => {
  it("has flat structure with mover summary", () => {
    const mover: ExportableUsMover = {
      id: "us_001",
      timestamp: "2025-01-10T09:00:00Z",
      date: "2025-01-10",
      threshold: 5,
      totalCount: 20,
      significantCount: 8,
      topMovers: "NVDA(+8.5%), AAPL(-3.2%)",
    };
    expect(mover.significantCount).toBe(8);
  });
});

describe("ExportablePbtEntry", () => {
  it("has flat structure with portfolio backtest summary", () => {
    const pbt: ExportablePbtEntry = {
      id: "pbt_001",
      createdAt: "2025-01-10T09:00:00Z",
      date: "2025-01-10",
      symbols: "600519, 000858, 002714",
      period: "2024-01-01 ~ 2024-12-31",
      initialCash: 1000000,
      strategyCount: 5,
      successCount: 4,
      failCount: 1,
      bestStrategy: "dual-ma",
    };
    expect(pbt.strategyCount).toBe(5);
    expect(pbt.bestStrategy).toBe("dual-ma");
  });
});

describe("New column definitions", () => {
  it("FAVORITE_COLUMNS has bilingual labels", () => {
    expect(FAVORITE_COLUMNS.length).toBeGreaterThan(0);
    const keys = FAVORITE_COLUMNS.map((c) => c.key);
    expect(keys).toContain("code");
    expect(keys).toContain("priceTarget");
    expect(keys).toContain("convictionScore");
  });

  it("SCAN_INDEX_COLUMNS includes screener fields", () => {
    const keys = SCAN_INDEX_COLUMNS.map((c) => c.key);
    expect(keys).toContain("screenerId");
    expect(keys).toContain("resultCount");
  });

  it("OPTIMIZATION_INDEX_COLUMNS includes best combo fields", () => {
    const keys = OPTIMIZATION_INDEX_COLUMNS.map((c) => c.key);
    expect(keys).toContain("bestReturn");
    expect(keys).toContain("bestSharpe");
    expect(keys).toContain("bestCompositeScore");
  });

  it("INSTITUTION_ACTIVITY_COLUMNS includes market tone", () => {
    const keys = INSTITUTION_ACTIVITY_COLUMNS.map((c) => c.key);
    expect(keys).toContain("marketTone");
    expect(keys).toContain("northboundNetIn");
  });

  it("DAILY_PICK_COLUMNS includes pick counts", () => {
    const keys = DAILY_PICK_COLUMNS.map((c) => c.key);
    expect(keys).toContain("pickCount");
    expect(keys).toContain("topPicks");
  });

  it("US_MOVER_COLUMNS includes significant count", () => {
    const keys = US_MOVER_COLUMNS.map((c) => c.key);
    expect(keys).toContain("significantCount");
    expect(keys).toContain("topMovers");
  });

  it("PBT_ENTRY_COLUMNS includes strategy count", () => {
    const keys = PBT_ENTRY_COLUMNS.map((c) => c.key);
    expect(keys).toContain("strategyCount");
    expect(keys).toContain("bestStrategy");
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
      signalRecords: [],
      confirmations: [],
      accountSummary: null,
      priceAlerts: [],
      conditionalOrders: [],
      dcaPlans: [],
      breakoutSetups: [],
      strategyHealth: [],
      alerts: [],
      backtestHistory: [],
      favorites: [],
      scanHistory: [],
      optimizationHistory: [],
      institutionActivity: [],
      dailyPicks: [],
      usMovers: [],
      pbtHistory: [],
    };
    expect(ds.meta.mode).toBe("paper");
    expect(ds.orders).toEqual([]);
    expect(ds.attributionSummary).toBeNull();
  });
});
