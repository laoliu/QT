import { describe, expect, it } from "vitest";
import { computePositionSize, type SizingParams } from "./position-sizer.js";

const base: SizingParams = {
  method: "fixed_amount",
  equity: 500_000,
  price: 25.0,
};

describe("computePositionSize", () => {
  describe("fixed_amount", () => {
    it("buys ¥10000 worth (default)", () => {
      const r = computePositionSize(base);
      // 10000 / 25 = 400 shares
      expect(r.quantity).toBe(400);
      expect(r.method).toBe("fixed_amount");
    });

    it("uses custom amount", () => {
      const r = computePositionSize({ ...base, amount: 50_000 });
      // 50000 / 25 = 2000
      expect(r.quantity).toBe(2000);
    });

    it("rounds down to lot size", () => {
      const r = computePositionSize({ ...base, price: 33 });
      // 10000 / 33 = 303.03 → floor to 300
      expect(r.quantity).toBe(300);
    });
  });

  describe("fixed_pct", () => {
    it("allocates 10% of equity", () => {
      const r = computePositionSize({ ...base, method: "fixed_pct", pct: 0.1 });
      // 500k * 10% / 25 = 2000
      expect(r.quantity).toBe(2000);
    });
  });

  describe("risk_pct", () => {
    it("sizes by risk per trade with stop percentage", () => {
      const r = computePositionSize({
        ...base,
        method: "risk_pct",
        riskPct: 0.02,
        stopPct: 0.05,
      });
      // riskAmt = 500k * 2% = 10000, stopDist = 25*5% = 1.25, qty = 10000/1.25 = 8000
      // Capped by maxPositionPct (default 30%): 500k*30%/25 = 6000
      expect(r.quantity).toBe(6000);
    });

    it("returns 0 when stopDistance is 0", () => {
      const r = computePositionSize({
        ...base,
        method: "risk_pct",
        riskPct: 0.02,
        stopDistance: 0,
      });
      expect(r.quantity).toBe(0);
    });
  });

  describe("kelly", () => {
    it("computes half-Kelly by default", () => {
      const r = computePositionSize({
        ...base,
        method: "kelly",
        winRate: 0.6,
        winLossRatio: 2.0,
      });
      // f = (0.6*2.0 - 0.4)/2.0 = (1.2-0.4)/2 = 0.4
      // half-Kelly: 0.4/2 = 0.2
      // amount = 500k*0.2 = 100k
      // qty = 100k/25 = 4000
      // Capped by maxPositionPct 30%: 500k*30%/25 = 6000 → no cap needed
      expect(r.quantity).toBe(4000);
      expect(r.kellyFraction).toBeCloseTo(0.2);
    });

    it("clamps negative Kelly to 0", () => {
      const r = computePositionSize({
        ...base,
        method: "kelly",
        winRate: 0.2,
        winLossRatio: 0.5,
      });
      // f = (0.2*0.5 - 0.8)/0.5 = (0.1-0.8)/0.5 = -1.4 → clamped to 0
      expect(r.quantity).toBe(0);
      expect(r.kellyFraction).toBe(0);
    });
  });

  describe("atr_vol", () => {
    it("sizes by ATR volatility", () => {
      const r = computePositionSize({
        ...base,
        method: "atr_vol",
        riskPct: 0.02,
        atrValue: 1.5,
        atrMultiplier: 2,
      });
      // riskAmt = 500k * 2% = 10000
      // riskPerShare = 1.5 * 2 = 3.0
      // rawQty = 10000 / 3.0 = 3333.33 → floor to 3300
      expect(r.quantity).toBe(3300);
    });
  });

  describe("limits", () => {
    it("caps at maxPositionPct", () => {
      const r = computePositionSize({
        ...base,
        amount: 500_000, // would be 20000 shares
        maxPositionPct: 0.1, // max 500k*10%/25 = 2000 shares
      });
      expect(r.quantity).toBe(2000);
    });

    it("caps at maxQuantity", () => {
      const r = computePositionSize({
        ...base,
        amount: 100_000, // 4000 shares
        maxQuantity: 1000,
      });
      expect(r.quantity).toBe(1000);
    });

    it("uses custom lot size", () => {
      const r = computePositionSize({ ...base, amount: 10_000, lotSize: 10 });
      // 10000/25 = 400 → already divisible by 10
      expect(r.quantity).toBe(400);

      const r2 = computePositionSize({ ...base, amount: 10_000, price: 33, lotSize: 10 });
      // 10000/33 = 303.03 → floor to 300 (multiple of 10)
      expect(r2.quantity).toBe(300);
    });
  });
});
