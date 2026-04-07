/**
 * DataExport — core data query & transformation layer.
 *
 * Extracts data from the multi-account management framework and
 * performance reporting module, then transforms it into a unified
 * intermediate structure suitable for export to CSV, XLSX, JSON, etc.
 *
 * Data sources:
 *   - Portfolio / positions (paper + live)
 *   - Orders (paper + live)
 *   - Trades / TradeRecords (paper + live)
 *   - Equity history snapshots
 *   - Performance attribution
 *   - Trade journal notes
 *   - Signals
 *   - Backtest results (from BacktestStore)
 *   - Conditional orders (stop-loss, take-profit, trailing, etc.)
 *   - Favorites / watchlist
 *
 * The intermediate structures are format-agnostic: each export target
 * (CSV, XLSX, PDF, …) consumes these same structures.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type {
  Order,
  TradeRecord,
  LivePosition,
  BrokerAccount,
  BacktestResult,
  QuantTradingConfig,
} from "../types.js";
import { EquityHistory, type EquitySnapshot } from "./equity-history.js";
import {
  computeAttribution,
  type AttributionResult,
  type StrategyAttribution,
  type SymbolAttribution,
  type PeriodPnl,
} from "./performance-attribution.js";
import type { TradeNote } from "./trade-journal.js";
import type { ConditionalOrder } from "./conditional-order.js";
import type { FavoriteStock } from "./favorites-store.js";

// ── Unified Exportable Structures ───────────────────────────────

/** A single flat row for portfolio position export. */
export interface ExportablePosition {
  symbol: string;
  name: string;
  market: string;
  quantity: number;
  availableQty: number;
  frozenQty: number;
  avgCost: number;
  currentPrice: number;
  marketValue: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  /** Weight as percentage of total portfolio */
  weightPct: number;
  /** Data source: "live" or "paper" */
  source: string;
}

/** A single flat row for order export. */
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
  createdAt: string;
  updatedAt: string;
  submittedAt: string;
  filledAt: string;
  errorMsg: string;
}

/** A single flat row for trade record export. */
export interface ExportableTrade {
  tradeId: string;
  symbol: string;
  name: string;
  market: string;
  side: string;
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  grossPnl: number;
  commission: number;
  netPnl: number;
  holdingDays: number;
  strategyId: string;
}

/** A single flat row for equity snapshot export. */
export interface ExportableEquitySnapshot {
  date: string;
  equity: number;
  cash: number;
  marketValue: number;
  dailyPnl: number;
  positionCount: number;
  benchmark: number | null;
}

/** A single flat row for signal export. */
export interface ExportableSignal {
  signalId: string;
  strategyId: string;
  symbol: string;
  side: string;
  strength: number;
  price: number;
  reason: string;
  createdAt: string;
  executed: boolean;
}

/** A single flat row for trade journal note export. */
export interface ExportableTradeNote {
  noteId: string;
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
}

/** A single flat row for conditional order export. */
export interface ExportableConditionalOrder {
  id: string;
  symbol: string;
  type: string;
  entryPrice: number;
  triggerValue: number;
  quantity: number;
  strategyId: string;
  description: string;
  triggered: boolean;
  createdAt: string;
  triggeredAt: string;
  /** Trailing stop: highest observed price */
  peakPrice: number | null;
  /** Computed trailing stop price */
  trailingStopPrice: number | null;
}

/** A single flat row for favorite/watchlist stock export. */
export interface ExportableFavorite {
  code: string;
  name: string;
  category: string;
  priority: number;
  notes: string;
  tags: string;
  priceTarget: number | null;
  convictionScore: number | null;
  addedAt: string;
  updatedAt: string;
}

/** A single flat row for backtest result export. */
export interface ExportableBacktestResult {
  runId: string;
  strategyId: string;
  symbols: string;
  startDate: string;
  endDate: string;
  initialCash: number;
  finalValue: number;
  totalReturn: number;
  annualizedReturn: number;
  maxDrawdown: number;
  sharpeRatio: number;
  sortinoRatio: number | null;
  calmarRatio: number | null;
  winRate: number;
  profitFactor: number | null;
  totalTrades: number;
  totalFees: number | null;
  createdAt: string;
  tags: string;
  notes: string;
  starred: boolean;
}

/** Performance summary — flat structure for export. */
export interface ExportablePerformanceSummary {
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
  /** Equity curve metrics */
  totalDays: number;
  startEquity: number;
  endEquity: number;
  totalReturn: number;
  totalReturnPct: number;
  maxDrawdown: number;
  maxDrawdownPct: number;
  avgDailyPnl: number;
  winDays: number;
  lossDays: number;
  dayWinRate: number;
  /** Risk-adjusted metrics */
  sharpeRatio: number | null;
  sortinoRatio: number | null;
  calmarRatio: number | null;
}

/** Account overview — flat structure for export. */
export interface ExportableAccountOverview {
  accountId: string;
  brokerType: string;
  cash: number;
  frozenCash: number;
  totalAssets: number;
  marketValue: number;
  profit: number;
  profitPct: number;
  todayProfit: number;
  positionCount: number;
  lastSyncAt: string;
}

/** Complete export dataset — all data a user may want to export. */
export interface ExportDataset {
  exportedAt: string;
  mode: "paper" | "live" | "all";
  account: ExportableAccountOverview | null;
  /** When mode="all", per-account breakdown (paper + live). */
  accounts: ExportableAccountOverview[];
  positions: ExportablePosition[];
  orders: ExportableOrder[];
  trades: ExportableTrade[];
  equityHistory: ExportableEquitySnapshot[];
  signals: ExportableSignal[];
  tradeNotes: ExportableTradeNote[];
  conditionalOrders: ExportableConditionalOrder[];
  favorites: ExportableFavorite[];
  backtestResults: ExportableBacktestResult[];
  performanceSummary: ExportablePerformanceSummary | null;
  attributionByStrategy: StrategyAttribution[];
  attributionBySymbol: SymbolAttribution[];
  pnlByDay: PeriodPnl[];
  pnlByWeek: PeriodPnl[];
  pnlByMonth: PeriodPnl[];
}

// ── Data Query Functions ────────────────────────────────────────

function dataDir(config: QuantTradingConfig): string {
  return config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

/** Load orders from disk for a given mode. */
export async function queryOrders(
  config: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<Order[]> {
  const filePath = path.join(dataDir(config), `orders-${mode}.json`);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as Order[];
  } catch {
    return [];
  }
}

/** Load trade records from disk for a given mode. */
export async function queryTrades(
  config: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<TradeRecord[]> {
  const filePath = path.join(dataDir(config), `trades-${mode}.json`);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as TradeRecord[];
  } catch {
    return [];
  }
}

/** Load equity history snapshots for a given mode. */
export async function queryEquityHistory(
  config: QuantTradingConfig,
  mode: "paper" | "live",
  dateRange?: { from?: string; to?: string },
): Promise<EquitySnapshot[]> {
  const eh = new EquityHistory({ dataDir: dataDir(config), mode });
  await eh.load();
  if (dateRange?.from && dateRange?.to) {
    return eh.getRange(dateRange.from, dateRange.to);
  }
  return eh.getAll();
}

/** Load signals from disk. */
export async function querySignals(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>[]> {
  const filePath = path.join(dataDir(config), "signals.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as Record<string, unknown>[];
  } catch {
    return [];
  }
}

/** Load trade journal notes from disk. */
export async function queryTradeNotes(
  config: QuantTradingConfig,
): Promise<TradeNote[]> {
  const filePath = path.join(dataDir(config), "trade-journal.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const data = JSON.parse(raw) as { notes?: TradeNote[] };
    return data.notes ?? [];
  } catch {
    return [];
  }
}

/** Load paper portfolio from disk. */
export async function queryPaperPortfolio(
  config: QuantTradingConfig,
): Promise<{ cash: number; totalValue: number; positions: Array<Record<string, unknown>> } | null> {
  for (const filename of ["portfolio-paper.json", "portfolio.json"]) {
    try {
      const raw = await fs.readFile(path.join(dataDir(config), filename), "utf-8");
      return JSON.parse(raw) as {
        cash: number;
        totalValue: number;
        positions: Array<Record<string, unknown>>;
      };
    } catch {
      continue;
    }
  }
  return null;
}

/** Load conditional orders from disk. */
export async function queryConditionalOrders(
  config: QuantTradingConfig,
): Promise<ConditionalOrder[]> {
  const filePath = path.join(dataDir(config), "conditional-orders.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as ConditionalOrder[];
  } catch {
    return [];
  }
}

/** Load favorites/watchlist from disk. */
export async function queryFavorites(
  config: QuantTradingConfig,
): Promise<FavoriteStock[]> {
  const filePath = path.join(dataDir(config), "favorites.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as FavoriteStock[];
  } catch {
    return [];
  }
}

/** Load backtest result index entries from disk. */
export async function queryBacktestResults(
  config: QuantTradingConfig,
): Promise<Array<{
  id: string;
  createdAt: string;
  tags: string[];
  notes?: string;
  starred: boolean;
  result: BacktestResult;
}>> {
  const btDir = path.join(dataDir(config), "backtest-results");
  try {
    const files = await fs.readdir(btDir);
    const jsonFiles = files.filter((f: string) => f.endsWith(".json") && f !== "index.json");
    const results: Array<{
      id: string;
      createdAt: string;
      tags: string[];
      notes?: string;
      starred: boolean;
      result: BacktestResult;
    }> = [];
    for (const file of jsonFiles) {
      try {
        const raw = await fs.readFile(path.join(btDir, file), "utf-8");
        const record = JSON.parse(raw) as {
          id: string;
          createdAt: string;
          tags: string[];
          notes?: string;
          starred: boolean;
          result: BacktestResult;
        };
        if (record.result) {
          results.push(record);
        }
      } catch {
        continue;
      }
    }
    return results;
  } catch {
    return [];
  }
}

// ── Data Transformation Functions ───────────────────────────────

/** Transform live positions to exportable format. */
export function transformPositions(
  positions: LivePosition[],
  totalAssets: number,
  source: string,
): ExportablePosition[] {
  return positions.map((p) => ({
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    availableQty: p.availableQty,
    frozenQty: p.frozenQty,
    avgCost: p.avgCost,
    currentPrice: p.currentPrice ?? 0,
    marketValue: p.marketValue ?? 0,
    unrealizedPnl: p.unrealizedPnl ?? 0,
    unrealizedPnlPct: p.unrealizedPnlPct ?? 0,
    weightPct: totalAssets > 0 ? ((p.marketValue ?? 0) / totalAssets) * 100 : 0,
    source,
  }));
}

/** Transform paper portfolio positions to exportable format. */
export function transformPaperPositions(
  positions: Array<Record<string, unknown>>,
  totalValue: number,
): ExportablePosition[] {
  return positions.map((p) => {
    const mv = (p.marketValue as number) ?? (p.quantity as number) * ((p.currentPrice as number) ?? (p.avgCost as number) ?? 0);
    return {
      symbol: String(p.symbol ?? ""),
      name: String(p.name ?? ""),
      market: String(p.market ?? "cn_stock"),
      quantity: (p.quantity as number) ?? 0,
      availableQty: (p.quantity as number) ?? 0,
      frozenQty: 0,
      avgCost: (p.avgCost as number) ?? 0,
      currentPrice: (p.currentPrice as number) ?? 0,
      marketValue: mv,
      unrealizedPnl: (p.unrealizedPnl as number) ?? 0,
      unrealizedPnlPct: (p.unrealizedPnlPct as number) ?? 0,
      weightPct: totalValue > 0 ? (mv / totalValue) * 100 : 0,
      source: "paper",
    };
  });
}

/** Transform orders to exportable format. */
export function transformOrders(orders: Order[]): ExportableOrder[] {
  return orders.map((o) => ({
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
    createdAt: o.createdAt,
    updatedAt: o.updatedAt,
    submittedAt: o.submittedAt ?? "",
    filledAt: o.filledAt ?? "",
    errorMsg: o.errorMsg ?? "",
  }));
}

/** Transform trade records to exportable format. */
export function transformTrades(trades: TradeRecord[]): ExportableTrade[] {
  return trades.map((t) => ({
    tradeId: t.tradeId,
    symbol: t.symbol,
    name: t.name ?? "",
    market: t.market,
    side: t.side,
    entryTime: t.entryTime,
    exitTime: t.exitTime ?? "",
    entryPrice: t.entryPrice,
    exitPrice: t.exitPrice ?? 0,
    quantity: t.quantity,
    grossPnl: t.grossPnl,
    commission: t.commission,
    netPnl: t.netPnl,
    holdingDays: t.holdingDays ?? 0,
    strategyId: t.strategyId ?? "",
  }));
}

/** Transform equity snapshots to exportable format. */
export function transformEquityHistory(
  snapshots: EquitySnapshot[],
): ExportableEquitySnapshot[] {
  return snapshots.map((s) => ({
    date: s.date,
    equity: s.equity,
    cash: s.cash,
    marketValue: s.marketValue,
    dailyPnl: s.dailyPnl,
    positionCount: s.positionCount,
    benchmark: s.benchmark ?? null,
  }));
}

/** Transform raw signal records to exportable format. */
export function transformSignals(
  signals: Record<string, unknown>[],
): ExportableSignal[] {
  return signals.map((s) => ({
    signalId: String(s.signalId ?? s.id ?? ""),
    strategyId: String(s.strategyId ?? ""),
    symbol: String(s.symbol ?? ""),
    side: String(s.side ?? s.direction ?? ""),
    strength: (s.strength as number) ?? 0,
    price: (s.price as number) ?? 0,
    reason: String(s.reason ?? s.note ?? ""),
    createdAt: String(s.createdAt ?? s.timestamp ?? ""),
    executed: Boolean(s.executed ?? false),
  }));
}

/** Transform trade journal notes to exportable format. */
export function transformTradeNotes(notes: TradeNote[]): ExportableTradeNote[] {
  return notes.map((n) => ({
    noteId: n.id,
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
  }));
}

/** Transform conditional orders to exportable format. */
export function transformConditionalOrders(
  orders: ConditionalOrder[],
): ExportableConditionalOrder[] {
  return orders.map((o) => ({
    id: o.id,
    symbol: o.symbol,
    type: o.type,
    entryPrice: o.entryPrice,
    triggerValue: o.triggerValue,
    quantity: o.quantity,
    strategyId: o.strategyId ?? "",
    description: o.description,
    triggered: o.triggered,
    createdAt: o.createdAt,
    triggeredAt: o.triggeredAt ?? "",
    peakPrice: o.peakPrice ?? null,
    trailingStopPrice: o.trailingStopPrice ?? null,
  }));
}

/** Transform favorites to exportable format. */
export function transformFavorites(
  favorites: FavoriteStock[],
): ExportableFavorite[] {
  return favorites.map((f) => ({
    code: f.code,
    name: f.name ?? "",
    category: f.category ?? "watchlist",
    priority: f.priority ?? 0,
    notes: f.notes ?? "",
    tags: (f.tags ?? []).join(", "),
    priceTarget: f.priceTarget ?? null,
    convictionScore: f.convictionScore ?? null,
    addedAt: f.addedAt,
    updatedAt: f.updatedAt,
  }));
}

/** Transform backtest records to exportable format. */
export function transformBacktestResults(
  records: Array<{
    id: string;
    createdAt: string;
    tags: string[];
    notes?: string;
    starred: boolean;
    result: BacktestResult;
  }>,
): ExportableBacktestResult[] {
  return records.map((r) => ({
    runId: r.id,
    strategyId: r.result.strategyId,
    symbols: r.result.symbols.join(", "),
    startDate: r.result.startDate,
    endDate: r.result.endDate,
    initialCash: r.result.initialCash,
    finalValue: r.result.finalValue,
    totalReturn: r.result.totalReturn,
    annualizedReturn: r.result.annualizedReturn,
    maxDrawdown: r.result.maxDrawdown,
    sharpeRatio: r.result.sharpeRatio,
    sortinoRatio: r.result.sortinoRatio ?? null,
    calmarRatio: r.result.calmarRatio ?? null,
    winRate: r.result.winRate,
    profitFactor: r.result.profitFactor ?? null,
    totalTrades: r.result.totalTrades,
    totalFees: r.result.totalFees ?? null,
    createdAt: r.createdAt,
    tags: r.tags.join(", "),
    notes: r.notes ?? "",
    starred: r.starred,
  }));
}

/** Build performance summary from attribution + equity history. */
export function buildPerformanceSummary(
  attribution: AttributionResult,
  equitySummary: ReturnType<EquityHistory["getSummary"]>,
  equitySnapshots?: EquitySnapshot[],
): ExportablePerformanceSummary {
  // Compute risk-adjusted metrics from equity history if available
  let sharpeRatio: number | null = null;
  let sortinoRatio: number | null = null;
  let calmarRatio: number | null = null;

  if (equitySnapshots && equitySnapshots.length > 1) {
    const dailyReturns: number[] = [];
    for (let i = 1; i < equitySnapshots.length; i++) {
      const prev = equitySnapshots[i - 1]!.equity;
      const curr = equitySnapshots[i]!.equity;
      if (prev > 0) {
        dailyReturns.push((curr - prev) / prev);
      }
    }

    if (dailyReturns.length > 1) {
      const avgReturn = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
      const variance =
        dailyReturns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) /
        (dailyReturns.length - 1);
      const stdDev = Math.sqrt(variance);

      // Annualized Sharpe (assuming ~242 trading days, risk-free rate = 0)
      if (stdDev > 0) {
        sharpeRatio = (avgReturn / stdDev) * Math.sqrt(242);
      }

      // Sortino: only downside deviation
      const downsideReturns = dailyReturns.filter((r) => r < 0);
      if (downsideReturns.length > 0) {
        const downsideVariance =
          downsideReturns.reduce((s, r) => s + r ** 2, 0) / downsideReturns.length;
        const downsideDev = Math.sqrt(downsideVariance);
        if (downsideDev > 0) {
          sortinoRatio = (avgReturn / downsideDev) * Math.sqrt(242);
        }
      }

      // Calmar: annualized return / max drawdown
      if (equitySummary.maxDrawdownPct > 0) {
        const annualizedReturnPct =
          equitySummary.totalReturnPct * (242 / Math.max(dailyReturns.length, 1));
        calmarRatio = annualizedReturnPct / equitySummary.maxDrawdownPct;
      }
    }
  }

  return {
    dateFrom: attribution.dateRange.from,
    dateTo: attribution.dateRange.to,
    totalTrades: attribution.totalTrades,
    totalNetPnl: attribution.totalNetPnl,
    totalGrossPnl: attribution.totalGrossPnl,
    totalCommission: attribution.totalCommission,
    overallWinRate: attribution.overallWinRate,
    avgWin: attribution.avgWin,
    avgLoss: attribution.avgLoss,
    profitFactor: attribution.profitFactor,
    totalDays: equitySummary.totalDays,
    startEquity: equitySummary.startEquity,
    endEquity: equitySummary.endEquity,
    totalReturn: equitySummary.totalReturn,
    totalReturnPct: equitySummary.totalReturnPct,
    maxDrawdown: equitySummary.maxDrawdown,
    maxDrawdownPct: equitySummary.maxDrawdownPct,
    avgDailyPnl: equitySummary.avgDailyPnl,
    winDays: equitySummary.winDays,
    lossDays: equitySummary.lossDays,
    dayWinRate: equitySummary.winRate,
    sharpeRatio,
    sortinoRatio,
    calmarRatio,
  };
}

/** Build account overview from broker account + positions. */
export function buildAccountOverview(
  account: BrokerAccount,
  positionCount: number,
): ExportableAccountOverview {
  return {
    accountId: account.accountId,
    brokerType: account.brokerType,
    cash: account.cash,
    frozenCash: account.frozenCash,
    totalAssets: account.totalAssets,
    marketValue: account.marketValue,
    profit: account.profit,
    profitPct: account.profitPct,
    todayProfit: account.todayProfit ?? 0,
    positionCount,
    lastSyncAt: account.lastSyncAt,
  };
}

// ── Full Export Dataset Builder ─────────────────────────────────

export interface ExportOptions {
  /** Which mode to export: "paper", "live", or "all" (both merged) */
  mode: "paper" | "live" | "all";
  /** Optional date range filter for trades, equity history, etc. */
  dateRange?: { from?: string; to?: string };
  /** If a live engine is available, extract live account/positions from it */
  liveAccount?: BrokerAccount;
  livePositions?: LivePosition[];
  /** Paper account state for multi-account view */
  paperAccount?: BrokerAccount;
  paperPositions?: LivePosition[];
  /** Whether to include backtest results (may be large) */
  includeBacktests?: boolean;
  /** Whether to include favorites/watchlist */
  includeFavorites?: boolean;
  /** Whether to include conditional orders */
  includeConditionalOrders?: boolean;
}

/**
 * Build a complete export dataset by querying all data sources
 * and transforming into unified exportable structures.
 */
export async function buildExportDataset(
  config: QuantTradingConfig,
  options: ExportOptions,
): Promise<ExportDataset> {
  const {
    mode,
    dateRange,
    liveAccount,
    livePositions,
    paperAccount,
    paperPositions,
    includeBacktests = true,
    includeFavorites = true,
    includeConditionalOrders = true,
  } = options;
  const modes: Array<"paper" | "live"> =
    mode === "all" ? ["paper", "live"] : [mode];

  // ── Query all data sources in parallel ──

  const [
    allOrderArrays,
    allTradeArrays,
    allEquityArrays,
    rawSignals,
    rawNotes,
    paperPortfolio,
    rawConditionalOrders,
    rawFavorites,
    rawBacktests,
  ] = await Promise.all([
    Promise.all(modes.map((m) => queryOrders(config, m))),
    Promise.all(modes.map((m) => queryTrades(config, m))),
    Promise.all(modes.map((m) => queryEquityHistory(config, m, dateRange))),
    querySignals(config),
    queryTradeNotes(config),
    mode !== "live" ? queryPaperPortfolio(config) : Promise.resolve(null),
    includeConditionalOrders ? queryConditionalOrders(config) : Promise.resolve([]),
    includeFavorites ? queryFavorites(config) : Promise.resolve([]),
    includeBacktests ? queryBacktestResults(config) : Promise.resolve([]),
  ]);

  // Merge arrays from both modes
  const allOrders = allOrderArrays.flat();
  const allTrades = allTradeArrays.flat();
  const allEquity = allEquityArrays.flat();

  // ── Filter by date range if specified ──

  let filteredOrders = allOrders;
  let filteredTrades = allTrades;

  if (dateRange?.from) {
    filteredOrders = filteredOrders.filter((o) => o.createdAt >= dateRange.from!);
    filteredTrades = filteredTrades.filter(
      (t) => (t.exitTime ?? t.entryTime) >= dateRange.from!,
    );
  }
  if (dateRange?.to) {
    filteredOrders = filteredOrders.filter((o) => o.createdAt <= dateRange.to!);
    filteredTrades = filteredTrades.filter(
      (t) => (t.exitTime ?? t.entryTime) <= dateRange.to!,
    );
  }

  // ── Build positions & accounts ──

  let positions: ExportablePosition[] = [];
  let account: ExportableAccountOverview | null = null;
  const accounts: ExportableAccountOverview[] = [];

  if (liveAccount && livePositions) {
    const liveOverview = buildAccountOverview(liveAccount, livePositions.length);
    accounts.push(liveOverview);
    positions.push(
      ...transformPositions(livePositions, liveAccount.totalAssets, liveAccount.brokerType),
    );
    if (mode === "live" || !paperAccount) {
      account = liveOverview;
    }
  }

  if (paperAccount && paperPositions) {
    const paperOverview = buildAccountOverview(paperAccount, paperPositions.length);
    accounts.push(paperOverview);
    positions.push(
      ...transformPositions(paperPositions, paperAccount.totalAssets, "paper"),
    );
    if (mode === "paper") {
      account = paperOverview;
    }
  } else if (paperPortfolio && mode !== "live") {
    positions.push(
      ...transformPaperPositions(paperPortfolio.positions, paperPortfolio.totalValue),
    );
  }

  // For "all" mode, create a consolidated overview
  if (mode === "all" && accounts.length > 0) {
    account = buildConsolidatedAccountOverview(accounts);
  } else if (!account && accounts.length > 0) {
    account = accounts[0]!;
  }

  // ── Transform data ──

  const exportableOrders = transformOrders(filteredOrders);
  const exportableTrades = transformTrades(filteredTrades);
  const exportableEquity = transformEquityHistory(allEquity);
  const exportableSignals = transformSignals(rawSignals);
  const exportableNotes = transformTradeNotes(rawNotes);
  const exportableConditionalOrders = transformConditionalOrders(rawConditionalOrders);
  const exportableFavorites = transformFavorites(rawFavorites);
  const exportableBacktests = transformBacktestResults(rawBacktests);

  // ── Performance attribution ──

  const attribution = computeAttribution(allTrades, dateRange);

  // ── Equity summary ──

  const equityHistory = new EquityHistory({ dataDir: dataDir(config), mode: modes[0] });
  await equityHistory.load();
  const equitySummary = equityHistory.getSummary();

  const performanceSummary =
    allTrades.length > 0 || equitySummary.totalDays > 0
      ? buildPerformanceSummary(attribution, equitySummary, equityHistory.getAll())
      : null;

  return {
    exportedAt: new Date().toISOString(),
    mode,
    account,
    accounts,
    positions,
    orders: exportableOrders,
    trades: exportableTrades,
    equityHistory: exportableEquity,
    signals: exportableSignals,
    tradeNotes: exportableNotes,
    conditionalOrders: exportableConditionalOrders,
    favorites: exportableFavorites,
    backtestResults: exportableBacktests,
    performanceSummary,
    attributionByStrategy: attribution.byStrategy,
    attributionBySymbol: attribution.bySymbol,
    pnlByDay: attribution.byDay,
    pnlByWeek: attribution.byWeek,
    pnlByMonth: attribution.byMonth,
  };
}

// ── Multi-Account Consolidation ───────────────────────────────

/** Build a consolidated account overview from multiple accounts. */
export function buildConsolidatedAccountOverview(
  accounts: ExportableAccountOverview[],
): ExportableAccountOverview {
  const totalAssets = accounts.reduce((s, a) => s + a.totalAssets, 0);
  const cash = accounts.reduce((s, a) => s + a.cash, 0);
  const frozenCash = accounts.reduce((s, a) => s + a.frozenCash, 0);
  const marketValue = accounts.reduce((s, a) => s + a.marketValue, 0);
  const profit = accounts.reduce((s, a) => s + a.profit, 0);
  const todayProfit = accounts.reduce((s, a) => s + a.todayProfit, 0);
  const positionCount = accounts.reduce((s, a) => s + a.positionCount, 0);
  const profitPct = totalAssets > 0 ? (profit / (totalAssets - profit)) * 100 : 0;

  // Use the most recent sync time among all accounts
  const lastSyncAt = accounts
    .map((a) => a.lastSyncAt)
    .filter(Boolean)
    .sort()
    .pop() ?? "";

  return {
    accountId: "consolidated",
    brokerType: accounts.map((a) => a.brokerType).join("+"),
    cash,
    frozenCash,
    totalAssets,
    marketValue,
    profit,
    profitPct,
    todayProfit,
    positionCount,
    lastSyncAt,
  };
}
