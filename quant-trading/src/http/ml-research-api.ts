/**
 * Canonical HTTP boundary for the A-share ML research pipeline.
 *
 * The AI tool and this route share the same Python bridge, while this
 * boundary adds the operational concerns an HTTP caller needs: explicit
 * Paper confirmation, idempotency, and an append-only audit record. It never
 * accepts a live execution action and never selects a broker.
 */
import { createHash } from "node:crypto";
import { appendFile, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript, type PythonBridgeResult } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";
import type { HttpHandler } from "./auth-middleware.js";
import { parseJsonBody } from "./parse-body.js";

const CANONICAL_ACTIONS = new Set([
  "list_models", "train", "train_tushare", "predict", "evaluate", "export", "execute_paper",
]);
const MUTATING_ACTIONS = new Set(["train", "train_tushare", "export", "execute_paper"]);

type JsonObject = Record<string, unknown>;
type RouteResult = { status: number; body: JsonObject };
type Runner = (payload: JsonObject) => Promise<PythonBridgeResult<unknown>>;
type IdempotencyEntry = { digest: string; promise: Promise<RouteResult> };

function json(res: ServerResponse, body: JsonObject, status: number): void {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body, null, 2));
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${stableJson(object[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function digestPayload(payload: JsonObject): string {
  return createHash("sha256").update(stableJson(payload)).digest("hex");
}

function requestIdempotencyKey(req: IncomingMessage, body: JsonObject): string {
  const header = req.headers["idempotency-key"];
  const value = typeof header === "string" ? header : body.idempotencyKey;
  return typeof value === "string" ? value.trim() : "";
}

function auditPath(config: QuantTradingConfig): string {
  return path.join(path.resolve(config.dataDir ?? "./data"), "research", "ml-ranking-audit.jsonl");
}

async function appendAudit(config: QuantTradingConfig, event: JsonObject): Promise<void> {
  const target = auditPath(config);
  await mkdir(path.dirname(target), { recursive: true });
  await appendFile(target, `${JSON.stringify({ timestamp: new Date().toISOString(), schemaVersion: 1, ...event })}\n`, "utf8");
}

function requestSummary(body: JsonObject): JsonObject {
  const result: JsonObject = {};
  for (const key of ["action", "runId", "modelId", "planPath", "outputPath", "dataPath", "predictionsPath"]) {
    if (body[key] !== undefined) result[key] = body[key];
  }
  return result;
}

/** Create a testable handler; production registration is provided below. */
export function createMLResearchHandler(
  config: QuantTradingConfig,
  options: { run?: Runner; audit?: (event: JsonObject) => Promise<void> } = {},
): HttpHandler {
  const run = options.run ?? ((payload) => runPythonScript(
    "aipha/engines/aipha_ml_engine.py", payload, config, { timeoutMs: 300_000 },
  ));
  const audit = options.audit ?? ((event) => appendAudit(config, event));
  const idempotency = new Map<string, IdempotencyEntry>();
  const restoreIdempotency = (async () => {
    if (options.audit) return;
    try {
      const contents = await readFile(auditPath(config), "utf8");
      for (const line of contents.split(/\r?\n/)) {
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line) as JsonObject;
          const key = typeof event.idempotencyKey === "string" ? event.idempotencyKey : "";
          const digest = typeof event.payloadDigest === "string" ? event.payloadDigest : "";
          const response = event.response as RouteResult | undefined;
          if (key && digest && response && typeof response.status === "number" && response.body) {
            idempotency.set(key, { digest, promise: Promise.resolve(response) });
          }
        } catch { /* tolerate a truncated final audit record */ }
      }
    } catch { /* a missing audit file means there is no prior request state */ }
  })();

  const execute = async (req: IncomingMessage): Promise<RouteResult> => {
    await restoreIdempotency;
    let body: JsonObject;
    try {
      body = await parseJsonBody<JsonObject>(req);
    } catch (error) {
      return { status: 400, body: { status: "error", code: "INVALID_JSON", message: (error as Error).message } };
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return { status: 400, body: { status: "error", code: "INVALID_BODY", message: "请求体必须是 JSON 对象" } };
    }
    const action = typeof body.action === "string" ? body.action : "list_models";
    if (!CANONICAL_ACTIONS.has(action)) {
      return { status: 400, body: { status: "error", code: "UNSUPPORTED_ACTION", message: `不支持的研究 action: ${action}` } };
    }
    if (body.allowLive === true || body.mode === "live") {
      return { status: 403, body: { status: "error", code: "LIVE_NOT_ALLOWED", message: "研究 HTTP 接口只允许生成或执行 Paper 计划" } };
    }
    if (action === "execute_paper" && body.confirm !== true) {
      return { status: 400, body: { status: "error", code: "CONFIRM_REQUIRED", message: "execute_paper requires confirm=true" } };
    }
    const key = requestIdempotencyKey(req, body);
    if (MUTATING_ACTIONS.has(action) && !key) {
      return { status: 400, body: { status: "error", code: "IDEMPOTENCY_REQUIRED", message: "写操作必须提供 Idempotency-Key" } };
    }
    const payload = { ...body };
    delete payload.idempotencyKey;
    const digest = digestPayload(payload);
    if (key) {
      const existing = idempotency.get(key);
      if (existing) {
        if (existing.digest !== digest) {
          return { status: 409, body: { status: "error", code: "IDEMPOTENCY_CONFLICT", message: "同一 Idempotency-Key 对应了不同请求" } };
        }
        const replay = await existing.promise;
        return { status: replay.status, body: { ...replay.body, idempotentReplay: true } };
      }
    }

    const promise = (async (): Promise<RouteResult> => {
      const request = requestSummary(payload);
      try {
        const bridge = await run(payload);
        if (!bridge.ok) {
          const result: RouteResult = { status: 502, body: { status: "error", code: "PYTHON_BRIDGE_FAILED", message: bridge.error ?? "Python bridge failed" } };
          await audit({ ...request, idempotencyKey: key || undefined, payloadDigest: digest, response: result, outcome: "failed", status: result.status, code: result.body.code, error: result.body.message });
          return result;
        }
        const inner = bridge.data as JsonObject | undefined;
        if (inner?.ok === false) {
          const result: RouteResult = { status: action === "execute_paper" ? 422 : 400, body: { status: "error", code: "RESEARCH_ACTION_FAILED", message: String(inner.error ?? "研究 action 执行失败"), data: inner.data } };
          await audit({ ...request, idempotencyKey: key || undefined, payloadDigest: digest, response: result, outcome: "failed", status: result.status, code: result.body.code, error: result.body.message });
          return result;
        }
        const result: RouteResult = { status: 200, body: { status: "ok", action, data: inner?.data ?? inner ?? bridge.data, idempotentReplay: false } };
        await audit({ ...request, idempotencyKey: key || undefined, payloadDigest: digest, response: result, outcome: "succeeded", status: result.status, provenance: (inner?.data as JsonObject | undefined)?.provenance });
        return result;
      } catch (error) {
        const result: RouteResult = { status: 500, body: { status: "error", code: "RESEARCH_ROUTE_FAILED", message: (error as Error).message } };
        try { await audit({ ...request, idempotencyKey: key || undefined, payloadDigest: digest, response: result, outcome: "failed", status: result.status, code: result.body.code, error: result.body.message }); } catch { /* preserve original error */ }
        return result;
      }
    })();
    if (key) idempotency.set(key, { digest, promise });
    return promise;
  };

  return async (req, res) => {
    if (req.method === "GET") {
      const bridge = await run({ action: "list_models" });
      if (!bridge.ok) { json(res, { status: "error", code: "PYTHON_BRIDGE_FAILED", message: bridge.error ?? "Python bridge failed" }, 502); return; }
      const inner = bridge.data as JsonObject | undefined;
      if (inner?.ok === false) { json(res, { status: "error", code: "RESEARCH_ACTION_FAILED", message: String(inner.error ?? "研究 action 执行失败") }, 400); return; }
      json(res, { status: "ok", action: "list_models", data: inner?.data ?? inner ?? bridge.data }, 200);
      return;
    }
    if (req.method !== "POST") { json(res, { status: "error", code: "METHOD_NOT_ALLOWED", message: "仅支持 GET/POST" }, 405); return; }
    const result = await execute(req);
    json(res, result.body, result.status);
  };
}

export function registerMLResearchRoutes(
  api: QuantTradingRuntime,
  config: QuantTradingConfig,
  secure: (handler: HttpHandler) => HttpHandler,
): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/research/ml-ranking",
    handler: secure(createMLResearchHandler(config)),
  });
}

