/**
 * strategy_router tool — AI-callable tool for stock profiling and
 * adaptive strategy routing.
 *
 * Actions:
 *   - profile:               Profile a stock's characteristics
 *   - recommend:             Get strategy recommendation based on profile
 *   - recommend_with_regime: Recommend with macro regime adjustment
 *   - route_portfolio:       Route portfolio of stocks to optimal combos
 *   - profile_route_backtest: Full pipeline: profile → route → composed backtest
 *   - routing_table:         View current routing table
 *   - build_table:           Build routing table from batch backtesting
 *   - enrich_with_routing:   Add routing info to stock candidates
 *   - get_regime:            Get current macro economic regime
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { StockProfile, StrategyRecommendation } from "../engine/dynamic-composer.js";
import type { QuantTradingConfig } from "../types.js";

// ── Result Types ─────────────────────────────────────────────────

interface ProfileResult {
  ok: boolean;
  error?: string;
  profiles?: StockProfile[];
}

interface RecommendResult {
  ok: boolean;
  error?: string;
  recommendations?: StrategyRecommendation[];
}

interface RoutingTableResult {
  ok: boolean;
  error?: string;
  table?: Record<
    string,
    {
      combo: { name: string; method: string; strategies: unknown[] };
      score: number;
      avgReturn: number;
      avgSharpe: number;
      avgMaxDD: number;
      avgWinRate: number;
      positiveRate: string;
      stockCount: number;
    }
  >;
  totalProfiles?: number;
}

interface BuildTableResult {
  ok: boolean;
  error?: string;
  totalStocks?: number;
  profileGroups?: number;
  routingEntries?: number;
  elapsed?: number;
}

interface ProfileRouteBacktestItem {
  symbol: string;
  name?: string;
  ok: boolean;
  profileKey?: string;
  combo?: { name: string; method: string; strategies: string[] };
  routingSource?: string;
  reason?: string;
  backtest?: {
    totalReturn: number;
    sharpeRatio: number;
    maxDrawdown: number;
    winRate: number;
    totalTrades: number;
  } | null;
  error?: string | null;
}

interface ProfileRouteBacktestResult {
  ok: boolean;
  error?: string;
  results?: ProfileRouteBacktestItem[];
  total?: number;
}

interface EnrichResult {
  ok: boolean;
  error?: string;
  enriched?: Array<Record<string, unknown>>;
}

interface RegimeResult {
  ok: boolean;
  error?: string;
  regime?: string;
  description?: string;
  signals?: Record<string, unknown>;
}

// ── Tool Factory ─────────────────────────────────────────────────

export function createStrategyRouterTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "strategy_router",
    description:
      "股票画像与策略自适应路由。" +
      "action=profile 分析股票特征画像。" +
      "action=recommend 基于画像推荐最优策略组合。" +
      "action=recommend_with_regime 推荐并叠加宏观周期调整。" +
      "action=route_portfolio 批量路由多只股票。" +
      "action=profile_route_backtest 完整流水线: 画像→路由→组合回测验证。" +
      "action=routing_table 查看路由表。" +
      "action=build_table 构建路由表。" +
      "action=enrich_with_routing 为候选股添加路由信息。" +
      "action=get_regime 获取当前宏观经济周期。\n" +
      "【重要】symbols 参数是必填的（routing_table/build_table/get_regime 除外）。" +
      "不能与 stock_screen 同时并行调用——必须先等 stock_screen 返回结果，" +
      "再将返回的股票代码传入 symbols 参数。\n" +
      "【推荐工作流】当用户要求'找股票/推荐股票'时，应优先使用 profile_route_backtest，" +
      "它会自动为每只股票匹配最优策略并运行回测验证，而不是用单一策略(如dual-ma-crossover)对所有股票回测。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "profile",
            "recommend",
            "recommend_with_regime",
            "route_portfolio",
            "profile_route_backtest",
            "routing_table",
            "build_table",
            "enrich_with_routing",
            "get_regime",
          ],
          description:
            "操作: profile=画像, recommend=推荐, recommend_with_regime=宏观调整推荐, " +
            "route_portfolio=组合路由, profile_route_backtest=画像→路由→回测, " +
            "routing_table=查看路由表, build_table=构建路由表, enrich_with_routing=候选股路由标注, get_regime=宏观周期",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔 (如 600519,000858,300750)",
        },
        endDate: {
          type: "string",
          description: "画像截止日期 YYYY-MM-DD (默认最新)",
        },
        startDate: {
          type: "string",
          description: "回测起始日期 (build_table 时使用，默认一年前)",
        },
        maxStocks: {
          type: "number",
          description: "build_table: 每个分类最多采样股票数 (默认 10)",
        },
        minBars: {
          type: "number",
          description: "最少 K 线条数 (默认 120)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "profile") {
        return await runProfile(args, config);
      }
      if (action === "recommend") {
        return await runRecommend(args, config);
      }
      if (action === "recommend_with_regime") {
        return await runRecommendWithRegime(args, config);
      }
      if (action === "route_portfolio") {
        return await runRoutePortfolio(args, config);
      }
      if (action === "profile_route_backtest") {
        return await runProfileRouteBacktest(args, config);
      }
      if (action === "routing_table") {
        return await runGetRoutingTable(config);
      }
      if (action === "build_table") {
        return await runBuildTable(args, config);
      }
      if (action === "enrich_with_routing") {
        return await runEnrichWithRouting(args, config);
      }
      if (action === "get_regime") {
        return await runGetRegime(config);
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

// ── Profile Handler ──────────────────────────────────────────────

async function runProfile(args: Record<string, unknown>, config: QuantTradingConfig) {
  const symbols = parseSymbols(args.symbols);
  if (!symbols.length)
    return {
      error:
        "请提供标的代码 (symbols)。如果刚调用过 stock_screen，请将返回的股票代码传入 symbols 参数；或直接使用 stock_recommend 工具一步完成选股+回测。",
    };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);

  try {
    const result = await runPythonScript<ProfileResult>(
      "engines/stock_profiler.py",
      { action: "profile", symbols, endDate },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `画像分析失败: ${result.error ?? "未知错误"}` };
    }

    const profiles = result.data.profiles ?? [];
    return {
      ok: true,
      total: profiles.length,
      profiles: profiles.map(formatProfile),
    };
  } catch (err) {
    return { error: `画像分析失败: ${(err as Error).message}` };
  }
}

// ── Recommend Handler ────────────────────────────────────────────

async function runRecommend(args: Record<string, unknown>, config: QuantTradingConfig) {
  const symbols = parseSymbols(args.symbols);
  if (!symbols.length)
    return {
      error:
        "请提供标的代码 (symbols)。如果刚调用过 stock_screen，请将返回的股票代码传入 symbols 参数；或直接使用 stock_recommend 工具一步完成选股+回测。",
    };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);

  try {
    const result = await runPythonScript<RecommendResult>(
      "engines/strategy_router.py",
      { action: "recommend", symbols, endDate },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `策略推荐失败: ${result.error ?? "未知错误"}` };
    }

    const recs = result.data.recommendations ?? [];
    return {
      ok: true,
      total: recs.length,
      recommendations: recs.map(formatRecommendation),
    };
  } catch (err) {
    return { error: `策略推荐失败: ${(err as Error).message}` };
  }
}

// ── Route Portfolio Handler ──────────────────────────────────────

async function runRoutePortfolio(args: Record<string, unknown>, config: QuantTradingConfig) {
  const symbols = parseSymbols(args.symbols);
  if (!symbols.length)
    return {
      error:
        "请提供标的代码 (symbols)。如果刚调用过 stock_screen，请将返回的股票代码传入 symbols 参数；或直接使用 stock_recommend 工具一步完成选股+回测。",
    };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);

  try {
    const result = await runPythonScript<RecommendResult>(
      "engines/strategy_router.py",
      { action: "route_portfolio", symbols, endDate },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `组合路由失败: ${result.error ?? "未知错误"}` };
    }

    const recs = result.data.recommendations ?? [];

    // Group by strategy combo for summary
    const comboGroups: Record<string, string[]> = {};
    for (const r of recs) {
      if (r.ok && r.combo) {
        const key = r.combo.name;
        if (!comboGroups[key]) comboGroups[key] = [];
        comboGroups[key].push(r.symbol);
      }
    }

    return {
      ok: true,
      total: recs.length,
      routingSummary: Object.entries(comboGroups).map(([combo, syms]) => ({
        combo,
        stockCount: syms.length,
        stocks: syms,
      })),
      details: recs.map(formatRecommendation),
    };
  } catch (err) {
    return { error: `组合路由失败: ${(err as Error).message}` };
  }
}

// ── Get Routing Table Handler ────────────────────────────────────

async function runGetRoutingTable(config: QuantTradingConfig) {
  try {
    const result = await runPythonScript<RoutingTableResult>(
      "engines/strategy_router.py",
      { action: "get_routing_table" },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `获取路由表失败: ${result.error ?? "未知错误"}` };
    }

    const data = result.data;
    if (!data.table || Object.keys(data.table).length === 0) {
      return {
        ok: true,
        message: "路由表为空。使用 action=build_table 构建数据驱动路由表。",
        hint: "当前使用基于规则的默认路由。",
      };
    }

    return {
      ok: true,
      totalProfiles: data.totalProfiles,
      table: data.table,
    };
  } catch (err) {
    return { error: `获取路由表失败: ${(err as Error).message}` };
  }
}

// ── Build Table Handler ──────────────────────────────────────────

async function runBuildTable(args: Record<string, unknown>, config: QuantTradingConfig) {
  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
  const startDate = (args.startDate as string) ?? getDefaultStartDate();
  const maxStocks = (args.maxStocks as number) ?? 10;
  const minBars = (args.minBars as number) ?? 120;

  try {
    const result = await runPythonScript<BuildTableResult>(
      "engines/build_routing_table.py",
      { action: "build", endDate, startDate, maxStocks, minBars },
      config,
      { timeoutMs: 300_000 }, // 5 min — heavy batch backtest
    );

    if (!result.ok || !result.data) {
      return { error: `构建路由表失败: ${result.error ?? "未知错误"}` };
    }

    const data = result.data;
    return {
      ok: true,
      message: "路由表构建完成",
      totalStocks: data.totalStocks,
      profileGroups: data.profileGroups,
      routingEntries: data.routingEntries,
      elapsed: data.elapsed ? `${data.elapsed.toFixed(1)}s` : undefined,
    };
  } catch (err) {
    return { error: `构建路由表失败: ${(err as Error).message}` };
  }
}

// ── Recommend with Regime Handler ────────────────────────────────

async function runRecommendWithRegime(args: Record<string, unknown>, config: QuantTradingConfig) {
  const symbols = parseSymbols(args.symbols);
  if (!symbols.length)
    return {
      error:
        "请提供标的代码 (symbols)。如果刚调用过 stock_screen，请将返回的股票代码传入 symbols 参数；或直接使用 stock_recommend 工具一步完成选股+回测。",
    };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);

  try {
    const result = await runPythonScript<RecommendResult>(
      "engines/strategy_router.py",
      { action: "recommend_with_regime", symbols, endDate },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `宏观调整推荐失败: ${result.error ?? "未知错误"}` };
    }

    const recs = result.data.recommendations ?? [];
    return {
      ok: true,
      total: recs.length,
      recommendations: recs.map(formatRecommendation),
    };
  } catch (err) {
    return { error: `宏观调整推荐失败: ${(err as Error).message}` };
  }
}

// ── Profile → Route → Backtest Handler ───────────────────────────

async function runProfileRouteBacktest(args: Record<string, unknown>, config: QuantTradingConfig) {
  const symbols = parseSymbols(args.symbols);
  if (!symbols.length)
    return {
      error:
        "请提供标的代码 (symbols)。如果刚调用过 stock_screen，请将返回的股票代码传入 symbols 参数；或直接使用 stock_recommend 工具一步完成选股+回测。",
    };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
  const startDate = (args.startDate as string) ?? getDefaultStartDate();

  try {
    const result = await runPythonScript<ProfileRouteBacktestResult>(
      "engines/strategy_router.py",
      { action: "profile_route_backtest", symbols, startDate, endDate },
      config,
      { timeoutMs: 300_000 }, // may take a while for many symbols
    );

    if (!result.ok || !result.data) {
      return { error: `画像路由回测失败: ${result.error ?? "未知错误"}` };
    }

    const items = result.data.results ?? [];
    return {
      ok: true,
      total: result.data.total,
      results: items.map((item) => ({
        symbol: item.symbol,
        name: item.name,
        ok: item.ok,
        profileKey: item.profileKey,
        combo: item.combo,
        routingSource: item.routingSource,
        reason: item.reason,
        backtest: item.backtest
          ? {
              totalReturn: `${item.backtest.totalReturn.toFixed(1)}%`,
              sharpeRatio: item.backtest.sharpeRatio.toFixed(2),
              maxDrawdown: `${item.backtest.maxDrawdown.toFixed(1)}%`,
              winRate: `${item.backtest.winRate.toFixed(1)}%`,
              totalTrades: item.backtest.totalTrades,
            }
          : null,
        error: item.error,
      })),
    };
  } catch (err) {
    return { error: `画像路由回测失败: ${(err as Error).message}` };
  }
}

// ── Enrich with Routing Handler ──────────────────────────────────

async function runEnrichWithRouting(args: Record<string, unknown>, config: QuantTradingConfig) {
  const candidates = args.candidates as Array<Record<string, unknown>> | undefined;
  if (!candidates?.length) return { error: "请提供候选股列表 (candidates)" };

  const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);

  try {
    const result = await runPythonScript<EnrichResult>(
      "engines/strategy_router.py",
      { action: "enrich_with_routing", candidates, endDate },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `路由标注失败: ${result.error ?? "未知错误"}` };
    }

    return { ok: true, enriched: result.data.enriched ?? [] };
  } catch (err) {
    return { error: `路由标注失败: ${(err as Error).message}` };
  }
}

// ── Get Regime Handler ───────────────────────────────────────────

async function runGetRegime(config: QuantTradingConfig) {
  try {
    const result = await runPythonScript<RegimeResult>(
      "engines/strategy_router.py",
      { action: "get_regime" },
      config,
    );

    if (!result.ok || !result.data) {
      return { error: `获取宏观周期失败: ${result.error ?? "未知错误"}` };
    }

    return {
      ok: true,
      regime: result.data.regime,
      description: result.data.description,
      signals: result.data.signals,
    };
  } catch (err) {
    return { error: `获取宏观周期失败: ${(err as Error).message}` };
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function parseSymbols(raw: unknown): string[] {
  if (!raw) return [];
  return String(raw)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function getDefaultStartDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

function formatProfile(p: StockProfile) {
  return {
    symbol: p.symbol,
    name: p.name,
    ok: p.ok,
    error: p.error,
    profileKey: p.profileKey,
    volatility: p.volatility
      ? `${p.volatility.class} (年化 ${(p.volatility.annualized * 100).toFixed(1)}%)`
      : undefined,
    trend: p.trend?.class,
    marketCap: p.marketCap
      ? `${p.marketCap.class} (${p.marketCap.value_yi.toFixed(0)}亿)`
      : undefined,
    liquidity: p.liquidity?.class,
    momentum: p.momentum
      ? `${p.momentum.class} (${(p.momentum.return20d * 100).toFixed(1)}%)`
      : undefined,
    volumeTrend: p.volumeTrend?.class,
    sector: p.sector,
  };
}

function formatRecommendation(r: StrategyRecommendation) {
  return {
    symbol: r.symbol,
    ok: r.ok,
    error: r.error,
    profileKey: r.profile?.profileKey,
    combo: r.combo
      ? {
          name: r.combo.name,
          method: r.combo.method,
          strategies: r.combo.strategies.map((s) => s.strategyId),
          minVotes: r.combo.minVotes,
        }
      : undefined,
    reason: r.reason,
    source: r.source,
    performance: r.performance
      ? {
          avgReturn: `${r.performance.avgReturn.toFixed(1)}%`,
          avgSharpe: r.performance.avgSharpe.toFixed(2),
          avgMaxDD: `${r.performance.avgMaxDD.toFixed(1)}%`,
          avgWinRate: `${r.performance.avgWinRate.toFixed(1)}%`,
          positiveRate: r.performance.positiveRate,
        }
      : undefined,
  };
}
