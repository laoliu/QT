/**
 * stock_recommend tool — compound "screen → route → backtest → recommend" pipeline.
 *
 * This tool forces the full workflow in a single call so the AI cannot
 * skip the screening step or substitute hardcoded stock codes.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { MarketDataProvider } from "../data/market-data-provider.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { ScanHistoryStore } from "../services/scan-history-store.js";
import { computeStopAdvice } from "../services/stop-advisor.js";
import type { KlineBar, QuantTradingConfig } from "../types.js";

// ── Screener rotation to avoid returning the same stocks every call ──
// When the AI doesn't specify a screenerId, we cycle through diverse screeners
// so consecutive calls produce different candidate pools.
const _ROTATION_SCREENERS = [
  "fscore", // 基本面-价值质量
  "technical", // 技术面-趋势突破
  "canslim", // 基本面-成长动量
  "volume_price", // 技术面-量价异动
  "magic_formula", // 基本面-魔法公式
  "peg", // 基本面-PEG成长
  "sector_hotspot", // 短线-板块热点
  "overnight", // 短线-隔夜持股
] as const;
let _rotationIndex = 0;

function pickRotatingScreener(): string {
  const screener = _ROTATION_SCREENERS[_rotationIndex % _ROTATION_SCREENERS.length];
  _rotationIndex++;
  return screener;
}

interface ScreenResult {
  code: string;
  name?: string;
  score?: number;
  factors?: Record<string, unknown>;
}

interface BacktestMetrics {
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  totalTrades: number;
}

interface RouteBacktestItem {
  symbol: string;
  name?: string;
  ok: boolean;
  profileKey?: string;
  combo?: { name: string; method: string; strategies: string[] };
  routingSource?: string;
  reason?: string;
  backtest?: BacktestMetrics | null;
  error?: string | null;
}

export function createStockRecommendTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  scanHistoryStore?: ScanHistoryStore,
  getDataProvider?: () => MarketDataProvider,
) {
  return {
    name: "stock_recommend",
    description:
      "【推荐选股一键工具】自动完成 全市场选股→策略路由→回测验证→排名推荐 全流程。\n" +
      "两种模式：\n" +
      "• mode=classic（默认）：全市场选股 → 策略画像 → 自适应回测 → 筛选推荐（偏向已验证的强势股）\n" +
      "• mode=predictive：前瞻式选股 → 主力吸筹 + 机构行为 + 舆情热度 + 板块轮动 → 预测未来潜力股\n" +
      "  - 寻找低位蓄势、主力建仓、机构增持、板块轮动受益的股票\n" +
      "  - 自动按持有周期分类：短期(1-10日)、中期(10-60日)、长期(60日+)\n" +
      "\n" +
      "⚠️ 选股器(screenerId)选择指南（classic模式下非常重要）：\n" +
      "不同选股器产生完全不同的候选股，同一选股器在同一天会返回相同结果。\n" +
      "如果用户没有指定screenerId，系统会自动轮换选股器以避免推荐重复。\n" +
      "根据用户意图选择合适的选股器：\n" +
      "  - 用户要'价值股/低估值/基本面好的' → fscore 或 magic_formula\n" +
      "  - 用户要'成长股/高增长' → canslim 或 peg\n" +
      "  - 用户要'短线/技术突破' → technical 或 volume_price\n" +
      "  - 用户要'隔夜/尾盘买入' → overnight 或 eod_catalyst\n" +
      "  - 用户要'热点/题材/龙头' → sector_hotspot 或 dragon_master 或 lianban_relay\n" +
      "  - 用户要'事件驱动/催化剂' → event_catalyst\n" +
      "  - 用户要'再推荐/换一批/不一样的' → 换一个不同的screenerId\n" +
      "参数：count=推荐数量(默认3), mode=模式, horizon=持有周期筛选, screenerId=选股策略。",
    parameters: {
      type: "object" as const,
      properties: {
        count: {
          type: "number",
          description: "推荐股票数量，默认3",
        },
        mode: {
          type: "string",
          enum: ["classic", "predictive"],
          description:
            "推荐模式: classic=经典回测验证(偏已涨), predictive=前瞻预测(偏未来潜力, 综合基本面+主力+热度+板块)",
        },
        horizon: {
          type: "string",
          enum: ["short", "mid", "long", "all"],
          description:
            "持有周期: short=短期(1-10日,动量/突破), mid=中期(10-60日,吸筹/轮动), long=长期(60日+,价值/成长), all=全部",
        },
        screenerId: {
          type: "string",
          enum: [
            "fscore",
            "canslim",
            "magic_formula",
            "technical",
            "volume_price",
            "peg",
            "overnight",
            "lianban_relay",
            "event_catalyst",
            "eod_catalyst",
            "sector_hotspot",
            "dragon_master",
          ],
          description:
            "选股策略(classic模式), 共12种。" +
            "基本面类: fscore=F-Score价值质量(季报驱动,适合中长线), canslim=CAN SLIM成长动量(盈利+营收增长), " +
            "magic_formula=魔法公式(高ROIC+低估值), peg=PEG/GARP合理价格成长股; " +
            "技术面类: technical=技术指标(均线/MACD/RSI突破), volume_price=量价异动(机构建仓/底部反转); " +
            "短线交易类: overnight=隔夜持股法(尾盘介入隔日卖), lianban_relay=连板接力(N进N+1), " +
            "dragon_master=真龙战法(主线龙一打板); " +
            "事件驱动类: event_catalyst=事件催化(美股映射A股), eod_catalyst=尾盘催化(14:30选股), " +
            "sector_hotspot=板块热点竞价(涨停池→同板块候选). " +
            "不指定则自动轮换避免重复。",
        },
        universe: {
          type: "string",
          enum: ["all", "hs300", "zz500", "zz1000", "sz50", "cyb", "kc50"],
          description:
            "选股范围。用户未指定时必须使用 all(全市场扫描约5000只)，不要自行缩小范围。超时会自动降级到较小范围，无需担心性能。",
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock", "hk_stock"],
          description: "市场类型，默认 cn_stock",
        },
      },
      required: [],
    },
    async execute(args: Record<string, unknown>) {
      const count = (args.count as number) ?? 3;
      const mode = (args.mode as string) ?? "classic";
      const horizon = (args.horizon as string) ?? "all";
      const market = (args.market as string) ?? "cn_stock";
      const requestedUniverse = (args.universe as string) ?? "all";

      if (mode === "predictive") {
        return runPredictiveRecommend(
          count,
          horizon,
          requestedUniverse,
          market,
          config,
          scanHistoryStore,
          getDataProvider,
        );
      }

      const screenerId = (args.screenerId as string) ?? pickRotatingScreener();
      const limit = Math.max(count * 5, 20); // screen more than needed

      // ── Step 1: Screen stocks from real market data ──────────────
      const screenResult = await runScreenWithFallback(
        screenerId,
        requestedUniverse,
        market,
        limit,
        config,
      );

      if (!screenResult.ok || screenResult.codes.length === 0) {
        return {
          ok: false,
          step: "screening",
          error: screenResult.error ?? "选股未返回结果",
          fallbackNote: screenResult.fallbackNote,
          hint: "Python 选股引擎可能未就绪。请检查 Python 环境配置，或稍后重试。",
        };
      }

      // ── Step 2: Route + Backtest via strategy_router ─────────────
      const symbols = screenResult.codes.slice(0, 15); // cap at 15 to avoid timeout
      const endDate = new Date().toISOString().slice(0, 10);
      const startDate = getOneYearAgo();

      const routeResult = await runPythonScript<{
        ok: boolean;
        results?: RouteBacktestItem[];
        total?: number;
        error?: string;
      }>(
        "engines/strategy_router.py",
        { action: "profile_route_backtest", symbols, startDate, endDate },
        config,
        { timeoutMs: 300_000 },
      );

      if (!routeResult.ok || !routeResult.data?.results) {
        // Fallback: if strategy_router Python fails, still return screening results
        return {
          ok: false,
          step: "backtest",
          screeningSucceeded: true,
          screenedStocks: screenResult.stocks.slice(0, count),
          screenerId,
          universe: screenResult.universe,
          error: routeResult.error ?? "策略路由回测引擎失败",
          hint: "选股已完成但回测引擎不可用。以上股票通过选股筛选但未经回测验证。",
        };
      }

      // ── Step 3: Rank and filter backtest results ─────────────────
      const backtested = (routeResult.data.results ?? [])
        .filter((r) => r.ok && r.backtest && r.backtest.totalTrades > 0)
        .map((r) => ({
          symbol: r.symbol,
          name: r.name ?? r.symbol,
          screenScore: screenResult.stocks.find((s) => s.code === r.symbol)?.score,
          strategy: r.combo
            ? { name: r.combo.name, method: r.combo.method, components: r.combo.strategies }
            : null,
          profileKey: r.profileKey,
          routingSource: r.routingSource,
          backtest: r.backtest!,
          warnings: generateWarnings(r.backtest!),
        }));

      // Sort by composite score: return > sharp > winRate, penalize drawdown
      backtested.sort((a, b) => {
        const scoreA = compositeScore(a.backtest);
        const scoreB = compositeScore(b.backtest);
        return scoreB - scoreA;
      });

      const recommended = backtested.slice(0, count);
      const insufficient = recommended.length < count;

      // ── Step 4: Compute stop-loss / take-profit advice ──────────
      const stopAdviceMap = new Map<string, { stopLoss: string; takeProfit: string; stopLossPct: string; takeProfitPct: string; rewardRiskRatio: string; method: string; description: string }>();
      if (getDataProvider) {
        const provider = getDataProvider();
        const klEndDate = new Date().toISOString().slice(0, 10);
        const klStartDate = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
        await Promise.all(
          recommended.map(async (r) => {
            try {
              const market = detectMarketFromSymbol(r.symbol);
              const bars: KlineBar[] = await provider.getKlines(r.symbol, "1d", klStartDate, klEndDate, market);
              if (bars.length < 5) return;
              const quote = await provider.getQuote(r.symbol, market);
              const currentPrice = quote?.price ?? bars[bars.length - 1].close;
              const advice = computeStopAdvice(r.symbol, currentPrice, currentPrice, bars, { style: "mid" });
              const rec = advice.recommended;
              stopAdviceMap.set(r.symbol, {
                stopLoss: `¥${rec.stopLoss}`,
                takeProfit: `¥${rec.takeProfit}`,
                stopLossPct: `${(rec.stopLossPct * 100).toFixed(1)}%`,
                takeProfitPct: `${(rec.takeProfitPct * 100).toFixed(1)}%`,
                rewardRiskRatio: `${rec.rewardRiskRatio}:1`,
                method: rec.method,
                description: rec.description,
              });
            } catch {
              // Non-fatal — skip stop advice for this stock
            }
          }),
        );
      }

      return {
        ok: true,
        pipeline: `stock_screen(${screenerId}, ${screenResult.universe}) → strategy_router(profile_route_backtest) → rank`,
        screening: {
          strategy: screenerId,
          universe: screenResult.universe,
          totalScreened: screenResult.totalScreened,
          candidateCount: screenResult.codes.length,
          fallbackNote: screenResult.fallbackNote,
        },
        backtestSummary: {
          totalBacktested: routeResult.data.results?.length ?? 0,
          passedFilter: backtested.length,
          failedOrZeroTrades: (routeResult.data.results?.length ?? 0) - backtested.length,
        },
        recommendations: recommended.map((r, i) => ({
          rank: i + 1,
          symbol: r.symbol,
          name: r.name,
          screenScore: r.screenScore,
          strategy: r.strategy,
          profileKey: r.profileKey,
          backtest: {
            totalReturn: `${r.backtest.totalReturn.toFixed(1)}%`,
            annualizedReturn:
              r.backtest.totalReturn > 0
                ? `${(r.backtest.totalReturn * (365 / 250)).toFixed(1)}%`
                : undefined,
            sharpeRatio: r.backtest.sharpeRatio.toFixed(2),
            maxDrawdown: `${r.backtest.maxDrawdown.toFixed(1)}%`,
            winRate: `${r.backtest.winRate.toFixed(1)}%`,
            totalTrades: r.backtest.totalTrades,
          },
          stopAdvice: stopAdviceMap.get(r.symbol) ?? null,
          warnings: r.warnings,
        })),
        note: insufficient
          ? `仅找到 ${recommended.length} 只合格股票（要求 ${count} 只）。部分候选股回测表现不佳或无交易信号被过滤。`
          : `从 ${screenResult.codes.length} 只候选股中筛选出 ${recommended.length} 只经回测验证的推荐股票。`,
      };
    },
  };
}

// ── Predictive recommendation pipeline ───────────────────────────

interface ChipAccStock {
  symbol: string;
  name: string;
  total_score: number;
  level: string;
  conclusion: string;
  dimension_scores?: Record<string, number>;
}

interface InstFlowData {
  score?: number;
  conclusion?: string;
}

async function runPredictiveRecommend(
  count: number,
  horizon: string,
  requestedUniverse: string,
  market: string,
  config: QuantTradingConfig,
  scanHistoryStore?: ScanHistoryStore,
  getDataProvider?: () => MarketDataProvider,
): Promise<Record<string, unknown>> {
  const limit = Math.max(count * 5, 20);

  // ── Step 1: Screen with volume_price (finds low-position accumulation signals) ──
  const screenResult = await runScreenWithFallback(
    "volume_price",
    requestedUniverse,
    market,
    limit,
    config,
  );

  if (!screenResult.ok || screenResult.codes.length === 0) {
    return {
      ok: false,
      mode: "predictive",
      step: "screening",
      error: screenResult.error ?? "量价选股未返回结果",
      hint: "volume_price 选股引擎未找到符合低位蓄势条件的股票，请稍后重试。",
    };
  }

  const candidates = screenResult.stocks.slice(0, 15);
  const symbolCsv = candidates.map((s) => s.code).join(",");

  // ── Step 2: Batch chip accumulation scan ────────────────────────
  const chipResult = await runPythonScript<{
    stocks?: ChipAccStock[];
  }>(
    "engines/chip_accumulation.py",
    { action: "scan", symbols: symbolCsv, lookback: 30, min_confidence: 20 },
    config,
    { timeoutMs: 60_000 },
  );

  const chipMap = new Map<string, { score: number; level: string; conclusion: string }>();
  if (chipResult.ok && chipResult.data?.stocks) {
    for (const s of chipResult.data.stocks) {
      chipMap.set(s.symbol, {
        score: s.total_score,
        level: s.level,
        conclusion: s.conclusion,
      });
    }
  }

  // ── Step 3: Sector ranking for context ──────────────────────────
  const sectorResult = await runPythonScript<{
    sectors?: Array<{
      name: string;
      code: string;
      change_pct: number;
      leader?: string;
    }>;
  }>("engines/sector_analysis.py", { action: "ranking", limit: 30 }, config, { timeoutMs: 30_000 });

  const topSectorNames: string[] = [];
  if (sectorResult.ok && sectorResult.data?.sectors) {
    for (const s of sectorResult.data.sectors.slice(0, 10)) {
      topSectorNames.push(s.name);
    }
  }

  // ── Step 4: Institutional flow for top candidates (single-symbol) ──
  const topCodes = candidates.slice(0, 8).map((s) => s.code);
  const instMap = new Map<string, { score: number; conclusion: string }>();

  await Promise.all(
    topCodes.map(async (symbol) => {
      try {
        const result = await runPythonScript<InstFlowData>(
          "engines/institutional_flow.py",
          { action: "smart_money_signal", symbol },
          config,
          { timeoutMs: 30_000 },
        );
        if (result.ok && result.data?.score != null) {
          instMap.set(symbol, {
            score: result.data.score,
            conclusion: result.data.conclusion ?? "",
          });
        }
      } catch {
        // Individual failure is non-fatal
      }
    }),
  );

  // ── Step 5: Compute predictive composite score ──────────────────
  const enriched = candidates.map((s) => {
    const factors = s.factors ?? {};
    const chip = chipMap.get(s.code);
    const inst = instMap.get(s.code);

    // Normalize screen score (volume_price typically 0-10) to 0-100
    const normalizedScreen = Math.min((s.score ?? 0) * 10, 100);
    const chipScore = chip?.score ?? 0;
    const instScore = inst?.score ?? 0;
    const divergenceScore = (factors.divergence_score as number) ?? 0;
    const bottomReversalScore = (factors.bottom_reversal_score as number) ?? 0;
    const volumeRatio = (factors.volume_ratio as number) ?? 1;
    const accumulationScore = (factors.accumulation_score as number) ?? 0;

    // Predictive composite: emphasizes forward-looking signals
    const scoring = {
      chip: chipScore * 0.3, // 主力吸筹 30%
      screen: normalizedScreen * 0.2, // 量价信号 20%
      institutional: instScore * 0.2, // 机构动向 20%
      divergence: Math.min(divergenceScore * 30, 15), // 底背离 15%
      bottomReversal: Math.min(bottomReversalScore * 30, 15), // 底部反转 15%
    };
    const predictiveScore =
      scoring.chip +
      scoring.screen +
      scoring.institutional +
      scoring.divergence +
      scoring.bottomReversal;

    // Horizon classification
    let assignedHorizon: "short" | "mid" | "long";
    let horizonReason: string;

    if (bottomReversalScore > 1.0 || volumeRatio > 2.5) {
      assignedHorizon = "short";
      horizonReason =
        bottomReversalScore > 1.0
          ? "底部放量反转信号明显，短期爆发力强"
          : `量比${volumeRatio.toFixed(1)}倍异常放量，动量触发临近`;
    } else if (chipScore >= 60 || (chipScore >= 40 && accumulationScore > 0.5)) {
      assignedHorizon = "mid";
      horizonReason = `主力吸筹信号(${chip?.level ?? "中等"})，建仓周期需10-60日消化`;
    } else {
      assignedHorizon = "long";
      horizonReason =
        instScore > 40
          ? "机构持续增持，属于价值成长型布局，适合中长线持有"
          : "低位整理蓄势，等待催化剂驱动，适合耐心布局";
    }

    return {
      symbol: s.code,
      name: s.name ?? s.code,
      predictiveScore,
      screenScore: s.score,
      signals: (factors.signals as string[]) ?? [],
      volumeRatio,
      chip: chip
        ? { score: chip.score, level: chip.level, conclusion: chip.conclusion }
        : undefined,
      institutional: inst ? { score: inst.score, conclusion: inst.conclusion } : undefined,
      horizon: assignedHorizon,
      horizonReason,
      scoring,
    };
  });

  // ── Step 6: Filter by horizon and rank ──────────────────────────
  let filtered = enriched;
  if (horizon !== "all") {
    filtered = enriched.filter((c) => c.horizon === horizon);
  }
  filtered.sort((a, b) => b.predictiveScore - a.predictiveScore);
  const recommended = filtered.slice(0, count);

  // Record to scan history
  if (scanHistoryStore) {
    await scanHistoryStore.save(
      "predictive",
      screenResult.universe,
      market,
      screenResult.totalScreened ?? candidates.length,
      recommended.map((r, i) => ({
        code: r.symbol,
        name: r.name,
        score: r.predictiveScore,
        rank: i + 1,
        factors: r.scoring,
        signals: r.signals,
      })),
      {
        tags: ["predictive", horizon],
        notes: `前瞻推荐: chip_acc=${chipMap.size}, inst_flow=${instMap.size}`,
      },
    );
  }

  const horizonLabels: Record<string, string> = {
    short: "短期(1-10日)",
    mid: "中期(10-60日)",
    long: "长期(60日+)",
  };

  // ── Step 7: Compute stop-loss / take-profit advice ──────────────
  const stopAdviceMap = new Map<string, { stopLoss: string; takeProfit: string; stopLossPct: string; takeProfitPct: string; rewardRiskRatio: string; method: string; description: string }>();
  if (getDataProvider) {
    const provider = getDataProvider();
    const klEndDate = new Date().toISOString().slice(0, 10);
    const klStartDate = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    await Promise.all(
      recommended.map(async (r) => {
        try {
          const mkt = detectMarketFromSymbol(r.symbol);
          const bars: KlineBar[] = await provider.getKlines(r.symbol, "1d", klStartDate, klEndDate, mkt);
          if (bars.length < 5) return;
          const quote = await provider.getQuote(r.symbol, mkt);
          const currentPrice = quote?.price ?? bars[bars.length - 1].close;
          const riskStyle = r.horizon === "short" ? "short" : r.horizon === "long" ? "long" : "mid";
          const advice = computeStopAdvice(r.symbol, currentPrice, currentPrice, bars, { style: riskStyle as "short" | "mid" | "long" });
          const rec = advice.recommended;
          stopAdviceMap.set(r.symbol, {
            stopLoss: `¥${rec.stopLoss}`,
            takeProfit: `¥${rec.takeProfit}`,
            stopLossPct: `${(rec.stopLossPct * 100).toFixed(1)}%`,
            takeProfitPct: `${(rec.takeProfitPct * 100).toFixed(1)}%`,
            rewardRiskRatio: `${rec.rewardRiskRatio}:1`,
            method: rec.method,
            description: rec.description,
          });
        } catch {
          // Non-fatal
        }
      }),
    );
  }

  return {
    ok: true,
    mode: "predictive",
    pipeline: `volume_price(${screenResult.universe}) → chip_accumulation → institutional_flow → predictive_rank`,
    screening: {
      strategy: "volume_price",
      universe: screenResult.universe,
      totalScreened: screenResult.totalScreened,
      candidateCount: screenResult.codes.length,
      fallbackNote: screenResult.fallbackNote,
    },
    enrichment: {
      chipAccumulation: chipResult.ok ? `已分析${chipMap.size}只` : "引擎不可用",
      institutionalFlow: `已分析${instMap.size}只`,
      sectorRanking: sectorResult.ok
        ? `热门板块: ${topSectorNames.slice(0, 5).join("、")}`
        : "引擎不可用",
    },
    recommendations: recommended.map((r, i) => ({
      rank: i + 1,
      symbol: r.symbol,
      name: r.name,
      predictiveScore: Math.round(r.predictiveScore * 10) / 10,
      horizon: r.horizon,
      horizonLabel: horizonLabels[r.horizon],
      horizonReason: r.horizonReason,
      signals: r.signals,
      volumeRatio: r.volumeRatio ? `${r.volumeRatio.toFixed(1)}x` : undefined,
      chipAccumulation: r.chip,
      institutionalFlow: r.institutional,
      stopAdvice: stopAdviceMap.get(r.symbol) ?? null,
      scoringBreakdown: {
        主力吸筹: `${r.scoring.chip.toFixed(1)} (30%)`,
        量价信号: `${r.scoring.screen.toFixed(1)} (20%)`,
        机构动向: `${r.scoring.institutional.toFixed(1)} (20%)`,
        底背离: `${r.scoring.divergence.toFixed(1)} (15%)`,
        底部反转: `${r.scoring.bottomReversal.toFixed(1)} (15%)`,
      },
    })),
    horizonDistribution: {
      short: enriched.filter((c) => c.horizon === "short").length,
      mid: enriched.filter((c) => c.horizon === "mid").length,
      long: enriched.filter((c) => c.horizon === "long").length,
    },
    note:
      recommended.length < count
        ? `满足${horizon === "all" ? "全部" : horizonLabels[horizon]}条件的候选仅${recommended.length}只。`
        : `从${screenResult.codes.length}只量价异动股中，综合主力吸筹+机构行为+底部信号，筛选出${recommended.length}只前瞻推荐。`,
    disclaimer:
      "前瞻预测基于多维度统计模型，不构成投资建议。主力行为和市场环境随时变化，请结合自身判断。",
  };
}

// ── Screening with progressive fallback ──────────────────────────

interface ScreenOutput {
  ok: boolean;
  codes: string[];
  stocks: ScreenResult[];
  universe: string;
  totalScreened?: number;
  error?: string;
  fallbackNote?: string;
}

async function runScreenWithFallback(
  screenerId: string,
  requestedUniverse: string,
  market: string,
  limit: number,
  config: QuantTradingConfig,
): Promise<ScreenOutput> {
  // Build the progressive fallback chain
  const universeTiers: string[] = [requestedUniverse];
  if (requestedUniverse === "all" && !universeTiers.includes("zz1000"))
    universeTiers.push("zz1000");
  if (
    (requestedUniverse === "all" || requestedUniverse === "zz1000") &&
    !universeTiers.includes("zz500")
  )
    universeTiers.push("zz500");
  if (!universeTiers.includes("hs300")) universeTiers.push("hs300");

  // Per-tier timeout budgets: smaller universes need less time.
  // Prevents slow API days from stalling for 4 × 300s = 20 min.
  // Note: full-market (all) needs more time for financial enrichment of 5000+ stocks,
  // especially when Xueqiu is rate-limited and falls back to slow financial provider.
  const tierTimeouts: Record<string, number> = {
    all: 360_000,
    zz1000: 240_000,
    zz500: 180_000,
    hs300: 120_000,
  };

  let lastError = "";
  const totalBudgetMs = 480_000; // 8 min total budget across all tiers
  const chainStart = Date.now();

  for (const universe of universeTiers) {
    const elapsed = Date.now() - chainStart;
    const remaining = totalBudgetMs - elapsed;
    if (remaining < 30_000) {
      // Less than 30s left — skip to last tier (hs300) which is fastest
      if (universe !== universeTiers[universeTiers.length - 1]) continue;
    }
    const tierBudget = tierTimeouts[universe] ?? 180_000;
    const timeoutMs = Math.min(tierBudget, remaining);
    const isLastTier = universe === universeTiers[universeTiers.length - 1];
    const result = await runPythonScript<{
      results?: Array<Record<string, unknown>>;
      count?: number;
      totalScreened?: number;
      error?: string;
    }>(
      "engines/screener_engine.py",
      {
        action: "run_screener",
        screenerId,
        market,
        codes: [],
        universe,
        limit,
        params: {},
        includeKlines:
          screenerId === "technical" || screenerId === "volume_price" || screenerId === "overnight",
        klineDays: 120,
      },
      config,
      // Skip retry on non-last tiers — fallback to smaller universe instead
      { timeoutMs, maxRetries: isLastTier ? 1 : 0 },
    );

    if (result.ok && result.data?.results && (result.data.results as unknown[]).length > 0) {
      const stocks: ScreenResult[] = (result.data.results as Array<Record<string, unknown>>).map(
        (r) => ({
          code: String(r.code ?? r.stock_id ?? ""),
          name: r.name ? String(r.name) : undefined,
          score: typeof r.score === "number" ? r.score : undefined,
          factors:
            r.factors && typeof r.factors === "object"
              ? (r.factors as Record<string, unknown>)
              : undefined,
        }),
      );
      const codes = stocks.map((s) => s.code).filter(Boolean);
      return {
        ok: true,
        codes,
        stocks,
        universe,
        totalScreened: (result.data.totalScreened as number) ?? codes.length,
        fallbackNote:
          universe !== requestedUniverse
            ? `原始范围(${requestedUniverse})超时，已自动缩小到${universe}完成筛选。`
            : undefined,
      };
    }

    lastError = result.error ?? "无结果";
    // Continue to next smaller universe
  }

  return {
    ok: false,
    codes: [],
    stocks: [],
    universe: requestedUniverse,
    error: `所有选股范围均失败: ${lastError}`,
  };
}

// ── Scoring & Warnings ───────────────────────────────────────────

function compositeScore(bt: BacktestMetrics): number {
  let score = 0;
  score += bt.totalReturn * 1.0;
  score += bt.sharpeRatio * 10;
  score += bt.winRate * 0.3;
  score -= Math.abs(bt.maxDrawdown) * 0.5;
  score += Math.min(bt.totalTrades, 50) * 0.1;
  // Penalize very few trades — unreliable statistics
  if (bt.totalTrades < 10) {
    score *= bt.totalTrades / 10; // e.g., 3 trades → 30% of score
  }
  return score;
}

function generateWarnings(bt: BacktestMetrics): string[] {
  const warnings: string[] = [];
  if (bt.totalTrades < 10) {
    warnings.push(`交易次数仅${bt.totalTrades}次，统计可靠性不足，建议延长回测周期或更换策略验证`);
  }
  if (bt.winRate >= 100 && bt.totalTrades < 20) {
    warnings.push(`100%胜率 + ${bt.totalTrades}次交易，极可能过拟合，实盘表现可能大幅偏离`);
  }
  if (bt.winRate >= 80 && bt.totalTrades < 10) {
    warnings.push(
      `高胜率(${bt.winRate.toFixed(0)}%) + 少量交易(${bt.totalTrades}次)，统计失真风险高`,
    );
  }
  if (Math.abs(bt.sharpeRatio) > 3 && Math.abs(bt.maxDrawdown) > 30) {
    warnings.push("夏普比率与回撤矛盾，可能统计失真");
  }
  if (bt.totalReturn < 0) {
    warnings.push(`回测收益为负(${bt.totalReturn.toFixed(1)}%)，需谨慎`);
  }
  if (bt.totalReturn > 0 && bt.totalReturn < 5 && Math.abs(bt.maxDrawdown) > 30) {
    warnings.push("风险收益比不佳");
  }
  return warnings;
}

function getOneYearAgo(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}
