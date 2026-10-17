import { describe, expect, it } from "vitest";
import { DataQualityValidator } from "./data-quality.js";
import { InMemoryStorage } from "./sqlite-storage.js";
import type { KlineRow } from "./sqlite-storage.js";

// ── Test helpers ─────────────────────────────────────────────

function row(date: string, o: number, h: number, l: number, c: number, v = 10000): KlineRow {
  return { symbol: "600519", date, interval: "1d", open: o, high: h, low: l, close: c, volume: v };
}

/** Generate N consecutive daily bars (weekdays and weekends alike) */
function generateBars(
  startDate: string,
  count: number,
  opts?: { basePrice?: number; baseVol?: number },
): KlineRow[] {
  const base = opts?.basePrice ?? 100;
  const vol = opts?.baseVol ?? 10000;
  const rows: KlineRow[] = [];
  const [y, m, d] = startDate.split("-").map(Number);
  for (let i = 0; i < count; i++) {
    const dt = new Date(Date.UTC(y!, m! - 1, d! + i));
    const ds = dt.toISOString().slice(0, 10);
    rows.push(row(ds, base + i, base + i + 5, base + i - 2, base + i + 3, vol + i * 100));
  }
  return rows;
}

// ── Tests ────────────────────────────────────────────────────

describe("DataQualityValidator", () => {
  const validator = new DataQualityValidator({
    skipWeekends: true,
    maxPriceChangeRatio: 0.1,
    maxVolumeSpikeRatio: 5,
    stalenessThresholdDays: 9999, // disable for fixed historical fixtures
  });

  it("returns clean report for valid data", () => {
    // Mon-Fri bars, consecutive, normal prices
    const rows = [
      row("2024-01-08", 100, 105, 98, 103), // Monday
      row("2024-01-09", 103, 108, 101, 106),
      row("2024-01-10", 106, 111, 104, 109),
      row("2024-01-11", 109, 114, 107, 112),
      row("2024-01-12", 112, 117, 110, 115), // Friday
    ];
    const report = validator.validate(rows, "600519", "1d");
    expect(report.barCount).toBe(5);
    expect(report.issues).toHaveLength(0);
    expect(report.score).toBe(100);
  });

  it("returns perfect score for empty data", () => {
    const report = validator.validate([], "600519", "1d");
    expect(report.barCount).toBe(0);
    expect(report.score).toBe(100);
  });

  // ── Missing days ──────────────────────────────────────────

  it("detects missing weekday bars", () => {
    const rows = [
      row("2024-01-08", 100, 105, 98, 103), // Monday
      // Tuesday 2024-01-09 missing
      row("2024-01-10", 103, 108, 101, 106), // Wednesday
    ];
    const report = validator.validate(rows, "600519", "1d");
    const missing = report.issues.filter((i) => i.category === "missing");
    expect(missing).toHaveLength(1);
    expect(missing[0]!.date).toBe("2024-01-09");
  });

  it("skips weekends when checking for gaps", () => {
    const rows = [
      row("2024-01-12", 100, 105, 98, 103), // Friday
      row("2024-01-15", 103, 108, 101, 106), // Monday — weekend gap is OK
    ];
    const report = validator.validate(rows, "600519", "1d");
    const missing = report.issues.filter((i) => i.category === "missing");
    expect(missing).toHaveLength(0);
  });

  it("flags weekend gaps when skipWeekends is false", () => {
    const strictValidator = new DataQualityValidator({
      skipWeekends: false,
      stalenessThresholdDays: 999,
    });
    const rows = [
      row("2024-01-12", 100, 105, 98, 103), // Friday
      row("2024-01-15", 103, 108, 101, 106), // Monday
    ];
    const report = strictValidator.validate(rows, "600519", "1d");
    const missing = report.issues.filter((i) => i.category === "missing");
    expect(missing).toHaveLength(2); // Saturday + Sunday
  });

  // ── OHLC consistency ──────────────────────────────────────

  it("detects high < close violation", () => {
    const rows = [
      row("2024-01-08", 100, 99, 98, 103), // high < close - invalid
    ];
    const report = validator.validate(rows, "600519", "1d");
    const ohlc = report.issues.filter((i) => i.category === "ohlc");
    expect(ohlc.length).toBeGreaterThan(0);
    expect(ohlc[0]!.severity).toBe("error");
  });

  it("detects low > open violation", () => {
    const rows = [
      row("2024-01-08", 100, 110, 105, 103), // low > open - invalid
    ];
    const report = validator.validate(rows, "600519", "1d");
    const ohlc = report.issues.filter((i) => i.category === "ohlc");
    expect(ohlc.length).toBeGreaterThan(0);
  });

  it("detects zero price", () => {
    const rows = [row("2024-01-08", 0, 5, -1, 0)];
    const report = validator.validate(rows, "600519", "1d");
    const priceIssues = report.issues.filter((i) => i.category === "price");
    expect(priceIssues.length).toBeGreaterThan(0);
    expect(priceIssues[0]!.severity).toBe("error");
  });

  // ── Price anomalies ───────────────────────────────────────

  it("detects large price moves (>10%)", () => {
    const rows = [
      row("2024-01-08", 100, 105, 98, 100),
      row("2024-01-09", 115, 120, 113, 115), // +15% move
    ];
    const report = validator.validate(rows, "600519", "1d");
    const price = report.issues.filter((i) => i.category === "price" && i.severity === "warning");
    expect(price).toHaveLength(1);
    expect(price[0]!.message).toContain("15.0%");
  });

  it("does not flag normal price moves", () => {
    const rows = [
      row("2024-01-08", 100, 105, 98, 100),
      row("2024-01-09", 102, 107, 100, 105), // +5% — within threshold
    ];
    const report = validator.validate(rows, "600519", "1d");
    const price = report.issues.filter((i) => i.category === "price" && i.severity === "warning");
    expect(price).toHaveLength(0);
  });

  // ── Volume anomalies ──────────────────────────────────────

  it("detects zero volume", () => {
    const rows = [
      row("2024-01-08", 100, 105, 98, 103, 0), // zero volume
    ];
    const report = validator.validate(rows, "600519", "1d");
    const vol = report.issues.filter((i) => i.category === "volume");
    expect(vol).toHaveLength(1);
    expect(vol[0]!.severity).toBe("info");
  });

  it("detects volume spikes", () => {
    // 20 normal bars + 1 spike
    const bars = generateBars("2024-01-01", 21, { baseVol: 10000 });
    bars.push(row("2024-01-22", 120, 125, 118, 123, 500000)); // 50× average
    const report = validator.validate(bars, "600519", "1d");
    const spikes = report.issues.filter((i) => i.category === "volume" && i.severity === "warning");
    expect(spikes.length).toBeGreaterThan(0);
  });

  // ── Duplicates ────────────────────────────────────────────

  it("detects duplicate bars", () => {
    const rows = [
      row("2024-01-08", 100, 105, 98, 103),
      row("2024-01-08", 101, 106, 99, 104), // duplicate date
    ];
    const report = validator.validate(rows, "600519", "1d");
    const dups = report.issues.filter((i) => i.category === "duplicate");
    expect(dups).toHaveLength(1);
    expect(dups[0]!.severity).toBe("warning");
  });

  // ── Staleness ─────────────────────────────────────────────

  it("detects stale data", () => {
    const staleValidator = new DataQualityValidator({
      stalenessThresholdDays: 3,
    });
    const rows = [
      row("2020-01-08", 100, 105, 98, 103), // very old data
    ];
    const report = staleValidator.validate(rows, "600519", "1d");
    const stale = report.issues.filter((i) => i.category === "stale");
    expect(stale).toHaveLength(1);
    expect(stale[0]!.severity).toBe("warning");
  });

  // ── Scoring ───────────────────────────────────────────────

  it("deducts score for errors more than warnings", () => {
    // Error: invalid OHLC
    const badRows = [
      row("2024-01-08", 100, 90, 80, 103), // high < close (error)
    ];
    const report = validator.validate(badRows, "600519", "1d");
    expect(report.score).toBeLessThan(100);
    expect(report.summary.errors).toBeGreaterThan(0);
  });

  // ── Storage-backed validation ─────────────────────────────

  it("validates data from storage", async () => {
    const storage = new InMemoryStorage(null);
    await storage.initialize();

    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-08",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-10",
        interval: "1d",
        open: 103,
        high: 108,
        low: 101,
        close: 106,
        volume: 12000,
      },
    ]);

    const bulkReport = await validator.validateFromStorage(storage, [
      { symbol: "600519", interval: "1d" },
      { symbol: "000001", interval: "1d" }, // no data
    ]);

    expect(bulkReport.symbolCount).toBe(2);
    expect(bulkReport.totalBars).toBe(2);
    expect(bulkReport.reports).toHaveLength(2);
    expect(bulkReport.reports[1]!.barCount).toBe(0);
    // Should detect missing Tuesday
    const r600519 = bulkReport.reports[0]!;
    const missing = r600519.issues.filter((i) => i.category === "missing");
    expect(missing).toHaveLength(1);
    expect(missing[0]!.date).toBe("2024-01-09");

    await storage.close();
  });
});
