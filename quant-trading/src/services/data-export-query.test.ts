import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  LivePosition,
  Order,
  PendingConfirmation,
  Portfolio,
  PortfolioPosition,
  QuantTradingConfig,
  TradeRecord,
} from "../types.js";
import type { Alert } from "./alert-store.js";
import type { BacktestIndexEntry } from "./backtest-store.js";
import type { BreakoutSetup } from "./breakout-monitor.js";
import type { ConditionalOrder } from "./conditional-order.js";
import type { DcaPlan } from "./dca-schedule.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { FavoriteStock } from "./favorites-store.js";
import type { OptimizationIndexEntry } from "./optimization-store.js";
import type { PriceAlertCondition } from "./price-alert.js";
import type { ScanIndexEntry } from "./scan-history-store.js";
import type { SignalTrackRecord } from "./signal-hitrate.js";
import type { StrategyHealthRecord } from "./strategy-health.js";
import type { TradeNote } from "./trade-journal.js";
import {
  ALERT_COLUMNS,
  BACKTEST_ENTRY_COLUMNS,
  BREAKOUT_SETUP_COLUMNS,
  CONDITIONAL_ORDER_COLUMNS,
  CONFIRMATION_COLUMNS,
  DAILY_PICK_COLUMNS,
  DCA_PLAN_COLUMNS,
  EQUITY_COLUMNS,
  FAVORITE_COLUMNS,
  INSTITUTION_ACTIVITY_COLUMNS,
  OPTIMIZATION_INDEX_COLUMNS,
  ORDER_COLUMNS,
  PBT_ENTRY_COLUMNS,
  PERIOD_PNL_COLUMNS,
  POSITION_COLUMNS,
  PRICE_ALERT_COLUMNS,
  SCAN_INDEX_COLUMNS,
  SIGNAL_RECORD_COLUMNS,
  STRATEGY_HEALTH_COLUMNS,
  TRADE_COLUMNS,
  US_MOVER_COLUMNS,
  queryExportableAlerts,
  queryExportableAttribution,
  queryExportableBacktestHistory,
  queryExportableBreakoutSetups,
  queryExportableConditionalOrders,
  queryExportableConfirmations,
  queryExportableDailyPicks,
  queryExportableDcaPlans,
  queryExportableEquityHistory,
  queryExportableFavorites,
  queryExportableInstitutionActivity,
  queryExportableOptimizationHistory,
  queryExportableOrders,
  queryExportablePbtHistory,
  queryExportablePositions,
  queryExportablePriceAlerts,
  queryExportableScanHistory,
  queryExportableSignalRecords,
  queryExportableStrategyHealth,
  queryExportableTrades,
  queryExportableUsMovers,
  queryExportDataset,
  type ExportableAccountSummary,
  type ExportableAlert,
  type ExportableAttributionSummary,
  type ExportableBacktestEntry,
  type ExportableBreakoutSetup,
  type ExportableConditionalOrder,
  type ExportableConfirmation,
  type ExportableDailyPick,
  type ExportableDcaPlan,
  type ExportableEquitySnapshot,
  type ExportableFavorite,
  type ExportableInstitutionActivity,
  type ExportableOptimizationIndex,
  type ExportableOrder,
  type ExportablePbtEntry,
  type ExportablePeriodPnl,
  type ExportablePosition,
  type ExportablePriceAlert,
  type ExportableScanIndex,
  type ExportableSignalRecord,
  type ExportableStrategyAttribution,
  type ExportableStrategyHealth,
  type ExportableSymbolAttribution,
  type ExportableTradeNote,
  type ExportableTrade,
  type ExportableUsMover,
  type ExportDataset,
  type ExportQueryOptions,
} from "./data-export-query.js";

// ── Mock fs ────────────────────────────────────────────────────

vi.mock("node:fs/promises");

const mockedFs = vi.mocked(fs);

// ── Mock computeAttribution ────────────────────────────────────

vi.mock("./performance-attribution.js", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./performance-attribution.js")>();
  return {
    ...orig,
    computeAttribution: vi.fn().mockReturnValue({
      dateRange: { from: "2025-01-01", to: "2025-01-31" },
      totalTrades: 3,
      totalNetPnl: 5000,
      totalGrossPnl: 5200,
      totalCommission: 200,
      overallWinRate: 0.67,
      avgWin: 4000,
      avgLoss: -1500,
      profitFactor: 2.6,
      byStrategy: [
        {
          strategyId: "dual-ma",
          trades: 3,
          wins: 2,
          losses: 1,
          winRate: 0.67,
          grossPnl: 5200,
          netPnl: 5000,
          avgWin: 4000,
          avgLoss: -1500,
          largestWin: 5000,
          largestLoss: -1500,
          profitFactor: 2.6,
          contributionPct: 100,
        },
      ],
      bySymbol: [
        {
          symbol: "600519",
          trades: 2,
          netPnl: 4000,
          avgPnl: 2000,
          winRate: 1.0,
          holdingDaysAvg: 5,
          contributionPct: 80,
        },
      ],
      byDay: [
        { period: "2025-01-10", trades: 1, netPnl: 3000, cumPnl: 3000, wins: 1, losses: 0 },
      ],
      byWeek: [
        { period: "2025-W02", trades: 2, netPnl: 4000, cumPnl: 4000, wins: 2, losses: 0 },
      ],
      byMonth: [
        { period: "2025-01", trades: 3, netPnl: 5000, cumPnl: 5000, wins: 2, losses: 1 },
      ],
    }),
  };
});

// ── Fixtures ───────────────────────────────────────────────────

const TEST_DATA_DIR = "/test/data";
const cfg: QuantTradingConfig = { dataDir: TEST_DATA_DIR };

function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
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
    ...overrides,
  } as Order;
}

function makeTrade(overrides: Partial<TradeRecord> = {}): TradeRecord {
  return {
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
    ...overrides,
  } as TradeRecord;
}

function makePortfolio(overrides: Partial<Portfolio> = {}): Portfolio {
  return {
    id: "paper-001",
    name: "Paper Portfolio",
    cash: 500000,
    totalValue: 1000000,
    positions: [
      {
        symbol: "600519",
        name: "贵州茅台",
        market: "cn_stock",
        quantity: 100,
        avgCost: 1800,
        currentPrice: 1900,
        marketValue: 190000,
        unrealizedPnl: 10000,
        unrealizedPnlPct: 5.56,
        weight: 0.19,
      } as PortfolioPosition,
    ],
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-15T10:00:00Z",
    ...overrides,
  };
}

function makeLivePosition(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    symbol: "000858",
    name: "五粮液",
    market: "cn_stock",
    quantity: 200,
    availableQty: 200,
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 150,
    currentPrice: 160,
    marketValue: 32000,
    unrealizedPnl: 2000,
    unrealizedPnlPct: 6.67,
    source: "broker",
    lastSyncAt: "2025-01-15T14:00:00Z",
    ...overrides,
  } as LivePosition;
}

function makeEquitySnapshot(overrides: Partial<EquitySnapshot> = {}): EquitySnapshot {
  return {
    date: "2025-01-10",
    equity: 1000000,
    cash: 500000,
    marketValue: 500000,
    dailyPnl: 5000,
    positionCount: 3,
    benchmark: 3200,
    ...overrides,
  };
}

function makeTradeNote(overrides: Partial<TradeNote> = {}): TradeNote {
  return {
    id: "n-1",
    tradeId: "t-1",
    symbol: "600519",
    date: "2025-01-10",
    content: "Good entry timing",
    tags: ["momentum", "breakout"],
    rating: 4,
    strategyId: "dual-ma",
    direction: "buy",
    entryPrice: 1800,
    exitPrice: 1900,
    pnl: 9950,
    lesson: "Follow the trend",
    createdAt: "2025-01-10T10:00:00Z",
    updatedAt: "2025-01-10T10:00:00Z",
    ...overrides,
  } as TradeNote;
}

function makeSignalRecord(overrides: Partial<SignalTrackRecord> = {}): SignalTrackRecord {
  return {
    signalTimestamp: new Date("2025-01-10T09:30:00Z").getTime(),
    symbol: "600519",
    direction: "buy",
    signalPrice: 1800,
    strategyId: "dual-ma",
    reason: "MA crossover",
    confidence: 0.85,
    checkpoints: {
      "T+1": { targetDate: "2025-01-11", price: 1820, result: "hit" },
      "T+3": { targetDate: "2025-01-13", price: 1780, result: "miss" },
      "T+5": { targetDate: "2025-01-15", result: "pending" },
      "T+10": { targetDate: "2025-01-20", result: "pending" },
    },
    ...overrides,
  } as SignalTrackRecord;
}

function makeConfirmation(overrides: Partial<PendingConfirmation> = {}): PendingConfirmation {
  return {
    confirmId: "c-1",
    orderId: "o-1",
    channel: "discord",
    userId: "user-123",
    messageText: "买入 600519 100股",
    expiresAt: "2025-01-10T09:35:00Z",
    status: "confirmed",
    respondedAt: "2025-01-10T09:31:00Z",
    respondedBy: "user-123",
    ...overrides,
  } as PendingConfirmation;
}

function makePriceAlert(overrides: Partial<PriceAlertCondition> = {}): PriceAlertCondition {
  return {
    id: "pa-1",
    symbol: "600519",
    type: "price_above",
    threshold: 2000,
    description: "贵州茅台 > 2000",
    createdAt: "2025-01-10T09:00:00Z",
    triggered: false,
    repeating: false,
    orderAction: {
      side: "buy",
      orderType: "market",
      quantity: 100,
      submitted: false,
    },
    ...overrides,
  } as PriceAlertCondition;
}

function makeConditionalOrder(overrides: Partial<ConditionalOrder> = {}): ConditionalOrder {
  return {
    id: "co-1",
    symbol: "600519",
    type: "stop_loss",
    entryPrice: 1800,
    triggerValue: 1700,
    quantity: 100,
    strategyId: "dual-ma",
    description: "止损 1700",
    createdAt: "2025-01-10T09:00:00Z",
    triggered: false,
    tiers: [
      { gainPct: 5, sellRatio: 0.5, triggered: true, triggerPrice: 1890, quantity: 50 },
      { gainPct: 10, sellRatio: 0.5, triggered: false, triggerPrice: 1980, quantity: 50 },
    ],
    ...overrides,
  } as ConditionalOrder;
}

function makeDcaPlan(overrides: Partial<DcaPlan> = {}): DcaPlan {
  return {
    id: "dca-1",
    symbol: "600519",
    amount: 10000,
    frequency: "monthly",
    dayOfPeriod: 1,
    hour: 10,
    status: "active",
    executionCount: 3,
    totalInvested: 30000,
    lastExecutedAt: "2025-01-01T10:00:00Z",
    nextExecutionAt: "2025-02-01T10:00:00Z",
    maxExecutions: 12,
    createdAt: "2024-10-01T10:00:00Z",
    description: "每月定投贵州茅台",
    ...overrides,
  } as DcaPlan;
}

function makeBreakoutSetup(overrides: Partial<BreakoutSetup> = {}): BreakoutSetup {
  return {
    id: "bs-1",
    symbol: "600519",
    triggerPrice: 2000,
    description: "突破 2000",
    createdAt: "2025-01-10T09:00:00Z",
    status: "watching",
    bufferPct: 1.0,
    holdChecks: 3,
    currentHoldCount: 0,
    orderType: "market",
    quantity: 100,
    stopLossPrice: 1950,
    enablePullback: true,
    expiresAt: "2025-02-10T09:00:00Z",
    ...overrides,
  } as BreakoutSetup;
}

function makeStrategyHealth(overrides: Partial<StrategyHealthRecord> = {}): StrategyHealthRecord {
  return {
    strategyId: "dual-ma",
    totalTrades: 50,
    recentResults: [
      { netPnl: 3000, exitTime: "2025-01-10T14:30:00Z" },
      { netPnl: -1000, exitTime: "2025-01-11T14:30:00Z" },
      { netPnl: 2000, exitTime: "2025-01-12T14:30:00Z" },
    ],
    lastSignalTime: "2025-01-12T09:30:00Z",
    ...overrides,
  };
}

function makeAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "alert-1",
    timestamp: "2025-01-10T09:00:00Z",
    severity: "warning",
    source: "pnl_monitor",
    message: "日亏损达 2%",
    acknowledged: false,
    ...overrides,
  } as Alert;
}

function makeBacktestEntry(overrides: Partial<BacktestIndexEntry> = {}): BacktestIndexEntry {
  return {
    id: "bt-1",
    createdAt: "2025-01-10T09:00:00Z",
    strategyId: "dual-ma",
    symbols: ["600519", "000858"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    engine: "vectorbt",
    tags: ["backtest", "ma"],
    starred: true,
    notes: "Good run",
    totalReturn: 35.5,
    annualizedReturn: 35.5,
    maxDrawdown: 12.3,
    sharpeRatio: 1.8,
    winRate: 0.65,
    totalTrades: 20,
    initialCash: 1000000,
    finalValue: 1355000,
    ...overrides,
  } as BacktestIndexEntry;
}

function makeFavorite(overrides: Partial<FavoriteStock> = {}): FavoriteStock {
  return {
    code: "600519",
    name: "贵州茅台",
    category: "watchlist",
    priority: 5,
    priceTarget: 2000,
    convictionScore: 8,
    notes: "白酒龙头",
    tags: ["blue-chip", "consumer"],
    addedAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-15T10:00:00Z",
    ...overrides,
  } as FavoriteStock;
}

function makeScanIndex(overrides: Partial<ScanIndexEntry> = {}): ScanIndexEntry {
  return {
    id: "scan-1",
    createdAt: "2025-01-10T09:00:00Z",
    date: "2025-01-10",
    screenerId: "fscore",
    universe: "hs300",
    market: "cn_stock",
    totalScreened: 300,
    resultCount: 15,
    topCodes: ["600519", "000858", "002714"],
    tags: ["value"],
    notes: "",
    ...overrides,
  } as ScanIndexEntry;
}

function makeOptimizationIndex(overrides: Partial<OptimizationIndexEntry> = {}): OptimizationIndexEntry {
  return {
    id: "opt-1",
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
    tags: ["auto"],
    starred: true,
    notes: "",
    ...overrides,
  } as OptimizationIndexEntry;
}

// ── Internal history types (same as in source) ──

interface InstitutionActivityHistoryEntry {
  id: string;
  version: string;
  timestamp: string;
  summary: {
    asOfDate: string;
    candidateCount: number;
    leaderCount: number;
    baitCount: number;
    marketTone: string;
    northboundNetIn: number | null;
  };
  candidateSymbols: string[];
  leaderSymbols: string[];
  baitSymbols: string[];
  topLeaders: Array<{ symbol: string; name: string }>;
}

interface DailyPickHistoryEntry {
  id: string;
  timestamp: string;
  date: string;
  threshold: number;
  triggered: boolean;
  triggeredCount: number;
  candidateCount: number;
  pickCount: number;
  picks: Array<{ code: string; name: string }>;
  triggeredUS: Array<{ symbol: string }>;
}

interface UsMoverHistoryEntry {
  id: string;
  timestamp: string;
  date: string;
  threshold: number;
  totalCount: number;
  significantCount: number;
  movers: Array<{ symbol: string; name: string; changePct: number }>;
}

interface PbtHistoryEntry {
  id: string;
  createdAt: string;
  date: string;
  symbols: string[];
  period: string;
  initialCash: number;
  strategyCount: number;
  successCount: number;
  failCount: number;
  bestStrategy: string | null;
}

function makeInstitutionActivity(overrides: Partial<InstitutionActivityHistoryEntry> = {}): InstitutionActivityHistoryEntry {
  return {
    id: "inst-1",
    version: "1",
    timestamp: "2025-01-10T09:00:00Z",
    summary: {
      asOfDate: "2025-01-10",
      candidateCount: 20,
      leaderCount: 5,
      baitCount: 3,
      marketTone: "偏多",
      northboundNetIn: 5000000000,
    },
    candidateSymbols: ["600519", "000858"],
    leaderSymbols: ["600519"],
    baitSymbols: ["002714"],
    topLeaders: [{ symbol: "600519", name: "贵州茅台" }],
    ...overrides,
  };
}

function makeDailyPick(overrides: Partial<DailyPickHistoryEntry> = {}): DailyPickHistoryEntry {
  return {
    id: "pick-1",
    timestamp: "2025-01-10T09:00:00Z",
    date: "2025-01-10",
    threshold: 3,
    triggered: true,
    triggeredCount: 2,
    candidateCount: 50,
    pickCount: 5,
    picks: [
      { code: "600519", name: "贵州茅台" },
      { code: "000858", name: "五粮液" },
    ],
    triggeredUS: [{ symbol: "AAPL" }, { symbol: "NVDA" }],
    ...overrides,
  };
}

function makeUsMover(overrides: Partial<UsMoverHistoryEntry> = {}): UsMoverHistoryEntry {
  return {
    id: "us-1",
    timestamp: "2025-01-10T09:00:00Z",
    date: "2025-01-10",
    threshold: 5,
    totalCount: 20,
    significantCount: 8,
    movers: [
      { symbol: "NVDA", name: "NVIDIA", changePct: 8.5 },
      { symbol: "AAPL", name: "Apple", changePct: -3.2 },
    ],
    ...overrides,
  };
}

function makePbtEntry(overrides: Partial<PbtHistoryEntry> = {}): PbtHistoryEntry {
  return {
    id: "pbt-1",
    createdAt: "2025-01-10T09:00:00Z",
    date: "2025-01-10",
    symbols: ["600519", "000858", "002714"],
    period: "2024-01-01 ~ 2024-12-31",
    initialCash: 1000000,
    strategyCount: 5,
    successCount: 4,
    failCount: 1,
    bestStrategy: "dual-ma",
    ...overrides,
  };
}

// ── File read helper ───────────────────────────────────────────

/** Map from file path pattern to data to return from readFile. */
const fileStore = new Map<string, unknown>();

beforeEach(() => {
  fileStore.clear();
  mockedFs.readFile.mockImplementation(async (p: any) => {
    const filePath = typeof p === "string" ? p : p.toString();
    for (const [pattern, data] of fileStore) {
      if (filePath.endsWith(pattern) || filePath === pattern) {
        return JSON.stringify(data);
      }
    }
    throw new Error(`ENOENT: no such file or directory, open '${filePath}'`);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function setFileData(filePathSuffix: string, data: unknown): void {
  fileStore.set(filePathSuffix, data);
}

// ════════════════════════════════════════════════════════════════
// ── Type-Level Tests: Exportable Structures Are Flat ──────────
// ════════════════════════════════════════════════════════════════

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

  it("allows null for optional number fields", () => {
    const order: ExportableOrder = {
      orderId: "o-2",
      symbol: "000858",
      market: "cn_stock",
      side: "buy",
      orderType: "market",
      price: null,
      quantity: 50,
      filledQty: 0,
      avgFillPrice: null,
      status: "pending",
      strategyId: "",
      brokerType: "paper",
      errorMsg: "",
      createdAt: "2025-01-10T09:30:00Z",
      updatedAt: "2025-01-10T09:30:00Z",
      submittedAt: "",
      filledAt: "",
    };
    expect(order.price).toBeNull();
    expect(order.avgFillPrice).toBeNull();
    expect(order.strategyId).toBe("");
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

  it("handles open trade (no exit)", () => {
    const trade: ExportableTrade = {
      tradeId: "t-2",
      symbol: "000858",
      name: "",
      market: "cn_stock",
      side: "buy",
      entryTime: "2025-01-10T09:30:00Z",
      exitTime: "",
      entryPrice: 150,
      exitPrice: null,
      quantity: 200,
      grossPnl: 0,
      commission: 0,
      netPnl: 0,
      holdingDays: null,
      strategyId: "",
      entryOrderId: "o-3",
      exitOrderId: "",
    };
    expect(trade.exitPrice).toBeNull();
    expect(trade.holdingDays).toBeNull();
    expect(trade.exitTime).toBe("");
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
    expect(paper.availableQty).toBeNull();

    const live: ExportablePosition = {
      ...paper,
      availableQty: 100,
      weight: null,
      source: "live",
    };
    expect(live.source).toBe("live");
    expect(live.availableQty).toBe(100);
    expect(live.weight).toBeNull();
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

describe("ExportDataset structure", () => {
  it("has correct shape with all sections", () => {
    const ds: ExportDataset = {
      meta: { exportedAt: new Date().toISOString(), mode: "paper" },
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

  it("supports date range in meta", () => {
    const ds: Partial<ExportDataset> = {
      meta: {
        exportedAt: "2025-01-15T00:00:00Z",
        mode: "live",
        dateRange: { from: "2025-01-01", to: "2025-01-31" },
      },
    };
    expect(ds.meta!.dateRange?.from).toBe("2025-01-01");
    expect(ds.meta!.mode).toBe("live");
  });
});

// ════════════════════════════════════════════════════════════════
// ── Column Definition Tests ───────────────────────────────────
// ════════════════════════════════════════════════════════════════

describe("Column definitions", () => {
  const allColumnSets = [
    { name: "ORDER_COLUMNS", cols: ORDER_COLUMNS },
    { name: "TRADE_COLUMNS", cols: TRADE_COLUMNS },
    { name: "POSITION_COLUMNS", cols: POSITION_COLUMNS },
    { name: "EQUITY_COLUMNS", cols: EQUITY_COLUMNS },
    { name: "PERIOD_PNL_COLUMNS", cols: PERIOD_PNL_COLUMNS },
    { name: "SIGNAL_RECORD_COLUMNS", cols: SIGNAL_RECORD_COLUMNS },
    { name: "CONFIRMATION_COLUMNS", cols: CONFIRMATION_COLUMNS },
    { name: "PRICE_ALERT_COLUMNS", cols: PRICE_ALERT_COLUMNS },
    { name: "CONDITIONAL_ORDER_COLUMNS", cols: CONDITIONAL_ORDER_COLUMNS },
    { name: "DCA_PLAN_COLUMNS", cols: DCA_PLAN_COLUMNS },
    { name: "BREAKOUT_SETUP_COLUMNS", cols: BREAKOUT_SETUP_COLUMNS },
    { name: "STRATEGY_HEALTH_COLUMNS", cols: STRATEGY_HEALTH_COLUMNS },
    { name: "ALERT_COLUMNS", cols: ALERT_COLUMNS },
    { name: "BACKTEST_ENTRY_COLUMNS", cols: BACKTEST_ENTRY_COLUMNS },
    { name: "FAVORITE_COLUMNS", cols: FAVORITE_COLUMNS },
    { name: "SCAN_INDEX_COLUMNS", cols: SCAN_INDEX_COLUMNS },
    { name: "OPTIMIZATION_INDEX_COLUMNS", cols: OPTIMIZATION_INDEX_COLUMNS },
    { name: "INSTITUTION_ACTIVITY_COLUMNS", cols: INSTITUTION_ACTIVITY_COLUMNS },
    { name: "DAILY_PICK_COLUMNS", cols: DAILY_PICK_COLUMNS },
    { name: "US_MOVER_COLUMNS", cols: US_MOVER_COLUMNS },
    { name: "PBT_ENTRY_COLUMNS", cols: PBT_ENTRY_COLUMNS },
  ];

  for (const { name, cols } of allColumnSets) {
    it(`${name} has bilingual labels and non-empty keys`, () => {
      expect(cols.length).toBeGreaterThan(0);
      for (const col of cols) {
        expect(col.key).toBeTruthy();
        expect(col.label).toBeTruthy();
        expect(col.labelCn).toBeTruthy();
      }
    });

    it(`${name} has no duplicate keys`, () => {
      const keys = cols.map((c) => c.key);
      expect(new Set(keys).size).toBe(keys.length);
    });
  }

  it("TRADE_COLUMNS includes key trade fields", () => {
    const keys = TRADE_COLUMNS.map((c) => c.key);
    expect(keys).toContain("tradeId");
    expect(keys).toContain("netPnl");
    expect(keys).toContain("entryPrice");
    expect(keys).toContain("exitPrice");
  });

  it("SIGNAL_RECORD_COLUMNS includes all hit-rate horizons", () => {
    const keys = SIGNAL_RECORD_COLUMNS.map((c) => c.key);
    expect(keys).toContain("hitT1");
    expect(keys).toContain("hitT3");
    expect(keys).toContain("hitT5");
    expect(keys).toContain("hitT10");
    expect(keys).toContain("priceT1");
    expect(keys).toContain("priceT10");
  });
});

// ════════════════════════════════════════════════════════════════
// ── Transformation Tests (via convenience functions) ──────────
// ════════════════════════════════════════════════════════════════

describe("queryExportableOrders", () => {
  it("transforms orders correctly", async () => {
    const orders = [makeOrder(), makeOrder({ orderId: "o-2", symbol: "000858" })];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg);
    expect(result).toHaveLength(2);
    expect(result[0]!.orderId).toBe("o-1");
    expect(result[0]!.symbol).toBe("600519");
    expect(result[0]!.price).toBe(1800);
  });

  it("handles missing optional fields with defaults", async () => {
    const order = makeOrder({
      price: undefined,
      avgFillPrice: undefined,
      strategyId: undefined,
      errorMsg: undefined,
      submittedAt: undefined,
      filledAt: undefined,
    });
    setFileData("orders-paper.json", [order]);

    const result = await queryExportableOrders(cfg);
    expect(result[0]!.price).toBeNull();
    expect(result[0]!.avgFillPrice).toBeNull();
    expect(result[0]!.strategyId).toBe("");
    expect(result[0]!.errorMsg).toBe("");
    expect(result[0]!.submittedAt).toBe("");
    expect(result[0]!.filledAt).toBe("");
  });

  it("filters by date range", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", createdAt: "2025-01-05T09:30:00Z" }),
      makeOrder({ orderId: "o-2", createdAt: "2025-01-10T09:30:00Z" }),
      makeOrder({ orderId: "o-3", createdAt: "2025-01-20T09:30:00Z" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { from: "2025-01-08", to: "2025-01-15" });
    expect(result).toHaveLength(1);
    expect(result[0]!.orderId).toBe("o-2");
  });

  it("filters by symbols", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", symbol: "600519" }),
      makeOrder({ orderId: "o-2", symbol: "000858" }),
      makeOrder({ orderId: "o-3", symbol: "002714" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { symbols: ["600519", "002714"] });
    expect(result).toHaveLength(2);
    expect(result.map((r) => r.symbol)).toEqual(["600519", "002714"]);
  });

  it("filters by strategy IDs", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", strategyId: "dual-ma" }),
      makeOrder({ orderId: "o-2", strategyId: "rsi" }),
      makeOrder({ orderId: "o-3", strategyId: undefined }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { strategyIds: ["dual-ma"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.strategyId).toBe("dual-ma");
  });

  it("returns empty array when file does not exist", async () => {
    const result = await queryExportableOrders(cfg);
    expect(result).toEqual([]);
  });

  it("uses live mode when specified", async () => {
    const orders = [makeOrder({ orderId: "live-1" })];
    setFileData("orders-live.json", orders);

    const result = await queryExportableOrders(cfg, { mode: "live" });
    expect(result).toHaveLength(1);
    expect(result[0]!.orderId).toBe("live-1");
  });
});

describe("queryExportableTrades", () => {
  it("transforms trades correctly", async () => {
    setFileData("trades-paper.json", [makeTrade()]);

    const result = await queryExportableTrades(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.tradeId).toBe("t-1");
    expect(result[0]!.netPnl).toBe(9950);
    expect(result[0]!.holdingDays).toBe(5);
  });

  it("handles open trades (no exit info)", async () => {
    const trade = makeTrade({
      exitTime: undefined,
      exitPrice: undefined,
      holdingDays: undefined,
      exitOrderId: undefined,
      name: undefined,
    });
    setFileData("trades-paper.json", [trade]);

    const result = await queryExportableTrades(cfg);
    expect(result[0]!.exitTime).toBe("");
    expect(result[0]!.exitPrice).toBeNull();
    expect(result[0]!.holdingDays).toBeNull();
    expect(result[0]!.exitOrderId).toBe("");
    expect(result[0]!.name).toBe("");
  });

  it("filters trades by date range", async () => {
    const trades = [
      makeTrade({ tradeId: "t-1", entryTime: "2025-01-05T09:30:00Z" }),
      makeTrade({ tradeId: "t-2", entryTime: "2025-01-10T09:30:00Z" }),
      makeTrade({ tradeId: "t-3", entryTime: "2025-02-01T09:30:00Z" }),
    ];
    setFileData("trades-paper.json", trades);

    const result = await queryExportableTrades(cfg, { from: "2025-01-08", to: "2025-01-31" });
    expect(result).toHaveLength(1);
    expect(result[0]!.tradeId).toBe("t-2");
  });

  it("filters by symbols and strategies combined", async () => {
    const trades = [
      makeTrade({ tradeId: "t-1", symbol: "600519", strategyId: "dual-ma" }),
      makeTrade({ tradeId: "t-2", symbol: "600519", strategyId: "rsi" }),
      makeTrade({ tradeId: "t-3", symbol: "000858", strategyId: "dual-ma" }),
    ];
    setFileData("trades-paper.json", trades);

    const result = await queryExportableTrades(cfg, {
      symbols: ["600519"],
      strategyIds: ["dual-ma"],
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.tradeId).toBe("t-1");
  });
});

describe("queryExportablePositions", () => {
  it("transforms paper positions", async () => {
    setFileData("portfolio-paper.json", makePortfolio());

    const result = await queryExportablePositions(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.source).toBe("paper");
    expect(result[0]!.symbol).toBe("600519");
    expect(result[0]!.availableQty).toBeNull();
    expect(result[0]!.weight).toBe(0.19);
  });

  it("transforms live positions", async () => {
    setFileData("positions-live.json", [makeLivePosition()]);

    const result = await queryExportablePositions(cfg, { mode: "live" });
    expect(result).toHaveLength(1);
    expect(result[0]!.source).toBe("live");
    expect(result[0]!.symbol).toBe("000858");
    expect(result[0]!.availableQty).toBe(200);
    expect(result[0]!.weight).toBeNull();
  });

  it("filters positions by symbol", async () => {
    const pf = makePortfolio({
      positions: [
        { symbol: "600519", market: "cn_stock", quantity: 100, avgCost: 1800 } as PortfolioPosition,
        { symbol: "000858", market: "cn_stock", quantity: 200, avgCost: 150 } as PortfolioPosition,
      ],
    });
    setFileData("portfolio-paper.json", pf);

    const result = await queryExportablePositions(cfg, { symbols: ["000858"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.symbol).toBe("000858");
  });

  it("returns empty when paper portfolio not found", async () => {
    const result = await queryExportablePositions(cfg, { mode: "paper" });
    expect(result).toEqual([]);
  });

  it("falls back to portfolio.json if portfolio-paper.json missing", async () => {
    setFileData("portfolio.json", makePortfolio());

    const result = await queryExportablePositions(cfg);
    expect(result).toHaveLength(1);
  });
});

describe("queryExportableEquityHistory", () => {
  it("transforms equity snapshots", async () => {
    setFileData("equity-history-paper.json", [makeEquitySnapshot()]);

    const result = await queryExportableEquityHistory(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.date).toBe("2025-01-10");
    expect(result[0]!.equity).toBe(1000000);
    expect(result[0]!.benchmark).toBe(3200);
  });

  it("handles null benchmark", async () => {
    setFileData("equity-history-paper.json", [makeEquitySnapshot({ benchmark: undefined })]);

    const result = await queryExportableEquityHistory(cfg);
    expect(result[0]!.benchmark).toBeNull();
  });

  it("filters by date range", async () => {
    const snapshots = [
      makeEquitySnapshot({ date: "2025-01-05" }),
      makeEquitySnapshot({ date: "2025-01-10" }),
      makeEquitySnapshot({ date: "2025-01-20" }),
    ];
    setFileData("equity-history-paper.json", snapshots);

    const result = await queryExportableEquityHistory(cfg, {
      from: "2025-01-08",
      to: "2025-01-15",
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.date).toBe("2025-01-10");
  });
});

describe("queryExportableAttribution", () => {
  it("returns attribution summary and breakdowns", async () => {
    setFileData("trades-paper.json", [makeTrade()]);

    const result = await queryExportableAttribution(cfg);
    expect(result.summary).not.toBeNull();
    expect(result.summary!.totalTrades).toBe(3);
    expect(result.summary!.totalNetPnl).toBe(5000);
    expect(result.byStrategy).toHaveLength(1);
    expect(result.byStrategy[0]!.strategyId).toBe("dual-ma");
    expect(result.bySymbol).toHaveLength(1);
    expect(result.bySymbol[0]!.symbol).toBe("600519");
    expect(result.periodPnl.length).toBeGreaterThan(0);
  });

  it("returns null summary when no trades", async () => {
    setFileData("trades-paper.json", []);

    const result = await queryExportableAttribution(cfg);
    expect(result.summary).toBeNull();
    expect(result.byStrategy).toEqual([]);
    expect(result.bySymbol).toEqual([]);
    expect(result.periodPnl).toEqual([]);
  });

  it("includes day/week/month granularity in periodPnl", async () => {
    setFileData("trades-paper.json", [makeTrade()]);

    const result = await queryExportableAttribution(cfg);
    const granularities = result.periodPnl.map((p) => p.granularity);
    expect(granularities).toContain("day");
    expect(granularities).toContain("week");
    expect(granularities).toContain("month");
  });
});

describe("queryExportableSignalRecords", () => {
  it("transforms signal records with checkpoint flattening", async () => {
    setFileData("signal-hitrate.json", [makeSignalRecord()]);

    const result = await queryExportableSignalRecords(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.hitT1).toBe("hit");
    expect(result[0]!.hitT3).toBe("miss");
    expect(result[0]!.hitT5).toBe("pending");
    expect(result[0]!.priceT1).toBe(1820);
    expect(result[0]!.priceT5).toBeNull();
    expect(result[0]!.confidence).toBe(0.85);
  });

  it("handles missing checkpoints gracefully", async () => {
    const record = makeSignalRecord({ checkpoints: {} });
    setFileData("signal-hitrate.json", [record]);

    const result = await queryExportableSignalRecords(cfg);
    expect(result[0]!.hitT1).toBe("pending");
    expect(result[0]!.priceT1).toBeNull();
    expect(result[0]!.hitT10).toBe("pending");
    expect(result[0]!.priceT10).toBeNull();
  });

  it("handles missing optional fields", async () => {
    const record = makeSignalRecord({ reason: undefined, confidence: undefined });
    setFileData("signal-hitrate.json", [record]);

    const result = await queryExportableSignalRecords(cfg);
    expect(result[0]!.reason).toBe("");
    expect(result[0]!.confidence).toBeNull();
  });

  it("filters by symbol and strategy", async () => {
    const records = [
      makeSignalRecord({ symbol: "600519", strategyId: "dual-ma" }),
      makeSignalRecord({ symbol: "000858", strategyId: "rsi" }),
    ];
    setFileData("signal-hitrate.json", records);

    const result = await queryExportableSignalRecords(cfg, {
      symbols: ["600519"],
      strategyIds: ["dual-ma"],
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.symbol).toBe("600519");
  });
});

describe("queryExportableConfirmations", () => {
  it("transforms confirmations correctly", async () => {
    setFileData("confirmations.json", [makeConfirmation()]);

    const result = await queryExportableConfirmations(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.confirmId).toBe("c-1");
    expect(result[0]!.status).toBe("confirmed");
    expect(result[0]!.channel).toBe("discord");
  });

  it("handles missing optional fields", async () => {
    const confirm = makeConfirmation({
      channel: undefined,
      userId: undefined,
      respondedAt: undefined,
      respondedBy: undefined,
    });
    setFileData("confirmations.json", [confirm]);

    const result = await queryExportableConfirmations(cfg);
    expect(result[0]!.channel).toBe("");
    expect(result[0]!.userId).toBe("");
    expect(result[0]!.respondedAt).toBe("");
    expect(result[0]!.respondedBy).toBe("");
  });

  it("filters by date range (expiresAt)", async () => {
    const confirmations = [
      makeConfirmation({ confirmId: "c-1", expiresAt: "2025-01-05T09:35:00Z" }),
      makeConfirmation({ confirmId: "c-2", expiresAt: "2025-01-10T09:35:00Z" }),
    ];
    setFileData("confirmations.json", confirmations);

    const result = await queryExportableConfirmations(cfg, { from: "2025-01-08" });
    expect(result).toHaveLength(1);
    expect(result[0]!.confirmId).toBe("c-2");
  });
});

describe("queryExportableTradeNotes (via dataset)", () => {
  it("transforms trade notes with tags joined", async () => {
    setFileData("trade-journal.json", [makeTradeNote()]);

    const ds = await queryExportDataset(cfg, { sections: ["notes"] });
    expect(ds.tradeNotes).toHaveLength(1);
    expect(ds.tradeNotes[0]!.tags).toBe("momentum, breakout");
    expect(ds.tradeNotes[0]!.rating).toBe(4);
    expect(ds.tradeNotes[0]!.lesson).toBe("Follow the trend");
  });

  it("handles missing optional fields in notes", async () => {
    const note = makeTradeNote({
      tradeId: undefined,
      rating: undefined,
      strategyId: undefined,
      direction: undefined,
      entryPrice: undefined,
      exitPrice: undefined,
      pnl: undefined,
      lesson: undefined,
    });
    setFileData("trade-journal.json", [note]);

    const ds = await queryExportDataset(cfg, { sections: ["notes"] });
    expect(ds.tradeNotes[0]!.tradeId).toBe("");
    expect(ds.tradeNotes[0]!.rating).toBeNull();
    expect(ds.tradeNotes[0]!.strategyId).toBe("");
    expect(ds.tradeNotes[0]!.direction).toBe("");
    expect(ds.tradeNotes[0]!.entryPrice).toBeNull();
    expect(ds.tradeNotes[0]!.exitPrice).toBeNull();
    expect(ds.tradeNotes[0]!.pnl).toBeNull();
    expect(ds.tradeNotes[0]!.lesson).toBe("");
  });
});

describe("queryExportablePriceAlerts", () => {
  it("transforms price alerts with order action flattened", async () => {
    setFileData("price-alerts.json", { alerts: [makePriceAlert()] });

    const result = await queryExportablePriceAlerts(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.hasOrderAction).toBe(true);
    expect(result[0]!.orderSide).toBe("buy");
    expect(result[0]!.orderType).toBe("market");
    expect(result[0]!.orderQuantity).toBe(100);
    expect(result[0]!.orderSubmitted).toBe(false);
  });

  it("handles alert without order action", async () => {
    const alert = makePriceAlert({ orderAction: undefined });
    setFileData("price-alerts.json", { alerts: [alert] });

    const result = await queryExportablePriceAlerts(cfg);
    expect(result[0]!.hasOrderAction).toBe(false);
    expect(result[0]!.orderSide).toBe("");
    expect(result[0]!.orderType).toBe("");
    expect(result[0]!.orderQuantity).toBeNull();
    expect(result[0]!.orderLimitPrice).toBeNull();
    expect(result[0]!.orderSubmitted).toBe(false);
  });

  it("filters by symbol", async () => {
    const alerts = [
      makePriceAlert({ id: "pa-1", symbol: "600519" }),
      makePriceAlert({ id: "pa-2", symbol: "000858" }),
    ];
    setFileData("price-alerts.json", { alerts });

    const result = await queryExportablePriceAlerts(cfg, { symbols: ["000858"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.symbol).toBe("000858");
  });
});

describe("queryExportableConditionalOrders", () => {
  it("transforms conditional orders with tier counting", async () => {
    setFileData("conditional-orders.json", { orders: [makeConditionalOrder()] });

    const result = await queryExportableConditionalOrders(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.tiersCount).toBe(2);
    expect(result[0]!.tiersTriggered).toBe(1);
    expect(result[0]!.type).toBe("stop_loss");
  });

  it("handles no tiers", async () => {
    const order = makeConditionalOrder({ tiers: undefined });
    setFileData("conditional-orders.json", { orders: [order] });

    const result = await queryExportableConditionalOrders(cfg);
    expect(result[0]!.tiersCount).toBe(0);
    expect(result[0]!.tiersTriggered).toBe(0);
  });

  it("handles missing optional fields", async () => {
    const order = makeConditionalOrder({
      strategyId: undefined,
      triggeredAt: undefined,
      triggerResult: undefined,
      peakPrice: undefined,
      trailingStopPrice: undefined,
    });
    setFileData("conditional-orders.json", { orders: [order] });

    const result = await queryExportableConditionalOrders(cfg);
    expect(result[0]!.strategyId).toBe("");
    expect(result[0]!.triggeredAt).toBe("");
    expect(result[0]!.triggerResult).toBe("");
    expect(result[0]!.peakPrice).toBeNull();
    expect(result[0]!.trailingStopPrice).toBeNull();
  });

  it("filters by symbol and strategy", async () => {
    const orders = [
      makeConditionalOrder({ id: "co-1", symbol: "600519", strategyId: "dual-ma" }),
      makeConditionalOrder({ id: "co-2", symbol: "000858", strategyId: "rsi" }),
    ];
    setFileData("conditional-orders.json", { orders });

    const result = await queryExportableConditionalOrders(cfg, {
      symbols: ["600519"],
      strategyIds: ["dual-ma"],
    });
    expect(result).toHaveLength(1);
  });
});

describe("queryExportableDcaPlans", () => {
  it("transforms DCA plans correctly", async () => {
    setFileData("dca-plans.json", { plans: [makeDcaPlan()] });

    const result = await queryExportableDcaPlans(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.frequency).toBe("monthly");
    expect(result[0]!.executionCount).toBe(3);
    expect(result[0]!.totalInvested).toBe(30000);
  });

  it("handles missing optional fields", async () => {
    const plan = makeDcaPlan({
      dayOfPeriod: undefined,
      hour: undefined,
      lastExecutedAt: undefined,
      nextExecutionAt: undefined,
      maxExecutions: undefined,
    });
    setFileData("dca-plans.json", { plans: [plan] });

    const result = await queryExportableDcaPlans(cfg);
    expect(result[0]!.dayOfPeriod).toBeNull();
    expect(result[0]!.hour).toBeNull();
    expect(result[0]!.lastExecutedAt).toBe("");
    expect(result[0]!.nextExecutionAt).toBe("");
    expect(result[0]!.maxExecutions).toBeNull();
  });
});

describe("queryExportableBreakoutSetups", () => {
  it("transforms breakout setups", async () => {
    setFileData("breakout-setups.json", { setups: [makeBreakoutSetup()] });

    const result = await queryExportableBreakoutSetups(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.triggerPrice).toBe(2000);
    expect(result[0]!.enablePullback).toBe(true);
    expect(result[0]!.stopLossPrice).toBe(1950);
  });

  it("handles missing optional fields", async () => {
    const setup = makeBreakoutSetup({
      stopLossPrice: undefined,
      expiresAt: undefined,
      triggeredAt: undefined,
      orderResult: undefined,
    });
    setFileData("breakout-setups.json", { setups: [setup] });

    const result = await queryExportableBreakoutSetups(cfg);
    expect(result[0]!.stopLossPrice).toBeNull();
    expect(result[0]!.expiresAt).toBe("");
    expect(result[0]!.triggeredAt).toBe("");
    expect(result[0]!.orderResult).toBe("");
  });
});

describe("queryExportableStrategyHealth", () => {
  it("transforms strategy health with win/loss calculation", async () => {
    setFileData("strategy-health.json", { strategies: [makeStrategyHealth()] });

    const result = await queryExportableStrategyHealth(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.recentTradeCount).toBe(3);
    expect(result[0]!.recentWins).toBe(2);
    expect(result[0]!.recentLosses).toBe(1);
    expect(result[0]!.recentWinRate).toBeCloseTo(2 / 3);
    expect(result[0]!.recentNetPnl).toBe(4000); // 3000 + -1000 + 2000
    expect(result[0]!.lastTradeTime).toBe("2025-01-12T14:30:00Z");
  });

  it("handles empty recent results", async () => {
    const health = makeStrategyHealth({ recentResults: [], lastSignalTime: null });
    setFileData("strategy-health.json", { strategies: [health] });

    const result = await queryExportableStrategyHealth(cfg);
    expect(result[0]!.recentTradeCount).toBe(0);
    expect(result[0]!.recentWins).toBe(0);
    expect(result[0]!.recentLosses).toBe(0);
    expect(result[0]!.recentWinRate).toBe(0);
    expect(result[0]!.recentNetPnl).toBe(0);
    expect(result[0]!.lastTradeTime).toBe("");
    expect(result[0]!.lastSignalTime).toBe("");
  });

  it("filters by strategy IDs", async () => {
    const records = [
      makeStrategyHealth({ strategyId: "dual-ma" }),
      makeStrategyHealth({ strategyId: "rsi" }),
    ];
    setFileData("strategy-health.json", { strategies: records });

    const result = await queryExportableStrategyHealth(cfg, { strategyIds: ["rsi"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.strategyId).toBe("rsi");
  });

  it("handles zero-pnl trades (neither win nor loss)", async () => {
    const health = makeStrategyHealth({
      recentResults: [
        { netPnl: 0, exitTime: "2025-01-10T14:30:00Z" },
        { netPnl: 1000, exitTime: "2025-01-11T14:30:00Z" },
      ],
    });
    setFileData("strategy-health.json", { strategies: [health] });

    const result = await queryExportableStrategyHealth(cfg);
    expect(result[0]!.recentWins).toBe(1);
    expect(result[0]!.recentLosses).toBe(0);
    expect(result[0]!.recentWinRate).toBe(1.0);
  });
});

describe("queryExportableAlerts", () => {
  it("transforms alerts correctly", async () => {
    setFileData("alerts.json", [makeAlert()]);

    const result = await queryExportableAlerts(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.severity).toBe("warning");
    expect(result[0]!.acknowledged).toBe(false);
  });

  it("filters by date range", async () => {
    const alerts = [
      makeAlert({ id: "a-1", timestamp: "2025-01-05T09:00:00Z" }),
      makeAlert({ id: "a-2", timestamp: "2025-01-10T09:00:00Z" }),
    ];
    setFileData("alerts.json", alerts);

    const result = await queryExportableAlerts(cfg, { from: "2025-01-08" });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("a-2");
  });
});

describe("queryExportableBacktestHistory", () => {
  it("transforms backtest entries with arrays joined", async () => {
    setFileData(path.join("backtests", "index.json"), [makeBacktestEntry()]);

    const result = await queryExportableBacktestHistory(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.symbols).toBe("600519, 000858");
    expect(result[0]!.tags).toBe("backtest, ma");
    expect(result[0]!.starred).toBe(true);
    expect(result[0]!.totalReturn).toBe(35.5);
  });

  it("handles missing notes", async () => {
    const entry = makeBacktestEntry({ notes: undefined });
    setFileData(path.join("backtests", "index.json"), [entry]);

    const result = await queryExportableBacktestHistory(cfg);
    expect(result[0]!.notes).toBe("");
  });

  it("filters by strategy and symbol", async () => {
    const entries = [
      makeBacktestEntry({ id: "bt-1", strategyId: "dual-ma", symbols: ["600519"] }),
      makeBacktestEntry({ id: "bt-2", strategyId: "rsi", symbols: ["000858"] }),
    ];
    setFileData(path.join("backtests", "index.json"), entries);

    const result = await queryExportableBacktestHistory(cfg, {
      strategyIds: ["dual-ma"],
      symbols: ["600519"],
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("bt-1");
  });
});

describe("queryExportableFavorites", () => {
  it("transforms favorites with tags joined", async () => {
    setFileData("favorites.json", [makeFavorite()]);

    const result = await queryExportableFavorites(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.tags).toBe("blue-chip, consumer");
    expect(result[0]!.convictionScore).toBe(8);
  });

  it("handles missing optional fields", async () => {
    const fav = makeFavorite({
      name: undefined,
      category: undefined,
      priority: undefined,
      priceTarget: undefined,
      convictionScore: undefined,
      notes: undefined,
      tags: undefined,
    });
    setFileData("favorites.json", [fav]);

    const result = await queryExportableFavorites(cfg);
    expect(result[0]!.name).toBe("");
    expect(result[0]!.category).toBe("");
    expect(result[0]!.priority).toBe(0);
    expect(result[0]!.priceTarget).toBeNull();
    expect(result[0]!.convictionScore).toBeNull();
    expect(result[0]!.notes).toBe("");
    expect(result[0]!.tags).toBe("");
  });

  it("filters by symbol (code)", async () => {
    const favs = [
      makeFavorite({ code: "600519" }),
      makeFavorite({ code: "000858" }),
    ];
    setFileData("favorites.json", favs);

    const result = await queryExportableFavorites(cfg, { symbols: ["600519"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.code).toBe("600519");
  });
});

describe("queryExportableScanHistory", () => {
  it("transforms scan index entries with arrays joined", async () => {
    setFileData(path.join("scans", "_index.json"), [makeScanIndex()]);

    const result = await queryExportableScanHistory(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.topCodes).toBe("600519, 000858, 002714");
    expect(result[0]!.tags).toBe("value");
    expect(result[0]!.screenerId).toBe("fscore");
  });

  it("filters by date range", async () => {
    const entries = [
      makeScanIndex({ id: "s-1", createdAt: "2025-01-05T09:00:00Z" }),
      makeScanIndex({ id: "s-2", createdAt: "2025-01-10T09:00:00Z" }),
    ];
    setFileData(path.join("scans", "_index.json"), entries);

    const result = await queryExportableScanHistory(cfg, { from: "2025-01-08" });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("s-2");
  });
});

describe("queryExportableOptimizationHistory", () => {
  it("transforms optimization entries", async () => {
    setFileData(path.join("optimizations", "_index.json"), [makeOptimizationIndex()]);

    const result = await queryExportableOptimizationHistory(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.bestReturn).toBe(35.5);
    expect(result[0]!.tags).toBe("auto");
    expect(result[0]!.autoScreen).toBe(true);
  });

  it("handles missing optional fields", async () => {
    const entry = makeOptimizationIndex({
      endDate: undefined,
      bestWinRate: undefined,
      bestCompositeScore: undefined,
      notes: undefined,
    });
    setFileData(path.join("optimizations", "_index.json"), [entry]);

    const result = await queryExportableOptimizationHistory(cfg);
    expect(result[0]!.endDate).toBe("");
    expect(result[0]!.bestWinRate).toBeNull();
    expect(result[0]!.bestCompositeScore).toBeNull();
    expect(result[0]!.notes).toBe("");
  });
});

describe("queryExportableInstitutionActivity", () => {
  it("transforms institution activity with arrays joined", async () => {
    setFileData("institution-activity-history.json", [makeInstitutionActivity()]);

    const result = await queryExportableInstitutionActivity(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.candidateSymbols).toBe("600519, 000858");
    expect(result[0]!.leaderSymbols).toBe("600519");
    expect(result[0]!.baitSymbols).toBe("002714");
    expect(result[0]!.topLeaderSymbols).toBe("600519");
    expect(result[0]!.marketTone).toBe("偏多");
  });

  it("filters by symbol (candidate or leader)", async () => {
    const entries = [
      makeInstitutionActivity({
        id: "i-1",
        candidateSymbols: ["600519"],
        leaderSymbols: [],
      }),
      makeInstitutionActivity({
        id: "i-2",
        candidateSymbols: [],
        leaderSymbols: ["000858"],
      }),
      makeInstitutionActivity({
        id: "i-3",
        candidateSymbols: ["002714"],
        leaderSymbols: ["002714"],
      }),
    ];
    setFileData("institution-activity-history.json", entries);

    const result = await queryExportableInstitutionActivity(cfg, { symbols: ["000858"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("i-2");
  });
});

describe("queryExportableDailyPicks", () => {
  it("transforms daily picks with formatted topPicks", async () => {
    setFileData("scan-history.json", [makeDailyPick()]);

    const result = await queryExportableDailyPicks(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.topPicks).toBe("600519(贵州茅台), 000858(五粮液)");
    expect(result[0]!.triggeredUSSymbols).toBe("AAPL, NVDA");
    expect(result[0]!.triggered).toBe(true);
  });

  it("filters by symbol (via picks.code)", async () => {
    const entries = [
      makeDailyPick({
        id: "p-1",
        picks: [{ code: "600519", name: "贵州茅台" }],
      }),
      makeDailyPick({
        id: "p-2",
        picks: [{ code: "000858", name: "五粮液" }],
      }),
    ];
    setFileData("scan-history.json", entries);

    const result = await queryExportableDailyPicks(cfg, { symbols: ["600519"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("p-1");
  });
});

describe("queryExportableUsMovers", () => {
  it("transforms US movers with formatted topMovers (top 10)", async () => {
    setFileData("us-mover-history.json", [makeUsMover()]);

    const result = await queryExportableUsMovers(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.topMovers).toContain("NVDA(+8.5%)");
    expect(result[0]!.topMovers).toContain("AAPL(-3.2%)");
  });

  it("filters by symbol (via movers.symbol)", async () => {
    const entries = [
      makeUsMover({
        id: "u-1",
        movers: [{ symbol: "NVDA", name: "NVIDIA", changePct: 8.5 }],
      }),
      makeUsMover({
        id: "u-2",
        movers: [{ symbol: "AAPL", name: "Apple", changePct: -3.2 }],
      }),
    ];
    setFileData("us-mover-history.json", entries);

    const result = await queryExportableUsMovers(cfg, { symbols: ["AAPL"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("u-2");
  });

  it("truncates topMovers to 10 entries", async () => {
    const movers = Array.from({ length: 15 }, (_, i) => ({
      symbol: `SYM${i}`,
      name: `Stock ${i}`,
      changePct: i * 1.1,
    }));
    setFileData("us-mover-history.json", [makeUsMover({ movers })]);

    const result = await queryExportableUsMovers(cfg);
    const moverParts = result[0]!.topMovers.split(", ");
    expect(moverParts.length).toBe(10);
  });
});

describe("queryExportablePbtHistory", () => {
  it("transforms PBT entries with arrays joined", async () => {
    setFileData("pbt-history.json", [makePbtEntry()]);

    const result = await queryExportablePbtHistory(cfg);
    expect(result).toHaveLength(1);
    expect(result[0]!.symbols).toBe("600519, 000858, 002714");
    expect(result[0]!.bestStrategy).toBe("dual-ma");
  });

  it("handles null bestStrategy", async () => {
    const entry = makePbtEntry({ bestStrategy: null });
    setFileData("pbt-history.json", [entry]);

    const result = await queryExportablePbtHistory(cfg);
    expect(result[0]!.bestStrategy).toBe("");
  });

  it("filters by symbol", async () => {
    const entries = [
      makePbtEntry({ id: "p-1", symbols: ["600519"] }),
      makePbtEntry({ id: "p-2", symbols: ["000858"] }),
    ];
    setFileData("pbt-history.json", entries);

    const result = await queryExportablePbtHistory(cfg, { symbols: ["600519"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("p-1");
  });
});

// ════════════════════════════════════════════════════════════════
// ── queryExportDataset Integration Tests ──────────────────────
// ════════════════════════════════════════════════════════════════

describe("queryExportDataset", () => {
  it("returns complete dataset with all sections populated", async () => {
    setFileData("orders-paper.json", [makeOrder()]);
    setFileData("trades-paper.json", [makeTrade()]);
    setFileData("portfolio-paper.json", makePortfolio());
    setFileData("equity-history-paper.json", [makeEquitySnapshot()]);
    setFileData("trade-journal.json", [makeTradeNote()]);
    setFileData("signal-hitrate.json", [makeSignalRecord()]);
    setFileData("confirmations.json", [makeConfirmation()]);
    setFileData("price-alerts.json", { alerts: [makePriceAlert()] });
    setFileData("conditional-orders.json", { orders: [makeConditionalOrder()] });
    setFileData("dca-plans.json", { plans: [makeDcaPlan()] });
    setFileData("breakout-setups.json", { setups: [makeBreakoutSetup()] });
    setFileData("strategy-health.json", { strategies: [makeStrategyHealth()] });
    setFileData("alerts.json", [makeAlert()]);
    setFileData(path.join("backtests", "index.json"), [makeBacktestEntry()]);
    setFileData("favorites.json", [makeFavorite()]);
    setFileData(path.join("scans", "_index.json"), [makeScanIndex()]);
    setFileData(path.join("optimizations", "_index.json"), [makeOptimizationIndex()]);
    setFileData("institution-activity-history.json", [makeInstitutionActivity()]);
    setFileData("scan-history.json", [makeDailyPick()]);
    setFileData("us-mover-history.json", [makeUsMover()]);
    setFileData("pbt-history.json", [makePbtEntry()]);

    const ds = await queryExportDataset(cfg);

    expect(ds.meta.mode).toBe("paper");
    expect(ds.meta.exportedAt).toBeTruthy();
    expect(ds.orders).toHaveLength(1);
    expect(ds.trades).toHaveLength(1);
    expect(ds.positions).toHaveLength(1);
    expect(ds.equityHistory).toHaveLength(1);
    expect(ds.attributionSummary).not.toBeNull();
    expect(ds.strategyAttribution).toHaveLength(1);
    expect(ds.symbolAttribution).toHaveLength(1);
    expect(ds.periodPnl.length).toBeGreaterThan(0);
    expect(ds.tradeNotes).toHaveLength(1);
    expect(ds.signalRecords).toHaveLength(1);
    expect(ds.confirmations).toHaveLength(1);
    expect(ds.priceAlerts).toHaveLength(1);
    expect(ds.conditionalOrders).toHaveLength(1);
    expect(ds.dcaPlans).toHaveLength(1);
    expect(ds.breakoutSetups).toHaveLength(1);
    expect(ds.strategyHealth).toHaveLength(1);
    expect(ds.alerts).toHaveLength(1);
    expect(ds.backtestHistory).toHaveLength(1);
    expect(ds.favorites).toHaveLength(1);
    expect(ds.scanHistory).toHaveLength(1);
    expect(ds.optimizationHistory).toHaveLength(1);
    expect(ds.institutionActivity).toHaveLength(1);
    expect(ds.dailyPicks).toHaveLength(1);
    expect(ds.usMovers).toHaveLength(1);
    expect(ds.pbtHistory).toHaveLength(1);
  });

  it("returns empty dataset when all files are missing", async () => {
    const ds = await queryExportDataset(cfg);

    expect(ds.meta.mode).toBe("paper");
    expect(ds.orders).toEqual([]);
    expect(ds.trades).toEqual([]);
    expect(ds.positions).toEqual([]);
    expect(ds.equityHistory).toEqual([]);
    expect(ds.attributionSummary).toBeNull();
    expect(ds.strategyAttribution).toEqual([]);
    expect(ds.symbolAttribution).toEqual([]);
    expect(ds.periodPnl).toEqual([]);
    expect(ds.accountSummary).toBeNull();
  });

  it("defaults to paper mode", async () => {
    setFileData("orders-paper.json", [makeOrder()]);

    const ds = await queryExportDataset(cfg);
    expect(ds.meta.mode).toBe("paper");
    expect(ds.orders).toHaveLength(1);
  });

  it("supports live mode", async () => {
    setFileData("orders-live.json", [makeOrder()]);
    setFileData("positions-live.json", [makeLivePosition()]);

    const ds = await queryExportDataset(cfg, { mode: "live" });
    expect(ds.meta.mode).toBe("live");
    expect(ds.orders).toHaveLength(1);
    expect(ds.positions).toHaveLength(1);
    expect(ds.positions[0]!.source).toBe("live");
  });

  it("respects section filtering - only fetch requested sections", async () => {
    setFileData("orders-paper.json", [makeOrder()]);
    setFileData("trades-paper.json", [makeTrade()]);

    const ds = await queryExportDataset(cfg, { sections: ["orders"] });
    expect(ds.orders).toHaveLength(1);
    expect(ds.trades).toEqual([]);
    expect(ds.positions).toEqual([]);
  });

  it("includes date range in meta when from/to provided", async () => {
    const ds = await queryExportDataset(cfg, { from: "2025-01-01", to: "2025-01-31" });
    expect(ds.meta.dateRange).toEqual({ from: "2025-01-01", to: "2025-01-31" });
  });

  it("meta.dateRange is undefined when no date range provided", async () => {
    const ds = await queryExportDataset(cfg);
    expect(ds.meta.dateRange).toBeUndefined();
  });

  it("meta.dateRange handles only from", async () => {
    const ds = await queryExportDataset(cfg, { from: "2025-01-01" });
    expect(ds.meta.dateRange).toEqual({ from: "2025-01-01", to: "" });
  });

  it("meta.dateRange handles only to", async () => {
    const ds = await queryExportDataset(cfg, { to: "2025-01-31" });
    expect(ds.meta.dateRange).toEqual({ from: "", to: "2025-01-31" });
  });

  it("attribution is skipped when no trades exist", async () => {
    setFileData("trades-paper.json", []);

    const ds = await queryExportDataset(cfg, { sections: ["trades", "attribution"] });
    expect(ds.attributionSummary).toBeNull();
    expect(ds.strategyAttribution).toEqual([]);
    expect(ds.symbolAttribution).toEqual([]);
    expect(ds.periodPnl).toEqual([]);
  });

  it("attribution is computed when trades exist", async () => {
    setFileData("trades-paper.json", [makeTrade()]);

    const ds = await queryExportDataset(cfg, { sections: ["attribution"] });
    expect(ds.attributionSummary).not.toBeNull();
    expect(ds.attributionSummary!.totalNetPnl).toBe(5000);
    expect(ds.strategyAttribution).toHaveLength(1);
  });

  it("trades section populates trades but attribution section is needed for attribution", async () => {
    setFileData("trades-paper.json", [makeTrade()]);

    const ds = await queryExportDataset(cfg, { sections: ["trades"] });
    expect(ds.trades).toHaveLength(1);
    expect(ds.attributionSummary).toBeNull();
  });
});

// ════════════════════════════════════════════════════════════════
// ── Account Summary Tests ─────────────────────────────────────
// ════════════════════════════════════════════════════════════════

describe("Account summary (via queryExportDataset)", () => {
  it("builds summary from equity history", async () => {
    const snapshots = [
      makeEquitySnapshot({ date: "2025-01-01", equity: 1000000, cash: 800000, marketValue: 200000, dailyPnl: 0, positionCount: 1 }),
      makeEquitySnapshot({ date: "2025-01-05", equity: 1020000, cash: 780000, marketValue: 240000, dailyPnl: 5000, positionCount: 2 }),
      makeEquitySnapshot({ date: "2025-01-10", equity: 980000, cash: 780000, marketValue: 200000, dailyPnl: -40000, positionCount: 2 }),
      makeEquitySnapshot({ date: "2025-01-15", equity: 1050000, cash: 750000, marketValue: 300000, dailyPnl: 70000, positionCount: 3 }),
    ];
    setFileData("equity-history-paper.json", snapshots);

    const ds = await queryExportDataset(cfg, { sections: ["account"] });
    const summary = ds.accountSummary;
    expect(summary).not.toBeNull();
    expect(summary!.mode).toBe("paper");
    expect(summary!.totalAssets).toBe(1050000);
    expect(summary!.totalReturn).toBe(50000); // 1050000 - 1000000
    expect(summary!.totalReturnPct).toBe(5); // 50000/1000000 * 100
    expect(summary!.maxDrawdown).toBe(40000); // peak 1020000 - 980000
    expect(summary!.maxDrawdownPct).toBeCloseTo(40000 / 1020000 * 100);
    expect(summary!.snapshotDate).toBe("2025-01-15");
  });

  it("builds summary from portfolio when no equity history (paper)", async () => {
    setFileData("portfolio-paper.json", makePortfolio());

    const ds = await queryExportDataset(cfg, { sections: ["account"] });
    const summary = ds.accountSummary;
    expect(summary).not.toBeNull();
    expect(summary!.mode).toBe("paper");
    expect(summary!.totalAssets).toBe(1000000);
    expect(summary!.cash).toBe(500000);
    expect(summary!.marketValue).toBe(500000);
    expect(summary!.positionCount).toBe(1);
    expect(summary!.totalReturn).toBeNull();
    expect(summary!.maxDrawdown).toBeNull();
  });

  it("returns null account summary when no data (live mode)", async () => {
    const ds = await queryExportDataset(cfg, { mode: "live", sections: ["account"] });
    expect(ds.accountSummary).toBeNull();
  });

  it("computes win rate from daily PnL", async () => {
    const snapshots = [
      makeEquitySnapshot({ date: "2025-01-01", dailyPnl: 1000 }),
      makeEquitySnapshot({ date: "2025-01-02", dailyPnl: -500 }),
      makeEquitySnapshot({ date: "2025-01-03", dailyPnl: 2000 }),
      makeEquitySnapshot({ date: "2025-01-04", dailyPnl: 0 }), // zero PnL not counted
    ];
    setFileData("equity-history-paper.json", snapshots);

    const ds = await queryExportDataset(cfg, { sections: ["account"] });
    expect(ds.accountSummary!.winRate).toBeCloseTo(2 / 3 * 100);
  });
});

// ════════════════════════════════════════════════════════════════
// ── Edge Cases / Boundary Conditions ──────────────────────────
// ════════════════════════════════════════════════════════════════

describe("Edge cases", () => {
  it("dateInRange: includes boundary dates", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", createdAt: "2025-01-10T09:30:00Z" }),
      makeOrder({ orderId: "o-2", createdAt: "2025-01-15T09:30:00Z" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { from: "2025-01-10", to: "2025-01-15" });
    expect(result).toHaveLength(2);
  });

  it("dateInRange: excludes dates outside range", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", createdAt: "2025-01-09T23:59:59Z" }),
      makeOrder({ orderId: "o-2", createdAt: "2025-01-16T00:00:00Z" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { from: "2025-01-10", to: "2025-01-15" });
    expect(result).toEqual([]);
  });

  it("handles empty arrays in all data files", async () => {
    setFileData("orders-paper.json", []);
    setFileData("trades-paper.json", []);
    setFileData("equity-history-paper.json", []);
    setFileData("trade-journal.json", []);
    setFileData("signal-hitrate.json", []);
    setFileData("confirmations.json", []);
    setFileData("price-alerts.json", { alerts: [] });
    setFileData("conditional-orders.json", { orders: [] });
    setFileData("dca-plans.json", { plans: [] });
    setFileData("breakout-setups.json", { setups: [] });
    setFileData("strategy-health.json", { strategies: [] });
    setFileData("alerts.json", []);
    setFileData(path.join("backtests", "index.json"), []);
    setFileData("favorites.json", []);

    const ds = await queryExportDataset(cfg);
    expect(ds.orders).toEqual([]);
    expect(ds.trades).toEqual([]);
    expect(ds.equityHistory).toEqual([]);
    expect(ds.attributionSummary).toBeNull();
  });

  it("uses default dataDir when config.dataDir is undefined", async () => {
    const cfgNoDir: QuantTradingConfig = {};
    // Should not throw, just return empty data
    const ds = await queryExportDataset(cfgNoDir, { sections: ["orders"] });
    expect(ds.orders).toEqual([]);
  });

  it("handles multiple filters simultaneously", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", symbol: "600519", strategyId: "dual-ma", createdAt: "2025-01-10T09:30:00Z" }),
      makeOrder({ orderId: "o-2", symbol: "600519", strategyId: "rsi", createdAt: "2025-01-10T09:30:00Z" }),
      makeOrder({ orderId: "o-3", symbol: "000858", strategyId: "dual-ma", createdAt: "2025-01-10T09:30:00Z" }),
      makeOrder({ orderId: "o-4", symbol: "600519", strategyId: "dual-ma", createdAt: "2025-02-10T09:30:00Z" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, {
      symbols: ["600519"],
      strategyIds: ["dual-ma"],
      from: "2025-01-01",
      to: "2025-01-31",
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.orderId).toBe("o-1");
  });

  it("handles malformed JSON (readJson returns null)", async () => {
    mockedFs.readFile.mockResolvedValue("not valid json" as any);

    // Should not throw, just return empty arrays
    const result = await queryExportableOrders(cfg);
    expect(result).toEqual([]);
  });

  it("backtest filter matches when any symbol in array matches", async () => {
    const entries = [
      makeBacktestEntry({ id: "bt-1", symbols: ["600519", "000858"] }),
      makeBacktestEntry({ id: "bt-2", symbols: ["002714"] }),
    ];
    setFileData(path.join("backtests", "index.json"), entries);

    const result = await queryExportableBacktestHistory(cfg, { symbols: ["000858"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("bt-1");
  });

  it("PBT filter matches when any symbol in array matches", async () => {
    const entries = [
      makePbtEntry({ id: "p-1", symbols: ["600519", "000858"] }),
      makePbtEntry({ id: "p-2", symbols: ["002714"] }),
    ];
    setFileData("pbt-history.json", entries);

    const result = await queryExportablePbtHistory(cfg, { symbols: ["000858"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("p-1");
  });

  it("strategy filter ignores items without strategyId", async () => {
    const orders = [
      makeOrder({ orderId: "o-1", strategyId: undefined }),
      makeOrder({ orderId: "o-2", strategyId: "dual-ma" }),
    ];
    setFileData("orders-paper.json", orders);

    const result = await queryExportableOrders(cfg, { strategyIds: ["dual-ma"] });
    expect(result).toHaveLength(1);
    expect(result[0]!.orderId).toBe("o-2");
  });

  it("empty sections array fetches all sections", async () => {
    setFileData("orders-paper.json", [makeOrder()]);
    setFileData("trades-paper.json", [makeTrade()]);

    // When sections is undefined/not provided, ALL_SECTIONS is used
    const ds = await queryExportDataset(cfg);
    expect(ds.orders).toHaveLength(1);
    expect(ds.trades).toHaveLength(1);
  });
});

// ════════════════════════════════════════════════════════════════
// ── UsMover topMovers formatting ──────────────────────────────
// ════════════════════════════════════════════════════════════════

describe("UsMover topMovers formatting", () => {
  it("formats positive change with + sign", async () => {
    setFileData("us-mover-history.json", [
      makeUsMover({ movers: [{ symbol: "NVDA", name: "NVIDIA", changePct: 8.5 }] }),
    ]);

    const result = await queryExportableUsMovers(cfg);
    expect(result[0]!.topMovers).toBe("NVDA(+8.5%)");
  });

  it("formats negative change without + sign", async () => {
    setFileData("us-mover-history.json", [
      makeUsMover({ movers: [{ symbol: "AAPL", name: "Apple", changePct: -3.2 }] }),
    ]);

    const result = await queryExportableUsMovers(cfg);
    expect(result[0]!.topMovers).toBe("AAPL(-3.2%)");
  });

  it("formats zero change as +0.0%", async () => {
    setFileData("us-mover-history.json", [
      makeUsMover({ movers: [{ symbol: "MSFT", name: "Microsoft", changePct: 0 }] }),
    ]);

    const result = await queryExportableUsMovers(cfg);
    expect(result[0]!.topMovers).toBe("MSFT(+0.0%)");
  });
});
