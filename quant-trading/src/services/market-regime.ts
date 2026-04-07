/**
 * MarketRegimeDetector — determines the current market regime using
 * index data (default: CSI 300 / 沪深300).
 *
 * Regime classification:
 *   - bull:     price above MA20 AND MA20 > MA60 (uptrend)
 *   - bear:     price below MA20 AND MA20 < MA60 (downtrend)
 *   - sideways: MA20 ≈ MA60 (within ±2%) and no clear trend
 *   - volatile: recent 20-day realised vol > 2× 60-day vol
 *
 * Used by LiveEngine.applyRegimeSwitch() to auto-pause/resume
 * strategies that have regime preferences.
 */
import type { MarketDataProvider } from "../data/market-data-provider.js";
import type { KlineBar, QuantTradingConfig } from "../types.js";
import { createDataProvider } from "../data/market-data-provider.js";

export type MarketRegime = "bull" | "bear" | "sideways" | "volatile";

export interface MarketRegimeConfig {
  /** Index symbol to track (default "000300" = CSI 300). */
  indexSymbol?: string;
  /** Short MA period (default 20). */
  shortMaPeriod?: number;
  /** Long MA period (default 60). */
  longMaPeriod?: number;
  /** Sideways threshold: MA20/MA60 ratio within ±this pct (default 0.02 = 2%). */
  sidewaysThreshold?: number;
  /** Volatile multiplier: if shortVol > longVol × this, regime = volatile (default 2.0). */
  volatileMultiplier?: number;
}

const DEFAULTS: Required<MarketRegimeConfig> = {
  indexSymbol: "000300",
  shortMaPeriod: 20,
  longMaPeriod: 60,
  sidewaysThreshold: 0.02,
  volatileMultiplier: 2.0,
};

function sma(values: number[], period: number): number | null {
  if (values.length < period) return null;
  const slice = values.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / period;
}

function realisedVol(closes: number[], period: number): number | null {
  if (closes.length < period + 1) return null;
  const slice = closes.slice(-(period + 1));
  const returns: number[] = [];
  for (let i = 1; i < slice.length; i++) {
    if (slice[i - 1] > 0) returns.push(Math.log(slice[i] / slice[i - 1]));
  }
  if (returns.length < 2) return null;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (returns.length - 1);
  return Math.sqrt(variance * 252); // annualised
}

export function createMarketRegimeDetector(
  config: QuantTradingConfig,
  regimeCfg?: MarketRegimeConfig,
): () => Promise<string | null> {
  const cfg: Required<MarketRegimeConfig> = { ...DEFAULTS, ...regimeCfg };
  const provider: MarketDataProvider = createDataProvider(config);

  return async (): Promise<string | null> => {
    try {
      // Fetch enough daily klines to compute MA60 + vol
      const end = new Date().toISOString().slice(0, 10);
      const start = new Date(Date.now() - 120 * 24 * 3600 * 1000).toISOString().slice(0, 10);
      const bars: KlineBar[] = await provider.getKlines(
        cfg.indexSymbol,
        "1d",
        start,
        end,
        "cn_stock",
      );
      if (bars.length < cfg.longMaPeriod + 1) return null;

      const closes = bars.map((b) => b.close);
      const currentPrice = closes[closes.length - 1];

      const maShort = sma(closes, cfg.shortMaPeriod);
      const maLong = sma(closes, cfg.longMaPeriod);
      if (maShort == null || maLong == null) return null;

      // Volatility check first (takes priority)
      const volShort = realisedVol(closes, cfg.shortMaPeriod);
      const volLong = realisedVol(closes, cfg.longMaPeriod);
      if (
        volShort != null &&
        volLong != null &&
        volLong > 0 &&
        volShort > volLong * cfg.volatileMultiplier
      ) {
        return "volatile";
      }

      // Sideways: MA20 ≈ MA60
      const maRatio = Math.abs(maShort - maLong) / maLong;
      if (maRatio < cfg.sidewaysThreshold) {
        return "sideways";
      }

      // Trend detection
      if (currentPrice > maShort && maShort > maLong) {
        return "bull";
      }
      if (currentPrice < maShort && maShort < maLong) {
        return "bear";
      }

      // Mixed signals → sideways
      return "sideways";
    } catch {
      return null;
    }
  };
}
