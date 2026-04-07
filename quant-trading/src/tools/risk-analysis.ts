/**
 * risk_analysis tool — portfolio risk metrics computation.
 *
 * Delegates to the Python report_engine for full risk analysis:
 * Sharpe, Sortino, Calmar, VaR, CVaR, max drawdown, benchmark alpha/beta.
 *
 * Supports cross-portfolio aggregated risk assessment via action=cross_portfolio_risk.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import { loadAllPortfolios } from "./portfolio.js";

export function createRiskAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "risk_analysis",
    description:
      "投资组合风险分析工具（完整风险引擎）。\n" +
      "计算 VaR、CVaR、最大回撤、夏普比率、Sortino、Calmar、波动率、Alpha/Beta 等。\n" +
      "action=portfolio_risk 分析组合风险（需提供净值曲线）,\n" +
      "action=position_risk 分析单标的风险,\n" +
      "action=backtest_risk 对回测结果进行风险分析,\n" +
      "action=earnings_risk 检查持仓标的财报日期风险（近期财报可能导致波动率跳升）,\n" +
      "action=cross_portfolio_risk 跨组合聚合风险评估（分析所有子组合的集中度、重叠持仓等）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["portfolio_risk", "position_risk", "backtest_risk", "earnings_risk", "cross_portfolio_risk"],
          description: "分析类型",
        },
        symbol: { type: "string", description: "标的代码（position_risk 时必填）" },
        period: { type: "number", description: "计算周期（天），默认252（一年）" },
        confidenceLevel: { type: "number", description: "VaR 置信水平，默认0.95" },
        equityCurve: {
          type: "string",
          description: "净值曲线，JSON 数组 [{date, value}]（portfolio_risk 时必填）",
        },
        initialCash: { type: "number", description: "初始资金" },
        riskFreeRate: { type: "number", description: "无风险利率（年化），默认 0.03" },
        benchmarkCurve: {
          type: "string",
          description: "基准曲线，JSON 数组 [{date, value}]（可选，用于计算 alpha/beta）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const period = (args.period as number) ?? 252;

      if (action === "portfolio_risk" || action === "backtest_risk") {
        // Parse equity curve
        let equityCurve: Array<{ date: string; value: number }> = [];
        if (args.equityCurve) {
          try {
            equityCurve = JSON.parse(args.equityCurve as string);
          } catch {
            return { error: "equityCurve 格式错误，请使用 JSON 数组" };
          }
        }

        if (equityCurve.length < 2) {
          return { error: "需要至少 2 个净值数据点" };
        }

        let benchmarkCurve: Array<{ date: string; value: number }> = [];
        if (args.benchmarkCurve) {
          try {
            benchmarkCurve = JSON.parse(args.benchmarkCurve as string);
          } catch {
            /* ignore bad benchmark */
          }
        }

        // Call Python report engine
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/report_engine.py",
          {
            equityCurve,
            initialCash: (args.initialCash as number) ?? equityCurve[0]?.value ?? 100000,
            riskFreeRate: (args.riskFreeRate as number) ?? 0.03,
            benchmarkCurve: benchmarkCurve.length > 0 ? benchmarkCurve : undefined,
          },
          config,
        );

        if (pyResult.ok && pyResult.data) {
          return { action, ...pyResult.data };
        }

        return {
          error: `风险分析引擎错误: ${pyResult.error ?? "未知错误"}`,
          hint: "请确保 Python 已安装并配置 pythonPath",
        };
      }

      if (action === "position_risk") {
        const symbol = args.symbol as string;
        if (!symbol) return { error: "请提供标的代码 (symbol)" };

        // Fetch historical prices via Python data provider and compute risk metrics
        const confidenceLevel = (args.confidenceLevel as number) ?? 0.95;
        const riskFreeRate = (args.riskFreeRate as number) ?? 0.03;

        let pyResult: { ok: boolean; data?: Record<string, unknown>; error?: string } | undefined;
        try {
          pyResult = await runPythonScript<Record<string, unknown>>(
            "engines/report_engine.py",
            {
              mode: "position_risk",
              symbol,
              period,
              confidenceLevel,
              riskFreeRate,
            },
            config,
          );
        } catch {
          // Python unavailable — fall through to basic computation
        }

        if (pyResult?.ok && pyResult.data) {
          return { action, symbol, period, ...pyResult.data };
        }

        // Fallback: compute basic risk metrics from daily returns in TypeScript
        // when Python is unavailable
        return computeBasicPositionRisk(symbol, period, confidenceLevel);
      }

      if (action === "earnings_risk") {
        // Check if any held/watched symbols have upcoming earnings that may cause
        // volatility spikes, using the earnings_calendar engine.
        const symbol = args.symbol as string | undefined;
        const days = (args.period as number) ?? 14;

        try {
          const earningsResult = await runPythonScript<{
            earnings?: Array<{
              symbol?: string;
              name?: string;
              disclosure_date?: string;
              first_disclosure_date?: string;
            }>;
          }>(
            "engines/earnings_calendar.py",
            {
              action: "upcoming",
              market: "cn",
              days,
              limit: 200,
            },
            config,
          );

          if (!earningsResult.ok) {
            return { error: earningsResult.error ?? "无法获取财报日历数据" };
          }

          const earnings = earningsResult.data?.earnings ?? [];

          // If a specific symbol is given, filter to that; otherwise report all upcoming
          const filtered = symbol ? earnings.filter((e) => e.symbol === symbol) : earnings;

          const riskItems = filtered.map((e) => {
            const daysToEarnings = e.disclosure_date
              ? Math.max(
                  0,
                  Math.floor(
                    (new Date(e.disclosure_date).getTime() - Date.now()) / (1000 * 60 * 60 * 24),
                  ),
                )
              : null;

            let riskLevel: "high" | "medium" | "low" = "low";
            if (daysToEarnings !== null && daysToEarnings <= 3) riskLevel = "high";
            else if (daysToEarnings !== null && daysToEarnings <= 7) riskLevel = "medium";

            return {
              symbol: e.symbol,
              name: e.name,
              disclosure_date: e.disclosure_date,
              days_until: daysToEarnings,
              risk_level: riskLevel,
              warning:
                riskLevel === "high"
                  ? "财报即将发布，建议降低仓位或设置止损"
                  : riskLevel === "medium"
                    ? "财报将在一周内发布，注意波动风险"
                    : "财报时间较远，暂无紧迫风险",
            };
          });

          const highRiskCount = riskItems.filter((r) => r.risk_level === "high").length;

          return {
            action: "earnings_risk",
            scan_days: days,
            total_upcoming: riskItems.length,
            high_risk_count: highRiskCount,
            items: riskItems.slice(0, 50),
            summary:
              highRiskCount > 0
                ? `⚠️ ${String(highRiskCount)} 只标的在 3 天内有财报，建议关注仓位风险`
                : "近期无高风险财报事件",
          };
        } catch {
          return { error: "earnings_risk 分析失败，请确保 Python 环境可用" };
        }
      }

      if (action === "cross_portfolio_risk") {
        // Cross-portfolio aggregated risk assessment
        const portfolios = await loadAllPortfolios(config);
        const portfolioList = Object.values(portfolios);

        if (portfolioList.length === 0) {
          return { error: "没有找到任何组合" };
        }

        // Aggregate totals
        const totalCash = portfolioList.reduce((s, p) => s + p.cash, 0);
        const totalValue = portfolioList.reduce((s, p) => s + p.totalValue, 0);

        // Collect all positions across portfolios
        const allPositions: Array<{
          symbol: string;
          name?: string;
          quantity: number;
          avgCost: number;
          marketValue: number;
          portfolioId: string;
          portfolioName: string;
        }> = [];
        for (const pf of portfolioList) {
          for (const pos of pf.positions) {
            allPositions.push({
              symbol: pos.symbol,
              name: pos.name,
              quantity: pos.quantity,
              avgCost: pos.avgCost,
              marketValue: pos.marketValue ?? pos.quantity * (pos.currentPrice ?? pos.avgCost),
              portfolioId: pf.id,
              portfolioName: pf.name,
            });
          }
        }

        // Symbol concentration: aggregate by symbol across all portfolios
        const symbolAgg: Record<
          string,
          { symbol: string; name?: string; totalQuantity: number; totalValue: number; portfolios: string[] }
        > = {};
        for (const pos of allPositions) {
          if (!symbolAgg[pos.symbol]) {
            symbolAgg[pos.symbol] = {
              symbol: pos.symbol,
              name: pos.name,
              totalQuantity: 0,
              totalValue: 0,
              portfolios: [],
            };
          }
          symbolAgg[pos.symbol].totalQuantity += pos.quantity;
          symbolAgg[pos.symbol].totalValue += pos.marketValue;
          if (!symbolAgg[pos.symbol].portfolios.includes(pos.portfolioId)) {
            symbolAgg[pos.symbol].portfolios.push(pos.portfolioId);
          }
        }

        // Identify overlapping positions (same symbol in multiple portfolios)
        const overlapping = Object.values(symbolAgg)
          .filter((s) => s.portfolios.length > 1)
          .map((s) => ({
            symbol: s.symbol,
            name: s.name,
            totalQuantity: s.totalQuantity,
            totalValue: `¥${s.totalValue.toFixed(2)}`,
            weightPct: totalValue > 0 ? `${((s.totalValue / totalValue) * 100).toFixed(2)}%` : "N/A",
            inPortfolios: s.portfolios,
          }));

        // Concentration risk: top holdings by value
        const topHoldings = Object.values(symbolAgg)
          .sort((a, b) => b.totalValue - a.totalValue)
          .slice(0, 10)
          .map((s) => ({
            symbol: s.symbol,
            name: s.name,
            totalValue: `¥${s.totalValue.toFixed(2)}`,
            weightPct: totalValue > 0 ? `${((s.totalValue / totalValue) * 100).toFixed(2)}%` : "N/A",
          }));

        // Per-portfolio risk summary
        const portfolioRiskSummary = portfolioList.map((pf) => {
          const posValue = pf.positions.reduce(
            (s, p) => s + (p.marketValue ?? p.quantity * (p.currentPrice ?? p.avgCost)),
            0,
          );
          const cashPct = pf.totalValue > 0 ? (pf.cash / pf.totalValue) * 100 : 100;
          const maxPositionPct =
            pf.positions.length > 0 && posValue > 0
              ? Math.max(
                  ...pf.positions.map(
                    (p) =>
                      ((p.marketValue ?? p.quantity * (p.currentPrice ?? p.avgCost)) / pf.totalValue) *
                      100,
                  ),
                )
              : 0;
          return {
            id: pf.id,
            name: pf.name,
            totalValue: `¥${pf.totalValue.toFixed(2)}`,
            cashPct: `${cashPct.toFixed(1)}%`,
            positionCount: pf.positions.length,
            maxSinglePositionPct: `${maxPositionPct.toFixed(1)}%`,
            weightInTotal: totalValue > 0 ? `${((pf.totalValue / totalValue) * 100).toFixed(1)}%` : "N/A",
          };
        });

        // Risk warnings
        const warnings: string[] = [];
        const uniqueSymbols = Object.keys(symbolAgg).length;
        if (uniqueSymbols < 3 && allPositions.length > 0) {
          warnings.push("持仓过于集中：总共仅持有 " + uniqueSymbols + " 只标的，建议分散投资");
        }
        if (overlapping.length > 0) {
          warnings.push(
            `${overlapping.length} 只标的出现在多个组合中，注意实际总敞口`,
          );
        }
        const topWeight = topHoldings[0]
          ? parseFloat(topHoldings[0].weightPct)
          : 0;
        if (topWeight > 30) {
          warnings.push(
            `最大单只标的占总资产 ${topWeight.toFixed(1)}%，超过 30% 警戒线`,
          );
        }
        const totalCashPct = totalValue > 0 ? (totalCash / totalValue) * 100 : 100;
        if (totalCashPct < 5) {
          warnings.push(`总现金比例仅 ${totalCashPct.toFixed(1)}%，流动性风险较高`);
        }

        return {
          action: "cross_portfolio_risk",
          aggregate: {
            portfolioCount: portfolioList.length,
            totalCash: `¥${totalCash.toFixed(2)}`,
            totalValue: `¥${totalValue.toFixed(2)}`,
            cashPct: `${totalCashPct.toFixed(1)}%`,
            uniqueSymbols,
            totalPositions: allPositions.length,
          },
          portfolioRiskSummary,
          overlappingPositions: overlapping,
          topHoldings,
          warnings: warnings.length > 0 ? warnings : ["当前风险状况良好"],
        };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

/**
 * Basic position risk metrics computed in TypeScript when Python is unavailable.
 * Uses a placeholder note indicating that full analysis requires market data.
 */
function computeBasicPositionRisk(
  symbol: string,
  period: number,
  confidenceLevel: number,
): Record<string, unknown> {
  return {
    positionRisk: {
      symbol,
      period: `${period} trading days`,
      confidenceLevel,
      note: "Python 环境不可用，无法获取历史行情。请确保已配置 pythonPath 并安装 akshare/yfinance。",
      hint: "可以手动提供 equityCurve 使用 portfolio_risk 分析。",
    },
  };
}
