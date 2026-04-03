/**
 * backtest tool — AI-callable tool for running strategy backtests.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { listStrategies, runBacktest } from "../engine/strategy-registry.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { QuantTradingConfig } from "../types.js";

/** Safe toFixed — returns "N/A" when the value is nullish or not a finite number. */
function fmt(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "N/A";
}

export function createBacktestTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  backtestStore?: BacktestStore,
) {
  return {
    name: "backtest",
    description:
      "运行量化策略回测。" +
      "支持50+内置策略（双均线、动量突破、均值回归、RSI、MACD、布林带等）、用户动态组合策略（通过strategy_composer创建）和 backtrader Python 策略。" +
      "返回年化收益率、最大回撤、夏普比率、胜率等绩效指标，以及前瞻信号分析。" +
      "action=run 运行回测, action=list 列出可用策略。\n" +
      "【重要提示】\n" +
      "• 如果用户要求'明天可以买的股票'，应将 endDate 设为今天，并检查返回的 forwardSignals 字段。\n" +
      "• 不要只用 dual-ma-crossover，先用 action=list 查看所有策略，或使用 strategy_router(action=profile_route_backtest) 自动匹配最优策略。\n" +
      "• 如果某只股票 totalTrades=0，说明该策略不适合这只股票，应换策略重试。\n" +
      "• 回测结果中包含 warnings 字段标记指标异常（如高夏普+高回撤矛盾）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["run", "list"],
          description: "操作: run=运行回测, list=列出策略",
        },
        strategyId: {
          type: "string",
          description: "策略ID (如 dual-ma-crossover)",
        },
        symbols: {
          type: "string",
          description: "标的代码，逗号分隔 (如 600519,000858)",
        },
        startDate: {
          type: "string",
          description: "回测起始日期 YYYY-MM-DD",
        },
        endDate: {
          type: "string",
          description: "回测结束日期 YYYY-MM-DD",
        },
        initialCash: {
          type: "number",
          description: "初始资金，默认 100000",
        },
        engine: {
          type: "string",
          enum: ["builtin", "backtrader"],
          description: "回测引擎，默认使用配置的 backtestEngine",
        },
        fastPeriod: {
          type: "number",
          description: "快速均线周期（双均线策略参数）",
        },
        slowPeriod: {
          type: "number",
          description: "慢速均线周期（双均线策略参数）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "list") {
        const strategies = listStrategies();
        return {
          strategies: strategies.map((s) => ({
            id: s.id,
            name: s.name,
            description: s.description,
            engine: s.engine,
            parameters: s.parameters.map((p) => ({
              name: p.name,
              type: p.type,
              default: p.default,
              description: p.description,
            })),
          })),
        };
      }

      if (action === "run") {
        const strategyId = args.strategyId as string;
        if (!strategyId) return { error: "请提供策略ID (strategyId)" };

        const symbols = ((args.symbols as string) ?? "000300").split(",").map((s) => s.trim());
        const startDate = (args.startDate as string) ?? getDefaultStartDate();
        const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const initialCash = (args.initialCash as number) ?? 100000;

        const parameters: Record<string, unknown> = {};
        if (args.fastPeriod != null) parameters.fastPeriod = args.fastPeriod;
        if (args.slowPeriod != null) parameters.slowPeriod = args.slowPeriod;

        try {
          const result = await runBacktest(
            {
              strategyId,
              symbols,
              startDate,
              endDate,
              initialCash,
              engine: (args.engine as "builtin" | "backtrader") ?? undefined,
              parameters,
            },
            config,
          );

          // Auto-save to backtest history store
          let savedId: string | undefined;
          if (backtestStore) {
            try {
              savedId = await backtestStore.save(result, {
                engine: (args.engine as string) ?? config.backtestEngine ?? "builtin",
              });
            } catch {
              // Best-effort save — don't fail the backtest
            }
          }

          return {
            savedId,
            summary: {
              strategy: strategyId,
              symbols,
              period: `${startDate} ~ ${endDate}`,
              initialCash: `¥${initialCash.toLocaleString()}`,
              finalValue: `¥${fmt(result.finalValue, 2)}`,
              totalReturn: `${fmt(result.totalReturn, 2)}%`,
              annualizedReturn: `${fmt(result.annualizedReturn, 2)}%`,
              maxDrawdown: `${fmt(result.maxDrawdown, 2)}%`,
              sharpeRatio: fmt(result.sharpeRatio, 3),
              winRate: `${fmt(result.winRate, 1)}%`,
              totalTrades: result.totalTrades ?? 0,
            },
            // Per-symbol breakdown for portfolio backtests (multi-symbol)
            perSymbol:
              "perSymbol" in result
                ? (
                    result as { perSymbol: Array<{ symbol: string; result: typeof result }> }
                  ).perSymbol.map(({ symbol, result: sr }) => ({
                    symbol,
                    allocation: `¥${(initialCash / symbols.length).toLocaleString()}`,
                    finalValue: `¥${fmt(sr.finalValue, 2)}`,
                    totalReturn: `${fmt(sr.totalReturn, 2)}%`,
                    annualizedReturn: `${fmt(sr.annualizedReturn, 2)}%`,
                    maxDrawdown: `${fmt(sr.maxDrawdown, 2)}%`,
                    winRate: `${fmt(sr.winRate, 1)}%`,
                    totalTrades: sr.totalTrades ?? 0,
                  }))
                : undefined,
            signals: result.signals.slice(-10).map((s) => ({
              date: new Date(s.timestamp).toISOString().slice(0, 10),
              direction: s.direction,
              symbol: s.symbol,
              price: fmt(s.price, 2),
              reason: s.reason,
            })),

            // ── Forward signal analysis: check if the last signal is a recent buy ──
            forwardSignals: analyzeForwardSignals(result.signals, endDate, symbols),

            // ── Metric sanity warnings ──
            warnings: validateMetrics(result, symbols),
          };
        } catch (err) {
          return { error: `回测失败: ${(err as Error).message}` };
        }
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

function getDefaultStartDate(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

/** Analyze the last signals to determine if there are active buy signals near the end date. */
function analyzeForwardSignals(
  signals: Array<{
    timestamp: number;
    direction: string;
    symbol: string;
    price: number;
    reason?: string;
  }>,
  endDate: string,
  symbols: string[],
): {
  hasBuySignal: boolean;
  summary: string;
  activeSymbols: string[];
  details: Array<{
    symbol: string;
    direction: string;
    date: string;
    price: string;
    reason?: string;
  }>;
} {
  const endTs = new Date(endDate).getTime();
  // Look at signals within the last 5 trading days of the backtest period
  const lookbackMs = 5 * 24 * 60 * 60 * 1000;
  const cutoff = endTs - lookbackMs;

  // Get the last signal for each symbol
  const lastSignalBySymbol = new Map<string, (typeof signals)[number]>();
  for (const s of signals) {
    if (s.timestamp >= cutoff) {
      const existing = lastSignalBySymbol.get(s.symbol);
      if (!existing || s.timestamp > existing.timestamp) {
        lastSignalBySymbol.set(s.symbol, s);
      }
    }
  }

  const activeSymbols: string[] = [];
  const details: Array<{
    symbol: string;
    direction: string;
    date: string;
    price: string;
    reason?: string;
  }> = [];

  for (const sym of symbols) {
    const lastSig = lastSignalBySymbol.get(sym);
    if (lastSig) {
      details.push({
        symbol: sym,
        direction: lastSig.direction,
        date: new Date(lastSig.timestamp).toISOString().slice(0, 10),
        price: fmt(lastSig.price, 2),
        reason: lastSig.reason,
      });
      if (lastSig.direction === "buy") {
        activeSymbols.push(sym);
      }
    }
  }

  const hasBuySignal = activeSymbols.length > 0;
  let summary: string;
  if (activeSymbols.length === 0) {
    summary = "回测期末5个交易日内无活跃买入信号，不建议明天买入这些股票。";
  } else {
    summary = `以下股票在回测期末有买入信号，可考虑明天买入: ${activeSymbols.join(", ")}。请结合实时行情和风险分析综合判断。`;
  }

  return { hasBuySignal, summary, activeSymbols, details };
}

/** Validate backtest metrics for internal consistency and flag anomalies. */
function validateMetrics(
  result: {
    totalReturn: number;
    maxDrawdown: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
  },
  symbols: string[],
): string[] {
  const warnings: string[] = [];

  // Check: high Sharpe ratio with high drawdown is contradictory
  if (Math.abs(result.sharpeRatio ?? 0) > 3 && Math.abs(result.maxDrawdown ?? 0) > 30) {
    warnings.push(
      `指标矛盾: 夏普比率${fmt(result.sharpeRatio, 2)}偏高，但最大回撤${fmt(result.maxDrawdown, 1)}%过大。可能是交易次数过少(${result.totalTrades ?? 0}次)导致统计失真。`,
    );
  }

  // Check: very few trades → unreliable statistics
  if ((result.totalTrades ?? 0) < 10) {
    warnings.push(
      `交易次数过少(${result.totalTrades ?? 0}次)，统计指标(胜率、夏普比率等)可靠性不足，建议延长回测周期或更换策略。`,
    );
  }

  // Check: unrealistically high win rate with few trades → overfitting
  if ((result.winRate ?? 0) >= 100 && (result.totalTrades ?? 0) < 20) {
    warnings.push(
      `100%胜率 + ${result.totalTrades ?? 0}次交易，极可能过拟合，实盘表现可能大幅偏离回测结果。`,
    );
  }

  // Check: zero trades for any symbol in multi-symbol backtest
  if ("perSymbol" in result) {
    const perSym = (
      result as { perSymbol: Array<{ symbol: string; result: { totalTrades: number } }> }
    ).perSymbol;
    if (perSym) {
      for (const ps of perSym) {
        if (ps.result.totalTrades === 0) {
          warnings.push(
            `${ps.symbol} 在回测期间产生0次交易信号，当前策略不适合该标的。建议使用 strategy_router(action=recommend, symbols=${ps.symbol}) 寻找更匹配的策略。`,
          );
        }
      }
    }
  }

  // Check: terrible risk/reward ratio
  if (result.totalReturn > 0 && result.totalReturn < 5 && Math.abs(result.maxDrawdown) > 50) {
    warnings.push(
      `风险收益比极差: 总收益仅${result.totalReturn.toFixed(1)}%但最大回撤达${result.maxDrawdown.toFixed(1)}%。不建议使用此策略。`,
    );
  }

  // Check: negative total return
  if (result.totalReturn < 0) {
    warnings.push(
      `回测总收益为负(${result.totalReturn.toFixed(2)}%)，该策略在此标的/时段上表现不佳。`,
    );
  }

  return warnings;
}
