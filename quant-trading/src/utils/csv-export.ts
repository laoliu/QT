/**
 * CSV export utility — generic typed CSV serializer.
 */

export interface CsvColumn<T> {
  /** Column header label. */
  label: string;
  /** Key to read from each row object. */
  key: keyof T & string;
}

/**
 * Convert an array of row objects to a CSV string using explicit column definitions.
 */
export function toCSV<T extends Record<string, unknown>>(
  rows: T[],
  columns: CsvColumn<T>[],
): string {
  const header = columns.map((c) => escapeCsvField(c.label)).join(",");
  const body = rows
    .map((row) =>
      columns.map((c) => escapeCsvField(String(row[c.key] ?? ""))).join(","),
    )
    .join("\n");
  return `${header}\n${body}`;
}

function escapeCsvField(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}
