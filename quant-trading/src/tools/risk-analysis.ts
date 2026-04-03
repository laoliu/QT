/**
 * risk_analysis tool — portfolio risk metrics computation.
 *
 * Delegates to the Python report_engine for full risk analysis:
 * Sharpe, Sortino, Calmar, VaR, CVaR, max drawdown, benchmark alpha/beta.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createRiskAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "risk_analysis",
    description:
      "投资组合风险分析工具（完整风险引擎）。\n" +
      "计算 VaR、CVaR、最大回撤、夏普比率、Sortino、Calmar、波动率、Alpha/Beta 等。\n" +
      "action=portfolio_risk 分析组合风险（需提供净值曲线）,\n" +
      "action=position_risk 分析单标的风险,\n" +
      "action=backtest_risk 对回测结果进行风险分析,\n" +
      "action=earnings_risk 检查持仓标的财报日期风险（近期财报可能导致波动率跳升）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["portfolio_risk", "position_risk", "backtest_risk", "earnings_risk"],
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
