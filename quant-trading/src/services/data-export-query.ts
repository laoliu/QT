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
  Portfolio,
  PortfolioPosition,
  QuantTradingConfig,
  TradeRecord,
} from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import {
  computeAttribution,
  type AttributionResult,
  type PeriodPnl,
  type StrategyAttribution,
  type SymbolAttribution,
} from "./performance-attribution.js";
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
  accountSummary: ExportableAccountSummary | null;
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
  | "account";

const ALL_SECTIONS: ExportSection[] = [
  "orders",
  "trades",
  "positions",
  "equity",
  "attribution",
  "notes",
  "account",
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
    accountSummary: null,
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

  // ── Account Summary ──
  if (sections.has("account")) {
    promises.push(
      buildAccountSummary(cfg, mode).then((summary) => {
        dataset.accountSummary = summary;
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
