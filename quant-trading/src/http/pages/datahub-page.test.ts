import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { getDatahubPageHtml } from "./datahub-page.js";

function renderBlockTrades(data: unknown) {
  const html = getDatahubPageHtml("/base");
  const script = html.slice(html.indexOf("function renderBlockTrades"), html.indexOf("async function fetchInstitutional"));
  const context: {
    BASE: string;
    encodeURIComponent: typeof encodeURIComponent;
    esc: (value: unknown) => string;
    numFmt: (value: unknown) => string;
    input?: unknown;
    result?: string;
  } = {
    BASE: "/base",
    encodeURIComponent,
    esc: (value: unknown) => String(value).replace(/[&<>\"']/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[char] || char)),
    numFmt: (value: unknown) => String(value ?? "-"),
  };
  context.input = data;
  runInNewContext(`${script}\nresult = renderBlockTrades(input);`, context);
  return context.result || "";
}

describe("institutional holdings links", () => {
  it("links block trade stock codes to stock details", () => {
    const output = renderBlockTrades({
      count: 1,
      trades: [{ symbol: "600519.SZ", date: "2026-10-21", name: "贵州茅台", premium_pct: 1.2 }],
    });

    expect(output).toContain('href="/base/quant/admin/stock?symbol=600519.SZ"');
    expect(output).toContain('target="_blank"');
    expect(output).toContain(">600519.SZ</a>");
  });

  it("keeps a missing stock code as a plain placeholder", () => {
    const output = renderBlockTrades({ trades: [{ date: "2026-10-21", name: "未知" }] });

    expect(output).toContain("<td>-</td>");
    expect(output).not.toContain("/quant/admin/stock?symbol=");
  });
});
