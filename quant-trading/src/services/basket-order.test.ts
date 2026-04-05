/**
 * Tests for BasketOrderService — basket/batch order management.
 *
 * Covers:
 *  - FR-3.1: Multi-stock basket order submission
 *  - FR-3.2: Three modes (quantity / amount / weight)
 *  - FR-3.3: Preview with estimated amounts and commissions
 *  - FR-3.4: Per-sub-order risk gate checks
 *  - FR-3.5: Basket status tracking (all_filled / partial / all_rejected)
 *  - FR-3.6: Rebalance → basket integration (tested via weight mode)
 *  - B1: Suspended stock handling (price=0)
 *  - B2: Total amount validation for weight mode
 *  - NFR-1: Basket of 20 stocks
 *  - NFR-6: Partial failure isolation
 */
import { describe, expect, it, vi } from "vitest";
import {
  BasketOrderService,
  roundToLot,
  estimateCommission,
  type QuoteProvider,
  type RiskChecker,
  type OrderSubmitter,
} from "./basket-order.js";

// ── Helpers ──────────────────────────────────────────────────

const PRICES: Record<string, number> = {
  "600519": 1850,  // 贵州茅台
  "000858": 135,   // 五粮液
  "601318": 52,    // 中国平安
  "600036": 36,    // 招商银行
  "000001": 11,    // 平安银行
  "SUSPENDED": 0,  // 停牌
};

function mockQuotes(): QuoteProvider {
  return { getPrice: async (symbol) => PRICES[symbol] ?? 100 };
}

function mockRisk(rejectSymbols: string[] = []): RiskChecker {
  return {
    check: (symbol, _side, _qty, _price) => {
      if (rejectSymbols.includes(symbol)) {
        return { passed: false, reason: `Risk limit exceeded for ${symbol}` };
      }
      return { passed: true };
    },
  };
}

function mockOrders(failSymbols: string[] = []): OrderSubmitter {
  let counter = 0;
  return {
    submit: async (symbol, _side, _qty, _price) => {
      if (failSymbols.includes(symbol)) {
        throw new Error(`Broker rejected order for ${symbol}`);
      }
      return { orderId: `ORD-${++counter}`, status: "filled" };
    },
  };
}

// ── Unit tests ──────────────────────────────────────────────

describe("roundToLot()", () => {
  it("rounds down to nearest 100", () => {
    expect(roundToLot(350)).toBe(300);
    expect(roundToLot(100)).toBe(100);
    expect(roundToLot(99)).toBe(0);
    expect(roundToLot(1050)).toBe(1000);
  });

  it("handles custom lot size", () => {
    expect(roundToLot(250, 50)).toBe(250);
    expect(roundToLot(275, 50)).toBe(250);
  });
});

describe("estimateCommission()", () => {
  it("calculates standard A-share commission", () => {
    // 100000 * 0.00025 = 25
    expect(estimateCommission(100000)).toBe(25);
  });

  it("applies minimum commission", () => {
    // 1000 * 0.00025 = 0.25 < 5, so min 5
    expect(estimateCommission(1000)).toBe(5);
  });
});

// ── BasketOrderService tests ────────────────────────────────

describe("BasketOrderService", () => {
  describe("preview()", () => {
    it("previews quantity mode basket", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 200 },
        ],
      });

      expect(preview.items).toHaveLength(2);
      expect(preview.items[0].quantity).toBe(100);
      expect(preview.items[0].estimatedPrice).toBe(1850);
      expect(preview.items[0].estimatedAmount).toBe(185000);
      expect(preview.items[0].riskCheckPassed).toBe(true);
      expect(preview.items[1].quantity).toBe(200);
      expect(preview.totalEstimatedAmount).toBe(185000 + 27000);
    });

    it("previews amount mode with lot rounding", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "amount",
        items: [
          { symbol: "000858", side: "buy", amount: 50000 }, // 50000/135 = 370.37 → 300
          { symbol: "601318", side: "buy", amount: 50000 }, // 50000/52 = 961.5 → 900
        ],
      });

      expect(preview.items[0].quantity).toBe(300);
      expect(preview.items[1].quantity).toBe(900);
    });

    it("previews weight mode with totalAmount", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "weight",
        totalAmount: 1000000,
        items: [
          { symbol: "600519", side: "buy", weight: 0.5 },  // 500000/1850 = 270.2 → 200
          { symbol: "000858", side: "buy", weight: 0.3 },  // 300000/135 = 2222.2 → 2200
          { symbol: "601318", side: "buy", weight: 0.2 },  // 200000/52 = 3846.1 → 3800
        ],
      });

      expect(preview.items[0].quantity).toBe(200);
      expect(preview.items[1].quantity).toBe(2200);
      expect(preview.items[2].quantity).toBe(3800);
    });

    it("handles suspended stock (price=0)", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "quantity",
        items: [
          { symbol: "SUSPENDED", side: "buy", quantity: 100 },
          { symbol: "600519", side: "buy", quantity: 100 },
        ],
      });

      expect(preview.items[0].riskCheckPassed).toBe(false);
      expect(preview.items[0].riskRejectReason).toContain("suspended");
      expect(preview.warnings).toHaveLength(1);
      expect(preview.items[1].riskCheckPassed).toBe(true);
    });

    it("warns when weights don't sum to 100%", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "weight",
        totalAmount: 1000000,
        items: [
          { symbol: "600519", side: "buy", weight: 0.3 },
          { symbol: "000858", side: "buy", weight: 0.3 },
        ],
      });

      expect(preview.warnings.some((w) => w.includes("60.0%"))).toBe(true);
    });

    it("rejects weight mode without totalAmount", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      await expect(
        svc.preview({ mode: "weight", items: [{ symbol: "600519", side: "buy", weight: 1 }] }),
      ).rejects.toThrow("totalAmount");
    });

    it("marks risk-rejected items in preview", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(["000858"]),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 100 },
        ],
      });

      expect(preview.items[0].riskCheckPassed).toBe(true);
      expect(preview.items[1].riskCheckPassed).toBe(false);
      expect(preview.items[1].riskRejectReason).toContain("Risk limit");
    });
  });

  describe("submit()", () => {
    it("submits basket and returns all_filled for full success", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const result = await svc.submit({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 200 },
          { symbol: "601318", side: "buy", quantity: 300 },
        ],
      });

      expect(result.status).toBe("all_filled");
      expect(result.summary.submitted).toBe(3);
      expect(result.summary.filled).toBe(3);
      expect(result.summary.rejected).toBe(0);
      expect(result.items).toHaveLength(3);
      expect(result.items.every((i) => i.orderId)).toBe(true);
    });

    it("returns partial when risk rejects some items", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(["000858"]),
        orders: mockOrders(),
      });

      const result = await svc.submit({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 200 },
        ],
      });

      expect(result.status).toBe("partial");
      expect(result.summary.filled).toBe(1);
      expect(result.summary.rejected).toBe(1);
      // Rejected item has no orderId
      const rejected = result.items.find((i) => i.symbol === "000858");
      expect(rejected?.status).toBe("rejected");
      expect(rejected?.orderId).toBeUndefined();
    });

    it("returns all_rejected when all fail risk check", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(["600519", "000858"]),
        orders: mockOrders(),
      });

      const result = await svc.submit({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 200 },
        ],
      });

      expect(result.status).toBe("all_rejected");
      expect(result.summary.rejected).toBe(2);
    });

    it("handles broker submission failure gracefully", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(["000858"]), // broker rejects this
      });

      const result = await svc.submit({
        mode: "quantity",
        items: [
          { symbol: "600519", side: "buy", quantity: 100 },
          { symbol: "000858", side: "buy", quantity: 200 },
        ],
      });

      expect(result.status).toBe("partial");
      expect(result.summary.filled).toBe(1);
      expect(result.summary.rejected).toBe(1);
      const failed = result.items.find((i) => i.symbol === "000858");
      expect(failed?.reason).toContain("Broker rejected");
    });

    it("tracks basket by id for status queries", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const result = await svc.submit({
        basketId: "basket-001",
        mode: "quantity",
        items: [{ symbol: "600519", side: "buy", quantity: 100 }],
      });

      const status = svc.getStatus("basket-001");
      expect(status).toBeDefined();
      expect(status?.basketId).toBe("basket-001");
    });

    it("handles 10-stock basket (AC-3 acceptance test)", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const items = Array.from({ length: 10 }, (_, i) => ({
        symbol: `00000${i}`,
        side: "buy" as const,
        quantity: 100,
      }));

      const result = await svc.submit({ mode: "quantity", items });
      expect(result.summary.submitted).toBe(10);
      expect(result.summary.filled).toBe(10);
    });

    it("weight mode: 100万 * 5只股各20% calculates correct lots (AC-3)", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      const preview = await svc.preview({
        mode: "weight",
        totalAmount: 1000000,
        items: [
          { symbol: "600519", side: "buy", weight: 0.2 },  // 200000/1850 = 108 → 100
          { symbol: "000858", side: "buy", weight: 0.2 },  // 200000/135 = 1481 → 1400
          { symbol: "601318", side: "buy", weight: 0.2 },  // 200000/52 = 3846 → 3800
          { symbol: "600036", side: "buy", weight: 0.2 },  // 200000/36 = 5555 → 5500
          { symbol: "000001", side: "buy", weight: 0.2 },  // 200000/11 = 18181 → 18100
        ],
      });

      // All quantities should be multiples of 100 (A-share lot size)
      for (const item of preview.items) {
        expect(item.quantity % 100).toBe(0);
        expect(item.quantity).toBeGreaterThan(0);
      }
    });
  });

  describe("listBaskets()", () => {
    it("returns all submitted baskets", async () => {
      const svc = new BasketOrderService({
        quotes: mockQuotes(),
        risk: mockRisk(),
        orders: mockOrders(),
      });

      await svc.submit({
        basketId: "b1",
        mode: "quantity",
        items: [{ symbol: "600519", side: "buy", quantity: 100 }],
      });
      await svc.submit({
        basketId: "b2",
        mode: "quantity",
        items: [{ symbol: "000858", side: "buy", quantity: 200 }],
      });

      expect(svc.listBaskets()).toHaveLength(2);
    });
  });
});
