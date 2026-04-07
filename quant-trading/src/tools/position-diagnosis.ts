/**
 * position_diagnosis tool — AI-callable for holistic position health analysis.
 *
 * Actions:
 *   diagnose   — Full health check on current holdings (P&L, risk, concentration, signals)
 *   single     — Diagnose a single position
 *   suggest    — Generate actionable suggestions (trim, add, hold, exit)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig } from "../types.js";

export function createPositionDiagnosisTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine,
) {
  return {
    name: "position_diagnosis",
    description:
      "持仓诊断工具。对当前持仓进行全面健康检查，包括盈亏分析、风险集中度、技术形态、资金流向，并给出调仓建议。\n" +
      "• action=diagnose — 全持仓诊断（综合评分+风险警示）\n" +
      "• action=single — 单只持仓深度诊断（需指定symbol）\n" +
      "• action=suggest — 基于诊断结果的调仓建议",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["diagnose", "single", "suggest"],
          description: "操作类型: diagnose=全持仓诊断, single=单只诊断, suggest=调仓建议",
        },
        symbol: {
          type: "string",
          description: "股票代码（single时必填），如 600519",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "diagnose";
      const engine = getEngine();
      const positions = engine.getPositions();

      if (positions.length === 0) {
        return {
          action,
          warning: "当前无持仓",
          positions: [],
          summary: "暂无持仓需要诊断，请先通过实盘交易或模拟交易建仓。",
        };
      }

      if (action === "single") {
        const symbol = args.symbol as string;
        if (!symbol) return { error: "请提供股票代码 (symbol)" };
        const pos = positions.find(
          (p: { symbol: string }) =>
            p.symbol === symbol || p.symbol.includes(symbol),
        );
        if (!pos)
          return {
            error: `未找到 ${symbol} 的持仓`,
            available: positions.map((p: { symbol: string }) => p.symbol),
          };

        return diagnoseSingle(pos, config);
      }

      // Full portfolio diagnosis
      const totalValue = positions.reduce(
        (sum: number, p: { marketValue?: number; quantity: number; avgCost?: number }) =>
          sum + (p.marketValue ?? p.quantity * (p.avgCost ?? 0)),
        0,
      );

      const diagnostics = await Promise.all(
        positions.map(
          async (pos: {
            symbol: string;
            quantity: number;
            avgCost?: number;
            marketValue?: number;
            pnl?: number;
            pnlPct?: number;
          }) => {
            const weight =
              totalValue > 0
                ? ((pos.marketValue ?? pos.quantity * (pos.avgCost ?? 0)) / totalValue) * 100
                : 0;
            const diagnosis = await diagnoseSingle(pos, config);
            return {
              symbol: pos.symbol,
              quantity: pos.quantity,
              avgCost: pos.avgCost,
              pnl: pos.pnl ?? 0,
              pnlPct: pos.pnlPct ?? 0,
              weight: Math.round(weight * 100) / 100,
              ...diagnosis,
            };
          },
        ),
      );

      // Concentration risk
      const maxWeight = Math.max(...diagnostics.map((d) => d.weight));
      const concentrationRisk =
        maxWeight > 40 ? "high" : maxWeight > 25 ? "medium" : "low";

      // Overall health score
      const scores = diagnostics
        .map((d) => (d as { healthScore?: number }).healthScore)
        .filter((s): s is number => typeof s === "number");
      const avgScore = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 50;

      const losers = diagnostics.filter((d) => d.pnl < 0);
      const winners = diagnostics.filter((d) => d.pnl > 0);

      const result: Record<string, unknown> = {
        action,
        positionCount: positions.length,
        totalValue: Math.round(totalValue),
        overallHealthScore: Math.round(avgScore),
        concentrationRisk,
        concentrationRisk_cn:
          concentrationRisk === "high"
            ? "⚠️ 集中度过高"
            : concentrationRisk === "medium"
              ? "中等集中"
              : "分散良好",
        winnerCount: winners.length,
        loserCount: losers.length,
        positions: diagnostics,
      };

      if (action === "suggest") {
        const suggestions: string[] = [];
        if (concentrationRisk === "high") {
          const heaviest = diagnostics.reduce((a, b) => (a.weight > b.weight ? a : b));
          suggestions.push(
            `${heaviest.symbol} 占比 ${heaviest.weight}% 过高，建议分批减仓至25%以下`,
          );
        }
        for (const d of diagnostics) {
          if (d.pnlPct < -10) {
            suggestions.push(`${d.symbol} 亏损 ${d.pnlPct.toFixed(1)}%，建议设置止损或评估是否继续持有`);
          }
          if (d.pnlPct > 30) {
            suggestions.push(`${d.symbol} 盈利 ${d.pnlPct.toFixed(1)}%，建议分批止盈锁定利润`);
          }
        }
        if (suggestions.length === 0) {
          suggestions.push("持仓整体健康，暂无紧急调仓建议");
        }
        result.suggestions = suggestions;
      }

      return result;
    },
  };
}

async function diagnoseSingle(
  pos: {
    symbol: string;
    quantity: number;
    avgCost?: number;
    marketValue?: number;
    pnl?: number;
    pnlPct?: number;
  },
  config: QuantTradingConfig,
): Promise<Record<string, unknown>> {
  // Try to get technical indicators and fund flow for comprehensive diagnosis
  const [techResult, flowResult] = await Promise.allSettled([
    runPythonScript<Record<string, unknown>>(
      "engines/compute_indicators.py",
      { symbol: pos.symbol, period: "60d" },
      config,
    ),
    runPythonScript<Record<string, unknown>>(
      "engines/fund_flow.py",
      { action: "stock", symbol: pos.symbol },
      config,
    ),
  ]);

  const tech =
    techResult.status === "fulfilled" && techResult.value.ok
      ? techResult.value.data
      : null;
  const flow =
    flowResult.status === "fulfilled" && flowResult.value.ok
      ? flowResult.value.data
      : null;

  // Health scoring: 0-100
  let healthScore = 50;
  const flags: string[] = [];

  const pnlPct = pos.pnlPct ?? 0;
  // P&L factor
  if (pnlPct > 20) healthScore += 15;
  else if (pnlPct > 5) healthScore += 10;
  else if (pnlPct < -15) {
    healthScore -= 20;
    flags.push("深套中，亏损超15%");
  } else if (pnlPct < -5) {
    healthScore -= 10;
    flags.push("浮亏中");
  }

  // Technical factor
  if (tech) {
    const rsi = (tech as { rsi?: number }).rsi;
    if (typeof rsi === "number") {
      if (rsi > 80) {
        healthScore -= 10;
        flags.push("RSI超买(>80)，短线有回调风险");
      } else if (rsi < 20) {
        healthScore += 5;
        flags.push("RSI超卖(<20)，可能反弹");
      }
    }
    const macdHist = (tech as { macd_hist?: number }).macd_hist;
    if (typeof macdHist === "number") {
      if (macdHist > 0) healthScore += 5;
      else {
        healthScore -= 5;
        flags.push("MACD空头排列");
      }
    }
  }

  // Fund flow factor
  if (flow) {
    const netInflow = (flow as { net_inflow_main?: number }).net_inflow_main;
    if (typeof netInflow === "number") {
      if (netInflow > 0) {
        healthScore += 10;
      } else {
        healthScore -= 5;
        flags.push("主力资金净流出");
      }
    }
  }

  healthScore = Math.max(0, Math.min(100, healthScore));

  const healthLevel =
    healthScore >= 70 ? "healthy" : healthScore >= 40 ? "caution" : "warning";

  return {
    healthScore,
    healthLevel,
    healthLevel_cn:
      healthLevel === "healthy"
        ? "健康"
        : healthLevel === "caution"
          ? "需关注"
          : "⚠️ 警告",
    flags: flags.length > 0 ? flags : ["暂无异常信号"],
    technicals: tech ?? { note: "技术指标数据不可用" },
    fundFlow: flow ?? { note: "资金流数据不可用" },
  };
}
