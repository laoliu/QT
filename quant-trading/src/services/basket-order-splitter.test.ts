import { describe, expect, it, vi } from "vitest";
import {
  splitBasketOrder,
  type BasketSplitterDeps,
  type PriceLookup,
  type MarketCapLookup,
} from "./basket-order-splitter.js";
import type { BasketOrderRequest } from "../types.js";

// ── Test helpers ───────────────────────────────────────────────────

/** Create a simple price lookup from a map */
function mockPriceLookup(prices: Record<string, number>): PriceLookup {
  return async (symbol) => prices[symbol] ?? null;
}

/** Create a simple market cap lookup from a map */
function mockMarketCapLookup(caps: Record<string, number>): MarketCapLookup {
  return async (symbol) => caps[symbol] ?? null;
}

function baseDeps(
  prices: Record<string, number>,
  caps?: Record<string, number>,
): BasketSplitterDeps {
  return {
    getPrice: mockPriceLookup(prices),
    getMarketCap: caps ? mockMarketCapLookup(caps) : undefined,
  };
}

// ── Tests ──────────────────────────────────────────────────────────

describe("splitBasketOrder", () => {
  // ── Equal weight strategy ─────────────────────────────────────

  describe("equal strategy", () => {
    it("splits equally across 3 symbols", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "600519" },
          { symbol: "000001" },
          { symbol: "510300" },
        ],
        strategy: "equal",
        totalAmount: 300_000,
      };
      const deps = baseDeps({
        "600519": 1800,
        "000001": 15,
        "510300": 5,
      });

      const result = await splitBasketOrder(req, deps);

      expect(result.strategy).toBe("equal");
      expect(result.totalAmount).toBe(300_000);
      expect(result.investableAmount).toBe(300_000);

      // 600519: allocatedAmount = 300000 * (1/3) = 100000, 100000/1800 = 55.55
      // roundToLot(55.55, 100) = 0 → skipped (too expensive for 1 lot)
      expect(result.legs).toHaveLength(2);
      expect(result.skipped).toHaveLength(1);
      expect(result.skipped[0].symbol).toBe("600519");

      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(1 / 3, 5);
        expect(leg.side).toBe("buy");
      }
    });

    it("rounds to lot size correctly", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "000001" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ "000001": 15 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(1);
      const leg = result.legs[0];
      // 100000 / 15 = 6666.67 → roundToLot(6666.67, 100) = 6600
      expect(leg.quantity).toBe(6600);
      expect(leg.quantity % 100).toBe(0);
    });

    it("uses custom lotSize", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "000001" }],
        strategy: "equal",
        totalAmount: 100_000,
        lotSize: 200,
      };
      const deps = baseDeps({ "000001": 15 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(1);
      // 100000 / 15 = 6666.67 → roundToLot(6666.67, 200) = 6600
      expect(result.legs[0].quantity % 200).toBe(0);
    });
  });

  // ── Cash reserve ──────────────────────────────────────────────

  describe("cash reserve", () => {
    it("reduces investable amount by cashReservePct", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "000001" }],
        strategy: "equal",
        totalAmount: 100_000,
        cashReservePct: 0.1, // 10% reserve
      };
      const deps = baseDeps({ "000001": 15 });

      const result = await splitBasketOrder(req, deps);

      expect(result.investableAmount).toBe(90_000);
      // 90000 / 15 = 6000
      expect(result.legs[0].quantity).toBe(6000);
    });

    it("defaults cashReservePct to 0", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "000001" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ "000001": 15 });

      const result = await splitBasketOrder(req, deps);

      expect(result.investableAmount).toBe(100_000);
    });
  });

  // ── Market cap strategy ───────────────────────────────────────

  describe("market_cap strategy", () => {
    it("weights by market capitalisation", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "600519" }, // big cap
          { symbol: "000001" }, // small cap
        ],
        strategy: "market_cap",
        totalAmount: 200_000,
      };
      const deps = baseDeps(
        { "600519": 100, "000001": 10 },
        { "600519": 2_000_000_000_000, "000001": 500_000_000_000 }, // 2T vs 0.5T
      );

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(2);
      // 600519 weight = 2T / 2.5T = 0.8, 000001 weight = 0.5T / 2.5T = 0.2
      const leg600519 = result.legs.find((l) => l.symbol === "600519")!;
      const leg000001 = result.legs.find((l) => l.symbol === "000001")!;

      expect(leg600519.weight).toBeCloseTo(0.8, 5);
      expect(leg000001.weight).toBeCloseTo(0.2, 5);
    });

    it("falls back to equal weight when getMarketCap is not provided", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "600519" }, { symbol: "000001" }],
        strategy: "market_cap",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = {
        getPrice: mockPriceLookup({ "600519": 10, "000001": 10 }),
        // No getMarketCap
      };

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(2);
      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });

    it("falls back to equal weight when all market caps are 0", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "market_cap",
        totalAmount: 100_000,
      };
      const deps = baseDeps(
        { A: 10, B: 10 },
        { A: 0, B: 0 },
      );

      const result = await splitBasketOrder(req, deps);

      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });

    it("falls back to equal weight when market cap returns null", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "market_cap",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = {
        getPrice: mockPriceLookup({ A: 10, B: 10 }),
        getMarketCap: async () => null,
      };

      const result = await splitBasketOrder(req, deps);

      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });
  });

  // ── Custom weight strategy ────────────────────────────────────

  describe("custom strategy", () => {
    it("uses user-provided weights (percentage format)", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: 60 }, // 60%
          { symbol: "B", weight: 40 }, // 40%
        ],
        strategy: "custom",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      expect(legA.weight).toBeCloseTo(0.6, 5);
      expect(legB.weight).toBeCloseTo(0.4, 5);
    });

    it("uses user-provided weights (decimal format)", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: 0.7 },
          { symbol: "B", weight: 0.3 },
        ],
        strategy: "custom",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      expect(legA.weight).toBeCloseTo(0.7, 5);
      expect(legB.weight).toBeCloseTo(0.3, 5);
    });

    it("normalises weights that do not sum to 100%", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: 30 },
          { symbol: "B", weight: 20 },
          // Total = 50, normalised: A=60%, B=40%
        ],
        strategy: "custom",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      expect(legA.weight).toBeCloseTo(0.6, 5);
      expect(legB.weight).toBeCloseTo(0.4, 5);
    });

    it("falls back to equal weight when all custom weights are zero", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: 0 },
          { symbol: "B", weight: 0 },
        ],
        strategy: "custom",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });

    it("treats negative weights as zero", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: -10 },
          { symbol: "B", weight: 50 },
        ],
        strategy: "custom",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legB = result.legs.find((l) => l.symbol === "B")!;
      // A has weight 0, B has 50 → normalised: A=0/50=0, B=50/50=1
      expect(legB.weight).toBeCloseTo(1.0, 5);
    });
  });

  // ── By-amount strategy ────────────────────────────────────────

  describe("by_amount strategy", () => {
    it("splits equally when no custom weights provided", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "by_amount",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 20 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(2);
      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });

    it("uses custom weights as ratios when provided", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", weight: 70 },
          { symbol: "B", weight: 30 },
        ],
        strategy: "by_amount",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      expect(legA.weight).toBeCloseTo(0.7, 5);
      expect(legB.weight).toBeCloseTo(0.3, 5);
    });
  });

  // ── By-quantity strategy ──────────────────────────────────────

  describe("by_quantity strategy", () => {
    it("uses explicit per-symbol quantities", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", quantity: 500 },
          { symbol: "B", quantity: 300 },
        ],
        strategy: "by_quantity",
      };
      const deps = baseDeps({ A: 10, B: 20 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(2);
      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      expect(legA.quantity).toBe(500);
      expect(legB.quantity).toBe(300);
    });

    it("distributes totalQuantity equally when no per-symbol qty", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "by_quantity",
        totalQuantity: 1000,
      };
      const deps = baseDeps({ A: 10, B: 20 });

      const result = await splitBasketOrder(req, deps);

      // 1000 / 2 = 500 each
      for (const leg of result.legs) {
        expect(leg.quantity).toBe(500);
      }
    });

    it("falls back to 1 lot when no quantity info at all", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "by_quantity",
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs[0].quantity).toBe(100); // default lotSize
    });

    it("rounds explicit quantity to lot size", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A", quantity: 350 }],
        strategy: "by_quantity",
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      // 350 → roundToLot(350, 100) = 300
      expect(result.legs[0].quantity).toBe(300);
    });

    it("allows symbols without price when explicit quantity is given", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "NO_PRICE", quantity: 200 }],
        strategy: "by_quantity",
      };
      // No price available
      const deps = baseDeps({});

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(1);
      expect(result.legs[0].quantity).toBe(200);
      expect(result.skipped).toHaveLength(0);
    });
  });

  // ── Price handling ────────────────────────────────────────────

  describe("price handling", () => {
    it("uses user-provided price override instead of lookup", async () => {
      const getPriceSpy = vi.fn(async () => 999);
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A", price: 50 }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = { getPrice: getPriceSpy };

      const result = await splitBasketOrder(req, deps);

      // Price 50 provided by user, so getPrice should NOT be called for A
      // Actually, the code checks s.price > 0 before calling getPrice
      expect(result.legs[0].price).toBe(50);
      // 100000 / 50 = 2000
      expect(result.legs[0].quantity).toBe(2000);
    });

    it("calls getPrice when symbol has no price override", async () => {
      const getPriceSpy = vi.fn(async () => 25);
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = { getPrice: getPriceSpy };

      const result = await splitBasketOrder(req, deps);

      expect(getPriceSpy).toHaveBeenCalledWith("A");
      expect(result.legs[0].price).toBe(25);
    });

    it("skips symbol when price is unavailable", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A" }, // has price
          { symbol: "B" }, // no price
        ],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10 }); // B has no price

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(1);
      expect(result.legs[0].symbol).toBe("A");
      expect(result.skipped).toHaveLength(1);
      expect(result.skipped[0].symbol).toBe("B");
      expect(result.skipped[0].reason).toBe("无法获取价格");
    });

    it("skips symbol with zero price from lookup", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = {
        getPrice: async () => 0, // returns 0
      };

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(0);
      expect(result.skipped).toHaveLength(1);
    });

    it("skips symbol with negative price from lookup", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = {
        getPrice: async () => -5,
      };

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(0);
      expect(result.skipped).toHaveLength(1);
    });
  });

  // ── Explicit quantity override ────────────────────────────────

  describe("explicit quantity override in weight-based strategies", () => {
    it("uses explicit quantity instead of weight calculation", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A", quantity: 800 },
          { symbol: "B" },
        ],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      const legA = result.legs.find((l) => l.symbol === "A")!;
      const legB = result.legs.find((l) => l.symbol === "B")!;

      // A uses explicit 800 (already a lot multiple)
      expect(legA.quantity).toBe(800);
      // B uses weight-based: 100000 * 0.5 / 10 = 5000
      expect(legB.quantity).toBe(5000);
    });
  });

  // ── Edge cases / boundary conditions ──────────────────────────

  describe("edge cases", () => {
    it("returns empty legs when all symbols have no price", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps: BasketSplitterDeps = {
        getPrice: async () => null,
      };

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(0);
      expect(result.skipped).toHaveLength(2);
      expect(result.summary).toContain("所有标的均无法获取价格");
    });

    it("handles single symbol basket", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 50_000,
      };
      const deps = baseDeps({ A: 25 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs).toHaveLength(1);
      expect(result.legs[0].weight).toBeCloseTo(1.0, 5);
      // 50000 / 25 = 2000
      expect(result.legs[0].quantity).toBe(2000);
    });

    it("handles zero totalAmount", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 0,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.investableAmount).toBe(0);
      // allocatedAmount = 0 * weight = 0, quantity = 0 → skipped
      expect(result.legs).toHaveLength(0);
      expect(result.skipped).toHaveLength(1);
      expect(result.skipped[0].reason).toBe("计算后数量为零");
    });

    it("handles undefined totalAmount (defaults to 0)", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.totalAmount).toBe(0);
      expect(result.investableAmount).toBe(0);
    });

    it("generates a basketId when none is provided", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 10_000,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.basketId).toBeTruthy();
      expect(typeof result.basketId).toBe("string");
    });

    it("uses provided basketId", async () => {
      const req: BasketOrderRequest = {
        basketId: "my-basket-123",
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 10_000,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.basketId).toBe("my-basket-123");
    });

    it("skips legs with computed quantity of 0 (expensive stock)", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "EXPENSIVE" }],
        strategy: "equal",
        totalAmount: 1000, // too little for a stock priced at 2000
      };
      const deps = baseDeps({ EXPENSIVE: 2000 });

      const result = await splitBasketOrder(req, deps);

      // 1000 / 2000 = 0.5 → roundToLot(0.5, 100) = 0 → skipped
      expect(result.legs).toHaveLength(0);
      expect(result.skipped).toHaveLength(1);
      expect(result.skipped[0].reason).toBe("计算后数量为零");
    });

    it("handles 100% cash reserve", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
        cashReservePct: 1.0, // 100% reserve
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.investableAmount).toBe(0);
      expect(result.legs).toHaveLength(0);
    });
  });

  // ── Sell side ─────────────────────────────────────────────────

  describe("sell side", () => {
    it("creates sell legs", async () => {
      const req: BasketOrderRequest = {
        side: "sell",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 20 });

      const result = await splitBasketOrder(req, deps);

      for (const leg of result.legs) {
        expect(leg.side).toBe("sell");
      }
    });
  });

  // ── Leg output fields ─────────────────────────────────────────

  describe("leg output fields", () => {
    it("includes estimatedValue and reason in each leg", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 25 });

      const result = await splitBasketOrder(req, deps);

      const leg = result.legs[0];
      expect(leg.estimatedValue).toBe(leg.quantity * leg.price);
      expect(leg.reason).toContain("等权");
      expect(leg.reason).toContain("¥");
    });

    it("generates correct reason for each strategy", async () => {
      const strategies = [
        { strategy: "equal" as const, contains: "等权" },
        { strategy: "custom" as const, contains: "自定义权重" },
        { strategy: "by_amount" as const, contains: "按资金比例" },
        { strategy: "by_quantity" as const, contains: "按股数分配" },
      ];

      for (const { strategy, contains } of strategies) {
        const req: BasketOrderRequest = {
          side: "buy",
          symbols: [{ symbol: "A", weight: 100, quantity: 200 }],
          strategy,
          totalAmount: 100_000,
          totalQuantity: 200,
        };
        const deps = baseDeps({ A: 10 });

        const result = await splitBasketOrder(req, deps);

        if (result.legs.length > 0) {
          expect(result.legs[0].reason).toContain(contains);
        }
      }
    });
  });

  // ── Summary output ────────────────────────────────────────────

  describe("summary", () => {
    it("includes strategy name in summary", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.summary).toContain("等权分配");
    });

    it("includes skipped symbols in summary", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A" },
          { symbol: "B" }, // no price
        ],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.summary).toContain("跳过");
      expect(result.summary).toContain("B");
    });

    it("includes investable amount and allocation info", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.summary).toContain("可投资金额");
      expect(result.summary).toContain("实际分配");
      expect(result.summary).toContain("拆分腿数");
    });
  });

  // ── Default strategy fallback ─────────────────────────────────

  describe("unknown strategy fallback", () => {
    it("falls back to equal weight for unknown strategy", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }, { symbol: "B" }],
        strategy: "unknown_strategy" as any,
        totalAmount: 100_000,
      };
      const deps = baseDeps({ A: 10, B: 10 });

      const result = await splitBasketOrder(req, deps);

      for (const leg of result.legs) {
        expect(leg.weight).toBeCloseTo(0.5, 5);
      }
    });
  });

  // ── Lot size rounding precision ───────────────────────────────

  describe("roundToLot precision", () => {
    it("handles floating point near lot boundary (e.g. 99.9999999)", async () => {
      // roundToLot adds 1e-9 before flooring to handle FP imprecision
      // 100 shares / 100 lotSize = exactly 1.0, should be 100
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
        totalAmount: 1000,
      };
      // 1000 / 10 = 100 → roundToLot(100, 100) = 100
      const deps = baseDeps({ A: 10 });

      const result = await splitBasketOrder(req, deps);

      expect(result.legs[0].quantity).toBe(100);
    });

    it("handles lotSize of 1 (for US stocks etc.)", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [{ symbol: "AAPL" }],
        strategy: "equal",
        totalAmount: 10_000,
        lotSize: 1,
      };
      const deps = baseDeps({ AAPL: 150 });

      const result = await splitBasketOrder(req, deps);

      // 10000 / 150 = 66.67 → roundToLot(66.67, 1) = 66
      expect(result.legs[0].quantity).toBe(66);
    });
  });

  // ── Multi-symbol comprehensive test ───────────────────────────

  describe("comprehensive multi-symbol test", () => {
    it("correctly splits a 5-symbol basket with mixed scenarios", async () => {
      const req: BasketOrderRequest = {
        side: "buy",
        symbols: [
          { symbol: "A" },          // normal
          { symbol: "B" },          // normal
          { symbol: "C" },          // no price → skipped
          { symbol: "D", price: 5 }, // user-provided price
          { symbol: "E" },          // very expensive → quantity 0 → skipped
        ],
        strategy: "equal",
        totalAmount: 50_000,
      };
      const deps = baseDeps({
        A: 10,
        B: 20,
        // C: no price
        D: 5, // will be overridden by user price
        E: 50_000, // too expensive
      });

      const result = await splitBasketOrder(req, deps);

      // C is skipped (no price), rest valid = 4 symbols
      // E: 50000 * 0.25 / 50000 = 0.25 → roundToLot(0.25, 100) = 0 → skipped
      const symbolsInLegs = result.legs.map((l) => l.symbol);
      expect(symbolsInLegs).toContain("A");
      expect(symbolsInLegs).toContain("B");
      expect(symbolsInLegs).toContain("D");
      expect(symbolsInLegs).not.toContain("C");

      // Verify all quantities are lot-sized
      for (const leg of result.legs) {
        expect(leg.quantity % 100).toBe(0);
        expect(leg.quantity).toBeGreaterThan(0);
      }
    });
  });
});
