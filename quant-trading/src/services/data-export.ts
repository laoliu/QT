/**
 * DataExporter — multi-format file exporter for trading data.
 *
 * Supports exporting trades, orders, positions, equity history, and signals
 * to CSV, Excel (XLSX-compatible TSV), and JSON formats.
 *
 * Features:
 *   - CSV, Excel (tab-separated with BOM), JSON serialization
 *   - Custom field selection (pick which columns to include)
 *   - Time range filtering (start/end date)
 *   - Configurable output directory
 *   - Filename includes timestamp for versioning
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import os from "node:os";

// ── Types ────────────────────────────────────────────────────

export type ExportFormat = "csv" | "excel" | "json";

export type ExportDataType =
  | "trades"
  | "orders"
  | "positions"
  | "equity"
  | "signals"
  | "journal";

export interface ExportFieldConfig {
  /** Only include these fields. If empty/undefined, include all. */
  include?: string[];
  /** Exclude these fields (applied after include). */
  exclude?: string[];
}

export interface ExportTimeRange {
  /** Start date (inclusive), ISO format YYYY-MM-DD or full ISO string. */
  start?: string;
  /** End date (inclusive), ISO format YYYY-MM-DD or full ISO string. */
  end?: string;
}

export interface ExportOptions {
  /** Output format. */
  format: ExportFormat;
  /** Data type label (used in filename). */
  dataType: ExportDataType;
  /** Field filtering configuration. */
  fields?: ExportFieldConfig;
  /** Time range filter. */
  timeRange?: ExportTimeRange;
  /** Field name to use for time range filtering (default: auto-detect). */
  dateField?: string;
  /** Output directory (default: ~/.openclaw/quant-trading/exports). */
  outputDir?: string;
  /** Custom filename (without extension). If not set, auto-generated. */
  filename?: string;
}

export interface ExportResult {
  /** Absolute path to the exported file. */
  filePath: string;
  /** Number of records exported. */
  recordCount: number;
  /** Number of fields per record. */
  fieldCount: number;
  /** File size in bytes. */
  fileSize: number;
  /** Export format used. */
  format: ExportFormat;
}

// ── Constants ───────────────────────────────────────────────

const DEFAULT_EXPORT_DIR = join(os.homedir(), ".openclaw", "quant-trading", "exports");

/** Common date field names used for auto-detection. */
const DATE_FIELD_CANDIDATES = [
  "date",
  "entryTime",
  "exitTime",
  "createdAt",
  "updatedAt",
  "timestamp",
  "exit_time",
  "entry_time",
  "created_at",
];

/** BOM prefix for Excel-compatible UTF-8 files. */
const UTF8_BOM = "\uFEFF";

// ── Core Export Function ────────────────────────────────────

/**
 * Export an array of records to the specified format.
 *
 * @param records - Array of flat objects (each record is a row).
 * @param options - Export configuration.
 * @returns Export result with file path and statistics.
 */
export function exportData(
  records: Record<string, unknown>[],
  options: ExportOptions,
): ExportResult {
  if (records.length === 0) {
    // Write empty file with headers if possible
    const outputPath = resolveOutputPath(options);
    ensureDir(dirname(outputPath));
    writeFileSync(outputPath, "");
    return {
      filePath: outputPath,
      recordCount: 0,
      fieldCount: 0,
      fileSize: 0,
      format: options.format,
    };
  }

  // 1. Filter by time range
  let filtered = filterByTimeRange(records, options.timeRange, options.dateField);

  // 2. Filter fields
  filtered = filterFields(filtered, options.fields);

  // 3. Serialize
  const content = serialize(filtered, options.format);

  // 4. Write
  const outputPath = resolveOutputPath(options);
  ensureDir(dirname(outputPath));
  writeFileSync(outputPath, content, "utf-8");

  const fileSize = Buffer.byteLength(content, "utf-8");

  const fieldCount =
    filtered.length > 0 ? Object.keys(filtered[0] as Record<string, unknown>).length : 0;

  return {
    filePath: outputPath,
    recordCount: filtered.length,
    fieldCount,
    fileSize,
    format: options.format,
  };
}

// ── Serializers ─────────────────────────────────────────────

/**
 * Serialize records to the specified format string.
 */
export function serialize(records: Record<string, unknown>[], format: ExportFormat): string {
  switch (format) {
    case "csv":
      return serializeCsv(records, ",");
    case "excel":
      return UTF8_BOM + serializeCsv(records, "\t");
    case "json":
      return serializeJson(records);
  }
}

/**
 * Serialize records to CSV (or TSV for Excel).
 */
function serializeCsv(records: Record<string, unknown>[], delimiter: string): string {
  if (records.length === 0) return "";

  const headers = Object.keys(records[0] as Record<string, unknown>);
  const lines: string[] = [headers.map((h) => escapeCsvField(String(h), delimiter)).join(delimiter)];

  for (const record of records) {
    const row = headers.map((h) => {
      const val = record[h];
      return escapeCsvField(formatValue(val), delimiter);
    });
    lines.push(row.join(delimiter));
  }

  return lines.join("\n") + "\n";
}

/**
 * Escape a CSV field value — quote if it contains the delimiter, quotes, or newlines.
 */
function escapeCsvField(value: string, delimiter: string): string {
  if (
    value.includes(delimiter) ||
    value.includes('"') ||
    value.includes("\n") ||
    value.includes("\r")
  ) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * Format a value for CSV output.
 */
function formatValue(val: unknown): string {
  if (val === null || val === undefined) return "";
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

/**
 * Serialize records to pretty-printed JSON.
 */
function serializeJson(records: Record<string, unknown>[]): string {
  return JSON.stringify(records, null, 2);
}

// ── Field Filtering ─────────────────────────────────────────

/**
 * Filter record fields based on include/exclude configuration.
 */
export function filterFields(
  records: Record<string, unknown>[],
  config?: ExportFieldConfig,
): Record<string, unknown>[] {
  if (!config) return records;

  const { include, exclude } = config;
  if ((!include || include.length === 0) && (!exclude || exclude.length === 0)) {
    return records;
  }

  return records.map((record) => {
    let keys = Object.keys(record);

    if (include && include.length > 0) {
      keys = include.filter((k) => k in record);
    }

    if (exclude && exclude.length > 0) {
      const excludeSet = new Set(exclude);
      keys = keys.filter((k) => !excludeSet.has(k));
    }

    const filtered: Record<string, unknown> = {};
    for (const k of keys) {
      filtered[k] = record[k];
    }
    return filtered;
  });
}

// ── Time Range Filtering ────────────────────────────────────

/**
 * Detect the date field in a record by checking common field names.
 */
export function detectDateField(record: Record<string, unknown>): string | null {
  for (const candidate of DATE_FIELD_CANDIDATES) {
    if (candidate in record) {
      return candidate;
    }
  }
  return null;
}

/**
 * Normalize a date value to a comparable string (YYYY-MM-DD or full ISO).
 */
function normalizeDate(value: unknown): string | null {
  if (value === null || value === undefined) return null;

  if (typeof value === "number") {
    // Unix timestamp (ms or seconds)
    const ts = value > 1e12 ? value : value * 1000;
    return new Date(ts).toISOString();
  }

  if (typeof value === "string") {
    // Already a date string — return as-is for comparison
    return value;
  }

  return null;
}

/**
 * Filter records by time range.
 */
export function filterByTimeRange(
  records: Record<string, unknown>[],
  timeRange?: ExportTimeRange,
  dateFieldOverride?: string,
): Record<string, unknown>[] {
  if (!timeRange || (!timeRange.start && !timeRange.end)) {
    return records;
  }

  if (records.length === 0) return records;

  const dateField = dateFieldOverride ?? detectDateField(records[0] as Record<string, unknown>);
  if (!dateField) return records;

  const start = timeRange.start ?? "";
  const end = timeRange.end ?? "\uffff"; // Max string for comparison

  return records.filter((record) => {
    const dateVal = normalizeDate(record[dateField]);
    if (!dateVal) return true; // Keep records without a date
    return dateVal >= start && dateVal <= end;
  });
}

// ── Path Resolution ─────────────────────────────────────────

/**
 * Build the output file path from options.
 */
function resolveOutputPath(options: ExportOptions): string {
  const dir = options.outputDir ?? DEFAULT_EXPORT_DIR;
  const ext = formatExtension(options.format);
  const name = options.filename ?? generateFilename(options.dataType);
  return join(dir, `${name}${ext}`);
}

/**
 * Generate a default filename with timestamp.
 */
function generateFilename(dataType: ExportDataType): string {
  const now = new Date();
  const ts = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    "_",
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join("");
  return `${dataType}_${ts}`;
}

/**
 * Get file extension for a format.
 */
function formatExtension(format: ExportFormat): string {
  switch (format) {
    case "csv":
      return ".csv";
    case "excel":
      return ".xlsx.tsv";
    case "json":
      return ".json";
  }
}

/**
 * Ensure a directory exists.
 */
function ensureDir(dir: string): void {
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
}

// ── Convenience Wrappers ────────────────────────────────────

/**
 * Export data to CSV format with optional field and time filtering.
 */
export function exportToCsv(
  records: Record<string, unknown>[],
  dataType: ExportDataType,
  opts?: Partial<Omit<ExportOptions, "format" | "dataType">>,
): ExportResult {
  return exportData(records, { ...opts, format: "csv", dataType });
}

/**
 * Export data to Excel-compatible TSV format.
 */
export function exportToExcel(
  records: Record<string, unknown>[],
  dataType: ExportDataType,
  opts?: Partial<Omit<ExportOptions, "format" | "dataType">>,
): ExportResult {
  return exportData(records, { ...opts, format: "excel", dataType });
}

/**
 * Export data to JSON format.
 */
export function exportToJson(
  records: Record<string, unknown>[],
  dataType: ExportDataType,
  opts?: Partial<Omit<ExportOptions, "format" | "dataType">>,
): ExportResult {
  return exportData(records, { ...opts, format: "json", dataType });
}
