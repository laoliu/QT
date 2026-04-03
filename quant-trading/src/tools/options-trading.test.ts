/**
 * Unit tests for options_trading AI tool.
 *
 * Mocks the LiveEngine and broker to test action routing
 * without real broker connections.
 */
import { describe, expect, it, vi } from "vitest";
import type { LiveEngine } from "../engine/live-engine.js";
import type { OrderManager } from "../services/order-manager.js";
import type { BrokerAccount, LivePosition, Order, QuantTradingConfig } from "../types.js";

// Stub Python bridge and data provider
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));
vi.mock("../data/market-data-provider.js", () => ({
  createOptionsDataProvider: () => ({
    getOptionChain: vi.fn(),
    getOptionQuote: vi.fn().mockResolvedValue({
      symbol: "10005646",
      price: 0.2345,
      bid1: 0.234,
      ask1: 0.235,
    }),
    listContracts: vi.fn(),
    getOptionKlines: vi.fn(),
    getIVSurface: vi.fn(),
    searchOptions: vi.fn(),
  }),
  detectMarketFromSymbol: () => "etf_option",
}));

const { createOptionsTradingTool } = await import("./options-trading.js");

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
        maxDeltaExposure: 500,
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
    profit: 0,
    profitPct: 0,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeOptionPosition(overrides?: Partial<LivePosition>): LivePosition {
  return {
    symbol: "10005646",
    market: "etf_option",
    quantity: 10,
    avgCost: 0.22,
    marketValue: 2345,
    currentPrice: 0.2345,
    profit: 145,
    profitPct: 0.066,
    optionType: "call",
    strike: 3.0,
    expiry: "2026-03-25",
    underlying: "510050",
    greeks: {
      delta: 0.55,
      gamma: 0.012,
      theta: -0.003,
      vega: 0.045,
      rho: 0.001,
      impliedVol: 0.235,
      calculatedAt: Date.now(),
    },
    ...overrides,
  } as LivePosition;
}

function makeMockEngine(overrides?: Partial<LiveEngine>): LiveEngine {
  const mockOrderMgr = {
    submit: vi.fn().mockResolvedValue({
      ok: true,
      order: { orderId: "opt-001", status: "submitted", side: "buy_open" },
    }),
    getOrders: vi.fn().mockResolvedValue([]),
  } as unknown as OrderManager;

  return {
    getStatus: vi.fn().mockReturnValue("running"),
    getAccount: vi.fn().mockReturnValue(makeAccount()),
    getPositions: vi.fn().mockReturnValue([makeOptionPosition()]),
    broker: {
      supportsOptions: vi.fn().mockReturnValue(true),
    },
    orderManager: mockOrderMgr,
    ...overrides,
  } as unknown as LiveEngine;
}

describe("options_trading tool", () => {
  it("has correct metadata", () => {
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => null);
    expect(tool.name).toBe("options_trading");
    expect(tool.description).toBeTruthy();
    expect(tool.parameters.properties.action).toBeDefined();
  });

  it("schema includes strikePrice and expiry parameters", () => {
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => null);
    const props = tool.parameters.properties;
    expect(props.strikePrice).toBeDefined();
    expect(props.strikePrice.type).toBe("number");
    expect(props.expiry).toBeDefined();
    expect(props.expiry.type).toBe("string");
  });

  it("positions action - shows option positions", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({ action: "positions" });
    expect(result.count).toBe(1);
    expect(result.positions[0].symbol).toBe("10005646");
    expect(result.positions[0].optionType).toBe("call");
    expect(result.positions[0].strike).toBe(3.0);
  });

  it("positions action - returns error when engine not running", async () => {
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => null);
    const result: any = await tool.execute({ action: "positions" });
    expect(result.message).toBeTruthy();
  });

  it("place_order action - submits order through engine", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "place_order",
      symbol: "10005646",
      side: "buy_open",
      quantity: 1,
      price: 0.235,
    });

    expect(engine.orderManager!.submit).toHaveBeenCalled();
    expect(result.orderId).toBe("opt-001");
  });

  it("place_order action - rejects invalid side", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "place_order",
      symbol: "10005646",
      side: "buy", // not an option side
      quantity: 1,
      price: 0.235,
    });

    expect(result.error).toContain("无效的期权交易方向");
  });

  it("place_order action - rejects missing symbol", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "place_order",
      side: "buy_open",
      quantity: 1,
      price: 0.235,
    });

    expect(result.error).toContain("symbol");
  });

  it("place_order action - rejects quantity <= 0", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "place_order",
      symbol: "10005646",
      side: "buy_open",
      quantity: 0,
      price: 0.235,
    });

    expect(result.error).toContain("quantity");
  });

  it("place_order action - rejects limit order without price", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "place_order",
      symbol: "10005646",
      side: "buy_open",
      quantity: 1,
      orderType: "limit",
      // no price
    });

    expect(result.error).toContain("price");
  });

  it("orders action - lists orders with option sides", async () => {
    const engine = makeMockEngine();
    const mockOrders: Order[] = [
      {
        orderId: "opt-001-uuid",
        symbol: "10005646",
        market: "etf_option",
        side: "buy_open",
        orderType: "limit",
        price: 0.235,
        quantity: 1,
        filledQty: 0,
        status: "submitted",
        brokerType: "paper",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      } as Order,
    ];
    (engine.orderManager!.getOrders as ReturnType<typeof vi.fn>).mockResolvedValueOnce(mockOrders);

    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);
    const result: any = await tool.execute({ action: "orders" });

    expect(result.count).toBe(1);
    expect(result.orders[0].side).toBe("buy_open");
  });

  it("risk_summary action - aggregates portfolio Greeks", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({ action: "risk_summary" });

    expect(result.optionPositions.total).toBe(1);
    expect(result.optionPositions.calls).toBe(1);
    expect(result.portfolioGreeks).toBeDefined();
    expect(result.portfolioGreeks.delta).toBeTruthy();
  });

  it("risk_summary action - uses contract quantity sum not position count", async () => {
    // Two short positions: -5 and -3 contracts = 8 total short contracts
    const engine = makeMockEngine({
      getPositions: vi
        .fn()
        .mockReturnValue([
          makeOptionPosition({ quantity: -5, symbol: "10005646" }),
          makeOptionPosition({ quantity: -3, symbol: "10005647" }),
        ]),
    });
    const cfgWithShortLimit = makeConfig();
    (cfgWithShortLimit.tradingAccount!.riskLimits as any).maxNakedShort = 6;

    const tool = createOptionsTradingTool(fakeApi, cfgWithShortLimit, () => engine);
    const result: any = await tool.execute({ action: "risk_summary" });

    // Should warn because 8 > 6 (by contract sum, not position count 2)
    expect(result.warnings).toBeDefined();
    expect(result.warnings.some((w: string) => w.includes("8"))).toBe(true);
  });

  it("risk_summary action - shows no-position message", async () => {
    const engine = makeMockEngine({
      getPositions: vi
        .fn()
        .mockReturnValue([{ symbol: "600519", market: "cn_stock", quantity: 100 }]),
    });
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({ action: "risk_summary" });
    expect(result.message).toContain("无期权持仓");
  });

  it("returns error for unknown action", async () => {
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => null);
    const result: any = await tool.execute({ action: "nonexistent" });
    expect(result.error).toBeTruthy();
  });

  it("place_spread action - rejects invalid leg side", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const legs = [{ symbol: "10005646", side: "buy", quantity: 1, price: 0.235 }];

    const result: any = await tool.execute({
      action: "place_spread",
      legs: JSON.stringify(legs),
    });

    expect(result.legs[0].error).toContain("无效的交易方向");
    expect(engine.orderManager!.submit).not.toHaveBeenCalled();
  });

  it("close_position — closes long position with sell_close", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "close_position",
      symbol: "10005646",
    });

    expect(engine.orderManager!.submit).toHaveBeenCalledWith(
      expect.objectContaining({ side: "sell_close", symbol: "10005646" }),
    );
    expect(result.side).toBe("sell_close");
  });

  it("close_position — closes short position with buy_close", async () => {
    const engine = makeMockEngine({
      getPositions: vi.fn().mockReturnValue([
        makeOptionPosition({ quantity: -5 }), // short position
      ]),
    });
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "close_position",
      symbol: "10005646",
    });

    expect(engine.orderManager!.submit).toHaveBeenCalledWith(
      expect.objectContaining({ side: "buy_close", quantity: 5 }),
    );
    expect(result.side).toBe("buy_close");
  });

  it("close_position — errors when no position found", async () => {
    const engine = makeMockEngine({
      getPositions: vi.fn().mockReturnValue([]),
    });
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "close_position",
      symbol: "10005646",
    });

    expect(result.error).toContain("未找到");
  });

  it("place_spread action - propagates option metadata from legs", async () => {
    const engine = makeMockEngine();
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const legs = [
      {
        symbol: "10005646",
        side: "buy_open",
        quantity: 1,
        price: 0.235,
        strike: 3.0,
        expiry: "2026-03-25",
        optionType: "call",
        underlying: "510050",
      },
    ];

    await tool.execute({
      action: "place_spread",
      legs: JSON.stringify(legs),
      spreadName: "test-spread",
    });

    expect(engine.orderManager!.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: "10005646",
        strike: 3.0,
        expiry: "2026-03-25",
        optionType: "call",
        underlying: "510050",
      }),
    );
  });

  it("exercise — errors when broker has no exerciseOption", async () => {
    const engine = makeMockEngine({
      broker: { supportsOptions: vi.fn().mockReturnValue(true) } as any,
    });
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "exercise",
      symbol: "10005646",
      quantity: 1,
    });

    expect(result.error).toContain("不支持");
  });

  it("exercise — delegates to broker.exerciseOption", async () => {
    const exerciseFn = vi.fn().mockResolvedValue({ success: true, message: "行权成功" });
    const engine = makeMockEngine({
      broker: {
        supportsOptions: vi.fn().mockReturnValue(true),
        exerciseOption: exerciseFn,
      } as any,
    });
    const tool = createOptionsTradingTool(fakeApi, makeConfig(), () => engine);

    const result: any = await tool.execute({
      action: "exercise",
      symbol: "10005646",
      quantity: 1,
    });

    expect(exerciseFn).toHaveBeenCalledWith("10005646", 1);
    expect(result.success).toBe(true);
  });
});
