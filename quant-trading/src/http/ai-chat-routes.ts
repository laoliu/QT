import { randomUUID } from "node:crypto";
import type { AiChatService } from "../services/ai-chat.js";
import type { ChatStore } from "../services/chat-store.js";
import type { QuantTradingRuntime } from "../runtime.js";
import type { HttpHandler } from "./auth-middleware.js";
import { json, parseObjectBody } from "./routes/_helpers.js";

export function registerAiChatRoutes(args: {
  api: QuantTradingRuntime;
  chat: AiChatService;
  store: ChatStore;
  secure: (handler: HttpHandler) => HttpHandler;
}): void {
  const { api, chat, store, secure } = args;
  const register = (path: string, handler: HttpHandler) => api.registerHttpRoute({ auth: "plugin", path, handler: secure(handler) });
  const ready = async () => chat.initialize();

  register("/quant/admin/api/chat/skills", async (_req, res) => {
    await ready(); json(res, { skills: await chat.getSkills(), configured: chat.configured });
  });
  register("/quant/admin/api/chat/conversations", async (req, res) => {
    await ready();
    if (req.method === "GET") { json(res, { conversations: store.listConversations() }); return; }
    if (req.method === "POST") { const row = store.createConversation(randomUUID()); json(res, { conversation: row }, 201); return; }
    json(res, { error: "Method not allowed" }, 405);
  });
  register("/quant/admin/api/chat/conversation", async (req, res) => {
    await ready();
    const body = req.method === "GET" ? null : await parseObjectBody(req);
    const url = new URL(req.url ?? "/", "http://localhost");
    const id = String(url.searchParams.get("id") ?? body?.id ?? "").trim();
    if (!id) { json(res, { error: "缺少会话 ID" }, 400); return; }
    if (req.method === "GET") {
      const conversation = store.getConversation(id);
      if (!conversation) { json(res, { error: "会话不存在" }, 404); return; }
      json(res, { conversation, messages: store.listMessages(id) }); return;
    }
    if (req.method === "PATCH") {
      const title = String(body?.title ?? "").trim().slice(0, 100);
      if (!title) { json(res, { error: "标题不能为空" }, 400); return; }
      if (!store.renameConversation(id, title)) { json(res, { error: "会话不存在" }, 404); return; }
      json(res, { conversation: store.getConversation(id) }); return;
    }
    if (req.method === "DELETE") {
      if (!store.deleteConversation(id)) { json(res, { error: "会话不存在" }, 404); return; }
      json(res, { ok: true }); return;
    }
    json(res, { error: "Method not allowed" }, 405);
  });
  register("/quant/admin/api/chat/send", async (req, res) => {
    if (req.method !== "POST") { json(res, { error: "Method not allowed" }, 405); return; }
    await ready();
    const body = await parseObjectBody(req);
    const conversationId = String(body.conversationId ?? "").trim();
    const message = String(body.message ?? "").trim();
    const skill = body.skill == null || body.skill === "" ? null : String(body.skill);
    if (!conversationId || !message) { json(res, { error: "conversationId 和 message 为必填项" }, 400); return; }
    res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive", "X-Accel-Buffering": "no" });
    res.write(": connected\n\n");
    let closed = false;
    res.on("close", () => { if (!res.writableEnded) closed = true; });
    const emit = (event: Record<string, unknown>) => { if (!closed) res.write(`data: ${JSON.stringify(event)}\n\n`); };
    try { await chat.send(conversationId, message, skill, emit); }
    catch (err) { emit({ type: "error", message: err instanceof Error ? err.message : String(err) }); }
    finally { if (!closed) res.end(); }
  });
}
