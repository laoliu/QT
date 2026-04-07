/**
 * DataExportService — multi-format file exporter for trading data.
 *
 * Supports:
 *   - CSV, Excel (XLSX), JSON serialization
 *   - Custom field filtering (pick which columns to include)
 *   - Time range filtering (startDate / endDate)
 *   - Multiple data sources: portfolio, trades, backtest, journal, equity, signals
 *
 * Excel output uses a lightweight XML-based SpreadsheetML format
 * (no external dependency required).
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

// ── Types ────────────────────────────────────────────────────

export type ExportFormat = "csv" | "excel" | "json";

export interface ExportOptions {
  /** Output format (default: csv) */
  format: ExportFormat;
  /** Only include these fields (columns). If empty/undefined, include all. */
  fields?: string[];
  /** Filter rows: only include records on or after this date (YYYY-MM-DD or ISO) */
  startDate?: string;
  /** Filter rows: only include records on or before this date (YYYY-MM-DD or ISO) */
  endDate?: string;
  /** Output file path. If not provided, auto-generated in dataDir/exports/ */
  outputPath?: string;
}

export interface ExportResult {
  /** Absolute path to the exported file */
  filePath: string;
  /** Number of rows exported */
  rowCount: number;
  /** Number of columns exported */
  columnCount: number;
  /** File size in bytes */
  fileSize: number;
  /** Export format used */
  format: ExportFormat;
}

// ── Helpers ──────────────────────────────────────────────────

/** Flatten a nested object to dot-notation keys. */
function flattenObject(obj: Record<string, unknown>, prefix = ""): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (value != null && typeof value === "object" && !Array.isArray(value)) {
      Object.assign(result, flattenObject(value as Record<string, unknown>, fullKey));
    } else {
      result[fullKey] = value;
    }
  }
  return result;
}

/** Normalize a date string to YYYY-MM-DD for comparison. */
function normalizeDate(d: string): string {
  return d.slice(0, 10);
}

/**
 * Extract a date value from a record for time-range filtering.
 * Tries common date field names in priority order.
 */
function extractDate(row: Record<string, unknown>): string | null {
  for (const key of [
    "date",
    "entryTime",
    "exitTime",
    "createdAt",
    "updatedAt",
    "timestamp",
  ]) {
    const val = row[key];
    if (typeof val === "string" && val.length >= 10) {
      return normalizeDate(val);
    }
    if (typeof val === "number" && val > 1e12) {
      return new Date(val).toISOString().slice(0, 10);
    }
  }
  return null;
}

/** Escape a CSV cell value. */
function csvEscape(value: unknown): string {
  if (value == null) return "";
  const str = typeof value === "object" ? JSON.stringify(value) : String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

// ── Serializers ─────────────────────────────────────────────

function serializeCSV(rows: Record<string, unknown>[], columns: string[]): string {
  const lines: string[] = [];
  lines.push(columns.map(csvEscape).join(","));
  for (const row of rows) {
    lines.push(columns.map((col) => csvEscape(row[col])).join(","));
  }
  return lines.join("\n") + "\n";
}

function serializeJSON(rows: Record<string, unknown>[], columns: string[]): string {
  const filtered = rows.map((row) => {
    const out: Record<string, unknown> = {};
    for (const col of columns) {
      out[col] = row[col] ?? null;
    }
    return out;
  });
  return JSON.stringify(filtered, null, 2);
}

/**
 * Serialize to SpreadsheetML XML (Office Open XML simplified).
 * Produces a valid .xlsx-compatible XML that Excel/LibreOffice/WPS can open.
 */
function serializeExcel(rows: Record<string, unknown>[], columns: string[]): string {
  const xmlEscape = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const lines: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<?mso-application progid="Excel.Sheet"?>',
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"',
    '  xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">',
    '  <Styles>',
    '    <Style ss:ID="header">',
    '      <Font ss:Bold="1"/>',
    '      <Interior ss:Color="#D9E1F2" ss:Pattern="Solid"/>',
    '    </Style>',
    '    <Style ss:ID="num"><NumberFormat ss:Format="0.00"/></Style>',
    '  </Styles>',
    '  <Worksheet ss:Name="Export">',
    `    <Table ss:ExpandedColumnCount="${columns.length}" ss:ExpandedRowCount="${rows.length + 1}">`,
  ];

  // Header row
  lines.push("      <Row>");
  for (const col of columns) {
    lines.push(`        <Cell ss:StyleID="header"><Data ss:Type="String">${xmlEscape(col)}</Data></Cell>`);
  }
  lines.push("      </Row>");

  // Data rows
  for (const row of rows) {
    lines.push("      <Row>");
    for (const col of columns) {
      const val = row[col];
      if (val == null) {
        lines.push('        <Cell><Data ss:Type="String"></Data></Cell>');
      } else if (typeof val === "number") {
        lines.push(`        <Cell ss:StyleID="num"><Data ss:Type="Number">${val}</Data></Cell>`);
      } else {
        lines.push(`        <Cell><Data ss:Type="String">${xmlEscape(String(val))}</Data></Cell>`);
      }
    }
    lines.push("      </Row>");
  }

  lines.push("    </Table>");
  lines.push("  </Worksheet>");
  lines.push("</Workbook>");
  return lines.join("\n");
}

// ── Export Service ───────────────────────────────────────────

export class DataExportService {
  private readonly exportDir: string;

  constructor(dataDir: string) {
    this.exportDir = join(dataDir, "exports");
  }

  /**
   * Export an array of data records to a file.
   *
   * @param data  Array of objects (any shape — will be flattened)
   * @param opts  Export options (format, fields, date range, output path)
   * @returns     ExportResult with file path and metadata
   */
  export(data: Record<string, unknown>[], opts: ExportOptions): ExportResult {
    if (!data.length) {
      throw new Error("没有数据可导出");
    }

    // 1. Flatten nested objects
    let rows = data.map((d) => flattenObject(d));

    // 2. Time-range filtering
    if (opts.startDate || opts.endDate) {
      const start = opts.startDate ? normalizeDate(opts.startDate) : null;
      const end = opts.endDate ? normalizeDate(opts.endDate) : null;
      rows = rows.filter((row) => {
        const d = extractDate(row);
        if (!d) return true; // keep rows without a date field
        if (start && d < start) return false;
        if (end && d > end) return false;
        return true;
      });
    }

    if (!rows.length) {
      throw new Error("过滤后没有数据可导出（检查时间范围）");
    }

    // 3. Determine columns
    let columns: string[];
    if (opts.fields && opts.fields.length > 0) {
      // Validate requested fields exist
      const allKeys = new Set<string>();
      for (const row of rows) {
        for (const k of Object.keys(row)) allKeys.add(k);
      }
      columns = opts.fields.filter((f) => allKeys.has(f));
      if (!columns.length) {
        throw new Error(
          `指定的字段均不存在。可用字段: ${[...allKeys].slice(0, 20).join(", ")}`,
        );
      }
    } else {
      // Collect all unique keys, preserving insertion order
      const keySet = new Set<string>();
      for (const row of rows) {
        for (const k of Object.keys(row)) keySet.add(k);
      }
      columns = [...keySet];
    }

    // 4. Serialize
    const format = opts.format ?? "csv";
    let content: string;
    let ext: string;
    switch (format) {
      case "csv":
        content = serializeCSV(rows, columns);
        ext = ".csv";
        break;
      case "json":
        content = serializeJSON(rows, columns);
        ext = ".json";
        break;
      case "excel":
        content = serializeExcel(rows, columns);
        ext = ".xml"; // SpreadsheetML, opens in Excel
        break;
      default:
        throw new Error(`不支持的格式: ${format}`);
    }

    // 5. Write file
    const filePath =
      opts.outputPath ??
      join(
        this.exportDir,
        `export_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}${ext}`,
      );
    const dir = dirname(filePath);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(filePath, content, "utf-8");

    const fileSize = Buffer.byteLength(content, "utf-8");

    return {
      filePath,
      rowCount: rows.length,
      columnCount: columns.length,
      fileSize,
      format,
    };
  }

  /** List available fields from a dataset (for UI/autocomplete). */
  getAvailableFields(data: Record<string, unknown>[]): string[] {
    const keys = new Set<string>();
    for (const row of data) {
      for (const k of Object.keys(flattenObject(row))) {
        keys.add(k);
      }
    }
    return [...keys].sort();
  }
}
