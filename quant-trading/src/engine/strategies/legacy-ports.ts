/** Retained Python IDs, canonical builtin IDs and public parameter names. */
export const additionalLegacyPorts = [
  { legacyId: "macd", canonicalId: "macd-long-only", names: { fast_period: "fastPeriod", slow_period: "slowPeriod", signal_period: "signalPeriod" } },
  { legacyId: "rsi", canonicalId: "rsi-long-only", names: {} },
  { legacyId: "adx-trend", canonicalId: "adx-long-only", names: { adx_threshold: "adxThreshold" } },
  { legacyId: "dmi", canonicalId: "dmi-long-only", names: {} },
  { legacyId: "dema-cross", canonicalId: "dema-long-only", names: { fast_period: "fastPeriod", slow_period: "slowPeriod" } },
  { legacyId: "aroon", canonicalId: "aroon-long-only", names: { upper_threshold: "upperThreshold", lower_threshold: "lowerThreshold" } },
  { legacyId: "trix", canonicalId: "trix-long-only", names: { signal_period: "signalPeriod" } },
  { legacyId: "envelope", canonicalId: "envelope-long-only", names: { ma_period: "maPeriod", pct: "pct" } },
  { legacyId: "price-channel-breakout", canonicalId: "price-channel-long-only", names: { breakout_period: "breakoutPeriod", exit_period: "exitPeriod", vol_confirm: "volConfirm" } },
  { legacyId: "mfi", canonicalId: "mfi-long-only", names: {} },
  { legacyId: "roc", canonicalId: "roc-long-only", names: {} },
  { legacyId: "adaptive-ma", canonicalId: "kama-long-only", names: { fast_period: "fastPeriod", slow_period: "slowPeriod" } },
  { legacyId: "multi-tf-ma", canonicalId: "triple-ma-long-only", names: { short_period: "shortPeriod", medium_period: "mediumPeriod", long_period: "longPeriod" } },
  { legacyId: "supertrend", canonicalId: "supertrend-python-long-only", names: { atr_period: "atrPeriod" } },
] as const;
