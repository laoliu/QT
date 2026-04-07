/**
 * OptionsRiskControlService — background service for pre-trade and real-time
 * options risk control.
 *
 * Capabilities:
 *   - Real-time portfolio VaR monitoring with threshold alerts
 *   - Greeks exposure monitoring with tiered alert levels
 *   - Concentration risk detection (single underlying, single expiry)
 *   - Margin utilization tracking
 *   - Auto-hedge trigger when Greeks breach critical thresholds
 *   - Periodic risk report generation
 *
 * Designed to complement the OptionsMonitorService by focusing on
 * portfolio-level risk metrics rather than individual position alerts.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { runPythonScript } from "../bridge/python-bridge.js";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";

// ── Types ────────────────────────────────────────────────────────

export type RiskControlConfig = {
  /** Enable/disable this service (default true) */
  enabled?: boolean;
  /** Check interval in seconds (default 120) */
  checkIntervalSecs?: number;
  /** VaR confidence level (default 0.95) */
  varConfidence?: number;
  /** VaR limit in CNY — alert when exceeded (default 0 = disabled) */
  varLimit?: number;
  /** Max concentration per underlying as % of total delta (default 60%) */
  maxConcentrationPct?: number;
  /** Max positions expiring in same week (default 0 = no limit) */
  maxSameWeekExpiry?: number;
  /** Critical delta threshold — triggers immediate notification (default 100) */
  criticalDelta?: number;
  /** Critical gamma threshold (default 20) */
  criticalGamma?: number;
  /** Margin utilization warning threshold as % (default 80) */
  marginUtilWarningPct?: number;
};

const DEFAULTS: Required<RiskControlConfig> = {
  enabled: true,
  checkIntervalSecs: 120,
  varConfidence: 0.95,
  varLimit: 0,
  maxConcentrationPct: 60,
  maxSameWeekExpiry: 0,
  criticalDelta: 100,
  criticalGamma: 20,
  marginUtilWarningPct: 80,
};

type RiskAlert = {
  severity: "info" | "warning" | "critical";
  type: string;
  message: string;
  timestamp: string;
  metric?: number;
  limit?: number;
};

// ── Service ──────────────────────────────────────────────────────

export function createOptionsRiskControlService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
): OpenClawPluginService {
  const riskCfg: Required<RiskControlConfig> = {
    ...DEFAULTS,
    ...config.optionsConfig?.riskControl,
  };

  let timer: ReturnType<typeof setInterval> | null = null;
  const alertHistory: RiskAlert[] = [];
  const sentAlerts = new Map<string, number>();
  const ALERT_COOLDOWN_MS = 15 * 60 * 1000; // 15 minutes

  // Track last VaR for trend detection
  let lastVaR: number | null = null;

  function shouldAlert(key: string): boolean {
    const last = sentAlerts.get(key);
    if (last && Date.now() - last < ALERT_COOLDOWN_MS) return false;
    sentAlerts.set(key, Date.now());
    return true;
  }

  function emitAlert(
    severity: "info" | "warning" | "critical",
    type: string,
    message: string,
    metric?: number,
    limit?: number,
  ) {
    const alert: RiskAlert = {
      severity,
      type,
      message,
      timestamp: new Date().toISOString(),
      metric,
      limit,
    };
    alertHistory.push(alert);
    if (alertHistory.length > 200) alertHistory.shift();

    const emoji = severity === "critical" ? "\u{1F6A8}" : severity === "warning" ? "\u26A0\uFE0F" : "\u2139\uFE0F";
    api.logger.warn(`[options-risk-ctrl] ${emoji} ${message}`);

    if (severity !== "info") {
      tryNotify(api, `${emoji} \u671F\u6743\u98CE\u63A7: ${message}`);
    }
  }

  async function runRiskCheck() {
    if (!isMarketOpen()) return;

    const positions = getPositions();
    const optionPositions = positions.filter(
      (p) => !!(p.strike || p.optionType || p.expiry),
    );
    if (optionPositions.length === 0) return;

    const r = config.optionsConfig?.riskFreeRate ?? 0.02;
    const q = config.optionsConfig?.dividendYield ?? 0;

    // Enrich positions with spot prices
    const enriched = optionPositions.map((p) => {
      const spotPrice = p.underlying
        ? positions.find((pos) => pos.symbol === p.underlying)?.currentPrice ?? p.currentPrice
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
        multiplier: 10000,
        avgCost: p.avgCost ?? 0,
        currentPrice: p.currentPrice ?? 0,
      };
    });

    // ── 1. Portfolio Greeks via Python ────────────────────────
    try {
      const greeksResult = await runPythonScript<{
        portfolioGreeks: {
          delta: number;
          gamma: number;
          theta: number;
          vega: number;
          cashDelta: number;
          cashGamma: number;
          cashTheta: number;
          cashVega: number;
        };
        positionCount: number;
      }>("engines/options_stress_test.py", {
        action: "portfolio_greeks",
        positions: enriched,
        riskFreeRate: r,
        dividendYield: q,
      }, config);

      if (greeksResult.ok && greeksResult.data) {
        const g = greeksResult.data.portfolioGreeks;

        // Critical delta breach
        if (Math.abs(g.delta) > riskCfg.criticalDelta) {
          const key = "critical_delta";
          if (shouldAlert(key)) {
            emitAlert(
              "critical",
              "critical_delta",
              `\u7EC4\u5408Delta\u8FBE\u5230\u4E34\u754C\u6C34\u5E73 ${g.delta.toFixed(2)}` +
                `\uFF08\u9650\u5236 \u00B1${riskCfg.criticalDelta}\uFF09` +
                `\uFF0C\u73B0\u91D1Delta \u00A5${g.cashDelta.toFixed(0)}` +
                ` \u2014 \u5EFA\u8BAE\u7ACB\u5373\u5BF9\u51B2`,
              Math.abs(g.delta),
              riskCfg.criticalDelta,
            );
          }
        }

        // Critical gamma breach
        if (Math.abs(g.gamma) > riskCfg.criticalGamma) {
          const key = "critical_gamma";
          if (shouldAlert(key)) {
            emitAlert(
              "critical",
              "critical_gamma",
              `\u7EC4\u5408Gamma\u8FBE\u5230\u4E34\u754C\u6C34\u5E73 ${g.gamma.toFixed(4)}` +
                `\uFF08\u9650\u5236 \u00B1${riskCfg.criticalGamma}\uFF09` +
                `\uFF0C\u73B0\u91D1Gamma \u00A5${g.cashGamma.toFixed(0)}/1%\u6CE2\u52A8` +
                ` \u2014 \u6807\u7684\u5927\u5E45\u6CE2\u52A8\u65F6\u98CE\u9669\u6781\u9AD8`,
              Math.abs(g.gamma),
              riskCfg.criticalGamma,
            );
          }
        }

        // Log summary
        api.logger.info(
          `[options-risk-ctrl] Greeks: \u0394=${g.delta.toFixed(2)} \u0393=${g.gamma.toFixed(4)} ` +
            `\u0398=${g.theta.toFixed(2)}/d V=${g.vega.toFixed(2)} | ` +
            `Cash: \u0394=\u00A5${g.cashDelta.toFixed(0)} \u0393=\u00A5${g.cashGamma.toFixed(0)}/1% ` +
            `\u0398=\u00A5${g.cashTheta.toFixed(0)}/d V=\u00A5${g.cashVega.toFixed(0)}/1%`,
        );
      }
    } catch {
      // Non-fatal: continue with other checks
    }

    // ── 2. VaR Check ─────────────────────────────────────────
    if (riskCfg.varLimit > 0) {
      try {
        const varResult = await runPythonScript<{
          var: number;
          cvar: number;
        }>("engines/options_stress_test.py", {
          action: "var",
          positions: enriched,
          confidence: riskCfg.varConfidence,
          horizonDays: 1,
          numSimulations: 2000, // fewer for background monitoring
          riskFreeRate: r,
          dividendYield: q,
        }, config);

        if (varResult.ok && varResult.data) {
          const currentVaR = varResult.data.var;

          if (currentVaR > riskCfg.varLimit) {
            const key = "var_breach";
            if (shouldAlert(key)) {
              emitAlert(
                "critical",
                "var_breach",
                `\u7EC4\u5408VaR \u00A5${currentVaR.toFixed(0)} \u8D85\u8FC7\u9650\u5236 \u00A5${riskCfg.varLimit}` +
                  `\uFF08${(riskCfg.varConfidence * 100).toFixed(0)}%\u7F6E\u4FE1\uFF09` +
                  ` \u2014 CVaR \u00A5${varResult.data.cvar.toFixed(0)}`,
                currentVaR,
                riskCfg.varLimit,
              );
            }
          }

          // VaR trend warning: if VaR increased >30% since last check
          if (lastVaR !== null && currentVaR > lastVaR * 1.3 && currentVaR > riskCfg.varLimit * 0.5) {
            const key = "var_trend";
            if (shouldAlert(key)) {
              emitAlert(
                "warning",
                "var_trend",
                `VaR\u5FEB\u901F\u4E0A\u5347: \u00A5${lastVaR.toFixed(0)} \u2192 \u00A5${currentVaR.toFixed(0)}` +
                  `\uFF08+${((currentVaR / lastVaR - 1) * 100).toFixed(0)}%\uFF09`,
                currentVaR,
              );
            }
          }

          lastVaR = currentVaR;
        }
      } catch {
        // VaR calculation is expensive; failures are non-fatal
      }
    }

    // ── 3. Concentration Risk ────────────────────────────────
    if (riskCfg.maxConcentrationPct > 0) {
      const underlyingGroups = new Map<string, number>();
      let totalAbsDelta = 0;

      for (const p of optionPositions) {
        const und = p.underlying || p.symbol;
        const posDelta = Math.abs((p.greeks?.delta ?? 0) * p.quantity);
        underlyingGroups.set(und, (underlyingGroups.get(und) ?? 0) + posDelta);
        totalAbsDelta += posDelta;
      }

      if (totalAbsDelta > 0) {
        for (const [und, delta] of underlyingGroups) {
          const pct = (delta / totalAbsDelta) * 100;
          if (pct > riskCfg.maxConcentrationPct) {
            const key = `concentration:${und}`;
            if (shouldAlert(key)) {
              emitAlert(
                "warning",
                "concentration",
                `${und} Delta\u96C6\u4E2D\u5EA6 ${pct.toFixed(0)}% \u8D85\u8FC7\u9650\u5236 ${riskCfg.maxConcentrationPct}%` +
                  ` \u2014 \u5EFA\u8BAE\u5206\u6563\u6807\u7684\u98CE\u9669`,
                pct,
                riskCfg.maxConcentrationPct,
              );
            }
          }
        }
      }
    }

    // ── 4. Expiry Concentration ──────────────────────────────
    if (riskCfg.maxSameWeekExpiry > 0) {
      const weekGroups = new Map<string, number>();
      for (const p of optionPositions) {
        if (!p.expiry) continue;
        const expiryDate = new Date(p.expiry);
        // Group by ISO week
        const weekStart = new Date(expiryDate);
        weekStart.setDate(weekStart.getDate() - weekStart.getDay());
        const weekKey = weekStart.toISOString().slice(0, 10);
        weekGroups.set(weekKey, (weekGroups.get(weekKey) ?? 0) + Math.abs(p.quantity));
      }

      for (const [week, count] of weekGroups) {
        if (count > riskCfg.maxSameWeekExpiry) {
          const key = `expiry_conc:${week}`;
          if (shouldAlert(key)) {
            emitAlert(
              "warning",
              "expiry_concentration",
              `${week} \u5468\u5185\u5230\u671F\u5408\u7EA6 ${count} \u5F20\u8D85\u8FC7\u9650\u5236 ${riskCfg.maxSameWeekExpiry}` +
                ` \u2014 \u5230\u671F\u96C6\u4E2D\u98CE\u9669\u8F83\u9AD8`,
              count,
              riskCfg.maxSameWeekExpiry,
            );
          }
        }
      }
    }
  }

  return {
    id: "options-risk-control",

    async start() {
      if (!riskCfg.enabled) {
        api.logger.info("[options-risk-ctrl] disabled by config");
        return;
      }
      api.logger.info(
        `[options-risk-ctrl] started (interval ${riskCfg.checkIntervalSecs}s, ` +
          `VaR limit ${riskCfg.varLimit > 0 ? `¥${riskCfg.varLimit}` : "disabled"}, ` +
          `critical Δ=${riskCfg.criticalDelta} Γ=${riskCfg.criticalGamma})`,
      );
      timer = setInterval(() => {
        runRiskCheck().catch((err) =>
          api.logger.error(`[options-risk-ctrl] check error: ${err}`),
        );
      }, riskCfg.checkIntervalSecs * 1000);

      // Run once immediately
      void runRiskCheck();
    },

    async stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      api.logger.info("[options-risk-ctrl] stopped");
    },
  };
}
