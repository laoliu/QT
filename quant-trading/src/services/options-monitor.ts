/**
 * OptionsMonitorService — background service for option-specific monitoring.
 *
 * Periodically checks:
 *   - Near-expiry positions (warns when DTE <= configurable threshold)
 *   - Portfolio Greeks breaches (Delta/Gamma/Vega/Theta limits)
 *   - IV spike detection (large IV moves on held positions)
 *   - Assignment/exercise risk for short ITM positions
 *   - Real-time Greeks refresh for option positions
 *
 * Designed to run alongside the existing PnL monitor and signal monitor.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { runPythonScript } from "../bridge/python-bridge.js";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { GreeksSnapshot, LivePosition, QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";
import { nowCSTISO } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────

export type OptionsAlertSeverity = "info" | "warning" | "critical";

export type OptionsAlert = {
  severity: OptionsAlertSeverity;
  type: string;
  message: string;
  timestamp: string;
  symbol?: string;
};

export type OptionsMonitorConfig = {
  /** Polling interval in seconds (default 60) */
  pollIntervalSecs?: number;
  /** DTE warning threshold in days (default 7). Can be a single number or array of thresholds. */
  expiryWarningDays?: number | number[];
  /** Max absolute portfolio Delta (default 50) */
  maxDelta?: number;
  /** Max absolute portfolio Gamma (default 10) */
  maxGamma?: number;
  /** Max absolute portfolio Vega (default 20) */
  maxVega?: number;
  /** Max absolute portfolio Theta per day (default 0 = no limit) */
  maxTheta?: number;
  /** Enable the monitor (default true) */
  enabled?: boolean;
  /** Greeks refresh interval in seconds (from config) */
  greeksRefreshSecs?: number;
  /** IV deviation threshold for alerts (default 2.0 = 2 std devs) */
  ivAlertThreshold?: number;
};

const DEFAULTS = {
  pollIntervalSecs: 60,
  expiryWarningDays: 7 as number | number[],
  maxDelta: 50,
  maxGamma: 10,
  maxVega: 20,
  maxTheta: 0,
  enabled: true,
  greeksRefreshSecs: 30,
  ivAlertThreshold: 2.0,
} satisfies Required<
  Pick<
    OptionsMonitorConfig,
    | "pollIntervalSecs"
    | "expiryWarningDays"
    | "maxDelta"
    | "maxGamma"
    | "maxVega"
    | "maxTheta"
    | "enabled"
    | "greeksRefreshSecs"
    | "ivAlertThreshold"
  >
>;

// ── Service ──────────────────────────────────────────────────

export function createOptionsMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
): OpenClawPluginService {
  const merged = {
    ...DEFAULTS,
    ...config.optionsConfig?.monitor,
  };
  const monCfg = {
    pollIntervalSecs: merged.pollIntervalSecs ?? DEFAULTS.pollIntervalSecs,
    expiryWarningDays: merged.expiryWarningDays ?? DEFAULTS.expiryWarningDays,
    maxDelta: merged.maxDelta ?? DEFAULTS.maxDelta,
    maxGamma: merged.maxGamma ?? DEFAULTS.maxGamma,
    maxVega: merged.maxVega ?? DEFAULTS.maxVega,
    maxTheta: merged.maxTheta ?? DEFAULTS.maxTheta,
    enabled: merged.enabled ?? DEFAULTS.enabled,
    greeksRefreshSecs: merged.greeksRefreshSecs ?? DEFAULTS.greeksRefreshSecs,
    ivAlertThreshold: merged.ivAlertThreshold ?? DEFAULTS.ivAlertThreshold,
  };

  let timer: ReturnType<typeof setInterval> | null = null;
  let greeksRefreshTimer: ReturnType<typeof setInterval> | null = null;
  const alertHistory: OptionsAlert[] = [];
  // Track sent alerts to avoid spamming (key=type:symbol, value=last alert timestamp)
  const sentAlerts = new Map<string, number>();
  const ALERT_COOLDOWN_MS = 30 * 60 * 1000; // 30 minutes between same alerts

  // IV history for spike detection (symbol -> recent IV readings)
  const ivHistory = new Map<string, number[]>();
  const IV_HISTORY_MAX = 60; // Keep last 60 readings per symbol

  function shouldAlert(key: string): boolean {
    const last = sentAlerts.get(key);
    if (last && Date.now() - last < ALERT_COOLDOWN_MS) return false;
    sentAlerts.set(key, Date.now());
    return true;
  }

  function emitAlert(
    severity: OptionsAlertSeverity,
    type: string,
    message: string,
    symbol?: string,
  ) {
    const alert: OptionsAlert = {
      severity,
      type,
      message,
      timestamp: nowCSTISO(),
      symbol,
    };
    alertHistory.push(alert);
    if (alertHistory.length > 200) alertHistory.shift();

    const emoji = severity === "critical" ? "\u{1F6A8}" : severity === "warning" ? "\u26A0\uFE0F" : "\u2139\uFE0F";
    api.logger.warn(`[options-monitor] ${emoji} ${message}`);

    if (severity !== "info") {
      tryNotify(api, `${emoji} \u671F\u6743\u98CE\u63A7: ${message}`);
    }
  }

  /** Refresh Greeks for option positions via Python bridge */
  async function refreshGreeks() {
    if (!isMarketOpen()) return;

    const positions = getPositions();
    const optionPositions = positions.filter((p) => p.optionType || p.strike || p.expiry);
    if (optionPositions.length === 0) return;

    for (const pos of optionPositions) {
      if (!pos.strike || !pos.expiry || !pos.currentPrice) continue;
      // We need the underlying price — look it up from positions
      const spotPrice = pos.underlying
        ? positions.find((p) => p.symbol === pos.underlying)?.currentPrice
        : undefined;
      if (!spotPrice) continue;

      try {
        const result = await runPythonScript<{
          delta: number;
          gamma: number;
          theta: number;
          vega: number;
          rho: number;
          impliedVol: number;
          theoreticalPrice: number;
        }>(
          "engines/greeks_engine.py",
          {
            action: "greeks",
            spotPrice,
            strike: pos.strike,
            optionType: pos.optionType ?? "call",
            expiry: pos.expiry,
            riskFreeRate: config.optionsConfig?.riskFreeRate ?? 0.02,
            dividendYield: config.optionsConfig?.dividendYield ?? 0,
            volatility: pos.greeks?.impliedVol ?? 0.25,
          },
          config,
        );

        if (result.ok && result.data) {
          const d = result.data;
          const newGreeks: GreeksSnapshot = {
            delta: d.delta,
            gamma: d.gamma,
            theta: d.theta,
            vega: d.vega,
            rho: d.rho,
            impliedVol: d.impliedVol,
            calculatedAt: Date.now(),
          };
          // Update the position's Greeks in-place
          (pos as { greeks?: GreeksSnapshot }).greeks = newGreeks;
        }
      } catch {
        // Silently skip failed refreshes — next cycle will retry
      }
    }

    api.logger.info(
      `[options-monitor] Greeks refreshed for ${optionPositions.length} option positions`,
    );
  }

  /** Detect IV spikes by comparing current IV against recent historical average */
  function checkIVSpikes(optionPositions: LivePosition[]) {
    for (const pos of optionPositions) {
      if (!pos.greeks?.impliedVol || pos.greeks.impliedVol <= 0) continue;
      const currentIV = pos.greeks.impliedVol;

      // Update IV history
      const history = ivHistory.get(pos.symbol) ?? [];
      history.push(currentIV);
      if (history.length > IV_HISTORY_MAX) history.shift();
      ivHistory.set(pos.symbol, history);

      // Need at least 10 readings to compute meaningful stats
      if (history.length < 10) continue;

      // Compute mean and std dev of historical IV
      const n = history.length;
      const mean = history.reduce((s, v) => s + v, 0) / n;
      const variance = history.reduce((s, v) => s + (v - mean) ** 2, 0) / n;
      const stddev = Math.sqrt(variance);

      if (stddev <= 0.001) continue; // Not enough variation

      const zScore = Math.abs(currentIV - mean) / stddev;
      if (zScore >= monCfg.ivAlertThreshold) {
        const key = `iv_spike:${pos.symbol}`;
        if (shouldAlert(key)) {
          const direction = currentIV > mean ? "\u98D9\u5347" : "\u9A64\u964D";
          emitAlert(
            zScore >= monCfg.ivAlertThreshold * 1.5 ? "critical" : "warning",
            "iv_spike",
            `${pos.symbol} \u9690\u542B\u6CE2\u52A8\u7387${direction}: ` +
              `IV=${(currentIV * 100).toFixed(1)}% (\u5747\u503C${(mean * 100).toFixed(1)}%, ` +
              `${zScore.toFixed(1)}\u03C3\u504F\u79BB) \u2014 \u6CE8\u610FVega\u98CE\u9669`,
            pos.symbol,
          );
        }
      }
    }
  }

  async function checkOnce() {
    if (!isMarketOpen()) return;

    const positions = getPositions();
    const optionPositions = positions.filter((p) => p.optionType || p.strike || p.expiry);

    if (optionPositions.length === 0) return;

    // ── 1. Near-expiry check ──────────────────────────────────
    const now = Date.now();
    // Resolve expiryWarningDays to a single max threshold for the comparison
    const maxExpiryWarn = Array.isArray(monCfg.expiryWarningDays)
      ? Math.max(...monCfg.expiryWarningDays)
      : monCfg.expiryWarningDays;
    for (const pos of optionPositions) {
      if (!pos.expiry) continue;
      const expiryTs = new Date(pos.expiry).getTime();
      const daysToExpiry = (expiryTs - now) / (24 * 60 * 60 * 1000);

      if (daysToExpiry <= 0) {
        const key = `expired:${pos.symbol}`;
        if (shouldAlert(key)) {
          emitAlert(
            "critical",
            "expired",
            `\u671F\u6743\u5408\u7EA6 ${pos.symbol} \u5DF2\u5230\u671F\uFF0C\u8BF7\u5C3D\u5FEB\u5904\u7406\u6301\u4ED3`,
            pos.symbol,
          );
        }
      } else if (daysToExpiry <= maxExpiryWarn) {
        const key = `expiring:${pos.symbol}`;
        if (shouldAlert(key)) {
          emitAlert(
            daysToExpiry <= 2 ? "critical" : "warning",
            "near_expiry",
            `\u671F\u6743\u5408\u7EA6 ${pos.symbol} \u5C06\u5728 ${daysToExpiry.toFixed(0)} \u5929\u540E\u5230\u671F` +
              ` (${pos.quantity > 0 ? "\u591A\u5934" : "\u7A7A\u5934"} ${Math.abs(pos.quantity)} \u5F20)`,
            pos.symbol,
          );
        }
      }
    }

    // ── 2. Portfolio Greeks checks ────────────────────────────
    const totalDelta = optionPositions.reduce(
      (sum, p) => sum + (p.greeks?.delta ?? 0) * p.quantity,
      0,
    );
    const totalGamma = optionPositions.reduce(
      (sum, p) => sum + (p.greeks?.gamma ?? 0) * p.quantity,
      0,
    );
    const totalVega = optionPositions.reduce(
      (sum, p) => sum + (p.greeks?.vega ?? 0) * p.quantity,
      0,
    );
    const totalTheta = optionPositions.reduce(
      (sum, p) => sum + (p.greeks?.theta ?? 0) * p.quantity,
      0,
    );

    if (Math.abs(totalDelta) > monCfg.maxDelta) {
      const key = "delta_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "delta_breach",
          `\u7EC4\u5408Delta\u66B4\u9732 ${totalDelta.toFixed(2)} \u8D85\u8FC7\u9650\u5236 \u00B1${monCfg.maxDelta}` + ` \u2014 \u5EFA\u8BAE\u5BF9\u51B2\u8C03\u6574`,
        );
      }
    }

    if (Math.abs(totalGamma) > monCfg.maxGamma) {
      const key = "gamma_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "gamma_breach",
          `\u7EC4\u5408Gamma\u66B4\u9732 ${totalGamma.toFixed(4)} \u8D85\u8FC7\u9650\u5236 \u00B1${monCfg.maxGamma}`,
        );
      }
    }

    if (Math.abs(totalVega) > monCfg.maxVega) {
      const key = "vega_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "vega_breach",
          `\u7EC4\u5408Vega\u66B4\u9732 ${totalVega.toFixed(2)} \u8D85\u8FC7\u9650\u5236 \u00B1${monCfg.maxVega}` +
            ` \u2014 \u5F53\u524D\u6CE2\u52A8\u7387\u53D8\u52A8\u98CE\u9669\u8F83\u9AD8`,
        );
      }
    }

    // ── 2b. Theta breach check ──────────────────────────────
    if (monCfg.maxTheta > 0 && Math.abs(totalTheta) > monCfg.maxTheta) {
      const key = "theta_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "theta_breach",
          `\u7EC4\u5408Theta\u65F6\u95F4\u635F\u8017 ${totalTheta.toFixed(2)}/\u5929 \u8D85\u8FC7\u9650\u5236 \u00B1${monCfg.maxTheta}` +
            ` \u2014 \u6BCF\u65E5\u65F6\u95F4\u4EF7\u503C${totalTheta < 0 ? "\u6D41\u5931" : "\u83B7\u53D6"}\u8FC7\u5927`,
        );
      }
    }

    // Log portfolio Greeks summary (info-level, no notification)
    api.logger.info(
      `[options-monitor] Greeks: \u0394=${totalDelta.toFixed(2)} \u0393=${totalGamma.toFixed(4)} ` +
        `\u0398=${totalTheta.toFixed(2)}/day V=${totalVega.toFixed(2)} | ${optionPositions.length} positions`,
    );

    // ── 3. Short ITM assignment risk ──────────────────────────
    for (const pos of optionPositions) {
      if (pos.quantity >= 0) continue; // only short positions
      if (!pos.strike || !pos.underlying) continue;

      // Use Greeks delta as ITM proxy: |delta| > 0.5 suggests ITM
      // This avoids the issue of confusing option price with underlying price
      const absDelta = Math.abs(pos.greeks?.delta ?? 0);
      const isLikelyITM = absDelta > 0.5;

      if (isLikelyITM) {
        const key = `assignment_risk:${pos.symbol}`;
        if (shouldAlert(key)) {
          emitAlert(
            "warning",
            "assignment_risk",
            `\u7A7A\u5934\u671F\u6743 ${pos.symbol} \u5F53\u524D\u5904\u4E8E\u5B9E\u503C\u72B6\u6001 (${pos.optionType} strike=${pos.strike}\uFF0C` +
              `|\u0394|=${absDelta.toFixed(2)})\uFF0C\u5B58\u5728\u88AB\u884C\u6743/\u6307\u6D3E\u98CE\u9669`,
            pos.symbol,
          );
        }
      }
    }

    // ── 4. IV spike detection ──────────────────────────────────
    checkIVSpikes(optionPositions);
  }

  return {
    id: "options-monitor",

    async start() {
      if (!monCfg.enabled) {
        api.logger.info("[options-monitor] disabled by config");
        return;
      }
      api.logger.info(
        `[options-monitor] started (poll ${monCfg.pollIntervalSecs}s, ` +
          `expiry warn ${monCfg.expiryWarningDays}d, ` +
          `Greeks refresh ${monCfg.greeksRefreshSecs}s)`,
      );
      timer = setInterval(() => {
        checkOnce().catch((err) => api.logger.error(`[options-monitor] check error: ${err}`));
      }, monCfg.pollIntervalSecs * 1000);

      // Separate timer for Greeks refresh (typically more frequent)
      if (monCfg.greeksRefreshSecs > 0) {
        greeksRefreshTimer = setInterval(() => {
          refreshGreeks().catch((err) =>
            api.logger.error(`[options-monitor] Greeks refresh error: ${err}`),
          );
        }, monCfg.greeksRefreshSecs * 1000);
      }

      // Run once immediately
      void checkOnce();
    },

    async stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      if (greeksRefreshTimer) {
        clearInterval(greeksRefreshTimer);
        greeksRefreshTimer = null;
      }
      api.logger.info("[options-monitor] stopped");
    },
  };
}
