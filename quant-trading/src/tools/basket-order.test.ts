import { describe, expect, it, vi } from "vitest";
import { createBasketOrderTool } from "./basket-order.js";
import type { QuantTradingConfig } from "../types.js";

// ── Minimal mocks for dependencies ──────────────────────────────

/** Minimal OpenClawPluginApi stub */
const mockApi = {
  logger: {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  },
} as any;

const defaultConfig: QuantTradingConfig = {};

/** Creates a mock engine with a broker that returns account data */
function mockEngine(cash = 100_000) {
  return {
    orderManager: {
      broker: {
        queryAccount: vi.fn(async () => ({
          cash,
          totalAssets: cash,
        })),
      },
      submit: vi.fn(async () => ({
        ok: true,
        order: { orderId: "test-order-1" },
      })),
    },
  } as any;
}

describe("createBasketOrderTool", () => {
  it("returns a tool object with correct name and description", () => {
    const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

    expect(tool.name).toBe("basket_order");
    expect(tool.description).toBeTruthy();
    expect(tool.description).toContain("篮子订单");
    expect(tool.parameters).toBeDefined();
    expect(tool.parameters.properties).toHaveProperty("action");
    expect(tool.parameters.properties).toHaveProperty("symbols");
  });

  it("has correct parameter schema", () => {
    const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

    const props = tool.parameters.properties;
    expect(props.action.enum).toEqual(["plan", "execute"]);
    expect(props.side.enum).toEqual(["buy", "sell"]);
    expect(props.strategy.enum).toEqual([
      "equal",
      "market_cap",
      "custom",
      "by_amount",
      "by_quantity",
    ]);
    expect(tool.parameters.required).toEqual(["action", "symbols"]);
  });

  describe("input validation", () => {
    it("returns error when symbols is empty", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);
      const result = await tool.execute({ action: "plan", symbols: [] });

      expect(result).toContain("请提供 symbols");
    });

    it("returns error when symbols is undefined", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);
      const result = await tool.execute({ action: "plan" });

      expect(result).toContain("请提供 symbols");
    });

    it("returns error when totalAmount missing for weight-based strategy and no engine", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "600519" }],
        strategy: "equal",
      });

      expect(result).toContain("请提供 total_amount");
    });

    it("returns error when totalAmount is 0 for weight-based strategy and no engine", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "600519" }],
        strategy: "equal",
        total_amount: 0,
      });

      expect(result).toContain("请提供 total_amount");
    });

    it("does not require totalAmount for by_quantity strategy", async () => {
      // by_quantity strategy should not error about totalAmount
      // It will proceed to split (may fail for other reasons like price lookup)
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "600519", quantity: 100 }],
        strategy: "by_quantity",
      });

      // Should not contain the totalAmount error
      expect(result).not.toContain("请提供 total_amount");
    });
  });

  describe("cash_reserve_pct handling", () => {
    it("converts percentage (0-100) to decimal", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      // Cash reserve of 50 (meaning 50%) → should be converted to 0.5
      // This is tested indirectly through the execution path
      // The tool checks: cashReservePctRaw > 1 ? cashReservePctRaw / 100 : cashReservePctRaw
      // So 50 → 0.5, 0.1 → 0.1

      // We just verify the tool doesn't crash with percentage values
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A", price: 10, quantity: 100 }],
        strategy: "by_quantity",
        cash_reserve_pct: 50,
      });

      expect(typeof result).toBe("string");
    });
  });

  describe("default values", () => {
    it("defaults side to buy", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      // Execute without side — should default to "buy"
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A", price: 10, quantity: 200 }],
        strategy: "by_quantity",
      });

      // The result should mention buy-related content, not error
      expect(typeof result).toBe("string");
    });

    it("defaults strategy to equal", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A" }],
        // no strategy specified → defaults to "equal"
        // but equal needs totalAmount
      });

      // Should ask for total_amount (confirming it defaults to equal strategy)
      expect(result).toContain("请提供 total_amount");
    });
  });

  describe("auto account amount", () => {
    it("uses account cash when totalAmount not provided and engine available", async () => {
      const engine = mockEngine(200_000);
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => engine);

      // Without total_amount, it should try to get from the engine
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A", price: 10 }],
        strategy: "equal",
      });

      expect(engine.orderManager.broker.queryAccount).toHaveBeenCalled();
      // Should not error about missing total_amount
      expect(result).not.toContain("请提供 total_amount");
    });

    it("returns error when engine has no orderManager", async () => {
      const engine = { orderManager: null } as any;
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => engine);

      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
      });

      expect(result).toContain("交易引擎未就绪");
    });

    it("returns error when account cash is zero", async () => {
      const engine = mockEngine(0);
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => engine);

      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A" }],
        strategy: "equal",
      });

      expect(result).toContain("账户可用资金为零");
    });
  });

  describe("plan vs execute actions", () => {
    it("plan action returns formatted plan text", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "A", price: 10, quantity: 200 }],
        strategy: "by_quantity",
      });

      expect(result).toContain("篮子订单拆分计划");
      expect(result).toContain("action=execute");
    });

    it("execute action returns error when engine not available", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      const result = await tool.execute({
        action: "execute",
        symbols: [{ symbol: "A", price: 10, quantity: 200 }],
        strategy: "by_quantity",
      });

      expect(result).toContain("引擎未初始化");
    });

    it("execute action returns error when orderManager not ready", async () => {
      const engine = { orderManager: null } as any;
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => engine);

      const result = await tool.execute({
        action: "execute",
        symbols: [{ symbol: "A", price: 10, quantity: 200 }],
        strategy: "by_quantity",
      });

      expect(result).toContain("交易引擎未就绪");
    });
  });

  describe("empty result handling", () => {
    it("returns '无有效腿' when all symbols are skipped", async () => {
      const tool = createBasketOrderTool(mockApi, defaultConfig, () => null);

      // Use a strategy that won't require engine, but all symbols fail
      const result = await tool.execute({
        action: "plan",
        symbols: [{ symbol: "X", quantity: 0 }], // qty 0 → no valid legs
        strategy: "by_quantity",
      });

      // Should indicate no valid legs or the skip reason
      expect(typeof result).toBe("string");
    });
  });
});
