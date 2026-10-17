/**
 * Unit tests for the live_trading AI tool.
 *
 * Mocks the LiveEngine and its sub-components (OrderManager,
 * LiveStrategyRegistry, BrokerAccount) to test action routing
 * without real broker connections.
 */
import { describe, expect, it, vi } from "vitest";
import type { LiveEngine } from "../engine/live-engine.js";
import type { LiveStrategyRegistry } from "../engine/live-strategy-registry.js";
import type { ConfirmationManager } from "../services/confirmation-manager.js";
import type { OrderManager } from "../services/order-manager.js";
import type { BrokerAccount, LivePosition, QuantTradingConfig } from "../types.js";
import { createLiveTradingTool } from "./live-trading.js";

const fakeApi = {
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
} as never;

function makeConfig(): QuantTradingConfig {
  return {
    dataDir: "/tmp/oc-quant-test",
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    tradingAccount: {
      mode: "paper",
      broker: "paper",
      riskLimits: {
        maxOrderValue: 500_000,
        maxPositionPct: 0.3,
        maxDailyLoss: 50_000,
      },
    },
  } as QuantTradingConfig;
}

function makeAccount(overrides?: Partial<BrokerAccount>): BrokerAccount {
  return {
    accountId: "paper",
    brokerType: "paper",
    cash: 500_000,
    frozenCash: 0,
    totalAssets: 500_000,
    marketValue: 0,
    profit: 5000,
    profitPct: 0.01,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeMockEngine(overrides?: Partial<LiveEngine>): LiveEngine {
  const mockRegistry = {
    list: vi.fn().mockReturnValue([
      {
        id: "ls-1",
        strategyId: "macd",
        symbols: ["600519"],
        enabled: true,
        addedAt: "2026-01-01",
      },
    ]),
  } as unknown as LiveStrategyRegistry;

  const mockConfirmMgr = {
    listPending: vi.fn().mockResolvedValue([]),
  } as unknown as ConfirmationManager;

  const mockOrderMgr = {
    submit: vi.fn().mockResolvedValue({ ok: true, order: { orderId: "o1", status: "submitted" } }),
    cancel: vi.fn().mockResolvedValue(undefined),
    cancelAll: vi.fn().mockResolvedValue(undefined),
    getDailyPnl: vi.fn().mockResolvedValue(0),
    confirmationManager: mockConfirmMgr,
  } as unknown as OrderManager;

  return {
    getStatus: vi.fn().mockReturnValue("running"),
    getAccount: vi.fn().mockReturnValue(makeAccount()),
    getPositions: vi.fn().mockReturnValue([]),
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    resume: vi.fn(),
    addStrategy: vi.fn().mockResolvedValue({
      id: "ls-new",
      strategyId: "rsi",
      symbols: ["000858"],
      enabled: true,
      addedAt: new Date().toISOString(),
    }),
    removeStrategy: vi.fn().mockResolvedValue(true),
    strategyRegistry: mockRegistry,
    orderManager: mockOrderMgr,
    ...overrides,
  } as unknown as LiveEngine;
}

// ── Tests ───────────────────────────────────────────────────────

describe("live_trading tool", () => {
  describe("engine_status", () => {
    it("returns engine status when running", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "engine_status" })) as any;

      expect(result.status).toBe("running");
      expect(result.account).toBeDefined();
      expect(result.account.cash).toContain("500000");
      expect(result.activeStrategies).toBe(1);
    });

    it("returns stopped when engine is null", async () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      const result = (await tool.execute({ action: "engine_status" })) as any;

      expect(result.status).toBe("stopped");
      expect(result.message).toContain("未初始化");
    });
  });

  describe("start_engine", () => {
    it("starts the engine", async () => {
      const engine = makeMockEngine({ getStatus: vi.fn().mockReturnValue("stopped") } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "start_engine" })) as any;

      expect(result.message).toContain("启动");
    });

    it("returns already running message", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "start_engine" })) as any;

      expect(result.message).toContain("已在运行");
    });

    it("returns error when engine is null", async () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      const result = (await tool.execute({ action: "start_engine" })) as any;
      expect(result.error).toContain("未初始化");
    });
  });

  describe("stop_engine", () => {
    it("stops the engine", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "stop_engine" })) as any;

      expect(result.message).toContain("已停止");
      expect(engine.stop).toHaveBeenCalled();
    });
  });

  describe("pause_engine / resume_engine", () => {
    it("pauses the engine", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "pause_engine" })) as any;

      expect(result.message).toContain("暂停");
      expect(engine.pause).toHaveBeenCalled();
    });

    it("resumes the engine", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "resume_engine" })) as any;

      expect(result.message).toContain("恢复");
      expect(engine.resume).toHaveBeenCalled();
    });
  });

  describe("add_strategy", () => {
    it("adds a strategy to the engine", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "add_strategy",
        strategy_id: "rsi",
        symbols: ["000858"],
      })) as any;

      expect(result.message).toContain("已添加");
      expect(result.live_strategy_id).toBe("ls-new");
    });

    it("returns error when strategy_id is missing", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "add_strategy", symbols: ["600519"] })) as any;
      expect(result.error).toContain("strategy_id");
    });

    it("returns error when symbols is empty", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "add_strategy",
        strategy_id: "rsi",
      })) as any;
      expect(result.error).toContain("标的");
    });
  });

  describe("remove_strategy", () => {
    it("removes a strategy by live_strategy_id", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "remove_strategy",
        live_strategy_id: "ls-1",
      })) as any;

      expect(result.message).toContain("已移除");
    });

    it("returns error for non-existent strategy", async () => {
      const engine = makeMockEngine({
        removeStrategy: vi.fn().mockResolvedValue(false),
      } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "remove_strategy",
        live_strategy_id: "ls-nonexistent",
      })) as any;
      expect(result.error).toContain("未找到");
    });
  });

  describe("list_strategies", () => {
    it("lists all strategies with metadata", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "list_strategies" })) as any;

      expect(result.total).toBe(1);
      expect(result.strategies[0].strategyId).toBe("macd");
    });

    it("returns empty when engine is null", async () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      const result = (await tool.execute({ action: "list_strategies" })) as any;
      expect(result.strategies).toHaveLength(0);
    });
  });

  describe("force_order", () => {
    it("submits a manual order through OrderManager", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "force_order",
        symbol: "600519",
        side: "buy",
        order_type: "limit",
        quantity: 100,
        price: 1850,
      })) as any;

      expect(result.message).toContain("下单成功");
      expect(result.orderId).toBe("o1");
      expect(engine.orderManager!.submit).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "600519",
          side: "buy",
          quantity: 100,
          price: 1850,
          strategyId: "manual",
        }),
      );
    });

    it("returns error when required fields missing", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "force_order",
        symbol: "600519",
      })) as any;
      expect(result.error).toContain("symbol");
    });

    it("returns error for limit order without price", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "force_order",
        symbol: "600519",
        side: "buy",
        order_type: "limit",
        quantity: 100,
      })) as any;
      expect(result.error).toContain("price");
    });

    it("returns risk rejection reason", async () => {
      const mockOrderMgr = {
        submit: vi
          .fn()
          .mockResolvedValue({ ok: false, reason: "超过单笔限额", ruleId: "MAX_ORDER_VALUE" }),
        confirmationManager: { listPending: vi.fn().mockResolvedValue([]) },
      } as unknown as OrderManager;

      const engine = makeMockEngine({ orderManager: mockOrderMgr } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "force_order",
        symbol: "600519",
        side: "buy",
        order_type: "market",
        quantity: 10000,
      })) as any;
      expect(result.error).toContain("超过单笔限额");
      expect(result.ruleId).toBe("MAX_ORDER_VALUE");
    });
  });

  describe("cancel_order / cancel_all_orders", () => {
    it("cancels a specific order", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "cancel_order",
        order_id: "order-abc123",
      })) as any;

      expect(result.message).toContain("已撤销");
      expect(engine.orderManager!.cancel).toHaveBeenCalledWith("order-abc123");
    });

    it("cancels all orders", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "cancel_all_orders" })) as any;

      expect(result.message).toContain("已撤销");
      expect(engine.orderManager!.cancelAll).toHaveBeenCalled();
    });
  });

  describe("get_risk_snapshot", () => {
    it("returns account + risk + positions summary", async () => {
      const positions: LivePosition[] = [
        {
          symbol: "600519",
          market: "cn_stock",
          quantity: 100,
          availableQty: 100,
          frozenQty: 0,
          todayBuyQty: 0,
          avgCost: 1800,
          marketValue: 185_000,
          source: "local",
          lastSyncAt: new Date().toISOString(),
        },
      ];
      const engine = makeMockEngine({
        getPositions: vi.fn().mockReturnValue(positions),
      } as any);

      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "get_risk_snapshot" })) as any;

      expect(result.status).toBe("running");
      expect(result.account.cash).toContain("500000");
      expect(result.limits).toBeDefined();
      expect(result.positions).toHaveLength(1);
      expect(result.positions[0].symbol).toBe("600519");
    });

    it("returns error when account not yet synced", async () => {
      const engine = makeMockEngine({ getAccount: vi.fn().mockReturnValue(null) } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "get_risk_snapshot" })) as any;
      expect(result.error).toContain("尚未同步");
    });

    it("includes maxDrawdown when equityHistory is provided", async () => {
      const engine = makeMockEngine();
      const mockEquityHistory = {
        getSummary: vi.fn().mockReturnValue({
          totalDays: 30,
          maxDrawdown: 15000,
          maxDrawdownPct: 3.5,
        }),
      };
      const tool = createLiveTradingTool(
        fakeApi,
        makeConfig(),
        () => engine,
        mockEquityHistory as any,
      );
      const result = (await tool.execute({ action: "get_risk_snapshot" })) as any;

      expect(result.maxDrawdown).toBe("¥15000.00");
      expect(result.maxDrawdownPct).toBe("3.50%");
    });

    it("omits maxDrawdown when equityHistory has no data", async () => {
      const engine = makeMockEngine();
      const mockEquityHistory = {
        getSummary: vi.fn().mockReturnValue({ totalDays: 0 }),
      };
      const tool = createLiveTradingTool(
        fakeApi,
        makeConfig(),
        () => engine,
        mockEquityHistory as any,
      );
      const result = (await tool.execute({ action: "get_risk_snapshot" })) as any;

      expect(result.maxDrawdown).toBeUndefined();
      expect(result.maxDrawdownPct).toBeUndefined();
    });
  });

  describe("list_confirmations", () => {
    it("returns pending confirmations", async () => {
      const mockConfirmMgr = {
        listPending: vi
          .fn()
          .mockResolvedValue([{ confirmId: "a3f9", orderId: "o1", status: "waiting" }]),
      } as unknown as ConfirmationManager;
      const mockOrderMgr = {
        submit: vi.fn(),
        cancel: vi.fn(),
        cancelAll: vi.fn(),
        getDailyPnl: vi.fn().mockResolvedValue(0),
        confirmationManager: mockConfirmMgr,
      } as unknown as OrderManager;

      const engine = makeMockEngine({ orderManager: mockOrderMgr } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "list_confirmations" })) as any;

      expect(result.total).toBe(1);
      expect(result.confirmations[0].confirmId).toBe("a3f9");
    });
  });

  describe("batch_order", () => {
    it("submits multiple orders in batch", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "batch_order",
        orders: [
          { symbol: "600519", side: "buy", quantity: 100, price: 1850, order_type: "limit" },
          { symbol: "000858", side: "buy", quantity: 200, price: 150, order_type: "limit" },
        ],
      })) as any;

      expect(result.success).toBe(2);
      expect(result.failed).toBe(0);
      expect(result.details).toHaveLength(2);
      expect(engine.orderManager!.submit).toHaveBeenCalledTimes(2);
    });

    it("returns error when orders array is empty", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "batch_order", orders: [] })) as any;
      expect(result.error).toContain("orders");
    });

    it("returns error when orders is not provided", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "batch_order" })) as any;
      expect(result.error).toContain("orders");
    });

    it("handles partial failures in batch", async () => {
      let callCount = 0;
      const mockOrderMgr = {
        submit: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount === 1) {
            return Promise.resolve({ ok: true, order: { orderId: "o1", status: "submitted" } });
          }
          return Promise.resolve({ ok: false, reason: "超过单笔限额" });
        }),
        cancel: vi.fn(),
        cancelAll: vi.fn(),
        getDailyPnl: vi.fn().mockResolvedValue(0),
        confirmationManager: { listPending: vi.fn().mockResolvedValue([]) },
      } as unknown as OrderManager;

      const engine = makeMockEngine({ orderManager: mockOrderMgr } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "batch_order",
        orders: [
          { symbol: "600519", side: "buy", quantity: 100, price: 1850, order_type: "limit" },
          { symbol: "000858", side: "buy", quantity: 200, price: 150, order_type: "limit" },
        ],
      })) as any;

      expect(result.success).toBe(1);
      expect(result.failed).toBe(1);
    });

    it("processes sell orders before buy orders", async () => {
      const callOrder: string[] = [];
      const mockOrderMgr = {
        submit: vi.fn().mockImplementation((req: any) => {
          callOrder.push(`${req.side}_${req.symbol}`);
          return Promise.resolve({ ok: true, order: { orderId: "o1", status: "submitted" } });
        }),
        cancel: vi.fn(),
        cancelAll: vi.fn(),
        getDailyPnl: vi.fn().mockResolvedValue(0),
        confirmationManager: { listPending: vi.fn().mockResolvedValue([]) },
      } as unknown as OrderManager;

      const engine = makeMockEngine({ orderManager: mockOrderMgr } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      await tool.execute({
        action: "batch_order",
        orders: [
          { symbol: "600519", side: "buy", quantity: 100, price: 1850, order_type: "limit" },
          { symbol: "000858", side: "sell", quantity: 200, price: 150, order_type: "limit" },
        ],
      });

      expect(callOrder[0]).toBe("sell_000858");
      expect(callOrder[1]).toBe("buy_600519");
    });

    it("preserves request order in results while submitting sells first", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "batch_order",
        orders: [
          { symbol: "600519", side: "buy", quantity: 100, price: 1850 },
          { symbol: "000858", side: "sell", quantity: 100, price: 150 },
        ],
      })) as any;

      expect(result.success).toBe(2);
      expect(result.failed).toBe(0);
      expect(result.details.map((item: any) => item.symbol)).toEqual(["600519", "000858"]);
    });

    it("validates limit orders require price", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "batch_order",
        orders: [
          { symbol: "600519", side: "buy", quantity: 100, order_type: "limit" },
        ],
      })) as any;

      expect(result.failed).toBe(1);
      expect(result.details[0].error).toContain("price");
    });

    it("returns error when engine is null", async () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      const result = (await tool.execute({
        action: "batch_order",
        orders: [{ symbol: "600519", side: "buy", quantity: 100, price: 1850 }],
      })) as any;
      expect(result.error).toContain("未初始化");
    });
  });

  describe("execute_rebalance", () => {
    it("returns error when engine is null", async () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      const result = (await tool.execute({
        action: "execute_rebalance",
        targets: [{ symbol: "600519", weight: 50 }],
      })) as any;
      expect(result.error).toContain("未初始化");
    });

    it("returns error when targets is empty", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "execute_rebalance",
        targets: [],
      })) as any;
      expect(result.error).toContain("targets");
    });

    it("returns error when targets not provided", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "execute_rebalance" })) as any;
      expect(result.error).toContain("targets");
    });

    it("rejects weights that don't sum to ~100%", async () => {
      const mockBroker = {
        queryAccount: vi.fn().mockResolvedValue({ totalAssets: 1_000_000, cash: 500_000 }),
        queryPositions: vi.fn().mockResolvedValue([]),
      };
      const mockOrderMgr = {
        submit: vi.fn(),
        cancel: vi.fn(),
        cancelAll: vi.fn(),
        getDailyPnl: vi.fn().mockResolvedValue(0),
        broker: mockBroker,
        confirmationManager: { listPending: vi.fn().mockResolvedValue([]) },
      } as unknown as OrderManager;

      const engine = makeMockEngine({ orderManager: mockOrderMgr } as any);
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({
        action: "execute_rebalance",
        targets: [
          { symbol: "600519", weight: 30 },
          { symbol: "000858", weight: 20 },
        ],
      })) as any;
      expect(result.error).toContain("权重总和");
    });
  });

  describe("unknown action", () => {
    it("returns error for unrecognized action", async () => {
      const engine = makeMockEngine();
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => engine);
      const result = (await tool.execute({ action: "invalid_action" })) as any;
      expect(result.error).toContain("未知操作");
    });
  });

  describe("QMT execution-plan boundary", () => {
    it.each(["force_order", "batch_order", "close_position"])(
      "rejects %s for live QMT before calling OrderManager",
      async (action) => {
        const engine = makeMockEngine();
        const config = makeConfig();
        config.tradingAccount = {
          ...config.tradingAccount!,
          mode: "live",
          broker: "qmt",
          requireExecutionPlan: true,
        };
        const tool = createLiveTradingTool(fakeApi, config, () => engine);
        const result = (await tool.execute({ action })) as any;

        expect(result.code).toBe("EXECUTION_PLAN_REQUIRED");
        expect(engine.orderManager!.submit).not.toHaveBeenCalled();
      },
    );
  });

  describe("tool metadata", () => {
    it("has correct tool name", () => {
      const tool = createLiveTradingTool(fakeApi, makeConfig(), () => null);
      expect(tool.name).toBe("live_trading");
      expect(tool.parameters.required).toContain("action");
    });
  });
});
