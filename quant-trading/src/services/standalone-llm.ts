/**
 * Standalone LLM subagent implementation.
 * Provides OpenAI-compatible chat completions for standalone mode,
 * enabling the strategy lab AI exploration without an external gateway.
 *
 * Supports: OpenAI, DeepSeek, Moonshot, Qwen, and any OpenAI-compatible API.
 */
import * as https from "node:https";
import * as http from "node:http";

export interface LlmConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  maxTokens: number;
  temperature: number;
  timeoutMs: number;
}

export interface SubagentRunResult {
  runId: string;
}

export interface SubagentWaitResult {
  status: "ok" | "error" | "timeout";
  error?: string;
}

export interface SubagentMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

const DEFAULT_CONFIG: LlmConfig = {
  baseUrl: "https://api.deepseek.com/v1",
  apiKey: "",
  model: "deepseek-chat",
  maxTokens: 4096,
  temperature: 0.7,
  timeoutMs: 120_000,
};

/**
 * Resolve LLM config from plugin config + environment variables.
 */
export function resolveLlmConfig(pluginConfig: Record<string, unknown>): LlmConfig | null {
  const llm = pluginConfig["llm"] as Record<string, unknown> | undefined;
  const apiKey =
    (llm?.["apiKey"] as string) ||
    process.env["LLM_API_KEY"] ||
    process.env["DEEPSEEK_API_KEY"] ||
    process.env["OPENAI_API_KEY"] ||
    "";

  if (!apiKey) return null;

  return {
    baseUrl: (llm?.["baseUrl"] as string) || DEFAULT_CONFIG.baseUrl,
    apiKey,
    model: (llm?.["model"] as string) || DEFAULT_CONFIG.model,
    maxTokens: Number(llm?.["maxTokens"]) || DEFAULT_CONFIG.maxTokens,
    temperature: Number(llm?.["temperature"]) || DEFAULT_CONFIG.temperature,
    timeoutMs: Number(llm?.["timeoutMs"]) || DEFAULT_CONFIG.timeoutMs,
  };
}

/**
 * Call the OpenAI-compatible chat completions endpoint.
 */
async function chatCompletion(config: LlmConfig, messages: Array<{ role: string; content: string }>): Promise<string> {
  // Construct full URL: baseUrl (e.g. "https://api.deepseek.com/v1") + "/chat/completions"
  const base = config.baseUrl.replace(/\/+$/, "");
  const fullUrl = base + "/chat/completions";
  const url = new URL(fullUrl);

  const body = JSON.stringify({
    model: config.model,
    messages,
    max_tokens: config.maxTokens,
    temperature: config.temperature,
  });

  return new Promise((resolve, reject) => {
    const isHttps = url.protocol === "https:";
    const lib = isHttps ? https : http;
    const options = {
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`,
        "Content-Length": Buffer.byteLength(body),
      },
      timeout: config.timeoutMs,
    };

    const req = lib.request(options, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf-8");
        if (res.statusCode && res.statusCode >= 400) {
          reject(new Error(`LLM API ${res.statusCode}: ${raw.slice(0, 500)}`));
          return;
        }
        try {
          const json = JSON.parse(raw) as {
            choices?: Array<{ message?: { content?: string } }>;
            error?: { message?: string };
          };
          if (json.error?.message) {
            reject(new Error(`LLM API error: ${json.error.message}`));
            return;
          }
          const content = json.choices?.[0]?.message?.content ?? "";
          resolve(content);
        } catch {
          reject(new Error(`LLM response parse error: ${raw.slice(0, 200)}`));
        }
      });
    });

    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("LLM request timeout"));
    });
    req.write(body);
    req.end();
  });
}

/**
 * Create a mock subagent runtime that uses direct LLM calls.
 * This object conforms to the shape expected by strategy-lab.ts.
 */
export function createStandaloneSubagent(config: LlmConfig) {
  // In-memory session store: sessionKey → messages
  const sessions = new Map<string, SubagentMessage[]>();

  return {
    async run(params: { sessionKey: string; message: string; deliver?: boolean; lane?: string; idempotencyKey?: string }) {
      const { sessionKey, message } = params;
      sessions.set(sessionKey, [{ role: "user", content: message }]);

      // Call LLM immediately
      try {
        const response = await chatCompletion(config, [
          { role: "system", content: "你是一个量化交易策略组合专家。请严格按照用户要求的 JSON 格式返回结果，不要添加额外说明文字。" },
          { role: "user", content: message },
        ]);
        sessions.get(sessionKey)?.push({ role: "assistant", content: response });
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        sessions.get(sessionKey)?.push({ role: "assistant", content: `ERROR: ${errMsg}` });
        throw err;
      }

      return { runId: sessionKey };
    },

    async waitForRun(_params: { runId: string; timeoutMs?: number }): Promise<SubagentWaitResult> {
      // Since we await chatCompletion in run(), if we get here the result is ready
      return { status: "ok" as const };
    },

    async getSessionMessages(params: { sessionKey: string; limit?: number }) {
      const msgs = sessions.get(params.sessionKey) ?? [];
      return { messages: msgs };
    },
  };
}
