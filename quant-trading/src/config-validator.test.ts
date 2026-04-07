/**
 * Unit tests for config-validator — validates QuantTradingConfig field checks.
 */
import { describe, expect, it } from "vitest";
import { validateConfig, type ConfigIssue } from "./config-validator.js";

function issuesAt(issues: ConfigIssue[], path: string) {
  return issues.filter((i) => i.path === path);
}

describe("config-validator", () => {
  it("returns empty for valid empty config", () => {
    expect(validateConfig({})).toEqual([]);
  });

  it("rejects non-object config", () => {
    const issues = validateConfig("not an object");
    expect(issues.length).toBe(1);
    expect(issues[0].severity).toBe("error");
  });

  it("rejects array config", () => {
    const issues = validateConfig([1, 2, 3]);
    expect(issues.length).toBe(1);
  });

  describe("pythonPath", () => {
    it("accepts string", () => {
      expect(validateConfig({ pythonPath: "/usr/bin/python3" })).toEqual([]);
    });

    it("rejects non-string", () => {
      const issues = validateConfig({ pythonPath: 42 });
      expect(issuesAt(issues, "pythonPath").length).toBe(1);
    });
  });

  describe("defaultMarket", () => {
    it("accepts valid market", () => {
      expect(validateConfig({ defaultMarket: "cn_stock" })).toEqual([]);
    });

    it("warns on invalid market", () => {
      const issues = validateConfig({ defaultMarket: "cn_stonk" });
      expect(issuesAt(issues, "defaultMarket").length).toBe(1);
      expect(issuesAt(issues, "defaultMarket")[0].severity).toBe("warning");
    });
  });

  describe("tradingAccount", () => {
    it("errors on invalid mode", () => {
      const issues = validateConfig({ tradingAccount: { mode: "liev" } });
      expect(issuesAt(issues, "tradingAccount.mode").length).toBe(1);
      expect(issuesAt(issues, "tradingAccount.mode")[0].severity).toBe("error");
    });

    it("errors on invalid broker", () => {
      const issues = validateConfig({ tradingAccount: { broker: "robinhood" } });
      expect(issuesAt(issues, "tradingAccount.broker").length).toBe(1);
    });

    it("accepts valid mode and broker", () => {
      expect(validateConfig({ tradingAccount: { mode: "paper", broker: "paper" } })).toEqual([]);
    });

    it("accepts futu as a valid broker (no longer unimplemented)", () => {
      const issues = validateConfig({ tradingAccount: { broker: "futu" } });
      const warnings = issues.filter(
        (i) => i.path === "tradingAccount.broker" && i.severity === "warning",
      );
      expect(warnings.length).toBe(0);
    });

    it("warns on unimplemented broker (ib)", () => {
      const issues = validateConfig({ tradingAccount: { broker: "ib" } });
      const warnings = issues.filter(
        (i) => i.path === "tradingAccount.broker" && i.severity === "warning",
      );
      expect(warnings.length).toBe(1);
      expect(warnings[0].message).toContain("not yet implemented");
    });
  });

  describe("Futu sub-config", () => {
    it("accepts valid futu config", () => {
      const issues = validateConfig({
        tradingAccount: {
          broker: "futu",
          futu: { openDHost: "127.0.0.1", openDPort: 11111, tradeEnv: "REAL" },
        },
      });
      expect(issuesAt(issues, "tradingAccount.futu.openDHost")).toEqual([]);
      expect(issuesAt(issues, "tradingAccount.futu.openDPort")).toEqual([]);
      expect(issuesAt(issues, "tradingAccount.futu.tradeEnv")).toEqual([]);
    });

    it("errors on invalid openDPort (0)", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { openDPort: 0 } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.openDPort").length).toBe(1);
      expect(issuesAt(issues, "tradingAccount.futu.openDPort")[0].severity).toBe("error");
    });

    it("errors on openDPort > 65535", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { openDPort: 70000 } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.openDPort").length).toBe(1);
    });

    it("errors on non-number openDPort", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { openDPort: "11111" } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.openDPort").length).toBe(1);
    });

    it("errors on non-string openDHost", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { openDHost: 12345 } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.openDHost").length).toBe(1);
    });

    it("errors on invalid tradeEnv", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { tradeEnv: "DEMO" } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.tradeEnv").length).toBe(1);
    });

    it("accepts SIMULATE tradeEnv", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "futu", futu: { tradeEnv: "SIMULATE" } },
      });
      expect(issuesAt(issues, "tradingAccount.futu.tradeEnv")).toEqual([]);
    });

    it("does not validate futu config when broker is not futu", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "paper", futu: { openDPort: -1 } },
      });
      // Should not produce futu-specific errors since broker != futu
      expect(issuesAt(issues, "tradingAccount.futu.openDPort")).toEqual([]);
    });

  describe("riskLimits", () => {
    it("warns on out-of-range maxPositionPct", () => {
      const issues = validateConfig({
        tradingAccount: { riskLimits: { maxPositionPct: 150 } },
      });
      expect(issuesAt(issues, "riskLimits.maxPositionPct").length).toBe(1);
    });

    it("warns on negative maxOrderValue", () => {
      const issues = validateConfig({
        tradingAccount: { riskLimits: { maxOrderValue: -100 } },
      });
      expect(issuesAt(issues, "riskLimits.maxOrderValue").length).toBe(1);
    });

    it("accepts valid risk limits", () => {
      const issues = validateConfig({
        tradingAccount: {
          riskLimits: { maxPositionPct: 30, maxOrderValue: 50000, maxDailyLoss: 5000 },
        },
      });
      expect(issues).toEqual([]);
    });
  });

  describe("QMT sub-config", () => {
    it("warns when qmt broker missing miniQmtPath", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "qmt", qmt: {} },
      });
      expect(issuesAt(issues, "tradingAccount.qmt.miniQmtPath").length).toBe(1);
    });

    it("warns when qmt broker missing accountId", () => {
      const issues = validateConfig({
        tradingAccount: { broker: "qmt", qmt: { miniQmtPath: "/path" } },
      });
      expect(issuesAt(issues, "tradingAccount.qmt.accountId").length).toBe(1);
    });

    it("passes when qmt config is complete", () => {
      const issues = validateConfig({
        tradingAccount: {
          broker: "qmt",
          qmt: { miniQmtPath: "/path/to/qmt", accountId: "123456" },
        },
      });
      expect(issuesAt(issues, "tradingAccount.qmt.miniQmtPath")).toEqual([]);
      expect(issuesAt(issues, "tradingAccount.qmt.accountId")).toEqual([]);
    });
  });

  describe("pnlMonitor", () => {
    it("warns on out-of-range warningPct", () => {
      const issues = validateConfig({ pnlMonitor: { warningPct: -5 } });
      expect(issuesAt(issues, "pnlMonitor.warningPct").length).toBe(1);
    });
  });

  describe("signalWebhook", () => {
    it("errors when secret is not a string", () => {
      const issues = validateConfig({ signalWebhook: { secret: 12345 } });
      expect(issuesAt(issues, "signalWebhook.secret").length).toBe(1);
    });

    it("accepts string secret", () => {
      expect(validateConfig({ signalWebhook: { secret: "my-secret" } })).toEqual([]);
    });
  });

  describe("apiToken", () => {
    it("accepts long token", () => {
      expect(validateConfig({ apiToken: "abcdefghijklmnop1234" })).toEqual([]);
    });

    it("warns on short token (< 16 chars)", () => {
      const issues = validateConfig({ apiToken: "short" });
      expect(issuesAt(issues, "apiToken").length).toBe(1);
      expect(issuesAt(issues, "apiToken")[0].severity).toBe("warning");
    });

    it("errors when apiToken is not a string", () => {
      const issues = validateConfig({ apiToken: 12345 });
      expect(issuesAt(issues, "apiToken").length).toBe(1);
      expect(issuesAt(issues, "apiToken")[0].severity).toBe("error");
    });
  });

  describe("corsOrigins", () => {
    it("accepts valid origins array", () => {
      expect(validateConfig({ corsOrigins: ["http://localhost:3000"] })).toEqual([]);
    });

    it("errors when corsOrigins is not an array", () => {
      const issues = validateConfig({ corsOrigins: "http://localhost" });
      expect(issuesAt(issues, "corsOrigins").length).toBe(1);
      expect(issuesAt(issues, "corsOrigins")[0].severity).toBe("error");
    });

    it("errors when corsOrigins contains non-string", () => {
      const issues = validateConfig({ corsOrigins: [123, "http://localhost"] });
      expect(issuesAt(issues, "corsOrigins").length).toBe(1);
      expect(issuesAt(issues, "corsOrigins")[0].severity).toBe("error");
    });

    it("warns when corsOrigins contains wildcard '*'", () => {
      const issues = validateConfig({ corsOrigins: ["*"] });
      const warnings = issuesAt(issues, "corsOrigins").filter((i) => i.severity === "warning");
      expect(warnings.length).toBe(1);
      expect(warnings[0].message).toContain("wildcard");
    });
  });

  describe("rateLimit", () => {
    it("accepts valid rateLimit config", () => {
      expect(validateConfig({ rateLimit: { maxRequests: 100, windowMs: 60_000 } })).toEqual([]);
    });

    it("errors when rateLimit is not an object", () => {
      const issues = validateConfig({ rateLimit: "fast" });
      expect(issuesAt(issues, "rateLimit").length).toBe(1);
      expect(issuesAt(issues, "rateLimit")[0].severity).toBe("error");
    });

    it("errors when maxRequests < 1", () => {
      const issues = validateConfig({ rateLimit: { maxRequests: 0 } });
      expect(issuesAt(issues, "rateLimit.maxRequests").length).toBe(1);
      expect(issuesAt(issues, "rateLimit.maxRequests")[0].severity).toBe("error");
    });

    it("errors when windowMs < 1000", () => {
      const issues = validateConfig({ rateLimit: { windowMs: 500 } });
      expect(issuesAt(issues, "rateLimit.windowMs").length).toBe(1);
      expect(issuesAt(issues, "rateLimit.windowMs")[0].severity).toBe("error");
    });

    it("accepts partial rateLimit config", () => {
      expect(validateConfig({ rateLimit: { maxRequests: 30 } })).toEqual([]);
      expect(validateConfig({ rateLimit: { windowMs: 120_000 } })).toEqual([]);
    });
  });

  describe("dataDir", () => {
    it("accepts valid string", () => {
      expect(validateConfig({ dataDir: "/tmp/quant" })).toEqual([]);
    });

    it("errors on non-string", () => {
      const issues = validateConfig({ dataDir: 42 });
      expect(issuesAt(issues, "dataDir").length).toBe(1);
      expect(issuesAt(issues, "dataDir")[0].severity).toBe("error");
    });

    it("errors on empty string", () => {
      const issues = validateConfig({ dataDir: "" });
      expect(issuesAt(issues, "dataDir").length).toBe(1);
    });
  });

  describe("dataProvider", () => {
    it("accepts valid providers", () => {
      for (const p of ["demo", "akshare", "tushare", "yfinance", "smart"]) {
        expect(validateConfig({ dataProvider: p })).toEqual([]);
      }
    });

    it("errors on invalid provider", () => {
      const issues = validateConfig({ dataProvider: "bloomberg" });
      expect(issuesAt(issues, "dataProvider").length).toBe(1);
      expect(issuesAt(issues, "dataProvider")[0].severity).toBe("error");
    });

    it("errors on non-string", () => {
      const issues = validateConfig({ dataProvider: 123 });
      expect(issuesAt(issues, "dataProvider").length).toBe(1);
    });
  });

  describe("backtestEngine", () => {
    it("accepts valid engines", () => {
      for (const e of ["builtin", "backtrader", "vnpy"]) {
        expect(validateConfig({ backtestEngine: e })).toEqual([]);
      }
    });

    it("errors on invalid engine", () => {
      const issues = validateConfig({ backtestEngine: "zipline" });
      expect(issuesAt(issues, "backtestEngine").length).toBe(1);
      expect(issuesAt(issues, "backtestEngine")[0].severity).toBe("error");
    });
  });

  describe("dataStaleness", () => {
    it("accepts valid config", () => {
      expect(
        validateConfig({
          dataStaleness: { enabled: true, checkIntervalMs: 30_000, warningThresholdMs: 60_000 },
        }),
      ).toEqual([]);
    });

    it("errors on non-object", () => {
      const issues = validateConfig({ dataStaleness: "fast" });
      expect(issuesAt(issues, "dataStaleness").length).toBe(1);
    });

    it("errors on non-boolean enabled", () => {
      const issues = validateConfig({ dataStaleness: { enabled: "yes" } });
      expect(issuesAt(issues, "dataStaleness.enabled").length).toBe(1);
    });

    it("warns on low checkIntervalMs", () => {
      const issues = validateConfig({ dataStaleness: { checkIntervalMs: 100 } });
      expect(issuesAt(issues, "dataStaleness.checkIntervalMs").length).toBe(1);
    });

    it("warns on negative thresholds", () => {
      const issues = validateConfig({
        dataStaleness: { warningThresholdMs: -1, criticalThresholdMs: -1 },
      });
      expect(issuesAt(issues, "dataStaleness.warningThresholdMs").length).toBe(1);
      expect(issuesAt(issues, "dataStaleness.criticalThresholdMs").length).toBe(1);
    });
  });

  describe("notifications", () => {
    it("accepts valid object", () => {
      expect(validateConfig({ notifications: { enabled: true } })).toEqual([]);
    });

    it("errors on non-object", () => {
      const issues = validateConfig({ notifications: "email" });
      expect(issuesAt(issues, "notifications").length).toBe(1);
    });
  });

  describe("defaultMarket — option types", () => {
    it("accepts etf_option as valid market", () => {
      expect(validateConfig({ defaultMarket: "etf_option" })).toEqual([]);
    });

    it("accepts index_option as valid market", () => {
      expect(validateConfig({ defaultMarket: "index_option" })).toEqual([]);
    });

    it("accepts commodity_option as valid market", () => {
      expect(validateConfig({ defaultMarket: "commodity_option" })).toEqual([]);
    });
  });

  describe("optionsConfig", () => {
    it("accepts valid optionsConfig", () => {
      const issues = validateConfig({
        optionsConfig: {
          riskFreeRate: 0.025,
          dividendYield: 0.01,
          defaultMultiplier: 10000,
          monitor: { enabled: true, pollIntervalSecs: 60, expiryWarningDays: 7 },
        },
      });
      expect(issues).toEqual([]);
    });

    it("errors on non-object optionsConfig", () => {
      const issues = validateConfig({ optionsConfig: "bad" });
      expect(issuesAt(issues, "optionsConfig").length).toBe(1);
    });

    it("warns on out-of-range riskFreeRate", () => {
      const issues = validateConfig({ optionsConfig: { riskFreeRate: 5 } });
      expect(issuesAt(issues, "optionsConfig.riskFreeRate").length).toBe(1);
    });

    it("warns on negative dividendYield", () => {
      const issues = validateConfig({ optionsConfig: { dividendYield: -0.1 } });
      expect(issuesAt(issues, "optionsConfig.dividendYield").length).toBe(1);
    });

    it("warns on non-positive defaultMultiplier", () => {
      const issues = validateConfig({ optionsConfig: { defaultMultiplier: 0 } });
      expect(issuesAt(issues, "optionsConfig.defaultMultiplier").length).toBe(1);
    });

    it("errors on non-object monitor", () => {
      const issues = validateConfig({ optionsConfig: { monitor: "bad" } });
      expect(issuesAt(issues, "optionsConfig.monitor").length).toBe(1);
    });

    it("errors on non-boolean monitor.enabled", () => {
      const issues = validateConfig({ optionsConfig: { monitor: { enabled: "yes" } } });
      expect(issuesAt(issues, "optionsConfig.monitor.enabled").length).toBe(1);
    });
  });

  describe("riskLimits — option fields", () => {
    it("warns on negative maxNakedShort", () => {
      const issues = validateConfig({
        tradingAccount: { mode: "paper", broker: "paper", riskLimits: { maxNakedShort: -1 } },
      });
      expect(issuesAt(issues, "riskLimits.maxNakedShort").length).toBe(1);
    });

    it("accepts zero maxNakedShort (blocks all shorts)", () => {
      const issues = validateConfig({
        tradingAccount: { mode: "paper", broker: "paper", riskLimits: { maxNakedShort: 0 } },
      });
      expect(issuesAt(issues, "riskLimits.maxNakedShort")).toEqual([]);
    });

    it("warns on non-positive maxDeltaExposure", () => {
      const issues = validateConfig({
        tradingAccount: {
          mode: "paper",
          broker: "paper",
          riskLimits: { maxDeltaExposure: 0 },
        },
      });
      expect(issuesAt(issues, "riskLimits.maxDeltaExposure").length).toBe(1);
    });
  });
});

describe("optionsConfig.monitor.expiryWarningDays — array support", () => {
  it("accepts a number[] of positive values", () => {
    const issues = validateConfig({
      optionsConfig: {
        monitor: { expiryWarningDays: [7, 3, 1] },
      },
    });
    expect(issuesAt(issues, "optionsConfig.monitor.expiryWarningDays")).toEqual([]);
  });

  it("warns on array containing non-positive value", () => {
    const issues = validateConfig({
      optionsConfig: {
        monitor: { expiryWarningDays: [7, 0, 1] },
      },
    });
    expect(issuesAt(issues, "optionsConfig.monitor.expiryWarningDays").length).toBe(1);
  });

  it("still accepts a single positive number", () => {
    const issues = validateConfig({
      optionsConfig: {
        monitor: { expiryWarningDays: 7 },
      },
    });
    expect(issuesAt(issues, "optionsConfig.monitor.expiryWarningDays")).toEqual([]);
  });
});

describe("greeksSource validation", () => {
  it("accepts valid greeksSource values", () => {
    for (const src of ["market", "local", "auto"]) {
      const issues = validateConfig({
        optionsConfig: { greeksSource: src as "market" | "local" | "auto" },
      });
      expect(issuesAt(issues, "optionsConfig.greeksSource")).toEqual([]);
    }
  });

  it("rejects invalid greeksSource value", () => {
    const issues = validateConfig({ optionsConfig: { greeksSource: "invalid" as "market" } });
    expect(issuesAt(issues, "optionsConfig.greeksSource").length).toBe(1);
  });
});

describe("greeksRefreshSecs validation", () => {
  it("accepts a positive value", () => {
    const issues = validateConfig({ optionsConfig: { monitor: { greeksRefreshSecs: 30 } } });
    expect(issuesAt(issues, "optionsConfig.monitor.greeksRefreshSecs")).toEqual([]);
  });

  it("rejects zero", () => {
    const issues = validateConfig({ optionsConfig: { monitor: { greeksRefreshSecs: 0 } } });
    expect(issuesAt(issues, "optionsConfig.monitor.greeksRefreshSecs").length).toBe(1);
  });

  it("rejects negative", () => {
    const issues = validateConfig({ optionsConfig: { monitor: { greeksRefreshSecs: -5 } } });
    expect(issuesAt(issues, "optionsConfig.monitor.greeksRefreshSecs").length).toBe(1);
  });
});

describe("ivAlertThreshold validation", () => {
  it("accepts a positive value", () => {
    const issues = validateConfig({ optionsConfig: { monitor: { ivAlertThreshold: 0.5 } } });
    expect(issuesAt(issues, "optionsConfig.monitor.ivAlertThreshold")).toEqual([]);
  });

  it("rejects zero", () => {
    const issues = validateConfig({ optionsConfig: { monitor: { ivAlertThreshold: 0 } } });
    expect(issuesAt(issues, "optionsConfig.monitor.ivAlertThreshold").length).toBe(1);
  });
});

describe("maxDailyLoss validation", () => {
  it("accepts a positive value", () => {
    const issues = validateConfig({ tradingAccount: { riskLimits: { maxDailyLoss: 5000 } } });
    expect(issuesAt(issues, "riskLimits.maxDailyLoss")).toEqual([]);
  });

  it("rejects negative", () => {
    const issues = validateConfig({ tradingAccount: { riskLimits: { maxDailyLoss: -100 } } });
    expect(issuesAt(issues, "riskLimits.maxDailyLoss").length).toBe(1);
  });
});
