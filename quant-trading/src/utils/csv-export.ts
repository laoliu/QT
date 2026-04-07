/**
 * CSV export utility — converts structured data to CSV format.
 *
 * Handles proper escaping of commas, quotes, and newlines per RFC 4180.
 * Supports optional BOM prefix for Excel compatibility with CJK characters.
 */

export type CsvColumn<T = Record<string, unknown>> = {
  /** Column header label */
  label: string;
  /** Key on the row object, or accessor function */
  key: keyof T | ((row: T) => unknown);
};

/** Escape a cell value for CSV (RFC 4180). */
function escapeCell(value: unknown): string {
  if (value == null) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n") || str.includes("\r")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Convert an array of objects to CSV string.
 *
 * @param rows   Data rows
 * @param columns Column definitions (label + key/accessor)
 * @param opts   Options: `bom` prepends UTF-8 BOM for Excel CJK support (default: true)
 */
export function toCSV<T extends Record<string, unknown>>(
  rows: T[],
  columns: CsvColumn<T>[],
  opts?: { bom?: boolean },
): string {
  const bom = opts?.bom !== false ? "\uFEFF" : "";
  const header = columns.map((c) => escapeCell(c.label)).join(",");
  const body = rows.map((row) =>
    columns
      .map((col) => {
        const val = typeof col.key === "function" ? col.key(row) : row[col.key as string];
        return escapeCell(val);
      })
      .join(","),
  );
  return bom + [header, ...body].join("\r\n") + "\r\n";
}
