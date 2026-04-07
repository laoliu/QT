import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  exportData,
  exportToCsv,
  exportToExcel,
  exportToJson,
  filterFields,
  filterByTimeRange,
  detectDateField,
  serialize,
  type ExportOptions,
} from "./data-export.js";

// ── Helpers ──────────────────────────────────────────────────

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "data-export-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function sampleTrades() {
  return [
    {
      symbol: "600519",
      side: "buy",
      entryPrice: 1800,
      exitPrice: 1900,
      quantity: 100,
      entryTime: "2025-01-10T09:30:00Z",
      exitTime: "2025-01-15T14:55:00Z",
      netPnl: 10000,
      strategyId: "macd",
    },
    {
      symbol: "000001",
      side: "buy",
      entryPrice: 15,
      exitPrice: 14.5,
      quantity: 1000,
      entryTime: "2025-02-01T09:30:00Z",
      exitTime: "2025-02-05T14:55:00Z",
      netPnl: -500,
      strategyId: "rsi",
    },
    {
      symbol: "300750",
      side: "buy",
      entryPrice: 250,
      exitPrice: 270,
      quantity: 200,
      entryTime: "2025-03-01T09:30:00Z",
      exitTime: "2025-03-10T14:55:00Z",
      netPnl: 4000,
      strategyId: "macd",
    },
  ];
}

// ── serialize ───────────────────────────────────────────────

describe("serialize", () => {
  it("serializes to CSV with proper escaping", () => {
    const records = [
      { name: "test, with comma", value: 42 },
      { name: 'has "quotes"', value: 100 },
    ];
    const csv = serialize(records, "csv");
    expect(csv).toContain("name,value\n");
    expect(csv).toContain('"test, with comma"');
    expect(csv).toContain('"has ""quotes"""');
  });

  it("serializes to Excel TSV with BOM", () => {
    const records = [{ symbol: "600519", price: 1800 }];
    const tsv = serialize(records, "excel");
    expect(tsv.startsWith("\uFEFF")).toBe(true);
    expect(tsv).toContain("symbol\tprice");
    expect(tsv).toContain("600519\t1800");
  });

  it("serializes to JSON", () => {
    const records = [{ a: 1, b: "hello" }];
    const json = serialize(records, "json");
    const parsed = JSON.parse(json);
    expect(parsed).toEqual(records);
  });

  it("handles empty records", () => {
    expect(serialize([], "csv")).toBe("");
    expect(serialize([], "json")).toBe("[]");
  });

  it("handles null and undefined values in CSV", () => {
    const records = [{ a: null, b: undefined, c: 0 }];
    const csv = serialize(records, "csv");
    expect(csv).toContain(",,0");
  });

  it("handles nested objects in CSV", () => {
    const records = [{ name: "test", nested: { x: 1 } }];
    const csv = serialize(records, "csv");
    // nested objects should be JSON-stringified
    expect(csv).toContain('"{""x"":1}"');
  });
});

// ── filterFields ────────────────────────────────────────────

describe("filterFields", () => {
  it("returns all fields when no config", () => {
    const records = [{ a: 1, b: 2, c: 3 }];
    expect(filterFields(records)).toEqual(records);
  });

  it("includes only specified fields", () => {
    const records = [{ a: 1, b: 2, c: 3 }];
    const result = filterFields(records, { include: ["a", "c"] });
    expect(result).toEqual([{ a: 1, c: 3 }]);
  });

  it("excludes specified fields", () => {
    const records = [{ a: 1, b: 2, c: 3 }];
    const result = filterFields(records, { exclude: ["b"] });
    expect(result).toEqual([{ a: 1, c: 3 }]);
  });

  it("applies include then exclude", () => {
    const records = [{ a: 1, b: 2, c: 3, d: 4 }];
    const result = filterFields(records, { include: ["a", "b", "c"], exclude: ["b"] });
    expect(result).toEqual([{ a: 1, c: 3 }]);
  });

  it("ignores non-existent include fields", () => {
    const records = [{ a: 1, b: 2 }];
    const result = filterFields(records, { include: ["a", "z"] });
    expect(result).toEqual([{ a: 1 }]);
  });
});

// ── filterByTimeRange ───────────────────────────────────────

describe("filterByTimeRange", () => {
  it("returns all records when no time range", () => {
    const records = sampleTrades();
    expect(filterByTimeRange(records)).toEqual(records);
  });

  it("filters by start date", () => {
    const records = sampleTrades();
    const result = filterByTimeRange(records, { start: "2025-02-01" });
    expect(result.length).toBe(2);
    expect(result[0].symbol).toBe("000001");
  });

  it("filters by end date", () => {
    const records = sampleTrades();
    const result = filterByTimeRange(records, { end: "2025-02-05T14:55:00Z" }, "entryTime");
    expect(result.length).toBe(2);
    expect(result[0].symbol).toBe("600519");
  });

  it("filters by start and end date", () => {
    const records = sampleTrades();
    const result = filterByTimeRange(
      records,
      { start: "2025-01-15", end: "2025-02-28" },
      "entryTime",
    );
    expect(result.length).toBe(1);
    expect(result[0].symbol).toBe("000001");
  });

  it("uses custom date field", () => {
    const records = sampleTrades();
    const result = filterByTimeRange(records, { start: "2025-02-06" }, "exitTime");
    expect(result.length).toBe(1);
    expect(result[0].symbol).toBe("300750");
  });

  it("keeps records without a date field", () => {
    const records = [{ symbol: "X", value: 1 }];
    const result = filterByTimeRange(records, { start: "2025-01-01" });
    expect(result.length).toBe(1);
  });
});

// ── detectDateField ─────────────────────────────────────────

describe("detectDateField", () => {
  it("detects 'date' field", () => {
    expect(detectDateField({ date: "2025-01-01", value: 1 })).toBe("date");
  });

  it("detects 'entryTime' field", () => {
    expect(detectDateField({ entryTime: "2025-01-01", symbol: "X" })).toBe("entryTime");
  });

  it("detects 'createdAt' field", () => {
    expect(detectDateField({ createdAt: "2025-01-01", id: 1 })).toBe("createdAt");
  });

  it("returns null when no date field found", () => {
    expect(detectDateField({ symbol: "X", value: 1 })).toBeNull();
  });
});

// ── exportData (integration) ────────────────────────────────

describe("exportData", () => {
  it("exports trades to CSV file", () => {
    const result = exportData(sampleTrades(), {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_trades",
    });

    expect(result.filePath).toContain("test_trades.csv");
    expect(result.recordCount).toBe(3);
    expect(result.format).toBe("csv");
    expect(result.fileSize).toBeGreaterThan(0);

    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content).toContain("symbol,side,entryPrice");
    expect(content).toContain("600519");
  });

  it("exports to Excel TSV file", () => {
    const result = exportData(sampleTrades(), {
      format: "excel",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_excel",
    });

    expect(result.filePath).toContain(".xlsx.tsv");
    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content.startsWith("\uFEFF")).toBe(true);
    expect(content).toContain("\t");
  });

  it("exports to JSON file", () => {
    const result = exportData(sampleTrades(), {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_json",
    });

    expect(result.filePath).toContain(".json");
    const content = fs.readFileSync(result.filePath, "utf-8");
    const parsed = JSON.parse(content);
    expect(parsed.length).toBe(3);
    expect(parsed[0].symbol).toBe("600519");
  });

  it("applies field filtering", () => {
    const result = exportData(sampleTrades(), {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_fields",
      fields: { include: ["symbol", "netPnl"] },
    });

    const content = JSON.parse(fs.readFileSync(result.filePath, "utf-8"));
    expect(Object.keys(content[0])).toEqual(["symbol", "netPnl"]);
    expect(result.fieldCount).toBe(2);
  });

  it("applies time range filtering", () => {
    const result = exportData(sampleTrades(), {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_time",
      timeRange: { start: "2025-02-01", end: "2025-02-28" },
      dateField: "entryTime",
    });

    const content = JSON.parse(fs.readFileSync(result.filePath, "utf-8"));
    expect(content.length).toBe(1);
    expect(content[0].symbol).toBe("000001");
    expect(result.recordCount).toBe(1);
  });

  it("combines field and time range filtering", () => {
    const result = exportData(sampleTrades(), {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_combined",
      fields: { include: ["symbol", "netPnl", "entryTime"] },
      timeRange: { start: "2025-01-01", end: "2025-01-31" },
      dateField: "entryTime",
    });

    expect(result.recordCount).toBe(1);
    expect(result.fieldCount).toBe(3);
  });

  it("handles empty records", () => {
    const result = exportData([], {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "test_empty",
    });

    expect(result.recordCount).toBe(0);
    expect(result.fileSize).toBe(0);
  });

  it("auto-generates filename with timestamp", () => {
    const result = exportData(sampleTrades(), {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
    });

    expect(result.filePath).toMatch(/trades_\d{8}_\d{6}\.csv$/);
  });
});

// ── Convenience wrappers ────────────────────────────────────

describe("convenience wrappers", () => {
  it("exportToCsv works", () => {
    const result = exportToCsv(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "wrap_csv",
    });
    expect(result.format).toBe("csv");
    expect(result.filePath).toContain(".csv");
  });

  it("exportToExcel works", () => {
    const result = exportToExcel(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "wrap_excel",
    });
    expect(result.format).toBe("excel");
  });

  it("exportToJson works", () => {
    const result = exportToJson(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "wrap_json",
    });
    expect(result.format).toBe("json");
  });

  it("convenience wrappers pass through options", () => {
    const result = exportToCsv(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "wrap_opts",
      fields: { include: ["symbol", "netPnl"] },
      timeRange: { start: "2025-02-01" },
      dateField: "entryTime",
    });
    expect(result.recordCount).toBe(2);
    expect(result.fieldCount).toBe(2);
  });
});
