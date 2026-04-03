import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REPORT_TEMPLATES, findReportTemplate } from "./report-templates.js";

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-report-template-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("report-templates", () => {
  it("has expected templates", () => {
    const ids = REPORT_TEMPLATES.map((t) => t.id);
    expect(ids).toContain("morning_brief");
    expect(ids).toContain("market_close");
    expect(ids).toContain("weekly_review");
    expect(ids).toContain("watchlist_update");
    expect(ids).toContain("daily_scan");
    expect(ids).toContain("institutional_top3");
    expect(ids).toContain("daily_journal");
  });

  it("findReportTemplate returns correct template", () => {
    expect(findReportTemplate("morning_brief")?.name).toBe("早盘简报");
    expect(findReportTemplate("market_close")?.name).toBe("收盘总结");
    expect(findReportTemplate("daily_scan")?.name).toBe("每日选股");
    expect(findReportTemplate("institutional_top3")?.name).toBe("机构行为Top3");
    expect(findReportTemplate("nonexistent")).toBeUndefined();
  });

  it("each template has required fields", () => {
    for (const t of REPORT_TEMPLATES) {
      expect(t.id).toBeTruthy();
      expect(t.name).toBeTruthy();
      expect(t.description).toBeTruthy();
      expect(typeof t.generate).toBe("function");
    }
  });

  it("morning_brief generates report with tool executor", async () => {
    const template = findReportTemplate("morning_brief")!;
    const executeTool = vi.fn().mockResolvedValue(JSON.stringify({ sectors: [], stocks: [] }));

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("早盘简报");
    expect(executeTool).toHaveBeenCalled();
  });

  it("market_close generates report", async () => {
    const template = findReportTemplate("market_close")!;
    const executeTool = vi
      .fn()
      .mockResolvedValue(
        JSON.stringify({ price: "3200.00", changePct: "+0.50%", sectors: [], stocks: [] }),
      );

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("收盘总结");
  });

  it("market_close includes portfolio P&L when available", async () => {
    const template = findReportTemplate("market_close")!;
    const executeTool = vi.fn().mockImplementation((name: string) => {
      if (name === "portfolio") {
        return Promise.resolve(
          JSON.stringify({
            totalValue: 500000,
            cash: 100000,
            positions: [
              { name: "贵州茅台", unrealizedPnl: 2000, unrealizedPnlPct: 1.5 },
              { name: "宁德时代", unrealizedPnl: -500, unrealizedPnlPct: -0.8 },
            ],
          }),
        );
      }
      return Promise.resolve(JSON.stringify({ price: "3200", sectors: [], stocks: [] }));
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("今日持仓盈亏");
    expect(result).toContain("贵州茅台");
    expect(result).toContain("500000");
  });

  it("market_close includes today's trade signals", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const template = findReportTemplate("market_close")!;
    const executeTool = vi.fn().mockImplementation((name: string) => {
      if (name === "trading_signal") {
        return Promise.resolve(
          JSON.stringify({
            signals: [
              {
                symbol: "600519",
                direction: "buy",
                price: 1800,
                timestamp: `${today}T10:00:00Z`,
                strategyId: "fscore",
              },
            ],
          }),
        );
      }
      return Promise.resolve(
        JSON.stringify({ price: "3200", sectors: [], stocks: [], positions: [] }),
      );
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("今日交易信号");
    expect(result).toContain("600519");
  });

  it("weekly_review generates report", async () => {
    const template = findReportTemplate("weekly_review")!;
    const executeTool = vi.fn().mockResolvedValue(JSON.stringify({ sectors: [] }));

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("周度回顾");
  });

  it("weekly_review includes trade stats and scan frequency", async () => {
    const template = findReportTemplate("weekly_review")!;
    const now = new Date().toISOString();
    const executeTool = vi
      .fn()
      .mockImplementation((name: string, args: Record<string, unknown>) => {
        if (name === "trading_signal") {
          return Promise.resolve(
            JSON.stringify({
              signals: [
                { symbol: "600519", direction: "buy", timestamp: now },
                { symbol: "000858", direction: "sell", timestamp: now },
                { symbol: "600519", direction: "sell", timestamp: now },
              ],
            }),
          );
        }
        if (name === "stock_screen" && args.action === "stock_frequency") {
          return Promise.resolve(
            JSON.stringify({
              stocks: [{ code: "600519", name: "贵州茅台", appearances: 5, avgScore: 7.5 }],
            }),
          );
        }
        return Promise.resolve(JSON.stringify({ sectors: [] }));
      });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("周度回顾");
    expect(result).toContain("本周交易统计");
    expect(result).toContain("本周高频推荐股票");
    expect(result).toContain("贵州茅台");
  });

  it("daily_scan generates screening report", async () => {
    const template = findReportTemplate("daily_scan")!;
    const executeTool = vi
      .fn()
      .mockImplementation((_name: string, args: Record<string, unknown>) => {
        if (args.action === "run_screener") {
          return Promise.resolve(
            JSON.stringify({
              results: [{ code: "600519", name: "贵州茅台", score: 8 }],
              count: 1,
            }),
          );
        }
        if (args.action === "scan_compare") {
          return Promise.resolve(
            JSON.stringify({
              summary: { overlapCount: 0, addedCount: 1, removedCount: 0 },
              added: [{ code: "600519", name: "贵州茅台", score: 8 }],
              removed: [],
            }),
          );
        }
        if (args.action === "stock_frequency") {
          return Promise.resolve(JSON.stringify({ stocks: [] }));
        }
        return Promise.resolve(JSON.stringify({}));
      });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("每日策略选股");
    expect(result).toContain("600519");
    expect(result).toContain("贵州茅台");
    expect(result).toContain("与上次对比");
  });

  it("institutional_top3 generates top-3 report with bullish corpus", async () => {
    const template = findReportTemplate("institutional_top3")!;
    await fs.writeFile(
      path.join(tmpDir, "institution-activity-history.json"),
      JSON.stringify([
        {
          id: "2026-03-26-scan",
          version: "2026-03-26-v1",
          timestamp: "2026-03-26T15:00:00.000Z",
          params: {},
          summary: {
            asOfDate: "2026-03-26",
            candidateCount: 3,
            leaderCount: 3,
            baitCount: 0,
            marketTone: "中性",
            northboundNetIn: 0,
            generatedAt: "2026-03-26T15:00:00.000Z",
          },
          candidateSymbols: ["600519", "000858", "300750"],
          leaderSymbols: ["600519", "000858", "300750"],
          baitSymbols: [],
          topLeaders: [
            {
              symbol: "600519",
              name: "贵州茅台",
              actionTag: "可观察",
              confidence: 80,
              netConviction: 12,
              buildScore: 9,
              techMoatScore: 8.6,
              techMoatGrade: "极强（竞对验证）",
              manualConfirmation: {
                moatConfirmed: true,
                restructureConfirmed: false,
                restructureLevel: "none",
                supplyChainVerified: true,
                orderVerified: true,
                customerVerified: true,
                patentVerified: false,
                notes: "已复核订单与客户稳定性",
                score: 8,
                updatedAt: "2026-03-26T15:10:00.000Z",
              },
              manualScore: 8,
              decisionScore: 39.4,
            },
          ],
          fullScan: null,
        },
      ]),
      "utf-8",
    );
    const executeTool = vi
      .fn()
      .mockImplementation((name: string, args: Record<string, unknown>) => {
        if (name === "fund_flow" && args.action === "rank") {
          return Promise.resolve(
            JSON.stringify({
              stocks: [
                { code: "600519", name: "贵州茅台", mainNetIn: 12e8 },
                { code: "000858", name: "五粮液", mainNetIn: 8e8 },
                { code: "300750", name: "宁德时代", mainNetIn: 6e8 },
              ],
            }),
          );
        }
        if (name === "chip_accumulation" && args.action === "detect") {
          const symbol = String(args.symbol ?? "");
          const map: Record<string, number> = {
            "600519": 78,
            "000858": 71,
            "300750": 66,
          };
          return Promise.resolve(JSON.stringify({ total_score: map[symbol] ?? 60 }));
        }
        if (name === "institutional_flow" && args.action === "smart_money_signal") {
          const symbol = String(args.symbol ?? "");
          const map: Record<string, number> = {
            "600519": 82,
            "000858": 74,
            "300750": 69,
          };
          return Promise.resolve(JSON.stringify({ score: map[symbol] ?? 60 }));
        }
        if (name === "tech_moat" && args.action === "analyze") {
          const symbol = String(args.symbol ?? "");
          const map: Record<string, { moatScore: number; moatGrade: string }> = {
            "600519": { moatScore: 8.6, moatGrade: "极强（竞对验证）" },
            "000858": { moatScore: 6.8, moatGrade: "较强（供应链验证）" },
            "300750": { moatScore: 7.9, moatGrade: "较强（供应链验证）" },
          };
          return Promise.resolve(
            JSON.stringify(map[symbol] ?? { moatScore: 4.2, moatGrade: "中等（多源验证）" }),
          );
        }
        if (name === "news_summary" && args.action === "sentiment") {
          return Promise.resolve(
            JSON.stringify({
              score: 0.4,
              topNews: ["公司获大额订单 利好业绩增长", "机构上调评级并维持买入"],
            }),
          );
        }
        if (name === "news_summary" && args.action === "stock_news") {
          return Promise.resolve(
            JSON.stringify({
              news: [{ title: "签约新项目并获得中标" }, { title: "回购计划发布" }],
            }),
          );
        }
        return Promise.resolve(JSON.stringify({}));
      });

    const result = await template.generate(executeTool, { dataDir: tmpDir } as never);
    expect(result).toContain("每日机构行为 Top3");
    expect(result).toContain("今日前三候选");
    expect(result).toContain("600519");
    expect(result).toContain("利好语料");
    expect(result).toContain("壁垒分");
    expect(result).toContain("人工确认分");
    expect(result).toContain("订单验证");
    expect(result).toContain("客户验证");
    expect(result).toContain("结构化历史复盘");
    expect(result).toContain("历史订单验证榜");
  });

  it("watchlist_update handles missing watchlist gracefully", async () => {
    const template = findReportTemplate("watchlist_update")!;
    const executeTool = vi.fn();

    const result = await template.generate(executeTool, {
      dataDir: "/tmp/nonexistent-dir-xyz",
    } as never);
    expect(result).toContain("自选股");
  });

  it("morning_brief includes northbound fund flow", async () => {
    const template = findReportTemplate("morning_brief")!;
    const executeTool = vi
      .fn()
      .mockImplementation((name: string, args: Record<string, unknown>) => {
        if (name === "fund_flow" && args.action === "northbound") {
          return Promise.resolve(
            JSON.stringify({
              realtime: { northTotal: 5e9, hk2sh: 3e9, hk2sz: 2e9 },
            }),
          );
        }
        return Promise.resolve(JSON.stringify({ sectors: [], stocks: [], positions: [] }));
      });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("北向资金");
    expect(result).toContain("沪股通");
    expect(result).toContain("深股通");
  });

  it("morning_brief includes portfolio recap", async () => {
    const template = findReportTemplate("morning_brief")!;
    const executeTool = vi.fn().mockImplementation((name: string) => {
      if (name === "portfolio") {
        return Promise.resolve(
          JSON.stringify({
            totalValue: 800000,
            cash: 200000,
            positions: [
              { name: "贵州茅台", unrealizedPnl: 5000 },
              { name: "宁德时代", unrealizedPnl: -1000 },
            ],
          }),
        );
      }
      return Promise.resolve(JSON.stringify({ sectors: [], stocks: [] }));
    });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("持仓概览");
    expect(result).toContain("800000");
    expect(result).toContain("贵州茅台");
  });

  it("market_close includes northbound fund flow", async () => {
    const template = findReportTemplate("market_close")!;
    const executeTool = vi
      .fn()
      .mockImplementation((name: string, args: Record<string, unknown>) => {
        if (name === "fund_flow" && args.action === "northbound") {
          return Promise.resolve(
            JSON.stringify({
              realtime: { northTotal: -3e9, hk2sh: -1e9, hk2sz: -2e9 },
              history: [{ date: "2024-03-05", northTotal: 2e9 }],
            }),
          );
        }
        return Promise.resolve(
          JSON.stringify({ price: "3200", sectors: [], stocks: [], positions: [] }),
        );
      });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("北向资金");
    expect(result).toContain("今日净流入");
  });

  it("daily_journal generates comprehensive journal", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const template = findReportTemplate("daily_journal")!;
    const executeTool = vi
      .fn()
      .mockImplementation((name: string, args: Record<string, unknown>) => {
        if (name === "market_data") {
          return Promise.resolve(JSON.stringify({ price: "3200.00", changePct: "+0.50%" }));
        }
        if (name === "portfolio" && args.action === "daily_pnl") {
          return Promise.resolve(JSON.stringify({ date: today, netPnl: 1500 }));
        }
        if (name === "portfolio" && args.action === "live_orders") {
          return Promise.resolve(
            JSON.stringify({
              orders: [
                {
                  symbol: "600519",
                  side: "buy",
                  qty: 100,
                  price: 1800,
                  status: "filled",
                  createdAt: `${today}T10:00:00Z`,
                },
              ],
            }),
          );
        }
        if (name === "portfolio" && args.action === "live_trades") {
          return Promise.resolve(
            JSON.stringify({
              trades: [
                {
                  symbol: "000858",
                  entryPrice: 150,
                  exitPrice: 155,
                  quantity: 200,
                  netPnl: 1000,
                  exitTime: `${today}T14:30:00Z`,
                },
              ],
            }),
          );
        }
        if (name === "trading_signal") {
          return Promise.resolve(
            JSON.stringify({
              signals: [
                {
                  symbol: "600519",
                  direction: "buy",
                  strategy: "dual-ma",
                  timestamp: `${today}T09:30:00Z`,
                },
              ],
            }),
          );
        }
        return Promise.resolve(JSON.stringify({ sectors: [], stocks: [] }));
      });

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("交易日志");
    expect(result).toContain("大盘表现");
    expect(result).toContain("今日盈亏");
    expect(result).toContain("1500");
    expect(result).toContain("今日委托单");
    expect(result).toContain("今日成交/了结");
    expect(result).toContain("000858");
    expect(result).toContain("今日信号统计");
  });

  it("daily_journal handles no data gracefully", async () => {
    const template = findReportTemplate("daily_journal")!;
    const executeTool = vi.fn().mockRejectedValue(new Error("not available"));

    const result = await template.generate(executeTool, {} as never);
    expect(result).toContain("交易日志");
    expect(result).toContain("暂无交易数据");
  });
});
