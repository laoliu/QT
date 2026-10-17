import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TradeJournal } from "../services/trade-journal.js";
import { createTradeJournalTool } from "./trade-journal.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;

describe("trade_journal tool", () => {
  let tmpDir: string;
  let journal: TradeJournal;
  let tool: ReturnType<typeof createTradeJournalTool>;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "journal-tool-"));
    journal = new TradeJournal({ maxNotes: 100, dataDir: tmpDir });
    tool = createTradeJournalTool(fakeApi, journal);
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  // ── add_note ──

  it("adds a note", async () => {
    const res = (await tool.execute({
      action: "add_note",
      symbol: "600519",
      content: "追涨买入，回调止损",
      tags: ["追涨"],
      rating: 3,
    })) as Record<string, unknown>;
    expect(res.message).toContain("已保存");
    expect(res.id).toBeDefined();
  });

  it("requires symbol and content", async () => {
    const res = (await tool.execute({ action: "add_note" })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── view ──

  it("views notes by symbol", async () => {
    await tool.execute({ action: "add_note", symbol: "600519", content: "note1" });
    await tool.execute({ action: "add_note", symbol: "600519", content: "note2" });
    const res = (await tool.execute({ action: "view", symbol: "600519" })) as Record<
      string,
      unknown
    >;
    expect(res.count).toBe(2);
  });

  it("views a single note by note_id", async () => {
    const addRes = (await tool.execute({
      action: "add_note",
      symbol: "600519",
      content: "test note",
    })) as Record<string, unknown>;
    const res = await tool.execute({ action: "view", note_id: addRes.id });
    expect((res as Record<string, unknown>).content).toBe("test note");
  });

  it("returns error when view has no params", async () => {
    const res = (await tool.execute({ action: "view" })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── update_note ──

  it("updates a note", async () => {
    const addRes = (await tool.execute({
      action: "add_note",
      symbol: "600519",
      content: "original",
    })) as Record<string, unknown>;
    const res = (await tool.execute({
      action: "update_note",
      note_id: addRes.id,
      content: "updated",
    })) as Record<string, unknown>;
    expect(res.message).toContain("已更新");
  });

  it("returns error for missing note_id on update", async () => {
    const res = (await tool.execute({ action: "update_note" })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── delete_note ──

  it("deletes a note", async () => {
    const addRes = (await tool.execute({
      action: "add_note",
      symbol: "600519",
      content: "to delete",
    })) as Record<string, unknown>;
    const res = (await tool.execute({
      action: "delete_note",
      note_id: addRes.id,
    })) as Record<string, unknown>;
    expect(res.message).toContain("已删除");
  });

  // ── search ──

  it("searches notes by keyword", async () => {
    await tool.execute({ action: "add_note", symbol: "600519", content: "均线金叉买入" });
    await tool.execute({ action: "add_note", symbol: "000858", content: "跌破支撑位" });
    const res = (await tool.execute({ action: "search", query: "金叉" })) as Record<
      string,
      unknown
    >;
    expect(res.count).toBe(1);
  });

  it("returns error for missing query on search", async () => {
    const res = (await tool.execute({ action: "search" })) as Record<string, unknown>;
    expect(res.error).toBeDefined();
  });

  // ── recent ──

  it("returns recent notes", async () => {
    await tool.execute({ action: "add_note", symbol: "600519", content: "note1" });
    const res = (await tool.execute({ action: "recent", limit: 5 })) as Record<string, unknown>;
    expect(res.count).toBe(1);
  });

  // ── summary ──

  it("returns summary stats", async () => {
    await tool.execute({ action: "add_note", symbol: "600519", content: "note1", rating: 4 });
    const res = (await tool.execute({ action: "summary" })) as Record<string, unknown>;
    expect(res).toBeDefined();
    expect((res as Record<string, unknown>).totalNotes).toBe(1);
  });

  // ── tags ──

  it("returns tag list", async () => {
    await tool.execute({
      action: "add_note",
      symbol: "600519",
      content: "note",
      tags: ["追涨", "止损"],
    });
    const res = (await tool.execute({ action: "tags" })) as Record<string, unknown>;
    expect((res.tags as string[]).length).toBe(2);
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const res = (await tool.execute({ action: "invalid" })) as Record<string, unknown>;
    expect(res.error).toContain("未知操作");
  });
});
