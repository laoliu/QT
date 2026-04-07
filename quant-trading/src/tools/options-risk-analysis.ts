/**
 * options_risk_analysis tool — AI-callable tool for advanced option risk analytics.
 *
 * Provides:
 *   - Greeks what-if / sensitivity analysis (spot/vol/time grid)
 *   - P&L attribution (Greeks-based decomposition)
 *   - Margin estimation for option positions
 *   - Second-order Greeks (Charm, Vanna, Vomma)
 *
 * Delegates heavy computation to greeks_engine.py via Python bridge.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createOptionsRiskAnalysisTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "options_risk_analysis",
    description:
      "\u671F\u6743\u98CE\u9669\u5206\u6790\u5DE5\u5177\uFF1A\u5E0C\u814A\u503C\u654F\u611F\u5EA6\u5206\u6790(what-if)\u3001P&L\u5F52\u56E0\u5206\u89E3\u3001\u4FDD\u8BC1\u91D1\u4F30\u7B97\u3002\n" +
      "\u9002\u7528\u4E8E\u671F\u6743\u4EA4\u6613\u5458\u7684\u65E5\u5E38\u98CE\u63A7\u548C\u5BF9\u51B2\u51B3\u7B56\u3002\n\n" +
      "action=what_if \u5E0C\u814A\u503C\u654F\u611F\u5EA6\u5206\u6790\uFF08\u6807\u7684\u4EF7/\u6CE2\u52A8\u7387/\u65F6\u95F4\u53D8\u52A8\u4E0B\u7684Greeks\u53D8\u5316\uFF09\uFF0C\n" +
      "action=pnl_explain P&L\u5F52\u56E0\u5206\u89E3\uFF08Delta/Gamma/Theta/Vega\u8D21\u732E\uFF09\uFF0C\n" +
      "action=margin \u4FDD\u8BC1\u91D1\u4F30\u7B97\uFF08\u5356\u51FA\u671F\u6743\u4FDD\u8BC1\u91D1\u8BA1\u7B97\uFF09\u3002",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["what_if", "pnl_explain", "margin"],
          description:
            "\u64CD\u4F5C\u7C7B\u578B: what_if=\u654F\u611F\u5EA6\u5206\u6790, pnl_explain=P&L\u5F52\u56E0, margin=\u4FDD\u8BC1\u91D1\u4F30\u7B97",
        },
        // ── Shared fields ──────────────────────────────────────
        spotPrice: {
          type: "number",
          description: "\u6807\u7684\u73B0\u4EF7",
        },
        strike: {
          type: "number",
          description: "\u884C\u6743\u4EF7",
        },
        optionType: {
          type: "string",
          enum: ["call", "put"],
          description: "\u671F\u6743\u7C7B\u578B",
        },
        volatility: {
          type: "number",
          description: "\u6CE2\u52A8\u7387\uFF080.25\u8868\u793A25%\uFF09",
        },
        expiry: {
          type: "string",
          description: "\u5230\u671F\u65E5 YYYY-MM-DD",
        },
        daysToExpiry: {
          type: "number",
          description: "\u5269\u4F59\u5929\u6570\uFF08\u4E0Eexpiry\u4E8C\u9009\u4E00\uFF09",
        },
        // ── what_if fields ─────────────────────────────────────
        spotBumpsPct: {
          type: "string",
          description:
            "\u6807\u7684\u4EF7\u683C\u53D8\u52A8\u767E\u5206\u6BD4\u5217\u8868\uFF08JSON\u6570\u7EC4\uFF09\uFF0C\u5982 [-10,-5,0,5,10]\uFF0C\u9ED8\u8BA4 [-10,-5,-2,-1,0,1,2,5,10]",
        },
        volBumps: {
          type: "string",
          description:
            "\u6CE2\u52A8\u7387\u53D8\u52A8\u5217\u8868\uFF08JSON\u6570\u7EC4\uFF09\uFF0C\u5982 [-0.05,0,0.05]\uFF0C\u9ED8\u8BA4 [-0.05,0,0.05]",
        },
        timeBumpsDays: {
          type: "string",
          description: "\u65F6\u95F4\u53D8\u52A8\u5929\u6570\u5217\u8868\uFF08JSON\u6570\u7EC4\uFF09\uFF0C\u5982 [0,7,14]\uFF0C\u9ED8\u8BA4 [0,7,14]",
        },
        // ── pnl_explain fields ─────────────────────────────────
        spotPriceOld: {
          type: "number",
          description: "\u524D\u4E00\u65F6\u523B\u6807\u7684\u4EF7\u683C\uFF08P&L\u5F52\u56E0\u7528\uFF09",
        },
        spotPriceNew: {
          type: "number",
          description: "\u5F53\u524D\u6807\u7684\u4EF7\u683C\uFF08P&L\u5F52\u56E0\u7528\uFF09",
        },
        volOld: {
          type: "number",
          description: "\u524D\u4E00\u65F6\u523BIV\uFF08P&L\u5F52\u56E0\u7528\uFF09",
        },
        volNew: {
          type: "number",
          description: "\u5F53\u524DIV\uFF08P&L\u5F52\u56E0\u7528\uFF09",
        },
        quantity: {
          type: "number",
          description: "\u5408\u7EA6\u5F20\u6570",
        },
        multiplier: {
          type: "number",
          description: "\u5408\u7EA6\u4E58\u6570\uFF08\u9ED8\u8BA410000\uFF09",
        },
        // ── margin fields ──────────────────────────────────────
        positionSide: {
          type: "string",
          enum: ["long", "short"],
          description: "\u6301\u4ED3\u65B9\u5411\uFF08\u4FDD\u8BC1\u91D1\u4F30\u7B97\u7528\uFF09\uFF0C\u9ED8\u8BA4 short",
        },
        settlementPrice: {
          type: "number",
          description: "\u7ED3\u7B97\u4EF7\uFF08\u53EF\u9009\uFF0C\u4FDD\u8BC1\u91D1\u4F30\u7B97\u7528\uFF09",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── Greeks What-If / Sensitivity ──────────────────────
        case "what_if": {
          const strike = args.strike as number;
          const optType = args.optionType as string;
          if (!strike || !optType) {
            return {
              error: "\u654F\u611F\u5EA6\u5206\u6790\u9700\u8981: strike (\u884C\u6743\u4EF7), optionType (call/put)",
            };
          }

          const spot = args.spotPrice as number;
          if (!spot) return { error: "\u8BF7\u63D0\u4F9B\u6807\u7684\u73B0\u4EF7 (spotPrice)" };

          const r = config.optionsConfig?.riskFreeRate ?? 0.02;
          const sigma = (args.volatility as number) ?? 0.25;
          const q = config.optionsConfig?.dividendYield ?? 0;

          // Parse bump arrays
          let spotBumps: number[] | undefined;
          let volBumps: number[] | undefined;
          let timeBumps: number[] | undefined;
          try {
            if (args.spotBumpsPct) spotBumps = JSON.parse(args.spotBumpsPct as string);
            if (args.volBumps) volBumps = JSON.parse(args.volBumps as string);
            if (args.timeBumpsDays) timeBumps = JSON.parse(args.timeBumpsDays as string);
          } catch {
            return { error: "\u53C2\u6570\u683C\u5F0F\u9519\u8BEF\uFF0C\u8BF7\u63D0\u4F9BJSON\u6570\u7EC4\u683C\u5F0F" };
          }

          const dte = args.daysToExpiry as number | undefined;
          const pyArgs: Record<string, unknown> = {
            action: "what_if",
            spotPrice: spot,
            strike,
            optionType: optType,
            riskFreeRate: r,
            volatility: sigma,
            dividendYield: q,
          };
          if (args.expiry) pyArgs.expiry = args.expiry;
          else if (dte) pyArgs.timeToExpiry = dte / 365;
          else pyArgs.timeToExpiry = 0.25;

          if (spotBumps) pyArgs.spotBumpsPct = spotBumps;
          if (volBumps) pyArgs.volBumps = volBumps;
          if (timeBumps) pyArgs.timeBumpsDays = timeBumps;

          const result = await runPythonScript<{
            baseSpot: number;
            baseVol: number;
            scenarios: Array<Record<string, number>>;
            scenarioCount: number;
          }>("engines/greeks_engine.py", pyArgs, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "\u654F\u611F\u5EA6\u5206\u6790\u5931\u8D25" };
          }

          const data = result.data;
          // Format scenarios for readability
          const formatted = data.scenarios.map((s) => ({
            spotBumpPct: s.spotBumpPct,
            volBump: s.volBump,
            timeBumpDays: s.timeBumpDays,
            spotPrice: s.spotPrice?.toFixed(4),
            delta: s.delta?.toFixed(4),
            gamma: s.gamma?.toFixed(6),
            theta: s.theta?.toFixed(4),
            vega: s.vega?.toFixed(4),
            charm: s.charm?.toFixed(6),
            vanna: s.vanna?.toFixed(6),
            vomma: s.vomma?.toFixed(6),
            theoreticalPrice: s.theoreticalPrice?.toFixed(6),
          }));

          return {
            baseSpot: data.baseSpot,
            baseVol: (data.baseVol * 100).toFixed(1) + "%",
            scenarioCount: data.scenarioCount,
            scenarios: formatted,
          };
        }

        // ── P&L Attribution ──────────────────────────────────
        case "pnl_explain": {
          const sOld = args.spotPriceOld as number;
          const sNew = args.spotPriceNew as number;
          const strike = args.strike as number;
          const optType = args.optionType as string;

          if (!sOld || !sNew || !strike || !optType) {
            return {
              error:
                "P&L\u5F52\u56E0\u9700\u8981: spotPriceOld, spotPriceNew, strike, optionType",
            };
          }

          const r = config.optionsConfig?.riskFreeRate ?? 0.02;
          const q = config.optionsConfig?.dividendYield ?? 0;
          const sigmaOld = (args.volOld as number) ?? (args.volatility as number) ?? 0.25;
          const sigmaNew = (args.volNew as number) ?? sigmaOld;
          const quantity = (args.quantity as number) ?? 1;
          const multiplier =
            (args.multiplier as number) ?? config.optionsConfig?.defaultMultiplier ?? 10000;

          const dte = args.daysToExpiry as number | undefined;
          const pyArgs: Record<string, unknown> = {
            action: "pnl_explain",
            spotPriceOld: sOld,
            spotPriceNew: sNew,
            volOld: sigmaOld,
            volNew: sigmaNew,
            strike,
            optionType: optType,
            riskFreeRate: r,
            dividendYield: q,
            quantity,
            multiplier,
          };
          if (args.expiry) {
            pyArgs.expiryOld = args.expiry;
            pyArgs.expiryNew = args.expiry;
          } else if (dte) {
            pyArgs.timeToExpiryOld = dte / 365;
            pyArgs.timeToExpiryNew = (dte - 1) / 365; // assume 1 day passed
          } else {
            pyArgs.timeToExpiryOld = 0.25;
            pyArgs.timeToExpiryNew = 0.25 - 1 / 365;
          }

          const result = await runPythonScript<{
            priceOld: number;
            priceNew: number;
            actualChange: number;
            deltaEffect: number;
            gammaEffect: number;
            thetaEffect: number;
            vegaEffect: number;
            explained: number;
            residual: number;
            pnlPerContract: number;
            totalPnl: number;
            decomposition: {
              delta: number;
              gamma: number;
              theta: number;
              vega: number;
              residual: number;
            };
          }>("engines/greeks_engine.py", pyArgs, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "P&L\u5F52\u56E0\u5206\u6790\u5931\u8D25" };
          }

          const d = result.data;
          return {
            priceOld: d.priceOld?.toFixed(6),
            priceNew: d.priceNew?.toFixed(6),
            actualChange: d.actualChange?.toFixed(6),
            attribution: {
              delta: d.deltaEffect?.toFixed(6),
              gamma: d.gammaEffect?.toFixed(6),
              theta: d.thetaEffect?.toFixed(6),
              vega: d.vegaEffect?.toFixed(6),
              explained: d.explained?.toFixed(6),
              residual: d.residual?.toFixed(6),
            },
            pnlPerContract: `\u00A5${d.pnlPerContract?.toFixed(2)}`,
            totalPnl: `\u00A5${d.totalPnl?.toFixed(2)}`,
            pnlDecomposition: {
              delta: `\u00A5${d.decomposition?.delta?.toFixed(2)}`,
              gamma: `\u00A5${d.decomposition?.gamma?.toFixed(2)}`,
              theta: `\u00A5${d.decomposition?.theta?.toFixed(2)}`,
              vega: `\u00A5${d.decomposition?.vega?.toFixed(2)}`,
              residual: `\u00A5${d.decomposition?.residual?.toFixed(2)}`,
            },
          };
        }

        // ── Margin Estimation ────────────────────────────────
        case "margin": {
          const spot = args.spotPrice as number;
          const strike = args.strike as number;
          const optType = args.optionType as string;

          if (!spot || !strike || !optType) {
            return {
              error: "\u4FDD\u8BC1\u91D1\u4F30\u7B97\u9700\u8981: spotPrice, strike, optionType",
            };
          }

          const sigma = (args.volatility as number) ?? 0.25;
          const positionSide = (args.positionSide as string) ?? "short";
          const quantity = (args.quantity as number) ?? 1;
          const multiplier =
            (args.multiplier as number) ?? config.optionsConfig?.defaultMultiplier ?? 10000;
          const settlement = args.settlementPrice as number | undefined;

          const dte = args.daysToExpiry as number | undefined;
          const pyArgs: Record<string, unknown> = {
            action: "margin",
            spotPrice: spot,
            strike,
            optionType: optType,
            volatility: sigma,
            positionSide,
            quantity,
            multiplier,
          };
          if (settlement != null) pyArgs.settlementPrice = settlement;
          if (args.expiry) pyArgs.expiry = args.expiry;
          else if (dte) pyArgs.timeToExpiry = dte / 365;
          else pyArgs.timeToExpiry = 0.25;

          const result = await runPythonScript<{
            marginPerContract: number;
            totalMargin: number;
            marginType: string;
            premiumComponent?: number;
            note: string;
          }>("engines/greeks_engine.py", pyArgs, config);

          if (!result.ok || !result.data) {
            return { error: result.error ?? "\u4FDD\u8BC1\u91D1\u4F30\u7B97\u5931\u8D25" };
          }

          const m = result.data;
          return {
            marginPerContract: `\u00A5${m.marginPerContract?.toFixed(2)}`,
            totalMargin: `\u00A5${m.totalMargin?.toFixed(2)}`,
            marginType: m.marginType,
            premiumComponent: m.premiumComponent ? `\u00A5${m.premiumComponent.toFixed(2)}` : undefined,
            quantity,
            note: m.note,
          };
        }

        default:
          return { error: `\u672A\u77E5\u64CD\u4F5C: ${action}` };
      }
    },
  };
}
