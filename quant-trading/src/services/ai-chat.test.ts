import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiChatService } from "./ai-chat.js";
import { ChatStore } from "./chat-store.js";

const config = { baseUrl: "http://llm.test/v1", apiKey: "test", model: "test", maxTokens: 1000, temperature: 0, timeoutMs: 1000 };
const tempDirs: string[] = [];
const stores: ChatStore[] = [];

afterEach(async () => {
  vi.restoreAllMocks();
  for (const store of stores.splice(0)) store.close();
  for (const dir of tempDirs.splice(0)) await fs.rm(dir, { recursive: true, force: true });
});

async function setup() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "qt-chat-"));
  tempDirs.push(dir);
  const store = new ChatStore(path.join(dir, "chat.db"));
  await store.initialize();
  stores.push(store);
  const skillsDir = path.join(dir, "skills", "stock");
  await fs.mkdir(skillsDir, { recursive: true });
  await fs.writeFile(path.join(skillsDir, "SKILL.md"), "---\nname: 股票分析\ndescription: 分析股票\n---\n使用行情工具分析趋势。", "utf8");
  const conversation = store.createConversation("c1");
  return { dir, store, conversation, skillsRoot: path.join(dir, "skills") };
}

describe("ChatStore", () => {
  it("persists conversations and messages in SQLite", async () => {
    const { store, conversation } = await setup();
    store.addMessage({ id: "m1", conversationId: conversation.id, role: "user", content: "你好", status: "complete", createdAt: new Date().toISOString(), skills: [], toolCalls: [] });
    expect(store.listConversations()[0]?.id).toBe("c1");
    expect(store.listMessages("c1")[0]?.content).toBe("你好");
    expect(store.renameConversation("c1", "测试")).toBe(true);
    expect(store.getConversation("c1")?.title).toBe("测试");
    expect(store.deleteConversation("c1")).toBe(true);
    expect(store.listMessages("c1")).toEqual([]);
  });
});

describe("AiChatService", () => {
  it("selects a project skill, invokes only read-only tools, and stores the result", async () => {
    const { store, conversation, skillsRoot } = await setup();
    const readTool = { name: "market_data", description: "quote", parameters: { type: "object", properties: { action: { type: "string" } } }, execute: vi.fn().mockResolvedValue({ price: 10 }) };
    const writeTool = { name: "live_trading", description: "write", execute: vi.fn() };
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ choices: [{ message: { content: '{"skill":"stock"}' } }] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ choices: [{ message: { content: null, tool_calls: [{ id: "call-1", type: "function", function: { name: "market_data", arguments: '{"action":"quote"}' } }] } }] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ choices: [{ message: { content: "报价为 10 元" } }] })));
    const service = new AiChatService(store, config, skillsRoot, [readTool, writeTool]);
    const events: Array<Record<string, unknown>> = [];
    await service.send(conversation.id, "查看行情", null, (event) => events.push(event));
    expect(readTool.execute).toHaveBeenCalledWith({ action: "quote" });
    expect(writeTool.execute).not.toHaveBeenCalled();
    expect(events.some((event) => event.type === "tool_result")).toBe(true);
    expect(store.listMessages("c1").at(-1)?.content).toContain("报价为 10");
  });

  it("rejects an unknown manually selected skill", async () => {
    const { store, conversation, skillsRoot } = await setup();
    const service = new AiChatService(store, config, skillsRoot, []);
    await expect(service.send(conversation.id, "分析", "missing", () => undefined)).rejects.toThrow("未找到 Skill");
  });

  it("persists a failed assistant message when the model is not configured", async () => {
    const { store, conversation, skillsRoot } = await setup();
    const service = new AiChatService(store, null, skillsRoot, []);
    const events: Array<Record<string, unknown>> = [];
    await service.send(conversation.id, "分析", null, (event) => events.push(event));
    expect(events.at(-1)?.type).toBe("error");
    expect(store.listMessages("c1").at(-1)?.status).toBe("error");
  });
});
