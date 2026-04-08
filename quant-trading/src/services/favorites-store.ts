/**
 * FavoritesStore — Manages user's favorite stocks (股票收藏).
 *
 * Features:
 * - Add/remove/update favorite stocks with metadata
 * - Store notes, tags, and collection date
 * - Search and filter favorites
 * - Persistent storage in JSON
 * - Support for categories (watchlist, portfolio, ideas, etc.)
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { AsyncMutex } from "../utils/async-mutex.js";

export interface FavoriteStock {
  /** Stock code (e.g., "600519") */
  code: string;
  /** Stock name (e.g., "ę́") */
  name?: string;
  /** User's custom notes */
  notes?: string;
  /** Tags for categorization (e.g., ["blue-chip", "consumer"]) */
  tags?: string[];
  /** Category: "watchlist", "ideas", "portfolio", "shortlist", etc. */
  category?: string;
  /** Favorite severity (1-5, higher = more important) */
  priority?: number;
  /** ISO timestamp when added */
  addedAt: string;
  /** ISO timestamp when last updated */
  updatedAt: string;
  /** User-assigned price target */
  priceTarget?: number;
  /** User's conviction level (1-10) */
  convictionScore?: number;
}

export interface FavoritesStoreOptions {
  dataDir?: string | null;
}

export class FavoritesStore {
  private favorites = new Map<string, FavoriteStock>();
  private readonly dataDir: string;
  private readonly favoritesPath: string;
  private readonly writeMutex = new AsyncMutex();

  constructor(opts: FavoritesStoreOptions = {}) {
    this.dataDir = opts.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.favoritesPath = path.join(this.dataDir, "favorites.json");
    this.loadFromDisk();
  }

  private loadFromDisk(): void {
    try {
      const raw = readFileSync(this.favoritesPath, "utf-8");
      const data = JSON.parse(raw) as FavoriteStock[];
      for (const stock of data) {
        this.favorites.set(stock.code, stock);
      }
    } catch {
      // File doesn't exist or invalid JSON; start with empty map
    }
  }

  private saveToDisk(): void {
    mkdirSync(this.dataDir, { recursive: true });
    const data = Array.from(this.favorites.values()).sort((a, b) =>
      a.addedAt < b.addedAt ? -1 : 1,
    );
    writeFileSync(this.favoritesPath, JSON.stringify(data, null, 2), "utf-8");
  }

  /**
   * Add or update a favorite stock.
   * If stock already exists, merges provided fields with existing data.
   */
  async addFavorite(
    code: string,
    options: Partial<Omit<FavoriteStock, "code" | "addedAt">> = {},
  ): Promise<FavoriteStock> {
    const release = await this.writeMutex.acquire();
    try {
      const normalized = code.trim().toUpperCase();
      const now = new Date().toISOString();

      const existing = this.favorites.get(normalized);
      const favorite: FavoriteStock = {
        code: normalized,
        ...existing,
        ...options,
        addedAt: existing?.addedAt ?? now,
        updatedAt: now,
      };

      this.favorites.set(normalized, favorite);
      this.saveToDisk();
      return favorite;
    } finally {
      release();
    }
  }

  /**
   * Remove a favorite stock by code.
   */
  async removeFavorite(code: string): Promise<boolean> {
    const release = await this.writeMutex.acquire();
    try {
      const normalized = code.trim().toUpperCase();
      const existed = this.favorites.has(normalized);
      if (existed) {
        this.favorites.delete(normalized);
        this.saveToDisk();
      }
      return existed;
    } finally {
      release();
    }
  }

  /**
   * Get a favorite stock by code.
   */
  getFavorite(code: string): FavoriteStock | undefined {
    const normalized = code.trim().toUpperCase();
    return this.favorites.get(normalized);
  }

  /**
   * List all favorite stocks, optionally filtered by category or tags.
   */
  listFavorites(filters?: {
    category?: string;
    tags?: string[]; // match ANY tag
    priority?: number; // minimum priority
  }): FavoriteStock[] {
    let result = Array.from(this.favorites.values());

    if (filters?.category) {
      result = result.filter((f) => f.category === filters.category);
    }

    if (filters?.tags && filters.tags.length > 0) {
      result = result.filter((f) => filters.tags!.some((tag) => f.tags?.includes(tag)));
    }

    if (filters?.priority !== undefined) {
      result = result.filter((f) => (f.priority ?? 0) >= filters.priority!);
    }

    return result.sort((a, b) => {
      // Sort by priority desc, then by date desc
      const priorityDiff = (b.priority ?? 0) - (a.priority ?? 0);
      if (priorityDiff !== 0) return priorityDiff;
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });
  }

  /**
   * Search favorites by stock code or name (case-insensitive substring match).
   */
  search(query: string): FavoriteStock[] {
    const q = query.toUpperCase();
    return Array.from(this.favorites.values()).filter(
      (f) => f.code.includes(q) || f.name?.toUpperCase().includes(q),
    );
  }

  /**
   * Get all unique tags used in favorites.
   */
  getAllTags(): string[] {
    const tags = new Set<string>();
    for (const fav of this.favorites.values()) {
      for (const tag of fav.tags ?? []) {
        tags.add(tag);
      }
    }
    return Array.from(tags).sort();
  }

  /**
   * Get all unique categories.
   */
  getAllCategories(): string[] {
    const cats = new Set<string>();
    for (const fav of this.favorites.values()) {
      if (fav.category) cats.add(fav.category);
    }
    return Array.from(cats).sort();
  }

  /**
   * Get count of favorites by category.
   */
  countByCategory(): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const fav of this.favorites.values()) {
      const cat = fav.category ?? "uncategorized";
      counts[cat] = (counts[cat] ?? 0) + 1;
    }
    return counts;
  }

  /**
   * Clear all favorites (use with caution).
   */
  async clear(): Promise<number> {
    const release = await this.writeMutex.acquire();
    try {
      const count = this.favorites.size;
      this.favorites.clear();
      this.saveToDisk();
      return count;
    } finally {
      release();
    }
  }

  /**
   * Get total number of favorites.
   */
  size(): number {
    return this.favorites.size;
  }
}
