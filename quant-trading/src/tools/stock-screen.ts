/**
 * stock_screen tool — AI-callable for screening stocks by criteria.
 *
 * Delegates to the Python factor_filter engine (migrated from jtstock)
 * for multi-factor screening, ranking, RPS, and industry filtering.
 *
 * Also supports advanced screener strategies via screener_engine:
 *   canslim, fscore, magic_formula, technical, volume_price, peg
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { ScanHistoryStore, ScanStockEntry } from "../services/scan-history-store.js";
import type { QuantTradingConfig, ScreenResult } from "../types.js";

export function createStockScreenTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  scanHistoryStore?: ScanHistoryStore,
) {
  return {
    name: "stock_screen",
    description:
      "股票筛选工具。支持9种action：\n" +
      "• action=run_screener — 策略选股（F-Score/CAN SLIM/魔法公式等），需指定screenerId。" +
      "可指定universe(如hs300/zz500)进行大范围筛选，或codes指定个股。" +
      "例: action=run_screener, screenerId=fscore, universe=hs300\n" +
      "• action=list_screeners — 列出可用策略\n" +
      "• action=screen — 因子筛选(PE/PB/ROE等)，Python引擎不可用时的可靠替代\n" +
      "• action=rank — 因子排名\n" +
      "• action=rps — 相对价格强度\n" +
      "• action=peer_compare — 同行对比（指定一只股票，自动获取同行业公司进行多维度对比）\n" +
      "• action=scan_history — 查询历史筛选记录（支持按日期/策略筛选）\n" +
      "• action=scan_compare — 对比两次筛选结果（查看新增/移除/评分变化）。不传scanId/compareId则自动对比最近两次同策略结果\n" +
      "• action=stock_frequency — 查看股票在多次筛选中出现的频率（高频出现=持续推荐）\n" +
      "重要：F-Score/Piotroski打分 请用 action=run_screener + screenerId=fscore，" +
      "数据已内置(akshare/yfinance)，不需要外部搜索API。\n" +
      "【选股→回测工作流】选股完成后，应将结果中的股票代码传给 backtest 或 strategy_router(action=profile_route_backtest) 进行回测验证。" +
      "如果 run_screener 超时/失败，会自动尝试缩小范围重试，或建议使用 screen action 替代。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "screen",
            "rank",
            "rps",
            "run_screener",
            "list_screeners",
            "peer_compare",
            "scan_history",
            "scan_compare",
            "stock_frequency",
          ],
          description:
            "操作类型: screen=多因子筛选, rank=因子排名, rps=相对价格强度, " +
            "run_screener=策略选股(需指定screenerId), list_screeners=查看可用策略, " +
            "peer_compare=同行业对比（需指定symbol）, " +
            "scan_history=查询历史筛选记录, scan_compare=对比两次筛选, " +
            "stock_frequency=股票推荐频率（高频=持续推荐）",
        },
        screenerId: {
          type: "string",
          enum: [
            "canslim",
            "fscore",
            "magic_formula",
            "technical",
            "volume_price",
            "peg",
            "overnight",
          ],
          description:
            "策略ID（action=run_screener时必填）：" +
            "fscore=Piotroski F-Score 9项打分（价值质量）, " +
            "canslim=CAN SLIM成长动量, magic_formula=Greenblatt魔法公式, " +
            "technical=技术面指标, volume_price=量价异动, peg=PEG合理成长, " +
            "overnight=一夜持股法（隔夜价差战法，8层过滤选股）",
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock", "hk_stock", "fund"],
          description: "市场类型",
        },
        codes: {
          type: "string",
          description:
            '指定股票代码列表(JSON数组)，例如: ["600519","000858","AAPL"]。' +
            "不指定则使用universe或全市场筛选。",
        },
        universe: {
          type: "string",
          enum: ["hs300", "zz500", "zz1000", "sz50", "cyb", "kc50", "zz100", "all", "watchlist"],
          description:
            "股票池（指数成分股），与codes二选一。" +
            "hs300=沪深300, zz500=中证500, zz1000=中证1000, sz50=上证50, " +
            "cyb=创业板指, kc50=科创50, zz100=中证100, all=全市场, " +
            "watchlist=自选股。用户未指定范围时必须使用 all（全市场），超时会自动降级。",
        },
        criteria: {
          type: "string",
          description:
            "筛选条件，JSON格式数组，例如: " +
            '[{"field":"pe","operator":"lt","value":20},{"field":"roe","operator":"gt","value":15}]',
        },
        factors: {
          type: "string",
          description:
            "因子列表（rank 模式），JSON格式数组，例如: " +
            '["pe","roe","market_cap"]。支持: pe, pb, roe, roa, eps, revenue_growth, ' +
            "total_market_val, price_fluctuate_ratio, stock_rps, history_volatility",
        },
        symbol: {
          type: "string",
          description:
            "目标股票代码（action=peer_compare 时使用），如 '600519'。" +
            "指定后自动查找同行业标的进行多维对比。",
        },
        industry: {
          type: "string",
          description: "行业过滤 (如 '白酒', '半导体')",
        },
        minListingDays: {
          type: "number",
          description: "最小上市天数（排除次新股）",
        },
        rpsPeriod: {
          type: "number",
          description: "RPS 计算周期（天），默认 120",
        },
        sortBy: {
          type: "string",
          description: "排序字段 (pe, pb, roe, market_cap, volume, rps, total_rank)",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
        date: {
          type: "string",
          description: "日期过滤 (YYYY-MM-DD格式)，用于 scan_history/stock_frequency",
        },
        scanId: {
          type: "string",
          description: "筛选记录ID，用于 scan_compare 的第一个比较对象 (baseId)",
        },
        compareId: {
          type: "string",
          description: "筛选记录ID，用于 scan_compare 的第二个比较对象",
        },
        minAppearances: {
          type: "number",
          description: "stock_frequency 最少出现次数门槛（默认2，只显示多次入选的股票）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "screen";
      const market = (args.market as string) ?? "cn_stock";
      const limit = (args.limit as number) ?? 20;

      // Route: list_screeners or run_screener → screener_engine.py
      if (action === "list_screeners" || action === "run_screener") {
        // Parse optional codes
        let codes: string[] = [];
        if (args.codes) {
          try {
            codes = JSON.parse(args.codes as string);
          } catch {
            return { error: "codes 格式错误，请使用 JSON 数组格式" };
          }
        }

        // Resolve watchlist universe → actual codes
        let universe = (args.universe as string) ?? "";
        if (universe === "watchlist" && codes.length === 0) {
          try {
            const fsM = await import("node:fs/promises");
            const osM = await import("node:os");
            const pathM = await import("node:path");
            const dir = config.dataDir ?? pathM.join(osM.homedir(), ".openclaw", "quant-trading");
            const raw = await fsM.readFile(pathM.join(dir, "watchlist.json"), "utf-8");
            const wl = JSON.parse(raw) as string[];
            if (wl.length === 0) {
              return { error: "自选股为空，请先使用 /自选 add <代码> 添加股票" };
            }
            codes = wl;
            universe = ""; // codes override universe
          } catch {
            return { error: "无法读取自选股列表，请先使用 /自选 add <代码> 添加股票" };
          }
        }

        const pyResult = await runPythonScript<{
          screenerId?: string;
          screenerName?: string;
          results?: Array<Record<string, unknown>>;
          count?: number;
          totalScreened?: number;
          screeners?: Array<Record<string, unknown>>;
          errors?: string[];
          error?: string;
        }>(
          "engines/screener_engine.py",
          {
            action,
            screenerId: args.screenerId,
            market,
            codes,
            universe,
            limit,
            params: {},
            includeKlines:
              args.screenerId === "technical" ||
              args.screenerId === "volume_price" ||
              args.screenerId === "overnight",
            klineDays: 120,
          },
          config,
        );

        if (pyResult.ok && pyResult.data) {
          const data = pyResult.data;
          const count = (data.count as number) ?? 0;
          if (count > 5) {
            (data as Record<string, unknown>).displayHint =
              `结果共${count}条，请分多条消息展示（每条最多5只股票），确保所有结果都完整显示，不要省略。`;
          }
          // Guidance for AI: chain screening results into backtest
          if (
            action === "run_screener" &&
            data.results &&
            Array.isArray(data.results) &&
            (data.results as Array<Record<string, unknown>>).length > 0
          ) {
            const topCodes = (data.results as Array<Record<string, unknown>>)
              .slice(0, 10)
              .map((r) => String(r.code ?? r.stock_id ?? ""))
              .filter(Boolean);
            if (topCodes.length > 0) {
              (data as Record<string, unknown>).nextStepHint =
                `选股完成。建议下一步：` +
                `1) 使用 strategy_router (action=profile_route_backtest, symbols=${topCodes.join(",")}) 进行画像→路由→回测验证；` +
                `2) 或使用 backtest (action=run, symbols=${topCodes.join(",")}, endDate=今天) 回测并检查最新信号。` +
                `3) 检查 backtest 输出的 forwardSignals 字段判断明天是否有买入信号。`;
            }
          }

          // Auto-save to scan history (best-effort)
          if (
            scanHistoryStore &&
            action === "run_screener" &&
            data.results &&
            Array.isArray(data.results)
          ) {
            try {
              const entries: ScanStockEntry[] = (
                data.results as Array<Record<string, unknown>>
              ).map((r, i) => ({
                code: String(r.code ?? r.stock_id ?? ""),
                name: r.name ? String(r.name) : undefined,
                score: typeof r.score === "number" ? r.score : undefined,
                rank: i + 1,
                factors:
                  r.factors && typeof r.factors === "object"
                    ? (r.factors as Record<string, unknown>)
                    : undefined,
                signals: Array.isArray(r.signals) ? (r.signals as string[]) : undefined,
              }));
              const savedId = await scanHistoryStore.save(
                String(args.screenerId ?? "unknown"),
                String(args.universe ?? (codes.length > 0 ? "custom" : "all")),
                market,
                (data.totalScreened as number) ?? entries.length,
                entries,
              );
              (data as Record<string, unknown>).savedScanId = savedId;
            } catch {
              // Best-effort — don't fail the screening
            }
          }

          return data;
        }

        // ── Fallback: if run_screener failed (timeout/error), try smaller universe or screen action ──
        if (action === "run_screener") {
          const failReason = pyResult.error ?? "未知错误";
          const isTimeout = failReason.includes("timed out");

          // Strategy 1: retry with progressively smaller universes on timeout
          const originalUniverse = universe || (codes.length === 0 ? "all" : "");
          const retryCandidates: string[] = [];
          if (originalUniverse === "all") retryCandidates.push("zz1000", "zz500", "hs300");
          else if (originalUniverse === "zz1000") retryCandidates.push("zz500", "hs300");
          else if (originalUniverse === "zz500") retryCandidates.push("hs300");

          if (isTimeout && retryCandidates.length > 0) {
            for (const smallerUniverse of retryCandidates) {
              const retryResult = await runPythonScript<typeof pyResult.data>(
                "engines/screener_engine.py",
                {
                  action: "run_screener",
                  screenerId: args.screenerId,
                  market,
                  codes: [],
                  universe: smallerUniverse,
                  limit,
                  params: {},
                  includeKlines:
                    args.screenerId === "technical" ||
                    args.screenerId === "volume_price" ||
                    args.screenerId === "overnight",
                  klineDays: 120,
                },
                config,
              );
              if (retryResult.ok && retryResult.data) {
                const retryData = retryResult.data as Record<string, unknown>;
                retryData.fallbackNote = `原始范围(${originalUniverse})超时，已自动缩小到${smallerUniverse}重新筛选。`;
                return retryData;
              }
              // If this tier also timed out, try next smaller tier
            }
          }

          // Strategy 2: fall back to TS-based screen action with basic criteria
          return {
            action: "run_screener",
            note:
              `策略选股引擎失败(${failReason})。` +
              `建议改用 action=screen 进行基础因子筛选(PE/PB/ROE)，或缩小选股范围(如 universe=hs300/sz50)后重试。`,
            fallbackHint: `可尝试: stock_screen(action=screen, criteria=[{"field":"pe","operator":"lt","value":25},{"field":"roe","operator":"gt","value":15}], universe=hs300)`,
            count: 0,
            results: [],
          };
        }

        return {
          action,
          note: "选股策略引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
          count: 0,
          results: [],
        };
      }

      // ── Peer comparison: fetch fundamentals for target + industry peers ──
      if (action === "peer_compare") {
        const symbol = (args.symbol as string) ?? (args.codes as string);
        if (!symbol) {
          return { error: "请提供标的代码 (symbol 参数指定股票代码，如 '600519')" };
        }

        // Parse symbol — accept JSON array or single code
        let targetCode: string;
        try {
          const parsed = JSON.parse(symbol);
          targetCode = Array.isArray(parsed) ? parsed[0] : symbol;
        } catch {
          targetCode = symbol
            .replace(/[[\]"]/g, "")
            .split(",")[0]
            .trim();
        }

        if (!targetCode) {
          return { error: "请提供一个有效的股票代码" };
        }

        const pyResult = await runPythonScript<{
          target?: Record<string, unknown>;
          peers?: Array<Record<string, unknown>>;
          industry?: string;
          comparison?: Record<string, unknown>;
          rankings?: Record<string, unknown>;
          comparisonTable?: Array<Record<string, unknown>>;
          displayHint?: string;
          error?: string;
          errors?: string[];
        }>("engines/peer_compare.py", { code: targetCode, market, limit }, config);

        if (pyResult.ok && pyResult.data) {
          const data = pyResult.data;
          if (data.error) {
            return { error: data.error, errors: data.errors };
          }
          return data;
        }

        return {
          action: "peer_compare",
          note: "同行对比引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
        };
      }

      // ── Scan History: query past screening runs ──
      if (action === "scan_history") {
        if (!scanHistoryStore) {
          return { error: "筛选历史功能未启用（需配置 dataDir）" };
        }
        const entries = scanHistoryStore.query({
          screenerId: args.screenerId as string | undefined,
          universe: args.universe as string | undefined,
          market: args.market as string | undefined,
          date: args.date as string | undefined,
          limit,
        });
        return {
          action: "scan_history",
          count: entries.length,
          total: scanHistoryStore.size,
          entries: entries.map((e) => ({
            id: e.id,
            date: e.date,
            screenerId: e.screenerId,
            universe: e.universe,
            resultCount: e.resultCount,
            totalScreened: e.totalScreened,
            topCodes: e.topCodes,
          })),
          hint: "使用 action=scan_compare，传入两个记录的 scanId 和 compareId 可以对比两次筛选结果的变化。",
        };
      }

      // ── Scan Compare: diff two screening runs ──
      if (action === "scan_compare") {
        if (!scanHistoryStore) {
          return { error: "筛选历史功能未启用（需配置 dataDir）" };
        }
        let baseId = args.scanId as string | undefined;
        let compareId = args.compareId as string | undefined;

        // Auto-compare: if no IDs provided, use latest two of the same screener
        if (!baseId && !compareId) {
          const pair = scanHistoryStore.getLatestPair(args.screenerId as string | undefined);
          if (!pair) {
            return {
              error: "没有足够的历史筛选记录进行对比（至少需要2次同策略筛选）",
              hint: "先用 action=run_screener 跑至少两次筛选，再使用 scan_compare",
            };
          }
          baseId = pair.baseId;
          compareId = pair.compareId;
        } else if (!baseId || !compareId) {
          return {
            error:
              "请提供 scanId（基准）和 compareId（对比）两个筛选记录ID，或两个都不传则自动对比最近两次",
          };
        }
        const comparison = scanHistoryStore.compare(baseId, compareId);
        if (!comparison) {
          return { error: "未找到指定的筛选记录，请检查ID是否正确" };
        }
        return {
          action: "scan_compare",
          baseDate: comparison.baseDate,
          compareDate: comparison.compareDate,
          screenerId: comparison.screenerId,
          summary: {
            overlapCount: comparison.overlapCount,
            addedCount: comparison.addedCount,
            removedCount: comparison.removedCount,
          },
          overlap: comparison.overlap,
          added: comparison.added.map((s) => ({ code: s.code, name: s.name, score: s.score })),
          removed: comparison.removed.map((s) => ({ code: s.code, name: s.name, score: s.score })),
        };
      }

      // ── Stock Frequency: track which stocks appear most across scans ──
      if (action === "stock_frequency") {
        if (!scanHistoryStore) {
          return { error: "筛选历史功能未启用（需配置 dataDir）" };
        }
        const freq = scanHistoryStore.getStockFrequency({
          screenerId: args.screenerId as string | undefined,
          after: args.date ? `${args.date as string}T00:00:00.000Z` : undefined,
          minAppearances: (args.minAppearances as number) ?? 2,
          limit,
        });
        return {
          action: "stock_frequency",
          count: freq.length,
          description: "以下股票在多次筛选中反复出现，出现频率越高代表持续性越强",
          stocks: freq.map((f) => ({
            code: f.code,
            name: f.name,
            appearances: f.appearances,
            dates: f.dates,
            avgScore: f.avgScore != null ? Number(f.avgScore.toFixed(2)) : undefined,
            latestScore: f.latestScore,
          })),
        };
      }

      // Original flow: screen / rank / rps → factor_filter.py

      // Parse criteria
      let criteria: Array<{ field: string; operator: string; value: unknown }> = [];
      if (args.criteria) {
        try {
          criteria = JSON.parse(args.criteria as string);
        } catch {
          return { error: "criteria 格式错误，请使用 JSON 数组格式" };
        }
      }

      // Parse factors
      let factors: string[] = [];
      if (args.factors) {
        try {
          factors = JSON.parse(args.factors as string);
        } catch {
          return { error: "factors 格式错误，请使用 JSON 数组格式" };
        }
      }

      // Try Python factor_filter engine
      const pyResult = await runPythonScript<{
        results: Array<{
          stock_id: string;
          name?: string;
          exchange?: string;
          total_rank?: number;
          factors?: Record<string, number>;
          rps?: number;
        }>;
        count: number;
      }>(
        "engines/factor_filter.py",
        {
          action,
          market,
          criteria,
          factors,
          industry: args.industry,
          minListingDays: args.minListingDays,
          rpsPeriod: args.rpsPeriod ?? 120,
          sortBy: args.sortBy,
          limit,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return {
          action,
          market,
          criteria,
          count: pyResult.data.count,
          results: pyResult.data.results,
        };
      }

      // Fallback: return a note that Python engine is not available
      return {
        action,
        market,
        criteria,
        note:
          "Python 筛选引擎未就绪，请确保 Python 已安装并配置 pythonPath。" +
          (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
        count: 0,
        results: [],
      };
    },
  };
}
