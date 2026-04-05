/**
 * Runtime config validation for QuantTradingConfig.
 *
 * Validates field types, enum values, and numeric ranges at startup
 * so misconfigurations surface immediately instead of at runtime.
 */
import type { QuantTradingConfig, MarketType, BacktestEngine } from "./types.js";

export interface ConfigIssue {
  path: string;
  message: string;
  severity: "error" | "warning";
}

const VALID_MARKETS: MarketType[] = [
  "cn_stock",
  "hk_stock",
  "us_stock",
  "fund",
  "futures",
  "crypto",
  "etf_option",
  "index_option",
  "commodity_option",
];
const VALID_MODES = ["paper", "live"] as const;
const VALID_BROKERS = ["paper", "qmt", "remote-qmt", "futu", "ib"] as const;
const VALID_DATA_PROVIDERS = ["demo", "akshare", "tushare", "yfinance", "smart"] as const;
const VALID_BACKTEST_ENGINES: BacktestEngine[] = ["builtin", "backtrader", "vnpy"];

/**
 * Validate a QuantTradingConfig and return a list of issues.
 * Returns empty array if config is valid.
 */
export function validateConfig(config: unknown): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    issues.push({ path: "", message: "config must be a non-array object", severity: "error" });
    return issues;
  }

  const cfg = config as Record<string, unknown>;

  // ── pythonPath ─────────────────────────────────────────────────
  if (cfg.pythonPath !== undefined && typeof cfg.pythonPath !== "string") {
    issues.push({
      path: "pythonPath",
      message: "pythonPath must be a string",
      severity: "error",
    });
  }

  // ── defaultMarket ──────────────────────────────────────────────
  if (cfg.defaultMarket !== undefined) {
    if (typeof cfg.defaultMarket !== "string") {
      issues.push({
        path: "defaultMarket",
        message: "defaultMarket must be a string",
        severity: "error",
      });
    } else if (!VALID_MARKETS.includes(cfg.defaultMarket as MarketType)) {
      issues.push({
        path: "defaultMarket",
        message: `defaultMarket "${cfg.defaultMarket}" is invalid. Valid: ${VALID_MARKETS.join(", ")}`,
        severity: "warning",
      });
    }
  }

  // ── tradingAccount ─────────────────────────────────────────────
  if (cfg.tradingAccount !== undefined) {
    if (typeof cfg.tradingAccount !== "object" || cfg.tradingAccount === null) {
      issues.push({
        path: "tradingAccount",
        message: "tradingAccount must be an object",
        severity: "error",
      });
    } else {
      const ta = cfg.tradingAccount as Record<string, unknown>;

      if (ta.mode !== undefined && !VALID_MODES.includes(ta.mode as (typeof VALID_MODES)[number])) {
        issues.push({
          path: "tradingAccount.mode",
          message: `mode "${String(ta.mode)}" is invalid. Valid: ${VALID_MODES.join(", ")}`,
          severity: "error",
        });
      }

      if (
        ta.broker !== undefined &&
        !VALID_BROKERS.includes(ta.broker as (typeof VALID_BROKERS)[number])
      ) {
        issues.push({
          path: "tradingAccount.broker",
          message: `broker "${String(ta.broker)}" is invalid. Valid: ${VALID_BROKERS.join(", ")}`,
          severity: "error",
        });
      }

      // Warn about unimplemented brokers that are valid but not yet available
      const UNIMPLEMENTED_BROKERS = ["ib", "eastmoney"] as const;
      if (
        ta.broker !== undefined &&
        UNIMPLEMENTED_BROKERS.includes(ta.broker as (typeof UNIMPLEMENTED_BROKERS)[number])
      ) {
        issues.push({
          path: "tradingAccount.broker",
          message: `broker "${String(ta.broker)}" is not yet implemented. Use "paper" or "qmt".`,
          severity: "warning",
        });
      }

      // ── riskLimits numeric checks ───────────────────────────
      if (ta.riskLimits && typeof ta.riskLimits === "object") {
        const rl = ta.riskLimits as Record<string, unknown>;
        validatePct(issues, rl, "riskLimits.maxPositionPct", "maxPositionPct");
        validatePositive(issues, rl, "riskLimits.maxOrderValue", "maxOrderValue");
        validatePositive(issues, rl, "riskLimits.maxDailyLoss", "maxDailyLoss");
        validatePositive(issues, rl, "riskLimits.maxOpenOrders", "maxOpenOrders");
        // Option-specific risk limits
        validateNonNegative(issues, rl, "riskLimits.maxNakedShort", "maxNakedShort");
        validatePositive(issues, rl, "riskLimits.maxDeltaExposure", "maxDeltaExposure");
        validatePositive(issues, rl, "riskLimits.maxGammaExposure", "maxGammaExposure");
        validatePositive(issues, rl, "riskLimits.maxVegaExposure", "maxVegaExposure");
        validatePositive(issues, rl, "riskLimits.nearExpiryDays", "nearExpiryDays");
      }

      // ── QMT sub-config ───────────────────────────────────────
      if (ta.broker === "qmt" && ta.qmt && typeof ta.qmt === "object") {
        const qmt = ta.qmt as Record<string, unknown>;
        if (!qmt.miniQmtPath || typeof qmt.miniQmtPath !== "string") {
          issues.push({
            path: "tradingAccount.qmt.miniQmtPath",
            message: "QMT broker requires miniQmtPath",
            severity: "warning",
          });
        }
        if (!qmt.accountId || typeof qmt.accountId !== "string") {
          issues.push({
            path: "tradingAccount.qmt.accountId",
            message: "QMT broker requires accountId",
            severity: "warning",
          });
        }
      }
    }
  }

  // ── pnlMonitor ─────────────────────────────────────────────────
  if (cfg.pnlMonitor && typeof cfg.pnlMonitor === "object") {
    const pm = cfg.pnlMonitor as Record<string, unknown>;
    validatePct(issues, pm, "pnlMonitor.warningPct", "warningPct");
    validatePct(issues, pm, "pnlMonitor.circuitBreakerPct", "circuitBreakerPct");
  }

  // ── signalWebhook ──────────────────────────────────────────────
  if (cfg.signalWebhook && typeof cfg.signalWebhook === "object") {
    const sw = cfg.signalWebhook as Record<string, unknown>;
    if (sw.secret !== undefined && typeof sw.secret !== "string") {
      issues.push({
        path: "signalWebhook.secret",
        message: "signalWebhook.secret must be a string",
        severity: "error",
      });
    }
  }

  // ── apiToken ──────────────────────────────────────────────────
  if (cfg.apiToken !== undefined) {
    if (typeof cfg.apiToken !== "string") {
      issues.push({
        path: "apiToken",
        message: "apiToken must be a string",
        severity: "error",
      });
    } else if (cfg.apiToken.length < 16) {
      issues.push({
        path: "apiToken",
        message: "apiToken is short (< 16 chars); consider using a longer token for security",
        severity: "warning",
      });
    }
  }

  // ── corsOrigins ───────────────────────────────────────────────
  if (cfg.corsOrigins !== undefined) {
    if (!Array.isArray(cfg.corsOrigins)) {
      issues.push({
        path: "corsOrigins",
        message: "corsOrigins must be an array of strings",
        severity: "error",
      });
    } else {
      for (const origin of cfg.corsOrigins as unknown[]) {
        if (typeof origin !== "string") {
          issues.push({
            path: "corsOrigins",
            message: `corsOrigins contains non-string value: ${String(origin)}`,
            severity: "error",
          });
        }
      }
      if ((cfg.corsOrigins as string[]).includes("*")) {
        issues.push({
          path: "corsOrigins",
          message: 'corsOrigins contains "*"; avoid wildcard in production',
          severity: "warning",
        });
      }
    }
  }

  // ── rateLimit ─────────────────────────────────────────────────
  if (cfg.rateLimit !== undefined) {
    if (typeof cfg.rateLimit !== "object" || cfg.rateLimit === null) {
      issues.push({
        path: "rateLimit",
        message: "rateLimit must be an object",
        severity: "error",
      });
    } else {
      const rl = cfg.rateLimit as Record<string, unknown>;
      if (rl.maxRequests !== undefined) {
        if (typeof rl.maxRequests !== "number" || rl.maxRequests < 1) {
          issues.push({
            path: "rateLimit.maxRequests",
            message: "maxRequests must be a positive integer",
            severity: "error",
          });
        }
      }
      if (rl.windowMs !== undefined) {
        if (typeof rl.windowMs !== "number" || rl.windowMs < 1000) {
          issues.push({
            path: "rateLimit.windowMs",
            message: "windowMs must be at least 1000 (1 second)",
            severity: "error",
          });
        }
      }
    }
  }

  // ── dataDir ───────────────────────────────────────────────────
  if (cfg.dataDir !== undefined) {
    if (typeof cfg.dataDir !== "string") {
      issues.push({
        path: "dataDir",
        message: "dataDir must be a string",
        severity: "error",
      });
    } else if (cfg.dataDir.length === 0) {
      issues.push({
        path: "dataDir",
        message: "dataDir must not be empty",
        severity: "error",
      });
    }
  }

  // ── dataProvider ──────────────────────────────────────────────
  if (cfg.dataProvider !== undefined) {
    if (typeof cfg.dataProvider !== "string") {
      issues.push({
        path: "dataProvider",
        message: "dataProvider must be a string",
        severity: "error",
      });
    } else if (
      !VALID_DATA_PROVIDERS.includes(cfg.dataProvider as (typeof VALID_DATA_PROVIDERS)[number])
    ) {
      issues.push({
        path: "dataProvider",
        message: `dataProvider "${cfg.dataProvider}" is invalid. Valid: ${VALID_DATA_PROVIDERS.join(", ")}`,
        severity: "error",
      });
    }
  }

  // ── backtestEngine ────────────────────────────────────────────
  if (cfg.backtestEngine !== undefined) {
    if (typeof cfg.backtestEngine !== "string") {
      issues.push({
        path: "backtestEngine",
        message: "backtestEngine must be a string",
        severity: "error",
      });
    } else if (!VALID_BACKTEST_ENGINES.includes(cfg.backtestEngine as BacktestEngine)) {
      issues.push({
        path: "backtestEngine",
        message: `backtestEngine "${cfg.backtestEngine}" is invalid. Valid: ${VALID_BACKTEST_ENGINES.join(", ")}`,
        severity: "error",
      });
    }
  }

  // ── dataStaleness ─────────────────────────────────────────────
  if (cfg.dataStaleness !== undefined) {
    if (typeof cfg.dataStaleness !== "object" || cfg.dataStaleness === null) {
      issues.push({
        path: "dataStaleness",
        message: "dataStaleness must be an object",
        severity: "error",
      });
    } else {
      const ds = cfg.dataStaleness as Record<string, unknown>;
      if (ds.enabled !== undefined && typeof ds.enabled !== "boolean") {
        issues.push({
          path: "dataStaleness.enabled",
          message: "dataStaleness.enabled must be a boolean",
          severity: "error",
        });
      }
      if (ds.checkIntervalMs !== undefined) {
        if (typeof ds.checkIntervalMs !== "number" || ds.checkIntervalMs < 1000) {
          issues.push({
            path: "dataStaleness.checkIntervalMs",
            message: "checkIntervalMs must be at least 1000ms",
            severity: "warning",
          });
        }
      }
      if (ds.warningThresholdMs !== undefined) {
        validatePositive(issues, ds, "dataStaleness.warningThresholdMs", "warningThresholdMs");
      }
      if (ds.criticalThresholdMs !== undefined) {
        validatePositive(issues, ds, "dataStaleness.criticalThresholdMs", "criticalThresholdMs");
      }
    }
  }

  // ── notifications ─────────────────────────────────────────────
  if (cfg.notifications !== undefined) {
    if (typeof cfg.notifications !== "object" || cfg.notifications === null) {
      issues.push({
        path: "notifications",
        message: "notifications must be an object",
        severity: "error",
      });
    }
  }

  // ── optionsConfig ─────────────────────────────────────────────
  if (cfg.optionsConfig !== undefined) {
    if (typeof cfg.optionsConfig !== "object" || cfg.optionsConfig === null) {
      issues.push({
        path: "optionsConfig",
        message: "optionsConfig must be an object",
        severity: "error",
      });
    } else {
      const oc = cfg.optionsConfig as Record<string, unknown>;
      if (oc.riskFreeRate !== undefined) {
        if (typeof oc.riskFreeRate !== "number" || oc.riskFreeRate < 0 || oc.riskFreeRate > 1) {
          issues.push({
            path: "optionsConfig.riskFreeRate",
            message: "riskFreeRate must be between 0 and 1",
            severity: "warning",
          });
        }
      }
      if (oc.dividendYield !== undefined) {
        if (typeof oc.dividendYield !== "number" || oc.dividendYield < 0 || oc.dividendYield > 1) {
          issues.push({
            path: "optionsConfig.dividendYield",
            message: "dividendYield must be between 0 and 1",
            severity: "warning",
          });
        }
      }
      if (oc.defaultMultiplier !== undefined) {
        validatePositive(issues, oc, "optionsConfig.defaultMultiplier", "defaultMultiplier");
      }
      if (oc.monitor !== undefined) {
        if (typeof oc.monitor !== "object" || oc.monitor === null) {
          issues.push({
            path: "optionsConfig.monitor",
            message: "optionsConfig.monitor must be an object",
            severity: "error",
          });
        } else {
          const mon = oc.monitor as Record<string, unknown>;
          if (mon.enabled !== undefined && typeof mon.enabled !== "boolean") {
            issues.push({
              path: "optionsConfig.monitor.enabled",
              message: "monitor.enabled must be a boolean",
              severity: "error",
            });
          }
          if (mon.pollIntervalSecs !== undefined) {
            validatePositive(
              issues,
              mon,
              "optionsConfig.monitor.pollIntervalSecs",
              "pollIntervalSecs",
            );
          }
          if (mon.expiryWarningDays !== undefined) {
            // expiryWarningDays can be a single number or number[]
            if (Array.isArray(mon.expiryWarningDays)) {
              for (const d of mon.expiryWarningDays as unknown[]) {
                if (typeof d !== "number" || d <= 0) {
                  issues.push({
                    path: "optionsConfig.monitor.expiryWarningDays",
                    message: "each element in expiryWarningDays must be a positive number",
                    severity: "warning",
                  });
                  break;
                }
              }
            } else {
              validatePositive(
                issues,
                mon,
                "optionsConfig.monitor.expiryWarningDays",
                "expiryWarningDays",
              );
            }
          }

          if (mon.greeksRefreshSecs !== undefined) {
            validatePositive(
              issues,
              mon,
              "optionsConfig.monitor.greeksRefreshSecs",
              "greeksRefreshSecs",
            );
          }
          if (mon.ivAlertThreshold !== undefined) {
            validatePositive(
              issues,
              mon,
              "optionsConfig.monitor.ivAlertThreshold",
              "ivAlertThreshold",
            );
          }
        }
      }
      // Validate greeksSource enum
      if (oc.greeksSource !== undefined) {
        const validSources = ["market", "local", "auto"];
        if (
          typeof oc.greeksSource !== "string" ||
          !validSources.includes(oc.greeksSource as string)
        ) {
          issues.push({
            path: "optionsConfig.greeksSource",
            message: `greeksSource must be one of: ${validSources.join(", ")}`,
            severity: "error",
          });
        }
      }
    }
  }

  return issues;
}

// ── Helpers ──────────────────────────────────────────────────

function validatePct(
  issues: ConfigIssue[],
  obj: Record<string, unknown>,
  path: string,
  key: string,
): void {
  const val = obj[key];
  if (val === undefined) return;
  if (typeof val !== "number" || val < 0 || val > 100) {
    issues.push({
      path,
      message: `${key} must be a number between 0 and 100, got ${String(val)}`,
      severity: "warning",
    });
  }
}

function validatePositive(
  issues: ConfigIssue[],
  obj: Record<string, unknown>,
  path: string,
  key: string,
): void {
  const val = obj[key];
  if (val === undefined) return;
  if (typeof val !== "number" || val <= 0) {
    issues.push({
      path,
      message: `${key} must be a positive number, got ${String(val)}`,
      severity: "warning",
    });
  }
}

function validateNonNegative(
  issues: ConfigIssue[],
  obj: Record<string, unknown>,
  path: string,
  key: string,
): void {
  const val = obj[key];
  if (val === undefined) return;
  if (typeof val !== "number" || val < 0) {
    issues.push({
      path,
      message: `${key} must be a non-negative number, got ${String(val)}`,
      severity: "warning",
    });
  }
}
