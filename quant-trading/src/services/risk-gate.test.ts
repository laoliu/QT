/**
 * Unit tests for RiskGate — covers all 13 pre-trade risk rules.
 *
 * isMarketOpen is mocked to return true by default so tests are
 * not time-dependent. Individual tests flip it to false when testing
 * the MARKET_HOURS rule.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { OrderRequest } from "../broker/adapter.js";
import type { BrokerAccount, LivePosition, Order } from "../types.js";

// Mock market-hours helper so tests are not time-dependent
vi.mock("../broker/paper-broker.js", () => ({
  isMarketOpen: vi.fn().mockReturnValue(true),
}));

import { isMarketOpen } from "../broker/paper-broker.js";
import { RiskGate } from "./risk-gate.js";

// ── Fixtures ────────────────────────────────────────────────────────────────

function makeAccount(overrides?: Partial<BrokerAccount>): BrokerAccount {
  return {
    accountId: "test-account",
    brokerType: "paper",
    cash: 1_000_000,
    frozenCash: 0,
    totalAssets: 1_000_000,
    marketValue: 0,
    profit: 0,
    profitPct: 0,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makePosition(symbol: string, overrides?: Partial<LivePosition>): LivePosition {
  return {
    symbol,
    market: "cn_stock",
    quantity: 1000,
    availableQty: 1000,
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 100,
    marketValue: 100_000,
    source: "local",
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeBuyOrder(overrides?: Partial<OrderRequest>): OrderRequest {
  return {
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    orderType: "limit",
    quantity: 100,
    price: 1_000, // ¥10万 total
    ...overrides,
  };
}

function makeSellOrder(overrides?: Partial<OrderRequest>): OrderRequest {
  return {
    symbol: "600519",
    market: "cn_stock",
    side: "sell",
    orderType: "limit",
    quantity: 100,
    price: 1_000,
    ...overrides,
  };
}

// ── Tests ───────────────────────────────────────────────────────────────────

describe("RiskGate", () => {
  beforeEach(() => {
    vi.mocked(isMarketOpen).mockReturnValue(true);
  });

  describe("Rule 1 — BLOCKED_SYMBOL", () => {
    it("rejects orders for blacklisted symbols", () => {
      const gate = new RiskGate({ blockedSymbols: ["600519", "000001"] });
      const result = gate.check(makeBuyOrder(), makeAccount(), [], 0);
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("BLOCKED_SYMBOL");
    });

    it("allows orders when blacklist is empty", () => {
      const gate = new RiskGate({ blockedSymbols: [] });
      const result = gate.check(makeBuyOrder({ price: 10 }), makeAccount(), [], 0);
      expect(result.passed).toBe(true);
    });

    it("allows non-blacklisted symbol when list has entries", () => {
      const gate = new RiskGate({ blockedSymbols: ["000001"] });
      const result = gate.check(
        makeBuyOrder({ price: 10, symbol: "600519" }),
        makeAccount(),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 2 — ALLOWED_SYMBOL", () => {
    it("rejects orders for symbols not in whitelist", () => {
      const gate = new RiskGate({ allowedSymbols: ["000001"] });
      const result = gate.check(makeBuyOrder({ symbol: "600519" }), makeAccount(), [], 0);
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("ALLOWED_SYMBOL");
    });

    it("allows orders when whitelist is empty (disabled)", () => {
      const gate = new RiskGate({ allowedSymbols: [] });
      const result = gate.check(makeBuyOrder({ price: 10 }), makeAccount(), [], 0);
      expect(result.passed).toBe(true);
    });

    it("allows whitelisted symbol", () => {
      const gate = new RiskGate({ allowedSymbols: ["600519"] });
      const result = gate.check(makeBuyOrder({ price: 10 }), makeAccount(), [], 0);
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 3 — MARKET_HOURS", () => {
    it("rejects orders when market is closed", () => {
      vi.mocked(isMarketOpen).mockReturnValue(false);
      const gate = new RiskGate();
      const result = gate.check(makeBuyOrder({ price: 10 }), makeAccount(), [], 0);
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MARKET_HOURS");
    });

    it("allows orders when market is open", () => {
      vi.mocked(isMarketOpen).mockReturnValue(true);
      const gate = new RiskGate();
      const result = gate.check(makeBuyOrder({ price: 10 }), makeAccount(), [], 0);
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 4 — INSUFFICIENT_CASH", () => {
    it("rejects buy order when insufficient cash", () => {
      const gate = new RiskGate();
      // Order value: 100 × ¥1000 = ¥100,000; account cash = ¥50,000
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 1_000 }),
        makeAccount({ cash: 50_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_CASH");
    });

    it("allows buy order when sufficient cash", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 100 }), // ¥1,000
        makeAccount({ cash: 1_000_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });

    it("does not apply cash check to sell orders", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeSellOrder({ quantity: 100, price: 1_000 }),
        makeAccount({ cash: 0 }),
        [makePosition("600519", { availableQty: 200 })],
        0,
      );
      // Sell order should not fail on cash
      expect(result.failedRule).not.toBe("INSUFFICIENT_CASH");
    });
  });

  describe("Rule 5 — INSUFFICIENT_POSITION", () => {
    it("rejects sell order when not enough available qty", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeSellOrder({ quantity: 500 }),
        makeAccount(),
        [makePosition("600519", { availableQty: 100 })],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_POSITION");
    });

    it("rejects sell when no position held", () => {
      const gate = new RiskGate();
      const result = gate.check(makeSellOrder({ quantity: 100 }), makeAccount(), [], 0);
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_POSITION");
    });

    it("allows sell when sufficient available qty", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeSellOrder({ quantity: 50, price: 100 }),
        makeAccount(),
        [makePosition("600519", { availableQty: 100 })],
        0,
      );
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 6 — MAX_ORDER_VALUE", () => {
    it("rejects order exceeding single-order limit", () => {
      const gate = new RiskGate({ maxOrderValue: 50_000 });
      // 100 × ¥1000 = ¥100,000 > ¥50,000
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 1_000 }),
        makeAccount({ cash: 200_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_ORDER_VALUE");
    });

    it("allows order at or below single-order limit", () => {
      const gate = new RiskGate({ maxOrderValue: 50_000 });
      const result = gate.check(
        makeBuyOrder({ quantity: 40, price: 1_000 }), // ¥40,000
        makeAccount({ cash: 200_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 7 — MAX_POSITION_PCT", () => {
    it("rejects buy that would exceed concentration limit", () => {
      // Total assets = ¥100,000; limit = 30%; existing pos = ¥0
      // Order: 1000 × ¥50 = ¥50,000 → 50% > 30%
      const gate = new RiskGate({
        maxPositionPct: 0.3,
        maxOrderValue: 1_000_000,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 1_000, price: 50 }),
        makeAccount({ totalAssets: 100_000, cash: 100_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_POSITION_PCT");
    });

    it("allows buy within concentration limit", () => {
      const gate = new RiskGate({
        maxPositionPct: 0.5,
        maxOrderValue: 1_000_000,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 10 }), // ¥1,000 / ¥100,000 = 1%
        makeAccount({ totalAssets: 100_000, cash: 100_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 8 — MAX_DAILY_LOSS", () => {
    it("rejects buy when daily loss limit hit", () => {
      const gate = new RiskGate({ maxDailyLoss: 10_000, confirmationThreshold: 1_000_000 });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount(),
        [],
        10_000, // exactly at limit
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_DAILY_LOSS");
    });

    it("still allows sell even when daily loss limit hit", () => {
      const gate = new RiskGate({ maxDailyLoss: 10_000 });
      const result = gate.check(
        makeSellOrder({ quantity: 10, price: 10 }),
        makeAccount(),
        [makePosition("600519", { availableQty: 100 })],
        11_000, // over limit
      );
      // sell side should not be blocked by daily loss
      expect(result.failedRule).not.toBe("MAX_DAILY_LOSS");
    });
  });

  describe("Rule 9 — MAX_TOTAL_LOSS", () => {
    it("rejects any order when total loss exceeds limit", () => {
      const gate = new RiskGate({ maxTotalLoss: 20_000, confirmationThreshold: 1_000_000 });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount({ profit: -25_000 }), // ¥25k loss > ¥20k limit
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_TOTAL_LOSS");
    });

    it("allows orders when losses are within total limit", () => {
      const gate = new RiskGate({ maxTotalLoss: 20_000, confirmationThreshold: 1_000_000 });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount({ profit: -5_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });
  });

  describe("Rule 10 — Soft confirmation threshold", () => {
    it("returns needsConfirmation=true for large orders", () => {
      const gate = new RiskGate({
        confirmationThreshold: 50_000,
        maxOrderValue: 1_000_000,
        maxPositionPct: 1,
      });
      // 100 × ¥1000 = ¥100,000 > ¥50,000 threshold
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 1_000 }),
        makeAccount({ cash: 2_000_000, totalAssets: 2_000_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
      expect(result.needsConfirmation).toBe(true);
      expect(result.confirmationReason).toBeTruthy();
    });

    it("does not request confirmation for small orders", () => {
      const gate = new RiskGate({ confirmationThreshold: 50_000 });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 100 }), // ¥1,000
        makeAccount(),
        [],
        0,
      );
      expect(result.needsConfirmation).toBeFalsy();
    });
  });

  describe("Rule 10 — STOP_LOSS (per-position)", () => {
    it("rejects buy when position loss exceeds stop-loss threshold", () => {
      const gate = new RiskGate({
        stopLossPct: 0.08,
        confirmationThreshold: 1_000_000,
      });
      // Position avgCost = 100, new buy price = 90 → loss = 10%
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 90, symbol: "600519" }),
        makeAccount({ cash: 100_000 }),
        [makePosition("600519", { avgCost: 100 })],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("STOP_LOSS");
      expect(result.reason).toContain("10.0%");
    });

    it("allows buy when position loss is below threshold", () => {
      const gate = new RiskGate({
        stopLossPct: 0.08,
        confirmationThreshold: 1_000_000,
      });
      // avgCost = 100, price = 95 → loss = 5% < 8%
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 95, symbol: "600519" }),
        makeAccount({ cash: 100_000 }),
        [makePosition("600519", { avgCost: 100 })],
        0,
      );
      expect(result.failedRule).not.toBe("STOP_LOSS");
    });

    it("does not apply stop-loss to sell orders", () => {
      const gate = new RiskGate({ stopLossPct: 0.08 });
      const result = gate.check(
        makeSellOrder({ quantity: 50, price: 80, symbol: "600519" }),
        makeAccount(),
        [makePosition("600519", { avgCost: 100, availableQty: 100 })],
        0,
      );
      expect(result.failedRule).not.toBe("STOP_LOSS");
    });

    it("skips stop-loss when no existing position", () => {
      const gate = new RiskGate({
        stopLossPct: 0.08,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 100, price: 10 }),
        makeAccount({ cash: 100_000 }),
        [], // no positions
        0,
      );
      expect(result.failedRule).not.toBe("STOP_LOSS");
    });
  });

  describe("Rule 11 — MAX_OPEN_ORDERS", () => {
    function makeOrderStub(id: number): Order {
      return {
        orderId: `order-${id}`,
        symbol: `60051${id}`,
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 10,
        status: "submitted",
        createdAt: new Date().toISOString(),
      } as Order;
    }

    it("rejects order when open orders at max", () => {
      const gate = new RiskGate({
        maxOpenOrders: 3,
        confirmationThreshold: 1_000_000,
      });
      const openOrders = [makeOrderStub(1), makeOrderStub(2), makeOrderStub(3)];
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount({ cash: 100_000 }),
        [],
        0,
        undefined,
        openOrders,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_OPEN_ORDERS");
    });

    it("allows order when open orders below max", () => {
      const gate = new RiskGate({
        maxOpenOrders: 5,
        confirmationThreshold: 1_000_000,
      });
      const openOrders = [makeOrderStub(1), makeOrderStub(2)];
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount({ cash: 100_000 }),
        [],
        0,
        undefined,
        openOrders,
      );
      expect(result.failedRule).not.toBe("MAX_OPEN_ORDERS");
    });

    it("allows order when no open orders array provided", () => {
      const gate = new RiskGate({
        maxOpenOrders: 1,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 10 }),
        makeAccount({ cash: 100_000 }),
        [],
        0,
      );
      expect(result.failedRule).not.toBe("MAX_OPEN_ORDERS");
    });
  });

  describe("Rule 12 — PRICE_LIMIT (涨跌停)", () => {
    it("rejects limit order price deviating beyond limit", () => {
      const gate = new RiskGate({
        priceLimitPct: 0.1,
        confirmationThreshold: 1_000_000,
      });
      // currentPrice = 100, order price = 115 → 15% deviation > 10%
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 115, orderType: "limit" }),
        makeAccount({ cash: 100_000 }),
        [makePosition("600519", { currentPrice: 100, avgCost: 95 })],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("PRICE_LIMIT");
      expect(result.reason).toContain("涨跌停");
    });

    it("allows limit order within price limit", () => {
      const gate = new RiskGate({
        priceLimitPct: 0.1,
        confirmationThreshold: 1_000_000,
      });
      // currentPrice = 100, order price = 105 → 5% deviation < 10%
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 105, orderType: "limit" }),
        makeAccount({ cash: 100_000 }),
        [makePosition("600519", { currentPrice: 100, avgCost: 95 })],
        0,
      );
      expect(result.failedRule).not.toBe("PRICE_LIMIT");
    });

    it("skips price-limit check for market orders", () => {
      const gate = new RiskGate({
        priceLimitPct: 0.1,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 200, orderType: "market" }),
        makeAccount({ cash: 100_000 }),
        [makePosition("600519", { currentPrice: 100, avgCost: 95 })],
        0,
      );
      expect(result.failedRule).not.toBe("PRICE_LIMIT");
    });

    it("skips price-limit when no reference price available", () => {
      const gate = new RiskGate({
        priceLimitPct: 0.1,
        confirmationThreshold: 1_000_000,
      });
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 200, orderType: "limit" }),
        makeAccount({ cash: 100_000 }),
        [], // no position → no reference price
        0,
      );
      expect(result.failedRule).not.toBe("PRICE_LIMIT");
    });
  });

  describe("default pass", () => {
    it("passes a normal small order with healthy account", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeBuyOrder({ quantity: 10, price: 50 }), // ¥500
        makeAccount({ cash: 1_000_000, totalAssets: 1_000_000 }),
        [],
        0,
      );
      expect(result.passed).toBe(true);
      expect(result.failedRule).toBeUndefined();
      expect(result.needsConfirmation).toBeFalsy();
    });
  });

  describe("per-strategy risk overrides", () => {
    it("uses overridden maxOrderValue when provided", () => {
      const gate = new RiskGate({ maxOrderValue: 500_000 }); // global limit ¥500k
      // ¥200k order passes global limit
      const passResult = gate.check(
        makeBuyOrder({ quantity: 200, price: 1_000 }),
        makeAccount({ cash: 1_000_000, totalAssets: 1_000_000 }),
        [],
        0,
      );
      expect(passResult.passed).toBe(true);
      // But fails with per-strategy override of ¥100k
      const failResult = gate.check(
        makeBuyOrder({ quantity: 200, price: 1_000 }),
        makeAccount({ cash: 1_000_000, totalAssets: 1_000_000 }),
        [],
        0,
        undefined,
        undefined,
        { maxOrderValue: 100_000 },
      );
      expect(failResult.passed).toBe(false);
      expect(failResult.failedRule).toBe("MAX_ORDER_VALUE");
    });

    it("uses overridden maxPositionPct when provided", () => {
      const gate = new RiskGate({ maxPositionPct: 0.5, maxOrderValue: 1_000_000 });
      // ¥400k buy into ¥1M account = 40% → passes global 50%
      const passResult = gate.check(
        makeBuyOrder({ quantity: 400, price: 1_000 }),
        makeAccount({ cash: 1_000_000, totalAssets: 1_000_000 }),
        [],
        0,
      );
      expect(passResult.passed).toBe(true);
      // But fails with per-strategy override of 30%
      const failResult = gate.check(
        makeBuyOrder({ quantity: 400, price: 1_000 }),
        makeAccount({ cash: 1_000_000, totalAssets: 1_000_000 }),
        [],
        0,
        undefined,
        undefined,
        { maxPositionPct: 0.3 },
      );
      expect(failResult.passed).toBe(false);
      expect(failResult.failedRule).toBe("MAX_POSITION_PCT");
    });
  });

  // ── Option-specific risk rules (Rule 13) ────────────────────────────

  describe("Rule 13 — Option Risk Rules", () => {
    function makeOptionOrder(overrides?: Partial<OrderRequest>): OrderRequest {
      return {
        symbol: "10005646",
        market: "etf_option",
        side: "buy_open",
        orderType: "limit",
        quantity: 1,
        price: 0.235,
        ...overrides,
      };
    }

    function makeOptionPosition(overrides?: Partial<LivePosition>): LivePosition {
      return {
        symbol: "10005646",
        market: "etf_option",
        quantity: -5,
        availableQty: 0,
        frozenQty: 0,
        todayBuyQty: 0,
        avgCost: 0.22,
        marketValue: 1175,
        source: "local",
        lastSyncAt: new Date().toISOString(),
        optionType: "call",
        strike: 3.0,
        expiry: "2026-04-25",
        greeks: {
          delta: -0.55,
          gamma: 0.012,
          theta: 0.003,
          vega: -0.045,
          rho: -0.001,
          impliedVol: 0.235,
          calculatedAt: Date.now(),
        },
        ...overrides,
      } as LivePosition;
    }

    it("MAX_NAKED_SHORT — rejects sell_open when naked short limit exceeded", () => {
      const gate = new RiskGate({ maxNakedShort: 5 });
      const shortPositions = [
        makeOptionPosition({ symbol: "P1" }),
        makeOptionPosition({ symbol: "P2" }),
        makeOptionPosition({ symbol: "P3" }),
        makeOptionPosition({ symbol: "P4" }),
        makeOptionPosition({ symbol: "P5" }),
      ];
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", quantity: 1 }),
        makeAccount(),
        shortPositions,
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_NAKED_SHORT");
    });

    it("MAX_NAKED_SHORT — allows sell_open when within limit", () => {
      const gate = new RiskGate({ maxNakedShort: 10 });
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", quantity: 1 }),
        makeAccount(),
        [makeOptionPosition()], // 1 existing short (qty=-5)
        0,
      );
      expect(result.passed).toBe(true);
    });

    it("MAX_NAKED_SHORT — sums contract quantities, not position count", () => {
      // 2 positions with qty=-5 each => currentShort = 10 contracts
      // maxNakedShort = 12, new order qty = 3 => 10+3 = 13 > 12
      const gate = new RiskGate({ maxNakedShort: 12 });
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", quantity: 3 }),
        makeAccount(),
        [makeOptionPosition({ symbol: "P1" }), makeOptionPosition({ symbol: "P2" })],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_NAKED_SHORT");
    });

    it("MAX_DELTA_EXPOSURE — rejects when delta exceeds limit", () => {
      const gate = new RiskGate({ maxDeltaExposure: 10 });
      // Position: qty=-5, delta=-0.55 => portfolio delta = (-0.55)*(-5) = 2.75
      // But let's make delta huge to trigger
      const bigDeltaPos = makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.9,
          gamma: 0,
          theta: 0,
          vega: 0,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
      });
      const result = gate.check(
        makeOptionOrder({ side: "buy_open" }),
        makeAccount(),
        [bigDeltaPos], // delta = 0.9 * 100 = 90 > 10
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_DELTA_EXPOSURE");
    });

    it("MAX_VEGA_EXPOSURE — rejects when vega exceeds limit", () => {
      const gate = new RiskGate({ maxVegaExposure: 1 });
      const bigVegaPos = makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.5,
          gamma: 0,
          theta: 0,
          vega: 0.5,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
      });
      const result = gate.check(
        makeOptionOrder({ side: "buy_open" }),
        makeAccount(),
        [bigVegaPos], // vega = 0.5 * 100 = 50 > 1
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_VEGA_EXPOSURE");
    });

    it("MAX_GAMMA_EXPOSURE — rejects when gamma exceeds limit", () => {
      const gate = new RiskGate({ maxGammaExposure: 5 });
      const bigGammaPos = makeOptionPosition({
        quantity: 100,
        greeks: {
          delta: 0.5,
          gamma: 0.1,
          theta: 0,
          vega: 0,
          rho: 0,
          impliedVol: 0.3,
          calculatedAt: Date.now(),
        },
      });
      const result = gate.check(
        makeOptionOrder({ side: "buy_open" }),
        makeAccount(),
        [bigGammaPos], // gamma = 0.1 * 100 = 10 > 5
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_GAMMA_EXPOSURE");
    });

    it("buy_open is treated as a buy for cash check", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeOptionOrder({ side: "buy_open", price: 0.235, quantity: 1 }),
        makeAccount({ cash: 0 }), // no cash
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_CASH");
    });

    it("sell_close is treated as a sell for position check", () => {
      const gate = new RiskGate();
      const result = gate.check(
        makeOptionOrder({ side: "sell_close", quantity: 10 }),
        makeAccount(),
        [], // no position
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_POSITION");
    });

    it("NEAR_EXPIRY_WARNING — blocks sell_open on near-expiry options", () => {
      const gate = new RiskGate({ nearExpiryDays: 5, maxNakedShort: 100 });
      // Expiry in 3 days — within the 5-day guard
      const nearExpiry = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", expiry: nearExpiry } as OrderRequest),
        makeAccount(),
        [],
        0,
      );
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("NEAR_EXPIRY_WARNING");
    });

    it("NEAR_EXPIRY_WARNING — allows sell_open on far-expiry options", () => {
      const gate = new RiskGate({ nearExpiryDays: 5, maxNakedShort: 100 });
      // Expiry in 30 days — well outside the guard
      const farExpiry = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", expiry: farExpiry } as OrderRequest),
        makeAccount(),
        [],
        0,
      );
      expect(result.passed).toBe(true);
    });

    it("sell_open is NOT blocked by INSUFFICIENT_POSITION", () => {
      const gate = new RiskGate({ maxNakedShort: 100 });
      const result = gate.check(
        makeOptionOrder({ side: "sell_open", quantity: 10 }),
        makeAccount(),
        [], // no existing position — that's fine for sell_open
        0,
      );
      expect(result.passed).toBe(true);
    });

    it("futures sell_open does NOT trigger option-specific rules (MAX_NAKED_SHORT)", () => {
      const gate = new RiskGate({ maxNakedShort: 0 }); // forbid naked shorts for options
      const futuresOrder: OrderRequest = {
        symbol: "IF2503",
        market: "futures",
        side: "sell_open",
        orderType: "market",
        quantity: 1,
        price: 4000,
      };
      const result = gate.check(
        futuresOrder,
        makeAccount(),
        [],
        0,
        "futures", // explicitly pass futures market
      );
      // Should NOT be blocked by MAX_NAKED_SHORT — that's option-only
      expect(result.failedRule).not.toBe("MAX_NAKED_SHORT");
    });
  });

  // ── Futures-specific risk rules ────────────────────────────────

  describe("Rule 14 — Futures-specific risk rules", () => {
    it("MAX_MARGIN_UTILIZATION — rejects buy_open when margin utilization exceeds limit", () => {
      const gate = new RiskGate({
        maxMarginUtilization: 0.5,
        maxOrderValue: 10_000_000,
        maxPositionPct: 1.0,
      });
      const existingPos = makePosition("RB2505", {
        market: "futures",
        frozenMargin: 450_000, // Already 45% utilized with 1M total assets
      });
      // New order notional = 5000 * 200 = 1,000,000 → estimated margin = 1M * 0.1 = 100,000
      // Total margin: (450,000 + 100,000) / 1M = 0.55 > 0.5 → should block
      const result = gate.check(
        {
          symbol: "IF2503",
          market: "futures",
          side: "buy_open",
          orderType: "market",
          quantity: 200,
          price: 5000,
        },
        makeAccount({ totalAssets: 1_000_000 }),
        [existingPos],
        0,
        "futures",
      );
      // (450_000 + 100_000) / 1M = 0.55 > 0.5
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_MARGIN_UTILIZATION");
    });

    it("MAX_MARGIN_UTILIZATION — allows buy_open within limit", () => {
      const gate = new RiskGate({ maxMarginUtilization: 0.8 });
      const existingPos = makePosition("RB2505", {
        market: "futures",
        frozenMargin: 100_000, // 10% utilized
      });
      const result = gate.check(
        {
          symbol: "IF2503",
          market: "futures",
          side: "buy_open",
          orderType: "market",
          quantity: 1,
          price: 5000,
        },
        makeAccount({ totalAssets: 1_000_000 }),
        [existingPos],
        0,
        "futures",
      );
      // 100_000 + 5_000 = 105_000 / 1_000_000 = 10.5% < 80%
      expect(result.passed).toBe(true);
    });

    it("MAX_MARGIN_UTILIZATION — does not apply to sell_close", () => {
      const gate = new RiskGate({ maxMarginUtilization: 0.01 }); // very tight
      const result = gate.check(
        {
          symbol: "IF2503",
          market: "futures",
          side: "sell_close",
          orderType: "market",
          quantity: 1,
          price: 5000,
        },
        makeAccount(),
        [makePosition("IF2503", { market: "futures", frozenMargin: 500_000 })],
        0,
        "futures",
      );
      expect(result.failedRule).not.toBe("MAX_MARGIN_UTILIZATION");
    });

    it("DELIVERY_PROXIMITY — rejects new opens near delivery month", () => {
      const gate = new RiskGate({ deliveryGuardDays: 5 });
      // Symbol IF2503 → delivery March 2025 (in the past, but code checks daysToDelivery >= 0)
      // Use a symbol whose delivery month is ~3 days from now
      const now = new Date();
      // Construct a symbol that delivers at end of current month
      const yy = String(now.getFullYear()).slice(2);
      const mm = String(now.getMonth() + 1).padStart(2, "0");
      const futureSymbol = `IF${yy}${mm}`; // current month
      const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0);
      const daysToDelivery = (lastDay.getTime() - now.getTime()) / (24 * 60 * 60 * 1000);

      const result = gate.check(
        {
          symbol: futureSymbol,
          market: "futures",
          side: "buy_open",
          orderType: "market",
          quantity: 1,
          price: 4000,
        },
        makeAccount(),
        [],
        0,
        "futures",
      );

      if (daysToDelivery <= 5 && daysToDelivery >= 0) {
        expect(result.passed).toBe(false);
        expect(result.failedRule).toBe("DELIVERY_PROXIMITY");
      } else {
        // If delivery date is passed or far, test still validates logic doesn't crash
        expect(result.failedRule).not.toBe("DELIVERY_PROXIMITY");
      }
    });

    it("DELIVERY_PROXIMITY — allows sell_close even near delivery", () => {
      const gate = new RiskGate({ deliveryGuardDays: 30 }); // very aggressive guard
      const now = new Date();
      const yy = String(now.getFullYear()).slice(2);
      const mm = String(now.getMonth() + 1).padStart(2, "0");
      const futureSymbol = `IF${yy}${mm}`;
      const result = gate.check(
        {
          symbol: futureSymbol,
          market: "futures",
          side: "sell_close",
          orderType: "market",
          quantity: 1,
          price: 4000,
        },
        makeAccount(),
        [makePosition(futureSymbol, { market: "futures" })],
        0,
        "futures",
      );
      // sell_close should NOT be blocked by delivery proximity
      expect(result.failedRule).not.toBe("DELIVERY_PROXIMITY");
    });

    it("MAX_FUTURES_LOTS — rejects when lots exceed per-contract limit", () => {
      const gate = new RiskGate({ maxFuturesLots: 50 });
      const result = gate.check(
        {
          symbol: "RB2505",
          market: "futures",
          side: "buy_open",
          orderType: "market",
          quantity: 20,
          price: 1850,
        },
        makeAccount(),
        [makePosition("RB2505", { market: "futures", quantity: 40 })],
        0,
        "futures",
      );
      // 40 existing + 20 new = 60 > 50 limit
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("MAX_FUTURES_LOTS");
    });

    it("MAX_FUTURES_LOTS — allows when within limit", () => {
      const gate = new RiskGate({ maxFuturesLots: 50 });
      const result = gate.check(
        {
          symbol: "RB2505",
          market: "futures",
          side: "buy_open",
          orderType: "market",
          quantity: 10,
          price: 1850,
        },
        makeAccount(),
        [makePosition("RB2505", { market: "futures", quantity: 30 })],
        0,
        "futures",
      );
      // 30 + 10 = 40 < 50
      expect(result.passed).toBe(true);
    });

    it("MAX_FUTURES_LOTS — does not apply to sell_close", () => {
      const gate = new RiskGate({ maxFuturesLots: 5 });
      const result = gate.check(
        {
          symbol: "RB2505",
          market: "futures",
          side: "sell_close",
          orderType: "market",
          quantity: 10,
          price: 1850,
        },
        makeAccount(),
        [makePosition("RB2505", { market: "futures", quantity: 50 })],
        0,
        "futures",
      );
      expect(result.failedRule).not.toBe("MAX_FUTURES_LOTS");
    });

    it("futures rules do not apply to cn_stock market", () => {
      const gate = new RiskGate({
        maxMarginUtilization: 0.01,
        maxFuturesLots: 1,
        deliveryGuardDays: 365,
      });
      const result = gate.check(makeBuyOrder(), makeAccount(), [], 0, "cn_stock");
      expect(result.failedRule).not.toBe("MAX_MARGIN_UTILIZATION");
      expect(result.failedRule).not.toBe("DELIVERY_PROXIMITY");
      expect(result.failedRule).not.toBe("MAX_FUTURES_LOTS");
    });
  });

  // ── Fix verification: stop-loss for short positions ─────────────

  describe("Rule 10 — STOP_LOSS (short position fix)", () => {
    it("does NOT block cover on losing short (cover reduces risk)", () => {
      const gate = new RiskGate({ stopLossPct: 0.05 });
      // Short position: avgCost=100, current price=110 → 10% loss for shorts
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -10,
        availableQty: 10,
        avgCost: 100,
        currentPrice: 110,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "cover",
        price: 110,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      // Cover should NOT be blocked — it's closing a losing position, not adding exposure
      expect(result.failedRule).not.toBe("STOP_LOSS");
    });

    it("blocks buy_open when same symbol has a losing long position", () => {
      const gate = new RiskGate({ stopLossPct: 0.05 });
      // Long position: avgCost=100, price=88 → 12% loss
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: 10,
        availableQty: 10,
        avgCost: 100,
        currentPrice: 88,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "buy_open",
        price: 88,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("STOP_LOSS");
    });

    it("blocks sell_open when same symbol has a losing short position", () => {
      const gate = new RiskGate({ stopLossPct: 0.05 });
      // Short position: avgCost=100, current price=110 → 10% loss for shorts
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -10,
        availableQty: 10,
        avgCost: 100,
        currentPrice: 110,
      });
      // sell_open adds MORE short exposure on a losing short — should be blocked
      const order = makeSellOrder({
        symbol: "RB2505",
        market: "futures",
        side: "sell_open",
        price: 110,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("STOP_LOSS");
    });

    it("does NOT trigger stop-loss when short position is profitable (price drops)", () => {
      const gate = new RiskGate({ stopLossPct: 0.05 });
      // Short position: avgCost=100, current price=90 → 10% profit for shorts
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -10,
        availableQty: 10,
        avgCost: 100,
        currentPrice: 90,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "cover",
        price: 90,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.failedRule).not.toBe("STOP_LOSS");
    });

    it("long position stop-loss still works correctly", () => {
      const gate = new RiskGate({ stopLossPct: 0.05 });
      // Long position: avgCost=100, price=90 → 10% loss
      const pos = makePosition("600519", {
        quantity: 1000,
        availableQty: 1000,
        avgCost: 100,
        currentPrice: 90,
      });
      const order = makeBuyOrder({ symbol: "600519", price: 90, quantity: 100 });
      const result = gate.check(order, makeAccount(), [pos], 0);
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("STOP_LOSS");
    });
  });

  // ── Fix verification: cash check uses margin for futures ────────

  describe("Rule 4 — INSUFFICIENT_CASH (futures margin fix)", () => {
    it("futures buy_open uses margin-based cash check (10% of notional)", () => {
      const gate = new RiskGate();
      // Cash = 10,000; order notional = 100,000; margin ≈ 10,000 + commission
      // For stock this would fail (needs 100k), for futures it passes (~10k margin)
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "buy_open",
        price: 10_000,
        quantity: 10,
      });
      const account = makeAccount({ cash: 12_000 });
      const result = gate.check(order, account, [], 0, "futures");
      expect(result.failedRule).not.toBe("INSUFFICIENT_CASH");
    });

    it("stock buy uses full notional cash check", () => {
      const gate = new RiskGate();
      const order = makeBuyOrder({
        symbol: "600519",
        price: 10_000,
        quantity: 10,
      });
      const account = makeAccount({ cash: 12_000 });
      const result = gate.check(order, account, [], 0, "cn_stock");
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_CASH");
    });
  });

  // ── Fix verification: cover / buy_close position check ──────────

  describe("Rule 5 — INSUFFICIENT_POSITION (cover/buy_close fix)", () => {
    it("cover order checks short position availability", () => {
      const gate = new RiskGate();
      // Short 5 lots, trying to cover 10 → should fail
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -5,
        availableQty: 5,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "cover",
        price: 100,
        quantity: 10,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_POSITION");
    });

    it("cover order passes when sufficient short position exists", () => {
      const gate = new RiskGate();
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -10,
        availableQty: 10,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "cover",
        price: 100,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.failedRule).not.toBe("INSUFFICIENT_POSITION");
    });

    it("buy_close order checks short position availability", () => {
      const gate = new RiskGate();
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: -3,
        availableQty: 3,
      });
      const order = makeBuyOrder({
        symbol: "RB2505",
        market: "futures",
        side: "buy_close",
        price: 100,
        quantity: 5,
      });
      const result = gate.check(order, makeAccount(), [pos], 0, "futures");
      expect(result.passed).toBe(false);
      expect(result.failedRule).toBe("INSUFFICIENT_POSITION");
    });
  });

  // ── Fix verification: margin utilization uses estimated margin ──

  describe("Rule 14a — MAX_MARGIN_UTILIZATION (margin calc fix)", () => {
    it("uses estimated margin rate (10%) instead of raw notional", () => {
      const gate = new RiskGate({ maxMarginUtilization: 0.5 });
      // totalAssets = 100,000; existing frozen margin = 30,000 (30%)
      // New order: notional = 50,000 → real margin ≈ 5,000 (10%)
      // Old calc: (30,000 + 50,000) / 100,000 = 80% > 50% → block (wrong)
      // New calc: (30,000 + 5,000) / 100,000 = 35% < 50% → pass (correct)
      const pos = makePosition("RB2505", {
        market: "futures",
        quantity: 10,
        frozenMargin: 30_000,
      });
      const order = makeBuyOrder({
        symbol: "IF2505",
        market: "futures",
        side: "buy_open",
        price: 5_000,
        quantity: 10,
      });
      const account = makeAccount({ totalAssets: 100_000 });
      const result = gate.check(order, account, [pos], 0, "futures");
      expect(result.failedRule).not.toBe("MAX_MARGIN_UTILIZATION");
    });
  });
});
