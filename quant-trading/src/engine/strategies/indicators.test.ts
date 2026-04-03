import { describe, expect, it } from "vitest";
import type { KlineBar } from "../../types.js";
import {
  atr,
  bollingerBands,
  cci,
  dmi,
  ema,
  ichimoku,
  macd,
  obv,
  parabolicSar,
  roc,
  rollingHighLow,
  rsi,
  sma,
  stochastic,
  williamsR,
} from "./indicators.js";

// ── SMA ──────────────────────────────────────────────────────────

describe("sma", () => {
  it("returns NaN before the period is ready", () => {
    const result = sma([1, 2, 3, 4, 5], 3);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    expect(result).toHaveLength(5);
  });

  it("computes simple moving averages correctly", () => {
    const result = sma([2, 4, 6, 8, 10], 3);
    expect(result[2]).toBeCloseTo(4); // (2+4+6)/3
    expect(result[3]).toBeCloseTo(6); // (4+6+8)/3
    expect(result[4]).toBeCloseTo(8); // (6+8+10)/3
  });

  it("handles period = 1 (identity)", () => {
    const data = [10, 20, 30];
    const result = sma(data, 1);
    expect(result).toEqual([10, 20, 30]);
  });

  it("handles period equal to data length", () => {
    const data = [1, 2, 3, 4, 5];
    const result = sma(data, 5);
    expect(result.slice(0, 4).every((v) => Number.isNaN(v))).toBe(true);
    expect(result[4]).toBeCloseTo(3); // (1+2+3+4+5)/5
  });

  it("returns empty array for empty data", () => {
    expect(sma([], 3)).toEqual([]);
  });
});

// ── rollingHighLow ───────────────────────────────────────────────

describe("rollingHighLow", () => {
  const bars: KlineBar[] = [
    { timestamp: 1704067200000, open: 10, high: 12, low: 9, close: 11, volume: 100 },
    { timestamp: 1704153600000, open: 11, high: 14, low: 10, close: 13, volume: 100 },
    { timestamp: 1704240000000, open: 13, high: 15, low: 11, close: 14, volume: 100 },
    { timestamp: 1704326400000, open: 14, high: 16, low: 12, close: 15, volume: 100 },
    { timestamp: 1704412800000, open: 15, high: 13, low: 8, close: 10, volume: 100 },
  ];

  it("returns NaN before lookback period", () => {
    const { highs, lows } = rollingHighLow(bars, 2);
    expect(highs[0]).toBeNaN();
    expect(highs[1]).toBeNaN();
    expect(lows[0]).toBeNaN();
    expect(lows[1]).toBeNaN();
  });

  it("computes rolling high/low excluding current bar", () => {
    const { highs, lows } = rollingHighLow(bars, 2);
    // Index 2: lookback bars [0,1] → high max(12,14)=14, low min(9,10)=9
    expect(highs[2]).toBe(14);
    expect(lows[2]).toBe(9);
    // Index 3: lookback bars [1,2] → high max(14,15)=15, low min(10,11)=10
    expect(highs[3]).toBe(15);
    expect(lows[3]).toBe(10);
    // Index 4: lookback bars [2,3] → high max(15,16)=16, low min(11,12)=11
    expect(highs[4]).toBe(16);
    expect(lows[4]).toBe(11);
  });

  it("handles lookback=1", () => {
    const { highs, lows } = rollingHighLow(bars, 1);
    expect(highs[0]).toBeNaN();
    // Index 1: lookback bar [0] → high=12, low=9
    expect(highs[1]).toBe(12);
    expect(lows[1]).toBe(9);
  });

  it("returns arrays of correct length", () => {
    const { highs, lows } = rollingHighLow(bars, 3);
    expect(highs).toHaveLength(5);
    expect(lows).toHaveLength(5);
  });
});

// ── bollingerBands ───────────────────────────────────────────────

describe("bollingerBands", () => {
  it("returns NaN before period is ready", () => {
    const closes = [10, 11, 12, 13, 14];
    const { middle, upper, lower } = bollingerBands(closes, 3, 2);
    expect(middle[0]).toBeNaN();
    expect(middle[1]).toBeNaN();
    expect(upper[0]).toBeNaN();
    expect(lower[0]).toBeNaN();
  });

  it("middle band equals SMA", () => {
    const closes = [10, 12, 14, 16, 18];
    const { middle } = bollingerBands(closes, 3, 2);
    const smaVals = sma(closes, 3);
    for (let i = 0; i < closes.length; i++) {
      if (Number.isNaN(smaVals[i]!)) {
        expect(middle[i]).toBeNaN();
      } else {
        expect(middle[i]).toBeCloseTo(smaVals[i]!);
      }
    }
  });

  it("upper > middle > lower for non-constant data", () => {
    const closes = [10, 12, 11, 14, 13, 16, 15];
    const { middle, upper, lower } = bollingerBands(closes, 3, 2);
    for (let i = 2; i < closes.length; i++) {
      expect(upper[i]!).toBeGreaterThan(middle[i]!);
      expect(middle[i]!).toBeGreaterThan(lower[i]!);
    }
  });

  it("bands collapse for constant data (stddev = 0)", () => {
    const closes = [10, 10, 10, 10, 10];
    const { middle, upper, lower } = bollingerBands(closes, 3, 2);
    for (let i = 2; i < closes.length; i++) {
      expect(middle[i]).toBeCloseTo(10);
      expect(upper[i]).toBeCloseTo(10);
      expect(lower[i]).toBeCloseTo(10);
    }
  });

  it("wider stdDev multiplier produces wider bands", () => {
    const closes = [10, 12, 14, 11, 13];
    const narrow = bollingerBands(closes, 3, 1);
    const wide = bollingerBands(closes, 3, 3);
    for (let i = 2; i < closes.length; i++) {
      const widthNarrow = narrow.upper[i]! - narrow.lower[i]!;
      const widthWide = wide.upper[i]! - wide.lower[i]!;
      expect(widthWide).toBeGreaterThan(widthNarrow);
    }
  });

  it("returns arrays of correct length", () => {
    const closes = [1, 2, 3, 4, 5];
    const { middle, upper, lower } = bollingerBands(closes, 3, 2);
    expect(middle).toHaveLength(5);
    expect(upper).toHaveLength(5);
    expect(lower).toHaveLength(5);
  });
});

// ── EMA ──────────────────────────────────────────────────────────

describe("ema", () => {
  it("returns NaN before the period is ready", () => {
    const result = ema([1, 2, 3, 4, 5], 3);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    expect(result).toHaveLength(5);
  });

  it("seeds with SMA then applies EMA formula", () => {
    const data = [2, 4, 6, 8, 10];
    const result = ema(data, 3);
    // Seed: SMA(2,4,6) = 4
    expect(result[2]).toBeCloseTo(4);
    // EMA at index 3: k = 2/(3+1) = 0.5; ema = 8*0.5 + 4*0.5 = 6
    expect(result[3]).toBeCloseTo(6);
    // EMA at index 4: ema = 10*0.5 + 6*0.5 = 8
    expect(result[4]).toBeCloseTo(8);
  });

  it("EMA equals value for period=1", () => {
    const data = [10, 20, 30];
    const result = ema(data, 1);
    // k = 2/(1+1) = 1.0 → EMA = close itself
    expect(result[0]).toBeCloseTo(10);
    expect(result[1]).toBeCloseTo(20);
    expect(result[2]).toBeCloseTo(30);
  });

  it("converges to constant value for constant data", () => {
    const data = [5, 5, 5, 5, 5, 5, 5, 5, 5, 5];
    const result = ema(data, 5);
    for (let i = 4; i < data.length; i++) {
      expect(result[i]).toBeCloseTo(5);
    }
  });

  it("returns empty array for empty data", () => {
    expect(ema([], 3)).toEqual([]);
  });
});

// ── ATR ──────────────────────────────────────────────────────────

describe("atr", () => {
  const bars: KlineBar[] = [
    { timestamp: 1704067200000, open: 10, high: 12, low: 9, close: 11, volume: 100 },
    { timestamp: 1704153600000, open: 11, high: 14, low: 10, close: 13, volume: 100 },
    { timestamp: 1704240000000, open: 13, high: 15, low: 11, close: 14, volume: 100 },
    { timestamp: 1704326400000, open: 14, high: 16, low: 12, close: 15, volume: 100 },
    { timestamp: 1704412800000, open: 15, high: 18, low: 13, close: 16, volume: 100 },
  ];

  it("returns NaN before the period is ready", () => {
    const result = atr(bars, 3);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    // Index 2 is the first valid ATR = SMA(TR[0..2], 3)
    expect(Number.isNaN(result[2])).toBe(false);
    expect(result).toHaveLength(5);
  });

  it("computes ATR as SMA of true range", () => {
    const result = atr(bars, 3);
    // TR[0] = high-low = 3
    // TR[1] = max(4, |14-11|=3, |10-11|=1) = 4
    // TR[2] = max(4, |15-13|=2, |11-13|=2) = 4
    // ATR[2] = SMA(TR[0..2], 3) = (3+4+4)/3 ≈ 3.667
    expect(result[2]).toBeCloseTo(11 / 3);
    // TR[3] = max(4, |16-14|=2, |12-14|=2) = 4
    // ATR[3] = SMA(TR[1..3], 3) = (4+4+4)/3 = 4
    expect(result[3]).toBeCloseTo(4);
  });

  it("returns positive values for valid indices", () => {
    const result = atr(bars, 2);
    for (let i = 2; i < bars.length; i++) {
      expect(result[i]).toBeGreaterThan(0);
    }
  });

  it("ATR is 0 for constant-price bars (high=low=close)", () => {
    const flatBars: KlineBar[] = Array.from({ length: 10 }, (_, i) => ({
      timestamp: 1704067200000 + i * 86400000,
      open: 100,
      high: 100,
      low: 100,
      close: 100,
      volume: 1000,
    }));
    const result = atr(flatBars, 3);
    for (let i = 3; i < flatBars.length; i++) {
      expect(result[i]).toBeCloseTo(0);
    }
  });

  it("returns empty array for empty bars", () => {
    expect(atr([], 3)).toEqual([]);
  });
});

// ── Edge case: sma period guard ──────────────────────────────────

describe("sma edge cases", () => {
  it("throws on period <= 0", () => {
    expect(() => sma([1, 2, 3], 0)).toThrow("period must be > 0");
    expect(() => sma([1, 2, 3], -1)).toThrow("period must be > 0");
  });
});

// ── Bollinger Bands: sample std dev ──────────────────────────────

describe("bollingerBands edge cases", () => {
  it("uses sample std dev (n-1) for wider bands", () => {
    // With population std dev, bands are narrower than with sample std dev
    // For constant data, both give 0, so use varying data
    const data = [10, 12, 14, 16, 18];
    const bb = bollingerBands(data, 3, 1);
    // At index 2: mean = 12, sample sd = sqrt(((10-12)^2+(12-12)^2+(14-12)^2)/2)
    const expectedSd = Math.sqrt((4 + 0 + 4) / 2); // = 2
    expect(bb.upper[2]).toBeCloseTo(12 + expectedSd);
    expect(bb.lower[2]).toBeCloseTo(12 - expectedSd);
  });
});

// ── RSI ──────────────────────────────────────────────────────────

describe("rsi", () => {
  it("returns NaN before the first period is ready", () => {
    const data = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84];
    const result = rsi(data, 14);
    // Not enough data — all should be NaN
    expect(result.every((v) => Number.isNaN(v))).toBe(true);
  });

  it("returns array of correct length", () => {
    const data = Array.from({ length: 30 }, (_, i) => 100 + i);
    const result = rsi(data, 14);
    expect(result).toHaveLength(30);
  });

  it("RSI is 100 for purely upward data (no losses)", () => {
    // 16 bars, all going up → avg loss = 0 → RSI = 100
    const data = Array.from({ length: 16 }, (_, i) => 100 + i);
    const result = rsi(data, 14);
    expect(result[14]).toBeCloseTo(100);
    expect(result[15]).toBeCloseTo(100);
  });

  it("RSI is 0 for purely downward data (no gains)", () => {
    const data = Array.from({ length: 16 }, (_, i) => 200 - i);
    const result = rsi(data, 14);
    expect(result[14]).toBeCloseTo(0);
    expect(result[15]).toBeCloseTo(0);
  });

  it("RSI is around 50 for flat data", () => {
    // Alternating up/down → roughly balanced gains/losses
    const data: number[] = [];
    for (let i = 0; i < 30; i++) data.push(100 + (i % 2 === 0 ? 1 : -1));
    const result = rsi(data, 14);
    // RSI should be roughly 50 (exact value depends on smoothing)
    for (let i = 14; i < data.length; i++) {
      expect(result[i]).toBeGreaterThan(30);
      expect(result[i]).toBeLessThan(70);
    }
  });

  it("RSI values are bounded between 0 and 100", () => {
    // Random-ish data
    const data = [10, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 15, 12, 8, 10, 13, 11, 14, 16, 18];
    const result = rsi(data, 5);
    for (let i = 5; i < data.length; i++) {
      expect(result[i]).toBeGreaterThanOrEqual(0);
      expect(result[i]).toBeLessThanOrEqual(100);
    }
  });

  it("throws on period <= 0", () => {
    expect(() => rsi([1, 2, 3], 0)).toThrow("period must be > 0");
    expect(() => rsi([1, 2, 3], -1)).toThrow("period must be > 0");
  });

  it("returns empty array for empty data", () => {
    expect(rsi([], 14)).toEqual([]);
  });
});

// ── MACD ─────────────────────────────────────────────────────────

describe("macd", () => {
  it("returns arrays of correct length", () => {
    const data = Array.from({ length: 50 }, (_, i) => 100 + Math.sin(i / 5) * 10);
    const { macdLine, signalLine, histogram } = macd(data);
    expect(macdLine).toHaveLength(50);
    expect(signalLine).toHaveLength(50);
    expect(histogram).toHaveLength(50);
  });

  it("returns NaN before slow EMA is ready", () => {
    const data = Array.from({ length: 50 }, (_, i) => 100 + i);
    const { macdLine } = macd(data, 12, 26, 9);
    // Slow EMA needs 26 bars. Before index 25, macdLine should be NaN.
    for (let i = 0; i < 25; i++) {
      expect(macdLine[i]).toBeNaN();
    }
    expect(Number.isNaN(macdLine[25])).toBe(false);
  });

  it("MACD line is 0 for constant data", () => {
    const data = Array.from({ length: 50 }, () => 100);
    const { macdLine } = macd(data, 12, 26, 9);
    for (let i = 25; i < data.length; i++) {
      expect(macdLine[i]).toBeCloseTo(0);
    }
  });

  it("MACD line is positive in pure uptrend (fast EMA > slow EMA)", () => {
    const data = Array.from({ length: 60 }, (_, i) => 100 + i * 2);
    const { macdLine } = macd(data, 12, 26, 9);
    // After warmup, fast EMA reacts faster → MACD > 0
    for (let i = 30; i < data.length; i++) {
      expect(macdLine[i]).toBeGreaterThan(0);
    }
  });

  it("MACD line is negative in pure downtrend", () => {
    const data = Array.from({ length: 60 }, (_, i) => 200 - i * 2);
    const { macdLine } = macd(data, 12, 26, 9);
    for (let i = 30; i < data.length; i++) {
      expect(macdLine[i]).toBeLessThan(0);
    }
  });

  it("histogram = macdLine - signalLine", () => {
    const data = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 3) * 20);
    const { macdLine, signalLine, histogram } = macd(data, 12, 26, 9);
    for (let i = 0; i < data.length; i++) {
      if (!Number.isNaN(histogram[i]!)) {
        expect(histogram[i]).toBeCloseTo(macdLine[i]! - signalLine[i]!);
      }
    }
  });

  it("custom periods work", () => {
    const data = Array.from({ length: 40 }, (_, i) => 100 + i);
    const { macdLine } = macd(data, 5, 10, 3);
    // With shorter periods, MACD should be valid sooner
    expect(Number.isNaN(macdLine[9])).toBe(false);
  });
});

// ── CCI (Commodity Channel Index) ────────────────────────────────

describe("cci", () => {
  function makeBars(closes: number[]): KlineBar[] {
    return closes.map((c, i) => ({
      timestamp: 1704067200000 + i * 86_400_000,
      open: c - 1,
      high: c + 2,
      low: c - 2,
      close: c,
      volume: 100,
    }));
  }

  it("returns NaN before the period is ready", () => {
    const bars = makeBars([10, 20, 30, 40, 50]);
    const result = cci(bars, 3);
    expect(result).toHaveLength(5);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    // Index 2 = period-1 = first valid
    expect(Number.isNaN(result[2])).toBe(false);
  });

  it("returns 0 for constant-price bars", () => {
    const bars = makeBars(Array.from({ length: 20 }, () => 100));
    const result = cci(bars, 14);
    for (let i = 13; i < 20; i++) {
      expect(result[i]).toBe(0);
    }
  });

  it("is positive in uptrend (TP > SMA(TP))", () => {
    // Steady uptrend: typical price rises monotonically
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 100 + i * 3));
    const result = cci(bars, 14);
    // After warmup, CCI should be positive because current TP > SMA(TP)
    for (let i = 14; i < 30; i++) {
      expect(result[i]).toBeGreaterThan(0);
    }
  });

  it("is negative in downtrend (TP < SMA(TP))", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 200 - i * 3));
    const result = cci(bars, 14);
    for (let i = 14; i < 30; i++) {
      expect(result[i]).toBeLessThan(0);
    }
  });

  it("throws on invalid period", () => {
    const bars = makeBars([1, 2, 3]);
    expect(() => cci(bars, 0)).toThrow("period must be > 0");
    expect(() => cci(bars, -5)).toThrow("period must be > 0");
  });

  it("handles period = 1", () => {
    const bars = makeBars([100, 110, 105]);
    const result = cci(bars, 1);
    // With period=1, SMA(TP) = TP, so CCI = 0 always
    for (let i = 0; i < bars.length; i++) {
      expect(result[i]).toBe(0);
    }
  });

  it("length matches input", () => {
    const bars = makeBars(Array.from({ length: 50 }, (_, i) => 100 + Math.sin(i) * 10));
    const result = cci(bars, 20);
    expect(result).toHaveLength(50);
  });
});

// ── DMI / ADX ────────────────────────────────────────────────────

describe("dmi", () => {
  function makeBars(prices: number[]): KlineBar[] {
    return prices.map((c, i) => ({
      timestamp: 1704067200000 + i * 86_400_000,
      open: c - 1,
      high: c + 2,
      low: c - 2,
      close: c,
      volume: 100,
    }));
  }

  it("returns NaN before 2×period bars", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 100 + i));
    const { plusDI, minusDI, adx } = dmi(bars, 14);
    expect(plusDI).toHaveLength(30);
    // Before period, all NaN
    for (let i = 0; i < 14; i++) {
      expect(plusDI[i]).toBeNaN();
      expect(minusDI[i]).toBeNaN();
    }
    // ADX needs 2×period to have enough DX values
    expect(adx[0]).toBeNaN();
  });

  it("+DI > -DI in uptrend", () => {
    const bars = makeBars(Array.from({ length: 60 }, (_, i) => 100 + i * 2));
    const { plusDI, minusDI } = dmi(bars, 14);
    // After warmup, +DI should dominate in uptrend
    for (let i = 30; i < 60; i++) {
      if (!Number.isNaN(plusDI[i]!) && !Number.isNaN(minusDI[i]!)) {
        expect(plusDI[i]).toBeGreaterThan(minusDI[i]!);
      }
    }
  });

  it("-DI > +DI in downtrend", () => {
    const bars = makeBars(Array.from({ length: 60 }, (_, i) => 200 - i * 2));
    const { plusDI, minusDI } = dmi(bars, 14);
    for (let i = 30; i < 60; i++) {
      if (!Number.isNaN(plusDI[i]!) && !Number.isNaN(minusDI[i]!)) {
        expect(minusDI[i]).toBeGreaterThan(plusDI[i]!);
      }
    }
  });

  it("ADX rises in strong trend", () => {
    const bars = makeBars(Array.from({ length: 80 }, (_, i) => 100 + i * 3));
    const { adx } = dmi(bars, 14);
    // Find first valid ADX pair and check it's positive
    const validAdx = adx.filter((v) => !Number.isNaN(v));
    expect(validAdx.length).toBeGreaterThan(0);
    // In a strong trend, ADX should be above 20
    expect(validAdx[validAdx.length - 1]).toBeGreaterThan(20);
  });

  it("throws on invalid period", () => {
    const bars = makeBars([1, 2, 3]);
    expect(() => dmi(bars, 0)).toThrow("period must be > 0");
  });

  it("handles short data gracefully", () => {
    const bars = makeBars([100, 110]);
    const { plusDI, minusDI, adx } = dmi(bars, 14);
    expect(plusDI.every((v) => Number.isNaN(v))).toBe(true);
    expect(minusDI.every((v) => Number.isNaN(v))).toBe(true);
    expect(adx.every((v) => Number.isNaN(v))).toBe(true);
  });
});

// ── Parabolic SAR ────────────────────────────────────────────────

describe("parabolicSar", () => {
  function makeBars(prices: number[]): KlineBar[] {
    return prices.map((c, i) => ({
      timestamp: 1704067200000 + i * 86_400_000,
      open: c - 0.5,
      high: c + 1,
      low: c - 1,
      close: c,
      volume: 100,
    }));
  }

  it("returns NaN for first bar", () => {
    const bars = makeBars([100, 110, 120]);
    const { sar, trend } = parabolicSar(bars);
    expect(sar[0]).toBeNaN();
    expect(trend[0]).toBe(0);
  });

  it("detects uptrend correctly", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 100 + i * 2));
    const { trend } = parabolicSar(bars);
    // After init, most bars should be uptrend (1)
    const upCount = trend.filter((t) => t === 1).length;
    expect(upCount).toBeGreaterThan(20);
  });

  it("detects downtrend correctly", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 200 - i * 2));
    const { trend } = parabolicSar(bars);
    const downCount = trend.filter((t) => t === -1).length;
    expect(downCount).toBeGreaterThan(20);
  });

  it("SAR is below price in uptrend", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 100 + i * 2));
    const { sar, trend } = parabolicSar(bars);
    for (let i = 5; i < 30; i++) {
      if (trend[i] === 1 && !Number.isNaN(sar[i]!)) {
        expect(sar[i]).toBeLessThan(bars[i]!.close + 5);
      }
    }
  });

  it("handles 2-bar minimum", () => {
    const bars = makeBars([100, 110]);
    const { sar, trend } = parabolicSar(bars);
    expect(sar).toHaveLength(2);
    expect(trend).toHaveLength(2);
  });

  it("returns empty-ish arrays for single bar", () => {
    const bars = makeBars([100]);
    const { sar, trend } = parabolicSar(bars);
    expect(sar).toHaveLength(1);
    expect(sar[0]).toBeNaN();
    expect(trend[0]).toBe(0);
  });

  it("length matches input", () => {
    const bars = makeBars(Array.from({ length: 50 }, (_, i) => 100 + Math.sin(i) * 10));
    const { sar, trend } = parabolicSar(bars);
    expect(sar).toHaveLength(50);
    expect(trend).toHaveLength(50);
  });
});

// ── OBV ──────────────────────────────────────────────────────────

describe("obv", () => {
  function makeBars(closes: number[], volumes: number[]): KlineBar[] {
    return closes.map((c, i) => ({
      open: c,
      high: c + 1,
      low: c - 1,
      close: c,
      volume: volumes[i] ?? 100,
      timestamp: i * 60_000,
    }));
  }

  it("returns empty for empty input", () => {
    expect(obv([])).toEqual([]);
  });

  it("first element equals first bar volume", () => {
    const bars = makeBars([100], [50]);
    const result = obv(bars);
    expect(result[0]).toBe(50);
  });

  it("up close adds volume", () => {
    const bars = makeBars([100, 102, 104], [100, 200, 300]);
    const result = obv(bars);
    // 100, 100+200=300, 300+300=600
    expect(result).toEqual([100, 300, 600]);
  });

  it("down close subtracts volume", () => {
    const bars = makeBars([104, 102, 100], [100, 200, 300]);
    const result = obv(bars);
    // 100, 100-200=-100, -100-300=-400
    expect(result).toEqual([100, -100, -400]);
  });

  it("flat close keeps volume unchanged", () => {
    const bars = makeBars([100, 100, 100], [100, 200, 300]);
    const result = obv(bars);
    expect(result).toEqual([100, 100, 100]);
  });

  it("mixed moves compute correctly", () => {
    const bars = makeBars([100, 105, 103, 103, 110], [10, 20, 30, 40, 50]);
    const result = obv(bars);
    // 10, 10+20=30, 30-30=0, 0(flat), 0+50=50
    expect(result).toEqual([10, 30, 0, 0, 50]);
  });
});

// ── Williams %R ──────────────────────────────────────────────────

describe("williamsR", () => {
  function makeBars(data: { high: number; low: number; close: number }[]): KlineBar[] {
    return data.map((d, i) => ({
      open: d.close,
      high: d.high,
      low: d.low,
      close: d.close,
      volume: 100,
      timestamp: i * 60_000,
    }));
  }

  it("throws on non-positive period", () => {
    expect(() => williamsR([], 0)).toThrow("period must be > 0");
    expect(() => williamsR([], -1)).toThrow("period must be > 0");
  });

  it("returns NaN before period is ready", () => {
    const bars = makeBars([
      { high: 110, low: 90, close: 100 },
      { high: 112, low: 92, close: 105 },
      { high: 115, low: 95, close: 110 },
    ]);
    const result = williamsR(bars, 3);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    expect(result[2]).not.toBeNaN();
  });

  it("close at highest high returns 0 (most overbought)", () => {
    const bars = makeBars([
      { high: 110, low: 90, close: 100 },
      { high: 120, low: 95, close: 120 }, // close = HH
    ]);
    const result = williamsR(bars, 2);
    expect(result[1]).toBeCloseTo(0, 5);
  });

  it("close at lowest low returns -100 (most oversold)", () => {
    const bars = makeBars([
      { high: 110, low: 90, close: 100 },
      { high: 115, low: 85, close: 85 }, // close = LL
    ]);
    const result = williamsR(bars, 2);
    expect(result[1]).toBe(-100);
  });

  it("close in middle returns value between -100 and 0", () => {
    const bars = makeBars([
      { high: 110, low: 90, close: 100 },
      { high: 110, low: 90, close: 100 },
    ]);
    const result = williamsR(bars, 2);
    // HH=110, LL=90, close=100 → (110-100)/(110-90)*(-100) = -50
    expect(result[1]).toBeCloseTo(-50, 5);
  });

  it("returns 0 when high equals low (zero range)", () => {
    const bars = makeBars([{ high: 100, low: 100, close: 100 }]);
    const result = williamsR(bars, 1);
    expect(result[0]).toBe(0);
  });

  it("length matches input", () => {
    const bars = makeBars(
      Array.from({ length: 30 }, (_, i) => ({
        high: 110 + i,
        low: 90 + i,
        close: 100 + i,
      })),
    );
    const result = williamsR(bars, 14);
    expect(result).toHaveLength(30);
  });
});

// ── ROC ──────────────────────────────────────────────────────────

describe("roc", () => {
  it("throws on non-positive period", () => {
    expect(() => roc([], 0)).toThrow("period must be > 0");
    expect(() => roc([], -5)).toThrow("period must be > 0");
  });

  it("returns NaN before period is ready", () => {
    const result = roc([100, 105, 110], 2);
    expect(result[0]).toBeNaN();
    expect(result[1]).toBeNaN();
    expect(result[2]).not.toBeNaN();
  });

  it("computes correct positive ROC in uptrend", () => {
    // ROC(2) at index 2 = (110 - 100) / 100 * 100 = 10%
    const result = roc([100, 105, 110], 2);
    expect(result[2]).toBeCloseTo(10, 5);
  });

  it("computes correct negative ROC in downtrend", () => {
    // ROC(1) at index 1 = (90 - 100) / 100 * 100 = -10%
    const result = roc([100, 90, 80], 1);
    expect(result[1]).toBeCloseTo(-10, 5);
    expect(result[2]).toBeCloseTo(-11.111, 2); // (80-90)/90*100
  });

  it("returns 0 for flat prices", () => {
    const result = roc([100, 100, 100, 100], 1);
    expect(result[1]).toBe(0);
    expect(result[2]).toBe(0);
    expect(result[3]).toBe(0);
  });

  it("handles zero previous close gracefully", () => {
    const result = roc([0, 50], 1);
    expect(result[1]).toBe(0); // prev=0 → returns 0 by design
  });

  it("length matches input", () => {
    const closes = Array.from({ length: 50 }, (_, i) => 100 + i);
    const result = roc(closes, 5);
    expect(result).toHaveLength(50);
  });
});

// ── Ichimoku ─────────────────────────────────────────────────────

describe("ichimoku", () => {
  function makeBars(prices: number[]): KlineBar[] {
    return prices.map((p, i) => ({
      open: p,
      high: p + 5,
      low: p - 5,
      close: p,
      volume: 100,
      timestamp: i * 60_000,
    }));
  }

  it("returns NaN for early tenkan values", () => {
    const bars = makeBars(Array.from({ length: 30 }, (_, i) => 100 + i));
    const result = ichimoku(bars, 9, 26, 52);
    expect(result.tenkan[0]).toBeNaN();
    expect(result.tenkan[7]).toBeNaN();
    expect(result.tenkan[8]).not.toBeNaN();
  });

  it("tenkan is midpoint of high/low over period", () => {
    // 10 bars with price 100..109, high=price+5, low=price-5
    const bars = makeBars(Array.from({ length: 10 }, (_, i) => 100 + i));
    const result = ichimoku(bars, 5, 26, 52);
    // At index 4: prices 100-104, highs 105-109, lows 95-99
    // midpoint = (109 + 95) / 2 = 102
    expect(result.tenkan[4]).toBe(102);
  });

  it("kijun is NaN until kijunPeriod-1", () => {
    const bars = makeBars(Array.from({ length: 60 }, (_, i) => 100 + i));
    const result = ichimoku(bars, 9, 26, 52);
    expect(result.kijun[24]).toBeNaN();
    expect(result.kijun[25]).not.toBeNaN();
  });

  it("chikou shifts close back by displacement", () => {
    const prices = Array.from({ length: 60 }, (_, i) => 100 + i);
    const bars = makeBars(prices);
    const result = ichimoku(bars, 9, 26, 52, 26);
    // chikou[0] = bars[26].close = 126
    expect(result.chikou[0]).toBe(126);
  });

  it("all arrays have same length as input", () => {
    const bars = makeBars(Array.from({ length: 100 }, (_, i) => 100 + i));
    const result = ichimoku(bars, 9, 26, 52);
    expect(result.tenkan).toHaveLength(100);
    expect(result.kijun).toHaveLength(100);
    expect(result.senkouA).toHaveLength(100);
    expect(result.senkouB).toHaveLength(100);
    expect(result.chikou).toHaveLength(100);
  });

  it("senkouA is displaced forward", () => {
    const bars = makeBars(Array.from({ length: 100 }, (_, i) => 100 + i));
    const result = ichimoku(bars, 9, 26, 52, 26);
    // senkouA should be NaN at the very start (needs tenkan+kijun ready + displacement)
    expect(result.senkouA[0]).toBeNaN();
    // After displacement offset from when tenkan/kijun become valid
    expect(result.senkouA[51]).not.toBeNaN(); // kijun ready at 25, displaced +26 = 51
  });
});

// ── Stochastic / KDJ ────────────────────────────────────────────

describe("stochastic", () => {
  function makeBars(data: { high: number; low: number; close: number }[]): KlineBar[] {
    return data.map((d, i) => ({
      open: d.close,
      high: d.high,
      low: d.low,
      close: d.close,
      volume: 100,
      timestamp: i * 60_000,
    }));
  }

  it("throws on non-positive period", () => {
    expect(() => stochastic([], 0)).toThrow("period must be > 0");
  });

  it("returns NaN before warmup", () => {
    const bars = makeBars(
      Array.from({ length: 20 }, (_, i) => ({
        high: 110 + i,
        low: 90 + i,
        close: 100 + i,
      })),
    );
    const { k, d, j } = stochastic(bars, 9, 3, 3);
    // rawK ready at index 8, k (sma 3 of rawK) ready at 10, d (sma 3 of k) ready at 12
    expect(k[9]).toBeNaN();
    expect(k[10]).not.toBeNaN();
    expect(d[11]).toBeNaN();
    expect(d[12]).not.toBeNaN();
    expect(j[12]).not.toBeNaN();
  });

  it("J = 3K - 2D", () => {
    const bars = makeBars(
      Array.from({ length: 30 }, (_, i) => ({
        high: 110 + i,
        low: 90 + i,
        close: 100 + i,
      })),
    );
    const { k, d, j } = stochastic(bars, 9, 3, 3);
    for (let i = 0; i < 30; i++) {
      if (!Number.isNaN(j[i])) {
        expect(j[i]).toBeCloseTo(3 * k[i]! - 2 * d[i]!, 10);
      }
    }
  });

  it("K/D stay between 0 and 100 in normal data", () => {
    const bars = makeBars(
      Array.from({ length: 50 }, (_, i) => ({
        high: 110 + Math.sin(i) * 10,
        low: 90 + Math.sin(i) * 10,
        close: 100 + Math.sin(i) * 10,
      })),
    );
    const { k, d } = stochastic(bars, 9, 3, 3);
    for (let i = 0; i < 50; i++) {
      if (!Number.isNaN(k[i])) {
        expect(k[i]!).toBeGreaterThanOrEqual(0);
        expect(k[i]!).toBeLessThanOrEqual(100);
      }
      if (!Number.isNaN(d[i])) {
        expect(d[i]!).toBeGreaterThanOrEqual(0);
        expect(d[i]!).toBeLessThanOrEqual(100);
      }
    }
  });

  it("flat range returns K=50", () => {
    const bars = makeBars(Array.from({ length: 20 }, () => ({ high: 100, low: 100, close: 100 })));
    const { k } = stochastic(bars, 5, 1, 1);
    // rawK when high===low → 50, sma(1) of 50 → 50
    for (let i = 4; i < 20; i++) {
      expect(k[i]).toBe(50);
    }
  });

  it("length matches input", () => {
    const bars = makeBars(
      Array.from({ length: 40 }, (_, i) => ({
        high: 110 + i,
        low: 90 + i,
        close: 100 + i,
      })),
    );
    const { k, d, j } = stochastic(bars, 9, 3, 3);
    expect(k).toHaveLength(40);
    expect(d).toHaveLength(40);
    expect(j).toHaveLength(40);
  });
});
