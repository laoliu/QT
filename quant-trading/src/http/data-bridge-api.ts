/**
 * Data Bridge API — Mac gateway side of Mac↔Windows QMT K-line bridge.
 *
 * Protocol (轮询模式，无需 Windows 开放端口):
 *
 *   POST /quant/api/data-request
 *     Mac 发起请求，body: {codes: string[], period?: string, count?: number}
 *     返回: {status:"ok", requestId: string}
 *
 *   GET  /quant/api/data-request
 *     Windows 轮询待处理请求（建议 5s 间隔）
 *     有任务返回 200 + {requestId, codes, period, count}
 *     无任务返回 204 No Content
 *
 *   POST /quant/api/data-response
 *     Windows 提交 xtdata 数据，body: {requestId: string, data: KlineMap}
 *     返回: {status:"ok", received: number}
 *
 *   GET  /quant/api/data-response?requestId=<id>
 *     Mac 轮询是否有结果（建议 3s 间隔）
 *     有结果返回 200 + {requestId, data: KlineMap}
 *     无结果返回 204 No Content
 *
 * KlineMap 格式:
 *   { "600519": [{date:"20260227", open:1900, high:1920, low:1890, close:1910, volume:12345678}] }
 *   key Ϊ 6 位股票代码（与 stock_id 保持一致，方便 screener_engine 直接匹配）
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export type KlineBar = {
  date: string; // "YYYYMMDD"
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Map: 6-digit stock_id → bars sorted by date ascending */
export type KlineMap = Record<string, KlineBar[]>;

type PendingRequest = {
  requestId: string;
  codes: string[]; // QMT format: ["600519.SH", "000001.SZ"]
  period: string; // "1d" | "5m" | etc.
  count: number;
  createdAt: number;
};

type PendingResponse = {
  requestId: string;
  data: KlineMap;
  receivedAt: number;
};

// In-memory multi-slot store (supports concurrent requests)
const _pendingRequests = new Map<string, PendingRequest>();
const _pendingResponses = new Map<string, PendingResponse>();
let _lastRequestPeriod: string | null = null;
let _lastRequestCount: number | null = null;

const REQUEST_TTL_MS = 5 * 60 * 1000; // 请求 5 分钟超时
const RESPONSE_TTL_MS = 10 * 60 * 1000; // 响应 10 分钟保留

// ── 磁盘缓存：bridge 返回的 K 线数据持久化 ──────────────────────────

const CACHE_DIR = path.join(os.homedir(), ".openclaw", "extensions", "quant-trading", "cache");
const CACHE_FILE = path.join(CACHE_DIR, "kline-bridge-cache.json");

// 磁盘缓存 TTL：当天交易日内有效（过期后下次会重新从 bridge 请求）
// 历史 K 线不会日内变化，所以只需每天更新一次
const DISK_CACHE_TTL_MS = 18 * 3600 * 1000; // 18 Сʱ

type DiskCache = {
  savedAt: number;
  period: string;
  count: number;
  data: KlineMap;
};

function _loadDiskCache(period: string, count: number): KlineMap | null {
  try {
    if (!fs.existsSync(CACHE_FILE)) return null;
    const raw = JSON.parse(fs.readFileSync(CACHE_FILE, "utf-8")) as DiskCache;
    if (Date.now() - raw.savedAt > DISK_CACHE_TTL_MS) return null;
    if (raw.period !== period || raw.count !== count) return null;
    const stockCount = Object.keys(raw.data).length;
    if (stockCount === 0) return null;
    console.log(
      `[data-bridge] 磁盘缓存命中: ${stockCount} 只股票K线 (${new Date(raw.savedAt).toLocaleString()})`,
    );
    return raw.data;
  } catch {
    return null;
  }
}

function _saveDiskCache(data: KlineMap, period: string, count: number): void {
  try {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    const cache: DiskCache = { savedAt: Date.now(), period, count, data };
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache), "utf-8");
    console.log(`[data-bridge] 已写入磁盘缓存: ${Object.keys(data).length} 只股票`);
  } catch (e) {
    console.log(`[data-bridge] 写入磁盘缓存失败: ${e}`);
  }
}

/** Mac 发起数据请求 (POST /quant/api/data-request) */
export function handleDataBridgePostRequest(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { status: "error", error: "body 必须是对象 {codes, period?, count?}" };
  }
  const b = body as Record<string, unknown>;
  const codes = b["codes"];
  if (!Array.isArray(codes) || codes.length === 0) {
    return { status: "error", error: "codes 不能为空" };
  }

  const period = (b["period"] as string) ?? "1d";
  const count = typeof b["count"] === "number" ? b["count"] : 120;

  // 检查磁盘缓存：如有当天有效缓存，直接返回（无需等 Windows）
  const cached = _loadDiskCache(period, count);
  if (cached) {
    const requestId = crypto.randomUUID();
    _pendingResponses.set(requestId, { requestId, data: cached, receivedAt: Date.now() });
    console.log(`[data-bridge] 使用磁盘缓存 (${Object.keys(cached).length} 只)，跳过 Windows 请求`);
    return { status: "ok", requestId, cached: true };
  }

  const requestId = crypto.randomUUID();
  _pendingRequests.set(requestId, {
    requestId,
    codes: codes as string[],
    period,
    count,
    createdAt: Date.now(),
  });
  _lastRequestPeriod = period;
  _lastRequestCount = count;
  // TTL-based cleanup of stale requests
  _cleanupStale();
  console.log(`[data-bridge] 新请求 ${requestId}: ${(codes as string[]).length} 只股票`);
  return { status: "ok", requestId };
}

/** Windows 轮询待处理请求 (GET /quant/api/data-request) — 返回 null 表示 204 */
export function handleDataBridgePollRequest(): PendingRequest | null {
  // Return the oldest pending request
  for (const [, req] of _pendingRequests) {
    if (Date.now() - req.createdAt > REQUEST_TTL_MS) {
      _pendingRequests.delete(req.requestId);
      console.log(`[data-bridge] 请求 ${req.requestId} 超时，已丢弃`);
      continue;
    }
    return req;
  }
  return null;
}

/** Windows 提交 xtdata K 线数据 (POST /quant/api/data-response) */
export function handleDataBridgePostResponse(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { status: "error", error: "body 必须是对象 {requestId, data}" };
  }
  const b = body as Record<string, unknown>;
  const requestId = b["requestId"] as string | undefined;
  if (!requestId) return { status: "error", error: "缺少 requestId" };

  // 支持错误响应：bridge 无法获取数据时可以主动报告，清除 pending request
  const errorMsg = b["error"] as string | undefined;
  if (errorMsg) {
    _pendingRequests.delete(requestId);
    console.log(`[data-bridge] 请求 ${requestId} 失败: ${errorMsg}`);
    return { status: "ok", error: errorMsg };
  }

  const data = b["data"] as KlineMap | undefined;
  if (!data || typeof data !== "object") return { status: "error", error: "缺少 data" };

  const count = Object.keys(data).length;
  _pendingResponses.set(requestId, { requestId, data, receivedAt: Date.now() });

  // Look up original request's period/count before removing it (avoids stale globals)
  const originalReq = _pendingRequests.get(requestId);
  const reqPeriod = originalReq?.period ?? _lastRequestPeriod ?? "1d";
  const reqCount = originalReq?.count ?? _lastRequestCount ?? 120;
  _pendingRequests.delete(requestId); // 请求已处理，清除

  // 持久化到磁盘（下次请求可直接使用缓存，无需等 Windows）
  _saveDiskCache(data, reqPeriod, reqCount);

  console.log(`[data-bridge] 收到响应 ${requestId}: ${count} 只股票K线`);
  return { status: "ok", received: count };
}

/** Mac 轮询结果 (GET /quant/api/data-response?requestId=xxx) — 返回 null 表示 204 */
export function handleDataBridgePollResponse(requestId: string): PendingResponse | null {
  const resp = _pendingResponses.get(requestId);
  if (!resp) return null;
  if (Date.now() - resp.receivedAt > RESPONSE_TTL_MS) {
    _pendingResponses.delete(requestId);
    return null;
  }
  return resp;
}

/** Remove stale entries to prevent unbounded memory growth. */
function _cleanupStale(): void {
  const now = Date.now();
  for (const [id, req] of _pendingRequests) {
    if (now - req.createdAt > REQUEST_TTL_MS) _pendingRequests.delete(id);
  }
  for (const [id, resp] of _pendingResponses) {
    if (now - resp.receivedAt > RESPONSE_TTL_MS) _pendingResponses.delete(id);
  }
}
