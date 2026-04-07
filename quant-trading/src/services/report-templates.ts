import {
  getLatestInstitutionTopLeaders,
  handleGetInstitutionActivityHistoryApi,
} from "../http/institution-history.js";
/**
 * Report templates for scheduled periodic pushes (定期推送).
 *
 * Uses the existing TaskScheduler to execute and deliver reports.
 * Templates:
 *   morning_brief   — Pre-market brief (macro, sector ranking, watchlist)
 *   market_close     — Post-market summary (indices, sector leaders, fund flow, portfolio P&L)
 *   weekly_review    — Weekly portfolio performance review (trades, scan frequency)
 *   watchlist_update — Watchlist price update
 *   daily_scan       — Daily strategy screening with auto-compare vs previous run
 */
import type { QuantTradingConfig } from "../types.js";

export type ReportTemplateId =
  | "morning_brief"
  | "market_close"
  | "weekly_review"
  | "watchlist_update"
  | "daily_scan"
  | "institutional_top3"
  | "daily_journal"
  | "monthly_report"
  | "daily_settlement";

export interface ReportTemplate {
  id: ReportTemplateId;
  name: string;
  description: string;
  /** Default interval in minutes. */
  defaultIntervalMinutes: number;
  /** Generate report content using the given tool executor. */
  generate: (
    executeToolFn: (name: string, args: Record<string, unknown>) => Promise<string>,
    config: QuantTradingConfig,
  ) => Promise<string>;
}

/**
 * Build a morning brief report.
 */
async function generateMorningBrief(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📋 **早盘简报**", ""];

  // Sector ranking (top 10)
  try {
    const sectorRaw = await executeTool("sector_analysis", { action: "ranking", limit: 10 });
    const sectorData = JSON.parse(sectorRaw);
    if (sectorData.sectors && sectorData.sectors.length > 0) {
      sections.push("**板块排名 (前10)**");
      for (const s of sectorData.sectors.slice(0, 10)) {
        const change =
          s.changePct != null
            ? `${s.changePct > 0 ? "+" : ""}${Number(s.changePct).toFixed(2)}%`
            : "--";
        const leader = s.leaderStock ? ` 领涨: ${String(s.leaderStock)}` : "";
        sections.push(`  • ${String(s.name)} ${change}${leader}`);
      }
      sections.push("");
    }
  } catch {
    sections.push("板块排名: 数据获取失败\n");
  }

  // Fund flow ranking (top 5)
  try {
    const flowRaw = await executeTool("fund_flow", { action: "rank", limit: 5 });
    const flowData = JSON.parse(flowRaw);
    if (flowData.stocks && flowData.stocks.length > 0) {
      sections.push("**主力资金净流入 (前5)**");
      for (const s of flowData.stocks.slice(0, 5)) {
        const netIn = s.mainNetIn != null ? `${(Number(s.mainNetIn) / 1e8).toFixed(2)}亿` : "--";
        sections.push(`  • ${String(s.name)}(${String(s.code)}) 净流入: ${netIn}`);
      }
      sections.push("");
    }
  } catch {
    sections.push("资金流向: 数据获取失败\n");
  }

  // Northbound fund flow
  try {
    const northRaw = await executeTool("fund_flow", { action: "northbound" });
    const northData = JSON.parse(northRaw);
    if (northData.realtime) {
      const rt = northData.realtime;
      const total = rt.northTotal != null ? `${(Number(rt.northTotal) / 1e8).toFixed(2)}亿` : "--";
      const sh = rt.hk2sh != null ? `${(Number(rt.hk2sh) / 1e8).toFixed(2)}亿` : "--";
      const sz = rt.hk2sz != null ? `${(Number(rt.hk2sz) / 1e8).toFixed(2)}亿` : "--";
      sections.push("**北向资金**");
      sections.push(`  净流入: ${total} (沪股通 ${sh} / 深股通 ${sz})`);
      sections.push("");
    }
  } catch {
    // Northbound data not available — skip
  }

  // Yesterday's portfolio recap
  try {
    const portfolioRaw = await executeTool("portfolio", { action: "view" });
    const portfolio = JSON.parse(portfolioRaw);
    if (portfolio.positions && portfolio.positions.length > 0) {
      const totalValue = portfolio.totalValue ?? portfolio.total_value;
      const cash = portfolio.cash ?? portfolio.available_cash;
      sections.push("**持仓概览**");
      if (totalValue != null) {
        sections.push(
          `  总资产: ¥${Number(totalValue).toFixed(2)}  现金: ¥${Number(cash ?? 0).toFixed(2)}  持仓${String(portfolio.positions.length)}只`,
        );
      }
      for (const p of portfolio.positions.slice(0, 8)) {
        const pnl = p.unrealizedPnl ?? p.pnl ?? p.profit;
        const pnlStr = pnl != null ? `${pnl >= 0 ? "+" : ""}¥${Number(pnl).toFixed(2)}` : "";
        const arrow = (pnl ?? 0) >= 0 ? "📈" : "📉";
        sections.push(`  ${arrow} ${String(p.name ?? p.symbol ?? p.code)} ${pnlStr}`);
      }
      sections.push("");
    }
  } catch {
    // Portfolio data not available — skip
  }

  return sections.join("\n");
}

/**
 * Build a market close summary.
 */
async function generateMarketClose(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📊 **收盘总结**", ""];

  // Index quotes
  for (const idx of ["000001", "399001", "399006"]) {
    try {
      const raw = await executeTool("market_data", { action: "quote", symbol: idx });
      const data = JSON.parse(raw);
      if (data.price) {
        const name = idx === "000001" ? "上证指数" : idx === "399001" ? "深证成指" : "创业板指";
        const changePct = data.changePct ?? "--";
        sections.push(`  ${name}: ${String(data.price)} (${String(changePct)})`);
      }
    } catch {
      // Skip failed index
    }
  }
  sections.push("");

  // Top sector performance
  try {
    const sectorRaw = await executeTool("sector_analysis", { action: "ranking", limit: 5 });
    const sectorData = JSON.parse(sectorRaw);
    if (sectorData.sectors && sectorData.sectors.length > 0) {
      sections.push("**今日领涨板块**");
      for (const s of sectorData.sectors.slice(0, 5)) {
        const change =
          s.changePct != null
            ? `${s.changePct > 0 ? "+" : ""}${Number(s.changePct).toFixed(2)}%`
            : "--";
        sections.push(`  • ${String(s.name)} ${change}`);
      }
      sections.push("");
    }
  } catch {
    sections.push("板块数据: 获取失败\n");
  }

  // Dragon tiger list
  try {
    const lhbRaw = await executeTool("dragon_tiger", { action: "daily", limit: 5 });
    const lhbData = JSON.parse(lhbRaw);
    if (lhbData.stocks && lhbData.stocks.length > 0) {
      sections.push("**龙虎榜热门**");
      for (const s of lhbData.stocks.slice(0, 5)) {
        const net = s.netAmount != null ? `净买${(Number(s.netAmount) / 1e8).toFixed(2)}亿` : "";
        sections.push(
          `  • ${String(s.name)}(${String(s.code)}) ${String(s.changePct ?? "")}% ${net}`,
        );
      }
      sections.push("");
    }
  } catch {
    sections.push("龙虎榜: 获取失败\n");
  }

  // Northbound fund flow
  try {
    const northRaw = await executeTool("fund_flow", { action: "northbound" });
    const northData = JSON.parse(northRaw);
    if (northData.realtime) {
      const rt = northData.realtime;
      const total = rt.northTotal != null ? `${(Number(rt.northTotal) / 1e8).toFixed(2)}亿` : "--";
      const sh = rt.hk2sh != null ? `${(Number(rt.hk2sh) / 1e8).toFixed(2)}亿` : "--";
      const sz = rt.hk2sz != null ? `${(Number(rt.hk2sz) / 1e8).toFixed(2)}亿` : "--";
      sections.push("**北向资金**");
      sections.push(`  今日净流入: ${total} (沪股通 ${sh} / 深股通 ${sz})`);
      // Show recent history if available
      if (northData.history?.length > 0) {
        for (const h of northData.history.slice(0, 3)) {
          const dayTotal =
            h.northTotal != null ? `${(Number(h.northTotal) / 1e8).toFixed(2)}亿` : "--";
          sections.push(`  ${String(h.date ?? "")} ${dayTotal}`);
        }
      }
      sections.push("");
    }
  } catch {
    // Northbound data not available — skip
  }

  // Portfolio P&L summary
  try {
    const portfolioRaw = await executeTool("portfolio", { action: "view" });
    const portfolio = JSON.parse(portfolioRaw);
    if (portfolio.positions && portfolio.positions.length > 0) {
      sections.push("**今日持仓盈亏**");
      const totalValue = portfolio.totalValue ?? portfolio.total_value;
      const cash = portfolio.cash ?? portfolio.available_cash;
      if (totalValue != null) {
        sections.push(
          `  总资产: ¥${Number(totalValue).toFixed(2)}  现金: ¥${Number(cash ?? 0).toFixed(2)}`,
        );
      }
      for (const p of portfolio.positions.slice(0, 10)) {
        const pnl = p.unrealizedPnl ?? p.pnl ?? p.profit;
        const pnlStr = pnl != null ? `${pnl >= 0 ? "+" : ""}¥${Number(pnl).toFixed(2)}` : "--";
        const pnlPct = p.unrealizedPnlPct ?? p.pnlPct ?? p.profitPct;
        const pctStr =
          pnlPct != null ? `(${pnlPct >= 0 ? "+" : ""}${Number(pnlPct).toFixed(2)}%)` : "";
        const arrow = (pnl ?? 0) >= 0 ? "📈" : "📉";
        sections.push(`  ${arrow} ${String(p.name ?? p.symbol ?? p.code)} ${pnlStr} ${pctStr}`);
      }
      sections.push("");
    }
  } catch {
    // Portfolio data not available — skip
  }

  // Today's trade summary
  try {
    const signalRaw = await executeTool("trading_signal", { action: "recent", limit: 20 });
    const signalData = JSON.parse(signalRaw);
    const today = new Date().toISOString().slice(0, 10);
    const todaySignals = (signalData.signals ?? []).filter((s: Record<string, unknown>) =>
      String(s.timestamp ?? s.createdAt ?? "").startsWith(today),
    );
    if (todaySignals.length > 0) {
      sections.push("**今日交易信号**");
      for (const s of todaySignals.slice(0, 10)) {
        const dir = s.direction === "buy" ? "🟢买入" : "🔴卖出";
        sections.push(
          `  ${dir} ${String(s.symbol ?? "")} ${s.price ? `¥${String(s.price)}` : ""} (${String(s.strategyId ?? s.reason ?? "")})`,
        );
      }
      sections.push("");
    }
  } catch {
    // Signal data not available — skip
  }

  return sections.join("\n");
}

/**
 * Build a weekly review.
 */
async function generateWeeklyReview(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📅 **周度回顾**", ""];

  // Sector weekly performance
  try {
    const sectorRaw = await executeTool("sector_analysis", { action: "ranking", limit: 10 });
    const sectorData = JSON.parse(sectorRaw);
    if (sectorData.sectors?.length > 0) {
      sections.push("**本周板块表现**");
      for (const s of sectorData.sectors.slice(0, 10)) {
        const change =
          s.changePct != null
            ? `${s.changePct > 0 ? "+" : ""}${Number(s.changePct).toFixed(2)}%`
            : "--";
        sections.push(`  • ${String(s.name)} ${change}`);
      }
      sections.push("");
    }
  } catch {
    sections.push("板块数据: 获取失败\n");
  }

  // Fund flow weekly overview
  try {
    const flowRaw = await executeTool("fund_flow", { action: "sector", limit: 5 });
    const flowData = JSON.parse(flowRaw);
    if (flowData.sectors?.length > 0) {
      sections.push("**行业资金流向**");
      for (const s of flowData.sectors.slice(0, 5)) {
        const net = s.mainNetIn != null ? `${(Number(s.mainNetIn) / 1e8).toFixed(2)}亿` : "--";
        sections.push(`  • ${String(s.name)} 主力净流入: ${net}`);
      }
      sections.push("");
    }
  } catch {
    sections.push("资金流向: 获取失败\n");
  }

  // Weekly trade summary
  try {
    const signalRaw = await executeTool("trading_signal", { action: "recent", limit: 100 });
    const signalData = JSON.parse(signalRaw);
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const weekSignals = (signalData.signals ?? []).filter(
      (s: Record<string, unknown>) => String(s.timestamp ?? s.createdAt ?? "") >= weekAgo,
    );
    if (weekSignals.length > 0) {
      const buys = weekSignals.filter((s: Record<string, unknown>) => s.direction === "buy").length;
      const sells = weekSignals.filter(
        (s: Record<string, unknown>) => s.direction === "sell",
      ).length;
      sections.push("**本周交易统计**");
      sections.push(
        `  总信号: ${String(weekSignals.length)} (买入 ${String(buys)} / 卖出 ${String(sells)})`,
      );
      // Count unique symbols
      const symbols = new Set(
        weekSignals.map((s: Record<string, unknown>) => String(s.symbol ?? "")),
      );
      sections.push(`  涉及标的: ${String(symbols.size)} 只`);
      sections.push("");
    }
  } catch {
    // Signal data not available — skip
  }

  // Weekly scan frequency — most recommended stocks
  try {
    const freqRaw = await executeTool("stock_screen", {
      action: "stock_frequency",
      minAppearances: 2,
      limit: 10,
    });
    const freqData = JSON.parse(freqRaw);
    if (freqData.stocks?.length > 0) {
      sections.push("**本周高频推荐股票**");
      for (const s of freqData.stocks.slice(0, 10)) {
        const score = s.avgScore != null ? ` 均分${String(s.avgScore)}` : "";
        sections.push(
          `  ⭐ ${String(s.code)} ${s.name ? String(s.name) : ""} 出现${String(s.appearances)}次${score}`,
        );
      }
      sections.push("");
    }
  } catch {
    // Scan frequency not available — skip
  }

  return sections.join("\n");
}

/**
 * Build a watchlist price update.
 */
async function generateWatchlistUpdate(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📋 **自选股行情更新**", ""];

  // Read watchlist file
  try {
    const fs = await import("node:fs/promises");
    const os = await import("node:os");
    const path = await import("node:path");
    const dir = _config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    const watchlistPath = path.join(dir, "watchlist.json");
    const raw = await fs.readFile(watchlistPath, "utf-8");
    const watchlist = JSON.parse(raw) as string[];

    if (watchlist.length === 0) {
      return "📋 **自选股** — 空\n\n使用 /自选 add <代码> 添加自选股";
    }

    for (const code of watchlist.slice(0, 30)) {
      try {
        const raw = await executeTool("market_data", { action: "quote", symbol: code });
        const data = JSON.parse(raw);
        if (data.price) {
          const arrow =
            Number(data.changePct?.replace?.("%", "") ?? data.changePct ?? 0) >= 0 ? "📈" : "📉";
          sections.push(
            `  ${arrow} ${code} ¥${String(data.price)} ${String(data.changePct ?? "")}`,
          );
        } else {
          sections.push(`  • ${code} (数据获取失败)`);
        }
      } catch {
        sections.push(`  • ${code} (数据获取失败)`);
      }
    }
  } catch {
    return "📋 **自选股** — 暂无自选股或读取失败";
  }

  return sections.join("\n");
}

/**
 * Build a daily scan report — runs screener + auto-compares with last run.
 */
async function generateDailyScan(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["🔍 **每日策略选股**", ""];
  const screeners = ["fscore", "technical"];

  for (const screenerId of screeners) {
    const label = screenerId === "fscore" ? "F-Score 价值选股" : "技术面选股";
    sections.push(`**${label}**`);

    // Run screener
    try {
      const scanRaw = await executeTool("stock_screen", {
        action: "run_screener",
        screenerId,
        universe: "hs300",
        limit: 10,
      });
      const scanData = JSON.parse(scanRaw);
      const results = scanData.results ?? [];
      if (results.length > 0) {
        sections.push(`  筛选结果 (${String(results.length)} 只):`);
        for (const r of results.slice(0, 10)) {
          const score = r.score != null ? ` 评分${String(r.score)}` : "";
          sections.push(
            `  • ${String(r.code ?? r.stock_id ?? "")} ${String(r.name ?? "")}${score}`,
          );
        }
      } else {
        sections.push("  今日无符合条件的股票");
      }
      sections.push("");

      // Auto-compare with previous run
      try {
        const compareRaw = await executeTool("stock_screen", {
          action: "scan_compare",
          screenerId,
        });
        const cmpData = JSON.parse(compareRaw);
        if (cmpData.summary) {
          const { overlapCount, addedCount, removedCount } = cmpData.summary;
          sections.push(
            `  📊 与上次对比: 保持${String(overlapCount)}只 | 新增${String(addedCount)}只 | 消失${String(removedCount)}只`,
          );
          if (cmpData.added?.length > 0) {
            sections.push("  新增:");
            for (const s of cmpData.added.slice(0, 5)) {
              sections.push(
                `    🆕 ${String(s.code)} ${String(s.name ?? "")} ${s.score != null ? `评分${String(s.score)}` : ""}`,
              );
            }
          }
          if (cmpData.removed?.length > 0) {
            sections.push("  消失:");
            for (const s of cmpData.removed.slice(0, 5)) {
              sections.push(
                `    ❌ ${String(s.code)} ${String(s.name ?? "")} ${s.score != null ? `评分${String(s.score)}` : ""}`,
              );
            }
          }
          sections.push("");
        }
      } catch {
        // No previous scan to compare — skip
      }
    } catch {
      sections.push("  筛选引擎未就绪\n");
    }
  }

  // Stock frequency (stocks appearing in multiple recent scans)
  try {
    const freqRaw = await executeTool("stock_screen", {
      action: "stock_frequency",
      minAppearances: 3,
      limit: 10,
    });
    const freqData = JSON.parse(freqRaw);
    if (freqData.stocks?.length > 0) {
      sections.push("**持续推荐股票（近期多次入选）**");
      for (const s of freqData.stocks.slice(0, 10)) {
        const score = s.avgScore != null ? ` 均分${String(s.avgScore)}` : "";
        sections.push(
          `  ⭐ ${String(s.code)} ${s.name ? String(s.name) : ""} 连续${String(s.appearances)}次入选${score}`,
        );
      }
      sections.push("");
    }
  } catch {
    // Stock frequency not available — skip
  }

  return sections.join("\n");
}

/**
 * Build a daily institutional behavior report and pick top 3 candidates.
 *
 * Logic:
 * 1) Candidate pool from today's main fund net inflow ranking.
 * 2) "Pre-accumulation" signal from chip_accumulation scan level/score.
 * 3) "Institutional rush" proxy from smart_money_signal.
 * 4) Bullish corpus score from stock_news + sentiment headlines/keywords.
 */
async function generateInstitutionalTop3(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["🏦 **每日机构行为 Top3**", ""];

  const bullishKeywords = [
    "利好",
    "中标",
    "签约",
    "订单",
    "增长",
    "预增",
    "扭亏",
    "回购",
    "增持",
    "突破",
    "创新高",
    "上调",
  ];

  const candidates: Array<{
    code: string;
    name: string;
    netInflow: number;
    accumulationScore: number;
    smartMoneyScore: number;
    moatScore: number;
    moatGrade: string;
    manualScore: number;
    manualSummary: string;
    sentimentScore: number;
    bullishCorpusScore: number;
    bullishLines: string[];
    totalScore: number;
  }> = [];

  // 1) Pull today's net inflow ranking as base universe.
  let rankedStocks: Array<Record<string, unknown>> = [];
  let latestManualMap: Record<string, Record<string, unknown>> = {};
  let historyMetrics: Record<string, unknown> | null = null;

  try {
    const latestTopLeaders = await getLatestInstitutionTopLeaders(config);
    latestManualMap = Object.fromEntries(
      latestTopLeaders.map((leader) => [
        leader.symbol,
        leader as unknown as Record<string, unknown>,
      ]),
    );
  } catch {
    latestManualMap = {};
  }
  try {
    const historyData = await handleGetInstitutionActivityHistoryApi(config);
    historyMetrics =
      historyData.metrics && typeof historyData.metrics === "object"
        ? (historyData.metrics as Record<string, unknown>)
        : null;
  } catch {
    historyMetrics = null;
  }
  try {
    const rankRaw = await executeTool("fund_flow", { action: "rank", limit: 15 });
    const rankData = JSON.parse(rankRaw);
    rankedStocks = Array.isArray(rankData.stocks) ? rankData.stocks : [];
  } catch {
    return "🏦 **每日机构行为 Top3**\n\n资金流向数据暂不可用，请稍后重试。";
  }

  if (rankedStocks.length === 0) {
    return "🏦 **每日机构行为 Top3**\n\n今日暂无可用净流入数据。";
  }

  // 2) Analyze top-ranked candidates with accumulation + smart money + bullish corpus.
  for (const stock of rankedStocks.slice(0, 10)) {
    const code = String(stock.code ?? stock.symbol ?? "").trim();
    if (!code) continue;
    const name = String(stock.name ?? "").trim();
    const netInflow = Number(stock.mainNetIn ?? stock.mainNetInflow ?? stock.netIn ?? 0) || 0;

    let accumulationScore = 0;
    let smartMoneyScore = 50;
    let moatScore = 0;
    let moatGrade = "待验证";
    let manualScore = 0;
    let manualSummary = "";
    let sentimentScore = 0;
    let bullishCorpusScore = 0;
    const bullishLines: string[] = [];
    const latestManual = latestManualMap[code] ?? null;

    if (latestManual) {
      manualScore = Number(latestManual.manualScore ?? 0) || 0;
      const manualConfirmation =
        latestManual.manualConfirmation && typeof latestManual.manualConfirmation === "object"
          ? (latestManual.manualConfirmation as Record<string, unknown>)
          : null;
      if (manualConfirmation) {
        const manualFlags: string[] = [];
        if (manualConfirmation.moatConfirmed) manualFlags.push("壁垒成立");
        if (manualConfirmation.supplyChainVerified) manualFlags.push("供应链验证");
        if (manualConfirmation.orderVerified) manualFlags.push("订单验证");
        if (manualConfirmation.customerVerified) manualFlags.push("客户验证");
        if (manualConfirmation.patentVerified) manualFlags.push("专利验证");
        const restructureLevel = String(manualConfirmation.restructureLevel ?? "");
        if (restructureLevel === "rumor") manualFlags.push("重组筹划");
        if (restructureLevel === "announcement") manualFlags.push("重组公告");
        if (restructureLevel === "approved") manualFlags.push("重组落地");
        const notes = String(manualConfirmation.notes ?? "").trim();
        if (manualFlags.length > 0 || notes) {
          manualSummary = [manualFlags.join("/"), notes].filter(Boolean).join(" | ");
        }
      }
    }

    try {
      const chipRaw = await executeTool("chip_accumulation", {
        action: "detect",
        symbol: code,
        lookback: 30,
      });
      const chipData = JSON.parse(chipRaw);
      accumulationScore = Number(chipData.total_score ?? 0) || 0;
    } catch {
      // Keep default score.
    }

    try {
      const instRaw = await executeTool("institutional_flow", {
        action: "smart_money_signal",
        symbol: code,
      });
      const instData = JSON.parse(instRaw);
      smartMoneyScore = Number(instData.score ?? 50) || 50;
    } catch {
      // Keep neutral score.
    }

    try {
      const moatRaw = await executeTool("tech_moat", {
        action: "analyze",
        symbol: code,
        company_name: name,
      });
      const moatData = JSON.parse(moatRaw);
      moatScore = Number(moatData.moatScore ?? 0) || 0;
      moatGrade = String(moatData.moatGrade ?? "待验证");
    } catch {
      // Keep neutral moat score.
    }

    try {
      const sentimentRaw = await executeTool("news_summary", {
        action: "sentiment",
        symbol: code,
        market: "cn",
        limit: 10,
        days: 3,
      });
      const sentimentData = JSON.parse(sentimentRaw);
      sentimentScore = Number(sentimentData.score ?? 0) || 0;

      const titles = Array.isArray(sentimentData.topNews)
        ? sentimentData.topNews.map((t: unknown) => String(t))
        : [];
      for (const t of titles.slice(0, 5)) {
        const hits = bullishKeywords.filter((kw) => t.includes(kw));
        if (hits.length > 0) {
          bullishCorpusScore += hits.length * 2;
          bullishLines.push(`新闻: ${t}`);
        }
      }
    } catch {
      // Keep neutral sentiment/corpus score.
    }

    try {
      const stockNewsRaw = await executeTool("news_summary", {
        action: "stock_news",
        symbol: code,
        market: "cn",
        limit: 6,
        days: 3,
      });
      const stockNewsData = JSON.parse(stockNewsRaw);
      const news = Array.isArray(stockNewsData.news) ? stockNewsData.news : [];
      for (const item of news.slice(0, 6)) {
        const title = String(item.title ?? "");
        const hits = bullishKeywords.filter((kw) => title.includes(kw));
        if (hits.length > 0) {
          bullishCorpusScore += hits.length;
          if (bullishLines.length < 3) bullishLines.push(`题材: ${title}`);
        }
      }
    } catch {
      // Optional news source.
    }

    const inflowScore = Math.max(0, Math.min(100, netInflow / 1e8));
    const sentimentNorm = Math.max(0, Math.min(100, (sentimentScore + 1) * 50));
    const corpusNorm = Math.max(0, Math.min(100, bullishCorpusScore * 8));
    const moatNorm = Math.max(0, Math.min(100, moatScore * 10));
    const manualNorm = Math.max(0, Math.min(100, manualScore * 10));

    const totalScore =
      inflowScore * 0.28 +
      accumulationScore * 0.2 +
      smartMoneyScore * 0.17 +
      moatNorm * 0.15 +
      manualNorm * 0.1 +
      sentimentNorm * 0.05 +
      corpusNorm * 0.05;

    candidates.push({
      code,
      name,
      netInflow,
      accumulationScore,
      smartMoneyScore,
      moatScore,
      moatGrade,
      manualScore,
      manualSummary,
      sentimentScore,
      bullishCorpusScore,
      bullishLines,
      totalScore,
    });
  }

  if (candidates.length === 0) {
    return "🏦 **每日机构行为 Top3**\n\n候选池为空，无法完成排序。";
  }

  candidates.sort((a, b) => b.totalScore - a.totalScore);
  const top3 = candidates.slice(0, 3);

  sections.push("**筛选逻辑**");
  sections.push("  1) 资金认可: 今日主力净流入排序");
  sections.push("  2) 前期吸筹: 主力吸筹引擎评分 (lookback=30)");
  sections.push("  3) 机构抢筹: smart_money_signal 综合机构行为评分");
  sections.push("  4) 技术壁垒: tech_moat 新闻/研报证据链评分");
  sections.push("  5) 人工确认: 最新历史快照中的订单/客户/专利/重组因子");
  sections.push("  6) 利好语料: 新闻标题关键词 + 情绪分");
  sections.push("");

  if (historyMetrics) {
    const latestOrder = Number(historyMetrics.latestOrderVerifiedCount ?? 0) || 0;
    const latestCustomer = Number(historyMetrics.latestCustomerVerifiedCount ?? 0) || 0;
    const latestPatent = Number(historyMetrics.latestPatentVerifiedCount ?? 0) || 0;
    const latestRestructure = Number(historyMetrics.latestRestructureCount ?? 0) || 0;
    const topOrder = Array.isArray(historyMetrics.topOrderLeaders)
      ? (historyMetrics.topOrderLeaders as Array<Record<string, unknown>>)
      : [];
    const topRestructure = Array.isArray(historyMetrics.topRestructureLeaders)
      ? (historyMetrics.topRestructureLeaders as Array<Record<string, unknown>>)
      : [];
    sections.push("**结构化历史复盘**");
    sections.push(
      `  最新结构化确认: 订单 ${latestOrder} | 客户 ${latestCustomer} | 专利 ${latestPatent} | 重组 ${latestRestructure}`,
    );
    if (topOrder.length > 0) {
      const item = topOrder[0]!;
      sections.push(
        `  历史订单验证榜: ${String(item.symbol ?? "--")} (${String(item.count ?? 0)}次, 分数 ${Number(item.score ?? 0).toFixed(1)})`,
      );
    }
    if (topRestructure.length > 0) {
      const item = topRestructure[0]!;
      sections.push(
        `  历史重组推进榜: ${String(item.symbol ?? "--")} (${String(item.label ?? "重组推进")}, ${String(item.count ?? 0)}次)`,
      );
    }
    sections.push("");
  }

  sections.push("**今日前三候选**");
  for (let i = 0; i < top3.length; i++) {
    const s = top3[i]!;
    const inflowYi = s.netInflow / 1e8;
    sections.push(
      `  ${String(i + 1)}. ${s.name || "--"}(${s.code})  综合分: ${s.totalScore.toFixed(1)}`,
    );
    sections.push(
      `     主力净流入: ${inflowYi >= 0 ? "+" : ""}${inflowYi.toFixed(2)}亿 | 吸筹分: ${s.accumulationScore.toFixed(1)} | 机构分: ${s.smartMoneyScore.toFixed(1)}`,
    );
    sections.push(
      `     壁垒分: ${s.moatScore.toFixed(1)} (${s.moatGrade}) | 人工确认分: ${s.manualScore.toFixed(1)} | 情绪分: ${s.sentimentScore.toFixed(3)} | 利好语料分: ${s.bullishCorpusScore.toFixed(1)}`,
    );
    if (s.manualSummary) {
      sections.push(`     人工确认: ${s.manualSummary}`);
    } else {
      sections.push(
        "     人工确认: 暂无结构化确认，建议在机构前三管理面板补充订单/客户/专利/重组级别。",
      );
    }
    if (s.bullishLines.length > 0) {
      sections.push(`     利好语料: ${s.bullishLines.slice(0, 2).join("；")}`);
    } else {
      sections.push("     利好语料: 暂无明显关键词触发，建议人工复核公告/研报。");
    }
  }

  return sections.join("\n");
}

/**
 * Build a daily trading journal — comprehensive review of today's operations.
 */
async function generateDailyJournal(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📝 **交易日志**", ""];
  const today = new Date().toISOString().slice(0, 10);

  // 1. Index performance
  const idxLines: string[] = [];
  for (const idx of ["000001", "399001", "399006"]) {
    try {
      const raw = await executeTool("market_data", { action: "quote", symbol: idx });
      const data = JSON.parse(raw);
      if (data.price) {
        const name = idx === "000001" ? "上证指数" : idx === "399001" ? "深证成指" : "创业板指";
        idxLines.push(`  ${name}: ${String(data.price)} (${String(data.changePct ?? "--")})`);
      }
    } catch {
      // skip
    }
  }
  if (idxLines.length > 0) {
    sections.push("**大盘表现**", ...idxLines, "");
  }

  // 2. Portfolio daily P&L
  try {
    const pnlRaw = await executeTool("portfolio", { action: "daily_pnl" });
    const pnlData = JSON.parse(pnlRaw);
    if (pnlData.netPnl != null) {
      const pnl = Number(pnlData.netPnl);
      const icon = pnl >= 0 ? "📈" : "📉";
      sections.push(`**今日盈亏** ${icon}`);
      sections.push(`  净盈亏: ${pnl >= 0 ? "+" : ""}¥${pnl.toFixed(2)}`);
      sections.push("");
    }
  } catch {
    // daily_pnl not available — try portfolio summary
    try {
      const sumRaw = await executeTool("portfolio", { action: "summary" });
      const sumData = JSON.parse(sumRaw);
      if (sumData.totalValue != null) {
        sections.push("**账户概要**");
        sections.push(
          `  总资产: ¥${Number(sumData.totalValue).toFixed(2)}  现金: ¥${Number(sumData.cash ?? 0).toFixed(2)}`,
        );
        sections.push("");
      }
    } catch {
      // skip
    }
  }

  // 3. Today's orders (live)
  try {
    const ordersRaw = await executeTool("portfolio", { action: "live_orders" });
    const ordersData = JSON.parse(ordersRaw);
    const orders = ordersData.orders ?? [];
    const todayOrders = orders.filter((o: Record<string, unknown>) =>
      String(o.createdAt ?? "").startsWith(today),
    );
    if (todayOrders.length > 0) {
      sections.push("**今日委托单**");
      let filled = 0;
      let cancelled = 0;
      for (const o of todayOrders) {
        const side = o.side === "buy" ? "🟢买" : "🔴卖";
        const status =
          o.status === "filled"
            ? "✅已成"
            : o.status === "cancelled"
              ? "❌撤单"
              : String(o.status ?? "");
        if (o.status === "filled") filled++;
        if (o.status === "cancelled") cancelled++;
        sections.push(
          `  ${side} ${String(o.symbol ?? "")} ${String(o.qty ?? "")}股 ¥${String(o.price ?? o.avgFillPrice ?? "--")} ${status}`,
        );
      }
      sections.push(
        `  合计: ${String(todayOrders.length)}单 (成交${String(filled)} 撤单${String(cancelled)})`,
      );
      sections.push("");
    }
  } catch {
    // Live orders not available — skip
  }

  // 4. Today's realized trades
  try {
    const tradesRaw = await executeTool("portfolio", { action: "live_trades" });
    const tradesData = JSON.parse(tradesRaw);
    const trades = tradesData.trades ?? [];
    const todayTrades = trades.filter((t: Record<string, unknown>) =>
      String(t.exitTime ?? "").startsWith(today),
    );
    if (todayTrades.length > 0) {
      sections.push("**今日成交/了结**");
      let totalPnl = 0;
      let win = 0;
      for (const t of todayTrades) {
        const pnl = Number(t.netPnl ?? 0);
        totalPnl += pnl;
        if (pnl > 0) win++;
        const icon = pnl >= 0 ? "✅" : "❌";
        sections.push(
          `  ${icon} ${String(t.symbol ?? "")} 买${String(t.entryPrice ?? "")}→卖${String(t.exitPrice ?? "")} ${pnl >= 0 ? "+" : ""}¥${pnl.toFixed(2)}`,
        );
      }
      const winRate = todayTrades.length > 0 ? ((win / todayTrades.length) * 100).toFixed(0) : "0";
      sections.push(
        `  小结: ${String(todayTrades.length)}笔 胜率${winRate}% 净盈亏${totalPnl >= 0 ? "+" : ""}¥${totalPnl.toFixed(2)}`,
      );
      sections.push("");
    }
  } catch {
    // Live trades not available — skip
  }

  // 5. Today's trading signals & hit rate
  try {
    const signalRaw = await executeTool("trading_signal", { action: "recent", limit: 50 });
    const signalData = JSON.parse(signalRaw);
    const todaySignals = (signalData.signals ?? []).filter((s: Record<string, unknown>) =>
      String(s.timestamp ?? s.date ?? s.createdAt ?? "").startsWith(today),
    );
    if (todaySignals.length > 0) {
      const buys = todaySignals.filter(
        (s: Record<string, unknown>) => s.direction === "buy",
      ).length;
      const sells = todaySignals.filter(
        (s: Record<string, unknown>) => s.direction === "sell",
      ).length;

      // Group by strategy
      const stratMap = new Map<string, number>();
      for (const s of todaySignals) {
        const id = String(s.strategy ?? s.strategyId ?? "unknown");
        stratMap.set(id, (stratMap.get(id) ?? 0) + 1);
      }

      sections.push("**今日信号统计**");
      sections.push(
        `  总信号: ${String(todaySignals.length)} (买入${String(buys)} 卖出${String(sells)})`,
      );
      for (const [strat, count] of stratMap) {
        sections.push(`  • ${strat}: ${String(count)}个`);
      }
      sections.push("");
    }
  } catch {
    // Signal data not available — skip
  }

  // 6. Northbound fund flow
  try {
    const northRaw = await executeTool("fund_flow", { action: "northbound" });
    const northData = JSON.parse(northRaw);
    if (northData.realtime) {
      const rt = northData.realtime;
      const total = rt.northTotal != null ? `${(Number(rt.northTotal) / 1e8).toFixed(2)}亿` : "--";
      sections.push("**北向资金**");
      sections.push(`  今日净流入: ${total}`);
      sections.push("");
    }
  } catch {
    // skip
  }

  // 7. Risk snapshot (if engine is on)
  try {
    const riskRaw = await executeTool("live_trading", { action: "get_risk_snapshot" });
    const riskData = JSON.parse(riskRaw);
    if (riskData.maxDrawdown != null || riskData.dailyLoss != null) {
      sections.push("**风控快照**");
      if (riskData.maxDrawdown != null) {
        sections.push(`  最大回撤: ${Number(riskData.maxDrawdown).toFixed(2)}%`);
      }
      if (riskData.dailyLoss != null) {
        sections.push(`  今日最大亏损: ¥${Number(riskData.dailyLoss).toFixed(2)}`);
      }
      sections.push("");
    }
  } catch {
    // Engine not running — skip
  }

  if (sections.length <= 2) {
    sections.push("暂无交易数据。开启实盘引擎或模拟盘后，日志将自动记录。");
  }

  return sections.join("\n");
}

/**
 * Build a monthly performance report — comprehensive review of the past month's trading.
 */
async function generateMonthlyReport(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["📊 **月度绩效报告**", ""];

  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth(); // current month (0-indexed)
  // Report covers previous month
  const reportMonth = month === 0 ? 12 : month;
  const reportYear = month === 0 ? year - 1 : year;
  const monthStr = `${String(reportYear)}-${String(reportMonth).padStart(2, "0")}`;
  sections.push(`**报告周期**: ${monthStr}`, "");

  // 1. Performance attribution (by strategy + by symbol + monthly P&L)
  try {
    const attrRaw = await executeTool("performance_attribution", {
      action: "full_report",
      from: `${monthStr}-01`,
      to: `${monthStr}-31`,
    });
    const attrData = JSON.parse(attrRaw);

    // Overall stats
    if (attrData.totalTrades != null) {
      sections.push("**整体表现**");
      const netPnl = Number(attrData.totalNetPnl ?? 0);
      const icon = netPnl >= 0 ? "📈" : "📉";
      sections.push(`  ${icon} 总交易: ${String(attrData.totalTrades)}笔`);
      sections.push(`  净盈亏: ${netPnl >= 0 ? "+" : ""}¥${netPnl.toFixed(2)}`);
      const winRate =
        attrData.overallWinRate != null
          ? `${(Number(attrData.overallWinRate) * 100).toFixed(1)}%`
          : "--";
      sections.push(`  胜率: ${winRate}`);
      if (attrData.profitFactor != null && attrData.profitFactor !== Infinity) {
        sections.push(`  盈亏比: ${Number(attrData.profitFactor).toFixed(2)}`);
      }
      if (attrData.avgWin != null) {
        sections.push(
          `  平均盈利: ¥${Number(attrData.avgWin).toFixed(2)}  平均亏损: ¥${Number(attrData.avgLoss ?? 0).toFixed(2)}`,
        );
      }
      sections.push("");
    }

    // Strategy breakdown
    if (attrData.byStrategy?.length > 0) {
      sections.push("**策略分析**");
      for (const s of attrData.byStrategy) {
        const sPnl = Number(s.netPnl ?? 0);
        const sIcon = sPnl >= 0 ? "✅" : "❌";
        const sWinRate = s.winRate != null ? `${(Number(s.winRate) * 100).toFixed(0)}%` : "--";
        sections.push(
          `  ${sIcon} ${String(s.strategyId)}: ${String(s.trades)}笔 ` +
            `净盈亏${sPnl >= 0 ? "+" : ""}¥${sPnl.toFixed(2)} ` +
            `胜率${sWinRate} 贡献${Number(s.contributionPct ?? 0).toFixed(1)}%`,
        );
      }
      sections.push("");
    }

    // Symbol breakdown (top 5 by absolute P&L)
    if (attrData.bySymbol?.length > 0) {
      const sorted = [...attrData.bySymbol].sort(
        (a: Record<string, unknown>, b: Record<string, unknown>) =>
          Math.abs(Number(b.netPnl ?? 0)) - Math.abs(Number(a.netPnl ?? 0)),
      );
      sections.push("**标的分析 (Top 5)**");
      for (const s of sorted.slice(0, 5)) {
        const sPnl = Number(s.netPnl ?? 0);
        const sIcon = sPnl >= 0 ? "📈" : "📉";
        sections.push(
          `  ${sIcon} ${String(s.symbol)}: ${String(s.trades)}笔 ` +
            `净盈亏${sPnl >= 0 ? "+" : ""}¥${sPnl.toFixed(2)}`,
        );
      }
      sections.push("");
    }
  } catch {
    sections.push("绩效归因数据: 获取失败\n");
  }

  // 2. Signal hit rate for the month
  try {
    const hitRaw = await executeTool("trading_signal", { action: "hit_rate" });
    const hitData = JSON.parse(hitRaw);
    if (hitData.stats) {
      sections.push("**信号命中率**");
      for (const [checkpoint, stat] of Object.entries(hitData.stats)) {
        const s = stat as Record<string, unknown>;
        if (s.total && Number(s.total) > 0) {
          sections.push(
            `  T+${checkpoint}: ${String(s.hits)}/${String(s.total)} ` +
              `(${(Number(s.hitRate ?? 0) * 100).toFixed(1)}%)`,
          );
        }
      }
      sections.push("");
    }
  } catch {
    // Signal hit rate not available — skip
  }

  // 3. Portfolio snapshot
  try {
    const portfolioRaw = await executeTool("portfolio", { action: "summary" });
    const pData = JSON.parse(portfolioRaw);
    if (pData.totalValue != null) {
      sections.push("**账户快照**");
      sections.push(`  总资产: ¥${Number(pData.totalValue).toFixed(2)}`);
      if (pData.cash != null) sections.push(`  现金: ¥${Number(pData.cash).toFixed(2)}`);
      if (pData.positionCount != null) sections.push(`  持仓: ${String(pData.positionCount)}只`);
      sections.push("");
    }
  } catch {
    // Portfolio not available — skip
  }

  // 4. Risk metrics
  try {
    const riskRaw = await executeTool("live_trading", { action: "get_risk_snapshot" });
    const riskData = JSON.parse(riskRaw);
    if (riskData.maxDrawdown != null) {
      sections.push("**风控指标**");
      sections.push(`  最大回撤: ${Number(riskData.maxDrawdown).toFixed(2)}%`);
      if (riskData.dailyLossMax != null) {
        sections.push(`  单日最大亏损: ¥${Number(riskData.dailyLossMax).toFixed(2)}`);
      }
      sections.push("");
    }
  } catch {
    // Risk snapshot not available — skip
  }

  if (sections.length <= 3) {
    sections.push("暂无交易数据。至少交易一个月后可生成月度报告。");
  }

  return sections.join("\n");
}

/**
 * Build a daily settlement summary — structured P&L breakdown at market close.
 *
 * Combines portfolio, live trades, and account data to produce:
 *   - Realized P&L (from today's closed trades)
 *   - Unrealized P&L change (from open positions)
 *   - Win rate & profit factor (today's closed trades)
 *   - Total fees (commission + tax)
 *   - Net P&L
 */
async function generateDailySettlement(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  _config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["💰 **每日结算汇总**", ""];
  const today = new Date().toISOString().slice(0, 10);

  // ── 1. Account snapshot ──
  let totalAssets = 0;
  let cash = 0;
  let todayProfit = 0;
  let hasAccount = false;

  try {
    const accountRaw = await executeTool("live_trading", { action: "status" });
    const accountData = JSON.parse(accountRaw);
    if (accountData.account) {
      totalAssets = Number(accountData.account.totalAssets ?? 0);
      cash = Number(accountData.account.cash ?? 0);
      todayProfit = Number(accountData.account.todayProfit ?? 0);
      hasAccount = true;
    }
  } catch {
    // Engine not running — try portfolio fallback
  }

  if (!hasAccount) {
    try {
      const sumRaw = await executeTool("portfolio", { action: "summary" });
      const sumData = JSON.parse(sumRaw);
      totalAssets = Number(sumData.totalValue ?? 0);
      cash = Number(sumData.cash ?? 0);
    } catch {
      // skip
    }
  }

  // ── 2. Today's closed trades — for realized PnL, win rate, profit factor, fees ──
  let closedTrades: Array<Record<string, unknown>> = [];
  try {
    const tradesRaw = await executeTool("portfolio", { action: "live_trades" });
    const tradesData = JSON.parse(tradesRaw);
    closedTrades = (tradesData.trades ?? []).filter((t: Record<string, unknown>) =>
      String(t.exitTime ?? "").startsWith(today),
    );
  } catch {
    // no trades data
  }

  let realizedPnl = 0;
  let grossWins = 0;
  let grossLosses = 0;
  let totalFees = 0;
  let wins = 0;
  let losses = 0;

  for (const t of closedTrades) {
    const netPnl = Number(t.netPnl ?? 0);
    const grossPnl = Number(t.grossPnl ?? netPnl);
    const commission = Number(t.commission ?? 0);
    realizedPnl += grossPnl;
    totalFees += commission;
    if (netPnl > 0) {
      grossWins += grossPnl;
      wins++;
    } else if (netPnl < 0) {
      grossLosses += Math.abs(grossPnl);
      losses++;
    }
  }

  const winRate = closedTrades.length > 0 ? wins / closedTrades.length : 0;
  const profitFactor =
    grossLosses > 0 ? grossWins / grossLosses : grossWins > 0 ? Infinity : 0;

  // ── 3. Unrealized PnL from current positions ──
  let unrealizedPnl = 0;
  let positionCount = 0;
  try {
    const portfolioRaw = await executeTool("portfolio", { action: "view" });
    const portfolio = JSON.parse(portfolioRaw);
    const positions = portfolio.positions ?? [];
    positionCount = positions.length;
    for (const p of positions) {
      unrealizedPnl += Number(p.unrealizedPnl ?? p.pnl ?? p.profit ?? 0);
    }
  } catch {
    // skip
  }

  // ── 4. Net P&L ──
  const netPnl = realizedPnl - totalFees + unrealizedPnl;
  const netPnlPct = totalAssets > 0 ? (netPnl / totalAssets) * 100 : 0;
  const icon = netPnl >= 0 ? "📈" : "📉";

  // ── Format output ──
  sections.push(`**日期**: ${today}`);
  sections.push("");

  sections.push(`**账户概况** ${icon}`);
  if (totalAssets > 0) {
    sections.push(`  总资产: ¥${totalAssets.toFixed(2)}`);
    sections.push(`  可用现金: ¥${cash.toFixed(2)}`);
    sections.push(`  持仓数量: ${String(positionCount)} 只`);
  }
  if (hasAccount && todayProfit !== 0) {
    sections.push(
      `  券商当日盈亏: ${todayProfit >= 0 ? "+" : ""}¥${todayProfit.toFixed(2)}`,
    );
  }
  sections.push("");

  sections.push("**已实现盈亏** (当日平仓)");
  sections.push(
    `  已实现盈亏: ${realizedPnl >= 0 ? "+" : ""}¥${realizedPnl.toFixed(2)}`,
  );
  sections.push(`  平仓笔数: ${String(closedTrades.length)}`);
  if (closedTrades.length > 0) {
    sections.push(`  盈利: ${String(wins)} 笔  |  亏损: ${String(losses)} 笔`);
    sections.push(`  胜率: ${(winRate * 100).toFixed(1)}%`);
    if (profitFactor === Infinity) {
      sections.push("  盈亏比: ∞ (无亏损交易)");
    } else if (profitFactor > 0) {
      sections.push(`  盈亏比: ${profitFactor.toFixed(2)}`);
    }
    // Per-trade details
    for (const t of closedTrades.slice(0, 10)) {
      const tNetPnl = Number(t.netPnl ?? 0);
      const tIcon = tNetPnl >= 0 ? "✅" : "❌";
      const sym = String(t.symbol ?? t.name ?? "");
      const entry = t.entryPrice != null ? `买¥${String(t.entryPrice)}` : "";
      const exit = t.exitPrice != null ? `→卖¥${String(t.exitPrice)}` : "";
      sections.push(
        `  ${tIcon} ${sym} ${entry}${exit} ${tNetPnl >= 0 ? "+" : ""}¥${tNetPnl.toFixed(2)}`,
      );
    }
    if (closedTrades.length > 10) {
      sections.push(`  ... 还有 ${String(closedTrades.length - 10)} 笔`);
    }
  } else {
    sections.push("  (今日无平仓交易)");
  }
  sections.push("");

  sections.push("**未实现盈亏变动**");
  sections.push(
    `  持仓浮盈/浮亏合计: ${unrealizedPnl >= 0 ? "+" : ""}¥${unrealizedPnl.toFixed(2)}`,
  );
  sections.push("");

  sections.push("**手续费合计**");
  sections.push(`  佣金+税费: ¥${totalFees.toFixed(2)}`);
  sections.push("");

  sections.push("**净收益**");
  sections.push(
    `  ${icon} 净收益: ${netPnl >= 0 ? "+" : ""}¥${netPnl.toFixed(2)} (${netPnlPct >= 0 ? "+" : ""}${netPnlPct.toFixed(2)}%)`,
  );
  sections.push("  计算公式: 已实现盈亏 - 手续费 + 未实现盈亏变动");

  return sections.join("\n");
}

/** All available report templates. */
export const REPORT_TEMPLATES: ReportTemplate[] = [
  {
    id: "morning_brief",
    name: "早盘简报",
    description: "盘前推送：板块排名、主力资金流向、北向资金、持仓概览",
    defaultIntervalMinutes: 0, // one-shot at scheduled time
    generate: generateMorningBrief,
  },
  {
    id: "market_close",
    name: "收盘总结",
    description: "收盘后推送：指数行情、领涨板块、龙虎榜热点、北向资金、持仓盈亏、今日信号",
    defaultIntervalMinutes: 0,
    generate: generateMarketClose,
  },
  {
    id: "weekly_review",
    name: "周度回顾",
    description: "每周推送：板块周表现、行业资金流向、本周交易统计、高频推荐股",
    defaultIntervalMinutes: 0,
    generate: generateWeeklyReview,
  },
  {
    id: "watchlist_update",
    name: "自选股行情",
    description: "定期推送自选股实时行情",
    defaultIntervalMinutes: 30,
    generate: generateWatchlistUpdate,
  },
  {
    id: "daily_scan",
    name: "每日选股",
    description: "每日策略选股+自动与上次结果对比（新增/消失/评分变化）",
    defaultIntervalMinutes: 0,
    generate: generateDailyScan,
  },
  {
    id: "institutional_top3",
    name: "机构行为Top3",
    description: "每日机构行为分析：净流入+吸筹+抢筹+利好语料，筛出前三",
    defaultIntervalMinutes: 0,
    generate: generateInstitutionalTop3,
  },
  {
    id: "daily_journal",
    name: "交易日志",
    description: "收盘后综合日志：大盘+盈亏+委托+成交+信号统计+风控",
    defaultIntervalMinutes: 0,
    generate: generateDailyJournal,
  },
  {
    id: "monthly_report",
    name: "月度绩效",
    description: "月度报告：整体盈亏+策略分析+标的分析+信号命中率+风控指标",
    defaultIntervalMinutes: 0,
    generate: generateMonthlyReport,
  },
  {
    id: "daily_settlement",
    name: "每日结算",
    description:
      "收盘结算汇总：已实现盈亏、未实现盈亏变动、胜率、盈亏比、手续费合计、净收益",
    defaultIntervalMinutes: 0,
    generate: generateDailySettlement,
  },
];

/** Find a report template by ID. */
export function findReportTemplate(id: string): ReportTemplate | undefined {
  return REPORT_TEMPLATES.find((t) => t.id === id);
}
