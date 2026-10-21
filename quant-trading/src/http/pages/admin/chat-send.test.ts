import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { getAdminPageHtml } from "./full-page.js";

class Element {
  value = ""; textContent = ""; innerHTML = ""; className = ""; disabled = false;
  scrollTop = 0; scrollHeight = 100;
  dataset: Record<string, string> = {};
  children: Element[] = [];
  classes = new Set<string>();
  classList = {
    add: (name: string) => this.classes.add(name),
    remove: (name: string) => this.classes.delete(name),
    toggle: (name: string, enabled: boolean) => enabled ? this.classes.add(name) : this.classes.delete(name),
  };
  listeners: Record<string, (event: any) => void> = {};
  appendChild(child: Element) { this.children.push(child); return child; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener(type: string, handler: (event: any) => void) { this.listeners[type] = handler; }
}

function chatUi(options: { existing?: boolean; failCreation?: boolean; failSend?: boolean } = {}) {
  const html = getAdminPageHtml("");
  const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][1]![1]!;
  const chatScript = script.split("/* ══════ UNIFIED AI CHAT ══════ */")[1]!.split("/* ── Auto-refresh")[0]!;
  const nodes = Object.fromEntries([
    "chat-input", "chat-skill", "chat-send", "chat-status", "chat-title", "chat-messages", "chat-tool-status", "chat-conversations",
  ].map(id => [id, new Element()]));
  const api = vi.fn().mockImplementation(async (_path: string, init?: { method: string }) => {
    if (init?.method === "POST") {
      if (options.failCreation) throw new Error("创建会话失败");
      return { conversation: { id: "new-id", title: "新对话" } };
    }
    return { conversations: [] };
  });
  const fetch = vi.fn().mockImplementation(async () => new Response(
    'data: {"type":"delta","text":"热点股票分析"}\n\ndata: {"type":"done"}\n\n',
    { status: options.failSend ? 503 : 200 },
  ));
  const sessionStorage = { setItem: vi.fn() };
  const context: Record<string, any> = {
    api, fetch, sessionStorage, TextDecoder, B: "", _T: "", toast: vi.fn(),
    esc: (value: unknown) => String(value ?? ""),
    document: { getElementById: (id: string) => nodes[id], createElement: () => new Element() },
  };
  context.window = context;
  runInNewContext(chatScript, context);
  if (options.existing) context._chatConversationId = "existing-id";
  const handler = html.match(/id="chat-send" onclick="([^"]+)"/)![1]!;
  return { nodes, api, fetch, context, sessionStorage, click: () => runInNewContext(handler, context) };
}

describe("sending chat messages from the generated admin page", () => {
  it("creates a conversation on click and displays a streamed reply", async () => {
    const ui = chatUi();
    ui.nodes["chat-input"].value = "今天有什么热点股票";
    expect(() => ui.click()).not.toThrow();
    expect(ui.nodes["chat-send"].disabled).toBe(true);
    expect(ui.nodes["chat-messages"].children[0].children[1].textContent).toBe("今天有什么热点股票");
    await vi.waitFor(() => expect(ui.nodes["chat-status"].textContent).toBe("完成"));
    expect(ui.api).toHaveBeenCalledWith("/quant/admin/api/chat/conversations", { method: "POST", body: "{}" });
    expect(JSON.parse(ui.fetch.mock.calls[0][1].body)).toEqual({ conversationId: "new-id", message: "今天有什么热点股票", skill: null });
    expect(ui.nodes["chat-messages"].children[1].children[1].textContent).toBe("热点股票分析");
    expect(ui.nodes["chat-send"].disabled).toBe(false);
    expect(ui.sessionStorage.setItem).toHaveBeenCalledWith("qt-chat-id", "new-id");
  });

  it("sends on Enter and preserves Skill mentions for server parsing", async () => {
    const ui = chatUi({ existing: true });
    ui.nodes["chat-input"].value = "@market 热点股票";
    ui.nodes["chat-skill"].value = "company";
    const preventDefault = vi.fn();
    ui.nodes["chat-input"].listeners.keydown({ key: "Enter", shiftKey: false, preventDefault });
    await vi.waitFor(() => expect(ui.nodes["chat-status"].textContent).toBe("完成"));
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(JSON.parse(ui.fetch.mock.calls[0][1].body)).toEqual({ conversationId: "existing-id", message: "@market 热点股票", skill: "company" });
    expect(ui.api.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it.each([{ failCreation: true }, { failSend: true }])("shows request failures and restores the send button (%j)", async options => {
    const ui = chatUi(options);
    ui.nodes["chat-input"].value = "热点股票";
    ui.click();
    await vi.waitFor(() => expect(ui.nodes["chat-status"].textContent).toBe("连接失败"));
    expect(ui.nodes["chat-messages"].children[1].children[1].textContent).toContain(options.failCreation ? "创建会话失败" : "HTTP 503");
    expect(ui.nodes["chat-send"].disabled).toBe(false);
    expect(ui.context._chatBusy).toBe(false);
  });

  it("ignores empty messages, Shift+Enter and duplicate clicks while sending", async () => {
    const ui = chatUi();
    ui.nodes["chat-input"].value = "   ";
    ui.click();
    expect(ui.api).not.toHaveBeenCalled();
    ui.nodes["chat-input"].value = "热点股票";
    ui.nodes["chat-input"].listeners.keydown({ key: "Enter", shiftKey: true });
    expect(ui.api).not.toHaveBeenCalled();
    ui.click();
    ui.click();
    await vi.waitFor(() => expect(ui.nodes["chat-status"].textContent).toBe("完成"));
    expect(ui.fetch).toHaveBeenCalledOnce();
  });
});
