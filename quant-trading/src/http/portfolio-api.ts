import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OrderRequest } from "../broker/adapter.js";
import { createDataProvider } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
/**
 * HTTP API for portfolio operations (REST-style).
 * Used by the web dashboard and external integrations.
 */
import type { Portfolio, QuantTradingConfig, OrderSide, OrderType, MarketType } from "../types.js";

// ── Pagination Helper ────────────────────────────────────────────

export interface PaginationParams {
  page?: number; // 1-based (default 1)
  pageSize?: number; // items per page (default 20, max 200)
}

export interface PaginatedResult<T> {
  status: string;
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
}

/** Apply pagination to an array. Clamps page/pageSize to valid ranges. */
export function paginate<T>(items: T[], params: PaginationParams): PaginatedResult<T> {
  const pageSize = Math.max(1, Math.min(params.pageSize ?? 20, 200));
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const page = Math.max(1, Math.min(params.page ?? 1, totalPages));
  const start = (page - 1) * pageSize;
  const data = items.slice(start, start + pageSize);
  return {
    status: "ok",
    data,
    pagination: { page, pageSize, total: items.length, totalPages },
  };
}

function getPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio-paper.json");
}

/** Legacy path — used only for migration fallback */
function getLegacyPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio.json");
}

/**
 * GET /quant/api/portfolio — returns portfolio snapshot.
 * When a live engine with a real broker (e.g. QMT) is running,
 * returns the actual account data from the broker.
 * Falls back to the paper portfolio file or default values.
 * @param liveOnly When true, skip paper fallback — return empty if broker isn't live QMT.
 */
export async function handlePortfolioApi(
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  liveOnly = false,
): Promise<Record<string, unknown>> {
  // Prefer live broker account data when available
  const engine = getEngine?.();
  if (engine) {
    // Auto-start engine if not running
    const status = engine.getStatus();
    if (status === "stopped" || status === "error") {
      try {
        await engine.start();
      } catch {
        // Fall through to paper portfolio below
      }
    }
    // Force a fresh broker sync so the API always returns up-to-date data
    const { account, positions } = await engine.syncNow();
    if (account) {
      // When broker reports 0 profit, compute from position-level unrealized PnL
      let profit = account.profit;
      let profitPct = account.profitPct;
      if (profit === 0 && positions.length > 0) {
        const totalUnrealized = positions.reduce(
          (sum, p) => sum + (p.unrealizedPnl ?? 0),
          0,
        );
        if (totalUnrealized !== 0) {
          profit = totalUnrealized;
          const totalCost = positions.reduce(
            (sum, p) => sum + (p.avgCost ?? 0) * (p.quantity ?? 0),
            0,
          );
          profitPct = totalCost > 0 ? totalUnrealized / totalCost : 0;
        }
      }
      return {
        status: "ok",
        source: account.brokerType,
        data: {
          id: account.accountId,
          name: account.brokerType === "paper" ? "模拟组合" : "实盘组合",
          cash: account.cash,
          frozenCash: account.frozenCash,
          totalValue: account.totalAssets,
          marketValue: account.marketValue,
          initialCash: account.totalAssets - profit,
          profit,
          profitPct,
          todayProfit: account.todayProfit ?? 0,
          lastSyncAt: account.lastSyncAt,
          brokerType: account.brokerType,
          positions: positions.map((p) => ({
            symbol: p.symbol,
            name: p.name,
            market: p.market,
            quantity: p.quantity,
            availableQty: p.availableQty,
            frozenQty: p.frozenQty,
            avgCost: p.avgCost,
            currentPrice: p.currentPrice,
            marketValue: p.marketValue,
            unrealizedPnl: p.unrealizedPnl,
            unrealizedPnlPct: p.unrealizedPnlPct,
          })),
        },
      };
    }
  }

  // If caller only wants live data and we didn't get it from broker, return empty
  if (liveOnly) {
    return {
      status: "ok",
      source: "live",
      data: {
        id: "live",
        name: "实盘组合",
        cash: 0,
        totalValue: 0,
        positions: [],
        message: "实盘未连接或无数据",
      },
    };
  }

  // Fallback: read paper portfolio from disk and enrich with live quotes
  let raw: string | null = null;
  try {
    raw = await fs.readFile(getPortfolioPath(config), "utf-8");
  } catch {
    // Try legacy path for migration
    try {
      raw = await fs.readFile(getLegacyPortfolioPath(config), "utf-8");
    } catch {
      // no file at all
    }
  }
  try {
    if (!raw) throw new Error("no portfolio file");
    const pf = JSON.parse(raw) as Portfolio;

    // Refresh currentPrice / PnL from live quotes so the dashboard shows real values
    if (pf.positions?.length) {
      try {
        const provider = createDataProvider(config);
        const QUOTE_TIMEOUT = 5_000;
        await Promise.all(
          pf.positions.map(async (pos) => {
            try {
              const q = await Promise.race([
                provider.getQuote(pos.symbol, pos.market),
                new Promise<null>((r) => setTimeout(() => r(null), QUOTE_TIMEOUT)),
              ]);
              if (q?.price) {
                pos.currentPrice = q.price;
                pos.marketValue = pos.quantity * q.price;
                if (pos.avgCost > 0) {
                  pos.unrealizedPnl = (q.price - pos.avgCost) * pos.quantity;
                  pos.unrealizedPnlPct = (q.price - pos.avgCost) / pos.avgCost;
                }
              }
            } catch {
              // Keep stale data if individual quote fails
            }
          }),
        );
        // Recompute portfolio-level totals
        const mv = pf.positions.reduce(
          (s, p) => s + (p.marketValue ?? p.quantity * (p.currentPrice ?? p.avgCost)),
          0,
        );
        pf.totalValue = pf.cash + mv;
        const initCash = pf.initialCash ?? 100_000;
        const profit = pf.totalValue - initCash;
        return {
          status: "ok",
          source: "paper",
          data: {
            ...pf,
            marketValue: mv,
            profit,
            profitPct: profit / initCash,
          },
        };
      } catch {
        // Quote enrichment failed entirely — return raw data
      }
    }

    return { status: "ok", source: "paper", data: pf };
  } catch {
    return {
      status: "ok",
      source: "paper",
      data: {
        id: "default",
        name: "默认组合",
        cash: 100000,
        totalValue: 100000,
        positions: [],
      },
    };
  }
}

/**
 * GET /quant/api/portfolio?mode=paper — force read paper portfolio from disk.
 * Always reads portfolio-paper.json regardless of active engine mode.
 * Falls back to legacy portfolio.json if new file doesn't exist yet.
 */
export async function handlePaperPortfolioOnly(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const dir = modeDataDir(config);
  // Try new mode-specific file first, then legacy
  for (const filename of ["portfolio-paper.json", "portfolio.json"]) {
    try {
      const raw = await fs.readFile(path.join(dir, filename), "utf-8");
      const pf = JSON.parse(raw) as Portfolio;
      return { status: "ok", source: "paper", data: pf };
    } catch {
      continue;
    }
  }
  return {
    status: "ok",
    source: "paper",
    data: {
      id: "paper",
      name: "模拟组合",
      cash: 100000,
      totalValue: 100000,
      positions: [],
    },
  };
}

// ── Mode-specific file readers (for cross-mode queries) ──────────

function modeDataDir(config: QuantTradingConfig): string {
  return config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

/**
 * Read orders from a specific mode file (paper or live).
 * Used when the UI is viewing a mode different from the active engine.
 */
export async function readOrdersFromFile(
  config: QuantTradingConfig,
  mode: "paper" | "live",
  statusFilter?: string,
): Promise<Record<string, unknown>> {
  const filePath = path.join(modeDataDir(config), `orders-${mode}.json`);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    let orders = JSON.parse(raw) as Record<string, unknown>[];
    if (statusFilter) orders = orders.filter((o) => o.status === statusFilter);
    return { status: "ok", mode, data: orders };
  } catch {
    return { status: "ok", mode, data: [] };
  }
}

/**
 * Read trades from a specific mode file.
 */
export async function readTradesFromFile(
  config: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<Record<string, unknown>> {
  const filePath = path.join(modeDataDir(config), `trades-${mode}.json`);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return { status: "ok", mode, data: JSON.parse(raw) };
  } catch {
    return { status: "ok", mode, data: [] };
  }
}

/**
 * Compute daily PnL from a specific mode's trades file.
 */
export async function readDailyPnlFromFile(
  config: QuantTradingConfig,
  mode: "paper" | "live",
): Promise<Record<string, unknown>> {
  const filePath = path.join(modeDataDir(config), `trades-${mode}.json`);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const trades = JSON.parse(raw) as { exitTime?: string; netPnl: number }[];
    const today = new Date().toISOString().slice(0, 10);
    const pnl = trades
      .filter((t) => t.exitTime?.startsWith(today))
      .reduce((sum, t) => sum + t.netPnl, 0);
    return { status: "ok", mode, data: { date: today, netPnl: pnl } };
  } catch {
    return { status: "ok", mode, data: { date: new Date().toISOString().slice(0, 10), netPnl: 0 } };
  }
}

/**
 * GET /quant/api/live/status — returns live engine status.
 */
export function handleLiveStatusApi(getEngine: () => LiveEngine | null): Record<string, unknown> {
  const engine = getEngine();
  if (!engine) {
    return { status: "ok", data: { engineStatus: "stopped" } };
  }
  const info = engine.getStatus();
  return { status: "ok", data: info };
}

/**
 * GET /quant/api/orders — returns today's live orders (supports pagination).
 */
export async function handleOrdersApi(
  getEngine: () => LiveEngine | null,
  statusFilter?: string,
  pagination?: PaginationParams,
): Promise<Record<string, unknown> | PaginatedResult<import("../types.js").Order>> {
  const engine = getEngine();
  if (!engine?.orderManager) return { status: "ok", data: [] };
  const orders = await engine.orderManager.getOrders();
  const filtered = statusFilter ? orders.filter((o) => o.status === statusFilter) : orders;
  if (pagination) return paginate(filtered, pagination);
  return { status: "ok", data: filtered };
}

/**
 * GET /quant/api/trades — returns today's closed trades.
 */
export async function handleTradesApi(
  getEngine: () => LiveEngine | null,
  symbol?: string,
): Promise<Record<string, unknown>> {
  const engine = getEngine();
  if (!engine?.orderManager) return { status: "ok", data: [] };
  return { status: "ok", data: await engine.orderManager.getTrades(symbol) };
}

/**
 * GET /quant/api/pnl — returns today's PnL summary.
 */
export async function handlePnlApi(
  getEngine: () => LiveEngine | null,
): Promise<Record<string, unknown>> {
  const engine = getEngine();
  if (!engine?.orderManager) return { status: "ok", data: null };
  const netPnl = await engine.orderManager.getDailyPnl();
  return {
    status: "ok",
    data: { date: new Date().toISOString().slice(0, 10), netPnl },
  };
}

/**
 * POST /quant/api/orders — place a new order via the live engine.
 * Expected body: { symbol, side, orderType?, quantity, price?, market? }
 */
export async function handlePlaceOrderApi(
  getEngine: () => LiveEngine | null,
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const engine = getEngine();
  if (!engine?.orderManager) {
    return { status: "error", message: "引擎未启动，无法下单" };
  }
  const symbol = body.symbol as string | undefined;
  const side = body.side as string | undefined;
  const quantity = body.quantity as number | undefined;
  if (!symbol || !side || !quantity) {
    return { status: "error", message: "缺少必要参数: symbol, side, quantity" };
  }
  // Validate quantity and price are sensible numbers
  if (typeof quantity !== "number" || !Number.isFinite(quantity) || quantity <= 0) {
    return { status: "error", message: `quantity 必须为正数，收到 ${quantity}` };
  }
  const rawPrice = body.price as number | undefined;
  if (
    rawPrice != null &&
    (typeof rawPrice !== "number" || !Number.isFinite(rawPrice) || rawPrice < 0)
  ) {
    return { status: "error", message: `price 必须为非负数，收到 ${rawPrice}` };
  }
  // Validate side and orderType to prevent arbitrary strings reaching the broker
  const validSides: OrderSide[] = ["buy", "sell", "short", "cover"];
  if (!validSides.includes(side as OrderSide)) {
    return {
      status: "error",
      message: `side "${side}" 无效，有效值: ${validSides.join(", ")}`,
    };
  }
  const orderType = (body.orderType as string) ?? "market";
  const validOrderTypes: OrderType[] = ["market", "limit"];
  if (!validOrderTypes.includes(orderType as OrderType)) {
    return {
      status: "error",
      message: `orderType "${orderType}" 无效，有效值: ${validOrderTypes.join(", ")}`,
    };
  }
  const req: OrderRequest = {
    symbol,
    market: (body.market as MarketType) ?? "cn_stock",
    side: side as OrderSide,
    orderType: orderType as OrderType,
    quantity,
    price: body.price as number | undefined,
  };
  try {
    const result = await engine.orderManager.submit(req);
    if (!result.ok) {
      return { status: "error", message: result.reason };
    }
    return { status: "ok", data: result.order };
  } catch (err) {
    return { status: "error", message: (err as Error).message };
  }
}

/**
 * GET /quant/api/signals — query signals.json with optional filters and pagination.
 */
export async function handleSignalsApi(
  config: QuantTradingConfig,
  filter: { strategyId?: string; symbol?: string; limit?: number },
  pagination?: PaginationParams,
): Promise<Record<string, unknown> | PaginatedResult<Record<string, unknown>>> {
  try {
    const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    const raw = await fs.readFile(path.join(dir, "signals.json"), "utf-8");
    let signals = JSON.parse(raw) as Array<Record<string, unknown>>;
    if (filter.strategyId) signals = signals.filter((s) => s["strategyId"] === filter.strategyId);
    if (filter.symbol) signals = signals.filter((s) => s["symbol"] === filter.symbol);
    if (pagination) return paginate(signals, pagination);
    const limit = filter.limit ?? 50;
    return { status: "ok", data: signals.slice(-limit) };
  } catch {
    return { status: "ok", data: [] };
  }
}

/**
 * GET /quant/api/stocklist — return the screened stock list pushed from Mac screener.
 * POST /quant/api/stocklist — replace the stock list (called by qmt_screener_mac.py).
 */
function getStocklistPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "stock-list.json");
}

export async function handleGetStocklistApi(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  try {
    const raw = await fs.readFile(getStocklistPath(config), "utf-8");
    const data = JSON.parse(raw) as unknown;
    return { status: "ok", data };
  } catch {
    return { status: "ok", data: [] };
  }
}

export async function handleSetStocklistApi(
  config: QuantTradingConfig,
  body: unknown,
): Promise<Record<string, unknown>> {
  if (!Array.isArray(body)) {
    return { status: "error", error: "body must be an array of stock codes" };
  }
  if (!body.every((s: unknown) => typeof s === "string" && s.length > 0)) {
    return { status: "error", error: "每个元素必须为非空字符串" };
  }
  const filePath = getStocklistPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(body, null, 2), "utf-8");
  // Also append to history
  await appendScreenerHistory(config, body as string[]);
  return { status: "ok", data: body, count: body.length };
}

/**
 * Screener history — stores daily snapshots for comparison.
 * File: screener-history.json = { "2026-03-07": { codes: [...], strategy: "..." }, ... }
 * Legacy format (string[]) is auto-upgraded on read.
 */

interface ScreenerHistoryEntry {
  codes: string[];
  strategy: string;
}

function getScreenerHistoryPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "screener-history.json");
}

async function loadScreenerHistory(
  config: QuantTradingConfig,
): Promise<Record<string, ScreenerHistoryEntry>> {
  try {
    const raw = await fs.readFile(getScreenerHistoryPath(config), "utf-8");
    const parsed = JSON.parse(raw) as Record<string, string[] | ScreenerHistoryEntry>;
    // Auto-upgrade legacy format (plain string[] → { codes, strategy })
    const result: Record<string, ScreenerHistoryEntry> = {};
    for (const [date, val] of Object.entries(parsed)) {
      if (Array.isArray(val)) {
        result[date] = { codes: val, strategy: "" };
      } else {
        result[date] = val;
      }
    }
    return result;
  } catch {
    return {};
  }
}

export async function appendScreenerHistory(
  config: QuantTradingConfig,
  codes: string[],
  strategy = "",
): Promise<void> {
  const history = await loadScreenerHistory(config);
  const today = new Date().toISOString().slice(0, 10);
  history[today] = { codes, strategy };
  // Keep last 30 days
  const dates = Object.keys(history).sort().reverse();
  const trimmed: Record<string, ScreenerHistoryEntry> = {};
  for (const d of dates.slice(0, 30)) {
    trimmed[d] = history[d];
  }
  const filePath = getScreenerHistoryPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(trimmed, null, 2), "utf-8");
}

/**
 * GET /quant/api/stocklist/history — screener history with comparison.
 * Returns: { dates, history, comparison: { new, dropped, consecutive } }
 */
export async function handleGetScreenerHistoryApi(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const history = await loadScreenerHistory(config);
  const dates = Object.keys(history).sort().reverse();
  if (dates.length === 0) return { status: "ok", dates: [], history: {}, comparison: null };

  const today = dates[0];
  const todayCodes = history[today]?.codes ?? [];
  const todaySet = new Set(todayCodes);

  // Build comparison against all past days
  const yesterday = dates.length > 1 ? dates[1] : null;
  const yesterdayCodes = yesterday ? (history[yesterday]?.codes ?? []) : [];
  const yesterdaySet = new Set(yesterdayCodes);

  // Count consecutive days for each stock
  const consecutive: Record<string, number> = {};
  for (const code of todaySet) {
    let count = 1;
    for (let i = 1; i < dates.length; i++) {
      if (history[dates[i]]?.codes?.includes(code)) {
        count++;
      } else {
        break;
      }
    }
    consecutive[code] = count;
  }

  const newStocks = [...todaySet].filter((c) => !yesterdaySet.has(c));
  const droppedStocks = [...yesterdaySet].filter((c) => !todaySet.has(c));
  const keptStocks = [...todaySet].filter((c) => yesterdaySet.has(c));

  return {
    status: "ok",
    dates,
    history,
    comparison: {
      date: today,
      prevDate: yesterday,
      total: todaySet.size,
      new: newStocks,
      dropped: droppedStocks,
      kept: keptStocks,
      consecutive,
    },
  };
}

/**
 * Investment plan history — stores timestamped plan snapshots with full plan data.
 * File: plan-history.json = [ { id, version, timestamp, label, params, summary, stocks, fullPlan }, ... ]
 *
 * Version format: "YYYY-MM-DD-vN" where N is the Nth plan generated on that date.
 */
interface PlanHistoryEntry {
  id: string;
  /** Date-based version label, e.g. "2026-03-08-v1" */
  version: string;
  timestamp: string;
  /** User-provided label/notes for this plan */
  label: string;
  params: Record<string, unknown>;
  summary: Record<string, unknown>;
  stocks: string[];
  stockCount: number;
  strategy: string;
  initialCapital: number;
  targetCapital: number;
  /** Full plan data for restore/comparison */
  fullPlan: Record<string, unknown> | null;
}

function getPlanHistoryPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "plan-history.json");
}

async function loadPlanHistory(config: QuantTradingConfig): Promise<PlanHistoryEntry[]> {
  try {
    const raw = await fs.readFile(getPlanHistoryPath(config), "utf-8");
    const entries = JSON.parse(raw) as PlanHistoryEntry[];
    // Migrate old entries: add version if missing
    let migrated = false;
    for (const entry of entries) {
      if (!entry.version) {
        entry.version = entry.id.substring(0, 10);
        migrated = true;
      }
      if (entry.label === undefined) {
        entry.label = "";
        migrated = true;
      }
    }
    if (migrated) {
      const filePath = getPlanHistoryPath(config);
      await fs.writeFile(filePath, JSON.stringify(entries, null, 2), "utf-8");
    }
    return entries;
  } catch {
    return [];
  }
}

/** Generate a date-based version string: YYYY-MM-DD-vN */
function generateVersion(existingHistory: PlanHistoryEntry[], now: Date): string {
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const todayCount = existingHistory.filter(
    (e) => e.version && e.version.startsWith(dateStr + "-v"),
  ).length;
  return `${dateStr}-v${todayCount + 1}`;
}

export async function appendPlanHistory(
  config: QuantTradingConfig,
  plan: Record<string, unknown>,
  params: Record<string, unknown>,
): Promise<void> {
  const history = await loadPlanHistory(config);
  const portfolio = (plan.portfolio ?? []) as Array<Record<string, unknown>>;
  const planMeta = (plan.plan ?? {}) as Record<string, unknown>;
  const summary = (plan.summary ?? {}) as Record<string, unknown>;
  const now = new Date();
  const entry: PlanHistoryEntry = {
    id: now.toISOString().replace(/[:.]/g, "-"),
    version: generateVersion(history, now),
    timestamp: now.toISOString(),
    label: "",
    params,
    summary: {
      strategy: planMeta.strategyLabel ?? planMeta.strategy ?? "",
      riskLevel: planMeta.riskLevel ?? "",
      feasibility: planMeta.feasibility ?? "",
      requiredReturn: planMeta.requiredReturn ?? 0,
      totalPositions: summary.totalPositions ?? portfolio.length,
      cashReservePct: summary.cashReservePct ?? 0,
    },
    stocks: portfolio.map((p) => String(p.symbol ?? p.code ?? "")),
    stockCount: portfolio.length,
    strategy: String(
      planMeta.strategyLabel ?? planMeta.strategy ?? params.strategy_preference ?? "",
    ),
    initialCapital: Number(params.initial_capital ?? 0),
    targetCapital: Number(params.target_capital ?? 0),
    fullPlan: plan,
  };
  history.unshift(entry);
  // Keep last 100 entries
  const trimmed = history.slice(0, 100);
  const filePath = getPlanHistoryPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(trimmed, null, 2), "utf-8");
}

export async function handleGetPlanHistoryApi(
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  const history = await loadPlanHistory(config);
  if (history.length === 0) return { status: "ok", entries: [], count: 0 };

  // Build stock frequency analysis across all plans
  const stockFreq: Record<string, number> = {};
  for (const entry of history) {
    for (const code of entry.stocks) {
      stockFreq[code] = (stockFreq[code] ?? 0) + 1;
    }
  }
  // Sort by frequency
  const topStocks = Object.entries(stockFreq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map(([code, count]) => ({ code, count, pct: Math.round((count / history.length) * 100) }));

  // Strip fullPlan from listing to reduce payload (details fetched per-entry)
  const entries = history.map(({ fullPlan: _fp, ...rest }) => rest);

  return {
    status: "ok",
    entries,
    count: history.length,
    topStocks,
  };
}

/** Get a single plan by ID, with full plan data. */
export async function handleGetPlanByIdApi(
  config: QuantTradingConfig,
  planId: string,
): Promise<Record<string, unknown>> {
  const history = await loadPlanHistory(config);
  const entry = history.find((e) => e.id === planId);
  if (!entry) return { status: "error", message: "未找到该计划" };
  return { status: "ok", entry };
}

/** Delete a plan from history by ID. */
export async function handleDeletePlanApi(
  config: QuantTradingConfig,
  planId: string,
): Promise<Record<string, unknown>> {
  const history = await loadPlanHistory(config);
  const idx = history.findIndex((e) => e.id === planId);
  if (idx === -1) return { status: "error", message: "未找到该计划" };
  history.splice(idx, 1);
  const filePath = getPlanHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok" };
}

/** Update plan label/notes by ID. */
export async function handleUpdatePlanLabelApi(
  config: QuantTradingConfig,
  planId: string,
  label: string,
): Promise<Record<string, unknown>> {
  const history = await loadPlanHistory(config);
  const entry = history.find((e) => e.id === planId);
  if (!entry) return { status: "error", message: "未找到该计划" };
  entry.label = label;
  const filePath = getPlanHistoryPath(config);
  await fs.writeFile(filePath, JSON.stringify(history, null, 2), "utf-8");
  return { status: "ok" };
}

/**
 * DELETE /quant/api/orders/:id — cancel an existing order.
 */
export async function handleCancelOrderApi(
  getEngine: () => LiveEngine | null,
  orderId: string,
): Promise<Record<string, unknown>> {
  const engine = getEngine();
  if (!engine?.orderManager) {
    return { status: "error", message: "引擎未启动" };
  }
  try {
    await engine.orderManager.cancel(orderId);
    return { status: "ok", data: { cancelled: orderId } };
  } catch (err) {
    return { status: "error", message: (err as Error).message };
  }
}
