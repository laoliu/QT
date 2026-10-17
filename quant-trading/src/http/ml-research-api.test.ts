import { Readable } from "node:stream";
import { describe, expect, it } from "vitest";
import type { IncomingMessage, ServerResponse } from "node:http";
import { createMLResearchHandler } from "./ml-research-api.js";

function request(method: string, body?: Record<string, unknown>, headers: Record<string, string> = {}) {
  const req = Readable.from(body === undefined ? [] : [Buffer.from(JSON.stringify(body))]) as unknown as IncomingMessage;
  Object.assign(req, { method, headers });
  return req;
}

function response() {
  let status = 0;
  let payload = "";
  const res = {
    writeHead(code: number) { status = code; return res; },
    end(value?: string) { payload = value ?? ""; return res; },
  } as unknown as ServerResponse;
  return { res, get status() { return status; }, get body() { return JSON.parse(payload) as Record<string, unknown>; } };
}

const config = { dataDir: "./.pytest-ml-research-api" } as never;

describe("canonical ML research HTTP boundary", () => {
  it("requires explicit confirmation before paper execution", async () => {
    let calls = 0;
    const handler = createMLResearchHandler(config, {
      run: async () => { calls++; return { ok: true, data: { ok: true, data: {} } }; },
      audit: async () => undefined,
    });
    const out = response();
    await handler(request("POST", { action: "execute_paper", idempotencyKey: "confirm-1" }), out.res);
    expect(out.status).toBe(400);
    expect(out.body.code).toBe("CONFIRM_REQUIRED");
    expect(calls).toBe(0);
  });

  it("deduplicates an execution request and rejects key reuse with a different payload", async () => {
    let calls = 0;
    const audits: Record<string, unknown>[] = [];
    const handler = createMLResearchHandler(config, {
      run: async () => {
        calls++;
        return { ok: true, data: { ok: true, data: { mode: "paper", orderIds: ["o-1"] } } };
      },
      audit: async (event) => { audits.push(event); },
    });
    const first = response();
    await handler(request("POST", { action: "execute_paper", confirm: true, planPath: "plan.json", accountValue: 1000 }, { "idempotency-key": "same-key" }), first.res);
    const replay = response();
    await handler(request("POST", { action: "execute_paper", confirm: true, planPath: "plan.json", accountValue: 1000 }, { "idempotency-key": "same-key" }), replay.res);
    const conflict = response();
    await handler(request("POST", { action: "execute_paper", confirm: true, planPath: "other.json", accountValue: 1000 }, { "idempotency-key": "same-key" }), conflict.res);
    expect(first.status).toBe(200);
    expect(replay.status).toBe(200);
    expect(replay.body.idempotentReplay).toBe(true);
    expect(conflict.status).toBe(409);
    expect(conflict.body.code).toBe("IDEMPOTENCY_CONFLICT");
    expect(calls).toBe(1);
    expect(audits).toHaveLength(1);
  });

  it("maps Python action failures and blocks live payloads", async () => {
    const handler = createMLResearchHandler(config, {
      run: async () => ({ ok: true, data: { ok: false, error: "bad paper plan" } }),
      audit: async () => undefined,
    });
    const failed = response();
    await handler(request("POST", { action: "execute_paper", confirm: true, planPath: "plan.json", accountValue: 1000 }, { "idempotency-key": "failure-1" }), failed.res);
    expect(failed.status).toBe(422);
    expect(failed.body.code).toBe("RESEARCH_ACTION_FAILED");

    const live = response();
    await handler(request("POST", { action: "execute_paper", confirm: true, mode: "live", planPath: "plan.json", accountValue: 1000 }, { "idempotency-key": "live-1" }), live.res);
    expect(live.status).toBe(403);
    expect(live.body.code).toBe("LIVE_NOT_ALLOWED");
  });
});

