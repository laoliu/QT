import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * Tests for the favorites tool and FavoritesStore
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { FavoritesStore } from "./favorites-store.js";

describe("FavoritesStore", () => {
  let tmpDir: string;
  let store: FavoritesStore;

  beforeEach(async () => {
    // Create a temporary directory for tests
    tmpDir = path.join(os.tmpdir(), `favorites-test-${Date.now()}`);
    await fs.mkdir(tmpDir, { recursive: true });
    store = new FavoritesStore({ dataDir: tmpDir });
  });

  afterEach(async () => {
    // Clean up temporary directory
    try {
      await fs.rm(tmpDir, { recursive: true, force: true });
    } catch {
      // Ignore cleanup errors
    }
  });

  it("should add a favorite stock", async () => {
    const fav = await store.addFavorite("600519", {
      name: "ę́",
      category: "blue-chip",
      priority: 5,
    });

    expect(fav.code).toBe("600519");
    expect(fav.name).toBe("ę́");
    expect(fav.category).toBe("blue-chip");
    expect(fav.priority).toBe(5);
  });

  it("should normalize stock codes to uppercase", async () => {
    const fav = await store.addFavorite("600519", { name: "ę́" });
    expect(fav.code).toBe("600519");

    const retrieved = store.getFavorite("600519");
    expect(retrieved).toBeDefined();
    expect(retrieved?.code).toBe("600519");
  });

  it("should list all favorites", async () => {
    await store.addFavorite("600519", { name: "ę́", category: "blue-chip", priority: 5 });
    await store.addFavorite("000858", { name: "五粮液", category: "blue-chip", priority: 4 });
    await store.addFavorite("300089", { name: "文化", category: "ideas", priority: 2 });

    const all = store.listFavorites();
    expect(all).toHaveLength(3);
  });

  it("should filter favorites by category", async () => {
    await store.addFavorite("600519", { name: "ę́", category: "blue-chip", priority: 5 });
    await store.addFavorite("000858", { name: "五粮液", category: "blue-chip", priority: 4 });
    await store.addFavorite("300089", { name: "文化", category: "ideas", priority: 2 });

    const bluechips = store.listFavorites({ category: "blue-chip" });
    expect(bluechips).toHaveLength(2);
    expect(bluechips.map((f) => f.code)).toEqual(expect.arrayContaining(["600519", "000858"]));
  });

  it("should filter favorites by priority", async () => {
    await store.addFavorite("600519", { name: "ę́", priority: 5 });
    await store.addFavorite("000858", { name: "五粮液", priority: 3 });
    await store.addFavorite("300089", { name: "文化", priority: 1 });

    const highPriority = store.listFavorites({ priority: 4 });
    expect(highPriority).toHaveLength(1);
    expect(highPriority[0].code).toBe("600519");
  });

  it("should filter favorites by tags", async () => {
    await store.addFavorite("600519", { name: "ę́", tags: ["blue-chip", "consumer"] });
    await store.addFavorite("000858", { name: "五粮液", tags: ["blue-chip", "consumer"] });
    await store.addFavorite("000333", { name: "美的", tags: ["tech"] });

    const withConsumer = store.listFavorites({ tags: ["consumer"] });
    expect(withConsumer).toHaveLength(2);
  });

  it("should search favorites by code", async () => {
    await store.addFavorite("600519", { name: "ę́" });
    await store.addFavorite("000858", { name: "五粮液" });

    const results = store.search("600");
    expect(results).toHaveLength(1);
    expect(results[0].code).toBe("600519");
  });

  it("should search favorites by name", async () => {
    await store.addFavorite("600519", { name: "ę́" });
    await store.addFavorite("000858", { name: "五粮液" });

    const results = store.search("é");
    expect(results).toHaveLength(1);
    expect(results[0].code).toBe("600519");
  });

  it("should update a favorite", async () => {
    let fav = await store.addFavorite("600519", { name: "ę́", priority: 3 });
    expect(fav.priority).toBe(3);

    fav = await store.addFavorite("600519", { notes: "看好白酒", priority: 5 });
    expect(fav.priority).toBe(5);
    expect(fav.notes).toBe("看好白酒");
    expect(fav.name).toBe("ę́"); // Should retain previous name
  });

  it("should remove a favorite", async () => {
    await store.addFavorite("600519", { name: "ę́" });
    expect(store.getFavorite("600519")).toBeDefined();

    const removed = await store.removeFavorite("600519");
    expect(removed).toBe(true);
    expect(store.getFavorite("600519")).toBeUndefined();
  });

  it("should return false when removing non-existent favorite", async () => {
    const removed = await store.removeFavorite("999999");
    expect(removed).toBe(false);
  });

  it("should get all tags", async () => {
    await store.addFavorite("600519", { tags: ["blue-chip", "consumer"] });
    await store.addFavorite("000858", { tags: ["blue-chip", "consumer"] });
    await store.addFavorite("300089", { tags: ["tech", "growth"] });

    const tags = store.getAllTags();
    expect(tags).toEqual(expect.arrayContaining(["blue-chip", "consumer", "tech", "growth"]));
  });

  it("should get all categories", async () => {
    await store.addFavorite("600519", { category: "watchlist" });
    await store.addFavorite("000858", { category: "ideas" });
    await store.addFavorite("300089", { category: "portfolio" });

    const categories = store.getAllCategories();
    expect(categories).toEqual(expect.arrayContaining(["watchlist", "ideas", "portfolio"]));
  });

  it("should count by category", async () => {
    await store.addFavorite("600519", { category: "watchlist" });
    await store.addFavorite("000858", { category: "watchlist" });
    await store.addFavorite("300089", { category: "ideas" });

    const counts = store.countByCategory();
    expect(counts["watchlist"]).toBe(2);
    expect(counts["ideas"]).toBe(1);
  });

  it("should clear all favorites", async () => {
    await store.addFavorite("600519", { name: "ę́" });
    await store.addFavorite("000858", { name: "五粮液" });

    const count = await store.clear();
    expect(count).toBe(2);
    expect(store.size()).toBe(0);
  });

  it("should persist to disk", async () => {
    await store.addFavorite("600519", {
      name: "ę́",
      category: "blue-chip",
      priority: 5,
      tags: ["consumer"],
    });

    // Create a new store instance and verify it loads from disk
    const store2 = new FavoritesStore({ dataDir: tmpDir });
    const fav = store2.getFavorite("600519");
    expect(fav).toBeDefined();
    expect(fav?.name).toBe("ę́");
    expect(fav?.category).toBe("blue-chip");
    expect(fav?.priority).toBe(5);
    expect(fav?.tags).toEqual(["consumer"]);
  });

  it("should sort by priority (descending) then by update time (descending)", async () => {
    await store.addFavorite("600519", { name: "ę́", priority: 3 });

    // Wait a bit to ensure different timestamps
    await new Promise((resolve) => setTimeout(resolve, 10));

    await store.addFavorite("000858", { name: "五粮液", priority: 5 });
    await store.addFavorite("300089", { name: "文化", priority: 5 });

    const sorted = store.listFavorites();
    expect(sorted[0].priority).toBe(5); // Highest priority first
    expect(sorted[1].priority).toBe(5);
    expect(sorted[2].priority).toBe(3); // Lower priority last
  });
});
