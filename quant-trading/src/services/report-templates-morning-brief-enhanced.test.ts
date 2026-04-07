/**
 * Tests for enhanced morning brief in report-templates.ts
 *
 * Covers the new sections added to generateMorningBrief:
 *   1. Overnight news summary (news_summary → market_news)
 *   2. Macro economic calendar (macro_data → calendar)
 *   3. Macro economic regime (macro_data → regime)
 *   4. Northbound fund flow trend (consecutive direction, history details)
 *   5. AI stock recommendations — classic (stock_recommend → classic)
 *   6. AI stock recommendations — predictive (stock_recommend → predictive)
 */
import { describe, expect, it, vi } from "vitest";
import { findReportTemplate } from "./report-templates.js";

/** Helper: get morning_brief template */
function getMorningBrief() {
  const t = findReportTemplate("morning_brief");
  if (!t) throw new Error("morning_brief template not found");
  return t;
}

/** Helper: default mock that returns empty/minimal data for all tools */
function createDefaultMock(overrides?: Record<string, unknown>) {
  return vi.fn().mockImplementation((name: string, args: Record<string, unknown>) => {
    const key = `${name}:${String(args.action ?? args.mode ?? "")}`;
    if (overrides && key in overrides) {
      const val = overrides[key];
      if (val instanceof Error) return Promise.reject(val);
      return Promise.resolve(typeof val === "string" ? val : JSON.stringify(val));
    }
    return Promise.resolve(JSON.stringify({ sectors: [], stocks: [], positions: [] }));
  });
}

describe("morning_brief — overnight news summary", () => {
  it("renders news with articles array", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": {
        articles: [
          { title: "美联储维持利率不变", source: "Reuters" },
          { title: "A股三大指数收涨", source: "新华社" },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("隔夜重大新闻");
    expect(result).toContain("美联储维持利率不变");
    expect(result).toContain("(Reuters)");
    expect(result).toContain("A股三大指数收涨");
    expect(result).toContain("(新华社)");
  });

  it("renders news with fallback 'news' key", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": {
        news: [{ headline: "GDP数据超预期", source: "Bloomberg" }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("隔夜重大新闻");
    expect(result).toContain("GDP数据超预期");
  });

  it("limits news to 8 items", async () => {
    const template = getMorningBrief();
    const articles = Array.from({ length: 12 }, (_, i) => ({
      title: `新闻${i + 1}`,
      source: "src",
    }));
    const executeTool = createDefaultMock({
      "news_summary:market_news": { articles },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("新闻8");
    expect(result).not.toContain("新闻9");
  });

  it("skips news section when articles array is empty", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": { articles: [] },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("隔夜重大新闻");
  });

  it("shows error message when news_summary throws", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": new Error("network error"),
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("隔夜新闻: 数据获取失败");
  });

  it("handles article without source gracefully", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": {
        articles: [{ title: "无来源新闻" }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("无来源新闻");
    // Should NOT have empty parentheses
    expect(result).not.toContain("()");
  });

  it("calls news_summary with correct parameters", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "news_summary:market_news": { articles: [] },
    });

    await template.generate(executeTool, {} as never);
    expect(executeTool).toHaveBeenCalledWith("news_summary", {
      action: "market_news",
      limit: 8,
      days: 1,
    });
  });
});

describe("morning_brief — macro economic calendar", () => {
  it("renders economic calendar with events array", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:calendar": {
        events: [
          { name: "CPI同比", time: "09:30", previous: "2.1%", forecast: "2.3%" },
          { event: "PMI", time: "10:00", previous: "50.1" },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("今日经济日历");
    expect(result).toContain("09:30 CPI同比");
    expect(result).toContain("前值:2.1%");
    expect(result).toContain("预期:2.3%");
    expect(result).toContain("10:00 PMI");
  });

  it("renders calendar with fallback 'calendar' key", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:calendar": {
        calendar: [{ name: "非农就业", time: "20:30" }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("今日经济日历");
    expect(result).toContain("非农就业");
  });

  it("limits calendar events to 6", async () => {
    const template = getMorningBrief();
    const events = Array.from({ length: 10 }, (_, i) => ({ name: `事件${i + 1}` }));
    const executeTool = createDefaultMock({
      "macro_data:calendar": { events },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("事件6");
    expect(result).not.toContain("事件7");
  });

  it("skips calendar section when events array is empty", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:calendar": { events: [] },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("今日经济日历");
  });

  it("silently skips calendar on error (no error message)", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:calendar": new Error("service down"),
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("今日经济日历");
    expect(result).not.toContain("数据获取失败");
    // Report should still contain the header
    expect(result).toContain("早盘简报");
  });

  it("handles event without time, previous, or forecast", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:calendar": {
        events: [{ name: "仅名称事件" }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("仅名称事件");
    expect(result).not.toContain("前值:");
    expect(result).not.toContain("预期:");
  });
});

describe("morning_brief — macro economic regime", () => {
  it("renders regime data with regime key", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:regime": {
        regime: "扩张期",
        description: "PMI连续3月>50",
        suggestion: "偏向成长股配置",
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("宏观经济周期");
    expect(result).toContain("当前阶段: 扩张期");
    expect(result).toContain("判断依据: PMI连续3月>50");
    expect(result).toContain("配置建议: 偏向成长股配置");
  });

  it("renders regime data with fallback phase/summary/allocation keys", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:regime": {
        phase: "衰退期",
        summary: "经济增速放缓",
        allocation: "防御型配置",
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("当前阶段: 衰退期");
    expect(result).toContain("判断依据: 经济增速放缓");
    expect(result).toContain("配置建议: 防御型配置");
  });

  it("skips regime section when neither regime nor phase is present", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:regime": { other: "data" },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("宏观经济周期");
  });

  it("renders regime without description and suggestion", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:regime": { regime: "过热期" },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("宏观经济周期");
    expect(result).toContain("当前阶段: 过热期");
    expect(result).not.toContain("判断依据:");
    expect(result).not.toContain("配置建议:");
  });

  it("silently skips regime on error", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "macro_data:regime": new Error("timeout"),
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("宏观经济周期");
  });
});

describe("morning_brief — northbound fund flow trend", () => {
  it("renders trend with consecutive inflow days", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 5e9, hk2sh: 3e9, hk2sz: 2e9 },
        history: [
          { date: "2026-04-04", northTotal: 3e9 },
          { date: "2026-04-03", northTotal: 2e9 },
          { date: "2026-04-02", northTotal: 1e9 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("北向资金");
    expect(result).toContain("趋势: 连续3日净流入");
    expect(result).toContain("近3日累计");
    expect(result).toContain("近期明细:");
    expect(result).toContain("2026-04-04");
    expect(result).toContain("2026-04-03");
    expect(result).toContain("2026-04-02");
  });

  it("renders trend with consecutive outflow days", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: -2e9, hk2sh: -1e9, hk2sz: -1e9 },
        history: [
          { date: "2026-04-04", northTotal: -1e9 },
          { date: "2026-04-03", northTotal: -2e9 },
          { date: "2026-04-02", northTotal: 500000000 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("趋势: 连续2日净流出");
  });

  it("handles mixed history (breaks consecutive on direction change)", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
        history: [
          { date: "2026-04-04", northTotal: 2e9 },
          { date: "2026-04-03", northTotal: -1e9 },
          { date: "2026-04-02", northTotal: 3e9 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("趋势: 连续1日净流入");
  });

  it("treats zero northTotal as inflow direction", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 0, hk2sh: 0, hk2sz: 0 },
        history: [
          { date: "2026-04-04", northTotal: 0 },
          { date: "2026-04-03", northTotal: 1e9 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    // 0 is >= 0, so direction = "in"
    expect(result).toContain("连续2日净流入");
  });

  it("limits history to 5 entries", async () => {
    const template = getMorningBrief();
    const history = Array.from({ length: 8 }, (_, i) => ({
      date: `2026-04-0${8 - i}`,
      northTotal: 1e9,
    }));
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
        history,
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("近5日累计");
    // 6th entry date should not appear in details
    expect(result).toContain("2026-04-08");
    expect(result).toContain("2026-04-04");
    expect(result).not.toContain("2026-04-03");
  });

  it("skips trend section when history is empty", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
        history: [],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("北向资金");
    expect(result).not.toContain("趋势:");
    expect(result).not.toContain("近期明细:");
  });

  it("skips trend section when history is undefined", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("北向资金");
    expect(result).not.toContain("趋势:");
  });

  it("handles history entry with null northTotal", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
        history: [{ date: "2026-04-04", northTotal: null }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    // northTotal null → Number(0) → 0 → direction "in"
    expect(result).toContain("趋势:");
    expect(result).toContain("--");
  });

  it("calculates correct cumulative total for recent history", async () => {
    const template = getMorningBrief();
    // 3e9 + 2e9 = 5e9 → 50.00亿
    const executeTool = createDefaultMock({
      "fund_flow:northbound": {
        realtime: { northTotal: 1e9, hk2sh: 500000000, hk2sz: 500000000 },
        history: [
          { date: "2026-04-04", northTotal: 3e9 },
          { date: "2026-04-03", northTotal: 2e9 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("50.00亿");
  });
});

describe("morning_brief — AI stock recommendations (classic)", () => {
  it("renders classic recommendations with full data", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": {
        recommendations: [
          { code: "600519", name: "贵州茅台", score: 92, reason: "估值修复" },
          { code: "000858", name: "五粮液", score: 88, summary: "资金流入" },
          { code: "300750", name: "宁德时代", score: 85 },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("AI推荐 — 经典选股 (Top 3)");
    expect(result).toContain("⭐ 600519 贵州茅台 评分:92 — 估值修复");
    expect(result).toContain("⭐ 000858 五粮液 评分:88 — 资金流入");
    expect(result).toContain("⭐ 300750 宁德时代 评分:85");
  });

  it("renders classic recommendations with fallback 'stocks' key", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": {
        stocks: [{ symbol: "SH600519", name: "贵州茅台", score: 90 }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("AI推荐 — 经典选股");
    expect(result).toContain("SH600519");
  });

  it("limits classic recommendations to 3", async () => {
    const template = getMorningBrief();
    const recs = Array.from({ length: 6 }, (_, i) => ({
      code: `00000${i}`,
      name: `股票${i}`,
      score: 90 - i,
    }));
    const executeTool = createDefaultMock({
      "stock_recommend:classic": { recommendations: recs },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("股票0");
    expect(result).toContain("股票2");
    expect(result).not.toContain("股票3");
  });

  it("skips classic section when recommendations array is empty", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": { recommendations: [] },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("AI推荐 — 经典选股");
  });

  it("shows error message when classic stock_recommend throws", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": new Error("service unavailable"),
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("经典选股推荐: 数据获取失败");
  });

  it("calls stock_recommend with correct parameters for classic mode", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": { recommendations: [] },
    });

    await template.generate(executeTool, {} as never);
    expect(executeTool).toHaveBeenCalledWith("stock_recommend", {
      mode: "classic",
      count: 3,
      universe: "all",
    });
  });

  it("handles recommendation without score and reason", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:classic": {
        recommendations: [{ code: "600519", name: "贵州茅台" }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("⭐ 600519 贵州茅台");
    // No score or reason suffix
    expect(result).not.toContain("评分:");
    expect(result).not.toContain("—");
  });
});

describe("morning_brief — AI stock recommendations (predictive)", () => {
  it("renders predictive recommendations with full data", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": {
        recommendations: [
          { code: "002475", name: "立讯精密", score: 87, reason: "AI需求爆发" },
          { code: "300059", name: "东方财富", score: 83, summary: "业绩超预期" },
        ],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("AI推荐 — 前瞻选股 (Top 3)");
    expect(result).toContain("🔮 002475 立讯精密 评分:87 — AI需求爆发");
    expect(result).toContain("🔮 300059 东方财富 评分:83 — 业绩超预期");
  });

  it("renders predictive with fallback 'stocks' key", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": {
        stocks: [{ symbol: "300059", name: "东方财富", score: 80 }],
      },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("AI推荐 — 前瞻选股");
    expect(result).toContain("300059");
  });

  it("limits predictive recommendations to 3", async () => {
    const template = getMorningBrief();
    const recs = Array.from({ length: 5 }, (_, i) => ({
      code: `10000${i}`,
      name: `前瞻${i}`,
      score: 95 - i,
    }));
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": { recommendations: recs },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("前瞻2");
    expect(result).not.toContain("前瞻3");
  });

  it("skips predictive section when recommendations are empty", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": { recommendations: [] },
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).not.toContain("AI推荐 — 前瞻选股");
  });

  it("shows error message when predictive stock_recommend throws", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": new Error("timeout"),
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("前瞻选股推荐: 数据获取失败");
  });

  it("calls stock_recommend with correct parameters for predictive mode", async () => {
    const template = getMorningBrief();
    const executeTool = createDefaultMock({
      "stock_recommend:predictive": { recommendations: [] },
    });

    await template.generate(executeTool, {} as never);
    expect(executeTool).toHaveBeenCalledWith("stock_recommend", {
      mode: "predictive",
      count: 3,
      universe: "all",
    });
  });
});

describe("morning_brief — full integration with all new sections", () => {
  it("generates complete brief with all new sections populated", async () => {
    const template = getMorningBrief();
    const executeTool = vi.fn().mockImplementation((name: string, args: Record<string, unknown>) => {
      if (name === "news_summary" && args.action === "market_news") {
        return Promise.resolve(
          JSON.stringify({
            articles: [
              { title: "美联储按兵不动", source: "Reuters" },
              { title: "中国PMI回升至51.2", source: "国家统计局" },
            ],
          }),
        );
      }
      if (name === "macro_data" && args.action === "calendar") {
        return Promise.resolve(
          JSON.stringify({
            events: [
              { name: "CPI同比", time: "09:30", previous: "2.1%", forecast: "2.3%" },
            ],
          }),
        );
      }
      if (name === "macro_data" && args.action === "regime") {
        return Promise.resolve(
          JSON.stringify({
            regime: "复苏期",
            description: "经济数据边际改善",
            suggestion: "均衡配置",
          }),
        );
      }
      if (name === "sector_analysis") {
        return Promise.resolve(
          JSON.stringify({
            sectors: [{ name: "半导体", changePct: 3.5, leaderStock: "中芯国际" }],
          }),
        );
      }
      if (name === "fund_flow" && args.action === "rank") {
        return Promise.resolve(
          JSON.stringify({
            stocks: [{ name: "贵州茅台", code: "600519", mainNetIn: 5e8 }],
          }),
        );
      }
      if (name === "fund_flow" && args.action === "northbound") {
        return Promise.resolve(
          JSON.stringify({
            realtime: { northTotal: 3e9, hk2sh: 2e9, hk2sz: 1e9 },
            history: [
              { date: "2026-04-04", northTotal: 2e9 },
              { date: "2026-04-03", northTotal: 1e9 },
            ],
          }),
        );
      }
      if (name === "stock_recommend" && args.mode === "classic") {
        return Promise.resolve(
          JSON.stringify({
            recommendations: [
              { code: "600519", name: "贵州茅台", score: 92, reason: "价值修复" },
            ],
          }),
        );
      }
      if (name === "stock_recommend" && args.mode === "predictive") {
        return Promise.resolve(
          JSON.stringify({
            recommendations: [
              { code: "002475", name: "立讯精密", score: 88, reason: "AI需求" },
            ],
          }),
        );
      }
      if (name === "portfolio") {
        return Promise.resolve(
          JSON.stringify({
            totalValue: 1000000,
            cash: 300000,
            positions: [{ name: "贵州茅台", unrealizedPnl: 5000 }],
          }),
        );
      }
      return Promise.resolve(JSON.stringify({}));
    });

    const result = await template.generate(executeTool, {} as never);

    // Verify all new sections appear in order
    expect(result).toContain("早盘简报");
    expect(result).toContain("隔夜重大新闻");
    expect(result).toContain("美联储按兵不动");
    expect(result).toContain("今日经济日历");
    expect(result).toContain("CPI同比");
    expect(result).toContain("宏观经济周期");
    expect(result).toContain("复苏期");
    expect(result).toContain("板块排名");
    expect(result).toContain("主力资金净流入");
    expect(result).toContain("北向资金");
    expect(result).toContain("趋势: 连续2日净流入");
    expect(result).toContain("AI推荐 — 经典选股");
    expect(result).toContain("AI推荐 — 前瞻选股");
    expect(result).toContain("持仓概览");

    // Verify section order: news before calendar before regime
    const newsIdx = result.indexOf("隔夜重大新闻");
    const calendarIdx = result.indexOf("今日经济日历");
    const regimeIdx = result.indexOf("宏观经济周期");
    const sectorIdx = result.indexOf("板块排名");
    const classicIdx = result.indexOf("AI推荐 — 经典选股");
    const predictiveIdx = result.indexOf("AI推荐 — 前瞻选股");
    const portfolioIdx = result.indexOf("持仓概览");

    expect(newsIdx).toBeLessThan(calendarIdx);
    expect(calendarIdx).toBeLessThan(regimeIdx);
    expect(regimeIdx).toBeLessThan(sectorIdx);
    expect(classicIdx).toBeLessThan(predictiveIdx);
    expect(predictiveIdx).toBeLessThan(portfolioIdx);
  });

  it("gracefully handles all new tools failing", async () => {
    const template = getMorningBrief();
    const executeTool = vi.fn().mockRejectedValue(new Error("all tools down"));

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("早盘简报");
    expect(result).toContain("隔夜新闻: 数据获取失败");
    expect(result).toContain("经典选股推荐: 数据获取失败");
    expect(result).toContain("前瞻选股推荐: 数据获取失败");
    // Calendar and regime silently skip
    expect(result).not.toContain("今日经济日历");
    expect(result).not.toContain("宏观经济周期");
  });

  it("handles invalid JSON from tool responses", async () => {
    const template = getMorningBrief();
    const executeTool = vi.fn().mockResolvedValue("not valid json");

    const result = await template.generate(executeTool, {} as never);
    // All sections should gracefully fail via try/catch
    expect(result).toContain("早盘简报");
    expect(result).toContain("隔夜新闻: 数据获取失败");
    expect(result).toContain("板块排名: 数据获取失败");
  });

  it("verifies correct tool call count for all new sections", async () => {
    const template = getMorningBrief();
    const executeTool = vi.fn().mockResolvedValue(
      JSON.stringify({ sectors: [], stocks: [], positions: [], recommendations: [] }),
    );

    await template.generate(executeTool, {} as never);

    // Check new tool calls exist
    const calls = executeTool.mock.calls.map(
      (c: [string, Record<string, unknown>]) => `${c[0]}:${String(c[1].action ?? c[1].mode ?? "")}`,
    );
    expect(calls).toContain("news_summary:market_news");
    expect(calls).toContain("macro_data:calendar");
    expect(calls).toContain("macro_data:regime");
    expect(calls).toContain("stock_recommend:classic");
    expect(calls).toContain("stock_recommend:predictive");
    expect(calls).toContain("fund_flow:northbound");
  });
});
