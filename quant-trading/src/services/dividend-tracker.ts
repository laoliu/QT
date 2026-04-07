/**
 * DividendTracker — tracks dividends, stock splits, and corporate actions.
 *
 * Provides:
 *   - Record and query dividend income from holdings
 *   - Upcoming ex-dividend date alerts for current positions
 *   - Stock split / rights issue handling with cost basis adjustment
 *   - Dividend yield tracking and income summary
 *
 * Persists to dividend-records.json in the data directory.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type CorporateActionType =
  | "cash_dividend"
  | "stock_dividend"
  | "stock_split"
  | "rights_issue"
  | "bonus_issue"
  | "reverse_split";

export interface DividendRecord {
  id: string;
  /** Symbol code. */
  symbol: string;
  /** Record date (YYYY-MM-DD). */
  recordDate: string;
  /** Ex-dividend date (YYYY-MM-DD). */
  exDate: string;
  /** Payment date (YYYY-MM-DD). */
  payDate?: string;
  /** Corporate action type. */
  actionType: CorporateActionType;
  /** Cash amount per share (for cash_dividend). */
  amountPerShare?: number;
  /** Total cash received. */
  totalAmount?: number;
  /** Number of shares held at record date. */
  sharesHeld?: number;
  /** Split/bonus ratio (e.g. 10 for 10-for-1 split). */
  ratio?: number;
  /** New shares received (stock_dividend, bonus_issue, rights_issue). */
  newShares?: number;
  /** Rights issue subscription price. */
  subscriptionPrice?: number;
  /** Whether the rights were exercised. */
  exercised?: boolean;
  /** Notes. */
  notes?: string;
  /** Mode: paper or live. */
  mode: "paper" | "live";
  createdAt: string;
}

export interface UpcomingDividend {
  symbol: string;
  exDate: string;
  recordDate: string;
  amountPerShare: number;
  /** Estimated total income based on current holdings. */
  estimatedIncome?: number;
  /** Days until ex-date. */
  daysUntil: number;
}

export interface DividendTrackerOptions {
  maxRecords?: number;
  dataDir?: string | null;
}

export class DividendTracker {
  private records: DividendRecord[] = [];
  private upcomingDividends: UpcomingDividend[] = [];
  private idCounter = 0;
  private readonly maxRecords: number;
  private readonly dataDir: string | null;

  constructor(opts: DividendTrackerOptions = {}) {
    this.maxRecords = opts.maxRecords ?? 5000;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Record a corporate action (dividend, split, etc.). */
  addRecord(params: {
    symbol: string;
    recordDate: string;
    exDate: string;
    payDate?: string;
    actionType: CorporateActionType;
    amountPerShare?: number;
    totalAmount?: number;
    sharesHeld?: number;
    ratio?: number;
    newShares?: number;
    subscriptionPrice?: number;
    exercised?: boolean;
    notes?: string;
    mode?: "paper" | "live";
  }): string {
    if (this.records.length >= this.maxRecords) {
      this.records = this.records.slice(Math.floor(this.maxRecords * 0.1));
    }

    const id = `div-${String(++this.idCounter).padStart(5, "0")}`;
    const record: DividendRecord = {
      id,
      symbol: params.symbol,
      recordDate: params.recordDate,
      exDate: params.exDate,
      payDate: params.payDate,
      actionType: params.actionType,
      amountPerShare: params.amountPerShare,
      totalAmount: params.totalAmount,
      sharesHeld: params.sharesHeld,
      ratio: params.ratio,
      newShares: params.newShares,
      subscriptionPrice: params.subscriptionPrice,
      exercised: params.exercised,
      notes: params.notes,
      mode: params.mode ?? "paper",
      createdAt: new Date().toISOString(),
    };

    this.records.push(record);
    this.saveToDisk();
    return id;
  }

  /** Delete a record. */
  deleteRecord(id: string): boolean {
    const idx = this.records.findIndex((r) => r.id === id);
    if (idx < 0) return false;
    this.records.splice(idx, 1);
    this.saveToDisk();
    return true;
  }

  /** Get records for a symbol. */
  getBySymbol(symbol: string, limit = 50): DividendRecord[] {
    return this.records.filter((r) => r.symbol === symbol).slice(-limit);
  }

  /** Get records for a date range. */
  getByDateRange(fromDate: string, toDate: string): DividendRecord[] {
    return this.records.filter((r) => r.exDate >= fromDate && r.exDate <= toDate);
  }

  /** Get all cash dividend records. */
  getCashDividends(mode?: "paper" | "live"): DividendRecord[] {
    return this.records.filter(
      (r) => r.actionType === "cash_dividend" && (!mode || r.mode === mode),
    );
  }

  /** Set upcoming dividends (typically fetched from market data). */
  setUpcomingDividends(dividends: UpcomingDividend[]): void {
    this.upcomingDividends = dividends;
  }

  /** Get upcoming dividends for held positions. */
  getUpcoming(): UpcomingDividend[] {
    return this.upcomingDividends.sort((a, b) => a.daysUntil - b.daysUntil);
  }

  /** Compute dividend income summary for a given period. */
  getIncomeSummary(
    fromDate?: string,
    toDate?: string,
    mode?: "paper" | "live",
  ): {
    totalIncome: number;
    dividendCount: number;
    bySymbol: Array<{ symbol: string; totalAmount: number; count: number }>;
    byMonth: Record<string, number>;
  } {
    let filtered = this.records.filter((r) => r.actionType === "cash_dividend");
    if (mode) filtered = filtered.filter((r) => r.mode === mode);
    if (fromDate) filtered = filtered.filter((r) => r.exDate >= fromDate);
    if (toDate) filtered = filtered.filter((r) => r.exDate <= toDate);

    const totalIncome = filtered.reduce((s, r) => s + (r.totalAmount ?? 0), 0);
    const dividendCount = filtered.length;

    const symbolMap = new Map<string, { totalAmount: number; count: number }>();
    const byMonth: Record<string, number> = {};

    for (const r of filtered) {
      const existing = symbolMap.get(r.symbol) ?? { totalAmount: 0, count: 0 };
      existing.totalAmount += r.totalAmount ?? 0;
      existing.count++;
      symbolMap.set(r.symbol, existing);

      const month = r.exDate.slice(0, 7);
      byMonth[month] = (byMonth[month] ?? 0) + (r.totalAmount ?? 0);
    }

    const bySymbol = [...symbolMap.entries()]
      .map(([symbol, data]) => ({ symbol, ...data }))
      .sort((a, b) => b.totalAmount - a.totalAmount);

    return {
      totalIncome: Number(totalIncome.toFixed(2)),
      dividendCount,
      bySymbol,
      byMonth,
    };
  }

  /** Compute cost basis adjustment from splits/bonus issues. */
  getCostBasisAdjustment(
    symbol: string,
  ): {
    adjustmentFactor: number;
    actions: Array<{ date: string; type: CorporateActionType; ratio: number }>;
  } {
    const actions = this.records
      .filter(
        (r) =>
          r.symbol === symbol &&
          (r.actionType === "stock_split" ||
            r.actionType === "bonus_issue" ||
            r.actionType === "reverse_split"),
      )
      .sort((a, b) => a.exDate.localeCompare(b.exDate));

    let adjustmentFactor = 1;
    const details: Array<{ date: string; type: CorporateActionType; ratio: number }> = [];

    for (const action of actions) {
      const ratio = action.ratio ?? 1;
      if (action.actionType === "reverse_split") {
        adjustmentFactor /= ratio;
      } else {
        adjustmentFactor *= ratio;
      }
      details.push({
        date: action.exDate,
        type: action.actionType,
        ratio,
      });
    }

    return { adjustmentFactor, actions: details };
  }

  get size(): number {
    return this.records.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/dividend-records.json`;
  }

  private saveToDisk(): void {
    const p = this.persistPath();
    if (!p) return;
    const dir = dirname(p);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(
      p,
      JSON.stringify({ idCounter: this.idCounter, records: this.records }, null, 2),
    );
  }

  private loadFromDisk(): void {
    const p = this.persistPath();
    if (!p || !existsSync(p)) return;
    try {
      const data = JSON.parse(readFileSync(p, "utf-8")) as {
        idCounter: number;
        records: DividendRecord[];
      };
      this.idCounter = data.idCounter ?? 0;
      this.records = data.records ?? [];
    } catch {
      // Ignore corrupt data
    }
  }
}
