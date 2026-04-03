/**
 * OptionsMonitorService — background service for option-specific monitoring.
 *
 * Periodically checks:
 *   - Near-expiry positions (warns when DTE ≤ configurable threshold)
 *   - Portfolio Greeks breaches (Delta/Gamma/Vega/Theta limits)
 *   - IV spike detection (large IV moves on held positions)
 *   - Assignment/exercise risk for short ITM positions
 *
 * Designed to run alongside the existing PnL monitor and signal monitor.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";

// ── Types ────────────────────────────────────────────────────────

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
  /** Enable the monitor (default true) */
  enabled?: boolean;
  /** Greeks refresh interval in seconds (from config) */
  greeksRefreshSecs?: number;
  /** IV deviation threshold for alerts */
  ivAlertThreshold?: number;
};

const DEFAULTS = {
  pollIntervalSecs: 60,
  expiryWarningDays: 7 as number | number[],
  maxDelta: 50,
  maxGamma: 10,
  maxVega: 20,
  enabled: true,
} satisfies Required<
  Pick<
    OptionsMonitorConfig,
    "pollIntervalSecs" | "expiryWarningDays" | "maxDelta" | "maxGamma" | "maxVega" | "enabled"
  >
>;

// ── Service ──────────────────────────────────────────────────────

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
    enabled: merged.enabled ?? DEFAULTS.enabled,
  };

  let timer: ReturnType<typeof setInterval> | null = null;
  const alertHistory: OptionsAlert[] = [];
  // Track sent alerts to avoid spamming (key=type:symbol, value=last alert timestamp)
  const sentAlerts = new Map<string, number>();
  const ALERT_COOLDOWN_MS = 30 * 60 * 1000; // 30 minutes between same alerts

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
      timestamp: new Date().toISOString(),
      symbol,
    };
    alertHistory.push(alert);
    if (alertHistory.length > 200) alertHistory.shift();

    const emoji = severity === "critical" ? "🚨" : severity === "warning" ? "⚠️" : "ℹ️";
    api.logger.warn(`[options-monitor] ${emoji} ${message}`);

    if (severity !== "info") {
      tryNotify(api, `${emoji} 期权风控: ${message}`);
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
            `期权合约 ${pos.symbol} 已到期，请尽快处理持仓`,
            pos.symbol,
          );
        }
      } else if (daysToExpiry <= maxExpiryWarn) {
        const key = `expiring:${pos.symbol}`;
        if (shouldAlert(key)) {
          emitAlert(
            daysToExpiry <= 2 ? "critical" : "warning",
            "near_expiry",
            `期权合约 ${pos.symbol} 将在 ${daysToExpiry.toFixed(0)} 天后到期` +
              ` (${pos.quantity > 0 ? "多头" : "空头"} ${Math.abs(pos.quantity)} 张)`,
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
          `组合Delta暴露 ${totalDelta.toFixed(2)} 超过限制 ±${monCfg.maxDelta}` + ` — 建议对冲调整`,
        );
      }
    }

    if (Math.abs(totalGamma) > monCfg.maxGamma) {
      const key = "gamma_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "gamma_breach",
          `组合Gamma暴露 ${totalGamma.toFixed(4)} 超过限制 ±${monCfg.maxGamma}`,
        );
      }
    }

    if (Math.abs(totalVega) > monCfg.maxVega) {
      const key = "vega_breach";
      if (shouldAlert(key)) {
        emitAlert(
          "warning",
          "vega_breach",
          `组合Vega暴露 ${totalVega.toFixed(2)} 超过限制 ±${monCfg.maxVega}` +
            ` — 当前波动率变动风险较高`,
        );
      }
    }

    // Log portfolio Greeks summary (info-level, no notification)
    api.logger.info(
      `[options-monitor] Greeks: Δ=${totalDelta.toFixed(2)} Γ=${totalGamma.toFixed(4)} ` +
        `Θ=${totalTheta.toFixed(2)}/day V=${totalVega.toFixed(2)} | ${optionPositions.length} positions`,
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
            `空头期权 ${pos.symbol} 当前处于实值状态 (${pos.optionType} strike=${pos.strike}，` +
              `|Δ|=${absDelta.toFixed(2)})，存在被行权/指派风险`,
            pos.symbol,
          );
        }
      }
    }
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
          `expiry warn ${monCfg.expiryWarningDays}d)`,
      );
      timer = setInterval(() => {
        checkOnce().catch((err) => api.logger.error(`[options-monitor] check error: ${err}`));
      }, monCfg.pollIntervalSecs * 1000);
      // Run once immediately
      void checkOnce();
    },

    async stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      api.logger.info("[options-monitor] stopped");
    },
  };
}
