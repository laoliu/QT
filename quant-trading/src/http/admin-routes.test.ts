import type { IncomingMessage, ServerResponse } from "node:http";
import { PassThrough } from "node:stream";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("../services/accumulation-top3.js", () => ({
  buildAccumulationTop3: vi.fn(),
}));

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { buildAccumulationTop3 } from "../services/accumulation-top3.js";
import { registerAdminRoutes } from "./admin-routes.js";

type RegisteredRoute = {
  path: string;
  handler: (req: IncomingMessage, res: ServerResponse) => Promise<void> | void;
};

interface MockRes {
  _status: number;
  _headers: Record<string, string>;
  _body: string;
  writeHead(status: number, headers?: Record<string, string>): MockRes;
  setHeader(name: string, value: string): MockRes;
  end(body?: string): void;
}

function mockRes(): MockRes {
  const res: MockRes = {
    _status: 200,
    _headers: {},
    _body: "",
    writeHead(status: number, headers?: Record<string, string>) {
      res._status = status;
      if (headers) Object.assign(res._headers, headers);
      return res;
    },
    setHeader(name: string, value: string) {
      res._headers[name.toLowerCase()] = value;
      return res;
    },
    end(body?: string) {
      res._body = body ?? "";
    },
  };
  return res;
}

function mockJsonReq(method = "POST", url = "/"): IncomingMessage & PassThrough {
  const req = new PassThrough() as IncomingMessage & PassThrough;
  req.method = method;
  req.url = url;
  req.headers = { "content-type": "application/json" };
  req.socket = { remoteAddress: "127.0.0.1" } as never;
  return req;
}

describe("admin-routes chip-accumulation top3", () => {
  const registeredRoutes: RegisteredRoute[] = [];
  const mockBuildAccumulationTop3 = vi.mocked(buildAccumulationTop3);
  const mockRunPythonScript = vi.mocked(runPythonScript);

  beforeEach(() => {
    registeredRoutes.length = 0;
    mockBuildAccumulationTop3.mockReset();
    mockRunPythonScript.mockReset();
    mockRunPythonScript.mockResolvedValue({ ok: false, error: "not configured" } as never);
    registerAdminRoutes({
      api: {
        registerHttpRoute(route: RegisteredRoute) {
          registeredRoutes.push(route);
        },
      } as never,
      config: { dataDir: "/tmp", pythonPath: "python3" } as never,
      dataWarehouse: {} as never,
      receiverService: null,
      userStrategyStore: {} as never,
      alertStore: {} as never,
      eventBus: {} as never,
      logStore: {} as never,
      priceAlertService: {} as never,
      conditionalOrderService: {} as never,
      favoritesStore: {} as never,
      taskScheduler: {} as never,
      getEngine: () => ({}) as never,
      secure: (handler) => handler,
      securePublic: (handler) => handler,
    });
  });

  it.each(["core", "quote", "history", "profile", "news"])("fetches stock detail section %s independently", async (section) => {
    mockRunPythonScript.mockClear();
    mockRunPythonScript.mockResolvedValue({ ok: true, data: { symbol: "000001", quote: { price: 12 } } });
    const route = registeredRoutes.find((item) => item.path === "/quant/admin/api/stock-detail");
    const req = mockJsonReq("GET", "/quant/admin/api/stock-detail?symbol=000001&section=" + section);
    const res = mockRes();
    await route!.handler(req, res as never);
    expect(res._status).toBe(200);
    expect(mockRunPythonScript).toHaveBeenCalledTimes(1);
    expect(mockRunPythonScript).toHaveBeenCalledWith(
      "scripts/stock_detail.py",
      expect.objectContaining({ action: ["core", "quote"].includes(section) ? "detail" : section, section, market: "cn_stock" }),
      expect.anything(),
      expect.objectContaining({ maxRetries: 0, timeoutMs: section === "core" ? 16_000 : 22_000 }),
    );
  });

  it.each(["profile", "news", "history"])("shares concurrent %s requests and caches until expiry", async (section) => {
    mockRunPythonScript.mockClear();
    const route = registeredRoutes.find((item) => item.path === "/quant/admin/api/stock-detail")!;
    const request = (market = "cn_stock", symbol = "000001") => route.handler(
      mockJsonReq("GET", `/quant/admin/api/stock-detail?symbol=${symbol}&section=${section}&market=${market}`), mockRes() as never);
    let finish!: (result: { ok: boolean; data: object }) => void;
    mockRunPythonScript.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const first = request();
    const second = request();
    expect(mockRunPythonScript).toHaveBeenCalledTimes(1);
    finish({ ok: true, data: { companyInfo: { name: "Example" }, news: [{ title: "News" }], klines: [{ close: 12 }] } });
    await Promise.all([first, second]);
    await request();
    expect(mockRunPythonScript).toHaveBeenCalledTimes(1);
    mockRunPythonScript.mockResolvedValue({ ok: true, data: {} });
    await request("index");
    await request("cn_stock", "600519");
    expect(mockRunPythonScript).toHaveBeenCalledTimes(3);
    const ttl = section === "profile" ? 30 * 60_000 : section === "news" ? 5 * 60_000 : 60_000;
    const now = Date.now();
    const time = vi.spyOn(Date, "now").mockReturnValue(now + ttl + 1);
    try {
      await request();
      expect(mockRunPythonScript).toHaveBeenCalledTimes(4);
    } finally {
      time.mockRestore();
    }
  });

  it("does not cache failed supplements or live quotes", async () => {
    mockRunPythonScript.mockClear();
    const route = registeredRoutes.find((item) => item.path === "/quant/admin/api/stock-detail")!;
    for (const section of ["news", "quote", "quote"]) {
      await route.handler(mockJsonReq("GET", `/quant/admin/api/stock-detail?symbol=000001&section=${section}`), mockRes() as never);
    }
    expect(mockRunPythonScript).toHaveBeenCalledTimes(3);
    await route.handler(mockJsonReq("GET", "/quant/admin/api/stock-detail?symbol=000001&section=news"), mockRes() as never);
    expect(mockRunPythonScript).toHaveBeenCalledTimes(4);
  });

  it("rejects unknown detail sections before starting Python", async () => {
    mockRunPythonScript.mockClear();
    const route = registeredRoutes.find((item) => item.path === "/quant/admin/api/stock-detail");
    const res = mockRes();
    await route!.handler(mockJsonReq("GET", "/quant/admin/api/stock-detail?symbol=000001&section=invalid"), res as never);
    expect(res._status).toBe(400);
    expect(mockRunPythonScript).not.toHaveBeenCalled();
  });

  it("forwards top3 request parameters into buildAccumulationTop3", async () => {
    mockBuildAccumulationTop3.mockResolvedValue({
      action: "top3",
      lookback: 30,
      min_confidence: 45,
      evaluate_count: 6,
      thresholds: {
        pre_accum_days: 12,
        pre_accum_count_min: 3,
        pre_accum_volume_ratio: 1.8,
        rush_change_pct: 6.5,
        rush_volume_ratio: 2.2,
        rush_turnover_pct: 4,
        pre_surge_chip_min: 9,
        pre_surge_volume_price_min: 7,
        pre_surge_turnover_min: 7,
      },
      top3: [],
    });

    const route = registeredRoutes.find(
      (item) => item.path === "/quant/admin/api/chip-accumulation/top3",
    );
    expect(route).toBeTruthy();

    const req = mockJsonReq();
    const body = {
      symbols: "600519,000858",
      lookback: 30,
      min_confidence: 45,
      evaluate_count: 6,
      as_of_date: "2026-03-20",
      pre_accum_days: 12,
      pre_accum_count_min: 3,
      pre_accum_volume_ratio: 1.8,
      rush_change_pct: 6.5,
      rush_volume_ratio: 2.2,
      rush_turnover_pct: 4,
      pre_surge_chip_min: 9,
      pre_surge_volume_price_min: 7,
      pre_surge_turnover_min: 7,
    };
    const res = mockRes();

    const pending = route?.handler(req, res as never);
    req.end(JSON.stringify(body));
    await pending;

    expect(mockBuildAccumulationTop3).toHaveBeenCalledWith(
      expect.objectContaining({
        symbols: ["600519", "000858"],
        lookback: 30,
        min_confidence: 45,
        evaluate_count: 6,
        as_of_date: "2026-03-20",
        thresholds: {
          pre_accum_days: 12,
          pre_accum_count_min: 3,
          pre_accum_volume_ratio: 1.8,
          rush_change_pct: 6.5,
          rush_volume_ratio: 2.2,
          rush_turnover_pct: 4,
          pre_surge_chip_min: 9,
          pre_surge_volume_price_min: 7,
          pre_surge_turnover_min: 7,
        },
      }),
    );
    expect(res._status).toBe(200);
    expect(res._body).toContain('"action": "top3"');
  });

  it("rejects non-POST requests for top3 route", async () => {
    const route = registeredRoutes.find(
      (item) => item.path === "/quant/admin/api/chip-accumulation/top3",
    );
    expect(route).toBeTruthy();

    const req = mockJsonReq("GET");
    const res = mockRes();

    await route?.handler(req, res as never);

    expect(mockBuildAccumulationTop3).not.toHaveBeenCalled();
    expect(res._status).toBe(405);
    expect(res._body).toContain("仅支持 POST");
  });

  it("forwards ambush_scan params to chip accumulation python engine", async () => {
    mockRunPythonScript.mockResolvedValue({
      ok: true,
      data: { action: "ambush_scan", count: 0, stocks: [] },
    } as never);

    const route = registeredRoutes.find(
      (item) => item.path === "/quant/admin/api/chip-accumulation",
    );
    expect(route).toBeTruthy();

    const req = mockJsonReq();
    const body = {
      action: "ambush_scan",
      symbols: "600519,000858",
      lookback: "35",
      min_confidence: "30",
      as_of_date: "2026-03-20",
      ambush_volume_ratio: "1.6",
      ambush_pre_days: "18",
      ambush_pre_hits: "2",
      ambush_change_max: "6",
      ambush_change_min: "-3",
      ambush_turnover_min: "0.5",
      ambush_turnover_max: "15",
      ambush_ma_period: "25",
      ambush_ma_deviation_max: "10",
      ambush_shrink_ratio: "0.9",
      ambush_stop_volatility: "1.2",
      ambush_near_low_max: "7",
      ambush_box_days: "12",
      ambush_box_amplitude: "20",
      ambush_ma_convergence: "4",
    };
    const res = mockRes();

    const pending = route?.handler(req, res as never);
    req.end(JSON.stringify(body));
    await pending;

    expect(mockRunPythonScript).toHaveBeenCalledWith(
      "engines/chip_accumulation.py",
      expect.objectContaining({
        action: "ambush_scan",
        symbols: "600519,000858",
        lookback: 35,
        min_confidence: 30,
        as_of_date: "2026-03-20",
        ambush_volume_ratio: 1.6,
        ambush_pre_days: 18,
        ambush_pre_hits: 2,
        ambush_change_max: 6,
        ambush_change_min: -3,
        ambush_turnover_min: 0.5,
        ambush_turnover_max: 15,
        ambush_ma_period: 25,
        ambush_ma_deviation_max: 10,
        ambush_shrink_ratio: 0.9,
        ambush_stop_volatility: 1.2,
        ambush_near_low_max: 7,
        ambush_box_days: 12,
        ambush_box_amplitude: 20,
        ambush_ma_convergence: 4,
      }),
      expect.anything(),
    );
    expect(res._status).toBe(200);
    expect(res._body).toContain('"action": "ambush_scan"');
  });

  it("falls back safely when ambush params are invalid strings", async () => {
    mockRunPythonScript.mockResolvedValue({
      ok: true,
      data: { action: "ambush_scan", count: 0, stocks: [] },
    } as never);

    const route = registeredRoutes.find(
      (item) => item.path === "/quant/admin/api/chip-accumulation",
    );
    expect(route).toBeTruthy();

    const req = mockJsonReq();
    const body = {
      action: "ambush_scan",
      symbols: "600519",
      lookback: "abc",
      min_confidence: "oops",
      ambush_volume_ratio: "bad",
      ambush_pre_days: "NaN",
      ambush_pre_hits: "",
      ambush_change_max: "--",
      ambush_change_min: "xyz",
      ambush_turnover_min: "n/a",
      ambush_turnover_max: "none",
      ambush_ma_period: "#",
      ambush_ma_deviation_max: "*",
      ambush_shrink_ratio: "x",
      ambush_stop_volatility: "?",
      ambush_near_low_max: "null",
      ambush_box_days: "undefined",
      ambush_box_amplitude: "[1]",
      ambush_ma_convergence: "{}",
    };
    const res = mockRes();

    const pending = route?.handler(req, res as never);
    req.end(JSON.stringify(body));
    await pending;

    expect(mockRunPythonScript).toHaveBeenCalledWith(
      "engines/chip_accumulation.py",
      expect.objectContaining({
        action: "ambush_scan",
        symbols: "600519",
        lookback: 30,
        min_confidence: 40,
        ambush_volume_ratio: undefined,
        ambush_pre_days: undefined,
        ambush_pre_hits: undefined,
        ambush_change_max: undefined,
        ambush_change_min: undefined,
        ambush_turnover_min: undefined,
        ambush_turnover_max: undefined,
        ambush_ma_period: undefined,
        ambush_ma_deviation_max: undefined,
        ambush_shrink_ratio: undefined,
        ambush_stop_volatility: undefined,
        ambush_near_low_max: undefined,
        ambush_box_days: undefined,
        ambush_box_amplitude: undefined,
        ambush_ma_convergence: undefined,
      }),
      expect.anything(),
    );
    expect(res._status).toBe(200);
  });
});
