/**
 * Unit tests for QMTBroker — the real-money ѸͶ broker adapter.
 *
 * Mocks PersistentPythonProcess to verify order flow, position queries,
 * event handling, and symbol normalisation without connecting to real QMT.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig, OrderStatus } from "../types.js";

// ── Mock PersistentPythonProcess ────────────────────────────────
let eventHandler: ((event: string, data: unknown) => void) | null = null;
const mockCall = vi.fn().mockResolvedValue({});
const mockStart = vi.fn().mockResolvedValue(undefined);
const mockStop = vi.fn();

vi.mock("../bridge/persistent-python.js", () => ({
  PersistentPythonProcess: class MockProc {
    start = mockStart;
    stop = mockStop;
    call = mockCall;
    onEvent = vi.fn((handler: (event: string, data: unknown) => void) => {
      eventHandler = handler;
    });
    constructor(_module: string, _cfg: unknown, _extra?: unknown) {}
  },
}));

vi.mock("./paper-broker.js", () => ({
  isCnMarketOpen: vi.fn(() => true),
}));

import { QMTBroker } from "./qmt-broker.js";
import { OrderManager } from "../services/order-manager.js";

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-qmt-test",
  pythonPath: "python3",
  tradingAccount: {
    mode: "live",
    broker: "qmt",
    qmt: {
      miniQmtPath: "/mock/qmt",
      accountId: "test-account",
      accountType: "STOCK",
    },
    riskLimits: {},
  },
} as QuantTradingConfig;

beforeEach(() => {
  vi.clearAllMocks();
  // Restore mockCall's resolved value after clearAllMocks resets it
  mockCall.mockResolvedValue({});
  mockStart.mockResolvedValue(undefined);
  eventHandler = null;
});

describe("QMTBroker", () => {
  describe("connect/disconnect", () => {
    it("connects via python process", async () => {
      const broker = new QMTBroker(config);
      expect(broker.connected).toBe(false);

      await broker.connect();
      expect(broker.connected).toBe(true);
      expect(mockStart).toHaveBeenCalled();
      expect(mockCall).toHaveBeenCalledWith("connect");
    });

    it("is idempotent on double connect", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.connect();
      expect(mockStart).toHaveBeenCalledTimes(1);
    });

    it("disconnects cleanly", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.disconnect();
      expect(broker.connected).toBe(false);
      expect(mockCall).toHaveBeenCalledWith("disconnect");
      expect(mockStop).toHaveBeenCalled();
    });
  });

  describe("placeOrder", () => {
    it("rejects orders without execution-plan provenance when the live gate is enabled", async () => {
      const gatedConfig: QuantTradingConfig = {
        ...config,
        tradingAccount: { ...config.tradingAccount!, requireExecutionPlan: true },
      };
      const broker = new QMTBroker(gatedConfig);
      await expect(
        broker.placeOrder({
          symbol: "600519",
          market: "cn_stock",
          side: "buy",
          orderType: "limit",
          price: 1800,
          quantity: 100,
        }),
      ).rejects.toThrow("direct broker orders are disabled");
      expect(mockCall).not.toHaveBeenCalledWith("place_order", expect.anything());
    });

    it("rejects forged provenance instead of treating it as a validated plan", async () => {
      const gatedConfig: QuantTradingConfig = {
        ...config,
        tradingAccount: { ...config.tradingAccount!, requireExecutionPlan: true },
      };
      const broker = new QMTBroker(gatedConfig);
      await expect(broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
        strategyId: "aipha-ml-ranking",
        modelId: "model-qmt-1",
        runId: "run-qmt-1",
        signalId: "signal-qmt-1",
        strategyVersion: "1.0.0",
      })).rejects.toThrow("direct broker orders are disabled");
      expect(mockCall).not.toHaveBeenCalledWith("place_order", expect.anything());
    });

    it("places a limit buy order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-001" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
        strategyId: "macd",
      });

      expect(order.brokerOrderId).toBe("QMT-001");
      expect(order.symbol).toBe("600519");
      expect(order.side).toBe("buy");
      expect(order.status).toBe("submitted");
      expect(order.brokerType).toBe("qmt");
      expect(order.quantity).toBe(100);

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          stock_code: "600519.SH",
          order_type: 23, // buy
          price_type: 11, // limit
          price: 1800,
          volume: 100,
        }),
      );
    });

    it("places a market sell order", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-002" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();

      const order = await broker.placeOrder({
        symbol: "000001.SZ",
        market: "cn_stock",
        side: "sell",
        orderType: "market",
        quantity: 200,
      });

      expect(order.side).toBe("sell");
      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({
          order_type: 24, // sell
          price_type: 5, // market
        }),
      );
    });
  });

  describe("cancelOrder", () => {
    it("cancels an order by local ID", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-003" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      await broker.cancelOrder(order.orderId);
      expect(mockCall).toHaveBeenCalledWith(
        "cancel_order",
        expect.objectContaining({
          order_id: "QMT-003",
          stock_code: "600519.SH",
        }),
      );

      // Order should now be cancelled
      const queried = await broker.queryOrder(order.orderId);
      expect(queried?.status).toBe("cancelled");
    });

    it("throws for non-existent order", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();
      await expect(broker.cancelOrder("nonexistent")).rejects.toThrow("not found");
    });
  });

  describe("modifyOrder", () => {
    it("cancels and resubmits with new price", async () => {
      let callCount = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") {
          callCount++;
          return { broker_order_id: `QMT-${callCount}` };
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const order = await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      const modified = await broker.modifyOrder(order.orderId, { price: 1750 });
      expect(modified.price).toBe(1750);
      expect(modified.orderId).not.toBe(order.orderId); // New order
    });
  });

  describe("queryAccount", () => {
    it("maps raw account data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_account") {
          return {
            account_id: "acc-001",
            cash: 500000,
            frozen_cash: 10000,
            total_asset: 800000,
            market_value: 300000,
            profit: 50000,
            profit_ratio: 0.0667,
          };
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const account = await broker.queryAccount();

      expect(account.accountId).toBe("acc-001");
      expect(account.cash).toBe(500000);
      expect(account.frozenCash).toBe(10000);
      expect(account.totalAssets).toBe(800000);
      expect(account.brokerType).toBe("qmt");
    });
  });

  describe("queryPositions", () => {
    it("maps raw position data", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_positions") {
          return [
            {
              stock_code: "600519.SH",
              stock_name: "贵州茅台",
              volume: 200,
              can_use_volume: 100,
              frozen_volume: 100,
              today_buy_volume: 100,
              avg_price: 1750,
              market_value: 360000,
            },
          ];
        }
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      const positions = await broker.queryPositions();

      expect(positions).toHaveLength(1);
      expect(positions[0].symbol).toBe("600519"); // stripped suffix
      expect(positions[0].name).toBe("贵州茅台");
      expect(positions[0].quantity).toBe(200);
      expect(positions[0].availableQty).toBe(100);
      expect(positions[0].avgCost).toBe(1750);
    });
  });

  describe("subscribeQuote", () => {
    it("subscribes and returns unsubscribe function", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeQuote(["600519"], cb);

      expect(mockCall).toHaveBeenCalledWith("subscribe_quote", {
        stock_codes: ["600519.SH"],
      });

      // Unsubscribe
      unsub();
      expect(mockCall).toHaveBeenCalledWith("unsubscribe_quote", {
        stock_codes: ["600519.SH"],
      });
    });
  });

  describe("event handling", () => {
    it("restores execution provenance from broker order remarks when querying after restart", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "query_orders") return [{
          order_id: "QMT-HISTORY-1",
          stock_code: "600519.SH",
          order_type: 23,
          price: 50,
          order_volume: 100,
          traded_volume: 100,
          traded_price: 50,
          order_status: 57,
          strategy_name: "aipha-ml-ranking",
          order_remark: "client-order|run_id=run-history|model_id=model-history|signal_id=signal-history|strategy_version=1.0.0",
          order_time: "2026-01-05 09:31:00",
        }];
        return {};
      });
      const broker = new QMTBroker(config);
      await broker.connect();

      await expect(broker.queryOrders({ symbol: "600519" })).resolves.toMatchObject([{
        runId: "run-history",
        modelId: "model-history",
        signalId: "signal-history",
      }]);
      await broker.disconnect();
    });

    it("dispatches fill events to subscribers", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const fillCb = vi.fn();
      broker.subscribeFills(fillCb);

      // Place an order first so local cache has it
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "QMT-FILL-1" };
        return {};
      });
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      // Simulate fill event from Python
      eventHandler?.("fill", {
        order_id: "QMT-FILL-1",
        stock_code: "600519.SH",
        traded_price: 1800,
        traded_volume: 100,
        traded_amount: 180000,
        commission: 5.4,
        time: "2024-01-15 10:30:00",
      });

      expect(fillCb).toHaveBeenCalledTimes(1);
      const fill = fillCb.mock.calls[0][0];
      expect(fill.price).toBe(1800);
      expect(fill.quantity).toBe(100);
      expect(fill.commission).toBe(5.4);
    });

    it("dispatches quote events to subscribers", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const quoteCb = vi.fn();
      broker.subscribeQuote(["600519"], quoteCb);

      // Simulate quote event
      eventHandler?.("quote", {
        stock_code: "600519.SH",
        last_price: 1850,
        open: 1800,
        high: 1860,
        low: 1790,
        last_close: 1810,
        volume: 50000,
        amount: 92000000,
        bid_price: [1849],
        ask_price: [1851],
        bid_vol: [100],
        ask_vol: [200],
        time: Date.now(),
      });

      expect(quoteCb).toHaveBeenCalledTimes(1);
      const snapshot = quoteCb.mock.calls[0][0];
      expect(snapshot.price).toBe(1850);
      expect(snapshot.symbol.code).toBe("600519");
    });

    it("unsubscribe stops receiving events", async () => {
      const broker = new QMTBroker(config);
      await broker.connect();

      const cb = vi.fn();
      const unsub = broker.subscribeFills(cb);
      unsub();

      eventHandler?.("fill", {
        order_id: "X",
        stock_code: "600519.SH",
        traded_price: 100,
        traded_volume: 10,
        traded_amount: 1000,
        commission: 0.3,
        time: "2024-01-15 10:30:00",
      });

      expect(cb).not.toHaveBeenCalled();
    });

    it("replays QMT order and trade history through OrderManager after restart", async () => {
      const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-qmt-replay-"));
      const cfg = {
        ...config,
        dataDir,
        tradingAccount: {
          ...config.tradingAccount!,
          riskLimits: {
            maxOrderValue: 500_000,
            maxPositionPct: 0.3,
            maxDailyLoss: 50_000,
            maxTotalLoss: 200_000,
            confirmationThreshold: 1_000_000,
            stopLossPct: 0,
            priceLimitPct: 0,
          },
        },
      } as QuantTradingConfig;
      let brokerOrderNo = 0;
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") {
          brokerOrderNo += 1;
          return { broker_order_id: `QMT-REPLAY-${brokerOrderNo}` };
        }
        if (method === "query_account") {
          return {
            account_id: "test-account",
            cash: 500_000,
            frozen_cash: 0,
            total_asset: 500_000,
            market_value: 50_000,
            profit: 0,
            profit_ratio: 0,
          };
        }
        if (method === "query_positions") {
          return [{
            stock_code: "600519.SH",
            stock_name: "贵州茅台",
            volume: 1_000,
            can_use_volume: 1_000,
            frozen_volume: 0,
            today_buy_volume: 0,
            avg_price: 50,
            market_value: 50_000,
          }];
        }
        return {};
      });

      const broker = new QMTBroker(cfg);
      await broker.connect();
      const manager = new OrderManager(cfg, broker, null);
      const metadata = {
        strategyId: "aipha-ml-ranking",
        modelId: "model-qmt-replay-1",
        signalId: "signal-qmt-replay-1",
        runId: "run-qmt-replay-1",
        strategyVersion: "1.0.0",
        dataTimestamp: 1_735_689_600_000,
      };
      const buy = await manager.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        quantity: 100,
        price: 50,
        ...metadata,
      }, { skipMarketHours: true });
      expect(buy.ok).toBe(true);
      const buyOrderId = buy.ok ? buy.order.orderId : "";
      eventHandler?.("fill", {
        order_id: "QMT-REPLAY-1",
        stock_code: "600519.SH",
        traded_price: 50,
        traded_volume: 100,
        traded_amount: 5_000,
        commission: 1,
        time: "2026-01-05 09:31:00",
      });
      await new Promise((resolve) => setTimeout(resolve, 30));

      const sell = await manager.submit({
        symbol: "600519",
        market: "cn_stock",
        side: "sell",
        orderType: "limit",
        quantity: 100,
        price: 55,
        ...metadata,
      }, { skipMarketHours: true });
      expect(sell.ok).toBe(true);
      eventHandler?.("fill", {
        order_id: "QMT-REPLAY-2",
        stock_code: "600519.SH",
        traded_price: 55,
        traded_volume: 100,
        traded_amount: 5_500,
        commission: 1,
        time: "2026-01-06 09:31:00",
      });
      await new Promise((resolve) => setTimeout(resolve, 60));

      const storedOrders = JSON.parse(
        await fs.readFile(path.join(dataDir, "orders-live.json"), "utf-8"),
      ) as Array<Record<string, unknown>>;
      const storedTrades = JSON.parse(
        await fs.readFile(path.join(dataDir, "trades-live.json"), "utf-8"),
      ) as Array<Record<string, unknown>>;
      expect(storedOrders).toHaveLength(2);
      expect(storedOrders.every((order) => order.runId === metadata.runId)).toBe(true);
      expect(storedOrders.every((order) => order.modelId === metadata.modelId)).toBe(true);
      expect(storedTrades).toHaveLength(1);
      expect(storedTrades[0]).toMatchObject({
        modelId: metadata.modelId,
        runId: metadata.runId,
        signalId: metadata.signalId,
        strategyVersion: metadata.strategyVersion,
        dataTimestamp: metadata.dataTimestamp,
      });

      await broker.disconnect();
      const replayBroker = new QMTBroker(cfg);
      await replayBroker.connect();
      const replayManager = new OrderManager(cfg, replayBroker, null);
      await expect(replayManager.getOrders({ symbol: "600519" })).resolves.toHaveLength(2);
      await expect(replayManager.getTrades("600519")).resolves.toMatchObject([
        expect.objectContaining({ modelId: metadata.modelId, runId: metadata.runId, signalId: metadata.signalId }),
      ]);
      await replayBroker.disconnect();
      expect(buyOrderId).toBeTruthy();
    });
  });

  describe("market hours", () => {
    it("delegates to isCnMarketOpen", async () => {
      const broker = new QMTBroker(config);
      const open = await broker.isMarketOpen();
      expect(open).toBe(true);
    });
  });

  describe("symbol normalisation", () => {
    it("adds SH suffix for 6xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q1" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "600519",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 1800,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "600519.SH" }),
      );
    });

    it("adds SZ suffix for 0xxxxx codes", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q2" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "000001",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 15,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "000001.SZ" }),
      );
    });

    it("passes already-suffixed symbols through", async () => {
      mockCall.mockImplementation((method: string) => {
        if (method === "place_order") return { broker_order_id: "Q3" };
        return {};
      });

      const broker = new QMTBroker(config);
      await broker.connect();
      await broker.placeOrder({
        symbol: "000001.SZ",
        market: "cn_stock",
        side: "buy",
        orderType: "limit",
        price: 15,
        quantity: 100,
      });

      expect(mockCall).toHaveBeenCalledWith(
        "place_order",
        expect.objectContaining({ stock_code: "000001.SZ" }),
      );
    });
  });
});
