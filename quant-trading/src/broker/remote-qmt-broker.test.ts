import { afterEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { RemoteQMTBroker } from "./remote-qmt-broker.js";

const config: QuantTradingConfig = {
  dataDir: "/tmp/qt-remote-qmt-test",
  tradingAccount: {
    mode: "live",
    broker: "remote-qmt",
    requireExecutionPlan: true,
    qmt: { windowsBridgeUrl: "http://127.0.0.1:18888" },
  },
} as QuantTradingConfig;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RemoteQMTBroker provenance boundary", () => {
  it("rejects a live order without complete execution-plan provenance", async () => {
    const broker = new RemoteQMTBroker(config);
    await expect(broker.placeOrder({
      symbol: "600519",
      market: "cn_stock",
      side: "buy",
      orderType: "limit",
      price: 1800,
      quantity: 100,
    })).rejects.toThrow("direct broker orders are disabled");
  });

  it("rejects forged provenance instead of forwarding a raw order", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ ok: true, data: { broker_order_id: "REMOTE-1" } }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const broker = new RemoteQMTBroker(config);
    await expect(broker.placeOrder({
      symbol: "600519",
      market: "cn_stock",
      side: "buy",
      orderType: "limit",
      price: 1800,
      quantity: 100,
      strategyId: "aipha-ml-ranking",
      strategyVersion: "1.0.0",
      runId: "run-remote-1",
      modelId: "model-remote-1",
      signalId: "signal-remote-1",
    })).rejects.toThrow("direct broker orders are disabled");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("restores provenance from a QMT order remark", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        data: [{
          order_id: "REMOTE-2",
          stock_code: "600519.SH",
          order_type: 23,
          order_status: 57,
          order_volume: 100,
          traded_volume: 100,
          traded_price: 1800,
          strategy_name: "aipha-ml-ranking",
          order_remark: "client-order|run_id=run-remote-2|model_id=model-remote-2|signal_id=signal-remote-2|strategy_version=1.0.0",
        }],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const broker = new RemoteQMTBroker(config);
    const [order] = await broker.queryOrders();
    expect(order).toMatchObject({
      brokerOrderId: "REMOTE-2",
      modelId: "model-remote-2",
      runId: "run-remote-2",
      signalId: "signal-remote-2",
      strategyVersion: "1.0.0",
    });
  });

  it("keeps local identity and cached provenance in list and single-order queries", async () => {
    const raw = {
      order_id: "REMOTE-LIST-1", stock_code: "600519.SH", order_type: 23,
      price: 50, order_volume: 100, traded_volume: 50, traded_price: 50, order_status: 56,
    };
    const fetchMock = vi.fn(async (_url: unknown, init: RequestInit) => {
      const { method } = JSON.parse(String(init.body));
      const data = method === "place_order" ? { broker_order_id: raw.order_id }
        : method === "query_orders" ? [raw] : raw;
      return { ok: true, json: async () => ({ ok: true, data }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    // Only the mocked transport permits legacy raw orders; production live gates stay enabled.
    const broker = new RemoteQMTBroker({ ...config,
      tradingAccount: { ...config.tradingAccount!, requireExecutionPlan: false } });
    const metadata = {
      clientOrderId: "client-remote-list", strategyId: "aipha-ml-ranking", modelId: "model-list",
      runId: "run-list", signalId: "signal-list", strategyVersion: "1.0.0",
      dataTimestamp: "2026-01-05T09:30:00+08:00",
    };
    const placed = await broker.placeOrder({
      symbol: "600519", market: "cn_stock", side: "buy", orderType: "limit",
      price: 50, quantity: 100, ...metadata,
    });
    const expected = { ...metadata, orderId: placed.orderId, createdAt: placed.createdAt,
      brokerOrderId: raw.order_id, filledQty: 50, status: "partial" };
    await expect(broker.queryOrders()).resolves.toMatchObject([expected]);
    await expect(broker.queryOrder(placed.orderId)).resolves.toMatchObject(expected);
  });
});
