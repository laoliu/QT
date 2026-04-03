/**
 * Data quality validation for historical K-line data.
 *
 * Detects:
 *   - Missing trading days (gaps in date sequence)
 *   - Price anomalies (extreme moves, zero/negative, OHLC violations)
 *   - Volume anomalies (zero volume on trading days, extreme spikes)
 *   - Stale data (cache too old for active symbols)
 *   - Duplicate records
 *
 * Works alongside the HistoricalDataWarehouse (E2) and DataStalenessMonitor.
 */
import type { KlineRow, QuantStorage } from "./sqlite-storage.js";

// ── Types ────────────────────────────────────────────────────

export type IssueSeverity = "info" | "warning" | "error";

export interface DataQualityIssue {
  severity: IssueSeverity;
  symbol: string;
  date: string;
  category: "missing" | "price" | "volume" | "ohlc" | "stale" | "duplicate";
  message: string;
  /** Extra context for programmatic consumers. */
  details?: Record<string, unknown>;
}

export interface ValidationOptions {
  /** Skip weekends when checking for gaps (default: true) */
  skipWeekends?: boolean;
  /** Skip known CN holidays — simple predefined list (default: false) */
  skipHolidays?: boolean;
  /** Max allowed daily price change ratio before flagging (default: 0.20 = 20%) */
  maxPriceChangeRatio?: number;
  /** Max allowed volume spike ratio vs 20-day average (default: 10) */
  maxVolumeSpikeRatio?: number;
  /** Warn if newest bar is older than N calendar days (default: 3) */
  stalenessThresholdDays?: number;
}

export interface ValidationReport {
  checkedAt: string;
  symbol: string;
  interval: string;
  barCount: number;
  dateRange: { min: string; max: string } | null;
  issues: DataQualityIssue[];
  summary: {
    total: number;
    errors: number;
    warnings: number;
    info: number;
  };
  score: number; // 0-100 quality score
}

export interface BulkValidationReport {
  checkedAt: string;
  symbolCount: number;
  totalBars: number;
  totalIssues: number;
  reports: ValidationReport[];
  overallScore: number;
}

// ── Config defaults ──────────────────────────────────────────

const DEFAULTS: Required<ValidationOptions> = {
  skipWeekends: true,
  skipHolidays: false,
  maxPriceChangeRatio: 0.2,
  maxVolumeSpikeRatio: 10,
  stalenessThresholdDays: 3,
};

// ── Helpers ──────────────────────────────────────────────────

/** Is this date a Saturday (6) or Sunday (0)? */
function isWeekend(dateStr: string): boolean {
  const [y, m, d] = dateStr.split("-").map(Number);
  const day = new Date(Date.UTC(y!, m! - 1, d!)).getUTCDay();
  return day === 0 || day === 6;
}

/** Add one calendar day to a YYYY-MM-DD string */
function nextDay(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y!, m! - 1, d!));
  dt.setUTCDate(dt.getUTCDate() + 1);
  return dt.toISOString().slice(0, 10);
}

/** Days between two YYYY-MM-DD dates (positive if b > a) */
function daysBetween(a: string, b: string): number {
  const da = new Date(a + "T00:00:00Z").getTime();
  const db = new Date(b + "T00:00:00Z").getTime();
  return Math.round((db - da) / 86_400_000);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// ── Validator ────────────────────────────────────────────────

export class DataQualityValidator {
  private opts: Required<ValidationOptions>;

  constructor(opts: ValidationOptions = {}) {
    this.opts = { ...DEFAULTS, ...opts };
  }

  /**
   * Validate kline data for a single symbol.
   * Pass kline rows directly or read from storage.
   */
  validate(rows: KlineRow[], symbol: string, interval: string): ValidationReport {
    const issues: DataQualityIssue[] = [];

    if (rows.length === 0) {
      return {
        checkedAt: new Date().toISOString(),
        symbol,
        interval,
        barCount: 0,
        dateRange: null,
        issues: [],
        summary: { total: 0, errors: 0, warnings: 0, info: 0 },
        score: 100,
      };
    }

    // Sort by date ascending
    const sorted = [...rows].sort((a, b) => a.date.localeCompare(b.date));
    const dateRange = { min: sorted[0]!.date, max: sorted[sorted.length - 1]!.date };

    // 1. Check for duplicates
    this.checkDuplicates(sorted, symbol, issues);

    // 2. Check for missing trading days
    this.checkMissingDays(sorted, symbol, issues);

    // 3. Check OHLC consistency
    this.checkOhlcConsistency(sorted, symbol, issues);

    // 4. Check price anomalies
    this.checkPriceAnomalies(sorted, symbol, issues);

    // 5. Check volume anomalies
    this.checkVolumeAnomalies(sorted, symbol, issues);

    // 6. Check staleness
    this.checkStaleness(sorted, symbol, issues);

    const errors = issues.filter((i) => i.severity === "error").length;
    const warnings = issues.filter((i) => i.severity === "warning").length;
    const info = issues.filter((i) => i.severity === "info").length;

    // Score: start at 100, deduct for issues
    const score = Math.max(0, Math.round(100 - errors * 10 - warnings * 3 - info * 0.5));

    return {
      checkedAt: new Date().toISOString(),
      symbol,
      interval,
      barCount: sorted.length,
      dateRange,
      issues,
      summary: { total: issues.length, errors, warnings, info },
      score,
    };
  }

  /**
   * Validate data for multiple symbols from storage.
   */
  async validateFromStorage(
    storage: QuantStorage,
    symbols: Array<{ symbol: string; interval: string }>,
  ): Promise<BulkValidationReport> {
    const reports: ValidationReport[] = [];
    let totalBars = 0;
    let totalIssues = 0;

    for (const { symbol, interval } of symbols) {
      const range = await storage.getKlineDateRange(symbol, interval);
      if (!range) {
        reports.push(this.validate([], symbol, interval));
        continue;
      }
      const rows = await storage.getKlines(symbol, interval, range.min, range.max);
      const report = this.validate(rows, symbol, interval);
      totalBars += report.barCount;
      totalIssues += report.issues.length;
      reports.push(report);
    }

    const overallScore =
      reports.length > 0
        ? Math.round(reports.reduce((sum, r) => sum + r.score, 0) / reports.length)
        : 100;

    return {
      checkedAt: new Date().toISOString(),
      symbolCount: symbols.length,
      totalBars,
      totalIssues,
      reports,
      overallScore,
    };
  }

  // ── Internal checks ────────────────────────────────────────

  private checkDuplicates(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    const seen = new Set<string>();
    for (const row of rows) {
      if (seen.has(row.date)) {
        issues.push({
          severity: "warning",
          symbol,
          date: row.date,
          category: "duplicate",
          message: `Duplicate bar for ${row.date}`,
        });
      }
      seen.add(row.date);
    }
  }

  private checkMissingDays(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    if (rows.length < 2) return;
    const dates = new Set(rows.map((r) => r.date));
    let cur = rows[0]!.date;
    const end = rows[rows.length - 1]!.date;

    while (cur <= end) {
      if (!dates.has(cur)) {
        const skip = this.opts.skipWeekends && isWeekend(cur);
        if (!skip) {
          issues.push({
            severity: "info",
            symbol,
            date: cur,
            category: "missing",
            message: `Missing bar for ${cur}`,
          });
        }
      }
      cur = nextDay(cur);
    }
  }

  private checkOhlcConsistency(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    for (const row of rows) {
      // High should be >= Open, Close, Low
      if (row.high < row.open || row.high < row.close || row.high < row.low) {
        issues.push({
          severity: "error",
          symbol,
          date: row.date,
          category: "ohlc",
          message: `High (${row.high}) is not the highest among OHLC`,
          details: { open: row.open, high: row.high, low: row.low, close: row.close },
        });
      }
      // Low should be <= Open, Close, High
      if (row.low > row.open || row.low > row.close || row.low > row.high) {
        issues.push({
          severity: "error",
          symbol,
          date: row.date,
          category: "ohlc",
          message: `Low (${row.low}) is not the lowest among OHLC`,
          details: { open: row.open, high: row.high, low: row.low, close: row.close },
        });
      }
      // Zero/negative prices
      if (row.close <= 0 || row.open <= 0) {
        issues.push({
          severity: "error",
          symbol,
          date: row.date,
          category: "price",
          message: `Zero or negative price detected (open=${row.open}, close=${row.close})`,
        });
      }
    }
  }

  private checkPriceAnomalies(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    for (let i = 1; i < rows.length; i++) {
      const prev = rows[i - 1]!;
      const cur = rows[i]!;
      if (prev.close === 0) continue;
      const changeRatio = Math.abs(cur.close - prev.close) / prev.close;
      if (changeRatio > this.opts.maxPriceChangeRatio) {
        issues.push({
          severity: "warning",
          symbol,
          date: cur.date,
          category: "price",
          message: `Large price move: ${(changeRatio * 100).toFixed(1)}% (${prev.close} → ${cur.close})`,
          details: { prevClose: prev.close, close: cur.close, changeRatio },
        });
      }
    }
  }

  private checkVolumeAnomalies(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    // Flag zero-volume bars
    for (const row of rows) {
      if (row.volume === 0) {
        issues.push({
          severity: "info",
          symbol,
          date: row.date,
          category: "volume",
          message: `Zero volume on ${row.date}`,
        });
      }
    }

    // Flag volume spikes (>N× 20-day average)
    const windowSize = 20;
    if (rows.length <= windowSize) return;

    for (let i = windowSize; i < rows.length; i++) {
      const window = rows.slice(i - windowSize, i);
      const avgVol = window.reduce((s, r) => s + r.volume, 0) / windowSize;
      if (avgVol === 0) continue;
      const cur = rows[i]!;
      const spikeRatio = cur.volume / avgVol;
      if (spikeRatio > this.opts.maxVolumeSpikeRatio) {
        issues.push({
          severity: "warning",
          symbol,
          date: cur.date,
          category: "volume",
          message: `Volume spike: ${spikeRatio.toFixed(1)}× average (${cur.volume} vs avg ${Math.round(avgVol)})`,
          details: { volume: cur.volume, avgVolume: avgVol, spikeRatio },
        });
      }
    }
  }

  private checkStaleness(rows: KlineRow[], symbol: string, issues: DataQualityIssue[]): void {
    const lastDate = rows[rows.length - 1]!.date;
    const days = daysBetween(lastDate, today());
    if (days > this.opts.stalenessThresholdDays) {
      issues.push({
        severity: "warning",
        symbol,
        date: lastDate,
        category: "stale",
        message: `Data is ${days} days old (last bar: ${lastDate})`,
        details: { daysSinceLastBar: days },
      });
    }
  }
}
