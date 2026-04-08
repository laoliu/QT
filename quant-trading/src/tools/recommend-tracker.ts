/**
 * recommend_tracker tool (RE-8) — 推荐追踪与复盘
 *
 * Actions:
 *   - list       — list tracked recommendations (with filters)
 *   - stats      — aggregate win-rate / return statistics
 *   - detail     — single recommendation detail with price update
 *   - refresh    — batch-refresh current prices & resolve expired ones
 *   - review     — review a specific batch of recommendations
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { MarketDataProvider } from "../data/market-data-provider.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { RecommendationTracker, TrackerQuery } from "../services/recommendation-tracker.js";

export function createRecommendTrackerTool(
  api: OpenClawPluginApi,
  tracker: RecommendationTracker,
  getDataProvider?: () => MarketDataProvider,
) {
  return {
    name: "recommend_tracker",
    description:
      "【推荐追踪与复盘】跟踪 stock_recommend 历史推荐标的的后续走势，统计推荐胜率。\n" +
      "功能:\n" +
      "• list — 查看追踪中的推荐记录（可按状态/模式/周期/标的筛选）\n" +
      "• stats — 统计推荐胜率、平均收益、最佳/最差推荐、按模式/周期/选股器分组统计\n" +
      "• detail — 查看单条推荐的详细追踪信息\n" +
      "• refresh — 刷新所有活跃推荐的当前价格，自动结算到期/止盈止损的推荐\n" +
      "• review — 复盘某批次推荐的整体表现",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "stats", "detail", "refresh", "review"],
          description:
            "操作: list=查看推荐列表, stats=胜率统计, detail=单条详情, refresh=刷新价格, review=批次复盘",
        },
        status: {
          type: "string",
          enum: ["active", "win", "loss", "expired", "stopped"],
          description: "按状态筛选(list/stats)",
        },
        mode: {
          type: "string",
          enum: ["classic", "predictive"],
          description: "按推荐模式筛选",
        },
        horizon: {
          type: "string",
          enum: ["short", "mid", "long"],
          description: "按持有周期筛选",
        },
        symbol: {
          type: "string",
          description: "按标的代码筛选",
        },
        screenerId: {
          type: "string",
          description: "按选股器筛选",
        },
        id: {
          type: "string",
          description: "推荐记录ID (detail操作需要)",
        },
        batchId: {
          type: "string",
          description: "批次ID (review操作需要)",
        },
        limit: {
          type: "number",
          description: "返回数量限制, 默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "list":
          return handleList(tracker, args);
        case "stats":
          return handleStats(tracker, args);
        case "detail":
          return handleDetail(tracker, args);
        case "refresh":
          return handleRefresh(tracker, getDataProvider);
        case "review":
          return handleReview(tracker, args);
        default:
          return { ok: false, error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Action handlers ──────────────────────────────────────────────

function buildQuery(args: Record<string, unknown>): TrackerQuery {
  return {
    status: args.status as TrackerQuery["status"],
    mode: args.mode as string | undefined,
    horizon: args.horizon as string | undefined,
    symbol: args.symbol as string | undefined,
    screenerId: args.screenerId as string | undefined,
    limit: (args.limit as number) ?? 20,
  };
}

function handleList(
  tracker: RecommendationTracker,
  args: Record<string, unknown>,
) {
  const q = buildQuery(args);
  const records = tracker.query(q);

  return {
    ok: true,
    action: "list",
    total: tracker.size,
    returned: records.length,
    recommendations: records.map((r) => ({
      id: r.id,
      symbol: r.symbol,
      name: r.name,
      date: r.date,
      mode: r.mode,
      screenerId: r.screenerId,
      horizon: r.horizon,
      horizonExpiry: r.horizonExpiry,
      priceAtRecommend: `¥${r.priceAtRecommend.toFixed(2)}`,
      currentPrice: r.currentPrice ? `¥${r.currentPrice.toFixed(2)}` : "未更新",
      returnPct: r.returnPct != null ? `${r.returnPct.toFixed(1)}%` : "-",
      status: r.status,
      resolution: r.resolution,
    })),
  };
}

function handleStats(
  tracker: RecommendationTracker,
  args: Record<string, unknown>,
) {
  const q = buildQuery(args);
  const stats = tracker.computeStats(q);

  return {
    ok: true,
    action: "stats",
    summary: {
      总推荐数: stats.total,
      已结算: stats.resolved,
      活跃追踪: stats.active,
      盈利次数: stats.wins,
      亏损次数: stats.losses,
      胜率: `${stats.winRate}%`,
      平均收益: `${stats.avgReturn}%`,
      中位收益: `${stats.medianReturn}%`,
      最佳收益: `${stats.bestReturn}%`,
      最差收益: `${stats.worstReturn}%`,
      平均持有天数: stats.avgHoldingDays,
    },
    byMode: stats.byMode,
    byHorizon: stats.byHorizon,
    byScreener: stats.byScreener,
    topPicks: stats.topPicks,
    worstPicks: stats.worstPicks,
  };
}

function handleDetail(
  tracker: RecommendationTracker,
  args: Record<string, unknown>,
) {
  const id = args.id as string;
  if (!id) return { ok: false, error: "请提供推荐记录ID (id参数)" };

  const rec = tracker.get(id);
  if (!rec) return { ok: false, error: `未找到推荐记录: ${id}` };

  const holdingDays = Math.round(
    (Date.now() - new Date(rec.date).getTime()) / (24 * 60 * 60 * 1000),
  );

  return {
    ok: true,
    action: "detail",
    recommendation: {
      id: rec.id,
      symbol: rec.symbol,
      name: rec.name,
      推荐日期: rec.date,
      推荐模式: rec.mode,
      选股策略: rec.screenerId,
      推荐排名: rec.rank,
      综合评分: rec.score,
      推荐价格: `¥${rec.priceAtRecommend.toFixed(2)}`,
      当前价格: rec.currentPrice ? `¥${rec.currentPrice.toFixed(2)}` : "未更新",
      收益率: rec.returnPct != null ? `${rec.returnPct.toFixed(1)}%` : "-",
      最高价: rec.maxPrice ? `¥${rec.maxPrice.toFixed(2)}` : "-",
      最低价: rec.minPrice != null && rec.minPrice !== Infinity ? `¥${rec.minPrice.toFixed(2)}` : "-",
      止损价: rec.stopLoss ? `¥${rec.stopLoss.toFixed(2)}` : "未设置",
      止盈价: rec.takeProfit ? `¥${rec.takeProfit.toFixed(2)}` : "未设置",
      持有周期: rec.horizon,
      到期日: rec.horizonExpiry,
      已持有天数: holdingDays,
      状态: rec.status,
      结算原因: rec.resolution ?? "-",
      回测指标: rec.backtestMetrics ?? null,
      策略: rec.strategy ?? "-",
      最后更新: rec.lastCheckedAt ?? "-",
    },
  };
}

async function handleRefresh(
  tracker: RecommendationTracker,
  getDataProvider?: () => MarketDataProvider,
) {
  if (!getDataProvider) {
    return { ok: false, error: "数据源不可用，无法刷新价格" };
  }

  const provider = getDataProvider();
  const quoteFn = async (symbol: string) => {
    const market = detectMarketFromSymbol(symbol);
    const quote = await provider.getQuote(symbol, market);
    if (!quote?.price) return null;
    return { price: quote.price };
  };

  const result = await tracker.updatePrices(quoteFn);

  return {
    ok: true,
    action: "refresh",
    updated: result.updated,
    resolved: result.resolved,
    message: `已刷新 ${result.updated} 条推荐价格，${result.resolved} 条已结算`,
  };
}

function handleReview(
  tracker: RecommendationTracker,
  args: Record<string, unknown>,
) {
  const batchId = args.batchId as string;
  if (!batchId) {
    // Show recent batches
    const all = tracker.query({ limit: 99999 });
    const batches = new Map<string, { date: string; count: number; mode: string }>();
    for (const r of all) {
      if (!batches.has(r.batchId)) {
        batches.set(r.batchId, { date: r.date, count: 0, mode: r.mode });
      }
      batches.get(r.batchId)!.count++;
    }
    const batchList = [...batches.entries()]
      .map(([id, info]) => ({ batchId: id, ...info }))
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, 10);

    return {
      ok: true,
      action: "review",
      message: "请提供 batchId 参数来复盘某批次推荐。以下是最近的推荐批次:",
      batches: batchList,
    };
  }

  const batchRecords = tracker
    .query({ limit: 99999 })
    .filter((r) => r.batchId === batchId);

  if (batchRecords.length === 0) {
    return { ok: false, error: `未找到批次: ${batchId}` };
  }

  const resolved = batchRecords.filter((r) => r.status !== "active");
  const wins = resolved.filter((r) => r.status === "win");
  const returns = resolved.map((r) => r.returnPct ?? 0);
  const avgReturn = returns.length > 0
    ? returns.reduce((s, v) => s + v, 0) / returns.length
    : 0;

  return {
    ok: true,
    action: "review",
    batchId,
    推荐日期: batchRecords[0].date,
    推荐模式: batchRecords[0].mode,
    选股策略: batchRecords[0].screenerId,
    总推荐数: batchRecords.length,
    已结算: resolved.length,
    胜率: resolved.length > 0
      ? `${Math.round((wins.length / resolved.length) * 1000) / 10}%`
      : "-",
    平均收益: returns.length > 0 ? `${avgReturn.toFixed(1)}%` : "-",
    recommendations: batchRecords.map((r) => ({
      symbol: r.symbol,
      name: r.name,
      rank: r.rank,
      priceAtRecommend: `¥${r.priceAtRecommend.toFixed(2)}`,
      currentPrice: r.currentPrice ? `¥${r.currentPrice.toFixed(2)}` : "未更新",
      returnPct: r.returnPct != null ? `${r.returnPct.toFixed(1)}%` : "-",
      status: r.status,
      resolution: r.resolution ?? "-",
    })),
  };
}
