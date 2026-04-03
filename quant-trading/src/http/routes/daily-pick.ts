import { runPythonScript } from "../../bridge/python-bridge.js";
/**
 * Daily Composite Picks API routes
 *
 * Extracted from admin-routes.ts for maintainability.
 */
import type { AdminRouteDeps } from "../admin-routes.js";
import {
  json,
  clampInteger,
  toFiniteNumber,
  round2,
  formatDateYmd,
  parseObjectBody,
  DAILY_PICK_STRATEGIES,
  DAILY_PICK_STRATEGY_LABELS,
  CTA_SCREEN_STRATEGY_MAP,
} from "./_helpers.js";

export function registerDailyPickRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure, securePublic, composedStrategyStore } = deps;

  // ── Daily Composite Picks API ────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/daily-picks",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }

      interface BacktestSummaryLite {
        totalReturn: number;
        annualizedReturn: number;
        maxDrawdown: number;
        sharpeRatio: number;
        winRate: number;
        profitFactor: number;
        totalTrades: number;
        finalEquity: number;
      }

      interface StrategyScreenCandidate {
        symbol: string;
        name: string;
        price: number;
        changePct: number;
        score: number;
        rank: number;
        reasons: string[];
      }

      interface BacktestDetail {
        equityCurve: Array<{ date: string; equity: number; cash: number; positions: number }>;
        monthlyReturns: Array<{ month: string; return: number; equity: number }>;
        trades: Array<Record<string, unknown>>;
        finalPositions: Array<Record<string, unknown>>;
        fullSummary: Record<string, unknown>;
      }

      interface StrategyRun {
        strategy: string;
        /** Strategy key sent to Python scripts (resolved for composed strategies) */
        screenStrategy: string;
        strategyLabel: string;
        screenCandidates: StrategyScreenCandidate[];
        screenCount: number;
        summary: BacktestSummaryLite | null;
        compositeScore: number;
        error?: string;
        rank?: number;
        backtestDetail?: BacktestDetail;
      }

      interface CombinedSource {
        strategy: string;
        strategyLabel: string;
        strategyRank: number;
        candidateRank: number;
        screenScore: number;
        reasons: string[];
      }

      interface CombinedStock {
        symbol: string;
        name: string;
        price: number;
        changePct: number;
        appearanceCount: number;
        contributionScore: number;
        rawScreenScore: number;
        sourceDetails: CombinedSource[];
        combinedScore: number;
        rank: number;
        selected: boolean;
      }

      interface StockBacktestEntry {
        symbol: string;
        name: string;
        strategy: string;
        strategyLabel: string;
        selectedBy: string[];
        summary: BacktestSummaryLite | null;
        compositeScore: number;
        equityCurve?: Array<{ date: string; equity: number; cash: number; positions: number }>;
        trades?: Array<Record<string, unknown>>;
        monthlyReturns?: Array<{ month: string; return: number; equity: number }>;
        error?: string;
        rank?: number;
      }

      interface PoolBacktestEntry {
        strategy: string;
        strategyLabel: string;
        summary: BacktestSummaryLite | null;
        compositeScore: number;
        error?: string;
        rank?: number;
      }

      const parseSummary = (rawSummary: unknown): BacktestSummaryLite | null => {
        if (!rawSummary || typeof rawSummary !== "object") {
          return null;
        }
        const s = rawSummary as Record<string, unknown>;
        if (typeof s.error === "string" && s.error.trim()) {
          return null;
        }
        return {
          totalReturn: round2(toFiniteNumber(s.totalReturn, 0)),
          annualizedReturn: round2(toFiniteNumber(s.annualizedReturn, 0)),
          maxDrawdown: round2(toFiniteNumber(s.maxDrawdown, 0)),
          sharpeRatio: round2(toFiniteNumber(s.sharpeRatio, 0)),
          winRate: round2(toFiniteNumber(s.winRate, 0)),
          profitFactor: round2(toFiniteNumber(s.profitFactor, 0)),
          totalTrades: clampInteger(s.totalTrades, 0, 1_000_000, 0),
          finalEquity: round2(toFiniteNumber(s.finalEquity, 0)),
        };
      };

      const strategyComposite = (summary: BacktestSummaryLite): number => {
        const score =
          summary.totalReturn * 1.0 +
          summary.annualizedReturn * 0.35 +
          summary.sharpeRatio * 12 +
          summary.winRate * 0.28 +
          Math.min(summary.profitFactor, 4) * 4 -
          summary.maxDrawdown * 0.85 +
          Math.min(summary.totalTrades, 120) * 0.03;
        return round2(score);
      };

      const stockComposite = (summary: BacktestSummaryLite): number => {
        const score =
          summary.totalReturn * 1.1 +
          summary.annualizedReturn * 0.25 +
          summary.sharpeRatio * 10 +
          summary.winRate * 0.3 +
          Math.min(summary.profitFactor, 4) * 3 -
          summary.maxDrawdown * 0.8;
        return round2(score);
      };

      try {
        const body = await parseObjectBody(req);
        const rawStrategies = Array.isArray(body.strategies)
          ? body.strategies.map((s) => String(s).trim()).filter(Boolean)
          : [...DAILY_PICK_STRATEGIES];
        const allowedStrategies = new Set<string>(DAILY_PICK_STRATEGIES);
        // Also allow user-created composed strategies (prefixed with "composed:")
        const composedIds = new Set<string>();
        if (composedStrategyStore) {
          for (const c of composedStrategyStore.list()) {
            const key = `composed:${c.id}`;
            allowedStrategies.add(key);
            composedIds.add(key);
          }
        }
        const strategies = rawStrategies.filter((s) => allowedStrategies.has(s));

        if (strategies.length === 0) {
          json(res, { error: "未提供有效策略" }, 400);
          return;
        }

        const topNPerStrategy = clampInteger(
          body.top_n_per_strategy ?? body.topNPerStrategy,
          5,
          20,
          10,
        );
        const topStrategyCount = clampInteger(
          body.top_strategy_count ?? body.topStrategyCount,
          2,
          3,
          3,
        );
        const finalStockLimit = clampInteger(
          body.final_stock_limit ?? body.finalStockLimit,
          3,
          8,
          8,
        );
        const initialCapital = toFiniteNumber(body.initial_capital ?? body.initialCapital, 500_000);
        const riskTolerance = String(body.risk_tolerance ?? body.riskTolerance ?? "medium");
        const rebalanceDays = clampInteger(body.rebalance_days ?? body.rebalanceDays, 1, 60, 10);
        const commissionRate = toFiniteNumber(body.commission_rate ?? body.commissionRate, 0.0003);
        const stampTax = toFiniteNumber(body.stamp_tax ?? body.stampTax, 0.001);
        const endDateDefault = formatDateYmd(new Date());
        const startDateDefault = (() => {
          const d = new Date();
          d.setMonth(d.getMonth() - 12);
          return formatDateYmd(d);
        })();
        const startDate = String(body.start_date ?? body.startDate ?? startDateDefault);
        const endDate = String(body.end_date ?? body.endDate ?? endDateDefault);

        // Capture cross-module enrichment context from the first successful screen
        let enrichmentContext: Record<string, unknown> = {};
        const strategyRuns = await Promise.all(
          strategies.map(async (strategy): Promise<StrategyRun> => {
            // Resolve label and screen key for composed / CTA strategies
            let screenStrategy = strategy;
            let strategyLabel = DAILY_PICK_STRATEGY_LABELS[strategy] ?? strategy;
            if (CTA_SCREEN_STRATEGY_MAP[strategy]) {
              screenStrategy = CTA_SCREEN_STRATEGY_MAP[strategy];
            } else if (composedIds.has(strategy) && composedStrategyStore) {
              const compId = strategy.slice("composed:".length);
              const comp = composedStrategyStore.get(compId);
              if (comp) {
                strategyLabel = comp.name;
                // Use "mixed" as the screen strategy for composed strategies
                screenStrategy = "mixed";
              }
            }

            const screenResult = await runPythonScript<Record<string, unknown>>(
              "scripts/investment_planner.py",
              {
                action: "screen",
                strategy: screenStrategy,
                risk_tolerance: riskTolerance,
                top_n: topNPerStrategy,
              },
              config,
              { timeoutMs: 240_000 },
            );

            if (!screenResult.ok) {
              return {
                strategy,
                screenStrategy,
                strategyLabel,
                screenCandidates: [],
                screenCount: 0,
                summary: null,
                compositeScore: Number.NEGATIVE_INFINITY,
                error: screenResult.error ?? `${strategyLabel} 选股失败`,
              };
            }

            const screenData = screenResult.data ?? {};
            // Capture enrichment metadata from first successful screen
            if (
              Object.keys(enrichmentContext).length === 0 &&
              screenData.macroRegime !== undefined
            ) {
              const posGuidance = (screenData.positionGuidance ?? {}) as Record<string, unknown>;
              enrichmentContext = {
                macroRegime: String(screenData.macroRegime ?? "unknown"),
                macroDescription: String(screenData.macroDescription ?? ""),
                lianbanPoolCount: toFiniteNumber(screenData.lianbanPoolCount, 0),
                hotStocksCount: toFiniteNumber(screenData.hotStocksCount, 0),
                positionGuidance: {
                  regimeAdjustment: String(posGuidance.regimeAdjustment ?? ""),
                  positionScale: toFiniteNumber(posGuidance.positionScale, 1),
                  maxSinglePositionPct: toFiniteNumber(posGuidance.maxSinglePositionPct, 0.2),
                  maxPositions: toFiniteNumber(posGuidance.maxPositions, 8),
                },
              };
            }
            const rawCandidates = Array.isArray(screenData.candidates)
              ? (screenData.candidates as Array<Record<string, unknown>>)
              : [];
            const screenCandidates = rawCandidates
              .slice(0, topNPerStrategy)
              .map((item, index): StrategyScreenCandidate => {
                const symbol = String(item.symbol ?? item.code ?? "").trim();
                const reasons = Array.isArray(item.reasons)
                  ? item.reasons.slice(0, 6).map((r) => String(r))
                  : [];
                return {
                  symbol,
                  name: String(item.name ?? symbol),
                  price: round2(toFiniteNumber(item.price, 0)),
                  changePct: round2(toFiniteNumber(item.changePct ?? item.change_pct, 0)),
                  score: round2(toFiniteNumber(item.score, 0)),
                  rank: index + 1,
                  reasons,
                };
              })
              .filter((item) => item.symbol.length > 0);

            if (screenCandidates.length === 0) {
              return {
                strategy,
                screenStrategy,
                strategyLabel,
                screenCandidates: [],
                screenCount: 0,
                summary: null,
                compositeScore: Number.NEGATIVE_INFINITY,
                error: `${strategyLabel} 未产出可用候选股`,
              };
            }

            const symbolList = screenCandidates.map((item) => item.symbol);
            const backtestResult = await runPythonScript<Record<string, unknown>>(
              "scripts/backtest_engine.py",
              {
                action: "run",
                initial_capital: initialCapital,
                start_date: startDate,
                end_date: endDate,
                strategy: screenStrategy,
                risk_tolerance: riskTolerance,
                symbols: symbolList,
                rebalance_days: rebalanceDays,
                commission_rate: commissionRate,
                stamp_tax: stampTax,
              },
              config,
              { timeoutMs: 240_000 },
            );

            if (!backtestResult.ok) {
              return {
                strategy,
                screenStrategy,
                strategyLabel,
                screenCandidates,
                screenCount: screenCandidates.length,
                summary: null,
                compositeScore: Number.NEGATIVE_INFINITY,
                error: backtestResult.error ?? `${strategyLabel} 回测失败`,
              };
            }

            const backtestData = backtestResult.data ?? {};
            const summary = parseSummary(backtestData.summary);
            if (!summary) {
              return {
                strategy,
                screenStrategy,
                strategyLabel,
                screenCandidates,
                screenCount: screenCandidates.length,
                summary: null,
                compositeScore: Number.NEGATIVE_INFINITY,
                error: `${strategyLabel} 回测结果不完整`,
              };
            }

            // Preserve full backtest detail for drill-down
            const rawCurve = Array.isArray(backtestData.equityCurve)
              ? (backtestData.equityCurve as Array<Record<string, unknown>>)
              : [];
            const rawMonthly = Array.isArray(backtestData.monthlyReturns)
              ? (backtestData.monthlyReturns as Array<Record<string, unknown>>)
              : [];
            const rawTrades = Array.isArray(backtestData.trades)
              ? (backtestData.trades as Array<Record<string, unknown>>)
              : [];
            const rawPositions = Array.isArray(backtestData.finalPositions)
              ? (backtestData.finalPositions as Array<Record<string, unknown>>)
              : [];
            const backtestDetail: BacktestDetail = {
              equityCurve: rawCurve.map((p) => ({
                date: String(p.date ?? ""),
                equity: toFiniteNumber(p.equity, 0),
                cash: toFiniteNumber(p.cash, 0),
                positions: clampInteger(p.positions, 0, 1000, 0),
              })),
              monthlyReturns: rawMonthly.map((m) => ({
                month: String(m.month ?? ""),
                return: round2(toFiniteNumber(m.return, 0)),
                equity: round2(toFiniteNumber(m.equity, 0)),
              })),
              trades: rawTrades.slice(0, 100),
              finalPositions: rawPositions,
              fullSummary: (backtestData.summary ?? {}) as Record<string, unknown>,
            };

            return {
              strategy,
              screenStrategy,
              strategyLabel,
              screenCandidates,
              screenCount: screenCandidates.length,
              summary,
              compositeScore: strategyComposite(summary),
              backtestDetail,
            };
          }),
        );

        const sortedRuns = [...strategyRuns].sort((a, b) => {
          const aOk = a.summary && !a.error;
          const bOk = b.summary && !b.error;
          if (aOk !== bOk) return aOk ? -1 : 1;
          return b.compositeScore - a.compositeScore;
        });

        let strategyRank = 1;
        for (const run of sortedRuns) {
          if (run.summary && !run.error) {
            run.rank = strategyRank++;
          }
        }

        const selectedStrategies = sortedRuns
          .filter((run) => run.summary && !run.error)
          .slice(0, topStrategyCount);
        if (selectedStrategies.length === 0) {
          json(res, { error: "所有策略执行失败，请稍后重试" }, 500);
          return;
        }

        const combinedMap = new Map<string, CombinedStock>();
        selectedStrategies.forEach((run, strategyIdx) => {
          const strategyWeight = Math.max(1, topStrategyCount - strategyIdx);
          run.screenCandidates.slice(0, topNPerStrategy).forEach((candidate, candidateIdx) => {
            const current = combinedMap.get(candidate.symbol) ?? {
              symbol: candidate.symbol,
              name: candidate.name,
              price: candidate.price,
              changePct: candidate.changePct,
              appearanceCount: 0,
              contributionScore: 0,
              rawScreenScore: 0,
              sourceDetails: [],
              combinedScore: 0,
              rank: 0,
              selected: false,
            };
            const rankWeight = (topNPerStrategy - candidateIdx) / topNPerStrategy;
            const contribution =
              Math.max(0, candidate.score) * 0.7 + rankWeight * 35 + strategyWeight * 10;

            current.appearanceCount += 1;
            current.contributionScore += contribution;
            current.rawScreenScore += candidate.score;
            current.sourceDetails.push({
              strategy: run.strategy,
              strategyLabel: run.strategyLabel,
              strategyRank: run.rank ?? strategyIdx + 1,
              candidateRank: candidate.rank,
              screenScore: candidate.score,
              reasons: candidate.reasons,
            });
            if ((!current.name || current.name === current.symbol) && candidate.name) {
              current.name = candidate.name;
            }
            if (current.price <= 0 && candidate.price > 0) {
              current.price = candidate.price;
            }
            if (Math.abs(current.changePct) < 0.001 && Math.abs(candidate.changePct) > 0.001) {
              current.changePct = candidate.changePct;
            }
            combinedMap.set(candidate.symbol, current);
          });
        });

        const combinedList = [...combinedMap.values()]
          .map((item): CombinedStock => {
            const avgScreenScore =
              item.appearanceCount > 0 ? item.rawScreenScore / item.appearanceCount : 0;
            // Score = average screen score for initial pool-entry ordering;
            // backtest composite will replace this after individual stock backtests.
            const combinedScore = avgScreenScore;
            const sourceDetails = [...item.sourceDetails].sort(
              (a, b) => a.strategyRank - b.strategyRank || a.candidateRank - b.candidateRank,
            );
            return {
              ...item,
              sourceDetails,
              combinedScore: round2(combinedScore),
            };
          })
          .sort((a, b) => b.combinedScore - a.combinedScore)
          .map((item, index) => ({
            ...item,
            rank: index + 1,
            selected: index < finalStockLimit,
          }));

        // Backtest all candidates so every stock gets a proper composite score
        const backtestPoolSize = combinedList.length;
        const backtestPool = combinedList.slice(0, backtestPoolSize);
        const backtestPoolSymbols = backtestPool.map((item) => item.symbol);

        let stockBacktestRanking: StockBacktestEntry[] = [];
        if (backtestPool.length > 0) {
          const benchmark = selectedStrategies[0]!;
          stockBacktestRanking = await Promise.all(
            backtestPool.map(async (stock): Promise<StockBacktestEntry> => {
              const btResult = await runPythonScript<Record<string, unknown>>(
                "scripts/backtest_engine.py",
                {
                  action: "run",
                  initial_capital: initialCapital,
                  start_date: startDate,
                  end_date: endDate,
                  strategy: benchmark.screenStrategy,
                  risk_tolerance: riskTolerance,
                  symbols: [stock.symbol],
                  rebalance_days: rebalanceDays,
                  commission_rate: commissionRate,
                  stamp_tax: stampTax,
                },
                config,
                { timeoutMs: 240_000 },
              );

              if (!btResult.ok) {
                return {
                  symbol: stock.symbol,
                  name: stock.name,
                  strategy: benchmark.strategy,
                  strategyLabel: benchmark.strategyLabel,
                  selectedBy: stock.sourceDetails.map((x) => x.strategyLabel),
                  summary: null,
                  compositeScore: Number.NEGATIVE_INFINITY,
                  error: btResult.error ?? "单股回测失败",
                };
              }

              // Check for application-level error from Python (process exits 0 but result has error)
              const btData = (btResult.data ?? {}) as Record<string, unknown>;
              if (typeof btData.error === "string" && btData.error.trim()) {
                return {
                  symbol: stock.symbol,
                  name: stock.name,
                  strategy: benchmark.strategy,
                  strategyLabel: benchmark.strategyLabel,
                  selectedBy: stock.sourceDetails.map((x) => x.strategyLabel),
                  summary: null,
                  compositeScore: Number.NEGATIVE_INFINITY,
                  error: btData.error,
                };
              }

              const summary = parseSummary(btData.summary);
              if (!summary) {
                return {
                  symbol: stock.symbol,
                  name: stock.name,
                  strategy: benchmark.strategy,
                  strategyLabel: benchmark.strategyLabel,
                  selectedBy: stock.sourceDetails.map((x) => x.strategyLabel),
                  summary: null,
                  compositeScore: Number.NEGATIVE_INFINITY,
                  error: "单股回测结果不完整",
                };
              }

              const rawData = btData;
              const equityCurve = Array.isArray(rawData.equityCurve)
                ? (rawData.equityCurve as Array<{
                    date: string;
                    equity: number;
                    cash: number;
                    positions: number;
                  }>)
                : [];
              const trades = Array.isArray(rawData.trades)
                ? (rawData.trades as Array<Record<string, unknown>>).slice(-100)
                : [];
              const monthlyReturns = Array.isArray(rawData.monthlyReturns)
                ? (rawData.monthlyReturns as Array<{
                    month: string;
                    return: number;
                    equity: number;
                  }>)
                : [];

              return {
                symbol: stock.symbol,
                name: stock.name,
                strategy: benchmark.strategy,
                strategyLabel: benchmark.strategyLabel,
                selectedBy: stock.sourceDetails.map((x) => x.strategyLabel),
                summary,
                compositeScore: stockComposite(summary),
                equityCurve,
                trades,
                monthlyReturns,
              };
            }),
          );

          stockBacktestRanking.sort((a, b) => {
            const aOk = a.summary && !a.error;
            const bOk = b.summary && !b.error;
            if (aOk !== bOk) return aOk ? -1 : 1;
            return b.compositeScore - a.compositeScore;
          });
          let rank = 1;
          for (const row of stockBacktestRanking) {
            if (row.summary && !row.error) {
              row.rank = rank++;
            }
          }
        }

        // ── Post-backtest filtering: remove stocks with poor backtest metrics ──
        // Build a map of backtest composite scores for each symbol
        const backtestScoreMap = new Map<string, number>();
        for (const row of stockBacktestRanking) {
          if (row.summary && !row.error) {
            backtestScoreMap.set(row.symbol, row.compositeScore);
          }
        }

        // Final ranking purely by backtest metrics — screen score only determines
        // which stocks enter the backtest pool; it does not affect final rank.
        // Stocks with successful backtest are ranked by backtest compositeScore;
        // stocks without backtest data are appended at the end (lower priority).
        const withBacktest = backtestPool
          .filter((stock) => backtestScoreMap.has(stock.symbol))
          .map((stock) => ({
            ...stock,
            combinedScore: backtestScoreMap.get(stock.symbol)!,
          }))
          .sort((a, b) => b.combinedScore - a.combinedScore);

        const withoutBacktest = backtestPool.filter((stock) => !backtestScoreMap.has(stock.symbol));

        const finalCandidates = [...withBacktest, ...withoutBacktest]
          .slice(0, finalStockLimit)
          .map((item, index) => ({
            ...item,
            rank: index + 1,
            selected: true,
          }));

        // Update combinedList with backtest-based scores and final selection status
        for (const item of combinedList) {
          item.selected = finalCandidates.some((f) => f.symbol === item.symbol);
          const bt = backtestScoreMap.get(item.symbol);
          if (bt !== undefined) {
            item.combinedScore = round2(bt);
          }
        }
        // Re-sort and re-rank by backtest-driven composite scores
        combinedList.sort((a, b) => b.combinedScore - a.combinedScore);
        for (let i = 0; i < combinedList.length; i++) {
          combinedList[i]!.rank = i + 1;
        }

        const finalStocks = finalCandidates;
        const finalSymbols = finalStocks.map((item) => item.symbol);

        // Keep full backtest results for combined list display before filtering
        const fullStockBacktestRanking = stockBacktestRanking;

        // Filter stockBacktestRanking to only include final selected stocks
        stockBacktestRanking = stockBacktestRanking.filter((r) => finalSymbols.includes(r.symbol));
        {
          let finalRank = 1;
          for (const row of stockBacktestRanking) {
            if (row.summary && !row.error) {
              row.rank = finalRank++;
            }
          }
        }

        // ── Phase 3 (pool compare) + Phase 4 (routing enrichment) in parallel ──
        let poolBacktestRanking: PoolBacktestEntry[] = [];
        let routingEnrichment: Array<{
          symbol: string;
          routing: {
            profileKey: string;
            volatility: string;
            trend: string;
            marketCap: string;
            combo: string;
            comboMethod: string;
            strategies: string[];
            source: string;
            reason: string;
          } | null;
        }> = [];

        const poolCompareTask = async () => {
          const compareResult = await runPythonScript<Record<string, unknown>>(
            "scripts/backtest_engine.py",
            {
              action: "compare",
              initial_capital: initialCapital,
              start_date: startDate,
              end_date: endDate,
              strategies: selectedStrategies.map((s) => s.screenStrategy),
              risk_tolerance: riskTolerance,
              symbols: finalSymbols,
              rebalance_days: rebalanceDays,
              commission_rate: commissionRate,
              stamp_tax: stampTax,
            },
            config,
            { timeoutMs: 300_000 },
          );

          if (compareResult.ok) {
            const compareData = compareResult.data ?? {};
            const comparison = Array.isArray(compareData.comparison)
              ? (compareData.comparison as Array<Record<string, unknown>>)
              : [];

            poolBacktestRanking = comparison.map((row): PoolBacktestEntry => {
              const summaryRaw = row.summary as Record<string, unknown> | undefined;
              const strategy = String(summaryRaw?.strategy ?? "").trim();
              const strategyLabel = String(
                summaryRaw?.strategyLabel ?? DAILY_PICK_STRATEGY_LABELS[strategy] ?? strategy,
              );
              const summary = parseSummary(summaryRaw);
              if (!summary) {
                return {
                  strategy,
                  strategyLabel,
                  summary: null,
                  compositeScore: Number.NEGATIVE_INFINITY,
                  error: String(summaryRaw?.error ?? "策略池回测失败"),
                };
              }
              return {
                strategy,
                strategyLabel,
                summary,
                compositeScore: strategyComposite(summary),
              };
            });

            poolBacktestRanking.sort((a, b) => {
              const aOk = a.summary && !a.error;
              const bOk = b.summary && !b.error;
              if (aOk !== bOk) return aOk ? -1 : 1;
              return b.compositeScore - a.compositeScore;
            });
            let poolRank = 1;
            for (const row of poolBacktestRanking) {
              if (row.summary && !row.error) {
                row.rank = poolRank++;
              }
            }
          }
        };

        const routingEnrichTask = async () => {
          if (finalStocks.length === 0) return;
          try {
            const enrichCandidates = finalStocks.map((s) => ({
              symbol: s.symbol,
              name: s.name,
            }));
            const enrichResult = await runPythonScript<Record<string, unknown>>(
              "engines/strategy_router.py",
              {
                action: "enrich_with_routing",
                candidates: enrichCandidates,
                endDate,
              },
              config,
              { timeoutMs: 60_000 },
            );
            if (enrichResult.ok && enrichResult.data) {
              const enriched = enrichResult.data.enriched as
                | Array<Record<string, unknown>>
                | undefined;
              if (enriched) {
                routingEnrichment = enriched.map((item) => ({
                  symbol: String(item.symbol ?? ""),
                  routing: item.routing as (typeof routingEnrichment)[number]["routing"],
                }));
              }
            }
          } catch {
            // routing enrichment is best-effort; don't fail the whole request
          }
        };

        await Promise.all([poolCompareTask(), routingEnrichTask()]);

        json(res, {
          status: "ok",
          generatedAt: new Date().toISOString(),
          enrichment: enrichmentContext,
          input: {
            strategies,
            topStrategyCount,
            topNPerStrategy,
            finalStockLimit,
            initialCapital,
            riskTolerance,
            rebalanceDays,
            startDate,
            endDate,
          },
          strategyRanking: sortedRuns.map((run) => ({
            rank: run.rank ?? null,
            strategy: run.strategy,
            screenStrategy: run.screenStrategy,
            strategyLabel: run.strategyLabel,
            screenCount: run.screenCount,
            topSymbols: run.screenCandidates.slice(0, 6).map((c) => c.symbol),
            compositeScore: run.summary ? run.compositeScore : null,
            totalReturn: run.summary?.totalReturn ?? null,
            annualizedReturn: run.summary?.annualizedReturn ?? null,
            maxDrawdown: run.summary?.maxDrawdown ?? null,
            sharpeRatio: run.summary?.sharpeRatio ?? null,
            winRate: run.summary?.winRate ?? null,
            profitFactor: run.summary?.profitFactor ?? null,
            totalTrades: run.summary?.totalTrades ?? null,
            error: run.error,
            // Full detail for drill-down
            screenCandidates: run.screenCandidates,
            scoreBreakdown: run.summary
              ? {
                  totalReturn: {
                    value: run.summary.totalReturn,
                    weight: 1.0,
                    contribution: round2(run.summary.totalReturn * 1.0),
                  },
                  annualizedReturn: {
                    value: run.summary.annualizedReturn,
                    weight: 0.35,
                    contribution: round2(run.summary.annualizedReturn * 0.35),
                  },
                  sharpeRatio: {
                    value: run.summary.sharpeRatio,
                    weight: 12,
                    contribution: round2(run.summary.sharpeRatio * 12),
                  },
                  winRate: {
                    value: run.summary.winRate,
                    weight: 0.28,
                    contribution: round2(run.summary.winRate * 0.28),
                  },
                  profitFactor: {
                    value: Math.min(run.summary.profitFactor, 4),
                    weight: 4,
                    contribution: round2(Math.min(run.summary.profitFactor, 4) * 4),
                  },
                  maxDrawdown: {
                    value: run.summary.maxDrawdown,
                    weight: -0.85,
                    contribution: round2(-run.summary.maxDrawdown * 0.85),
                  },
                  tradeFrequency: {
                    value: Math.min(run.summary.totalTrades, 120),
                    weight: 0.03,
                    contribution: round2(Math.min(run.summary.totalTrades, 120) * 0.03),
                  },
                  total: run.compositeScore,
                }
              : null,
            backtestDetail: run.backtestDetail
              ? {
                  equityCurve: run.backtestDetail.equityCurve,
                  monthlyReturns: run.backtestDetail.monthlyReturns,
                  trades: run.backtestDetail.trades,
                  finalPositions: run.backtestDetail.finalPositions,
                  fullSummary: run.backtestDetail.fullSummary,
                }
              : null,
          })),
          selectedStrategies: selectedStrategies.map((run) => ({
            rank: run.rank ?? null,
            strategy: run.strategy,
            strategyLabel: run.strategyLabel,
            compositeScore: run.compositeScore,
          })),
          combinedList: combinedList.map((item) => {
            const btRow = fullStockBacktestRanking.find((r) => r.symbol === item.symbol);
            const btSummary = btRow?.summary ?? null;
            return {
              rank: item.rank,
              symbol: item.symbol,
              name: item.name,
              price: item.price,
              changePct: item.changePct,
              appearanceCount: item.appearanceCount,
              combinedScore: item.combinedScore,
              selected: item.selected,
              selectedBy: item.sourceDetails.map((x) => x.strategyLabel),
              backtestReturn: btSummary?.totalReturn ?? null,
              backtestSharpe: btSummary?.sharpeRatio ?? null,
              backtestDrawdown: btSummary?.maxDrawdown ?? null,
              backtestWinRate: btSummary?.winRate ?? null,
              backtestError: btRow?.error ?? null,
              sourceDetails: item.sourceDetails,
              scoreBreakdown: {
                avgScreenScore: round2(
                  item.appearanceCount > 0 ? item.rawScreenScore / item.appearanceCount : 0,
                ),
                appearanceCount: item.appearanceCount,
                total: item.combinedScore,
                perStrategy: item.sourceDetails.map((src) => ({
                  strategy: src.strategy,
                  strategyLabel: src.strategyLabel,
                  strategyRank: src.strategyRank,
                  candidateRank: src.candidateRank,
                  screenScore: src.screenScore,
                  reasons: src.reasons,
                })),
              },
            };
          }),
          finalSymbols,
          stockBacktestRanking: stockBacktestRanking.map((row) => ({
            rank: row.rank ?? null,
            symbol: row.symbol,
            name: row.name,
            selectedBy: row.selectedBy,
            strategy: row.strategy,
            strategyLabel: row.strategyLabel,
            compositeScore: row.summary ? row.compositeScore : null,
            totalReturn: row.summary?.totalReturn ?? null,
            annualizedReturn: row.summary?.annualizedReturn ?? null,
            maxDrawdown: row.summary?.maxDrawdown ?? null,
            sharpeRatio: row.summary?.sharpeRatio ?? null,
            winRate: row.summary?.winRate ?? null,
            profitFactor: row.summary?.profitFactor ?? null,
            totalTrades: row.summary?.totalTrades ?? null,
            equityCurve: row.equityCurve ?? [],
            trades: row.trades ?? [],
            monthlyReturns: row.monthlyReturns ?? [],
            error: row.error,
          })),
          poolBacktestRanking: poolBacktestRanking.map((row) => ({
            rank: row.rank ?? null,
            strategy: row.strategy,
            strategyLabel: row.strategyLabel,
            compositeScore: row.summary ? row.compositeScore : null,
            totalReturn: row.summary?.totalReturn ?? null,
            annualizedReturn: row.summary?.annualizedReturn ?? null,
            maxDrawdown: row.summary?.maxDrawdown ?? null,
            sharpeRatio: row.summary?.sharpeRatio ?? null,
            winRate: row.summary?.winRate ?? null,
            profitFactor: row.summary?.profitFactor ?? null,
            totalTrades: row.summary?.totalTrades ?? null,
            error: row.error,
          })),
          routingEnrichment,
        });
      } catch (err) {
        json(
          res,
          { error: `每日综合选股失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });
}
