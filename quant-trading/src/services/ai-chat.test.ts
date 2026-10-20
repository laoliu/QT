import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiChatService } from "./ai-chat.js";
import { ChatStore } from "./chat-store.js";

const config = { baseUrl: "http://llm.test/v1", apiKey: "test", model: "test", maxTokens: 1000, temperature: 0, timeoutMs: 1000 };
const tempDirs: string[] = [];
const stores: ChatStore[] = [];
const reply = (message: Record<string, unknown>) => new Response(JSON.stringify({ choices: [{ message }] }));

async function addSkill(root: string, id: string, tools: string[], title = id) {
  await fs.mkdir(path.join(root, id), { recursive: true });
  await fs.writeFile(path.join(root, id, "SKILL.md"), `---\nname: ${id}\ndescription: ${id}专用研究\nallowed-tools: ${JSON.stringify(tools)}\nmetadata: ${JSON.stringify({ display_name: title, category: "研究" })}\n---\n${id}工作流。`, "utf8");
}

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

  it("keeps insertion order when user and assistant timestamps match", async () => {
    const { store } = await setup();
    for (const [id, role] of [["u", "user"], ["a", "assistant"]] as const) {
      store.addMessage({ id, conversationId: "c1", role, content: id, status: "complete", createdAt: "2026-10-20T01:00:00Z", skills: [], toolCalls: [] });
    }
    expect(store.listMessages("c1").map((m) => m.id)).toEqual(["u", "a"]);
    expect(store.listMessages("c1", 1)[0]?.id).toBe("a");
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

  it("refreshes the catalog and explains missing capabilities without restarting", async () => {
    const { store, skillsRoot } = await setup();
    const service = new AiChatService(store, config, skillsRoot, []);
    await service.initialize();
    expect(await service.getSkills()).toHaveLength(1);
    await addSkill(skillsRoot, "new-skill", ["market_data"], "新增技能");
    expect(await service.getSkills()).toContainEqual(expect.objectContaining({ id: "new-skill", displayName: "新增技能", available: false, unavailableTools: ["market_data"] }));
    await expect(service.send("c1", "研究", "new-skill", () => {})).rejects.toThrow("暂不可用");
  });

  it("uses recent context to combine main/support Skills and scopes the model's tools", async () => {
    const { store, skillsRoot } = await setup();
    await addSkill(skillsRoot, "company", ["market_data"], "个股研究");
    await addSkill(skillsRoot, "holdings", ["portfolio"], "持仓分析");
    store.addMessage({ id: "old", conversationId: "c1", role: "user", content: "请分析600519.SH", status: "complete", createdAt: "2026-10-19T01:00:00Z", skills: ["company"], toolCalls: [] });
    store.addMessage({ id: "failed", conversationId: "c1", role: "assistant", content: "上次的错误", status: "error", createdAt: "2026-10-19T01:00:01Z", skills: [], toolCalls: [] });
    const mkTool = (name: string) => ({ name, parameters: { properties: { action: { type: "string" } } }, execute: vi.fn() });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply({ content: '```json\n{"skills":["company","holdings","company","invented"]}\n```' })).mockResolvedValueOnce(reply({ content: "结合持仓分析" }));
    const events: Array<Record<string, unknown>> = [];
    const service = new AiChatService(store, config, skillsRoot, [mkTool("market_data"), mkTool("portfolio"), mkTool("data_cache")]);
    await service.send("c1", "这只股票结合我的持仓怎么看", null, (e) => events.push(e));
    const classifier = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body));
    expect(classifier.messages[1].content).toContain("600519.SH");
    expect(classifier.messages[1].content).not.toContain("上次的错误");
    const request = JSON.parse(String(fetchMock.mock.calls[1]![1]!.body));
    expect(request.messages[0].content).toContain("主 Skill：个股研究");
    expect(request.messages[0].content).toContain("辅助 Skill：持仓分析");
    expect(request.tools.map((t: any) => t.function.name).sort()).toEqual(["market_data", "portfolio"]);
    expect(events).toContainEqual({ type: "skill", skills: ["company", "holdings"], skillNames: ["个股研究", "持仓分析"], automatic: true });
    expect(store.listMessages("c1").at(-1)?.skills).toEqual(["company", "holdings"]);
  });

  it("honors manual selection and strips a matching mention without a classification call", async () => {
    const { store, skillsRoot } = await setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(reply({ content: "分析完成" }));
    const service = new AiChatService(store, config, skillsRoot, []);
    await service.send("c1", "@stock 分析600519", "stock", () => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body));
    expect(request.messages.at(-1).content).toBe("分析600519");
    expect(store.listMessages("c1").at(-1)?.skills).toEqual(["stock"]);
  });

  it("limits auto selection to three known Skills and does not carry them into an unrelated question", async () => {
    const { store, skillsRoot } = await setup();
    for (const id of ["a", "b", "c", "d"]) await addSkill(skillsRoot, id, []);
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply({ content: '{"skills":["bad","a","b","a","c","d"]}' })).mockResolvedValueOnce(reply({ content: "研究" }))
      .mockResolvedValueOnce(reply({ content: '{"skills":[]}' })).mockResolvedValueOnce(reply({ content: "你好" }));
    const service = new AiChatService(store, config, skillsRoot, []);
    await service.send("c1", "跨领域研究", null, () => {});
    expect(store.listMessages("c1").at(-1)?.skills).toEqual(["a", "b", "c"]);
    await service.send("c1", "你好", null, () => {});
    expect(store.listMessages("c1").at(-1)?.skills).toEqual([]);
  });

  it("blocks tool calls outside the selected Skill even when globally queryable", async () => {
    const { store, skillsRoot } = await setup();
    await addSkill(skillsRoot, "company", ["market_data"]);
    const execute = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(reply({ tool_calls: [{ id: "bad", type: "function", function: { name: "data_cache", arguments: '{"action":"stats"}' } }] }));
    const service = new AiChatService(store, config, skillsRoot, [{ name: "market_data", execute }, { name: "data_cache", execute }]);
    await service.send("c1", "分析股票", "company", () => {});
    expect(execute).not.toHaveBeenCalled();
    expect(store.listMessages("c1").at(-1)).toMatchObject({ status: "error", content: "模型请求了未授权的工具：data_cache" });
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
