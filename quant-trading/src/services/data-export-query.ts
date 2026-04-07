/**
 * DataExportQuery — core data query & transformation layer for data export.
 *
 * Extracts data from multi-account management framework (orders, trades,
 * positions, accounts) and performance reporting module (equity history,
 * attribution, PnL), then transforms into unified exportable intermediate
 * structures.
 *
 * These intermediate structures are format-agnostic flat records suitable
 * for serialization into CSV, Excel, or other tabular formats.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
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
import {
  computeAttribution,
  type AttributionResult,
  type PeriodPnl,
  type StrategyAttribution,
  type SymbolAttribution,
} from "./performance-attribution.js";
import type { PriceAlertCondition } from "./price-alert.js";
import type { SignalTrackRecord } from "./signal-hitrate.js";
import type { StrategyHealthRecord } from "./strategy-health.js";
import type { TradeNote } from "./trade-journal.js";

// ── Exportable Intermediate Structures ──────────────────────────

/** Flat order row ready for tabular export. */
export interface ExportableOrder {
  orderId: string;
  symbol: string;
  market: string;
  side: string;
  orderType: string;
  price: number | null;
  quantity: number;
  filledQty: number;
  avgFillPrice: number | null;
  status: string;
  strategyId: string;
  brokerType: string;
  errorMsg: string;
  createdAt: string;
  updatedAt: string;
  submittedAt: string;
  filledAt: string;
}

/** Flat trade record row ready for tabular export. */
export interface ExportableTrade {
  tradeId: string;
  symbol: string;
  name: string;
  market: string;
  side: string;
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number | null;
  quantity: number;
  grossPnl: number;
  commission: number;
  netPnl: number;
  holdingDays: number | null;
  strategyId: string;
  entryOrderId: string;
  exitOrderId: string;
}

/** Flat position row (paper or live) ready for tabular export. */
export interface ExportablePosition {
  symbol: string;
  name: string;
  market: string;
  quantity: number;
  availableQty: number | null;
  avgCost: number;
  currentPrice: number | null;
  marketValue: number | null;
  unrealizedPnl: number | null;
  unrealizedPnlPct: number | null;
  weight: number | null;
  source: "paper" | "live";
}

/** Flat equity snapshot row ready for tabular export. */
export interface ExportableEquitySnapshot {
  date: string;
  equity: number;
  cash: number;
  marketValue: number;
  dailyPnl: number;
  positionCount: number;
  benchmark: number | null;
}

/** Flat performance attribution summary row. */
export interface ExportableAttributionSummary {
  dateFrom: string;
  dateTo: string;
  totalTrades: number;
  totalNetPnl: number;
  totalGrossPnl: number;
  totalCommission: number;
  overallWinRate: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
}

/** Flat strategy attribution row. */
export interface ExportableStrategyAttribution {
  strategyId: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  grossPnl: number;
  netPnl: number;
  avgWin: number;
  avgLoss: number;
  largestWin: number;
  largestLoss: number;
  profitFactor: number;
  contributionPct: number;
}

/** Flat symbol attribution row. */
export interface ExportableSymbolAttribution {
  symbol: string;
  trades: number;
  netPnl: number;
  avgPnl: number;
  winRate: number;
  holdingDaysAvg: number;
  contributionPct: number;
}

/** Flat period PnL row (daily/weekly/monthly). */
export interface ExportablePeriodPnl {
  period: string;
  granularity: "day" | "week" | "month";
  trades: number;
  netPnl: number;
  cumPnl: number;
  wins: number;
  losses: number;
}

/** Flat trade note row. */
export interface ExportableTradeNote {
  id: string;
  tradeId: string;
  symbol: string;
  date: string;
  content: string;
  tags: string;
  rating: number | null;
  strategyId: string;
  direction: string;
  entryPrice: number | null;
  exitPrice: number | null;
  pnl: number | null;
  lesson: string;
  createdAt: string;
  updatedAt: string;
}

/** Flat signal hit-rate record row ready for tabular export. */
export interface ExportableSignalRecord {
  signalDate: string;
  symbol: string;
  direction: string;
  signalPrice: number;
  strategyId: string;
  reason: string;
  confidence: number | null;
  hitT1: string;
  hitT3: string;
  hitT5: string;
  hitT10: string;
  priceT1: number | null;
  priceT3: number | null;
  priceT5: number | null;
  priceT10: number | null;
}

/** Flat confirmation history row ready for tabular export. */
export interface ExportableConfirmation {
  confirmId: string;
  orderId: string;
  channel: string;
  userId: string;
  status: string;
  expiresAt: string;
  respondedAt: string;
  respondedBy: string;
  messageText: string;
}

/** Flat price alert row ready for tabular export. */
export interface ExportablePriceAlert {
  id: string;
  symbol: string;
  type: string;
  threshold: number;
  description: string;
  createdAt: string;
  triggered: boolean;
  repeating: boolean;
  lastTriggeredAt: string;
  hasOrderAction: boolean;
  orderSide: string;
  orderType: string;
  orderQuantity: number | null;
  orderLimitPrice: number | null;
  orderSubmitted: boolean;
}

/** Flat conditional order row ready for tabular export. */
export interface ExportableConditionalOrder {
  id: string;
  symbol: string;
  type: string;
  entryPrice: number;
  triggerValue: number;
  quantity: number;
  strategyId: string;
  description: string;
  createdAt: string;
  triggered: boolean;
  triggeredAt: string;
  triggerResult: string;
  peakPrice: number | null;
  trailingStopPrice: number | null;
  tiersCount: number;
  tiersTriggered: number;
}

/** Flat DCA plan row ready for tabular export. */
export interface ExportableDcaPlan {
  id: string;
  symbol: string;
  amount: number;
  frequency: string;
  dayOfPeriod: number | null;
  hour: number | null;
  status: string;
  executionCount: number;
  totalInvested: number;
  lastExecutedAt: string;
  nextExecutionAt: string;
  maxExecutions: number | null;
  createdAt: string;
  description: string;
}

/** Flat breakout setup row ready for tabular export. */
export interface ExportableBreakoutSetup {
  id: string;
  symbol: string;
  triggerPrice: number;
  description: string;
  createdAt: string;
  status: string;
  bufferPct: number;
  holdChecks: number;
  currentHoldCount: number;
  orderType: string;
  quantity: number;
  stopLossPrice: number | null;
  enablePullback: boolean;
  expiresAt: string;
  triggeredAt: string;
  orderResult: string;
}

/** Flat strategy health row ready for tabular export. */
export interface ExportableStrategyHealth {
  strategyId: string;
  totalTrades: number;
  recentTradeCount: number;
  recentWins: number;
  recentLosses: number;
  recentWinRate: number;
  recentNetPnl: number;
  lastSignalTime: string;
  lastTradeTime: string;
}

/** Flat alert row ready for tabular export. */
export interface ExportableAlert {
  id: string;
  timestamp: string;
  severity: string;
  source: string;
  message: string;
  acknowledged: boolean;
}

/** Flat backtest index row ready for tabular export. */
export interface ExportableBacktestEntry {
  id: string;
  createdAt: string;
  strategyId: string;
  symbols: string;
  startDate: string;
  endDate: string;
  engine: string;
  tags: string;
  starred: boolean;
  notes: string;
  totalReturn: number;
  annualizedReturn: number;
  maxDrawdown: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
  initialCash: number;
  finalValue: number;
}

/** Account summary row. */
export interface ExportableAccountSummary {
  mode: "paper" | "live";
  accountId: string;
  totalAssets: number;
  cash: number;
  marketValue: number;
  positionCount: number;
  totalReturn: number | null;
  totalReturnPct: number | null;
  maxDrawdown: number | null;
  maxDrawdownPct: number | null;
  winRate: number | null;
  snapshotDate: string;
}

/** Bundled export dataset — all intermediate structures in one object. */
export interface ExportDataset {
  meta: {
    exportedAt: string;
    mode: "paper" | "live";
    dateRange?: { from: string; to: string };
  };
  orders: ExportableOrder[];
  trades: ExportableTrade[];
  positions: ExportablePosition[];
  equityHistory: ExportableEquitySnapshot[];
  attributionSummary: ExportableAttributionSummary | null;
  strategyAttribution: ExportableStrategyAttribution[];
  symbolAttribution: ExportableSymbolAttribution[];
  periodPnl: ExportablePeriodPnl[];
  tradeNotes: ExportableTradeNote[];
  signalRecords: ExportableSignalRecord[];
  confirmations: ExportableConfirmation[];
  accountSummary: ExportableAccountSummary | null;
  priceAlerts: ExportablePriceAlert[];
  conditionalOrders: ExportableConditionalOrder[];
  dcaPlans: ExportableDcaPlan[];
  breakoutSetups: ExportableBreakoutSetup[];
  strategyHealth: ExportableStrategyHealth[];
  alerts: ExportableAlert[];
  backtestHistory: ExportableBacktestEntry[];
}

// ── Query Options ───────────────────────────────────────────────

export interface ExportQueryOptions {
  /** Paper or live mode. Default: "paper". */
  mode?: "paper" | "live";
  /** Start date filter (ISO YYYY-MM-DD, inclusive). */
  from?: string;
  /** End date filter (ISO YYYY-MM-DD, inclusive). */
  to?: string;
  /** Filter by symbols. */
  symbols?: string[];
  /** Filter by strategy IDs. */
  strategyIds?: string[];
  /** Which data sections to include. All by default. */
  sections?: ExportSection[];
}

export type ExportSection =
  | "orders"
  | "trades"
  | "positions"
  | "equity"
  | "attribution"
  | "notes"
  | "signals"
  | "confirmations"
  | "account"
  | "priceAlerts"
  | "conditionalOrders"
  | "dcaPlans"
  | "breakoutSetups"
  | "strategyHealth"
  | "alerts"
  | "backtestHistory";

const ALL_SECTIONS: ExportSection[] = [
  "orders",
  "trades",
  "positions",
  "equity",
  "attribution",
  "notes",
  "signals",
  "confirmations",
  "account",
  "priceAlerts",
  "conditionalOrders",
  "dcaPlans",
  "breakoutSetups",
  "strategyHealth",
  "alerts",
  "backtestHistory",
];

// ── Internal helpers ────────────────────────────────────────────

function dataDir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

async function readJson<T>(filePath: string): Promise<T | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function dateInRange(dateStr: string | undefined, from?: string, to?: string): boolean {
  if (!dateStr) return true;
  const d = dateStr.slice(0, 10);
  if (from && d < from) return false;
  if (to && d > to) return false;
  return true;
}

// ── Raw Data Query Functions ────────────────────────────────────

/** Load orders from file for a given mode. */
async function queryOrders(
  cfg: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<Order[]> {
  const filePath = path.join(dataDir(cfg), `orders-${mode}.json`);
  return (await readJson<Order[]>(filePath)) ?? [];
}

/** Load trade records from file for a given mode. */
async function queryTrades(
  cfg: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<TradeRecord[]> {
  const filePath = path.join(dataDir(cfg), `trades-${mode}.json`);
  return (await readJson<TradeRecord[]>(filePath)) ?? [];
}

/** Load paper portfolio. */
async function queryPaperPortfolio(cfg: QuantTradingConfig): Promise<Portfolio | null> {
  const dir = dataDir(cfg);
  for (const filename of ["portfolio-paper.json", "portfolio.json"]) {
    const pf = await readJson<Portfolio>(path.join(dir, filename));
    if (pf) return pf;
  }
  return null;
}

/** Load live positions from file. */
async function queryLivePositions(cfg: QuantTradingConfig): Promise<LivePosition[]> {
  // Live positions may be cached in the positions-live.json file
  const filePath = path.join(dataDir(cfg), "positions-live.json");
  return (await readJson<LivePosition[]>(filePath)) ?? [];
}

/** Load equity history snapshots from file for a given mode. */
async function queryEquityHistory(
  cfg: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<EquitySnapshot[]> {
  const suffix = mode ? `-${mode}` : "";
  const filePath = path.join(dataDir(cfg), `equity-history${suffix}.json`);
  return (await readJson<EquitySnapshot[]>(filePath)) ?? [];
}

/** Load trade journal notes. */
async function queryTradeNotes(cfg: QuantTradingConfig): Promise<TradeNote[]> {
  const filePath = path.join(dataDir(cfg), "trade-journal.json");
  return (await readJson<TradeNote[]>(filePath)) ?? [];
}

/** Load signal hit-rate records. */
async function querySignalRecords(cfg: QuantTradingConfig): Promise<SignalTrackRecord[]> {
  const filePath = path.join(dataDir(cfg), "signal-hitrate.json");
  return (await readJson<SignalTrackRecord[]>(filePath)) ?? [];
}

/** Load confirmation history records. */
async function queryConfirmations(cfg: QuantTradingConfig): Promise<PendingConfirmation[]> {
  const filePath = path.join(dataDir(cfg), "confirmations.json");
  return (await readJson<PendingConfirmation[]>(filePath)) ?? [];
}

/** Load price alert conditions. */
async function queryPriceAlerts(cfg: QuantTradingConfig): Promise<PriceAlertCondition[]> {
  const filePath = path.join(dataDir(cfg), "price-alerts.json");
  const data = await readJson<{ alerts: PriceAlertCondition[] }>(filePath);
  return data?.alerts ?? [];
}

/** Load conditional orders. */
async function queryConditionalOrders(cfg: QuantTradingConfig): Promise<ConditionalOrder[]> {
  const filePath = path.join(dataDir(cfg), "conditional-orders.json");
  const data = await readJson<{ orders: ConditionalOrder[] }>(filePath);
  return data?.orders ?? [];
}

/** Load DCA plans. */
async function queryDcaPlans(cfg: QuantTradingConfig): Promise<DcaPlan[]> {
  const filePath = path.join(dataDir(cfg), "dca-plans.json");
  const data = await readJson<{ plans: DcaPlan[] }>(filePath);
  return data?.plans ?? [];
}

/** Load breakout setups. */
async function queryBreakoutSetups(cfg: QuantTradingConfig): Promise<BreakoutSetup[]> {
  const filePath = path.join(dataDir(cfg), "breakout-setups.json");
  const data = await readJson<{ setups: BreakoutSetup[] }>(filePath);
  return data?.setups ?? [];
}

/** Load strategy health records. */
async function queryStrategyHealthRecords(cfg: QuantTradingConfig): Promise<StrategyHealthRecord[]> {
  const filePath = path.join(dataDir(cfg), "strategy-health.json");
  const data = await readJson<{ strategies: StrategyHealthRecord[] }>(filePath);
  return data?.strategies ?? [];
}

/** Load persisted alerts from alert store. */
async function queryAlerts(cfg: QuantTradingConfig): Promise<Alert[]> {
  const filePath = path.join(dataDir(cfg), "alerts.json");
  return (await readJson<Alert[]>(filePath)) ?? [];
}

/** Load backtest index entries. */
async function queryBacktestIndex(cfg: QuantTradingConfig): Promise<BacktestIndexEntry[]> {
  const filePath = path.join(dataDir(cfg), "backtests", "index.json");
  return (await readJson<BacktestIndexEntry[]>(filePath)) ?? [];
}

// ── Transformation Functions ────────────────────────────────────

function transformOrder(o: Order): ExportableOrder {
  return {
    orderId: o.orderId,
    symbol: o.symbol,
    market: o.market,
    side: o.side,
    orderType: o.orderType,
    price: o.price ?? null,
    quantity: o.quantity,
    filledQty: o.filledQty,
    avgFillPrice: o.avgFillPrice ?? null,
    status: o.status,
    strategyId: o.strategyId ?? "",
    brokerType: o.brokerType,
    errorMsg: o.errorMsg ?? "",
    createdAt: o.createdAt,
    updatedAt: o.updatedAt,
    submittedAt: o.submittedAt ?? "",
    filledAt: o.filledAt ?? "",
  };
}

function transformTrade(t: TradeRecord): ExportableTrade {
  return {
    tradeId: t.tradeId,
    symbol: t.symbol,
    name: t.name ?? "",
    market: t.market,
    side: t.side,
    entryTime: t.entryTime,
    exitTime: t.exitTime ?? "",
    entryPrice: t.entryPrice,
    exitPrice: t.exitPrice ?? null,
    quantity: t.quantity,
    grossPnl: t.grossPnl,
    commission: t.commission,
    netPnl: t.netPnl,
    holdingDays: t.holdingDays ?? null,
    strategyId: t.strategyId ?? "",
    entryOrderId: t.entryOrderId,
    exitOrderId: t.exitOrderId ?? "",
  };
}

function transformPaperPosition(p: PortfolioPosition): ExportablePosition {
  return {
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    availableQty: null,
    avgCost: p.avgCost,
    currentPrice: p.currentPrice ?? null,
    marketValue: p.marketValue ?? null,
    unrealizedPnl: p.unrealizedPnl ?? null,
    unrealizedPnlPct: p.unrealizedPnlPct ?? null,
    weight: p.weight ?? null,
    source: "paper",
  };
}

function transformLivePosition(p: LivePosition): ExportablePosition {
  return {
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    availableQty: p.availableQty,
    avgCost: p.avgCost,
    currentPrice: p.currentPrice ?? null,
    marketValue: p.marketValue ?? null,
    unrealizedPnl: p.unrealizedPnl ?? null,
    unrealizedPnlPct: p.unrealizedPnlPct ?? null,
    weight: null,
    source: "live",
  };
}

function transformEquitySnapshot(s: EquitySnapshot): ExportableEquitySnapshot {
  return {
    date: s.date,
    equity: s.equity,
    cash: s.cash,
    marketValue: s.marketValue,
    dailyPnl: s.dailyPnl,
    positionCount: s.positionCount,
    benchmark: s.benchmark ?? null,
  };
}

function transformStrategyAttribution(a: StrategyAttribution): ExportableStrategyAttribution {
  return { ...a };
}

function transformSymbolAttribution(a: SymbolAttribution): ExportableSymbolAttribution {
  return { ...a };
}

function transformPeriodPnl(p: PeriodPnl, granularity: "day" | "week" | "month"): ExportablePeriodPnl {
  return {
    period: p.period,
    granularity,
    trades: p.trades,
    netPnl: p.netPnl,
    cumPnl: p.cumPnl,
    wins: p.wins,
    losses: p.losses,
  };
}

function transformTradeNote(n: TradeNote): ExportableTradeNote {
  return {
    id: n.id,
    tradeId: n.tradeId ?? "",
    symbol: n.symbol,
    date: n.date,
    content: n.content,
    tags: n.tags.join(", "),
    rating: n.rating ?? null,
    strategyId: n.strategyId ?? "",
    direction: n.direction ?? "",
    entryPrice: n.entryPrice ?? null,
    exitPrice: n.exitPrice ?? null,
    pnl: n.pnl ?? null,
    lesson: n.lesson ?? "",
    createdAt: n.createdAt,
    updatedAt: n.updatedAt,
  };
}

function checkpointResult(
  checkpoints: SignalTrackRecord["checkpoints"],
  key: string,
): string {
  return checkpoints[key]?.result ?? "pending";
}

function checkpointPrice(
  checkpoints: SignalTrackRecord["checkpoints"],
  key: string,
): number | null {
  return checkpoints[key]?.price ?? null;
}

function transformSignalRecord(r: SignalTrackRecord): ExportableSignalRecord {
  return {
    signalDate: new Date(r.signalTimestamp).toISOString(),
    symbol: r.symbol,
    direction: r.direction,
    signalPrice: r.signalPrice,
    strategyId: r.strategyId,
    reason: r.reason ?? "",
    confidence: r.confidence ?? null,
    hitT1: checkpointResult(r.checkpoints, "T+1"),
    hitT3: checkpointResult(r.checkpoints, "T+3"),
    hitT5: checkpointResult(r.checkpoints, "T+5"),
    hitT10: checkpointResult(r.checkpoints, "T+10"),
    priceT1: checkpointPrice(r.checkpoints, "T+1"),
    priceT3: checkpointPrice(r.checkpoints, "T+3"),
    priceT5: checkpointPrice(r.checkpoints, "T+5"),
    priceT10: checkpointPrice(r.checkpoints, "T+10"),
  };
}

function transformConfirmation(c: PendingConfirmation): ExportableConfirmation {
  return {
    confirmId: c.confirmId,
    orderId: c.orderId,
    channel: c.channel ?? "",
    userId: c.userId ?? "",
    status: c.status,
    expiresAt: c.expiresAt,
    respondedAt: c.respondedAt ?? "",
    respondedBy: c.respondedBy ?? "",
    messageText: c.messageText,
  };
}

function transformPriceAlert(a: PriceAlertCondition): ExportablePriceAlert {
  return {
    id: a.id,
    symbol: a.symbol,
    type: a.type,
    threshold: a.threshold,
    description: a.description,
    createdAt: a.createdAt,
    triggered: a.triggered,
    repeating: a.repeating,
    lastTriggeredAt: a.lastTriggeredAt ?? "",
    hasOrderAction: !!a.orderAction,
    orderSide: a.orderAction?.side ?? "",
    orderType: a.orderAction?.orderType ?? "",
    orderQuantity: a.orderAction?.quantity ?? null,
    orderLimitPrice: a.orderAction?.limitPrice ?? null,
    orderSubmitted: a.orderAction?.submitted ?? false,
  };
}

function transformConditionalOrder(o: ConditionalOrder): ExportableConditionalOrder {
  const tiersTriggered = o.tiers?.filter((t) => t.triggered).length ?? 0;
  return {
    id: o.id,
    symbol: o.symbol,
    type: o.type,
    entryPrice: o.entryPrice,
    triggerValue: o.triggerValue,
    quantity: o.quantity,
    strategyId: o.strategyId ?? "",
    description: o.description,
    createdAt: o.createdAt,
    triggered: o.triggered,
    triggeredAt: o.triggeredAt ?? "",
    triggerResult: o.triggerResult ?? "",
    peakPrice: o.peakPrice ?? null,
    trailingStopPrice: o.trailingStopPrice ?? null,
    tiersCount: o.tiers?.length ?? 0,
    tiersTriggered,
  };
}

function transformDcaPlan(p: DcaPlan): ExportableDcaPlan {
  return {
    id: p.id,
    symbol: p.symbol,
    amount: p.amount,
    frequency: p.frequency,
    dayOfPeriod: p.dayOfPeriod ?? null,
    hour: p.hour ?? null,
    status: p.status,
    executionCount: p.executionCount,
    totalInvested: p.totalInvested,
    lastExecutedAt: p.lastExecutedAt ?? "",
    nextExecutionAt: p.nextExecutionAt ?? "",
    maxExecutions: p.maxExecutions ?? null,
    createdAt: p.createdAt,
    description: p.description,
  };
}

function transformBreakoutSetup(s: BreakoutSetup): ExportableBreakoutSetup {
  return {
    id: s.id,
    symbol: s.symbol,
    triggerPrice: s.triggerPrice,
    description: s.description,
    createdAt: s.createdAt,
    status: s.status,
    bufferPct: s.bufferPct,
    holdChecks: s.holdChecks,
    currentHoldCount: s.currentHoldCount,
    orderType: s.orderType,
    quantity: s.quantity,
    stopLossPrice: s.stopLossPrice ?? null,
    enablePullback: s.enablePullback,
    expiresAt: s.expiresAt ?? "",
    triggeredAt: s.triggeredAt ?? "",
    orderResult: s.orderResult ?? "",
  };
}

function transformStrategyHealth(r: StrategyHealthRecord): ExportableStrategyHealth {
  const wins = r.recentResults.filter((t) => t.netPnl > 0).length;
  const losses = r.recentResults.filter((t) => t.netPnl < 0).length;
  const recentNetPnl = r.recentResults.reduce((sum, t) => sum + t.netPnl, 0);
  const lastTrade = r.recentResults.length > 0
    ? r.recentResults[r.recentResults.length - 1]!.exitTime
    : "";
  return {
    strategyId: r.strategyId,
    totalTrades: r.totalTrades,
    recentTradeCount: r.recentResults.length,
    recentWins: wins,
    recentLosses: losses,
    recentWinRate: wins + losses > 0 ? wins / (wins + losses) : 0,
    recentNetPnl,
    lastSignalTime: r.lastSignalTime ?? "",
    lastTradeTime: lastTrade,
  };
}

function transformAlert(a: Alert): ExportableAlert {
  return {
    id: a.id,
    timestamp: a.timestamp,
    severity: a.severity,
    source: a.source,
    message: a.message,
    acknowledged: a.acknowledged,
  };
}

function transformBacktestEntry(e: BacktestIndexEntry): ExportableBacktestEntry {
  return {
    id: e.id,
    createdAt: e.createdAt,
    strategyId: e.strategyId,
    symbols: e.symbols.join(", "),
    startDate: e.startDate,
    endDate: e.endDate,
    engine: e.engine,
    tags: e.tags.join(", "),
    starred: e.starred,
    notes: e.notes ?? "",
    totalReturn: e.totalReturn,
    annualizedReturn: e.annualizedReturn,
    maxDrawdown: e.maxDrawdown,
    sharpeRatio: e.sharpeRatio,
    winRate: e.winRate,
    totalTrades: e.totalTrades,
    initialCash: e.initialCash,
    finalValue: e.finalValue,
  };
}

function buildAttributionSummary(attr: AttributionResult): ExportableAttributionSummary {
  return {
    dateFrom: attr.dateRange.from,
    dateTo: attr.dateRange.to,
    totalTrades: attr.totalTrades,
    totalNetPnl: attr.totalNetPnl,
    totalGrossPnl: attr.totalGrossPnl,
    totalCommission: attr.totalCommission,
    overallWinRate: attr.overallWinRate,
    avgWin: attr.avgWin,
    avgLoss: attr.avgLoss,
    profitFactor: attr.profitFactor,
  };
}

// ── Filter Helpers ──────────────────────────────────────────────

function filterOrders(orders: Order[], opts: ExportQueryOptions): Order[] {
  let result = orders;
  if (opts.from || opts.to) {
    result = result.filter((o) => dateInRange(o.createdAt, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((o) => set.has(o.symbol));
  }
  if (opts.strategyIds?.length) {
    const set = new Set(opts.strategyIds);
    result = result.filter((o) => o.strategyId && set.has(o.strategyId));
  }
  return result;
}

function filterTrades(trades: TradeRecord[], opts: ExportQueryOptions): TradeRecord[] {
  let result = trades;
  if (opts.from || opts.to) {
    result = result.filter((t) => dateInRange(t.entryTime, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((t) => set.has(t.symbol));
  }
  if (opts.strategyIds?.length) {
    const set = new Set(opts.strategyIds);
    result = result.filter((t) => t.strategyId && set.has(t.strategyId));
  }
  return result;
}

function filterPositions(positions: ExportablePosition[], opts: ExportQueryOptions): ExportablePosition[] {
  if (!opts.symbols?.length) return positions;
  const set = new Set(opts.symbols);
  return positions.filter((p) => set.has(p.symbol));
}

function filterEquityHistory(
  snapshots: EquitySnapshot[],
  opts: ExportQueryOptions,
): EquitySnapshot[] {
  if (!opts.from && !opts.to) return snapshots;
  return snapshots.filter((s) => dateInRange(s.date, opts.from, opts.to));
}

function filterTradeNotes(notes: TradeNote[], opts: ExportQueryOptions): TradeNote[] {
  let result = notes;
  if (opts.from || opts.to) {
    result = result.filter((n) => dateInRange(n.date, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((n) => set.has(n.symbol));
  }
  return result;
}

function filterSignalRecords(
  records: SignalTrackRecord[],
  opts: ExportQueryOptions,
): SignalTrackRecord[] {
  let result = records;
  if (opts.from || opts.to) {
    result = result.filter((r) =>
      dateInRange(new Date(r.signalTimestamp).toISOString(), opts.from, opts.to),
    );
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((r) => set.has(r.symbol));
  }
  if (opts.strategyIds?.length) {
    const set = new Set(opts.strategyIds);
    result = result.filter((r) => set.has(r.strategyId));
  }
  return result;
}

function filterConfirmations(
  confirmations: PendingConfirmation[],
  opts: ExportQueryOptions,
): PendingConfirmation[] {
  let result = confirmations;
  if (opts.from || opts.to) {
    result = result.filter((c) => dateInRange(c.expiresAt, opts.from, opts.to));
  }
  return result;
}

function filterPriceAlerts(
  alerts: PriceAlertCondition[],
  opts: ExportQueryOptions,
): PriceAlertCondition[] {
  let result = alerts;
  if (opts.from || opts.to) {
    result = result.filter((a) => dateInRange(a.createdAt, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((a) => set.has(a.symbol));
  }
  return result;
}

function filterConditionalOrders(
  orders: ConditionalOrder[],
  opts: ExportQueryOptions,
): ConditionalOrder[] {
  let result = orders;
  if (opts.from || opts.to) {
    result = result.filter((o) => dateInRange(o.createdAt, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((o) => set.has(o.symbol));
  }
  if (opts.strategyIds?.length) {
    const set = new Set(opts.strategyIds);
    result = result.filter((o) => o.strategyId && set.has(o.strategyId));
  }
  return result;
}

function filterDcaPlans(
  plans: DcaPlan[],
  opts: ExportQueryOptions,
): DcaPlan[] {
  let result = plans;
  if (opts.from || opts.to) {
    result = result.filter((p) => dateInRange(p.createdAt, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((p) => set.has(p.symbol));
  }
  return result;
}

function filterBreakoutSetups(
  setups: BreakoutSetup[],
  opts: ExportQueryOptions,
): BreakoutSetup[] {
  let result = setups;
  if (opts.from || opts.to) {
    result = result.filter((s) => dateInRange(s.createdAt, opts.from, opts.to));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((s) => set.has(s.symbol));
  }
  return result;
}

function filterStrategyHealth(
  records: StrategyHealthRecord[],
  opts: ExportQueryOptions,
): StrategyHealthRecord[] {
  if (!opts.strategyIds?.length) return records;
  const set = new Set(opts.strategyIds);
  return records.filter((r) => set.has(r.strategyId));
}

function filterAlerts(
  alerts: Alert[],
  opts: ExportQueryOptions,
): Alert[] {
  if (!opts.from && !opts.to) return alerts;
  return alerts.filter((a) => dateInRange(a.timestamp, opts.from, opts.to));
}

function filterBacktestEntries(
  entries: BacktestIndexEntry[],
  opts: ExportQueryOptions,
): BacktestIndexEntry[] {
  let result = entries;
  if (opts.from || opts.to) {
    result = result.filter((e) => dateInRange(e.createdAt, opts.from, opts.to));
  }
  if (opts.strategyIds?.length) {
    const set = new Set(opts.strategyIds);
    result = result.filter((e) => set.has(e.strategyId));
  }
  if (opts.symbols?.length) {
    const set = new Set(opts.symbols);
    result = result.filter((e) => e.symbols.some((s) => set.has(s)));
  }
  return result;
}

// ── Main Export Query Function ──────────────────────────────────

/**
 * Query and transform all trading data into a unified exportable dataset.
 *
 * This is the single entry point for the data export pipeline. It:
 *   1. Queries raw data from the multi-account management framework
 *   2. Queries performance data from the reporting module
 *   3. Applies date range, symbol, and strategy filters
 *   4. Transforms everything into flat, tabular-friendly structures
 *
 * @param cfg Plugin config (for data directory resolution)
 * @param opts Query options (mode, date range, filters, sections)
 * @returns Complete exportable dataset
 */
export async function queryExportDataset(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportDataset> {
  const mode = opts.mode ?? "paper";
  const sections = new Set(opts.sections ?? ALL_SECTIONS);

  const dataset: ExportDataset = {
    meta: {
      exportedAt: new Date().toISOString(),
      mode,
      dateRange: opts.from || opts.to ? { from: opts.from ?? "", to: opts.to ?? "" } : undefined,
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
  };

  // Parallel data queries — only fetch what's needed
  const promises: Promise<void>[] = [];

  // ── Orders ──
  if (sections.has("orders")) {
    promises.push(
      queryOrders(cfg, mode).then((raw) => {
        const filtered = filterOrders(raw, opts);
        dataset.orders = filtered.map(transformOrder);
      }),
    );
  }

  // ── Trades (also needed for attribution) ──
  let filteredTrades: TradeRecord[] = [];
  const needTrades = sections.has("trades") || sections.has("attribution");
  if (needTrades) {
    promises.push(
      queryTrades(cfg, mode).then((raw) => {
        filteredTrades = filterTrades(raw, opts);
        if (sections.has("trades")) {
          dataset.trades = filteredTrades.map(transformTrade);
        }
      }),
    );
  }

  // ── Positions ──
  if (sections.has("positions")) {
    if (mode === "paper") {
      promises.push(
        queryPaperPortfolio(cfg).then((pf) => {
          if (pf?.positions) {
            const exported = pf.positions.map(transformPaperPosition);
            dataset.positions = filterPositions(exported, opts);
          }
        }),
      );
    } else {
      promises.push(
        queryLivePositions(cfg).then((raw) => {
          const exported = raw.map(transformLivePosition);
          dataset.positions = filterPositions(exported, opts);
        }),
      );
    }
  }

  // ── Equity History ──
  if (sections.has("equity")) {
    promises.push(
      queryEquityHistory(cfg, mode).then((raw) => {
        const filtered = filterEquityHistory(raw, opts);
        dataset.equityHistory = filtered.map(transformEquitySnapshot);
      }),
    );
  }

  // ── Trade Notes ──
  if (sections.has("notes")) {
    promises.push(
      queryTradeNotes(cfg).then((raw) => {
        const filtered = filterTradeNotes(raw, opts);
        dataset.tradeNotes = filtered.map(transformTradeNote);
      }),
    );
  }

  // ── Signal Hit-Rate Records ──
  if (sections.has("signals")) {
    promises.push(
      querySignalRecords(cfg).then((raw) => {
        const filtered = filterSignalRecords(raw, opts);
        dataset.signalRecords = filtered.map(transformSignalRecord);
      }),
    );
  }

  // ── Confirmation History ──
  if (sections.has("confirmations")) {
    promises.push(
      queryConfirmations(cfg).then((raw) => {
        const filtered = filterConfirmations(raw, opts);
        dataset.confirmations = filtered.map(transformConfirmation);
      }),
    );
  }

  // ── Account Summary ──
  if (sections.has("account")) {
    promises.push(
      buildAccountSummary(cfg, mode).then((summary) => {
        dataset.accountSummary = summary;
      }),
    );
  }

  // ── Price Alerts ──
  if (sections.has("priceAlerts")) {
    promises.push(
      queryPriceAlerts(cfg).then((raw) => {
        const filtered = filterPriceAlerts(raw, opts);
        dataset.priceAlerts = filtered.map(transformPriceAlert);
      }),
    );
  }

  // ── Conditional Orders ──
  if (sections.has("conditionalOrders")) {
    promises.push(
      queryConditionalOrders(cfg).then((raw) => {
        const filtered = filterConditionalOrders(raw, opts);
        dataset.conditionalOrders = filtered.map(transformConditionalOrder);
      }),
    );
  }

  // ── DCA Plans ──
  if (sections.has("dcaPlans")) {
    promises.push(
      queryDcaPlans(cfg).then((raw) => {
        const filtered = filterDcaPlans(raw, opts);
        dataset.dcaPlans = filtered.map(transformDcaPlan);
      }),
    );
  }

  // ── Breakout Setups ──
  if (sections.has("breakoutSetups")) {
    promises.push(
      queryBreakoutSetups(cfg).then((raw) => {
        const filtered = filterBreakoutSetups(raw, opts);
        dataset.breakoutSetups = filtered.map(transformBreakoutSetup);
      }),
    );
  }

  // ── Strategy Health ──
  if (sections.has("strategyHealth")) {
    promises.push(
      queryStrategyHealthRecords(cfg).then((raw) => {
        const filtered = filterStrategyHealth(raw, opts);
        dataset.strategyHealth = filtered.map(transformStrategyHealth);
      }),
    );
  }

  // ── Alerts ──
  if (sections.has("alerts")) {
    promises.push(
      queryAlerts(cfg).then((raw) => {
        const filtered = filterAlerts(raw, opts);
        dataset.alerts = filtered.map(transformAlert);
      }),
    );
  }

  // ── Backtest History ──
  if (sections.has("backtestHistory")) {
    promises.push(
      queryBacktestIndex(cfg).then((raw) => {
        const filtered = filterBacktestEntries(raw, opts);
        dataset.backtestHistory = filtered.map(transformBacktestEntry);
      }),
    );
  }

  // Wait for all parallel queries
  await Promise.all(promises);

  // ── Attribution (depends on trades being loaded) ──
  if (sections.has("attribution") && filteredTrades.length > 0) {
    const attr = computeAttribution(filteredTrades, {
      from: opts.from,
      to: opts.to,
    });
    dataset.attributionSummary = buildAttributionSummary(attr);
    dataset.strategyAttribution = attr.byStrategy.map(transformStrategyAttribution);
    dataset.symbolAttribution = attr.bySymbol.map(transformSymbolAttribution);
    dataset.periodPnl = [
      ...attr.byDay.map((p) => transformPeriodPnl(p, "day")),
      ...attr.byWeek.map((p) => transformPeriodPnl(p, "week")),
      ...attr.byMonth.map((p) => transformPeriodPnl(p, "month")),
    ];
  }

  return dataset;
}

/**
 * Build an account summary by combining portfolio data with equity history.
 */
async function buildAccountSummary(
  cfg: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<ExportableAccountSummary | null> {
  const equitySnapshots = await queryEquityHistory(cfg, mode);
  if (equitySnapshots.length === 0) {
    // Try to build from portfolio data for paper mode
    if (mode === "paper") {
      const pf = await queryPaperPortfolio(cfg);
      if (!pf) return null;
      return {
        mode,
        accountId: pf.id,
        totalAssets: pf.totalValue,
        cash: pf.cash,
        marketValue: pf.totalValue - pf.cash,
        positionCount: pf.positions.length,
        totalReturn: null,
        totalReturnPct: null,
        maxDrawdown: null,
        maxDrawdownPct: null,
        winRate: null,
        snapshotDate: pf.updatedAt?.slice(0, 10) ?? new Date().toISOString().slice(0, 10),
      };
    }
    return null;
  }

  // Compute summary from equity history
  const sorted = [...equitySnapshots].sort((a, b) => a.date.localeCompare(b.date));
  const first = sorted[0]!;
  const last = sorted[sorted.length - 1]!;
  const totalReturn = last.equity - first.equity;
  const totalReturnPct = first.equity > 0 ? (totalReturn / first.equity) * 100 : 0;

  // Max drawdown
  let peak = -Infinity;
  let maxDrawdown = 0;
  let maxDrawdownPct = 0;
  for (const s of sorted) {
    if (s.equity > peak) peak = s.equity;
    const dd = peak - s.equity;
    if (dd > maxDrawdown) {
      maxDrawdown = dd;
      maxDrawdownPct = peak > 0 ? (dd / peak) * 100 : 0;
    }
  }

  // Win rate from daily PnL
  let winDays = 0;
  let lossDays = 0;
  for (const s of sorted) {
    if (s.dailyPnl > 0) winDays++;
    else if (s.dailyPnl < 0) lossDays++;
  }
  const winRate = winDays + lossDays > 0 ? (winDays / (winDays + lossDays)) * 100 : null;

  return {
    mode,
    accountId: mode === "paper" ? "paper" : "live",
    totalAssets: last.equity,
    cash: last.cash,
    marketValue: last.marketValue,
    positionCount: last.positionCount,
    totalReturn,
    totalReturnPct,
    maxDrawdown,
    maxDrawdownPct,
    winRate,
    snapshotDate: last.date,
  };
}

// ── Convenience: Query single section ───────────────────────────

/** Query only orders in exportable form. */
export async function queryExportableOrders(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableOrder[]> {
  const mode = opts.mode ?? "paper";
  const raw = await queryOrders(cfg, mode);
  return filterOrders(raw, opts).map(transformOrder);
}

/** Query only trades in exportable form. */
export async function queryExportableTrades(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableTrade[]> {
  const mode = opts.mode ?? "paper";
  const raw = await queryTrades(cfg, mode);
  return filterTrades(raw, opts).map(transformTrade);
}

/** Query only positions in exportable form. */
export async function queryExportablePositions(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportablePosition[]> {
  const mode = opts.mode ?? "paper";
  let positions: ExportablePosition[];
  if (mode === "paper") {
    const pf = await queryPaperPortfolio(cfg);
    positions = pf?.positions.map(transformPaperPosition) ?? [];
  } else {
    const raw = await queryLivePositions(cfg);
    positions = raw.map(transformLivePosition);
  }
  return filterPositions(positions, opts);
}

/** Query only equity history in exportable form. */
export async function queryExportableEquityHistory(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableEquitySnapshot[]> {
  const mode = opts.mode ?? "paper";
  const raw = await queryEquityHistory(cfg, mode);
  return filterEquityHistory(raw, opts).map(transformEquitySnapshot);
}

/** Query performance attribution in exportable form. */
export async function queryExportableAttribution(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<{
  summary: ExportableAttributionSummary | null;
  byStrategy: ExportableStrategyAttribution[];
  bySymbol: ExportableSymbolAttribution[];
  periodPnl: ExportablePeriodPnl[];
}> {
  const mode = opts.mode ?? "paper";
  const raw = await queryTrades(cfg, mode);
  const filtered = filterTrades(raw, opts);
  if (filtered.length === 0) {
    return { summary: null, byStrategy: [], bySymbol: [], periodPnl: [] };
  }
  const attr = computeAttribution(filtered, { from: opts.from, to: opts.to });
  return {
    summary: buildAttributionSummary(attr),
    byStrategy: attr.byStrategy.map(transformStrategyAttribution),
    bySymbol: attr.bySymbol.map(transformSymbolAttribution),
    periodPnl: [
      ...attr.byDay.map((p) => transformPeriodPnl(p, "day")),
      ...attr.byWeek.map((p) => transformPeriodPnl(p, "week")),
      ...attr.byMonth.map((p) => transformPeriodPnl(p, "month")),
    ],
  };
}

/** Query signal hit-rate records in exportable form. */
export async function queryExportableSignalRecords(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableSignalRecord[]> {
  const raw = await querySignalRecords(cfg);
  return filterSignalRecords(raw, opts).map(transformSignalRecord);
}

/** Query confirmation history in exportable form. */
export async function queryExportableConfirmations(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableConfirmation[]> {
  const raw = await queryConfirmations(cfg);
  return filterConfirmations(raw, opts).map(transformConfirmation);
}

/** Query price alerts in exportable form. */
export async function queryExportablePriceAlerts(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportablePriceAlert[]> {
  const raw = await queryPriceAlerts(cfg);
  return filterPriceAlerts(raw, opts).map(transformPriceAlert);
}

/** Query conditional orders in exportable form. */
export async function queryExportableConditionalOrders(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableConditionalOrder[]> {
  const raw = await queryConditionalOrders(cfg);
  return filterConditionalOrders(raw, opts).map(transformConditionalOrder);
}

/** Query DCA plans in exportable form. */
export async function queryExportableDcaPlans(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableDcaPlan[]> {
  const raw = await queryDcaPlans(cfg);
  return filterDcaPlans(raw, opts).map(transformDcaPlan);
}

/** Query breakout setups in exportable form. */
export async function queryExportableBreakoutSetups(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableBreakoutSetup[]> {
  const raw = await queryBreakoutSetups(cfg);
  return filterBreakoutSetups(raw, opts).map(transformBreakoutSetup);
}

/** Query strategy health in exportable form. */
export async function queryExportableStrategyHealth(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableStrategyHealth[]> {
  const raw = await queryStrategyHealthRecords(cfg);
  return filterStrategyHealth(raw, opts).map(transformStrategyHealth);
}

/** Query alerts in exportable form. */
export async function queryExportableAlerts(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableAlert[]> {
  const raw = await queryAlerts(cfg);
  return filterAlerts(raw, opts).map(transformAlert);
}

/** Query backtest history in exportable form. */
export async function queryExportableBacktestHistory(
  cfg: QuantTradingConfig,
  opts: ExportQueryOptions = {},
): Promise<ExportableBacktestEntry[]> {
  const raw = await queryBacktestIndex(cfg);
  return filterBacktestEntries(raw, opts).map(transformBacktestEntry);
}

// ── Column definitions for tabular export ───────────────────────

/** Column metadata for building CSV/Excel headers from exportable types. */
export interface ExportColumnDef {
  key: string;
  label: string;
  labelCn: string;
}

export const ORDER_COLUMNS: ExportColumnDef[] = [
  { key: "orderId", label: "Order ID", labelCn: "订单号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "market", label: "Market", labelCn: "市场" },
  { key: "side", label: "Side", labelCn: "方向" },
  { key: "orderType", label: "Order Type", labelCn: "订单类型" },
  { key: "price", label: "Price", labelCn: "价格" },
  { key: "quantity", label: "Quantity", labelCn: "数量" },
  { key: "filledQty", label: "Filled Qty", labelCn: "成交数量" },
  { key: "avgFillPrice", label: "Avg Fill Price", labelCn: "成交均价" },
  { key: "status", label: "Status", labelCn: "状态" },
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
  { key: "brokerType", label: "Broker", labelCn: "券商" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
  { key: "filledAt", label: "Filled At", labelCn: "成交时间" },
];

export const TRADE_COLUMNS: ExportColumnDef[] = [
  { key: "tradeId", label: "Trade ID", labelCn: "交易号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "name", label: "Name", labelCn: "名称" },
  { key: "market", label: "Market", labelCn: "市场" },
  { key: "side", label: "Side", labelCn: "方向" },
  { key: "entryTime", label: "Entry Time", labelCn: "开仓时间" },
  { key: "exitTime", label: "Exit Time", labelCn: "平仓时间" },
  { key: "entryPrice", label: "Entry Price", labelCn: "开仓价" },
  { key: "exitPrice", label: "Exit Price", labelCn: "平仓价" },
  { key: "quantity", label: "Quantity", labelCn: "数量" },
  { key: "grossPnl", label: "Gross P&L", labelCn: "毛利润" },
  { key: "commission", label: "Commission", labelCn: "手续费" },
  { key: "netPnl", label: "Net P&L", labelCn: "净利润" },
  { key: "holdingDays", label: "Holding Days", labelCn: "持仓天数" },
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
];

export const POSITION_COLUMNS: ExportColumnDef[] = [
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "name", label: "Name", labelCn: "名称" },
  { key: "market", label: "Market", labelCn: "市场" },
  { key: "quantity", label: "Quantity", labelCn: "数量" },
  { key: "availableQty", label: "Available Qty", labelCn: "可卖数量" },
  { key: "avgCost", label: "Avg Cost", labelCn: "成本价" },
  { key: "currentPrice", label: "Current Price", labelCn: "现价" },
  { key: "marketValue", label: "Market Value", labelCn: "市值" },
  { key: "unrealizedPnl", label: "Unrealized P&L", labelCn: "浮动盈亏" },
  { key: "unrealizedPnlPct", label: "Unrealized P&L %", labelCn: "浮动盈亏%" },
  { key: "weight", label: "Weight", labelCn: "权重" },
  { key: "source", label: "Source", labelCn: "来源" },
];

export const EQUITY_COLUMNS: ExportColumnDef[] = [
  { key: "date", label: "Date", labelCn: "日期" },
  { key: "equity", label: "Equity", labelCn: "总权益" },
  { key: "cash", label: "Cash", labelCn: "现金" },
  { key: "marketValue", label: "Market Value", labelCn: "市值" },
  { key: "dailyPnl", label: "Daily P&L", labelCn: "当日盈亏" },
  { key: "positionCount", label: "Positions", labelCn: "持仓数" },
  { key: "benchmark", label: "Benchmark", labelCn: "基准" },
];

export const PERIOD_PNL_COLUMNS: ExportColumnDef[] = [
  { key: "period", label: "Period", labelCn: "周期" },
  { key: "granularity", label: "Granularity", labelCn: "粒度" },
  { key: "trades", label: "Trades", labelCn: "交易次数" },
  { key: "netPnl", label: "Net P&L", labelCn: "净盈亏" },
  { key: "cumPnl", label: "Cumulative P&L", labelCn: "累计盈亏" },
  { key: "wins", label: "Wins", labelCn: "盈利次数" },
  { key: "losses", label: "Losses", labelCn: "亏损次数" },
];

export const SIGNAL_RECORD_COLUMNS: ExportColumnDef[] = [
  { key: "signalDate", label: "Signal Date", labelCn: "信号时间" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "direction", label: "Direction", labelCn: "方向" },
  { key: "signalPrice", label: "Signal Price", labelCn: "信号价格" },
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
  { key: "reason", label: "Reason", labelCn: "原因" },
  { key: "confidence", label: "Confidence", labelCn: "置信度" },
  { key: "hitT1", label: "T+1 Result", labelCn: "T+1结果" },
  { key: "hitT3", label: "T+3 Result", labelCn: "T+3结果" },
  { key: "hitT5", label: "T+5 Result", labelCn: "T+5结果" },
  { key: "hitT10", label: "T+10 Result", labelCn: "T+10结果" },
  { key: "priceT1", label: "T+1 Price", labelCn: "T+1价格" },
  { key: "priceT3", label: "T+3 Price", labelCn: "T+3价格" },
  { key: "priceT5", label: "T+5 Price", labelCn: "T+5价格" },
  { key: "priceT10", label: "T+10 Price", labelCn: "T+10价格" },
];

export const CONFIRMATION_COLUMNS: ExportColumnDef[] = [
  { key: "confirmId", label: "Confirm ID", labelCn: "确认号" },
  { key: "orderId", label: "Order ID", labelCn: "订单号" },
  { key: "status", label: "Status", labelCn: "状态" },
  { key: "channel", label: "Channel", labelCn: "渠道" },
  { key: "userId", label: "User ID", labelCn: "用户" },
  { key: "expiresAt", label: "Expires At", labelCn: "过期时间" },
  { key: "respondedAt", label: "Responded At", labelCn: "响应时间" },
  { key: "respondedBy", label: "Responded By", labelCn: "响应人" },
];

export const PRICE_ALERT_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "Alert ID", labelCn: "预警号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "type", label: "Condition Type", labelCn: "条件类型" },
  { key: "threshold", label: "Threshold", labelCn: "阈值" },
  { key: "description", label: "Description", labelCn: "描述" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
  { key: "triggered", label: "Triggered", labelCn: "已触发" },
  { key: "repeating", label: "Repeating", labelCn: "重复" },
  { key: "lastTriggeredAt", label: "Last Triggered", labelCn: "最近触发" },
  { key: "hasOrderAction", label: "Has Order", labelCn: "关联下单" },
  { key: "orderSide", label: "Order Side", labelCn: "下单方向" },
  { key: "orderQuantity", label: "Order Qty", labelCn: "下单数量" },
  { key: "orderSubmitted", label: "Order Submitted", labelCn: "已提交" },
];

export const CONDITIONAL_ORDER_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "ID", labelCn: "编号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "type", label: "Type", labelCn: "类型" },
  { key: "entryPrice", label: "Entry Price", labelCn: "入场价" },
  { key: "triggerValue", label: "Trigger Value", labelCn: "触发值" },
  { key: "quantity", label: "Quantity", labelCn: "数量" },
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
  { key: "description", label: "Description", labelCn: "描述" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
  { key: "triggered", label: "Triggered", labelCn: "已触发" },
  { key: "triggeredAt", label: "Triggered At", labelCn: "触发时间" },
  { key: "triggerResult", label: "Result", labelCn: "结果" },
  { key: "peakPrice", label: "Peak Price", labelCn: "峰值价" },
  { key: "trailingStopPrice", label: "Trailing Stop", labelCn: "移动止损价" },
  { key: "tiersCount", label: "Tiers", labelCn: "档位数" },
  { key: "tiersTriggered", label: "Tiers Triggered", labelCn: "已触发档位" },
];

export const DCA_PLAN_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "Plan ID", labelCn: "计划号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "amount", label: "Amount", labelCn: "金额" },
  { key: "frequency", label: "Frequency", labelCn: "频率" },
  { key: "dayOfPeriod", label: "Day", labelCn: "执行日" },
  { key: "hour", label: "Hour", labelCn: "执行时" },
  { key: "status", label: "Status", labelCn: "状态" },
  { key: "executionCount", label: "Executions", labelCn: "执行次数" },
  { key: "totalInvested", label: "Total Invested", labelCn: "累计投入" },
  { key: "lastExecutedAt", label: "Last Executed", labelCn: "上次执行" },
  { key: "nextExecutionAt", label: "Next Execution", labelCn: "下次执行" },
  { key: "maxExecutions", label: "Max Executions", labelCn: "最大次数" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
];

export const BREAKOUT_SETUP_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "Setup ID", labelCn: "设置号" },
  { key: "symbol", label: "Symbol", labelCn: "代码" },
  { key: "triggerPrice", label: "Trigger Price", labelCn: "触发价" },
  { key: "status", label: "Status", labelCn: "状态" },
  { key: "bufferPct", label: "Buffer %", labelCn: "缓冲%" },
  { key: "holdChecks", label: "Hold Checks", labelCn: "确认次数" },
  { key: "currentHoldCount", label: "Current Count", labelCn: "当前计数" },
  { key: "orderType", label: "Order Type", labelCn: "订单类型" },
  { key: "quantity", label: "Quantity", labelCn: "数量" },
  { key: "stopLossPrice", label: "Stop Loss", labelCn: "止损价" },
  { key: "enablePullback", label: "Pullback", labelCn: "回踩" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
  { key: "triggeredAt", label: "Triggered At", labelCn: "触发时间" },
  { key: "orderResult", label: "Order Result", labelCn: "下单结果" },
];

export const STRATEGY_HEALTH_COLUMNS: ExportColumnDef[] = [
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
  { key: "totalTrades", label: "Total Trades", labelCn: "总交易数" },
  { key: "recentTradeCount", label: "Recent Trades", labelCn: "近期交易" },
  { key: "recentWins", label: "Recent Wins", labelCn: "近期盈利" },
  { key: "recentLosses", label: "Recent Losses", labelCn: "近期亏损" },
  { key: "recentWinRate", label: "Recent Win Rate", labelCn: "近期胜率" },
  { key: "recentNetPnl", label: "Recent Net P&L", labelCn: "近期净盈亏" },
  { key: "lastSignalTime", label: "Last Signal", labelCn: "最近信号" },
  { key: "lastTradeTime", label: "Last Trade", labelCn: "最近交易" },
];

export const ALERT_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "Alert ID", labelCn: "告警号" },
  { key: "timestamp", label: "Timestamp", labelCn: "时间" },
  { key: "severity", label: "Severity", labelCn: "级别" },
  { key: "source", label: "Source", labelCn: "来源" },
  { key: "message", label: "Message", labelCn: "内容" },
  { key: "acknowledged", label: "Acknowledged", labelCn: "已确认" },
];

export const BACKTEST_ENTRY_COLUMNS: ExportColumnDef[] = [
  { key: "id", label: "Backtest ID", labelCn: "回测号" },
  { key: "createdAt", label: "Created At", labelCn: "创建时间" },
  { key: "strategyId", label: "Strategy", labelCn: "策略" },
  { key: "symbols", label: "Symbols", labelCn: "标的" },
  { key: "startDate", label: "Start Date", labelCn: "开始日期" },
  { key: "endDate", label: "End Date", labelCn: "结束日期" },
  { key: "engine", label: "Engine", labelCn: "引擎" },
  { key: "tags", label: "Tags", labelCn: "标签" },
  { key: "starred", label: "Starred", labelCn: "收藏" },
  { key: "totalReturn", label: "Total Return %", labelCn: "总收益率%" },
  { key: "annualizedReturn", label: "Annual Return %", labelCn: "年化收益%" },
  { key: "maxDrawdown", label: "Max Drawdown %", labelCn: "最大回撤%" },
  { key: "sharpeRatio", label: "Sharpe Ratio", labelCn: "夏普比率" },
  { key: "winRate", label: "Win Rate", labelCn: "胜率" },
  { key: "totalTrades", label: "Total Trades", labelCn: "总交易数" },
  { key: "initialCash", label: "Initial Cash", labelCn: "初始资金" },
  { key: "finalValue", label: "Final Value", labelCn: "最终价值" },
];
