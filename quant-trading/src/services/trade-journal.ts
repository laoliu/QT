/**
 * TradeJournal — persistent trade notes and annotations.
 *
 * Allows users to:
 *   - Attach notes to specific trades (by tradeId or symbol+date)
 *   - Tag trades with labels (e.g. "止损", "追涨", "波段")
 *   - Rate trade quality (1-5 stars)
 *   - Search and review notes
 *
 * Used during daily review / 复盘 to record lessons learned.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export interface TradeNote {
  id: string;
  /** Trade ID (from TradeRecord), or null for standalone notes. */
  tradeId?: string;
  /** Symbol code. */
  symbol: string;
  /** Date of the trade/note (YYYY-MM-DD). */
  date: string;
  /** Free-form note text. */
  content: string;
  /** Tags for categorization. */
  tags: string[];
  /** Self-assessment score 1-5. */
  rating?: number;
  /** Strategy that generated the trade. */
  strategyId?: string;
  /** Entry/exit direction for context. */
  direction?: "buy" | "sell" | "short" | "cover";
  /** Entry price at the time of note. */
  entryPrice?: number;
  /** Exit price at the time of note. */
  exitPrice?: number;
  /** P&L at the time of note. */
  pnl?: number;
  /** Lesson learned / key takeaway. */
  lesson?: string;
  createdAt: string;
  updatedAt: string;
}

export interface TradeJournalOptions {
  maxNotes?: number;
  dataDir?: string | null;
}

export class TradeJournal {
  private notes: TradeNote[] = [];
  private idCounter = 0;
  private readonly maxNotes: number;
  private readonly dataDir: string | null;

  constructor(opts: TradeJournalOptions = {}) {
    this.maxNotes = opts.maxNotes ?? 2000;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  /** Add a new trade note. Returns the note ID. */
  addNote(params: {
    symbol: string;
    content: string;
    tradeId?: string;
    tags?: string[];
    rating?: number;
    strategyId?: string;
    direction?: TradeNote["direction"];
    entryPrice?: number;
    exitPrice?: number;
    pnl?: number;
    lesson?: string;
    date?: string;
  }): string {
    if (this.notes.length >= this.maxNotes) {
      // Prune oldest 10%
      this.notes = this.notes.slice(Math.floor(this.maxNotes * 0.1));
    }

    const now = new Date().toISOString();
    const id = `tn-${String(++this.idCounter).padStart(5, "0")}`;
    const note: TradeNote = {
      id,
      tradeId: params.tradeId,
      symbol: params.symbol,
      date: params.date ?? now.slice(0, 10),
      content: params.content,
      tags: params.tags ?? [],
      rating: params.rating,
      strategyId: params.strategyId,
      direction: params.direction,
      entryPrice: params.entryPrice,
      exitPrice: params.exitPrice,
      pnl: params.pnl,
      lesson: params.lesson,
      createdAt: now,
      updatedAt: now,
    };

    this.notes.push(note);
    this.saveToDisk();
    return id;
  }

  /** Update an existing note. */
  updateNote(
    id: string,
    patch: Partial<Pick<TradeNote, "content" | "tags" | "rating" | "lesson">>,
  ): boolean {
    const note = this.notes.find((n) => n.id === id);
    if (!note) return false;
    if (patch.content !== undefined) note.content = patch.content;
    if (patch.tags !== undefined) note.tags = patch.tags;
    if (patch.rating !== undefined) note.rating = patch.rating;
    if (patch.lesson !== undefined) note.lesson = patch.lesson;
    note.updatedAt = new Date().toISOString();
    this.saveToDisk();
    return true;
  }

  /** Delete a note by ID. */
  deleteNote(id: string): boolean {
    const idx = this.notes.findIndex((n) => n.id === id);
    if (idx < 0) return false;
    this.notes.splice(idx, 1);
    this.saveToDisk();
    return true;
  }

  /** Get a note by ID. */
  getNote(id: string): TradeNote | undefined {
    return this.notes.find((n) => n.id === id);
  }

  /** Get notes for a specific trade. */
  getByTradeId(tradeId: string): TradeNote[] {
    return this.notes.filter((n) => n.tradeId === tradeId);
  }

  /** Get notes for a specific symbol. */
  getBySymbol(symbol: string, limit = 20): TradeNote[] {
    return this.notes.filter((n) => n.symbol === symbol).slice(-limit);
  }

  /** Get notes for a specific date. */
  getByDate(date: string): TradeNote[] {
    return this.notes.filter((n) => n.date === date);
  }

  /** Search notes by content or tags. */
  search(query: string, limit = 20): TradeNote[] {
    const q = query.toLowerCase();
    return this.notes
      .filter(
        (n) =>
          n.content.toLowerCase().includes(q) ||
          n.tags.some((t) => t.toLowerCase().includes(q)) ||
          n.lesson?.toLowerCase().includes(q) ||
          n.symbol.toLowerCase().includes(q),
      )
      .slice(-limit);
  }

  /** Get notes within a date range (inclusive). */
  getByDateRange(startDate: string, endDate: string): TradeNote[] {
    return this.notes.filter((n) => n.date >= startDate && n.date <= endDate);
  }

  /** Get recent notes (newest first). */
  getRecent(limit = 20): TradeNote[] {
    return this.notes.slice(-limit).reverse();
  }

  /** Get all unique tags used. */
  getAllTags(): string[] {
    const tagsSet = new Set<string>();
    for (const n of this.notes) {
      for (const t of n.tags) tagsSet.add(t);
    }
    return [...tagsSet].sort();
  }

  /** Get summary statistics. */
  getSummary(): {
    totalNotes: number;
    avgRating: number;
    topTags: Array<{ tag: string; count: number }>;
    notesByMonth: Record<string, number>;
  } {
    const totalNotes = this.notes.length;
    const rated = this.notes.filter((n) => n.rating != null);
    const avgRating =
      rated.length > 0 ? rated.reduce((sum, n) => sum + n.rating!, 0) / rated.length : 0;

    const tagCounts = new Map<string, number>();
    const monthCounts: Record<string, number> = {};
    for (const n of this.notes) {
      for (const t of n.tags) {
        tagCounts.set(t, (tagCounts.get(t) ?? 0) + 1);
      }
      const month = n.date.slice(0, 7);
      monthCounts[month] = (monthCounts[month] ?? 0) + 1;
    }

    const topTags = [...tagCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([tag, count]) => ({ tag, count }));

    return { totalNotes, avgRating, topTags, notesByMonth: monthCounts };
  }

  get size(): number {
    return this.notes.length;
  }

  // ── Persistence ────────────────────────────────────────────

  private persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/trade-journal.json`;
  }

  private saveToDisk(): void {
    const p = this.persistPath();
    if (!p) return;
    const dir = dirname(p);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(p, JSON.stringify({ idCounter: this.idCounter, notes: this.notes }, null, 2));
  }

  private loadFromDisk(): void {
    const p = this.persistPath();
    if (!p || !existsSync(p)) return;
    try {
      const data = JSON.parse(readFileSync(p, "utf-8")) as {
        idCounter: number;
        notes: TradeNote[];
      };
      this.idCounter = data.idCounter ?? 0;
      this.notes = data.notes ?? [];
    } catch {
      // Ignore corrupt data
    }
  }
}
