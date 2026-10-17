import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { PaperBroker } from "../broker/paper-broker.js";
import { OrderManager } from "./order-manager.js";
import type { QuantTradingConfig } from "../types.js";

const brokers: PaperBroker[] = [];

function makeConfig(dataDir: string): QuantTradingConfig {
  return {
    dataDir,
    dataProvider: "demo",
    broker: "paper",
    initialCash: 100_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    tradingAccount: {
      mode: "paper",
      broker: "paper",
      riskLimits: {
        maxOrderValue: 50_000,
        maxPositionPct: 0.9,
        maxDailyLoss: 50_000,
        maxTotalLoss: 200_000,
        confirmationThreshold: 1_000_000,
        stopLossPct: 0,
        priceLimitPct: 0,
      },
    },
  } as QuantTradingConfig;
}

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("timed out waiting for PaperBroker fill");
}

afterEach(async () => {
  await Promise.all(brokers.splice(0).map((broker) => broker.disconnect()));
});

describe("OrderManager + PaperBroker historical replay", () => {
  it("persists paper orders and trades, then replays them after restart", async () => {
    const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-paper-replay-"));
    const cfg = makeConfig(dataDir);
    const broker = new PaperBroker(cfg);
    brokers.push(broker);
    await broker.connect();
    const manager = new OrderManager(cfg, broker, null);

    const metadata = {
      strategyId: "paper-replay-strategy",
      signalId: "signal-paper-replay-1",
      runId: "run-paper-replay-1",
      strategyVersion: "1.0.0",
      dataTimestamp: 1_767_340_200_000,
    };
    const buy = await manager.submit({
      symbol: "AAPL",
      market: "us_stock",
      side: "buy",
      orderType: "market",
      quantity: 100,
      price: 10,
      ...metadata,
    }, { skipMarketHours: true });
    expect(buy.ok).toBe(true);
    if (!buy.ok) return;
    await waitFor(async () => (await broker.queryOrder(buy.order.orderId))?.status === "filled");

    const sell = await manager.submit({
      symbol: "AAPL",
      market: "us_stock",
      side: "sell",
      orderType: "market",
      quantity: 100,
      price: 11,
      ...metadata,
    }, { skipMarketHours: true });
    expect(sell.ok).toBe(true);
    if (!sell.ok) return;
    await waitFor(async () => (await broker.queryOrder(sell.order.orderId))?.status === "filled");
    await waitFor(async () => (await manager.getTrades("AAPL")).length === 1);

    const storedOrders = JSON.parse(
      await fs.readFile(path.join(dataDir, "orders-paper.json"), "utf-8"),
    ) as Array<Record<string, unknown>>;
    const storedTrades = JSON.parse(
      await fs.readFile(path.join(dataDir, "trades-paper.json"), "utf-8"),
    ) as Array<Record<string, unknown>>;
    expect(storedOrders).toHaveLength(2);
    expect(storedOrders.every((order) => order.runId === metadata.runId)).toBe(true);
    expect(storedTrades).toHaveLength(1);
    expect(storedTrades[0]).toMatchObject({
      runId: metadata.runId,
      signalId: metadata.signalId,
      strategyVersion: metadata.strategyVersion,
      dataTimestamp: metadata.dataTimestamp,
    });

    await broker.disconnect();
    const replayBroker = new PaperBroker(cfg);
    brokers.push(replayBroker);
    await replayBroker.connect();
    const replayManager = new OrderManager(cfg, replayBroker, null);
    await expect(replayManager.getOrders({ symbol: "AAPL" })).resolves.toHaveLength(2);
    await expect(replayManager.getTrades("AAPL")).resolves.toMatchObject([
      expect.objectContaining({ runId: metadata.runId, signalId: metadata.signalId }),
    ]);
  });
});
