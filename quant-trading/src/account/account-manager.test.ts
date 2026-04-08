/**
 * Tests for AccountManager  multi-account lifecycle management.
 *
 * Covers:
 *  - Account registration and state tracking
 *  - Connect/disconnect lifecycle (idempotent)
 *  - connectAll for batch initialization
 *  - Aggregate view across accounts
 *  - Cannot unregister a connected account
 *  - Account state updates
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AccountStore } from "./account-store.js";
import { AccountManager } from "./account-manager.js";

let tmpDir: string;
let store: AccountStore;
let manager: AccountManager;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-account-mgr-test-"));
  store = new AccountStore({ dataDir: tmpDir });
  manager = new AccountManager(store);
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

const PAPER = {
  accountId: "paper-default",
  label: "模拟账户",
  broker: "paper" as const,
  market: ["cn_stock"],
  mode: "paper" as const,
  brokerConfig: {},
};

const QMT = {
  accountId: "qmt-001",
  label: "QMT实盘",
  broker: "qmt" as const,
  market: ["cn_stock"],
  mode: "live" as const,
  brokerConfig: { miniQmtPath: "C:\\miniQMT" },
};

describe("AccountManager", () => {
  describe("register / unregister", () => {
    it("registers account and tracks state", () => {
      manager.register(PAPER);
      expect(manager.size).toBe(1);
      expect(manager.hasAccount("paper-default")).toBe(true);
      const state = manager.getState("paper-default");
      expect(state?.connected).toBe(false);
      expect(state?.positionCount).toBe(0);
    });

    it("unregisters a disconnected account", () => {
      manager.register(PAPER);
      manager.unregister("paper-default");
      expect(manager.size).toBe(0);
      expect(manager.hasAccount("paper-default")).toBe(false);
    });

    it("rejects unregister of connected account", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      expect(() => manager.unregister("paper-default")).toThrow("Disconnect first");
    });
  });

  describe("connect / disconnect", () => {
    it("connects account", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      expect(manager.getState("paper-default")?.connected).toBe(true);
    });

    it("connect is idempotent", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      await manager.connect("paper-default"); // no-op
      expect(manager.getState("paper-default")?.connected).toBe(true);
    });

    it("disconnects account", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      await manager.disconnect("paper-default");
      expect(manager.getState("paper-default")?.connected).toBe(false);
    });

    it("disconnect is idempotent", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      await manager.disconnect("paper-default");
      await manager.disconnect("paper-default"); // no-op
      expect(manager.getState("paper-default")?.connected).toBe(false);
    });

    it("throws on connect for non-existent account", async () => {
      await expect(manager.connect("nonexistent")).rejects.toThrow("not found");
    });

    it("throws on disconnect for non-existent account", async () => {
      await expect(manager.disconnect("nonexistent")).rejects.toThrow("not found");
    });
  });

  describe("connectAll", () => {
    it("connects all registered accounts", async () => {
      manager.register(PAPER);
      manager.register(QMT);
      await manager.connectAll();
      expect(manager.getState("paper-default")?.connected).toBe(true);
      expect(manager.getState("qmt-001")?.connected).toBe(true);
    });
  });

  describe("listAccounts", () => {
    it("lists all registered accounts", () => {
      manager.register(PAPER);
      manager.register(QMT);
      const accounts = manager.listAccounts();
      expect(accounts).toHaveLength(2);
    });
  });

  describe("aggregate view", () => {
    it("returns zero sums when no account data", () => {
      manager.register(PAPER);
      const agg = manager.getAggregate();
      expect(agg.totalAssets).toBe(0);
      expect(agg.totalCash).toBe(0);
      expect(agg.accounts).toHaveLength(1);
    });

    it("aggregates across multiple accounts with data", async () => {
      manager.register(PAPER);
      manager.register(QMT);
      await manager.connectAll();

      // Simulate account data updates
      manager.updateAccountState("paper-default", {
        account: {
          accountId: "paper-default",
          brokerType: "paper",
          cash: 50000,
          frozenCash: 0,
          totalAssets: 80000,
          marketValue: 30000,
          profit: 5000,
          profitPct: 0.0625,
          lastSyncAt: new Date().toISOString(),
        },
      });

      manager.updateAccountState("qmt-001", {
        account: {
          accountId: "qmt-001",
          brokerType: "qmt",
          cash: 100000,
          frozenCash: 10000,
          totalAssets: 200000,
          marketValue: 100000,
          profit: 20000,
          profitPct: 0.1,
          lastSyncAt: new Date().toISOString(),
        },
      });

      const agg = manager.getAggregate();
      expect(agg.totalAssets).toBe(280000);
      expect(agg.totalCash).toBe(150000);
      expect(agg.totalMarketValue).toBe(130000);
      expect(agg.totalProfit).toBe(25000);
      expect(agg.accounts).toHaveLength(2);
    });
  });

  describe("updateAccountState", () => {
    it("updates partial state", async () => {
      manager.register(PAPER);
      await manager.connect("paper-default");
      manager.updateAccountState("paper-default", { positionCount: 5, pendingOrderCount: 2 });
      const state = manager.getState("paper-default");
      expect(state?.positionCount).toBe(5);
      expect(state?.pendingOrderCount).toBe(2);
      expect(state?.connected).toBe(true); // unchanged
    });

    it("throws for non-existent account", () => {
      expect(() => manager.updateAccountState("nonexistent", {})).toThrow("not found");
    });
  });

  describe("getBroker / getOrderManager / getRiskGate", () => {
    it("throws when account is not connected (no factory)", async () => {
      manager.register(PAPER);
      expect(() => manager.getBroker("paper-default")).toThrow("No broker instance");
      expect(() => manager.getOrderManager("paper-default")).toThrow("No order manager");
      expect(() => manager.getRiskGate("paper-default")).toThrow("No risk gate");
    });

    it("returns instances when connected with factory", async () => {
      const mockBroker = { brokerType: "paper", connected: true, connect: async () => {}, disconnect: async () => {} } as any;
      const mockOrderManager = { submit: async () => {} } as any;
      const mockRiskGate = { check: () => ({ passed: true }) } as any;

      const factoryManager = new AccountManager(store, {
        createBroker: () => mockBroker,
        createOrderManager: () => mockOrderManager,
        createRiskGate: () => mockRiskGate,
      });

      factoryManager.register(PAPER);
      await factoryManager.connect("paper-default");

      expect(factoryManager.getBroker("paper-default")).toBe(mockBroker);
      expect(factoryManager.getOrderManager("paper-default")).toBe(mockOrderManager);
      expect(factoryManager.getRiskGate("paper-default")).toBe(mockRiskGate);
    });
  });
});
