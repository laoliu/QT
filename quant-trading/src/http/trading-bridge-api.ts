/**
 * Trading Bridge API — Mac↔Windows QMT remote trading bridge.
 *
 * Same polling pattern as data-bridge-api.ts:
 *
 *   POST /quant/api/trading-request
 *     Mac submits a trading command (place_order, cancel_order, query_positions, etc.)
 *     Returns: {status:"ok", requestId: string}
 *
 *   GET  /quant/api/trading-request
 *     Windows polls for pending trading commands
 *     Has task: 200 + {requestId, method, params}
 *     No task: 204
 *
 *   POST /quant/api/trading-response
 *     Windows pushes execution result
 *     Returns: {status:"ok"}
 *
 *   GET  /quant/api/trading-response?requestId=<id>
 *     Mac polls for result (blocks up to timeout)
 *     Has result: 200 + {requestId, ok, data?, error?}
 *     No result: 204
 *
 * Supported methods:
 *   connect, disconnect, place_order, cancel_order,
 *   query_order, query_orders, query_positions, query_account,
 *   subscribe_quote, unsubscribe_quote
 */

import crypto from "node:crypto";

export type TradingMethod =
  | "connect"
  | "disconnect"
  | "place_order"
  | "cancel_order"
  | "query_order"
  | "query_orders"
  | "query_positions"
  | "query_account"
  | "subscribe_quote"
  | "unsubscribe_quote"
  | "get_quote"
  | "get_klines"
  | "save_strategy_file";

type PendingTradingRequest = {
  requestId: string;
  method: TradingMethod;
  params: Record<string, unknown>;
  createdAt: number;
};

type TradingResponse = {
  requestId: string;
  ok: boolean;
  data?: unknown;
  error?: string;
  receivedAt: number;
};

// ── In-memory stores ──────────────────────────────────────────────

const _pendingRequests = new Map<string, PendingTradingRequest>();
const _responses = new Map<string, TradingResponse>();

/** Callbacks waiting for a specific requestId */
const _waiters = new Map<string, Array<(resp: TradingResponse) => void>>();

const REQUEST_TTL_MS = 30_000; // 30s timeout for trading commands
const RESPONSE_TTL_MS = 5 * 60_000; // keep responses 5 min

// ── Event push store (fills, order_status, quotes from Windows) ──

export type TradingEvent = {
  seq: number;
  event: string; // "fill" | "order_status" | "quote"
  data: unknown;
  receivedAt: number;
};

const _eventQueue: TradingEvent[] = [];
const MAX_EVENT_QUEUE = 500;
const _eventListeners: Array<(evt: TradingEvent) => void> = [];
let _nextEventSeq = 1;

/** Register a listener for real-time events from Windows. */
export function onTradingEvent(cb: (evt: TradingEvent) => void): () => void {
  _eventListeners.push(cb);
  return () => {
    const idx = _eventListeners.indexOf(cb);
    if (idx >= 0) _eventListeners.splice(idx, 1);
  };
}

// ── Mac side: submit trading request ──────────────────────────────

export function submitTradingRequest(
  method: TradingMethod,
  params: Record<string, unknown>,
): string {
  _cleanupStale();
  const requestId = crypto.randomUUID();
  _pendingRequests.set(requestId, { requestId, method, params, createdAt: Date.now() });
  console.log(`[trading-bridge] 新请求 ${requestId}: ${method}`);
  return requestId;
}

/** Wait for a trading response (resolves when Windows pushes result or timeout). */
export function waitForTradingResponse(
  requestId: string,
  timeoutMs = 15_000,
): Promise<TradingResponse> {
  // Check if already have a response
  const existing = _responses.get(requestId);
  if (existing) return Promise.resolve(existing);

  return new Promise<TradingResponse>((resolve) => {
    const timer = setTimeout(() => {
      // Remove waiter
      const arr = _waiters.get(requestId);
      if (arr) {
        const idx = arr.indexOf(resolve);
        if (idx >= 0) arr.splice(idx, 1);
        if (arr.length === 0) _waiters.delete(requestId);
      }
      // Timeout → return error
      resolve({
        requestId,
        ok: false,
        error: "Windows 端未响应（超时 " + Math.round(timeoutMs / 1000) + "s）",
        receivedAt: Date.now(),
      });
    }, timeoutMs);

    const waiterFn = (resp: TradingResponse) => {
      clearTimeout(timer);
      resolve(resp);
    };

    if (!_waiters.has(requestId)) _waiters.set(requestId, []);
    _waiters.get(requestId)!.push(waiterFn);
  });
}

// ── Windows side: poll pending requests ───────────────────────────

/** GET /quant/api/trading-request — Windows polls for pending commands. */
export function handleTradingPollRequest(): PendingTradingRequest | null {
  for (const [, req] of _pendingRequests) {
    if (Date.now() - req.createdAt > REQUEST_TTL_MS) {
      _pendingRequests.delete(req.requestId);
      console.log(`[trading-bridge] 请求 ${req.requestId} 超时，已丢弃`);
      continue;
    }
    return req;
  }
  return null;
}

/** POST /quant/api/trading-request — Mac submits a trading command via HTTP. */
export function handleTradingPostRequest(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { status: "error", error: "body 必须是对象 {method, params}" };
  }
  const b = body as Record<string, unknown>;
  const method = b["method"] as TradingMethod;
  if (!method) return { status: "error", error: "缺少 method" };
  const params = (b["params"] as Record<string, unknown>) ?? {};
  const requestId = submitTradingRequest(method, params);
  return { status: "ok", requestId };
}

// ── Windows side: push response ───────────────────────────────────

/** POST /quant/api/trading-response — Windows pushes execution result. */
export function handleTradingPostResponse(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { status: "error", error: "body 必须是对象 {requestId, ok, data?, error?}" };
  }
  const b = body as Record<string, unknown>;
  const requestId = b["requestId"] as string | undefined;
  if (!requestId) return { status: "error", error: "缺少 requestId" };

  const resp: TradingResponse = {
    requestId,
    ok: !!b["ok"],
    data: b["data"],
    error: b["error"] as string | undefined,
    receivedAt: Date.now(),
  };
  _responses.set(requestId, resp);
  _pendingRequests.delete(requestId);

  // Notify waiters
  const waiters = _waiters.get(requestId);
  if (waiters) {
    for (const fn of waiters) fn(resp);
    _waiters.delete(requestId);
  }

  console.log(
    `[trading-bridge] 收到响应 ${requestId}: ${resp.ok ? "ok" : "error"} ${resp.error ?? ""}`,
  );
  return { status: "ok" };
}

/** GET /quant/api/trading-response?requestId=xxx — Mac polls for result. */
export function handleTradingPollResponse(requestId: string): TradingResponse | null {
  const resp = _responses.get(requestId);
  if (!resp) return null;
  if (Date.now() - resp.receivedAt > RESPONSE_TTL_MS) {
    _responses.delete(requestId);
    return null;
  }
  return resp;
}

// ── Windows side: push real-time events ───────────────────────────

/** POST /quant/api/trading-events — Windows pushes fill/quote/order_status events. */
export function handleTradingEventsPost(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { status: "error", error: "body 必须是对象 {events: [{event, data}]}" };
  }
  const b = body as Record<string, unknown>;
  const events = b["events"] as Array<{ event: string; data: unknown }> | undefined;
  if (!Array.isArray(events)) return { status: "error", error: "缺少 events 数组" };

  let count = 0;
  for (const evt of events) {
    if (!evt.event) continue;
    const te: TradingEvent = {
      seq: _nextEventSeq++,
      event: evt.event,
      data: evt.data,
      receivedAt: Date.now(),
    };
    _eventQueue.push(te);
    if (_eventQueue.length > MAX_EVENT_QUEUE) _eventQueue.shift();
    for (const cb of _eventListeners) {
      try {
        cb(te);
      } catch {
        // ignore listener errors
      }
    }
    count++;
  }
  return { status: "ok", received: count, latestSeq: _eventQueue.at(-1)?.seq ?? 0 };
}

/** GET /quant/api/trading-events — Mac reads queued events. */
export function handleTradingEventsPoll(
  afterSeq?: number,
  since?: number,
): {
  events: TradingEvent[];
  latestSeq: number;
} {
  const seqCutoff = afterSeq ?? 0;
  const timeCutoff = since ?? 0;
  const events = _eventQueue.filter((event) => {
    if (seqCutoff > 0) return event.seq > seqCutoff;
    if (timeCutoff > 0) return event.receivedAt > timeCutoff;
    return true;
  });
  return { events, latestSeq: _eventQueue.at(-1)?.seq ?? 0 };
}

export function resetTradingBridgeStateForTests(): void {
  _pendingRequests.clear();
  _responses.clear();
  _waiters.clear();
  _eventQueue.splice(0, _eventQueue.length);
  _eventListeners.splice(0, _eventListeners.length);
  _nextEventSeq = 1;
}

// ── Cleanup ──────────────────────────────────────────────────────

function _cleanupStale(): void {
  const now = Date.now();
  for (const [id, req] of _pendingRequests) {
    if (now - req.createdAt > REQUEST_TTL_MS) _pendingRequests.delete(id);
  }
  for (const [id, resp] of _responses) {
    if (now - resp.receivedAt > RESPONSE_TTL_MS) _responses.delete(id);
  }
}
