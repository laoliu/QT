/**
 * portfolio_backtest tool — AI-callable tool for backtesting strategies
 * against the user's existing portfolio holdings.
 *
 * Actions:
 *   - run:      Backtest one or more strategies on current portfolio holdings
 *   - compare:  Compare multiple strategies side-by-side on portfolio
 *   - summary:  Quick summary of best strategy for current holdings
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { listStrategies, runBacktest } from "../engine/strategy-registry.js";
import { runBatchBacktest } from "../services/batch-backtest.js";
import type { BacktestResult, Portfolio, PortfolioPosition, QuantTradingConfig } from "../types.js";

function getPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio.json");
}

async function loadPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
  const filePath = getPortfolioPath(config);
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as Portfolio;
  } catch {
    return {
      id: "default",
      name: "默认组合",
      cash: 100000,
      totalValue: 100000,
      positions: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  }
}

function getDefaultStartDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

interface PerPositionResult {
  symbol: string;
  name: string;
  quantity: number;
  avgCost: number;
  currentValue: number;
  strategyId: string;
  strategyName: string;
  backtestReturn: string;
  annualizedReturn: string;
  maxDrawdown: string;
  sharpeRatio: string;
  winRate: string;
  totalTrades: number;
  finalValue: number;
}

interface PortfolioBacktestSummary {
  portfolioName: string;
  positionCount: number;
  totalInvested: number;
  strategyId: string;
  strategyName: string;
  backtestInitialValue: number;
  backtestFinalValue: number;
  totalReturn: string;
  annualizedReturn: string;
  maxDrawdown: string;
  sharpeRatio: string;
  avgWinRate: string;
  totalTrades: number;
  period: string;
  perPosition: PerPositionResult[];
}

export function createPortfolioBacktestTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "portfolio_backtest",
    description:
      "基于已有持仓的策略回测工具。读取用户当前持仓的股票，用指定策略（或多个策略）进行历史回测，" +
      "评估组合在不同策略下的盈利能力。\n" +
      "action=run 对持仓运行单策略回测，action=compare 对比多个策略在持仓上的表现，" +
      "action=summary 快速找出最适合当前持仓的策略。\n" +
      "【重要】回测基于日K线数据，包含真实A股费率（佣金万3、印花税万5、滑点千1）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["run", "compare", "summary"],
          description: "操作: run=运行单策略回测, compare=多策略对比, summary=最优策略推荐",
        },
        strategyIds: {
          type: "string",
          description:
            "策略ID，逗号分隔。run时指定单个策略，compare时指定多个策略对比。" +
            "如: dual-ma-crossover,rsi-filter,momentum-breakout",
        },
        symbols: {
          type: "string",
          description: "自定义股票代码(可选)，逗号分隔。不填时使用当前持仓的所有股票。",
        },
        startDate: {
          type: "string",
          description: "回测起始日期 YYYY-MM-DD (默认一年前)",
        },
        endDate: {
          type: "string",
          description: "回测结束日期 YYYY-MM-DD (默认今天)",
        },
        initialCash: {
          type: "number",
          description: "初始资金(可选)。不填时使用持仓总市值作为初始资金",
        },
        weightMode: {
          type: "string",
          enum: ["equal", "by_position"],
          description:
            "资金分配方式: equal=等权分配, by_position=按持仓比例分配 (默认 by_position)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const portfolio = await loadPortfolio(config);

      // Resolve symbols: use portfolio positions if not specified
      let symbols: string[];
      let positions: PortfolioPosition[];
      if (args.symbols) {
        symbols = (args.symbols as string)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        positions = symbols.map((sym) => {
          const existing = portfolio.positions.find((p) => p.symbol === sym);
          return (
            existing ?? {
              symbol: sym,
              name: sym,
              market: "cn_stock" as const,
              quantity: 0,
              avgCost: 0,
            }
          );
        });
      } else {
        if (portfolio.positions.length === 0) {
          return {
            error:
              "当前没有持仓。请先通过 portfolio tool 添加持仓（action=buy），或使用 symbols 参数指定股票代码。",
          };
        }
        positions = portfolio.positions;
        symbols = positions.map((p) => p.symbol);
      }

      const startDate = (args.startDate as string) ?? getDefaultStartDate();
      const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
      const weightMode = (args.weightMode as string) ?? "by_position";

      // Calculate initial capital
      const totalInvested = positions.reduce(
        (sum, p) => sum + p.quantity * (p.currentPrice ?? p.avgCost),
        0,
      );
      const initialCash =
        (args.initialCash as number) ?? (totalInvested > 0 ? totalInvested : 100000);

      const allStrategies = listStrategies();

      if (action === "run") {
        const strategyIds = args.strategyIds
          ? (args.strategyIds as string).split(",").map((s) => s.trim())
          : ["dual-ma-crossover"];

        if (strategyIds.length === 0) {
          return { error: "请提供至少一个策略ID (strategyIds)" };
        }

        const results: PortfolioBacktestSummary[] = [];

        for (const strategyId of strategyIds) {
          const stratDef = allStrategies.find((s) => s.id === strategyId);
          if (!stratDef) {
            results.push({
              portfolioName: portfolio.name,
              positionCount: positions.length,
              totalInvested,
              strategyId,
              strategyName: "未知策略",
              backtestInitialValue: initialCash,
              backtestFinalValue: 0,
              totalReturn: "N/A",
              annualizedReturn: "N/A",
              maxDrawdown: "N/A",
              sharpeRatio: "N/A",
              avgWinRate: "N/A",
              totalTrades: 0,
              period: `${startDate} ~ ${endDate}`,
              perPosition: [],
            });
            continue;
          }

          try {
            const result = await runBacktest(
              {
                strategyId,
                symbols,
                startDate,
                endDate,
                initialCash,
              },
              config,
            );

            const perPosition = buildPerPositionResults(
              positions,
              result,
              strategyId,
              stratDef.name,
            );

            results.push({
              portfolioName: portfolio.name,
              positionCount: positions.length,
              totalInvested: Math.round(totalInvested * 100) / 100,
              strategyId,
              strategyName: stratDef.name,
              backtestInitialValue: initialCash,
              backtestFinalValue: Math.round(result.finalValue * 100) / 100,
              totalReturn: `${result.totalReturn.toFixed(2)}%`,
              annualizedReturn: `${result.annualizedReturn.toFixed(2)}%`,
              maxDrawdown: `${result.maxDrawdown.toFixed(2)}%`,
              sharpeRatio: result.sharpeRatio.toFixed(3),
              avgWinRate: `${result.winRate.toFixed(1)}%`,
              totalTrades: result.totalTrades,
              period: `${startDate} ~ ${endDate}`,
              perPosition,
            });
          } catch (err) {
            results.push({
              portfolioName: portfolio.name,
              positionCount: positions.length,
              totalInvested,
              strategyId,
              strategyName: stratDef.name,
              backtestInitialValue: initialCash,
              backtestFinalValue: 0,
              totalReturn: `错误: ${(err as Error).message}`,
              annualizedReturn: "N/A",
              maxDrawdown: "N/A",
              sharpeRatio: "N/A",
              avgWinRate: "N/A",
              totalTrades: 0,
              period: `${startDate} ~ ${endDate}`,
              perPosition: [],
            });
          }
        }

        return {
          portfolio: {
            name: portfolio.name,
            positions: positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              avgCost: `¥${p.avgCost.toFixed(2)}`,
              currentValue: `¥${(p.quantity * (p.currentPrice ?? p.avgCost)).toFixed(2)}`,
            })),
            totalInvested: `¥${totalInvested.toFixed(2)}`,
          },
          results: results.length === 1 ? results[0] : results,
        };
      }

      if (action === "compare") {
        const strategyIds = args.strategyIds
          ? (args.strategyIds as string).split(",").map((s) => s.trim())
          : ["dual-ma-crossover", "momentum-breakout", "rsi-filter", "mean-reversion"];

        const batchItems = strategyIds.map((id) => ({
          label: allStrategies.find((s) => s.id === id)?.name ?? id,
          request: {
            strategyId: id,
            symbols,
            startDate,
            endDate,
            initialCash,
          },
        }));

        const batchResult = await runBatchBacktest(batchItems, config);

        return {
          portfolio: {
            name: portfolio.name,
            symbols,
            totalInvested: `¥${totalInvested.toFixed(2)}`,
            initialCash: `¥${initialCash.toLocaleString()}`,
          },
          period: `${startDate} ~ ${endDate}`,
          comparison: batchResult.ranked.map((r, idx) => ({
            rank: idx + 1,
            strategy: r.label,
            strategyId: r.strategyId,
            totalReturn: r.totalReturn != null ? `${r.totalReturn.toFixed(2)}%` : "N/A",
            annualizedReturn:
              r.annualizedReturn != null ? `${r.annualizedReturn.toFixed(2)}%` : "N/A",
            finalValue: r.finalValue != null ? `¥${r.finalValue.toFixed(2)}` : "N/A",
            profit: r.finalValue != null ? `¥${(r.finalValue - initialCash).toFixed(2)}` : "N/A",
            maxDrawdown: r.maxDrawdown != null ? `${r.maxDrawdown.toFixed(2)}%` : "N/A",
            sharpeRatio: r.sharpeRatio != null ? r.sharpeRatio.toFixed(3) : "N/A",
            winRate: r.winRate != null ? `${r.winRate.toFixed(1)}%` : "N/A",
            totalTrades: r.totalTrades ?? 0,
          })),
          bestStrategy: batchResult.bestStrategy,
          summary: batchResult.bestStrategy
            ? `推荐策略: ${batchResult.bestStrategy}，在回测期间表现最优`
            : "所有策略回测均失败",
          failedStrategies: batchResult.results
            .filter((r) => !r.ok)
            .map((r) => ({ strategy: r.label, error: r.error })),
        };
      }

      if (action === "summary") {
        // Auto-select top strategies and find the best one
        const candidateIds = [
          "dual-ma-crossover",
          "momentum-breakout",
          "mean-reversion",
          "rsi-filter",
          "rsi-enhanced",
          "macd-trend",
          "bollinger-breakout",
          "volume-confirmation",
          "turtle-breakout",
          "ema-crossover",
        ];

        const batchItems = candidateIds
          .filter((id) => allStrategies.some((s) => s.id === id))
          .map((id) => ({
            label: allStrategies.find((s) => s.id === id)?.name ?? id,
            request: {
              strategyId: id,
              symbols,
              startDate,
              endDate,
              initialCash,
            },
          }));

        const batchResult = await runBatchBacktest(batchItems, config);
        const top3 = batchResult.ranked.slice(0, 3);

        return {
          portfolio: {
            name: portfolio.name,
            symbols,
            positionCount: positions.length,
            totalInvested: `¥${totalInvested.toFixed(2)}`,
          },
          period: `${startDate} ~ ${endDate}`,
          recommendation:
            top3.length > 0
              ? {
                  bestStrategy: top3[0]!.label,
                  bestStrategyId: top3[0]!.strategyId,
                  expectedReturn: `${top3[0]!.totalReturn?.toFixed(2)}%`,
                  expectedProfit:
                    top3[0]!.finalValue != null
                      ? `¥${(top3[0]!.finalValue - initialCash).toFixed(2)}`
                      : "N/A",
                  maxDrawdown: `${top3[0]!.maxDrawdown?.toFixed(2)}%`,
                  sharpeRatio: top3[0]!.sharpeRatio?.toFixed(3),
                }
              : null,
          top3: top3.map((r, idx) => ({
            rank: idx + 1,
            strategy: r.label,
            strategyId: r.strategyId,
            totalReturn: `${r.totalReturn?.toFixed(2)}%`,
            profit: r.finalValue != null ? `¥${(r.finalValue - initialCash).toFixed(2)}` : "N/A",
            sharpeRatio: r.sharpeRatio?.toFixed(3),
          })),
          testedCount: batchItems.length,
          successCount: batchResult.successCount,
          suggestion:
            top3.length > 0
              ? `建议使用「${top3[0]!.label}」策略管理当前持仓，预期年化收益 ${top3[0]!.annualizedReturn?.toFixed(2)}%`
              : "未找到合适策略，建议调整持仓或时间范围后重试",
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

/** Build per-position results from a multi-symbol backtest result. */
function buildPerPositionResults(
  positions: PortfolioPosition[],
  result: BacktestResult & { perSymbol?: Array<{ symbol: string; result: BacktestResult }> },
  strategyId: string,
  strategyName: string,
): PerPositionResult[] {
  if (result.perSymbol) {
    return result.perSymbol.map(({ symbol, result: sr }) => {
      const pos = positions.find((p) => p.symbol === symbol);
      return {
        symbol,
        name: pos?.name ?? symbol,
        quantity: pos?.quantity ?? 0,
        avgCost: pos?.avgCost ?? 0,
        currentValue: (pos?.quantity ?? 0) * (pos?.currentPrice ?? pos?.avgCost ?? 0),
        strategyId,
        strategyName,
        backtestReturn: `${sr.totalReturn.toFixed(2)}%`,
        annualizedReturn: `${sr.annualizedReturn.toFixed(2)}%`,
        maxDrawdown: `${sr.maxDrawdown.toFixed(2)}%`,
        sharpeRatio: sr.sharpeRatio.toFixed(3),
        winRate: `${sr.winRate.toFixed(1)}%`,
        totalTrades: sr.totalTrades,
        finalValue: Math.round(sr.finalValue * 100) / 100,
      };
    });
  }

  // Single-symbol fallback
  const pos = positions[0];
  return [
    {
      symbol: result.symbols[0] ?? "",
      name: pos?.name ?? "",
      quantity: pos?.quantity ?? 0,
      avgCost: pos?.avgCost ?? 0,
      currentValue: (pos?.quantity ?? 0) * (pos?.currentPrice ?? pos?.avgCost ?? 0),
      strategyId,
      strategyName,
      backtestReturn: `${result.totalReturn.toFixed(2)}%`,
      annualizedReturn: `${result.annualizedReturn.toFixed(2)}%`,
      maxDrawdown: `${result.maxDrawdown.toFixed(2)}%`,
      sharpeRatio: result.sharpeRatio.toFixed(3),
      winRate: `${result.winRate.toFixed(1)}%`,
      totalTrades: result.totalTrades,
      finalValue: Math.round(result.finalValue * 100) / 100,
    },
  ];
}
