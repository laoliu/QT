/**
 * Unit tests for chat command handlers — validates argument parsing,
 * engine state checks, and formatting for /confirm, /reject, /pause,
 * /resume, /orders, /pnl, /quote (stub), /screen, /backtest, /portfolio, /signal,
 * /预警, /watchlist, /quant, /板块, /资金, /龙虎榜.
 *
 * Heavy I/O commands (/选股) are excluded — they spawn real subprocesses.
 */
import { describe, expect, it, vi } from "vitest";

// ── Mock dependencies ──────────────────────────────────────────
vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: vi.fn(() => ({
    name: "demo",
    getQuote: vi.fn(),
  })),
  detectMarketFromSymbol: vi.fn(() => "cn_stock"),
}));

vi.mock("../engine/strategy-registry.js", () => ({
  listStrategies: vi.fn(() => [
    { id: "macd", name: "MACD Strategy" },
    { id: "rsi", name: "RSI Strategy" },
  ]),
  runBacktest: vi.fn().mockResolvedValue({
    strategyId: "macd",
    symbols: ["600519"],
    startDate: "2025-01-01",
    endDate: "2025-06-30",
    initialCash: 100000,
    finalValue: 112000,
    totalReturn: 0.12,
    annualizedReturn: 0.24,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 20,
    signals: [],
    equityCurve: [],
  }),
}));

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockImplementation((_script: string, args: Record<string, unknown>) => {
    if (args?.action === "detail") {
      return Promise.resolve({
        ok: true,
        data: {
          name: (args.sector as string) ?? "半导体",
          change_pct: 3.5,
          stocks: [{ code: "600519", name: "贵州茅台", change_pct: 2.1, price: 1800 }],
        },
      });
    }
    return Promise.resolve({
      ok: true,
      data: {
        sectors: [{ name: "半导体", change_pct: 3.5, leader: "中芯国际", leader_change: 5.2 }],
        stocks: [
          { code: "600519", name: "贵州茅台", change_pct: 2.1, net_inflow: 50000000, price: 1800 },
        ],
        date: "2026-03-03",
      },
    });
  }),
}));

import type { LiveEngine } from "../engine/live-engine.js";
import type { PriceAlertService } from "../services/price-alert.js";
import type { TaskScheduler, ScheduledTask } from "../services/task-scheduler.js";
import type { QuantTradingConfig } from "../types.js";
import { createQuantCommands } from "./index.js";

function makeApi() {
  return {
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() },
  } as never;
}

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-cmd-test",
  pythonPath: "python3",
} as QuantTradingConfig;

function findCommand(
  name: string,
  engine?: LiveEngine | null,
  alertService?: PriceAlertService,
  scheduler?: TaskScheduler,
) {
  const cmds = createQuantCommands(
    makeApi(),
    config,
    engine != null ? () => engine : undefined,
    alertService,
    scheduler,
  );
  return cmds.find((c) => c.name === name || c.aliases?.includes(name));
}

function makeMockAlertService(): PriceAlertService {
  return {
    addAlert: vi.fn().mockReturnValue("alert-0001"),
    removeAlert: vi.fn().mockReturnValue(true),
    listAlerts: vi.fn().mockReturnValue([
      {
        id: "alert-0001",
        symbol: "600519",
        type: "price_above",
        threshold: 2000,
        description: "600519 价格突破 ¥2000.00",
        triggered: false,
        repeating: false,
        createdAt: new Date().toISOString(),
      },
    ]),
    activeCount: 1,
  } as unknown as PriceAlertService;
}

describe("chat commands", () => {
  // ── Stub/redirect commands ────────────────────────────────────
  describe("/screen", () => {
    it("returns usage when args empty", async () => {
      const cmd = findCommand("/screen");
      const out = await cmd!.execute("");
      expect(out).toContain("用法");
    });

    it("attempts real screening with conditions", async () => {
      const cmd = findCommand("/screen");
      const out = await cmd!.execute("pe<20 roe>15");
      // Should show results from mock or graceful error
      expect(out).toContain("筛选条件");
    });

    it("echoes filter args", async () => {
      const cmd = findCommand("/screen");
      const out = await cmd!.execute("pe<20 roe>15");
      expect(out).toContain("pe<20 roe>15");
    });
  });

  describe("/backtest", () => {
    it("lists strategies when no args", async () => {
      const cmd = findCommand("/backtest");
      const out = await cmd!.execute("");
      expect(out).toContain("macd");
      expect(out).toContain("rsi");
    });

    it("runs backtest and shows results", async () => {
      const cmd = findCommand("/backtest");
      const out = await cmd!.execute("macd 600519");
      expect(out).toContain("回测结果");
      expect(out).toContain("macd");
      expect(out).toContain("600519");
      expect(out).toContain("总收益");
      expect(out).toContain("夏普比率");
    });
  });

  describe("/portfolio", () => {
    it("shows empty state when no portfolio file", async () => {
      const cmd = findCommand("/portfolio");
      const out = await cmd!.execute("");
      expect(out).toContain("暂无持仓");
    });
  });

  describe("/signal", () => {
    it("shows empty state when no signals file", async () => {
      const cmd = findCommand("/signal");
      const out = await cmd!.execute("");
      expect(out).toContain("暂无交易信号");
    });
  });

  // ── Live engine commands ──────────────────────────────────────

  describe("/pause", () => {
    it("returns error when engine is null", async () => {
      const cmd = findCommand("/pause", null);
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });

    it("pauses engine and returns confirmation", async () => {
      const engine = { pause: vi.fn() } as unknown as LiveEngine;
      const cmd = findCommand("/pause", engine);
      const out = await cmd!.execute("");
      expect(engine.pause).toHaveBeenCalled();
      expect(out).toContain("暂停");
    });
  });

  describe("/resume", () => {
    it("returns error when engine is null", async () => {
      const cmd = findCommand("/resume", null);
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });

    it("resumes engine and returns confirmation", async () => {
      const engine = { resume: vi.fn() } as unknown as LiveEngine;
      const cmd = findCommand("/resume", engine);
      const out = await cmd!.execute("");
      expect(engine.resume).toHaveBeenCalled();
      expect(out).toContain("恢复");
    });
  });

  describe("/confirm", () => {
    it("returns usage when no args", async () => {
      const engine = { orderManager: null } as unknown as LiveEngine;
      const cmd = findCommand("/confirm", engine);
      const out = await cmd!.execute("");
      expect(out).toContain("用法");
    });

    it("returns error when engine is null", async () => {
      const cmd = findCommand("/confirm", null);
      const out = await cmd!.execute("abc123");
      expect(out).toContain("❌");
    });

    it("confirms successfully", async () => {
      const engine = {
        orderManager: {
          confirmationManager: { confirm: vi.fn().mockResolvedValue("订单已确认") },
        },
      } as unknown as LiveEngine;
      const cmd = findCommand("/confirm", engine);
      const out = await cmd!.execute("abc123");
      expect(out).toContain("✅");
    });
  });

  describe("/reject", () => {
    it("returns usage when no args", async () => {
      const engine = { orderManager: null } as unknown as LiveEngine;
      const cmd = findCommand("/reject", engine);
      const out = await cmd!.execute("");
      expect(out).toContain("用法");
    });

    it("rejects successfully", async () => {
      const engine = {
        orderManager: {
          confirmationManager: { reject: vi.fn().mockResolvedValue("订单已拒绝") },
        },
      } as unknown as LiveEngine;
      const cmd = findCommand("/reject", engine);
      const out = await cmd!.execute("abc123");
      expect(out).toContain("🚫");
    });
  });

  describe("/orders", () => {
    it("returns error when engine is null", async () => {
      const cmd = findCommand("/orders", null);
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });

    it("returns empty message when no orders", async () => {
      const engine = {
        orderManager: { getOrders: vi.fn().mockResolvedValue([]) },
      } as unknown as LiveEngine;
      const cmd = findCommand("/orders", engine);
      const out = await cmd!.execute("");
      expect(out).toContain("暂无");
    });

    it("lists orders", async () => {
      const orders = [
        {
          orderId: "order-abc123",
          symbol: "600519",
          side: "buy",
          quantity: 100,
          filledQty: 50,
          status: "partial_filled",
        },
      ];
      const engine = {
        orderManager: { getOrders: vi.fn().mockResolvedValue(orders) },
      } as unknown as LiveEngine;
      const cmd = findCommand("/orders", engine);
      const out = await cmd!.execute("");
      expect(out).toContain("600519");
      expect(out).toContain("买");
      expect(out).toContain("已成50");
    });
  });

  describe("/pnl", () => {
    it("returns error when engine is null", async () => {
      const cmd = findCommand("/pnl", null);
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });

    it("shows daily pnl", async () => {
      const engine = {
        orderManager: { getDailyPnl: vi.fn().mockResolvedValue(1234.56) },
      } as unknown as LiveEngine;
      const cmd = findCommand("/pnl", engine);
      const out = await cmd!.execute("");
      expect(out).toContain("1234.56");
      expect(out).toContain("盈亏");
    });
  });

  // ── Command composition ──────────────────────────────────────
  describe("createQuantCommands", () => {
    it("returns array of command definitions", () => {
      const cmds = createQuantCommands(makeApi(), config);
      expect(cmds.length).toBeGreaterThan(5);
      for (const cmd of cmds) {
        expect(cmd.name).toBeTruthy();
        expect(typeof cmd.execute).toBe("function");
      }
    });

    it("all command names start with /", () => {
      const cmds = createQuantCommands(makeApi(), config);
      for (const cmd of cmds) {
        expect(cmd.name.startsWith("/")).toBe(true);
      }
    });
  });

  // ── /预警 (Price Alerts) ──────────────────────────────────────
  describe("/预警", () => {
    it("returns error when service unavailable", async () => {
      const cmd = findCommand("/预警");
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });

    it("lists alerts when no args", async () => {
      const svc = makeMockAlertService();
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("");
      expect(out).toContain("条件预警");
      expect(out).toContain("alert-0001");
    });

    it("adds an alert", async () => {
      const svc = makeMockAlertService();
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("add 600519 price_above 2000");
      expect(out).toContain("✅");
      expect(out).toContain("alert-0001");
      expect(svc.addAlert).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: "600519",
          type: "price_above",
          threshold: 2000,
        }),
      );
    });

    it("shows usage for add with wrong args", async () => {
      const svc = makeMockAlertService();
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("add");
      expect(out).toContain("用法");
    });

    it("rejects invalid condition type", async () => {
      const svc = makeMockAlertService();
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("add 600519 invalid_type 100");
      expect(out).toContain("❌");
      expect(out).toContain("无效条件类型");
    });

    it("deletes an alert", async () => {
      const svc = makeMockAlertService();
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("del alert-0001");
      expect(out).toContain("🗑️");
      expect(svc.removeAlert).toHaveBeenCalledWith("alert-0001");
    });

    it("shows empty state message", async () => {
      const svc = makeMockAlertService();
      (svc.listAlerts as ReturnType<typeof vi.fn>).mockReturnValue([]);
      const cmd = findCommand("/预警", null, svc);
      const out = await cmd!.execute("");
      expect(out).toContain("空");
    });
  });

  // ── /板块 (Sector Analysis) ───────────────────────────────────
  describe("/板块", () => {
    it("shows sector ranking by default", async () => {
      const cmd = findCommand("/板块");
      const out = await cmd!.execute("");
      expect(out).toContain("板块");
      expect(out).toContain("半导体");
    });

    it("shows sector detail for specific query", async () => {
      const cmd = findCommand("/板块");
      const out = await cmd!.execute("半导体");
      // Detail mode returns a formatted view
      expect(out).toContain("半导体");
    });
  });

  // ── /资金 (Fund Flow) ─────────────────────────────────────────
  describe("/资金", () => {
    it("shows ranking by default", async () => {
      const cmd = findCommand("/资金");
      const out = await cmd!.execute("");
      expect(out).toContain("资金");
    });
  });

  // ── /龙虎榜 (Dragon Tiger) ────────────────────────────────────
  describe("/龙虎榜", () => {
    it("shows daily board by default", async () => {
      const cmd = findCommand("/龙虎榜");
      const out = await cmd!.execute("");
      expect(out).toContain("龙虎榜");
    });
  });

  // ── /quant (Help) ─────────────────────────────────────────────
  describe("/quant", () => {
    it("shows help text with all command groups", async () => {
      const cmd = findCommand("/quant");
      const out = await cmd!.execute("");
      expect(out).toContain("量化交易插件");
      expect(out).toContain("/quote");
      expect(out).toContain("/板块");
      expect(out).toContain("/预警");
      expect(out).toContain("/task");
      expect(out).toContain("引擎状态");
    });
  });

  // ── /task (Scheduled Tasks) ──────────────────────────────────
  describe("/task", () => {
    function makeMockScheduler(tasks: Partial<ScheduledTask>[] = []): TaskScheduler {
      return {
        list: vi.fn().mockReturnValue(
          tasks.map((t) => ({
            id: t.id ?? "task-1",
            description: t.description ?? "测试任务",
            status: t.status ?? "pending",
            scheduledAt: t.scheduledAt ?? new Date().toISOString(),
            createdAt: t.createdAt ?? new Date().toISOString(),
            ...t,
          })),
        ),
        cancel: vi.fn().mockReturnValue(true),
        pendingCount: tasks.filter((t) => (t.status ?? "pending") === "pending").length,
      } as unknown as TaskScheduler;
    }

    it("shows empty state when no tasks", async () => {
      const sched = makeMockScheduler();
      const cmd = findCommand("/task", null, undefined, sched);
      const out = await cmd!.execute("");
      expect(out).toContain("空");
    });

    it("lists pending tasks", async () => {
      const sched = makeMockScheduler([
        {
          id: "task-1",
          description: "日报生成",
          status: "pending",
          meta: { reportId: "daily_report" },
        },
      ]);
      const cmd = findCommand("/task", null, undefined, sched);
      const out = await cmd!.execute("");
      expect(out).toContain("task-1");
      expect(out).toContain("日报生成");
    });

    it("cancels a task", async () => {
      const sched = makeMockScheduler();
      const cmd = findCommand("/task", null, undefined, sched);
      const out = await cmd!.execute("cancel task-1");
      expect(out).toContain("已取消");
    });

    it("returns error when service not available", async () => {
      const cmd = findCommand("/task");
      const out = await cmd!.execute("");
      expect(out).toContain("❌");
    });
  });
});
