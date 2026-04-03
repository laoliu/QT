/**
 * HTTP security middleware for quant-trading API routes.
 *
 * Provides Bearer token authentication, CORS header injection,
 * and per-IP rate limiting as composable higher-order functions
 * that wrap the raw `(req, res) => void` plugin HTTP handler.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { QuantTradingConfig } from "../types.js";

/** Plugin HTTP route handler signature (mirrors OpenClawPluginHttpRouteHandler). */
export type HttpHandler = (req: IncomingMessage, res: ServerResponse) => Promise<void> | void;

// ── Bearer Token Auth ─────────────────────────────────────────────

export interface AuthOptions {
  /** Routes that skip Bearer auth (still get CORS / rate-limit). */
  publicPaths?: string[];
}

/**
 * Wrap a handler with Bearer token authentication.
 *
 * - If `config.apiToken` is not set, the handler is returned as-is (open access).
 * - OPTIONS requests are always passed through (for CORS preflight).
 * - Paths listed in `opts.publicPaths` are exempt from auth.
 */
export function withAuth(
  config: QuantTradingConfig,
  handler: HttpHandler,
  opts?: AuthOptions,
): HttpHandler {
  const token = config.apiToken;
  if (!token) return handler;

  const publicPaths = new Set(opts?.publicPaths ?? []);

  return (req, res) => {
    // Always allow OPTIONS (CORS preflight)
    if (req.method === "OPTIONS") return handler(req, res);

    // Allow public paths
    const pathname = extractPathname(req.url);
    if (publicPaths.has(pathname)) return handler(req, res);

    const authHeader = req.headers.authorization;
    if (!authHeader) {
      return sendJson(res, 401, { status: "error", message: "Missing Authorization header" });
    }

    const match = /^Bearer\s+(.+)$/i.exec(authHeader);
    if (!match || !timingSafeCompareStr(match[1], token)) {
      return sendJson(res, 401, { status: "error", message: "Invalid or expired token" });
    }

    return handler(req, res);
  };
}

// ── CORS ──────────────────────────────────────────────────────────

/**
 * Wrap a handler with CORS headers.
 *
 * - If `config.corsOrigins` is empty/unset, no CORS headers are added.
 * - Matching is exact (no wildcards); use `["*"]` for open access (not recommended).
 * - OPTIONS preflight returns 204 immediately after writing CORS headers.
 */
export function withCors(config: QuantTradingConfig, handler: HttpHandler): HttpHandler {
  const origins = config.corsOrigins;
  if (!origins || origins.length === 0) return handler;

  const originSet = new Set(origins);
  const allowAll = originSet.has("*");

  return (req, res) => {
    const reqOrigin = req.headers.origin;
    if (reqOrigin && (allowAll || originSet.has(reqOrigin))) {
      res.setHeader("Access-Control-Allow-Origin", allowAll ? "*" : reqOrigin);
      res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
      res.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
      res.setHeader("Access-Control-Max-Age", "86400");
    }

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    return handler(req, res);
  };
}

// ── Rate Limiter ──────────────────────────────────────────────────

interface RateLimitEntry {
  count: number;
  windowStart: number;
}

/**
 * Simple per-IP sliding-window rate limiter.
 *
 * Maintains state in-memory — entries are cleaned up lazily
 * when a new request arrives for that IP after the window expires.
 */
export class RateLimiter {
  private readonly maxRequests: number;
  private readonly windowMs: number;
  private readonly entries = new Map<string, RateLimitEntry>();

  constructor(maxRequests = 60, windowMs = 60_000) {
    this.maxRequests = maxRequests;
    this.windowMs = windowMs;
    // Periodic cleanup of stale entries to prevent unbounded memory growth
    this._cleanupTimer = setInterval(() => this.cleanup(), windowMs);
    // Allow process to exit even if timer is active
    if (this._cleanupTimer.unref) this._cleanupTimer.unref();
  }

  private readonly _cleanupTimer: ReturnType<typeof setInterval>;

  /**
   * Check whether a request from `ip` is allowed.
   * Returns `{ allowed: true, remaining }` or `{ allowed: false, retryAfterMs }`.
   */
  check(
    ip: string,
    now = Date.now(),
  ): { allowed: true; remaining: number } | { allowed: false; retryAfterMs: number } {
    let entry = this.entries.get(ip);

    // First request or window expired → reset
    if (!entry || now - entry.windowStart >= this.windowMs) {
      entry = { count: 1, windowStart: now };
      this.entries.set(ip, entry);
      return { allowed: true, remaining: this.maxRequests - 1 };
    }

    entry.count++;
    if (entry.count > this.maxRequests) {
      const retryAfterMs = this.windowMs - (now - entry.windowStart);
      return { allowed: false, retryAfterMs: Math.max(retryAfterMs, 0) };
    }

    return { allowed: true, remaining: this.maxRequests - entry.count };
  }

  /** Remove stale entries (call periodically for long-running processes). */
  cleanup(now = Date.now()): void {
    for (const [ip, entry] of this.entries) {
      if (now - entry.windowStart >= this.windowMs) {
        this.entries.delete(ip);
      }
    }
  }

  /** Current number of tracked IPs (for tests / monitoring). */
  get size(): number {
    return this.entries.size;
  }

  /** Stop the periodic cleanup timer. */
  dispose(): void {
    clearInterval(this._cleanupTimer);
  }
}

/**
 * Wrap a handler with per-IP rate limiting.
 *
 * - If `config.rateLimit` is not set, uses defaults (60 req / 60s).
 * - Disabled entirely if `config.rateLimit` is explicitly `undefined`
 *   AND **no** `apiToken` is set (backward-compatible open mode).
 * - OPTIONS requests are exempt from rate limiting.
 */
export function withRateLimit(
  config: QuantTradingConfig,
  handler: HttpHandler,
  limiter?: RateLimiter,
): HttpHandler {
  const rl = config.rateLimit;
  // If neither apiToken nor rateLimit is configured, skip rate limiting entirely
  if (!config.apiToken && rl === undefined) return handler;

  const rateLimiter = limiter ?? new RateLimiter(rl?.maxRequests ?? 60, rl?.windowMs ?? 60_000);

  return (req, res) => {
    // OPTIONS / HEAD are exempt
    if (req.method === "OPTIONS" || req.method === "HEAD") return handler(req, res);

    const ip = extractIp(req);
    const result = rateLimiter.check(ip);

    if (!result.allowed) {
      const retryAfterSecs = Math.ceil(result.retryAfterMs / 1000);
      res.setHeader("Retry-After", String(retryAfterSecs));
      return sendJson(res, 429, {
        status: "error",
        message: "Too many requests",
        retryAfter: retryAfterSecs,
      });
    }

    res.setHeader("X-RateLimit-Remaining", String(result.remaining));
    return handler(req, res);
  };
}

// ── Composed Security Wrapper ─────────────────────────────────────

/**
 * Apply the full security stack: Rate Limit → CORS → Auth → Handler.
 *
 * Order matters:
 * 1. Rate limit first (reject abusers before any processing).
 * 2. CORS second (so preflight OPTIONS get proper headers even when rate-limited).
 * 3. Auth last (only authenticated requests reach the handler).
 */
export function withSecurity(
  config: QuantTradingConfig,
  handler: HttpHandler,
  opts?: AuthOptions & { rateLimiter?: RateLimiter },
): HttpHandler {
  const authed = withAuth(config, handler, opts);
  const corsed = withCors(config, authed);
  return withRateLimit(config, corsed, opts?.rateLimiter);
}

// ── Helpers ───────────────────────────────────────────────────────

function extractPathname(url: string | undefined): string {
  if (!url) return "/";
  const qIdx = url.indexOf("?");
  return qIdx >= 0 ? url.slice(0, qIdx) : url;
}

function extractIp(req: IncomingMessage): string {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string") {
    return forwarded.split(",")[0].trim();
  }
  return req.socket?.remoteAddress ?? "unknown";
}

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(body);
}

/** Constant-time string comparison to prevent timing attacks on token auth. */
export function timingSafeCompareStr(a: string, b: string): boolean {
  // Hash both strings to fixed-length buffers so comparison time
  // does not leak the input length.
  const aHash = createHash("sha256").update(a).digest();
  const bHash = createHash("sha256").update(b).digest();
  return timingSafeEqual(aHash, bHash);
}
