/**
 * Unit tests for options_data AI tool.
 *
 * Mocks the OptionsDataProvider to test action routing and formatting helpers
 * without real market data connections.
 */
import { describe, expect, it, vi } from "vitest";
import type { OptionChain, OptionContract, OptionQuote, QuantTradingConfig } from "../types.js";

// Stub runPythonScript — the tool delegates to the provider which calls this
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

// Stub the provider factory to return our mock
const mockProvider = {
  getOptionChain: vi.fn(),
  getOptionQuote: vi.fn(),
  getOptionContracts: vi.fn(),
  getOptionKlines: vi.fn(),
  getIVSurface: vi.fn(),
  searchOptionUnderlyings: vi.fn(),
};

vi.mock("../data/market-data-provider.js", () => ({
  createOptionsDataProvider: () => mockProvider,
}));

// Import after mocks are set up
const { createOptionsDataTool } = await import("./options-data.js");

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
  } as QuantTradingConfig;
}

function makeQuote(overrides?: Partial<OptionQuote>): OptionQuote {
  return {
    symbol: "10005646",
    name: "50ETF购3月3000",
    price: 0.2345,
    change: 0.012,
    changePct: 5.38,
    open: 0.222,
    high: 0.245,
    low: 0.218,
    prevClose: 0.222,
    volume: 12_345,
    amount: 2_890_000,
    openInterest: 56_789,
    timestamp: Date.now(),
    underlying: "510050",
    strike: 3.0,
    expiry: "2026-03-25",
    optionType: "call",
    impliedVol: 0.2356,
    delta: 0.55,
    gamma: 0.012,
    theta: -0.003,
    vega: 0.045,
    bid1: 0.234,
    ask1: 0.235,
    bidVol1: 100,
    askVol1: 200,
    intrinsicValue: 0.1,
    timeValue: 0.1345,
    ...overrides,
  };
}

function makeChain(): OptionChain {
  return {
    underlying: "510050",
    underlyingPrice: 3.1,
    expiryDates: ["2026-03-25", "2026-04-23"],
    currentExpiry: "2026-03-25",
    strikes: [2.8, 2.9, 3.0, 3.1, 3.2],
    calls: [makeQuote({ strike: 3.0, optionType: "call" })],
    puts: [makeQuote({ strike: 3.0, optionType: "put", name: "50ETF沽3月3000" })],
  };
}

describe("options_data tool", () => {
  const tool = createOptionsDataTool(fakeApi, makeConfig());

  it("has correct metadata", () => {
    expect(tool.name).toBe("options_data");
    expect(tool.description).toBeTruthy();
    expect(tool.parameters.properties.action).toBeDefined();
  });

  it("chain action - formats OptionChain correctly", async () => {
    const chain = makeChain();
    mockProvider.getOptionChain.mockResolvedValueOnce(chain);

    const result: any = await tool.execute({ action: "chain", underlying: "510050" });
    expect(result.underlying).toBe("510050");
    expect(result.underlyingPrice).toBe(3.1);
    expect(result.currentExpiry).toBe("2026-03-25");
    expect(result.totalContracts).toBe(2);
    expect(result.calls).toHaveLength(1);
    expect(result.puts).toHaveLength(1);
  });

  it("quote action - formats OptionQuote correctly", async () => {
    const quote = makeQuote();
    mockProvider.getOptionQuote.mockResolvedValueOnce(quote);

    const result: any = await tool.execute({ action: "quote", symbol: "10005646" });
    expect(result.symbol).toBe("10005646");
    expect(result.price).toBe("0.2345");
    expect(result.bid).toBe("0.2340");
    expect(result.ask).toBe("0.2350");
    expect(result.delta).toBe("0.5500");
    expect(result.impliedVol).toBe("23.56%");
  });

  it("quote action - returns error when no quote found", async () => {
    mockProvider.getOptionQuote.mockResolvedValueOnce(null);

    const result: any = await tool.execute({ action: "quote", symbol: "INVALID" });
    expect(result.error).toBeTruthy();
  });

  it("chain action - returns error when no chain found", async () => {
    mockProvider.getOptionChain.mockResolvedValueOnce(null);

    const result: any = await tool.execute({ action: "chain", underlying: "INVALID" });
    expect(result.error).toBeTruthy();
  });

  it("contracts action - formats contract list", async () => {
    const contracts: OptionContract[] = [
      {
        symbol: "10005646",
        underlying: "510050",
        underlyingName: "50ETF",
        strike: 3.0,
        expiry: "2026-03-25",
        optionType: "call",
        exerciseType: "european",
        contractMultiplier: 10000,
        exchange: "SSE",
      },
    ];
    mockProvider.getOptionContracts.mockResolvedValueOnce(contracts);

    const result: any = await tool.execute({
      action: "contracts",
      underlying: "510050",
    });
    expect(result.contracts).toHaveLength(1);
    expect(result.contracts[0].multiplier).toBe(10000);
    expect(result.contracts[0].exerciseType).toBe("european");
  });

  it("search action - delegates to provider", async () => {
    mockProvider.searchOptionUnderlyings.mockResolvedValueOnce([
      {
        code: "510050",
        name: "50ETF",
        market: "etf_option",
        exchange: "SSE",
        multiplier: 10000,
      },
    ]);

    const result: any = await tool.execute({
      action: "search",
      keyword: "50ETF",
    });
    expect(result.results).toBeDefined();
  });

  it("returns error for unknown action", async () => {
    const result: any = await tool.execute({ action: "nonexistent" });
    expect(result.error).toBeTruthy();
  });

  it("klines action - returns K-line data", async () => {
    const bars = [
      {
        timestamp: new Date("2026-03-01").getTime(),
        open: 0.22,
        high: 0.24,
        low: 0.21,
        close: 0.235,
        volume: 5000,
        amount: 1_175_000,
      },
    ];
    mockProvider.getOptionKlines.mockResolvedValueOnce(bars);

    const result: any = await tool.execute({
      action: "klines",
      symbol: "10005646",
      startDate: "2026-03-01",
      endDate: "2026-03-02",
    });
    expect(result.count).toBe(1);
    expect(result.data[0].close).toBe("0.2350");
    expect(result.data[0].volume).toBe(5000);
  });

  it("klines action - handles Python date string format", async () => {
    const bars = [
      {
        // Python returns `date` string, no `timestamp`
        date: "2026-03-01",
        timestamp: 0, // should be ignored when `date` is present
        open: 0.22,
        high: 0.24,
        low: 0.21,
        close: 0.235,
        volume: 5000,
        amount: 1_175_000,
      },
    ];
    mockProvider.getOptionKlines.mockResolvedValueOnce(bars);

    const result: any = await tool.execute({
      action: "klines",
      symbol: "10005646",
    });
    expect(result.count).toBe(1);
    expect(result.data[0].date).toBe("2026-03-01");
  });

  it("klines action - returns error when no data", async () => {
    mockProvider.getOptionKlines.mockResolvedValueOnce([]);

    const result: any = await tool.execute({
      action: "klines",
      symbol: "10005646",
    });
    expect(result.error).toBeTruthy();
  });

  it("iv_surface action - returns IV surface data", async () => {
    mockProvider.getIVSurface.mockResolvedValueOnce({
      underlying: "510050",
      underlyingPrice: 3.1,
      timestamp: Date.now(),
      data: [
        { expiry: "2026-03-25", strike: 2.8, iv: 0.25, delta: 0.8 },
        { expiry: "2026-03-25", strike: 3.0, iv: 0.22, delta: 0.55 },
        { expiry: "2026-03-25", strike: 3.2, iv: 0.24, delta: 0.3 },
        { expiry: "2026-04-23", strike: 2.8, iv: 0.23, delta: 0.78 },
        { expiry: "2026-04-23", strike: 3.0, iv: 0.21, delta: 0.52 },
        { expiry: "2026-04-23", strike: 3.2, iv: 0.22, delta: 0.28 },
      ],
    });

    const result: any = await tool.execute({
      action: "iv_surface",
      underlying: "510050",
    });
    expect(result.underlying).toBe("510050");
    expect(result.dataCount).toBe(6);
    expect(result.data[0].iv).toBe("25.00%");
  });

  it("greeks action - returns error for missing params", async () => {
    const result: any = await tool.execute({
      action: "greeks",
      // missing strikePrice, optionType, daysToExpiry
    });
    expect(result.error).toContain("strikePrice");
  });

  it("greeks action - sends camelCase params to Python", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mockRunPython = vi.mocked(runPythonScript);
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        delta: 0.55,
        gamma: 0.012,
        theta: -0.003,
        vega: 0.045,
        rho: 0.001,
        impliedVol: 0.25,
        calculatedAt: Date.now(),
      },
    });

    await tool.execute({
      action: "greeks",
      strikePrice: 3.0,
      optionType: "call",
      daysToExpiry: 30,
      spotPrice: 3.1,
      volatility: 0.25,
    });

    // Verify camelCase keys match Python's expected format
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/greeks_engine.py",
      expect.objectContaining({
        action: "greeks",
        spotPrice: 3.1,
        strike: 3.0,
        optionType: "call",
        volatility: 0.25,
      }),
      expect.anything(),
    );
    // Verify no snake_case keys
    const callArgs = mockRunPython.mock.calls[mockRunPython.mock.calls.length - 1][1];
    expect(callArgs.spot_price).toBeUndefined();
    expect(callArgs.strike_price).toBeUndefined();
    expect(callArgs.option_type).toBeUndefined();
  });
});
