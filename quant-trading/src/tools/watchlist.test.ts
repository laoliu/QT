/**
 * Tests for the watchlist AI-callable tool.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { createWatchlistTool } from "./watchlist.js";

const fakeApi = {
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
} as never;

describe("watchlist tool", () => {
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = path.join(os.tmpdir(), `watchlist-test-${Date.now()}`);
    await fs.mkdir(tmpDir, { recursive: true });
  });

  afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  function makeTool() {
    return createWatchlistTool(fakeApi, { dataDir: tmpDir } as QuantTradingConfig);
  }

  it("list returns empty when no watchlist exists", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "list" })) as any;
    expect(result.count).toBe(0);
    expect(result.codes).toEqual([]);
  });

  it("add adds codes and returns count", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "add", codes: ["600519", "000858"] })) as any;
    expect(result.added).toEqual(["600519", "000858"]);
    expect(result.count).toBe(2);

    // Verify persistence
    const raw = await fs.readFile(path.join(tmpDir, "watchlist.json"), "utf-8");
    const saved = JSON.parse(raw);
    expect(saved).toEqual(["600519", "000858"]);
  });

  it("add skips duplicates", async () => {
    const tool = makeTool();
    await tool.execute({ action: "add", codes: ["600519"] });
    const result = (await tool.execute({ action: "add", codes: ["600519", "000858"] })) as any;
    expect(result.added).toEqual(["000858"]);
    expect(result.count).toBe(2);
  });

  it("remove removes codes", async () => {
    const tool = makeTool();
    await tool.execute({ action: "add", codes: ["600519", "000858", "300750"] });
    const result = (await tool.execute({ action: "remove", codes: ["000858"] })) as any;
    expect(result.removed).toEqual(["000858"]);
    expect(result.count).toBe(2);
  });

  it("remove returns message when code not found", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "remove", codes: ["999999"] })) as any;
    expect(result.message).toContain("不在自选股中");
  });

  it("clear empties the watchlist", async () => {
    const tool = makeTool();
    await tool.execute({ action: "add", codes: ["600519", "000858"] });
    const result = (await tool.execute({ action: "clear" })) as any;
    expect(result.previousCount).toBe(2);
    expect(result.count).toBe(0);

    // Verify list is empty
    const listResult = (await tool.execute({ action: "list" })) as any;
    expect(listResult.count).toBe(0);
  });

  it("add errors when no codes given", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "add" })) as any;
    expect(result.error).toContain("codes");
  });

  it("uppercases stock codes", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "add", codes: ["aapl", "msft"] })) as any;
    expect(result.added).toEqual(["AAPL", "MSFT"]);
  });
});
