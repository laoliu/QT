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
});
