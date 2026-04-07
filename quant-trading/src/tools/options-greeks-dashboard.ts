/**
 * options_greeks_dashboard tool — AI-callable tool for real-time portfolio Greeks
 * dashboard, stress testing, VaR calculation, and hedge suggestions.
 *
 * Use cases for options traders:
 *   - Real-time portfolio Greeks overview (including cash/dollar Greeks)
 *   - Stress testing (predefined + custom scenarios)
 *   - Portfolio VaR / CVaR calculation
 *   - Pre-trade Greeks impact assessment
 *   - Auto-hedge suggestions (delta/vega/gamma neutral)
 *
 * Delegates computation to options_stress_test.py via Python bridge.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";

export function createOptionsGreeksDashboardTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  return {
    name: "options_greeks_dashboard",
    description:
      "期权Greeks仪表盘与风控工具：实时组合Greeks概览、压力测试、VaR计算、盘前风控、对冲建议。\n" +
      "适用于期权交易员的日常风控管理。\n\n" +
      "action=dashboard 组合Greeks仪表盘（含现金Greeks），\n" +
      "action=stress_test 压力测试（市场崩盘/波动率飙升/闪崩等场景），\n" +
      "action=var 组合VaR/CVaR计算，\n" +
      "action=pretrade 盘前/下单前Greeks影响评估，\n" +
      "action=hedge 对冲建议（Delta/Vega/Gamma中性）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["dashboard", "stress_test", "var", "pretrade", "hedge"],
          description:
            "操作类型: dashboard=Greeks仪表盘, stress_test=压力测试, " +
            "var=VaR计算, pretrade=下单前评估, hedge=对冲建议",
        },
        // ── stress_test fields ────────────────────────────────
        scenarios: {
          type: "string",
          description:
            '压力测试场景名称列表(JSON数组)，可选: "market_crash"(市场崩盘), ' +
            '"flash_crash"(闪崩), "vol_explosion"(波动率爆发), "vol_crush"(波动率坍缩), ' +
            '"rate_hike"(加息冲击), "time_decay_weekend"(周末时间衰减), ' +
            '"rally"(大幅上涨), "gamma_squeeze"(Gamma逼空)。不填则运行所有场景。',
        },
        // ── var fields ────────────────────────────────────────
        confidence: {
          type: "number",
          description: "VaR置信水平，默认0.95（95%）",
        },
        horizonDays: {
          type: "number",
          description: "VaR时间窗口（天），默认1",
        },
        // ── pretrade fields ───────────────────────────────────
        symbol: {
          type: "string",
          description: "待下单期权合约代码（pretrade评估用）",
        },
        optionType: {
          type: "string",
          enum: ["call", "put"],
          description: "期权类型（pretrade评估用）",
        },
        strike: {
          type: "number",
          description: "行权价（pretrade评估用）",
        },
        spotPrice: {
          type: "number",
          description: "标的现价（pretrade评估用）",
        },
        expiry: {
          type: "string",
          description: "到期日YYYY-MM-DD（pretrade评估用）",
        },
        quantity: {
          type: "number",
          description: "合约张数（正=买入，负=卖出）（pretrade评估用）",
        },
        volatility: {
          type: "number",
          description: "隐含波动率（pretrade评估用）",
        },
        underlying: {
          type: "string",
          description: "标的代码",
        },
        // ── hedge fields ──────────────────────────────────────
        hedgeTargets: {
          type: "string",
          description:
            '对冲目标(JSON对象)，如 {"delta":0,"vega":0}。默认Delta和Vega中性。',
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      // Gather current option positions from live engine
      const engine = getEngine();
      const allPositions = engine?.getPositions() ?? [];
      const optionPositions = allPositions.filter(isOptionPosition);

      const r = config.optionsConfig?.riskFreeRate ?? 0.02;
      const q = config.optionsConfig?.dividendYield ?? 0;

      // Enrich positions with spot prices for the Python engine
      const enrichedPositions = enrichPositions(optionPositions, allPositions);

      switch (action) {
        // ── Portfolio Greeks Dashboard ────────────────────────
        case "dashboard": {
          if (enrichedPositions.length === 0) {
            return { message: "当前无期权持仓", count: 0 };
          }

          const result = await runPythonScript<{
            portfolioGreeks: Record<string, number>;
            positions: Array<Record<string, unknown>>;
            positionCount: number;
          }>("engines/options_stress_test.py", {
            action: "portfolio_greeks",
            positions: enrichedPositions,
            riskFreeRate: r,
            dividendYield: q,
          }, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "Greeks计算失败" };
          }

          const d = result.data;
          return {
            portfolioGreeks: formatPortfolioGreeks(d.portfolioGreeks),
            positionCount: d.positionCount,
            positions: d.positions.map(formatPositionGreeks),
          };
        }

        // ── Stress Testing ───────────────────────────────────
        case "stress_test": {
          if (enrichedPositions.length === 0) {
            return { message: "当前无期权持仓，无法进行压力测试" };
          }

          let scenarios: string[] | undefined;
          try {
            if (args.scenarios) scenarios = JSON.parse(args.scenarios as string);
          } catch {
            return { error: "scenarios参数格式错误，请提供JSON数组" };
          }

          const result = await runPythonScript<{
            basePortfolioValue: number;
            worstScenario: string;
            worstPnl: number;
            scenarios: Array<{
              scenario: string;
              description: string;
              totalPnl: number;
              totalPnlPct: number;
              baseValue: number;
              stressedValue: number;
              positionImpacts: Array<Record<string, unknown>>;
              worstPosition: string | null;
            }>;
          }>("engines/options_stress_test.py", {
            action: "stress_test",
            positions: enrichedPositions,
            scenarios,
            riskFreeRate: r,
            dividendYield: q,
          }, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "压力测试失败" };
          }

          const st = result.data;
          return {
            basePortfolioValue: `¥${st.basePortfolioValue.toFixed(2)}`,
            worstScenario: st.worstScenario,
            worstPnl: `¥${st.worstPnl.toFixed(2)}`,
            scenarios: st.scenarios.map((s) => ({
              scenario: s.scenario,
              description: s.description,
              totalPnl: `¥${s.totalPnl.toFixed(2)}`,
              totalPnlPct: `${s.totalPnlPct.toFixed(1)}%`,
              worstPosition: s.worstPosition,
              positionImpacts: s.positionImpacts.slice(0, 5).map((p) => ({
                symbol: p.symbol,
                pnlImpact: `¥${(p.pnlImpact as number).toFixed(2)}`,
                pnlPct: `${(p.pnlPct as number).toFixed(1)}%`,
              })),
            })),
          };
        }

        // ── Portfolio VaR ────────────────────────────────────
        case "var": {
          if (enrichedPositions.length === 0) {
            return { message: "当前无期权持仓，无法计算VaR" };
          }

          const confidence = (args.confidence as number) ?? 0.95;
          const horizonDays = (args.horizonDays as number) ?? 1;

          const result = await runPythonScript<{
            var: number;
            cvar: number;
            confidence: number;
            horizonDays: number;
            meanPnl: number;
            stdPnl: number;
            maxLoss: number;
            maxGain: number;
            percentiles: Record<string, number>;
            positionCount: number;
          }>("engines/options_stress_test.py", {
            action: "var",
            positions: enrichedPositions,
            confidence,
            horizonDays,
            riskFreeRate: r,
            dividendYield: q,
          }, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "VaR计算失败" };
          }

          const v = result.data;
          return {
            var: `¥${v.var.toFixed(2)}`,
            cvar: `¥${v.cvar.toFixed(2)}`,
            confidence: `${(v.confidence * 100).toFixed(0)}%`,
            horizonDays: v.horizonDays,
            interpretation: `在${(v.confidence * 100).toFixed(0)}%置信水平下，` +
              `未来${v.horizonDays}天最大潜在损失为 ¥${v.var.toFixed(2)}`,
            statistics: {
              meanPnl: `¥${v.meanPnl.toFixed(2)}`,
              stdPnl: `¥${v.stdPnl.toFixed(2)}`,
              maxLoss: `¥${v.maxLoss.toFixed(2)}`,
              maxGain: `¥${v.maxGain.toFixed(2)}`,
            },
            percentiles: v.percentiles,
            positionCount: v.positionCount,
          };
        }

        // ── Pre-Trade Check ──────────────────────────────────
        case "pretrade": {
          const symbol = args.symbol as string;
          const optType = args.optionType as string;
          const strike = args.strike as number;
          const spot = args.spotPrice as number;
          const qty = args.quantity as number;
          const expiry = args.expiry as string;
          const vol = (args.volatility as number) ?? 0.25;

          if (!optType || !strike || !spot || !qty) {
            return {
              error: "盘前评估需要: optionType, strike, spotPrice, quantity",
            };
          }

          const monitorCfg = config.optionsConfig?.monitor;
          const limits: Record<string, number> = {};
          if (monitorCfg) {
            if (monitorCfg.maxDelta) limits.maxDelta = monitorCfg.maxDelta;
            if (monitorCfg.maxGamma) limits.maxGamma = monitorCfg.maxGamma;
            if (monitorCfg.maxVega) limits.maxVega = monitorCfg.maxVega;
            if (monitorCfg.maxTheta) limits.maxTheta = monitorCfg.maxTheta;
          }

          const result = await runPythonScript<{
            orderGreeks: Record<string, number>;
            before: Record<string, number>;
            after: Record<string, number>;
            marginEstimate: number | null;
            breaches: string[] | null;
            warnings: string[] | null;
            approved: boolean;
          }>("engines/options_stress_test.py", {
            action: "pretrade_check",
            positions: enrichedPositions,
            newOrder: {
              symbol: symbol ?? "",
              optionType: optType,
              strike,
              spotPrice: spot,
              quantity: qty,
              expiry: expiry ?? "",
              volatility: vol,
              underlying: args.underlying ?? "",
              multiplier: config.optionsConfig?.defaultMultiplier ?? 10000,
            },
            limits,
            riskFreeRate: r,
            dividendYield: q,
          }, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "盘前评估失败" };
          }

          const pt = result.data;
          return {
            approved: pt.approved,
            orderGreeks: {
              delta: pt.orderGreeks.delta?.toFixed(4),
              gamma: pt.orderGreeks.gamma?.toFixed(6),
              theta: pt.orderGreeks.theta?.toFixed(4),
              vega: pt.orderGreeks.vega?.toFixed(4),
              cashDelta: `¥${pt.orderGreeks.cashDelta?.toFixed(0)}`,
              cashVega: `¥${pt.orderGreeks.cashVega?.toFixed(0)}`,
              theoreticalPrice: pt.orderGreeks.theoreticalPrice?.toFixed(6),
            },
            greeksComparison: {
              before: formatGreeksCompact(pt.before),
              after: formatGreeksCompact(pt.after),
            },
            marginEstimate: pt.marginEstimate ? `¥${pt.marginEstimate.toFixed(2)}` : "无（买方无保证金）",
            breaches: pt.breaches,
            warnings: pt.warnings,
          };
        }

        // ── Hedge Suggestions ────────────────────────────────
        case "hedge": {
          if (enrichedPositions.length === 0) {
            return { message: "当前无期权持仓，无需对冲" };
          }

          let hedgeTargets: Record<string, number> | undefined;
          try {
            if (args.hedgeTargets) hedgeTargets = JSON.parse(args.hedgeTargets as string);
          } catch {
            return { error: "hedgeTargets参数格式错误" };
          }

          const result = await runPythonScript<{
            currentGreeks: Record<string, number>;
            hedgeTargets: Record<string, number>;
            suggestions: Array<{
              type: string;
              description: string;
              instrument?: string;
              quantity?: number;
              greeksImpact?: Record<string, unknown>;
              priority: string;
              note?: string;
            }>;
            suggestionCount: number;
          }>("engines/options_stress_test.py", {
            action: "hedge_suggestions",
            positions: enrichedPositions,
            hedgeTargets,
            riskFreeRate: r,
            dividendYield: q,
          }, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "对冲建议计算失败" };
          }

          const h = result.data;
          return {
            currentGreeks: formatPortfolioGreeks(h.currentGreeks),
            hedgeTargets: h.hedgeTargets,
            suggestions: h.suggestions,
            suggestionCount: h.suggestionCount,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Helpers ──────────────────────────────────────────────────────

function isOptionPosition(p: LivePosition): boolean {
  return !!(p.strike || p.optionType || p.expiry);
}

/** Enrich option positions with spot prices from underlying positions */
function enrichPositions(
  optionPositions: LivePosition[],
  allPositions: LivePosition[],
): Array<Record<string, unknown>> {
  return optionPositions.map((p) => {
    const spotPrice = p.underlying
      ? allPositions.find((pos) => pos.symbol === p.underlying)?.currentPrice ?? p.currentPrice
      : p.currentPrice;

    return {
      symbol: p.symbol,
      quantity: p.quantity,
      optionType: p.optionType ?? "call",
      strike: p.strike ?? 0,
      expiry: p.expiry ?? "",
      underlying: p.underlying ?? "",
      spotPrice: spotPrice ?? 0,
      volatility: p.greeks?.impliedVol ?? 0.25,
      multiplier: 10000, // default for ETF options
      avgCost: p.avgCost ?? 0,
      currentPrice: p.currentPrice ?? 0,
    };
  });
}

function formatPortfolioGreeks(g: Record<string, number>): Record<string, string> {
  return {
    delta: g.delta?.toFixed(4),
    gamma: g.gamma?.toFixed(6),
    theta: `${g.theta?.toFixed(4)} (每天)`,
    vega: g.vega?.toFixed(4),
    rho: g.rho?.toFixed(4),
    cashDelta: `¥${g.cashDelta?.toFixed(0)}`,
    cashGamma: `¥${g.cashGamma?.toFixed(0)} (1%波动)`,
    cashTheta: `¥${g.cashTheta?.toFixed(0)} (每天)`,
    cashVega: `¥${g.cashVega?.toFixed(0)} (1%IV变动)`,
  };
}

function formatPositionGreeks(p: Record<string, unknown>): Record<string, unknown> {
  return {
    symbol: p.symbol,
    underlying: p.underlying,
    optionType: p.optionType,
    strike: p.strike,
    expiry: p.expiry,
    dte: p.dte,
    quantity: p.quantity,
    delta: (p.delta as number)?.toFixed(4),
    gamma: (p.gamma as number)?.toFixed(6),
    theta: (p.theta as number)?.toFixed(4),
    vega: (p.vega as number)?.toFixed(4),
    iv: `${((p.iv as number) * 100).toFixed(1)}%`,
    cashDelta: `¥${(p.cashDelta as number)?.toFixed(0)}`,
    cashTheta: `¥${(p.cashTheta as number)?.toFixed(0)}`,
    marketValue: `¥${(p.marketValue as number)?.toFixed(2)}`,
    moneyness: p.moneyness,
  };
}

function formatGreeksCompact(g: Record<string, number>): Record<string, string> {
  return {
    delta: g.delta?.toFixed(4),
    gamma: g.gamma?.toFixed(6),
    theta: g.theta?.toFixed(4),
    vega: g.vega?.toFixed(4),
    cashDelta: `¥${g.cashDelta?.toFixed(0)}`,
  };
}
