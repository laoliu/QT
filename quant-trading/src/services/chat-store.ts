import fs from "node:fs/promises";
import path from "node:path";

export type ChatMessage = {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  status: "complete" | "running" | "error";
  createdAt: string;
  skills: string[];
  toolCalls: Array<{ name: string; arguments: Record<string, unknown>; result?: unknown; error?: string }>;
};

export type ChatConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
};

type Statement = { all: (...args: unknown[]) => unknown[]; get: (...args: unknown[]) => unknown; run: (...args: unknown[]) => { changes: number } };
type Db = { pragma: (sql: string) => unknown; exec: (sql: string) => unknown; prepare: (sql: string) => Statement; close: () => void };

export class ChatStore {
  private db: Db | null = null;
  private ready: Promise<void> | null = null;

  constructor(private readonly dbPath: string) {}

  initialize(): Promise<void> {
    if (this.ready) return this.ready;
    this.ready = (async () => {
      const mod = await import("better-sqlite3");
      const Database = mod.default ?? mod;
      await fs.mkdir(path.dirname(this.dbPath), { recursive: true });
      this.db = new Database(this.dbPath) as Db;
      this.db.pragma("journal_mode = WAL");
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS chat_schema_version (version INTEGER NOT NULL);
        INSERT INTO chat_schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM chat_schema_version);
        CREATE TABLE IF NOT EXISTS chat_conversations (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('complete','running','error')), created_at TEXT NOT NULL,
          skills_json TEXT NOT NULL DEFAULT '[]', tool_calls_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, created_at);
      `);
      this.db.pragma("foreign_keys = ON");
    })();
    return this.ready;
  }

  private get dbOrThrow(): Db {
    if (!this.db) throw new Error("Chat store is not initialized");
    return this.db;
  }

  listConversations(): ChatConversation[] {
    return this.dbOrThrow.prepare("SELECT id, title, created_at AS createdAt, updated_at AS updatedAt FROM chat_conversations ORDER BY updated_at DESC").all() as ChatConversation[];
  }

  createConversation(id: string, title = "新对话", now = new Date().toISOString()): ChatConversation {
    this.dbOrThrow.prepare("INSERT INTO chat_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)").run(id, title, now, now);
    return { id, title, createdAt: now, updatedAt: now };
  }

  renameConversation(id: string, title: string, now = new Date().toISOString()): boolean {
    return Boolean(this.dbOrThrow.prepare("UPDATE chat_conversations SET title=?, updated_at=? WHERE id=?").run(title, now, id).changes);
  }

  deleteConversation(id: string): boolean {
    return Boolean(this.dbOrThrow.prepare("DELETE FROM chat_conversations WHERE id=?").run(id).changes);
  }

  getConversation(id: string): ChatConversation | null {
    const row = this.dbOrThrow.prepare("SELECT id, title, created_at AS createdAt, updated_at AS updatedAt FROM chat_conversations WHERE id=?").get(id) as ChatConversation | undefined;
    return row ?? null;
  }

  listMessages(conversationId: string, limit = 200): ChatMessage[] {
    const rows = this.dbOrThrow.prepare(`SELECT id, conversation_id AS conversationId, role, content, status,
      created_at AS createdAt, skills_json AS skillsJson, tool_calls_json AS toolCallsJson
      FROM chat_messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?`).all(conversationId, limit) as Array<Record<string, unknown>>;
    return rows.reverse().map((r) => ({
      id: String(r.id), conversationId: String(r.conversationId), role: r.role as ChatMessage["role"],
      content: String(r.content), status: r.status as ChatMessage["status"], createdAt: String(r.createdAt),
      skills: JSON.parse(String(r.skillsJson)) as string[],
      toolCalls: JSON.parse(String(r.toolCallsJson)) as ChatMessage["toolCalls"],
    }));
  }

  addMessage(message: ChatMessage): void {
    this.dbOrThrow.prepare(`INSERT INTO chat_messages(id,conversation_id,role,content,status,created_at,skills_json,tool_calls_json)
      VALUES(?,?,?,?,?,?,?,?)`).run(message.id, message.conversationId, message.role, message.content, message.status,
      message.createdAt, JSON.stringify(message.skills), JSON.stringify(message.toolCalls));
    this.dbOrThrow.prepare("UPDATE chat_conversations SET updated_at=? WHERE id=?").run(message.createdAt, message.conversationId);
    if (message.role === "user") {
      this.dbOrThrow.prepare("UPDATE chat_conversations SET title=? WHERE id=? AND title='新对话'").run(message.content.slice(0, 48), message.conversationId);
    }
  }

  updateMessage(id: string, update: Pick<ChatMessage, "content" | "status" | "skills" | "toolCalls">): void {
    this.dbOrThrow.prepare("UPDATE chat_messages SET content=?,status=?,skills_json=?,tool_calls_json=? WHERE id=?")
      .run(update.content, update.status, JSON.stringify(update.skills), JSON.stringify(update.toolCalls), id);
  }

  close(): void { this.db?.close(); this.db = null; this.ready = null; }
}
