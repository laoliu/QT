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
  type ExportFieldConfig,
  type ExportTimeRange,
} from "./data-export.js";

// ── Test Helpers ────────────────────────────────────────────

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "data-export-comprehensive-"));
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

// ── serialize: CSV edge cases ──────────────────────────────

describe("serialize — CSV edge cases", () => {
  it("escapes fields containing newlines", () => {
    const records = [{ note: "line1\nline2", value: 1 }];
    const csv = serialize(records, "csv");
    expect(csv).toContain('"line1\nline2"');
  });

  it("escapes fields containing carriage returns", () => {
    const records = [{ note: "line1\rline2", value: 1 }];
    const csv = serialize(records, "csv");
    expect(csv).toContain('"line1\rline2"');
  });

  it("escapes fields containing both delimiter and quotes", () => {
    const records = [{ note: 'say "hello, world"', value: 1 }];
    const csv = serialize(records, "csv");
    // Should escape quotes to "" and wrap in quotes
    expect(csv).toContain('"say ""hello, world"""');
  });

  it("does not escape plain alphanumeric fields", () => {
    const records = [{ name: "simple", value: 42 }];
    const csv = serialize(records, "csv");
    const lines = csv.split("\n");
    expect(lines[1]).toBe("simple,42");
  });

  it("handles boolean values", () => {
    const records = [{ active: true, deleted: false }];
    const csv = serialize(records, "csv");
    expect(csv).toContain("true,false");
  });

  it("handles numeric zero and negative values", () => {
    const records = [{ zero: 0, negative: -100, float: -0.5 }];
    const csv = serialize(records, "csv");
    expect(csv).toContain("0,-100,-0.5");
  });

  it("handles empty string values", () => {
    const records = [{ a: "", b: "x" }];
    const csv = serialize(records, "csv");
    expect(csv).toContain(",x");
  });

  it("handles array values by JSON-stringifying", () => {
    const records = [{ tags: [1, 2, 3], name: "test" }];
    const csv = serialize(records, "csv");
    // Array should be JSON stringified and then CSV-escaped
    expect(csv).toContain('"[1,2,3]"');
  });

  it("CSV ends with a newline", () => {
    const records = [{ a: 1 }];
    const csv = serialize(records, "csv");
    expect(csv.endsWith("\n")).toBe(true);
  });

  it("CSV header order matches first record key order", () => {
    const records = [{ z: 1, a: 2, m: 3 }];
    const csv = serialize(records, "csv");
    const headerLine = csv.split("\n")[0];
    expect(headerLine).toBe("z,a,m");
  });
});

// ── serialize: Excel TSV edge cases ────────────────────────

describe("serialize — Excel TSV edge cases", () => {
  it("uses tab delimiter instead of comma", () => {
    const records = [{ a: 1, b: 2 }];
    const tsv = serialize(records, "excel");
    // Remove BOM for checking
    const content = tsv.slice(1);
    expect(content).toContain("a\tb");
    expect(content).toContain("1\t2");
  });

  it("escapes tab characters within fields", () => {
    const records = [{ note: "has\ttab", value: 1 }];
    const tsv = serialize(records, "excel");
    // Field containing tab should be quoted
    expect(tsv).toContain('"has\ttab"');
  });

  it("Excel format with comma in field does not quote (comma is not delimiter)", () => {
    const records = [{ note: "hello, world", value: 1 }];
    const tsv = serialize(records, "excel");
    // In TSV mode, comma is not the delimiter, so it should NOT be quoted
    // unless it also contains tab/quote/newline
    expect(tsv).toContain("hello, world");
    // Should NOT be wrapped in quotes
    expect(tsv).not.toContain('"hello, world"');
  });

  it("BOM is exactly U+FEFF", () => {
    const records = [{ a: 1 }];
    const tsv = serialize(records, "excel");
    expect(tsv.charCodeAt(0)).toBe(0xfeff);
  });
});

// ── serialize: JSON edge cases ─────────────────────────────

describe("serialize — JSON edge cases", () => {
  it("produces pretty-printed JSON with 2-space indent", () => {
    const records = [{ a: 1 }];
    const json = serialize(records, "json");
    expect(json).toBe(JSON.stringify(records, null, 2));
  });

  it("preserves null values in JSON", () => {
    const records = [{ a: null, b: 1 }];
    const json = serialize(records, "json");
    const parsed = JSON.parse(json);
    expect(parsed[0].a).toBeNull();
  });

  it("preserves nested objects in JSON", () => {
    const records = [{ name: "test", nested: { x: 1, y: [2, 3] } }];
    const json = serialize(records, "json");
    const parsed = JSON.parse(json);
    expect(parsed[0].nested).toEqual({ x: 1, y: [2, 3] });
  });

  it("handles special characters in JSON strings", () => {
    const records = [{ text: 'line1\nline2\t"quoted"' }];
    const json = serialize(records, "json");
    const parsed = JSON.parse(json);
    expect(parsed[0].text).toBe('line1\nline2\t"quoted"');
  });

  it("handles empty array", () => {
    const json = serialize([], "json");
    expect(json).toBe("[]");
  });

  it("handles unicode characters", () => {
    const records = [{ name: "中文测试", emoji: "🚀" }];
    const json = serialize(records, "json");
    const parsed = JSON.parse(json);
    expect(parsed[0].name).toBe("中文测试");
    expect(parsed[0].emoji).toBe("🚀");
  });
});

// ── filterFields: edge cases ───────────────────────────────

describe("filterFields — edge cases", () => {
  it("returns original records when config is undefined", () => {
    const records = [{ a: 1, b: 2 }];
    expect(filterFields(records, undefined)).toEqual(records);
  });

  it("returns original records when include and exclude are both empty arrays", () => {
    const records = [{ a: 1, b: 2 }];
    const result = filterFields(records, { include: [], exclude: [] });
    expect(result).toEqual(records);
  });

  it("returns empty objects when all fields are excluded", () => {
    const records = [{ a: 1, b: 2 }];
    const result = filterFields(records, { exclude: ["a", "b"] });
    expect(result).toEqual([{}]);
  });

  it("returns empty objects when include contains only non-existent fields", () => {
    const records = [{ a: 1, b: 2 }];
    const result = filterFields(records, { include: ["x", "y", "z"] });
    expect(result).toEqual([{}]);
  });

  it("preserves include field order", () => {
    const records = [{ a: 1, b: 2, c: 3 }];
    const result = filterFields(records, { include: ["c", "a"] });
    expect(Object.keys(result[0])).toEqual(["c", "a"]);
  });

  it("handles multiple records with different field sets consistently", () => {
    // All records filtered using include — only existing keys are retained
    const records = [
      { a: 1, b: 2, c: 3 },
      { a: 10, b: 20, d: 40 }, // no 'c' field
    ];
    const result = filterFields(records, { include: ["a", "c"] });
    expect(result[0]).toEqual({ a: 1, c: 3 });
    expect(result[1]).toEqual({ a: 10 }); // 'c' not in record, skipped
  });

  it("exclude non-existent fields has no effect", () => {
    const records = [{ a: 1, b: 2 }];
    const result = filterFields(records, { exclude: ["z"] });
    expect(result).toEqual([{ a: 1, b: 2 }]);
  });

  it("works with single record", () => {
    const records = [{ name: "test", value: 42, extra: "remove" }];
    const result = filterFields(records, { include: ["name", "value"] });
    expect(result).toEqual([{ name: "test", value: 42 }]);
  });

  it("handles empty records array", () => {
    const result = filterFields([], { include: ["a"] });
    expect(result).toEqual([]);
  });
});

// ── filterByTimeRange: edge cases ──────────────────────────

describe("filterByTimeRange — edge cases", () => {
  it("returns all records when timeRange is undefined", () => {
    const records = sampleTrades();
    expect(filterByTimeRange(records, undefined)).toEqual(records);
  });

  it("returns all records when both start and end are undefined", () => {
    const records = sampleTrades();
    expect(filterByTimeRange(records, { start: undefined, end: undefined })).toEqual(records);
  });

  it("returns all records when timeRange is empty object", () => {
    const records = sampleTrades();
    expect(filterByTimeRange(records, {})).toEqual(records);
  });

  it("returns empty array when filtering empty records", () => {
    const result = filterByTimeRange([], { start: "2025-01-01" });
    expect(result).toEqual([]);
  });

  it("returns all records when no date field detected and no override", () => {
    const records = [
      { symbol: "X", value: 1 },
      { symbol: "Y", value: 2 },
    ];
    const result = filterByTimeRange(records, { start: "2025-01-01" });
    expect(result).toEqual(records);
  });

  it("filters with start only — includes records on or after start", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: "2025-01-15", v: 2 },
      { date: "2025-02-01", v: 3 },
    ];
    const result = filterByTimeRange(records, { start: "2025-01-15" });
    expect(result.length).toBe(2);
    expect(result[0]).toEqual({ date: "2025-01-15", v: 2 });
  });

  it("filters with end only — includes records on or before end", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: "2025-01-15", v: 2 },
      { date: "2025-02-01", v: 3 },
    ];
    const result = filterByTimeRange(records, { end: "2025-01-15" });
    expect(result.length).toBe(2);
    expect(result[1]).toEqual({ date: "2025-01-15", v: 2 });
  });

  it("handles Unix timestamps in seconds", () => {
    // 2025-01-15T00:00:00Z = 1736899200 seconds
    const records = [
      { timestamp: 1736899200, v: 1 }, // 2025-01-15
      { timestamp: 1738368000, v: 2 }, // 2025-02-01
    ];
    const result = filterByTimeRange(records, { start: "2025-01-20" }, "timestamp");
    // Unix timestamp in seconds will be converted via normalizeDate
    // 1736899200 * 1000 -> 2025-01-15T00:00:00.000Z (before 2025-01-20)
    expect(result.length).toBe(1);
  });

  it("handles Unix timestamps in milliseconds", () => {
    // 2025-01-15T00:00:00Z = 1736899200000 ms
    const records = [
      { timestamp: 1736899200000, v: 1 }, // 2025-01-15
      { timestamp: 1738368000000, v: 2 }, // 2025-02-01
    ];
    const result = filterByTimeRange(records, { end: "2025-01-20" }, "timestamp");
    expect(result.length).toBe(1);
    expect(result[0].v).toBe(1);
  });

  it("keeps records with null date values", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: null, v: 2 },
      { date: "2025-03-01", v: 3 },
    ];
    const result = filterByTimeRange(records, { start: "2025-02-01" });
    // Records with null date should be kept
    expect(result.length).toBe(2);
    expect(result.map((r) => r.v)).toEqual([2, 3]);
  });

  it("keeps records with undefined date values", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: undefined, v: 2 },
      { date: "2025-03-01", v: 3 },
    ];
    const result = filterByTimeRange(records, { start: "2025-02-01" });
    expect(result.length).toBe(2);
  });

  it("dateFieldOverride takes priority over auto-detect", () => {
    const records = [
      { date: "2025-01-01", createdAt: "2025-06-01", v: 1 },
      { date: "2025-03-01", createdAt: "2025-06-01", v: 2 },
    ];
    // Auto-detect would find "date", but override says "createdAt"
    const result = filterByTimeRange(records, { start: "2025-05-01" }, "createdAt");
    // Both have createdAt "2025-06-01" which is >= "2025-05-01"
    expect(result.length).toBe(2);
  });

  it("exact boundary match is inclusive for both start and end", () => {
    const records = [
      { date: "2025-01-15", v: 1 },
      { date: "2025-02-15", v: 2 },
      { date: "2025-03-15", v: 3 },
    ];
    const result = filterByTimeRange(records, { start: "2025-01-15", end: "2025-03-15" });
    expect(result.length).toBe(3);
  });

  it("filters out all records when range excludes everything", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: "2025-02-01", v: 2 },
    ];
    const result = filterByTimeRange(records, { start: "2025-06-01", end: "2025-06-30" });
    expect(result.length).toBe(0);
  });
});

// ── detectDateField: comprehensive ─────────────────────────

describe("detectDateField — comprehensive", () => {
  it("detects 'date' as highest priority", () => {
    // 'date' comes first in candidate list
    expect(detectDateField({ date: "2025-01-01", entryTime: "2025-01-02" })).toBe("date");
  });

  it("detects 'updatedAt'", () => {
    expect(detectDateField({ updatedAt: "2025-01-01", value: 1 })).toBe("updatedAt");
  });

  it("detects 'timestamp'", () => {
    expect(detectDateField({ timestamp: 1234567890, id: 1 })).toBe("timestamp");
  });

  it("detects 'exit_time' (snake_case)", () => {
    expect(detectDateField({ exit_time: "2025-01-01", symbol: "X" })).toBe("exit_time");
  });

  it("detects 'entry_time' (snake_case)", () => {
    expect(detectDateField({ entry_time: "2025-01-01", symbol: "X" })).toBe("entry_time");
  });

  it("detects 'created_at' (snake_case)", () => {
    expect(detectDateField({ created_at: "2025-01-01", id: 1 })).toBe("created_at");
  });

  it("returns null for record with no matching date fields", () => {
    expect(detectDateField({ id: 1, name: "test", value: 42 })).toBeNull();
  });

  it("returns null for empty record", () => {
    expect(detectDateField({})).toBeNull();
  });

  it("detects 'exitTime' field", () => {
    expect(detectDateField({ exitTime: "2025-01-01", symbol: "X" })).toBe("exitTime");
  });
});

// ── exportData: comprehensive integration ──────────────────

describe("exportData — comprehensive integration", () => {
  it("creates nested output directories", () => {
    const nestedDir = path.join(tmpDir, "deep", "nested", "dir");
    const result = exportData([{ a: 1 }], {
      format: "json",
      dataType: "trades",
      outputDir: nestedDir,
      filename: "nested_test",
    });

    expect(fs.existsSync(result.filePath)).toBe(true);
    expect(result.filePath).toContain(path.join("deep", "nested", "dir"));
  });

  it("correct file extension for CSV", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "ext_csv",
    });
    expect(result.filePath).toMatch(/\.csv$/);
  });

  it("correct file extension for Excel", () => {
    const result = exportData([{ a: 1 }], {
      format: "excel",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "ext_excel",
    });
    expect(result.filePath).toMatch(/\.xlsx\.tsv$/);
  });

  it("correct file extension for JSON", () => {
    const result = exportData([{ a: 1 }], {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "ext_json",
    });
    expect(result.filePath).toMatch(/\.json$/);
  });

  it("empty records produce empty file", () => {
    const result = exportData([], {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "empty_json",
    });
    expect(result.recordCount).toBe(0);
    expect(result.fieldCount).toBe(0);
    expect(result.fileSize).toBe(0);
    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content).toBe("");
  });

  it("fileSize reflects actual byte count (including multi-byte chars)", () => {
    const records = [{ name: "中文", value: 1 }];
    const result = exportData(records, {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "unicode_size",
    });
    const actual = fs.statSync(result.filePath).size;
    expect(result.fileSize).toBe(actual);
  });

  it("fieldCount reflects filtered field count", () => {
    const records = [{ a: 1, b: 2, c: 3, d: 4 }];
    const result = exportData(records, {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "field_count",
      fields: { include: ["a", "b"] },
    });
    expect(result.fieldCount).toBe(2);
  });

  it("recordCount reflects time-filtered count", () => {
    const records = [
      { date: "2025-01-01", v: 1 },
      { date: "2025-06-01", v: 2 },
      { date: "2025-12-01", v: 3 },
    ];
    const result = exportData(records, {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "record_count",
      timeRange: { start: "2025-05-01" },
    });
    expect(result.recordCount).toBe(2);
  });

  it("auto-generated filename includes dataType", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "orders",
      outputDir: tmpDir,
    });
    expect(result.filePath).toMatch(/orders_\d{8}_\d{6}\.csv$/);
  });

  it("auto-generated filename for positions", () => {
    const result = exportData([{ a: 1 }], {
      format: "json",
      dataType: "positions",
      outputDir: tmpDir,
    });
    expect(result.filePath).toMatch(/positions_\d{8}_\d{6}\.json$/);
  });

  it("auto-generated filename for equity", () => {
    const result = exportData([{ a: 1 }], {
      format: "excel",
      dataType: "equity",
      outputDir: tmpDir,
    });
    expect(result.filePath).toMatch(/equity_\d{8}_\d{6}\.xlsx\.tsv$/);
  });

  it("auto-generated filename for signals", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "signals",
      outputDir: tmpDir,
    });
    expect(result.filePath).toMatch(/signals_\d{8}_\d{6}\.csv$/);
  });

  it("time range + field filtering together produces correct subset", () => {
    const records = [
      { date: "2025-01-01", symbol: "A", price: 100, volume: 500 },
      { date: "2025-02-01", symbol: "B", price: 200, volume: 600 },
      { date: "2025-03-01", symbol: "C", price: 300, volume: 700 },
    ];
    const result = exportData(records, {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "combined_filter",
      timeRange: { start: "2025-01-15", end: "2025-02-15" },
      fields: { include: ["symbol", "price"] },
    });

    const content = JSON.parse(fs.readFileSync(result.filePath, "utf-8"));
    expect(content).toEqual([{ symbol: "B", price: 200 }]);
    expect(result.recordCount).toBe(1);
    expect(result.fieldCount).toBe(2);
  });

  it("field exclude filtering writes correct CSV", () => {
    const records = [
      { a: 1, b: 2, c: 3 },
      { a: 10, b: 20, c: 30 },
    ];
    const result = exportData(records, {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "exclude_csv",
      fields: { exclude: ["b"] },
    });

    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content).toContain("a,c");
    expect(content).not.toContain(",2");
    expect(content).not.toContain(",20");
  });

  it("single record export", () => {
    const records = [{ symbol: "AAPL", price: 150 }];
    const result = exportData(records, {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "single",
    });
    expect(result.recordCount).toBe(1);
    const content = fs.readFileSync(result.filePath, "utf-8");
    const lines = content.trim().split("\n");
    expect(lines.length).toBe(2); // header + 1 data row
  });

  it("large batch export (100 records)", () => {
    const records = Array.from({ length: 100 }, (_, i) => ({
      id: i,
      value: Math.random(),
      date: `2025-${String(Math.floor(i / 10) + 1).padStart(2, "0")}-01`,
    }));
    const result = exportData(records, {
      format: "json",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "large_batch",
    });
    expect(result.recordCount).toBe(100);
    const content = JSON.parse(fs.readFileSync(result.filePath, "utf-8"));
    expect(content.length).toBe(100);
  });

  it("writes valid UTF-8 CSV file with special chars", () => {
    const records = [
      { name: "中国A股", value: 100 },
      { name: "日本株式", value: 200 },
    ];
    const result = exportData(records, {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "utf8_csv",
    });
    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content).toContain("中国A股");
    expect(content).toContain("日本株式");
  });

  it("Excel export file is readable and contains BOM", () => {
    const records = [{ symbol: "600519", price: 1800 }];
    const result = exportData(records, {
      format: "excel",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "bom_check",
    });
    const buf = fs.readFileSync(result.filePath);
    // BOM in UTF-8 is EF BB BF
    expect(buf[0]).toBe(0xef);
    expect(buf[1]).toBe(0xbb);
    expect(buf[2]).toBe(0xbf);
  });
});

// ── Convenience wrappers: comprehensive ────────────────────

describe("convenience wrappers — comprehensive", () => {
  it("exportToCsv with field filtering", () => {
    const result = exportToCsv(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "conv_csv_fields",
      fields: { include: ["symbol", "netPnl"] },
    });
    expect(result.format).toBe("csv");
    expect(result.fieldCount).toBe(2);
    const content = fs.readFileSync(result.filePath, "utf-8");
    expect(content.split("\n")[0]).toBe("symbol,netPnl");
  });

  it("exportToExcel with time range filtering", () => {
    const result = exportToExcel(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "conv_excel_time",
      timeRange: { start: "2025-02-01" },
      dateField: "entryTime",
    });
    expect(result.format).toBe("excel");
    expect(result.recordCount).toBe(2);
  });

  it("exportToJson with combined filtering", () => {
    const result = exportToJson(sampleTrades(), "trades", {
      outputDir: tmpDir,
      filename: "conv_json_combo",
      fields: { include: ["symbol", "entryTime", "netPnl"], exclude: ["entryTime"] },
      timeRange: { end: "2025-02-28" },
      dateField: "entryTime",
    });
    expect(result.format).toBe("json");
    const content = JSON.parse(fs.readFileSync(result.filePath, "utf-8"));
    // Should have only symbol and netPnl (entryTime excluded)
    expect(Object.keys(content[0])).toEqual(["symbol", "netPnl"]);
    // Should have 2 records (Jan and Feb, filtered by end date on entryTime)
    expect(content.length).toBe(2);
  });

  it("exportToCsv with empty records", () => {
    const result = exportToCsv([], "trades", {
      outputDir: tmpDir,
      filename: "conv_empty",
    });
    expect(result.recordCount).toBe(0);
    expect(result.fileSize).toBe(0);
  });

  it("exportToJson for different data types", () => {
    const signalRecords = [
      { signalId: "s1", type: "entry", timestamp: "2025-01-01" },
    ];
    const result = exportToJson(signalRecords, "signals", {
      outputDir: tmpDir,
      filename: "conv_signals",
    });
    expect(result.format).toBe("json");
    expect(result.filePath).toContain("conv_signals.json");
  });

  it("exportToExcel for equity data type", () => {
    const equityRecords = [
      { date: "2025-01-01", equity: 100000, drawdown: 0 },
      { date: "2025-01-02", equity: 101000, drawdown: 0 },
    ];
    const result = exportToExcel(equityRecords, "equity", {
      outputDir: tmpDir,
      filename: "conv_equity",
    });
    expect(result.format).toBe("excel");
    expect(result.recordCount).toBe(2);
  });
});

// ── Export result structure validation ──────────────────────

describe("ExportResult structure", () => {
  it("contains all required fields", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "result_check",
    });

    expect(result).toHaveProperty("filePath");
    expect(result).toHaveProperty("recordCount");
    expect(result).toHaveProperty("fieldCount");
    expect(result).toHaveProperty("fileSize");
    expect(result).toHaveProperty("format");
  });

  it("filePath is absolute", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "abs_path",
    });
    expect(path.isAbsolute(result.filePath)).toBe(true);
  });

  it("fileSize is a positive number for non-empty exports", () => {
    const result = exportData([{ a: 1 }], {
      format: "csv",
      dataType: "trades",
      outputDir: tmpDir,
      filename: "size_check",
    });
    expect(result.fileSize).toBeGreaterThan(0);
    expect(typeof result.fileSize).toBe("number");
  });

  it("format field matches requested format", () => {
    for (const format of ["csv", "excel", "json"] as const) {
      const result = exportData([{ a: 1 }], {
        format,
        dataType: "trades",
        outputDir: tmpDir,
        filename: `fmt_${format}`,
      });
      expect(result.format).toBe(format);
    }
  });
});
