import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { getAdminMainScript } from "./main-script.js";

function page(payload: unknown, reject = false) {
  const nodes: Record<string, { innerHTML: string; textContent: string }> = {
    "local-data-result": { innerHTML: "old success", textContent: "" },
    "local-data-status": { innerHTML: "old status", textContent: "" },
  };
  const context = {
    document: { getElementById: (id: string) => nodes[id] },
    api: () => reject ? Promise.reject(new Error("network unavailable")) : Promise.resolve(payload),
    toast: () => undefined,
    esc: (v: unknown) => String(v).replace(/</g, "&lt;"),
    checkLocalData: undefined as unknown as () => Promise<void>,
  };
  const script = getAdminMainScript();
  runInNewContext(script.slice(script.indexOf("function localDataPayload("), script.indexOf("function syncLocalData(")), context);
  return { nodes, check: context.checkLocalData };
}

describe("offline cache status displayed in the admin page", () => {
  it.each([
    { ok: false, error: "Cache dir not found: G:/missing" },
    { ok: true, data: { ok: false, error: "Cache dir not found: G:/missing" } },
    { status: "error", message: "Python unavailable" },
    { ok: true, data: {} },
  ])("shows failures and malformed payloads without a false all-current result", async payload => {
    const p = page(payload);
    await p.check();
    expect(p.nodes["local-data-result"].innerHTML).toContain("❌");
    expect(p.nodes["local-data-result"].innerHTML).not.toMatch(/undefined|全部最新/);
    expect(p.nodes["local-data-status"].textContent).toContain("检查失败");
  });

  it.each([false, true])("renders flat and historical wrapped responses (wrapped=%s)", async wrapped => {
    const data = { ok: true, cached_months: 178, latest_month: "202610", cache_dir: "G:/qtdata/cache/equity_tushare",
      stale_months: [{ month: "202606", reason: "missing_adj_factor" }] };
    const p = page(wrapped ? { ok: true, data } : data);
    await p.check();
    const html = p.nodes["local-data-result"].innerHTML;
    expect(html).toContain("178");
    expect(html).toContain("202606");
    expect(html).toContain(data.cache_dir);
    expect(html).not.toMatch(/undefined|全部最新/);
  });

  it("does not display an empty cache as all current", async () => {
    const p = page({ ok: true, cached_months: 0, latest_month: null, stale_months: [] });
    await p.check();
    expect(p.nodes["local-data-result"].innerHTML).toContain("尚无月度缓存");
    expect(p.nodes["local-data-result"].innerHTML).not.toContain("全部最新");
  });

  it("replaces a stale success display on request failure", async () => {
    const p = page(null, true);
    await p.check();
    expect(p.nodes["local-data-result"].innerHTML).toContain("network unavailable");
    expect(p.nodes["local-data-status"].textContent).toContain("检查失败");
  });
});
