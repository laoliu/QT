/**
 * Unit tests for ConfirmationManager — covers confirm/reject/timeout flows.
 *
 * Uses temp dir for file-based confirmation persistence.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrderRequest } from "../broker/adapter.js";
import type { PendingConfirmation, QuantTradingConfig } from "../types.js";
import { ConfirmationManager } from "./confirmation-manager.js";

// ── Fixtures ────────────────────────────────────────────────────

let tmpDir: string;

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: tmpDir,
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    tradingAccount: {
      mode: "paper",
      broker: "paper",
      riskLimits: {
        confirmationTimeoutSecs: 2, // Fast timeout for tests
      },
    },
    ...overrides,
  } as QuantTradingConfig;
}

function makeOrderReq(overrides?: Partial<OrderRequest>): OrderRequest {
  return {
    symbol: "600519",
    market: "cn_stock",
    side: "buy",
    orderType: "limit",
    quantity: 100,
    price: 1850,
    strategyId: "dual-ma-crossover",
    ...overrides,
  };
}

// ── Setup / Teardown ────────────────────────────────────────────

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-quant-cm-test-"));
});

afterEach(async () => {
  vi.restoreAllMocks();
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function loadConfirmations(): Promise<PendingConfirmation[]> {
  try {
    return JSON.parse(await fs.readFile(path.join(tmpDir, "confirmations.json"), "utf-8"));
  } catch {
    return [];
  }
}

// ── Tests ───────────────────────────────────────────────────────

describe("ConfirmationManager", () => {
  describe("request()", () => {
    it("creates a pending confirmation with a short ID", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      // Don't await the promise — we'll confirm it manually
      const promise = mgr.request(makeOrderReq(), "金额超过阈值", "order-123");

      // Give time for the disk write to flush
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      expect(confirms).toHaveLength(1);
      expect(confirms[0].confirmId).toMatch(/^[0-9a-f]{4}$/);
      expect(confirms[0].orderId).toBe("order-123");
      expect(confirms[0].status).toBe("waiting");
      expect(confirms[0].messageText).toContain("600519");
      expect(confirms[0].messageText).toContain("买入");
      expect(confirms[0].messageText).toContain("100 股");

      // Clean up by confirming
      await mgr.confirm(confirms[0].confirmId);
      await promise;
    });
  });

  describe("confirm()", () => {
    it("resolves the request promise with true", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(makeOrderReq(), "需要确认", "order-456");
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      const confirmId = confirms[0].confirmId;

      const msg = await mgr.confirm(confirmId, "user-1");
      expect(msg).toContain("已确认");

      const confirmed = await promise;
      expect(confirmed).toBe(true);

      // Check file updated
      const updated = await loadConfirmations();
      expect(updated[0].status).toBe("confirmed");
      expect(updated[0].respondedBy).toBe("user-1");
    });

    it("returns error message for unknown confirmId", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const msg = await mgr.confirm("xxxx");
      expect(msg).toContain("未找到");
    });
  });

  describe("reject()", () => {
    it("resolves the request promise with false", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(makeOrderReq(), "需要确认", "order-789");
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      const confirmId = confirms[0].confirmId;

      const msg = await mgr.reject(confirmId);
      expect(msg).toContain("已拒绝");

      const rejected = await promise;
      expect(rejected).toBe(false);

      const updated = await loadConfirmations();
      expect(updated[0].status).toBe("rejected");
    });
  });

  describe("timeout", () => {
    it("auto-rejects after configured timeout", async () => {
      const cfg = makeConfig({
        tradingAccount: {
          mode: "paper",
          broker: "paper",
          riskLimits: { confirmationTimeoutSecs: 1 },
        },
      } as Partial<QuantTradingConfig>);
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(makeOrderReq(), "需要确认", "order-timeout");

      // Wait for timeout (1 sec + buffer)
      const result = await promise;
      expect(result).toBe(false);

      // Wait for the async expire() to flush to disk
      await new Promise((r) => setTimeout(r, 100));

      // Verify status persisted as expired
      const confirms = await loadConfirmations();
      const item = confirms.find((c) => c.orderId === "order-timeout");
      expect(item?.status).toBe("expired");
    });
  });

  describe("listPending()", () => {
    it("returns only waiting non-expired confirmations", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      // Create two pending requests sequentially to avoid write race
      const p1 = mgr.request(makeOrderReq({ symbol: "600519" }), "原因A", "o1");
      await new Promise((r) => setTimeout(r, 100));
      const p2 = mgr.request(makeOrderReq({ symbol: "000858" }), "原因B", "o2");
      await new Promise((r) => setTimeout(r, 100));

      const pending = await mgr.listPending();
      expect(pending).toHaveLength(2);

      // Confirm one
      const confirms = await loadConfirmations();
      await mgr.confirm(confirms[0].confirmId);
      await p1;

      const remaining = await mgr.listPending();
      expect(remaining).toHaveLength(1);

      // Clean up
      await mgr.reject(confirms[1].confirmId);
      await p2;
    });
  });

  describe("double confirm/reject", () => {
    it("returns already-resolved message on second confirm", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(makeOrderReq(), "原因", "dup-test");
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      const confirmId = confirms[0].confirmId;

      await mgr.confirm(confirmId);
      await promise;

      // Second confirm attempt
      const msg = await mgr.confirm(confirmId);
      expect(msg).toContain("已确认");
    });
  });

  describe("message formatting", () => {
    it("formats sell order correctly", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(
        makeOrderReq({ side: "sell", quantity: 500, price: 200 }),
        "大额卖单",
        "sell-order",
      );
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      expect(confirms[0].messageText).toContain("卖出");
      expect(confirms[0].messageText).toContain("500 股");
      expect(confirms[0].messageText).toContain("大额卖单");

      await mgr.confirm(confirms[0].confirmId);
      await promise;
    });

    it("formats option buy_open order with 张 unit", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(
        makeOrderReq({
          symbol: "10005646",
          market: "etf_option" as any,
          side: "buy_open" as any,
          quantity: 5,
          price: 0.235,
        }),
        "期权下单确认",
        "opt-order-1",
      );
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      expect(confirms[0].messageText).toContain("买入开仓");
      expect(confirms[0].messageText).toContain("5 张");
      // Should NOT contain "股"
      expect(confirms[0].messageText).not.toContain("5 股");

      await mgr.confirm(confirms[0].confirmId);
      await promise;
    });

    it("formats option sell_close order with 张 unit", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(
        makeOrderReq({
          symbol: "10005646",
          market: "etf_option" as any,
          side: "sell_close" as any,
          quantity: 3,
          price: 0.25,
        }),
        "平仓确认",
        "opt-order-2",
      );
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      expect(confirms[0].messageText).toContain("卖出平仓");
      expect(confirms[0].messageText).toContain("3 张");

      await mgr.confirm(confirms[0].confirmId);
      await promise;
    });

    it("shows market order type when price is 0", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      const promise = mgr.request(
        makeOrderReq({ price: undefined, orderType: "market" }),
        "确认",
        "market-order",
      );
      await new Promise((r) => setTimeout(r, 50));

      const confirms = await loadConfirmations();
      expect(confirms[0].messageText).toContain("市价单");

      await mgr.confirm(confirms[0].confirmId);
      await promise;
    });
  });

  describe("dispose()", () => {
    it("clears pending timers and resolves promises with false", async () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);

      // Start a request (it will hang until confirmed/rejected/expired)
      const promise = mgr.request(makeOrderReq(), "test dispose", "dispose-test");
      await new Promise((r) => setTimeout(r, 50));

      mgr.dispose();

      // Promise should resolve with false (rejected)
      const result = await promise;
      expect(result).toBe(false);
    });

    it("is safe to call dispose() multiple times", () => {
      const cfg = makeConfig();
      const mgr = new ConfirmationManager(cfg, null);
      mgr.dispose();
      mgr.dispose(); // should not throw
    });
  });
});
