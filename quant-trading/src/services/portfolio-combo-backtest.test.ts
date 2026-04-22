import { describe, expect, it } from "vitest";
import type {
  ApplyComboToPaperOptions,
  ComboBacktestOptions,
  ComboCompareResult,
  ComboContribution,
  ComboDrawdownPoint,
  ComboEquityPoint,
  ComboMonthlyReturn,
  ComboSubResult,
  PortfolioComboBacktestResult,
} from "./portfolio-combo-backtest.js";

// ── Type-level tests (compile-time) + shape validation ──────────

describe("ComboBacktestOptions", () => {
  it("accepts minimal options", () => {
    const opts: ComboBacktestOptions = {};
    expect(opts).toBeDefined();
  });

  it("accepts full options", () => {
    const opts: ComboBacktestOptions = {
      batchId: "batch-2025",
      topN: 10,
      allocationMode: "max_sharpe",
      rebalanceFreq: "monthly",
      periodDays: 252,
      initialCapital: 1_000_000,
      customWeights: [0.1, 0.2, 0.3, 0.4],
    };
    expect(opts.topN).toBe(10);
    expect(opts.allocationMode).toBe("max_sharpe");
    expect(opts.customWeights).toHaveLength(4);
  });
});

describe("PortfolioComboBacktestResult shape", () => {
  const mockResult: PortfolioComboBacktestResult = {
    ok: true,
    allocation_mode: "equal",
    rebalance_freq: "monthly",
    period_days: 252,
    initial_capital: 1_000_000,
    top_n: 3,
    strategies_used: ["macd_cross", "rsi_reversal", "momentum"],
    combined_metrics: {
      total_return: 15.23,
      annualized_return: 12.5,
      sharpe_ratio: 1.35,
      max_drawdown: 8.2,
      win_rate: 54.3,
      calmar_ratio: 1.52,
    },
    equity_curve: [
      { date: "2024-01-02", value: 1_000_000 },
      { date: "2024-12-31", value: 1_152_300 },
    ],
    sub_results: [
      { name: "macd_cross", weight: 0.333, annual_return: 18.5, max_drawdown: 10.2, sharpe: 1.2, total_trades: 45, total_fees: 1200 },
      { name: "rsi_reversal", weight: 0.333, annual_return: 8.1, max_drawdown: 6.5, sharpe: 0.9, total_trades: 32, total_fees: 800 },
      { name: "momentum", weight: 0.334, annual_return: 12.3, max_drawdown: 12.1, sharpe: 1.5, total_trades: 28, total_fees: 600 },
    ],
    sub_equity_curves: {
      macd_cross: [{ date: "2024-01-02", value: 100 }, { date: "2024-12-31", value: 118.5 }],
    },
    correlation_matrix: {
      macd_cross: { macd_cross: 1, rsi_reversal: 0.35, momentum: 0.12 },
      rsi_reversal: { macd_cross: 0.35, rsi_reversal: 1, momentum: 0.48 },
      momentum: { macd_cross: 0.12, rsi_reversal: 0.48, momentum: 1 },
    },
    monthly_returns: [
      { month: "2024-01", return: 2.1 },
      { month: "2024-02", return: -0.5 },
    ],
    drawdown_curve: [
      { date: "2024-01-02", drawdown: 0 },
      { date: "2024-03-15", drawdown: -5.2 },
    ],
    contributions: [
      { strategy_id: "macd_cross", contribution_pct: 45.2, weighted_return: 6.16 },
      { strategy_id: "rsi_reversal", contribution_pct: 20.1, weighted_return: 2.7 },
      { strategy_id: "momentum", contribution_pct: 34.7, weighted_return: 4.11 },
    ],
  };

  it("has correct ok flag", () => {
    expect(mockResult.ok).toBe(true);
  });

  it("has all combined metrics", () => {
    const m = mockResult.combined_metrics;
    expect(m.total_return).toBeTypeOf("number");
    expect(m.annualized_return).toBeTypeOf("number");
    expect(m.sharpe_ratio).toBeTypeOf("number");
    expect(m.max_drawdown).toBeTypeOf("number");
    expect(m.win_rate).toBeTypeOf("number");
    expect(m.calmar_ratio).toBeTypeOf("number");
  });

  it("has strategies_used matching sub_results count", () => {
    expect(mockResult.strategies_used).toHaveLength(mockResult.sub_results.length);
  });

  it("sub_result weights sum ≈ 1.0", () => {
    const wsum = mockResult.sub_results.reduce((s, r) => s + r.weight, 0);
    expect(wsum).toBeCloseTo(1.0, 2);
  });

  it("equity_curve values are monotonically described by dates", () => {
    const dates = mockResult.equity_curve.map((p) => p.date);
    expect(dates[0] < dates[dates.length - 1]).toBe(true);
  });

  it("contributions add up to ~100%", () => {
    const total = mockResult.contributions.reduce((s, c) => s + c.contribution_pct, 0);
    expect(total).toBeCloseTo(100, 0);
  });

  it("correlation matrix is symmetric", () => {
    const corr = mockResult.correlation_matrix!;
    const keys = Object.keys(corr);
    for (const r of keys) {
      for (const c of keys) {
        expect(corr[r][c]).toBeCloseTo(corr[c][r], 4);
      }
    }
  });

  it("correlation matrix diagonal is 1.0", () => {
    const corr = mockResult.correlation_matrix!;
    for (const k of Object.keys(corr)) {
      expect(corr[k][k]).toBe(1);
    }
  });
});

describe("ComboCompareResult shape", () => {
  it("holds multiple allocation mode results", () => {
    const compare: ComboCompareResult = {
      ok: true,
      comparisons: {
        equal: { ok: true, allocation_mode: "equal" } as PortfolioComboBacktestResult,
        max_sharpe: { ok: true, allocation_mode: "max_sharpe" } as PortfolioComboBacktestResult,
      },
    };
    expect(Object.keys(compare.comparisons)).toHaveLength(2);
    expect(compare.comparisons.equal.allocation_mode).toBe("equal");
    expect(compare.comparisons.max_sharpe.allocation_mode).toBe("max_sharpe");
  });
});

describe("ApplyComboToPaperOptions", () => {
  it("accepts minimal options", () => {
    const opts: ApplyComboToPaperOptions = {
      allocations: [{ strategy_name: "macd_cross", weight: 0.5 }],
    };
    expect(opts.allocations).toHaveLength(1);
    expect(opts.dryRun).toBeUndefined();
  });

  it("accepts full options", () => {
    const opts: ApplyComboToPaperOptions = {
      allocations: [
        { strategy_name: "macd_cross", weight: 0.5, latest_signals: [] },
        { strategy_name: "rsi_reversal", weight: 0.5 },
      ],
      dryRun: true,
      executePct: 80,
      createConditionalOrders: true,
      trailingStopPct: 3,
    };
    expect(opts.dryRun).toBe(true);
    expect(opts.executePct).toBe(80);
    expect(opts.allocations).toHaveLength(2);
  });

  it("weights in allocations sum to 1.0", () => {
    const opts: ApplyComboToPaperOptions = {
      allocations: [
        { strategy_name: "a", weight: 0.3 },
        { strategy_name: "b", weight: 0.3 },
        { strategy_name: "c", weight: 0.4 },
      ],
    };
    const wsum = opts.allocations.reduce((s, a) => s + a.weight, 0);
    expect(wsum).toBeCloseTo(1.0, 10);
  });
});

describe("Edge cases for result types", () => {
  it("handles error result", () => {
    const r: PortfolioComboBacktestResult = {
      ok: false,
      error: "没有找到全局策略排名数据",
    } as PortfolioComboBacktestResult;
    expect(r.ok).toBe(false);
    expect(r.error).toContain("策略排名");
  });

  it("handles empty equity curve", () => {
    const pts: ComboEquityPoint[] = [];
    expect(pts).toHaveLength(0);
  });

  it("handles null correlation matrix", () => {
    const r = { correlation_matrix: null } as Partial<PortfolioComboBacktestResult>;
    expect(r.correlation_matrix).toBeNull();
  });

  it("drawdown values are negative or zero", () => {
    const dd: ComboDrawdownPoint[] = [
      { date: "2024-01-02", drawdown: 0 },
      { date: "2024-02-01", drawdown: -3.5 },
      { date: "2024-03-01", drawdown: -8.2 },
    ];
    for (const p of dd) {
      expect(p.drawdown).toBeLessThanOrEqual(0);
    }
  });

  it("monthly returns can be negative", () => {
    const mr: ComboMonthlyReturn[] = [
      { month: "2024-01", return: 5.2 },
      { month: "2024-02", return: -3.1 },
    ];
    expect(mr[0].return).toBeGreaterThan(0);
    expect(mr[1].return).toBeLessThan(0);
  });

  it("contribution pct can be negative for losing strategies", () => {
    const c: ComboContribution = {
      strategy_id: "bad_strategy",
      contribution_pct: -15.3,
      weighted_return: -0.023,
    };
    expect(c.contribution_pct).toBeLessThan(0);
  });
});
