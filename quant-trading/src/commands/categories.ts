/**
 * Stock screening categories and formatting helpers.
 *
 * Each category re-weights the 4 investment dimensions (valuation, growth,
 * profitability, quality+momentum — each 0–25 points) to surface stocks
 * matching a specific investing style.
 *
 * Usage:
 *   /选股          → overview of all categories (top 5 each)
 *   /选股 高成长    → detailed view of high-growth category
 */

// ── Types ─────────────────────────────────────────────────────────────

export interface ScreenCategory {
  key: string;
  name: string;
  emoji: string;
  desc: string;
  /** Aliases the user can type to select this category. */
  keywords: string[];
  /** Return true if the stock qualifies for this category. */
  filter: (f: Record<string, unknown>) => boolean;
  /** Dimension weights (must sum to 1.0). */
  weights: {
    valuation: number;
    growth: number;
    profitability: number;
    quality_momentum: number;
  };
}

export interface StockResult {
  code: string;
  name: string;
  score: number;
  signals: string[];
  factors: Record<string, unknown>;
}

// ── Category definitions ──────────────────────────────────────────────

export const SCREEN_CATEGORIES: ScreenCategory[] = [
  {
    key: "growth",
    name: "高成长",
    emoji: "🚀",
    desc: "营收/利润高速增长",
    keywords: ["高成长", "成长", "growth"],
    filter: (f) => {
      const revG = Number(f.revenue_growth_yoy ?? -999);
      const npG = Number(f.net_profit_growth_yoy ?? -999);
      // Require meaningful growth: revenue >=20% OR net profit >=25%
      // Also accept strong combined growth (both >=10%)
      return revG >= 20 || npG >= 25 || (revG >= 10 && npG >= 10);
    },
    weights: { valuation: 0.15, growth: 0.45, profitability: 0.25, quality_momentum: 0.15 },
  },
  {
    key: "bluechip",
    name: "稳健白马",
    emoji: "🏛️",
    desc: "高ROE+低负债+稳定盈利",
    keywords: ["稳健", "白马", "稳健白马", "bluechip"],
    filter: (f) =>
      // Loosened: ROE>=8 (was 10), F-Score>=4 (was 5), debt<60 (was 55)
      Number(f.roe ?? 0) >= 8 && Number(f.debt_ratio ?? 100) < 60 && Number(f.f_score ?? 0) >= 4,
    weights: { valuation: 0.25, growth: 0.1, profitability: 0.4, quality_momentum: 0.25 },
  },
  {
    key: "value",
    name: "价值洼地",
    emoji: "💎",
    desc: "低估值+基本面合格",
    keywords: ["价值", "低估值", "潜力", "value"],
    filter: (f) => {
      const pe = Number(f.pe ?? 999);
      const pb = Number(f.pb ?? 999);
      return pe > 0 && pe < 25 && pb > 0 && pb < 3 && Number(f.roe ?? 0) > 5;
    },
    weights: { valuation: 0.45, growth: 0.2, profitability: 0.2, quality_momentum: 0.15 },
  },
  {
    key: "momentum",
    name: "强势动能",
    emoji: "⚡",
    desc: "技术面强+RPS高+放量",
    keywords: ["热点", "动能", "强势", "momentum"],
    filter: () => true, // All pass — sorted by momentum-weighted score
    weights: { valuation: 0.1, growth: 0.15, profitability: 0.15, quality_momentum: 0.6 },
  },
  {
    key: "lowrisk",
    name: "低风险",
    emoji: "🛡️",
    desc: "低波动+低负债+稳现金流",
    keywords: ["低风险", "安全", "低波", "lowrisk"],
    filter: (f) => {
      const pe = Number(f.pe ?? 999);
      const revG = Number(f.revenue_growth_yoy ?? 0);
      // Must have: low volatility + low debt + reasonable valuation + no severe revenue decline
      return (
        Number(f.volatility_20d ?? 999) < 35 &&
        Number(f.debt_ratio ?? 100) < 50 &&
        pe > 0 &&
        pe < 60 &&
        revG > -20
      );
    },
    weights: { valuation: 0.25, growth: 0.1, profitability: 0.35, quality_momentum: 0.3 },
  },
  {
    key: "overall",
    name: "综合优选",
    emoji: "🏆",
    desc: "四维均衡得分最高",
    keywords: ["综合", "全部", "overall", "all"],
    filter: () => true,
    weights: { valuation: 0.25, growth: 0.25, profitability: 0.25, quality_momentum: 0.25 },
  },
];

// ── Score computation ─────────────────────────────────────────────────

/** Re-weight the 4 investment sub-scores (each 0–25) into a 0–100 category score. */
function catScore(f: Record<string, unknown>, w: ScreenCategory["weights"]): number {
  const inv = (f.inv_details ?? {}) as Record<string, number>;
  return (
    ((inv.valuation ?? 0) * w.valuation +
      (inv.growth ?? 0) * w.growth +
      (inv.profitability ?? 0) * w.profitability +
      (inv.quality_momentum ?? 0) * w.quality_momentum) *
    4
  );
}

/**
 * Check how many stocks have valid fundamental data.
 * If most stocks are missing fundamentals, the data source was likely rate-limited.
 */
function checkDataQuality(results: StockResult[]): {
  hasFinancials: number;
  total: number;
  degraded: boolean;
} {
  let hasFinancials = 0;
  for (const r of results) {
    const f = r.factors || {};
    // A stock has financials if at least 2 of these key fields are present and non-zero
    const checks = [f.roe, f.pe, f.revenue_growth_yoy, f.f_score].filter(
      (v) => v != null && Number(v) !== 0 && Number(v) !== -999,
    );
    if (checks.length >= 2) hasFinancials++;
  }
  return {
    hasFinancials,
    total: results.length,
    degraded: results.length > 0 && hasFinancials < results.length * 0.3,
  };
}

/** Apply a loosened version of a category filter for fallback display. */
function looseFilter(cat: ScreenCategory, f: Record<string, unknown>): boolean {
  // Momentum and overall always pass — no loosening needed
  if (cat.key === "momentum" || cat.key === "overall") return true;

  // When fundamental data is missing, most values will be absent.
  // In that case, accept the stock if it has ANY positive factor data.
  const hasFundamentals =
    f.roe != null || f.pe != null || f.revenue_growth_yoy != null || f.f_score != null;

  // If no fundamentals at all, accept by score or always include
  if (!hasFundamentals) return true;

  // For categories with strict fundamental filters, use relaxed criteria
  switch (cat.key) {
    case "growth":
      return Number(f.revenue_growth_yoy ?? 0) >= 5 || Number(f.net_profit_growth_yoy ?? 0) >= 5;
    case "bluechip":
      return Number(f.roe ?? 0) >= 5 || Number(f.f_score ?? 0) >= 3;
    case "value":
      return (
        (Number(f.pe ?? 0) > 0 && Number(f.pe ?? 999) < 40) ||
        (Number(f.pb ?? 0) > 0 && Number(f.pb ?? 999) < 5)
      );
    case "lowrisk":
      return (
        Number(f.volatility_20d ?? 0) < 50 &&
        Number(f.pe ?? 999) < 100 &&
        Number(f.revenue_growth_yoy ?? 0) > -30
      );
    default:
      return true;
  }
}

/** Find the best matching category for a user keyword. */
export function findCategory(input: string): ScreenCategory | undefined {
  const normalize = (s: string) =>
    s
      .toLowerCase()
      .trim()
      .replace(/[\s\u3000]+/g, "")
      .replace(/[，。、“”"'`~!@#$%^&*()\-_=+\[\]{};:,.<>/?\\|]+/g, "");

  const q = normalize(input);
  if (!q) return undefined;

  return SCREEN_CATEGORIES.find((c) => {
    if (normalize(c.key) === q) return true;
    if (normalize(c.name) === q) return true;
    return c.keywords.some((k) => normalize(k) === q);
  });
}

// ── Formatting helpers ────────────────────────────────────────────────

/** Compute industry-level median PE/PB/ROE from the screening pool for peer comparison. */
interface IndustryStats {
  medianPE: number | null;
  medianROE: number | null;
  medianPB: number | null;
  count: number;
}

function buildIndustryMedians(results: StockResult[]): Map<string, IndustryStats> {
  const byIndustry = new Map<string, { pe: number[]; roe: number[]; pb: number[] }>();
  for (const r of results) {
    const f = r.factors || {};
    const industry = f.industry ? String(f.industry) : "";
    if (!industry) continue;
    const entry = byIndustry.get(industry) ?? { pe: [], roe: [], pb: [] };
    const pe = f.pe != null ? Number(f.pe) : null;
    const roe = f.roe != null ? Number(f.roe) : null;
    const pb = f.pb != null ? Number(f.pb) : null;
    if (pe != null && pe > 0 && pe < 500) entry.pe.push(pe);
    if (roe != null) entry.roe.push(roe);
    if (pb != null && pb > 0) entry.pb.push(pb);
    byIndustry.set(industry, entry);
  }

  const median = (arr: number[]): number | null => {
    if (arr.length === 0) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
  };

  const result = new Map<string, IndustryStats>();
  for (const [industry, data] of byIndustry) {
    result.set(industry, {
      medianPE: median(data.pe),
      medianROE: median(data.roe),
      medianPB: median(data.pb),
      count: Math.max(data.pe.length, data.roe.length, data.pb.length),
    });
  }
  return result;
}

/** Format peer deviation as compact string, e.g. "vs同行PE-12%" */
function formatPeerDev(
  f: Record<string, unknown>,
  industryMedians: Map<string, IndustryStats>,
): string {
  const industry = f.industry ? String(f.industry) : "";
  if (!industry) return "";
  const stats = industryMedians.get(industry);
  if (!stats || stats.count < 3) return ""; // need ≥3 peers for meaningful comparison

  const parts: string[] = [];
  const pe = f.pe != null ? Number(f.pe) : null;
  if (pe != null && pe > 0 && stats.medianPE != null && stats.medianPE > 0) {
    const dev = ((pe - stats.medianPE) / stats.medianPE) * 100;
    if (Math.abs(dev) >= 10) {
      parts.push(`PE${dev > 0 ? "+" : ""}${dev.toFixed(0)}%vs同行`);
    }
  }
  const roe = f.roe != null ? Number(f.roe) : null;
  if (roe != null && stats.medianROE != null && stats.medianROE !== 0) {
    const dev = ((roe - stats.medianROE) / Math.abs(stats.medianROE)) * 100;
    if (Math.abs(dev) >= 15) {
      parts.push(`ROE${dev > 0 ? "+" : ""}${dev.toFixed(0)}%vs同行`);
    }
  }
  return parts.join(" ");
}

/** One-line compact stock display for the overview panel. */
function formatBrief(r: StockResult, score: number, overlapCount?: number): string {
  const f = r.factors || {};
  const parts: string[] = [];
  const industry = f.industry ? String(f.industry) : "";
  if (industry) parts.push(industry);
  if (f.pe != null) parts.push(`PE${Number(f.pe).toFixed(0)}`);
  if (f.roe != null) parts.push(`ROE${Number(f.roe).toFixed(0)}%`);
  const revG = f.revenue_growth_yoy;
  if (revG != null) parts.push(`营收${Number(revG) >= 0 ? "+" : ""}${Number(revG).toFixed(0)}%`);
  // OCF quality indicator
  const ocfRatio = f.ocf_to_net_profit;
  if (ocfRatio != null) {
    parts.push(Number(ocfRatio) > 1 ? "OCF✓" : "OCF✗");
  }
  const brief = parts.length > 0 ? `  ${parts.join(" ")}` : "";
  const overlapTag = overlapCount != null && overlapCount >= 3 ? " ⭐" : "";
  return `${r.code} ${r.name || ""}${brief}  ${score.toFixed(0)}分${overlapTag}`;
}

/** Multi-line detailed stock display for category detail view. */
function formatFull(
  r: StockResult,
  idx: number,
  score: number,
  industryMedians?: Map<string, IndustryStats>,
): string {
  const f = r.factors || {};
  const industry = f.industry ? String(f.industry) : "";
  const mktCap = f.total_market_val_yi ? `${Number(f.total_market_val_yi).toFixed(0)}亿` : "";
  const tag = [industry, mktCap].filter(Boolean).join(" ");
  const tagStr = tag ? ` (${tag})` : "";

  const parts: string[] = [];
  if (f.pe != null) parts.push(`PE=${Number(f.pe).toFixed(1)}`);
  if (f.pb != null) parts.push(`PB=${Number(f.pb).toFixed(2)}`);
  if (f.roe != null) parts.push(`ROE=${Number(f.roe).toFixed(1)}%`);
  if (f.f_score != null) parts.push(`F=${String(f.f_score)}/9`);
  const revG = f.revenue_growth_yoy;
  if (revG != null) parts.push(`营收${Number(revG) >= 0 ? "+" : ""}${Number(revG).toFixed(1)}%`);
  const metricsStr = parts.length > 0 ? `  ${parts.join(" ")}` : "";

  const extra: string[] = [];
  if (f.chg_3m != null)
    extra.push(`3月${Number(f.chg_3m) >= 0 ? "+" : ""}${Number(f.chg_3m).toFixed(1)}%`);
  if (f.chg_6m != null)
    extra.push(`6月${Number(f.chg_6m) >= 0 ? "+" : ""}${Number(f.chg_6m).toFixed(1)}%`);
  if (f.rps_120 != null) extra.push(`RPS=${Number(f.rps_120).toFixed(0)}`);
  if (f.volatility_20d != null) extra.push(`波动${Number(f.volatility_20d).toFixed(0)}%`);
  // Cash flow quality
  const ocfRatio = f.ocf_to_net_profit;
  if (ocfRatio != null) {
    const ratio = Number(ocfRatio);
    extra.push(ratio > 1 ? `OCF/NP=${ratio.toFixed(1)}✓` : `OCF/NP=${ratio.toFixed(1)}⚠`);
  }
  // Accounts receivable collection risk
  const arDays = f.ar_turnover_days;
  if (arDays != null) {
    const days = Number(arDays);
    if (days > 120) extra.push(`应收${days.toFixed(0)}天⚠`);
    else if (days > 0) extra.push(`应收${days.toFixed(0)}天`);
  }
  // Gross margin trend (cost-sensitivity proxy)
  const gm = f.gross_margin != null ? Number(f.gross_margin) : null;
  const gmPrev = f.gross_margin_prev != null ? Number(f.gross_margin_prev) : null;
  if (gm != null && gmPrev != null && gmPrev > 0) {
    const delta = gm - gmPrev;
    if (Math.abs(delta) >= 1) {
      extra.push(`毛利率${delta >= 0 ? "+" : ""}${delta.toFixed(1)}pp${delta < -3 ? "⚠" : ""}`);
    }
  }
  const volSig = (r.signals || []).find((s) => s.startsWith("放量"));
  if (volSig) {
    const m = volSig.match(/[\d.]+倍/);
    if (m) extra.push(`${m[0]}量`);
  }
  const extraLine = extra.length > 0 ? `\n       ${extra.join(" | ")}` : "";

  // Peer comparison line (only in detail view where industryMedians is passed)
  const peerDev = industryMedians ? formatPeerDev(f, industryMedians) : "";
  const peerLine = peerDev ? `\n       📊 ${peerDev}` : "";

  const invD = f.inv_details as Record<string, number> | undefined;
  const subLine = invD
    ? `\n       估值${Number(invD.valuation ?? 0).toFixed(0)} 成长${Number(invD.growth ?? 0).toFixed(0)} 盈利${Number(invD.profitability ?? 0).toFixed(0)} 质量${Number(invD.quality_momentum ?? 0).toFixed(0)}`
    : "";

  return `  ${String(idx).padStart(2, " ")}. ${r.code} ${r.name || ""}${tagStr}  ${score.toFixed(0)}分${metricsStr}${extraLine}${peerLine}${subLine}`;
}

// ── Public formatters (used by /选股 command) ─────────────────────────

/** Render a detailed view of a single category's results. */
export function formatCategoryDetail(
  cat: ScreenCategory,
  results: StockResult[],
  totalCount: number,
): string {
  // Build industry medians from full pool for peer comparison
  const industryMedians = buildIndustryMedians(results);

  const qualifying = results
    .filter((r) => cat.filter(r.factors || {}))
    .map((r) => ({ r, cs: catScore(r.factors || {}, cat.weights) }))
    .sort((a, b) => b.cs - a.cs)
    .slice(0, 15);

  if (qualifying.length === 0) {
    // Try fallback with loosened filters
    const dq = checkDataQuality(results);
    const fallback = results
      .filter((r) => looseFilter(cat, r.factors || {}))
      .map((r) => ({ r, cs: catScore(r.factors || {}, cat.weights) }))
      .sort((a, b) => b.cs - a.cs)
      .slice(0, 10);

    if (fallback.length === 0) {
      return `${cat.emoji} ${cat.name}：当前池中无符合条件的股票`;
    }

    const lines = [
      `${cat.emoji} **${cat.name}** — ${cat.desc}`,
      dq.degraded
        ? `⚠️ 基本面数据不完整（${String(dq.hasFinancials)}/${String(dq.total)}只有财务数据），以下为宽松条件筛选结果，仅供参考`
        : `无严格符合条件的股票，以下为宽松筛选结果`,
      "",
    ];
    for (let i = 0; i < fallback.length; i++) {
      lines.push(formatFull(fallback[i].r, i + 1, fallback[i].cs, industryMedians));
    }
    return lines.join("\n");
  }

  const lines = [
    `${cat.emoji} **${cat.name}** — ${cat.desc}`,
    `${String(qualifying.length)} 只符合（从 ${String(totalCount)} 只中筛选）`,
    "",
  ];

  for (let i = 0; i < qualifying.length; i++) {
    lines.push(formatFull(qualifying[i].r, i + 1, qualifying[i].cs, industryMedians));
  }

  return lines.join("\n");
}

/**
 * Pre-compute how many categories each stock qualifies for.
 * Used to annotate multi-category overlap in the overview.
 */
function buildOverlapMap(results: StockResult[]): Map<string, { count: number; cats: string[] }> {
  const map = new Map<string, { count: number; cats: string[] }>();
  for (const cat of SCREEN_CATEGORIES) {
    // "overall" and "momentum" accept everything — exclude from overlap count
    if (cat.key === "overall" || cat.key === "momentum") continue;
    const qualifying = results
      .filter((r) => cat.filter(r.factors || {}))
      .map((r) => ({ r, cs: catScore(r.factors || {}, cat.weights) }))
      .sort((a, b) => b.cs - a.cs)
      .slice(0, 10); // top 10 per category for overlap tracking
    for (const { r } of qualifying) {
      const entry = map.get(r.code) ?? { count: 0, cats: [] };
      entry.count++;
      entry.cats.push(cat.name);
      map.set(r.code, entry);
    }
  }
  return map;
}

/** Render an overview panel with top 5 stocks per category. */
export function formatCategoryOverview(results: StockResult[], totalCount: number): string {
  const dq = checkDataQuality(results);
  const lines = [
    `选股完成，共 ${String(totalCount)} 只，已存入 stock-list.json`,
    "QMT 明日开盘前自动拉取",
  ];

  if (dq.degraded) {
    lines.push(
      `⚠️ 基本面数据不完整（${String(dq.hasFinancials)}/${String(dq.total)}只有财务数据），部分分类结果可能受限`,
    );
  }
  lines.push("");

  // Pre-compute multi-category overlap map
  const overlapMap = buildOverlapMap(results);

  // Track which stocks appear across categories for dedup awareness
  const seenCodes = new Set<string>();
  let uniqueCount = 0;

  for (const cat of SCREEN_CATEGORIES) {
    let qualifying = results
      .filter((r) => cat.filter(r.factors || {}))
      .map((r) => ({ r, cs: catScore(r.factors || {}, cat.weights) }))
      .sort((a, b) => b.cs - a.cs)
      .slice(0, 5);

    // Fallback: if empty and data was degraded, try loosened filter
    let isFallback = false;
    if (qualifying.length === 0 && dq.degraded) {
      qualifying = results
        .filter((r) => looseFilter(cat, r.factors || {}))
        .map((r) => ({ r, cs: catScore(r.factors || {}, cat.weights) }))
        .sort((a, b) => b.cs - a.cs)
        .slice(0, 5);
      isFallback = qualifying.length > 0;
    }

    if (qualifying.length === 0) {
      lines.push(`${cat.emoji} ${cat.name}（${cat.desc}）：无符合`);
      lines.push("");
      continue;
    }

    const suffix = isFallback ? " ⚠️宽松" : "";
    lines.push(
      `${cat.emoji} **${cat.name}** (${cat.desc}) [${String(qualifying.length)}ֻ]${suffix}:`,
    );
    for (const { r, cs } of qualifying) {
      const isNew = !seenCodes.has(r.code);
      if (isNew) {
        seenCodes.add(r.code);
        uniqueCount++;
      }
      const overlap = overlapMap.get(r.code);
      lines.push(`  • ${formatBrief(r, cs, overlap?.count)}`);
    }
    lines.push("");
  }

  // Summary line showing unique stock count
  if (uniqueCount < totalCount) {
    lines.push(`📊 各分类共覆盖 ${String(uniqueCount)} 只不同标的`);
  }

  // Cross-category overlap summary: list stocks in ≥3 filtered categories
  const multiHits = [...overlapMap.entries()]
    .filter(([, v]) => v.count >= 3)
    .sort((a, b) => b[1].count - a[1].count);
  if (multiHits.length > 0) {
    lines.push("");
    lines.push("⭐ **多类上榜标的**（同时命中≥3个筛选类别，基本面因子较全面）:");
    for (const [code, { count, cats }] of multiHits) {
      const stock = results.find((r) => r.code === code);
      const name = stock?.name || "";
      const f = stock?.factors || {};
      // Brief quality snapshot
      const hints: string[] = [];
      if (f.pe != null) hints.push(`PE${Number(f.pe).toFixed(0)}`);
      if (f.f_score != null) hints.push(`F=${String(f.f_score)}/9`);
      const ocfR = f.ocf_to_net_profit;
      if (ocfR != null) hints.push(Number(ocfR) > 1 ? "OCF✓" : "OCF⚠");
      const arD = f.ar_turnover_days;
      if (arD != null && Number(arD) > 120) hints.push(`应收${Number(arD).toFixed(0)}天⚠`);
      const hintStr = hints.length > 0 ? `  ${hints.join(" ")}` : "";
      lines.push(`  ${code} ${name}  ${String(count)}类命中（${cats.join("、")}）${hintStr}`);
    }
  }

  lines.push("");
  lines.push("💡 查看详情: /选股 高成长、/选股 稳健白马、/选股 价值洼地 等");
  return lines.join("\n");
}

/** Show available categories when user input doesn't match. */
export function showCategoryHelp(input: string): string {
  const catList = SCREEN_CATEGORIES.map((c) => `  ${c.emoji} **${c.name}** — ${c.desc}`);
  return [
    `未知类别「${input}」`,
    "",
    "可用类别:",
    ...catList,
    "",
    "用法: /选股 <类别>  例如: /选股 高成长",
  ].join("\n");
}
