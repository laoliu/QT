import { afterEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingRuntime } from "../runtime.js";
import { createMarketDataTool } from "./market-data.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";

const config = {
  defaultMarket: "cn_stock" as const,
  tradingAccount: { broker: "remote-qmt" as const, qmt: { windowsBridgeUrl: "http://qmt.test:18888" } },
  dataProviders: { localData: { enabled: false } },
};
const api = {} as QuantTradingRuntime;

function quotes(sourceTime?: number) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, options) => {
    const { codes } = JSON.parse(String(options?.body));
    const code = codes[0];
    const isStock = code === "000001.SZ";
    return new Response(JSON.stringify({ ok: true, data: { quotes: {
      [code]: { lastPrice: isStock ? 12 : 3730, lastClose: isStock ? 11 : 3700,
        name: isStock ? "平安银行" : "指数", time: sourceTime, open: 3700, high: 3740, low: 3690, volume: 100, amount: 1000 },
    } } }));
  });
}

afterEach(() => { vi.restoreAllMocks(); });

describe("index quotes through the AI tool and QMT provider", () => {
  it.each([
    ["sh000001", "cn_stock", "000001.SH", "index"],
    ["sz399001", "cn_stock", "399001.SZ", "index"],
    ["sz399006", "cn_stock", "399006.SZ", "index"],
    ["上证指数", "index", "000001.SH", "index"],
    ["000001.SH", "cn_stock", "000001.SH", "index"],
    ["000001", "index", "000001.SH", "index"],
    ["000001", "cn_stock", "000001.SZ", "cn_stock"],
    ["sz000001", "cn_stock", "000001.SZ", "cn_stock"],
  ])("routes %s (%s) to %s", async (symbol, market, expectedCode, expectedMarket) => {
    const fetchMock = quotes();
    const tool = createMarketDataTool(api, config);
    const result = await tool.execute({ action: "quote", symbol, market }) as Record<string, any>;
    expect(result).not.toHaveProperty("error");
    expect(result.symbol.market).toBe(expectedMarket);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).codes).toEqual([expectedCode]);
    expect(result.price).toBe(expectedCode === "000001.SZ" ? "12.00" : "3730.00");
  });

  it("keeps same-code stock and index quotes in separate cache entries", async () => {
    const fetchMock = quotes();
    const provider = createDataProvider(config);
    expect((await provider.getQuote("000001", "cn_stock"))?.symbol.name).toBe("平安银行");
    expect((await provider.getQuote("000001", "index"))?.price).toBe(3730);
    expect((await provider.getQuote("000001", "cn_stock"))?.price).toBe(12);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["上证指数", "沪指", "sh000001"])("resolves %s locally without stock-search requests", async keyword => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const tool = createMarketDataTool(api, config);
    const result = await tool.execute({ action: "search", keyword, market: "cn_stock" }) as Record<string, any>;
    expect(result.results).toContainEqual({ code: "000001.SH", name: "上证指数", market: "index", exchange: "SSE" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not claim the retrieval timestamp is exchange data time", async () => {
    quotes();
    const result = await createMarketDataTool(api, config).execute({ action: "quote", symbol: "sh000001" }) as Record<string, any>;
    expect(result.source).toBe("qmt");
    expect(result.retrievedAt).toMatch(/\+08:00$/);
    expect(result.dataTime).toBeNull();
    expect(result.freshness).toBe("unknown");
    expect(result.timeNote).toContain("不能据此认定为今天盘中数据");
  });

  it("preserves the source timestamp and labels an old snapshot", async () => {
    quotes(Date.parse("2026-10-16T15:00:00+08:00"));
    const result = await createMarketDataTool(api, config).execute({ action: "quote", symbol: "399006.SZ", market: "index" }) as Record<string, any>;
    expect(result.dataTime).toBe("2026-10-16T15:00:00.000+08:00");
    expect(result.dataDate).toBe("2026-10-16");
    expect(result.freshness).toBe("previous_session");
  });

  it("detects explicit indices without reclassifying plain stock 000001", () => {
    expect(detectMarketFromSymbol("000001")).toBe("cn_stock");
    expect(detectMarketFromSymbol("000001.SZ")).toBe("cn_stock");
    expect(detectMarketFromSymbol("000001.SH")).toBe("index");
    expect(detectMarketFromSymbol("sz399006")).toBe("index");
  });
});
