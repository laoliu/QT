import { randomUUID } from "node:crypto";
import path from "node:path";
import type { ChatMessage, ChatStore } from "./chat-store.js";
import { loadProjectSkills, type ProjectSkill } from "./project-skills.js";
import type { LlmConfig } from "./standalone-llm.js";
import { createChatTools } from "./chat-tool-policy.js";

export type ChatTool = {
  name: string; description?: string; parameters?: Record<string, unknown>;
  execute(args: Record<string, unknown>): Promise<unknown>;
};
export type ChatEvent = Record<string, unknown>;

const MAX_TOOL_ROUNDS = 8;
const MAX_CONTEXT_MESSAGES = 30;
const MAX_SELECTED_SKILLS = 3;

type ProviderMessage = { role: string; content?: string | null; tool_calls?: Array<Record<string, unknown>>; tool_call_id?: string; name?: string };

async function completion(config: LlmConfig, messages: ProviderMessage[], tools?: Array<Record<string, unknown>>, onDelta?: (text: string) => void): Promise<{ content: string; toolCalls: Array<Record<string, unknown>> }> {
  const base = config.baseUrl.replace(/\/+$/, "");
  const response = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` },
    body: JSON.stringify({ model: config.model, messages, max_tokens: config.maxTokens, temperature: config.temperature, ...(onDelta ? { stream: true } : {}), ...(tools?.length ? { tools, tool_choice: "auto" } : {}) }),
    signal: AbortSignal.timeout(config.timeoutMs),
  });
  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`LLM API ${response.status}: ${errorBody.slice(0, 400)}`);
  }
  if (onDelta && response.headers.get("content-type")?.includes("text/event-stream") && response.body) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", content = "";
    const contentChunks: string[] = [];
    const calls = new Map<number, { id?: string; type?: string; function: { name: string; arguments: string } }>();
    const consume = (payload: string) => {
      if (!payload || payload === "[DONE]") return;
      const data = JSON.parse(payload) as { choices?: Array<{ delta?: { content?: string; tool_calls?: Array<{ index: number; id?: string; type?: string; function?: { name?: string; arguments?: string } }> } }> };
      const delta = data.choices?.[0]?.delta;
      if (delta?.content) { content += delta.content; contentChunks.push(delta.content); }
      for (const fragment of delta?.tool_calls ?? []) {
        const call = calls.get(fragment.index) ?? { function: { name: "", arguments: "" } };
        if (fragment.id) call.id = fragment.id;
        if (fragment.type) call.type = fragment.type;
        if (fragment.function?.name) call.function.name += fragment.function.name;
        if (fragment.function?.arguments) call.function.arguments += fragment.function.arguments;
        calls.set(fragment.index, call);
      }
    };
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const dataLine = frame.split(/\r?\n/).find((line) => line.startsWith("data:"));
        if (dataLine) consume(dataLine.slice(5).trim());
      }
      if (done) break;
    }
    if (buffer.trim().startsWith("data:")) consume(buffer.trim().slice(5).trim());
    const toolCalls = [...calls.entries()].sort((a, b) => a[0] - b[0]).map(([, call]) => call);
    if (toolCalls.length === 0) for (const chunk of contentChunks) onDelta(chunk);
    return { content, toolCalls };
  }
  const raw = await response.text();
  const data = JSON.parse(raw) as { choices?: Array<{ message?: { content?: string | null; tool_calls?: Array<Record<string, unknown>> } }>; error?: { message?: string } };
  if (data.error?.message) throw new Error(data.error.message);
  const message = data.choices?.[0]?.message;
  if (!message) throw new Error("LLM response did not contain a choice");
  if (onDelta && message.content && !(message.tool_calls?.length)) onDelta(message.content);
  return { content: message.content ?? "", toolCalls: message.tool_calls ?? [] };
}

export class AiChatService {
  private skillsPromise: Promise<ProjectSkill[]> | null = null;
  private readonly tools: Map<string, ChatTool>;

  constructor(
    readonly store: ChatStore,
    private readonly config: LlmConfig | null,
    private readonly skillsDir: string,
    tools: ChatTool[],
  ) {
    this.tools = createChatTools(tools);
  }

  get configured(): boolean { return Boolean(this.config); }

  async initialize(): Promise<void> {
    await this.store.initialize();
    await this.skills();
  }

  async getSkills() {
    // Listing refreshes on-disk skills, so adding one never requires restarting
    // a connected trading service. Each send also takes a fresh snapshot.
    this.skillsPromise = loadProjectSkills(this.skillsDir);
    return (await this.skills()).map(({ id, name, displayName, description, category, tools }) => {
      const unavailableTools = tools.filter((tool) => !this.tools.has(tool));
      return { id, name, displayName, description, category, available: unavailableTools.length === 0, unavailableTools };
    });
  }

  private skills(): Promise<ProjectSkill[]> {
    this.skillsPromise ??= loadProjectSkills(this.skillsDir);
    return this.skillsPromise;
  }

  async send(conversationId: string, input: string, requestedSkill: string | null, emit: (event: ChatEvent) => void): Promise<void> {
    let question = input.trim();
    const mention = /^@([a-z0-9-]+)(?=\s|$)\s*/i.exec(question);
    if (mention) { requestedSkill ||= mention[1]!; question = question.slice(mention[0].length).trim(); }
    if (!question) throw new Error("消息不能为空");
    if (!this.store.getConversation(conversationId)) throw new Error("会话不存在");
    this.skillsPromise = loadProjectSkills(this.skillsDir);
    const availableSkills = await this.skills();
    const priorRows = this.store.listMessages(conversationId, MAX_CONTEXT_MESSAGES).filter((m) => m.status === "complete");
    const manual = requestedSkill ? availableSkills.find((s) => s.id === requestedSkill) : null;
    if (requestedSkill && !manual) throw new Error(`未找到 Skill：${requestedSkill}`);
    if (manual?.tools.some((name) => !this.tools.has(name))) throw new Error(`Skill 暂不可用：${manual.displayName}，所需工具尚未接入`);
    const selected = manual ? [manual] : this.config ? await this.selectSkills(question, availableSkills.filter((s) => s.tools.every((name) => this.tools.has(name))), priorRows.slice(-6)) : [];
    const skillIds = selected.map((s) => s.id);
    emit({ type: "skill", skills: skillIds, skillNames: selected.map((s) => s.displayName), automatic: !requestedSkill });

    const now = new Date().toISOString();
    const userMessage: ChatMessage = { id: randomUUID(), conversationId, role: "user", content: question, status: "complete", createdAt: now, skills: skillIds, toolCalls: [] };
    const assistantMessage: ChatMessage = { id: randomUUID(), conversationId, role: "assistant", content: "", status: "running", createdAt: new Date().toISOString(), skills: skillIds, toolCalls: [] };
    this.store.addMessage(userMessage);
    this.store.addMessage(assistantMessage);

    try {
      if (!this.config) throw new Error("尚未配置大模型，请在 llm 配置中设置 API Key");
      const prior = priorRows
        .map((m) => ({ role: m.role, content: m.content } satisfies ProviderMessage));
      const system = [
        "你是量化交易项目的 AI 助手。使用真实工具数据回答，不得编造数据；清楚标注数据缺失和风险。",
        "你可以调用提供的查询、计算与研究工具。不得声称已下单、修改配置、训练模型或保存策略。查询可能更新底层行情缓存，但不会修改业务配置和交易状态。",
        "以本轮工具定义和返回结果为准，不调用未提供的工具或 action；数据缺失、过期、部分成功、未完成验证必须明确说明。区分 A 股股票与指数、实盘与模拟；不把现时快照当作 PIT 数据。",
        selected.length ? selected.map((s, i) => `${i === 0 ? "主" : "辅助"} Skill：${s.displayName}\n${s.body}`).join("\n\n") : "当前没有匹配到专用 Skill，直接根据工具能力回答。",
      ].join("\n\n");
      const messages: ProviderMessage[] = [{ role: "system", content: system }, ...prior, { role: "user", content: question }];
      const scopedNames = new Set(selected.flatMap((s) => s.tools));
      const scopedTools = new Map([...this.tools].filter(([name]) => !scopedNames.size || scopedNames.has(name)));
      const defs = [...scopedTools.values()].map((tool) => ({ type: "function", function: { name: tool.name, description: tool.description ?? tool.name, parameters: tool.parameters ?? { type: "object", properties: {} } } }));
      let answer = "";
      for (let round = 0; round <= MAX_TOOL_ROUNDS; round++) {
        const result = await completion(this.config, messages, defs, (text) => emit({ type: "delta", text }));
        if (!result.toolCalls.length) { answer += result.content; break; }
        if (round === MAX_TOOL_ROUNDS) throw new Error("模型工具调用超过最大轮数");
        messages.push({ role: "assistant", content: result.content || null, tool_calls: result.toolCalls });
        for (const rawCall of result.toolCalls) {
          const fn = rawCall.function as { name?: string; arguments?: string } | undefined;
          const id = String(rawCall.id ?? randomUUID());
          const name = fn?.name ?? "";
          const tool = scopedTools.get(name);
          if (!tool) throw new Error(`模型请求了未授权的工具：${name}`);
          let args: Record<string, unknown>;
          try { args = JSON.parse(fn?.arguments ?? "{}") as Record<string, unknown>; }
          catch { throw new Error(`工具 ${name} 参数格式无效`); }
          emit({ type: "tool_start", name, arguments: args });
          const trace: ChatMessage["toolCalls"][number] = { name, arguments: args };
          assistantMessage.toolCalls.push(trace);
          try {
            const value = await tool.execute(args);
            trace.result = value;
            emit({ type: "tool_result", name, result: value });
            messages.push({ role: "tool", tool_call_id: id, name, content: JSON.stringify(value) });
          } catch (err) {
            trace.error = err instanceof Error ? err.message : String(err);
            emit({ type: "tool_result", name, error: trace.error });
            messages.push({ role: "tool", tool_call_id: id, name, content: JSON.stringify({ error: trace.error }) });
          }
        }
      }
      assistantMessage.content = answer || "（模型返回了空内容）";
      assistantMessage.status = "complete";
      this.store.updateMessage(assistantMessage.id, assistantMessage);
      emit({ type: "done", messageId: assistantMessage.id });
    } catch (err) {
      assistantMessage.status = "error";
      assistantMessage.content = err instanceof Error ? err.message : String(err);
      this.store.updateMessage(assistantMessage.id, assistantMessage);
      emit({ type: "error", message: assistantMessage.content });
    }
  }

  private async selectSkills(question: string, skills: ProjectSkill[], recent: ChatMessage[]): Promise<ProjectSkill[]> {
    if (!skills.length) return [];
    try {
      const result = await completion(this.config!, [
        { role: "system", content: `为当前问题选择一个主 Skill，确有跨领域需求时最多再选两个辅助 Skill。只返回 JSON：{"skills":["主id","辅助id"]}；不相关时返回 {"skills":[]}。历史仅用于理解“这只股票”“继续”等指代；当前问题改变话题时不要沿用旧 Skill。优先选专用 Skill，financial-data-hub 仅用于通用数据咨询。可选项：${JSON.stringify(skills.map((s) => ({ id: s.id, name: s.displayName, description: s.description })))}` },
        { role: "user", content: JSON.stringify({ recent: recent.map((m) => ({ role: m.role, content: m.content.slice(-2000), skills: m.skills })), question }) },
      ]);
      const choice = JSON.parse(result.content.trim().replace(/^```(?:json)?\s*|\s*```$/g, "")) as { skills?: unknown; skill?: unknown };
      const ids = Array.isArray(choice.skills) ? choice.skills : typeof choice.skill === "string" ? [choice.skill] : [];
      return [...new Set(ids)].flatMap((id) => { const skill = skills.find((s) => s.id === id); return skill ? [skill] : []; }).slice(0, MAX_SELECTED_SKILLS);
    } catch { return []; }
  }
}

export function defaultChatDbPath(dataDir: string | undefined): string {
  return path.join(dataDir ?? path.resolve("data"), "chat.db");
}
