import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { withAuth, withCors, withRateLimit, withSecurity, RateLimiter } from "./auth-middleware.js";

// ── Test helpers ──────────────────────────────────────────────────

function mockReq(overrides: Record<string, unknown> = {}): IncomingMessage {
  return {
    method: "GET",
    url: "/quant/api/portfolio",
    headers: {},
    socket: { remoteAddress: "127.0.0.1" },
    ...overrides,
  } as unknown as IncomingMessage;
}

interface MockRes {
  _status: number;
  _headers: Record<string, string>;
  _body: string;
  writeHead(status: number, headers?: Record<string, string>): MockRes;
  setHeader(name: string, value: string): MockRes;
  end(body?: string): void;
}

function mockRes(): MockRes {
  const res: MockRes = {
    _status: 200,
    _headers: {},
    _body: "",
    writeHead(status: number, headers?: Record<string, string>) {
      res._status = status;
      if (headers) Object.assign(res._headers, headers);
      return res;
    },
    setHeader(name: string, value: string) {
      res._headers[name.toLowerCase()] = value;
      return res;
    },
    end(body?: string) {
      res._body = body ?? "";
    },
  };
  return res;
}

function createOkHandler() {
  return vi.fn((_req: unknown, res: MockRes) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"ok":true}');
  });
}

// ── withAuth ──────────────────────────────────────────────────────

describe("withAuth", () => {
  it("passes through when apiToken is not configured", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {};
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq(), res as never);
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("rejects request with no Authorization header", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq(), res as never);
    expect(res._status).toBe(401);
    expect(res._body).toContain("Missing Authorization");
    expect(okHandler).not.toHaveBeenCalled();
  });

  it("rejects request with wrong token", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { authorization: "Bearer wrong-token" } }), res as never);
    expect(res._status).toBe(401);
    expect(res._body).toContain("Invalid");
    expect(okHandler).not.toHaveBeenCalled();
  });

  it("rejects request with non-Bearer scheme", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { authorization: "Basic dXNlcjpwYXNz" } }), res as never);
    expect(res._status).toBe(401);
    expect(okHandler).not.toHaveBeenCalled();
  });

  it("allows request with correct Bearer token", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(
      mockReq({ headers: { authorization: "Bearer my-secret-token-1234" } }),
      res as never,
    );
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("always passes OPTIONS requests (CORS preflight)", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ method: "OPTIONS" }), res as never);
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("allows public paths without token", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never, {
      publicPaths: ["/quant/dashboard/ui"],
    });
    const res = mockRes();
    await handler(mockReq({ url: "/quant/dashboard/ui" }), res as never);
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("still requires auth on non-public paths", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never, {
      publicPaths: ["/quant/dashboard/ui"],
    });
    const res = mockRes();
    await handler(mockReq({ url: "/quant/api/portfolio" }), res as never);
    expect(res._status).toBe(401);
  });

  it("is case-insensitive for Bearer keyword", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "my-secret-token-1234" };
    const handler = withAuth(config, okHandler as never);
    const res = mockRes();
    await handler(
      mockReq({ headers: { authorization: "bearer my-secret-token-1234" } }),
      res as never,
    );
    expect(okHandler).toHaveBeenCalledOnce();
  });
});

// ── withCors ──────────────────────────────────────────────────────

describe("withCors", () => {
  it("passes through when corsOrigins is not configured", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {};
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq(), res as never);
    expect(okHandler).toHaveBeenCalledOnce();
    expect(res._headers["access-control-allow-origin"]).toBeUndefined();
  });

  it("adds CORS headers for matching origin", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { corsOrigins: ["http://localhost:3000"] };
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { origin: "http://localhost:3000" } }), res as never);
    expect(res._headers["access-control-allow-origin"]).toBe("http://localhost:3000");
    expect(res._headers["access-control-allow-methods"]).toContain("GET");
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("does not add CORS headers for non-matching origin", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { corsOrigins: ["http://localhost:3000"] };
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { origin: "http://evil.com" } }), res as never);
    expect(res._headers["access-control-allow-origin"]).toBeUndefined();
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("handles OPTIONS preflight with 204 and no body", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { corsOrigins: ["http://localhost:3000"] };
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(
      mockReq({ method: "OPTIONS", headers: { origin: "http://localhost:3000" } }),
      res as never,
    );
    expect(res._status).toBe(204);
    expect(res._headers["access-control-allow-origin"]).toBe("http://localhost:3000");
    expect(okHandler).not.toHaveBeenCalled();
  });

  it("supports wildcard origin '*'", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { corsOrigins: ["*"] };
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { origin: "http://any-domain.com" } }), res as never);
    expect(res._headers["access-control-allow-origin"]).toBe("*");
  });

  it("sets Access-Control-Max-Age header", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { corsOrigins: ["http://localhost:3000"] };
    const handler = withCors(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { origin: "http://localhost:3000" } }), res as never);
    expect(res._headers["access-control-max-age"]).toBe("86400");
  });
});

// ── RateLimiter ───────────────────────────────────────────────────

describe("RateLimiter", () => {
  it("allows requests within limit", () => {
    const limiter = new RateLimiter(3, 60_000);
    const now = Date.now();
    const r1 = limiter.check("1.2.3.4", now);
    expect(r1.allowed).toBe(true);
    if (r1.allowed) expect(r1.remaining).toBe(2);

    const r2 = limiter.check("1.2.3.4", now + 100);
    expect(r2.allowed).toBe(true);
    if (r2.allowed) expect(r2.remaining).toBe(1);

    const r3 = limiter.check("1.2.3.4", now + 200);
    expect(r3.allowed).toBe(true);
    if (r3.allowed) expect(r3.remaining).toBe(0);
  });

  it("rejects requests exceeding limit", () => {
    const limiter = new RateLimiter(2, 60_000);
    const now = Date.now();
    limiter.check("1.2.3.4", now);
    limiter.check("1.2.3.4", now + 100);
    const r3 = limiter.check("1.2.3.4", now + 200);
    expect(r3.allowed).toBe(false);
    if (!r3.allowed) expect(r3.retryAfterMs).toBeGreaterThan(0);
  });

  it("resets after window expires", () => {
    const limiter = new RateLimiter(2, 1000);
    const now = Date.now();
    limiter.check("1.2.3.4", now);
    limiter.check("1.2.3.4", now + 100);
    // Window expired
    const r = limiter.check("1.2.3.4", now + 1100);
    expect(r.allowed).toBe(true);
    if (r.allowed) expect(r.remaining).toBe(1);
  });

  it("tracks IPs independently", () => {
    const limiter = new RateLimiter(1, 60_000);
    const now = Date.now();
    const r1 = limiter.check("1.1.1.1", now);
    expect(r1.allowed).toBe(true);
    const r2 = limiter.check("2.2.2.2", now);
    expect(r2.allowed).toBe(true);
    expect(limiter.size).toBe(2);
  });

  it("cleanup removes stale entries", () => {
    const limiter = new RateLimiter(10, 1000);
    const now = Date.now();
    limiter.check("a", now);
    limiter.check("b", now);
    expect(limiter.size).toBe(2);
    limiter.cleanup(now + 2000);
    expect(limiter.size).toBe(0);
  });

  it("auto-cleans stale entries via periodic timer", async () => {
    vi.useFakeTimers();
    const limiter = new RateLimiter(10, 500);
    try {
      limiter.check("x");
      limiter.check("y");
      expect(limiter.size).toBe(2);
      // Advance past the window to trigger auto-cleanup
      vi.advanceTimersByTime(600);
      expect(limiter.size).toBe(0);
    } finally {
      limiter.dispose();
      vi.useRealTimers();
    }
  });
});

// ── withRateLimit ─────────────────────────────────────────────────

describe("withRateLimit", () => {
  it("skips rate limiting when no apiToken and no rateLimit config", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {};
    const handler = withRateLimit(config, okHandler as never);
    // Should be the same function (no wrapping)
    expect(handler).toBe(okHandler);
  });

  it("applies rate limiting when apiToken is set", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "tok" };
    const limiter = new RateLimiter(2, 60_000);
    const handler = withRateLimit(config, okHandler as never, limiter);

    const res1 = mockRes();
    await handler(mockReq(), res1 as never);
    expect(okHandler).toHaveBeenCalledOnce();

    const res2 = mockRes();
    await handler(mockReq(), res2 as never);
    expect(okHandler).toHaveBeenCalledTimes(2);

    // Third request should be rejected
    const res3 = mockRes();
    await handler(mockReq(), res3 as never);
    expect(res3._status).toBe(429);
    expect(res3._body).toContain("Too many requests");
    expect(res3._headers["retry-after"]).toBeDefined();
  });

  it("exempts OPTIONS requests from rate limiting", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "tok" };
    const limiter = new RateLimiter(1, 60_000);
    const handler = withRateLimit(config, okHandler as never, limiter);

    // Use up the limit
    const res1 = mockRes();
    await handler(mockReq(), res1 as never);

    // OPTIONS should still pass
    const res2 = mockRes();
    await handler(mockReq({ method: "OPTIONS" }), res2 as never);
    expect(okHandler).toHaveBeenCalledTimes(2);
  });

  it("sets X-RateLimit-Remaining header", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { rateLimit: { maxRequests: 10, windowMs: 60_000 } };
    const handler = withRateLimit(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq(), res as never);
    expect(res._headers["x-ratelimit-remaining"]).toBeDefined();
  });

  it("uses x-forwarded-for for IP detection", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = { apiToken: "tok" };
    const limiter = new RateLimiter(1, 60_000);
    const handler = withRateLimit(config, okHandler as never, limiter);

    // IP from x-forwarded-for
    const res1 = mockRes();
    await handler(mockReq({ headers: { "x-forwarded-for": "10.0.0.1, 10.0.0.2" } }), res1 as never);
    expect(okHandler).toHaveBeenCalledOnce();

    // Different IP via socket still allowed
    const res2 = mockRes();
    await handler(mockReq(), res2 as never);
    expect(okHandler).toHaveBeenCalledTimes(2);
  });
});

// ── withSecurity (composed) ───────────────────────────────────────

describe("withSecurity", () => {
  it("applies full security stack: auth + cors + rate-limit", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {
      apiToken: "secure-token-abcdef",
      corsOrigins: ["http://localhost:3000"],
      rateLimit: { maxRequests: 100, windowMs: 60_000 },
    };
    const handler = withSecurity(config, okHandler as never);
    const res = mockRes();
    await handler(
      mockReq({
        headers: {
          authorization: "Bearer secure-token-abcdef",
          origin: "http://localhost:3000",
        },
      }),
      res as never,
    );
    expect(okHandler).toHaveBeenCalledOnce();
    expect(res._headers["access-control-allow-origin"]).toBe("http://localhost:3000");
    expect(res._headers["x-ratelimit-remaining"]).toBeDefined();
  });

  it("rejects unauthenticated requests after CORS/rate-limit pass", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {
      apiToken: "secure-token-abcdef",
      corsOrigins: ["http://localhost:3000"],
    };
    const handler = withSecurity(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq({ headers: { origin: "http://localhost:3000" } }), res as never);
    expect(res._status).toBe(401);
    expect(okHandler).not.toHaveBeenCalled();
  });

  it("allows public paths through full security stack", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {
      apiToken: "tok-1234567890123456",
      corsOrigins: ["http://localhost:3000"],
    };
    const handler = withSecurity(config, okHandler as never, {
      publicPaths: ["/quant/dashboard/ui"],
    });
    const res = mockRes();
    await handler(
      mockReq({
        url: "/quant/dashboard/ui",
        headers: { origin: "http://localhost:3000" },
      }),
      res as never,
    );
    expect(okHandler).toHaveBeenCalledOnce();
  });

  it("handles OPTIONS preflight through composed stack", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {
      apiToken: "tok-1234567890123456",
      corsOrigins: ["http://localhost:3000"],
    };
    const handler = withSecurity(config, okHandler as never);
    const res = mockRes();
    await handler(
      mockReq({
        method: "OPTIONS",
        headers: { origin: "http://localhost:3000" },
      }),
      res as never,
    );
    // CORS handled: 204 returned, handler NOT called
    expect(res._status).toBe(204);
    expect(res._headers["access-control-allow-origin"]).toBe("http://localhost:3000");
  });

  it("works with no security config (fully open)", async () => {
    const okHandler = createOkHandler();
    const config: QuantTradingConfig = {};
    const handler = withSecurity(config, okHandler as never);
    const res = mockRes();
    await handler(mockReq(), res as never);
    expect(okHandler).toHaveBeenCalledOnce();
  });
});
