import { Readable } from "node:stream";
import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it } from "vitest";
import { createResearchOperationsHandler, registerResearchOperationsRoutes } from "./research-operations-api.js";
import { getResearchOperationsHtml } from "./pages/research-operations-page.js";

function request(method = "GET", query = "") {
  return Object.assign(Readable.from([]), { method, url: "/quant/api/research/operations" + query }) as IncomingMessage;
}
function response() {
  let status = 0, payload = "";
  const headers: Record<string, string> = {};
  const res = {
    setHeader(k: string, v: string) { headers[k] = v; },
    writeHead(code: number) { status = code; return res; },
    end(value?: string) { payload = value ?? ""; },
  } as unknown as ServerResponse;
  return { res, headers, get status() { return status; }, get body() { return JSON.parse(payload); } };
}

describe("research operations read-only boundary", () => {
  it("blocks writes, file paths and traversal before invoking a reader", async () => {
    let calls = 0;
    const handler = createResearchOperationsHandler({ read: async () => { calls++; return {}; } });
    for (const [method, query, code] of [["POST", "", 405], ["DELETE", "", 405],
      ["GET", "?date=../../.env", 400], ["GET", "?path=E:/work/QT/.env", 400],
      ["GET", "?scope=live", 400], ["GET", "?date=2026-02-30", 400],
      ["GET", "?offset=-1", 400], ["GET", "?limit=1000", 400]] as const) {
      const out = response(); await handler(request(method, query), out.res); expect(out.status).toBe(code);
    }
    expect(calls).toBe(0);
  });

  it("shares concurrent reads, caches observations, and surfaces missing scheduler evidence", async () => {
    let calls = 0;
    const handler = createResearchOperationsHandler({
      read: async () => { calls++; return { observed_at: "fixture", paper: { passed: false } }; },
      schedule: async () => { throw new Error("secret scheduler stderr"); },
    });
    const first = response(), second = response(), third = response();
    await Promise.all([handler(request(), first.res), handler(request(), second.res)]);
    await handler(request(), third.res);
    expect(calls).toBe(1);
    expect(third.body.cached).toBe(true);
    expect(first.body.data.scheduler.available).toBe(false);
    expect(JSON.stringify(first.body)).not.toContain("secret scheduler stderr");
    expect(first.headers["Cache-Control"]).toBe("no-store");
  });

  it("passes bounded prediction paging and hides Python errors without returning stale success", async () => {
    const queries: unknown[] = [];
    const handler = createResearchOperationsHandler({ read: async (query) => {
      queries.push(query); if (query.scope === "paper") throw new Error("TUSHARE_TOKEN=private");
      return { total: 120, rows: [{ symbol: "600000.SH", rank: 51 }] };
    } });
    const ok = response(); await handler(request("GET", "?section=predictions&scope=research&date=2026-09-09&offset=50&limit=50"), ok.res);
    expect(ok.status).toBe(200);
    expect(queries[0]).toMatchObject({ date: "2026-09-09", offset: 50, limit: 50 });
    const failed = response(); await handler(request("GET", "?section=predictions"), failed.res);
    expect(failed.status).toBe(503);
    expect(JSON.stringify(failed.body)).not.toContain("private");
  });

  it("protects the API with the existing security wrapper and registers a navigable page", () => {
    const registrations: any[] = [], secured: any[] = [];
    registerResearchOperationsRoutes({ registerHttpRoute: (route: any) => registrations.push(route) } as never,
      {} as never, (handler) => { secured.push(handler); return handler; }, (handler) => handler);
    expect(secured).toHaveLength(1);
    expect(registrations.map((r) => r.path)).toEqual(["/quant/api/research/operations", "/quant/admin/research"]);
  });

  it("emits valid browser JavaScript and escapes script-breaking tokens", () => {
    const html = getResearchOperationsHtml("", '</script><img src=x onerror="alert(1)">');
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    expect(scripts).toHaveLength(1);
    expect(() => new Function(scripts[0][1])).not.toThrow();
    expect(html).not.toContain('</script><img');
    expect(html).toContain("历史研究结果 · OOS 回测");
    expect(html).toContain("真实逐日 Paper · 独立账本");
    expect(html).toContain("模型评分排名第 ");
    expect(html).toContain("target.reasonLabel");
  });
});
