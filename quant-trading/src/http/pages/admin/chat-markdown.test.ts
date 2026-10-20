import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { getChatMarkdownScript } from "./chat-markdown.js";
import { getAdminPageHtml } from "./full-page.js";

function renderer(): { render(input: string): string } {
  const context: Record<string, unknown> = {};
  context.window = context;
  runInNewContext(getChatMarkdownScript(), context);
  return context.qtChatMarkdown as { render(input: string): string };
}

describe("chat Markdown in the generated admin page", () => {
  it("emits valid browser scripts including the local renderer", () => {
    const html = getAdminPageHtml("");
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    expect(scripts).toHaveLength(2);
    for (const [, script] of scripts) expect(() => new Function(script)).not.toThrow();
  });

  it("renders analysis headings, emphasis, lists, tables and fenced code", () => {
    const html = renderer().render("## 分析\n\n**重点**\n\n- 行情\n- 新闻\n\n| 板块 | 涨跌幅 |\n|---|---|\n| 华为概念 | +4.37% |\n\n```python\nprint('ok')\n```");
    expect(html).toContain("<h2>分析</h2>");
    expect(html).toContain("<strong>重点</strong>");
    expect(html).toContain("<ul>");
    expect(html).toContain('<div class="chat-table-scroll"><table>');
    expect(html).toContain("<td>华为概念</td>");
    expect(html).toContain('<code class="language-python">');
  });

  it("keeps model-supplied HTML and unsafe link schemes inert", () => {
    const html = renderer().render('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[bad](javascript:alert(1))\n\n[encoded](jav&#x61;script:alert(1))\n\n![bad](data:text/html,payload)');
    expect(html).not.toMatch(/<script|<img|href="(?:javascript|data):/i);
    expect(html).toContain("&lt;script&gt;");
  });

  it("escapes code and isolates links opened in a new tab", () => {
    const html = renderer().render('```html\n<img src=x onerror="alert(1)">\n```\n\n[来源](https://example.com)');
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});
