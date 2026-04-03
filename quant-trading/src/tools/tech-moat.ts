import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

function collectTechMoatInputs(payload: Record<string, unknown>): {
  companyName: string;
  newsTitles: string[];
} {
  const companyName = String(payload.name ?? payload.symbol ?? "").trim();
  const rows: string[] = [];

  const pushRow = (value: unknown): void => {
    const text = String(value ?? "").trim();
    if (text) rows.push(text);
  };

  const news = Array.isArray(payload.news) ? payload.news : [];
  for (const item of news) {
    const row = item as Record<string, unknown>;
    const title = String(row.title ?? "").trim();
    const content = String(row.content ?? "").trim();
    if (title && content) pushRow(`${title} ${content}`);
    else pushRow(title || content);
  }

  const research = Array.isArray(payload.research) ? payload.research : [];
  for (const item of research) {
    const row = item as Record<string, unknown>;
    const parts = [row.title, row.rating, row.source]
      .map((value) => String(value ?? "").trim())
      .filter(Boolean);
    if (parts.length > 0) pushRow(parts.join(" "));
  }

  const seen = new Set<string>();
  const newsTitles = rows.filter((entry) => {
    if (seen.has(entry)) return false;
    seen.add(entry);
    return true;
  });

  return { companyName, newsTitles };
}

export function createTechMoatTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "tech_moat",
    description:
      "技术壁垒证据链分析。\n" +
      "• action=analyze — 从新闻/研报/外部信息评估技术壁垒可信度\n" +
      "• action=compare — 对比多只股票的技术壁垒强弱",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["analyze", "compare"],
          description: "分析类型",
        },
        symbol: {
          type: "string",
          description: "股票代码，如 688008",
        },
        company_name: {
          type: "string",
          description: "公司名称，可选",
        },
        news_titles: {
          type: "array",
          items: { type: "string" },
          description: "可选，外部提供的新闻/研报文本列表；不提供时自动抓取个股详情新闻",
        },
        candidates: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string" },
              name: { type: "string" },
              news_titles: { type: "array", items: { type: "string" } },
            },
            required: ["symbol"],
          },
          description: "compare 模式下的候选列表",
        },
        market: {
          type: "string",
          description: "自动抓取详情时使用的市场，默认 cn_stock",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = String(args.action ?? "analyze");
      if (action === "compare") {
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/tech_moat_engine.py",
          {
            action,
            candidates: Array.isArray(args.candidates) ? args.candidates : [],
          },
          config,
        );
        if (!pyResult.ok) return { error: pyResult.error ?? "技术壁垒对比失败" };
        return pyResult.data;
      }

      const symbol = String(args.symbol ?? "").trim();
      if (!symbol) return { error: "symbol is required" };

      let companyName = String(args.company_name ?? "").trim();
      let newsTitles = Array.isArray(args.news_titles)
        ? args.news_titles.map((item) => String(item ?? "").trim()).filter(Boolean)
        : [];

      if (newsTitles.length === 0) {
        const detailResult = await runPythonScript<Record<string, unknown>>(
          "scripts/stock_detail.py",
          {
            action: "detail",
            symbol,
            market: args.market ?? "cn_stock",
            kline_days: 120,
            kline_interval: "1d",
          },
          config,
        );
        if (!detailResult.ok || !detailResult.data || typeof detailResult.data !== "object") {
          return { error: detailResult.error ?? "技术壁垒数据准备失败" };
        }
        const inputs = collectTechMoatInputs(detailResult.data as Record<string, unknown>);
        companyName = companyName || inputs.companyName;
        newsTitles = inputs.newsTitles;
      }

      if (newsTitles.length === 0) {
        return { error: "未收集到足够的新闻/研报证据" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/tech_moat_engine.py",
        {
          action: "analyze",
          symbol,
          company_name: companyName,
          news_titles: newsTitles,
        },
        config,
      );
      if (!pyResult.ok) return { error: pyResult.error ?? "技术壁垒分析失败" };
      return pyResult.data;
    },
  };
}
