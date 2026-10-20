import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runPythonScript } from "../bridge/python-bridge.js";
import { MarketContextService } from "./market-context.js";

vi.mock("../bridge/python-bridge.js", () => ({ runPythonScript: vi.fn() }));

const runPython = vi.mocked(runPythonScript);
let service: MarketContextService;

function response(action: string) {
  const payloads: Record<string, unknown> = {
    core_snapshot: { sentiment: { score: 65, tone: "偏多" }, lianban_pool: {} },
    macro_snapshot: { macro_regime: "BULL", volatility_regime: "NORMAL" },
    st_snapshot: { st_statuses: { "600000": { stage: "MAIN-5", risk_level: "extreme" } } },
    catalyst_snapshot: { catalysts: { "600000": { event_type: "us_mover", impact: "high", description: "QCOM +6%" } } },
    auction_snapshot: { auction_signals: { "600000": { score: 80, signal_type: "strong", gap_up_pct: 3.2 } } },
  };
  return { ok: true, data: payloads[action] };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-19T02:00:00Z"));
  runPython.mockReset();
  runPython.mockImplementation(async (_script, args) => response(String(args.action)) as never);
  service = new MarketContextService({} as never);
});

afterEach(() => {
  service.stop();
  vi.useRealTimers();
});

describe("market context refresh isolation", () => {
  it("reads the raw Python snapshot contract into all caches", async () => {
    await service.refreshAll();
    const context = service.getContext("600000");
    expect(context.sentiment?.score).toBe(65);
    expect(context.macroRegime).toBe("BULL");
    expect(context.stStatus?.riskLevel).toBe("extreme");
    expect(context.catalyst?.description).toBe("QCOM +6%");
    expect(context.auction?.score).toBe(80);
    expect(runPython.mock.calls.every((call) => call[3]?.maxRetries === 0)).toBe(true);
  });

  it("refreshes macro and ST while the Yahoo process is still pending", async () => {
    let finish!: (value: never) => void;
    runPython.mockImplementation(async (_script, args) => args.action === "catalyst_snapshot"
      ? new Promise((resolve) => { finish = resolve; })
      : response(String(args.action)) as never);
    const pending = service.refreshAll();
    await vi.advanceTimersByTimeAsync(0);
    expect(service.getContext("600000").macroRegime).toBe("BULL");
    expect(service.getContext("600000").stStatus?.riskLevel).toBe("extreme");
    finish({ ok: false, error: "Yahoo timeout" } as never);
    await pending;
    expect(service.getContext("600000").catalyst).toBeNull();
  });

  it("keeps previous catalyst data on unavailable snapshots", async () => {
    await service.refreshAll();
    const old = service.getContext("600000").catalyst;
    vi.setSystemTime(new Date("2026-10-19T04:00:00Z"));
    runPython.mockImplementation(async (_script, args) => args.action === "catalyst_snapshot"
      ? { ok: true, data: { catalysts: null } } as never
      : response(String(args.action)) as never);
    await service.refreshAll();
    expect(service.getContext("600000").catalyst).toBe(old);
  });

  it("clears old catalyst signals when a successful scan finds none", async () => {
    await service.refreshAll();
    vi.setSystemTime(new Date("2026-10-19T04:00:00Z"));
    runPython.mockImplementation(async (_script, args) => args.action === "catalyst_snapshot"
      ? { ok: true, data: { catalysts: {} } } as never
      : response(String(args.action)) as never);
    await service.refreshAll();
    expect(service.getContext("600000").catalyst).toBeNull();
  });

  it("refreshes low-frequency data after TTL expiration without duplicate start timers", async () => {
    service.start();
    service.start();
    await vi.advanceTimersByTimeAsync(35 * 60_000);
    const actions = runPython.mock.calls.map((call) => call[1].action);
    expect(actions.filter((action) => action === "macro_snapshot")).toHaveLength(2);
    expect(actions.filter((action) => action === "catalyst_snapshot")).toHaveLength(1);
    expect(actions.filter((action) => action === "st_snapshot")).toHaveLength(1);
  });

  it("passes the watchlist and prevents overlapping auction scans", async () => {
    vi.setSystemTime(new Date("2026-10-19T01:20:00Z"));
    let finish!: (value: never) => void;
    runPython.mockImplementation(async (_script, args) => args.action === "auction_snapshot"
      ? new Promise((resolve) => { finish = resolve; })
      : response(String(args.action)) as never);
    service.setWatchedSymbols(["600000", "600000"]);
    service.start();
    await vi.advanceTimersByTimeAsync(30_000);
    const calls = runPython.mock.calls.filter((call) => call[1].action === "auction_snapshot");
    expect(calls).toHaveLength(1);
    expect(calls[0][1].symbols).toEqual(["600000"]);
    finish(response("auction_snapshot") as never);
    await vi.advanceTimersByTimeAsync(0);
  });
});
