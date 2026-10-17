import { randomUUID } from "node:crypto";
import path from "node:path";
import type { ChatMessage, ChatStore } from "./chat-store.js";
import { loadProjectSkills, type ProjectSkill } from "./project-skills.js";
import type { LlmConfig } from "./standalone-llm.js";

export type ChatTool = {
  name: string; description?: string; parameters?: Record<string, unknown>;
  execute(args: Record<string, unknown>): Promise<unknown>;
};
export type ChatEvent = Record<string, unknown>;

const READ_ONLY_TOOLS = new Set([
  "market_data", "portfolio", "risk_analysis", "fund_flow", "dragon_tiger", "lianban_analysis",
  "sector_analysis", "options_data", "stochastic_pricing", "news_summary", "macro_data",
  "earnings_calendar", "institutional_flow", "social_sentiment", "chip_accumulation",
  "value_investing", "financial_calculator", "correlation_analysis", "tech_moat",
]);
const MAX_TOOL_ROUNDS = 5;
const MAX_CONTEXT_MESSAGES = 30;

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
    this.tools = new Map(tools.filter((tool) => READ_ONLY_TOOLS.has(tool.name)).map((tool) => {
      if (tool.name !== "portfolio") return [tool.name, tool];
      const allowed = new Set(["show", "view", "summary", "live_orders", "live_trades", "daily_pnl"]);
      const parameters = structuredClone(tool.parameters ?? {});
      const props = parameters.properties as Record<string, Record<string, unknown>> | undefined;
      if (props?.action) props.action.enum = [...allowed];
      return [tool.name, {
        ...tool,
        parameters,
        execute: (args: Record<string, unknown>) => {
          if (!allowed.has(String(args.action ?? "show"))) throw new Error("portfolio 仅开放持仓/摘要/订单查询操作");
          return tool.execute(args);
        },
      }];
    }));
  }

  get configured(): boolean { return Boolean(this.config); }

  async initialize(): Promise<void> {
    await this.store.initialize();
    this.skillsPromise = loadProjectSkills(this.skillsDir);
    await this.skillsPromise;
  }

  async getSkills(): Promise<Array<Pick<ProjectSkill, "id" | "name" | "description">>> {
    return (await this.skills()).map(({ id, name, description }) => ({ id, name, description }));
  }

  private skills(): Promise<ProjectSkill[]> {
    this.skillsPromise ??= loadProjectSkills(this.skillsDir);
    return this.skillsPromise;
  }

  async send(conversationId: string, input: string, requestedSkill: string | null, emit: (event: ChatEvent) => void): Promise<void> {
    let question = input.trim();
    const mention = /^@([a-z0-9-]+)\s+/i.exec(question);
    if (!requestedSkill && mention) { requestedSkill = mention[1]!; question = question.slice(mention[0].length).trim(); }
    if (!question) throw new Error("消息不能为空");
    if (!this.store.getConversation(conversationId)) throw new Error("会话不存在");
    const availableSkills = await this.skills();
    const selected = requestedSkill ? availableSkills.find((s) => s.id === requestedSkill) : this.config ? await this.selectSkill(question, availableSkills) : null;
    if (requestedSkill && !selected) throw new Error(`未找到 Skill：${requestedSkill}`);
    const skillIds = selected ? [selected.id] : [];
    emit({ type: "skill", skills: skillIds, automatic: !requestedSkill });

    const now = new Date().toISOString();
    const userMessage: ChatMessage = { id: randomUUID(), conversationId, role: "user", content: question, status: "complete", createdAt: now, skills: skillIds, toolCalls: [] };
    const assistantMessage: ChatMessage = { id: randomUUID(), conversationId, role: "assistant", content: "", status: "running", createdAt: new Date().toISOString(), skills: skillIds, toolCalls: [] };
    this.store.addMessage(userMessage);
    this.store.addMessage(assistantMessage);

    try {
      if (!this.config) throw new Error("尚未配置大模型，请在 llm 配置中设置 API Key");
      const prior = this.store.listMessages(conversationId, MAX_CONTEXT_MESSAGES)
        .filter((m) => m.id !== assistantMessage.id && m.id !== userMessage.id && m.status === "complete")
        .map((m) => ({ role: m.role, content: m.content } satisfies ProviderMessage));
      const system = [
        "你是量化交易项目的 AI 助手。使用真实工具数据回答，不得编造数据；清楚标注数据缺失和风险。",
        "你可以调用提供的只读查询与分析工具。不得声称已下单、修改配置或执行任何写操作。",
        selected ? `当前引用 Skill：${selected.name}\n${selected.body}` : "当前没有匹配到专用 Skill，直接根据工具能力回答。",
      ].join("\n\n");
      const messages: ProviderMessage[] = [{ role: "system", content: system }, ...prior, { role: "user", content: question }];
      const defs = [...this.tools.values()].map((tool) => ({ type: "function", function: { name: tool.name, description: tool.description ?? tool.name, parameters: tool.parameters ?? { type: "object", properties: {} } } }));
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
          const tool = this.tools.get(name);
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

  private async selectSkill(question: string, skills: ProjectSkill[]): Promise<ProjectSkill | null> {
    if (!skills.length) return null;
    try {
      const result = await completion(this.config!, [
        { role: "system", content: `根据用户问题选择最相关的一个项目 Skill。只返回 JSON：{"skill":"<id>"}；不相关时返回 {"skill":null}。可选项：${JSON.stringify(skills.map((s) => ({ id: s.id, name: s.name, description: s.description })))}` },
        { role: "user", content: question },
      ]);
      const choice = JSON.parse(result.content) as { skill?: string | null };
      return skills.find((s) => s.id === choice.skill) ?? null;
    } catch { return null; }
  }
}

export function defaultChatDbPath(dataDir: string | undefined): string {
  return path.join(dataDir ?? path.resolve("data"), "chat.db");
}
