/**
 * LiveBacktestCompareService — compare live trading performance against backtest results.
 *
 * Detects "strategy decay": when live performance degrades significantly
 * relative to what the backtest predicted. Generates alerts when the
 * divergence exceeds a configurable threshold (default 20%).
 *
 * Features:
 *   - Side-by-side metric comparison (return, Sharpe, drawdown, win rate)
 *   - Per-metric decay detection with configurable thresholds
 *   - Decay alert generation integrated with AlertStore
 *   - Historical decay tracking for trend analysis
 */

import type { BacktestResult } from "../types.js";
import type { AlertStore } from "./alert-store.js";
import type { BacktestStore } from "./backtest-store.js";
import type { EquityHistory, EquitySnapshot } from "./equity-history.js";
import type { EventBus } from "./event-bus.js";

// ── Types ────────────────────────────────────────────────────────

export interface LiveBacktestCompareRequest {
  /** Strategy ID to compare */
  strategyId: string;
  /** Start date for comparison (YYYY-MM-DD) */
  dateFrom: string;
  /** End date for comparison (YYYY-MM-DD) */
  dateTo: string;
}

export interface MetricComparison {
  metric: string;
  label: string;
  backtest: number;
  live: number;
  /** (live - backtest) / |backtest| as percentage; negative = decay */
  divergencePct: number;
  /** Whether this metric shows significant decay */
  decayed: boolean;
}

export interface LiveBacktestCompareResult {
  strategyId: string;
  dateFrom: string;
  dateTo: string;
  /** Backtest result summary */
  backtestSummary: {
    totalReturn: number;
    annualizedReturn: number;
    maxDrawdown: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
  };
  /** Live trading performance summary */
  liveSummary: {
    totalReturn: number;
    equityStart: number;
    equityEnd: number;
    maxDrawdown: number;
    tradingDays: number;
  };
  /** Per-metric divergence analysis */
  metrics: MetricComparison[];
  /** Overall decay score (average of absolute divergence percentages) */
  overallDecayScore: number;
  /** Whether any metric exceeds the decay threshold */
  hasDecay: boolean;
  /** Human-readable decay alerts */
  decayAlerts: string[];
}

export interface DecayAlert {
  strategyId: string;
  metric: string;
  label: string;
  divergencePct: number;
  threshold: number;
  message: string;
  timestamp: string;
}

// ── Config ───────────────────────────────────────────────────────

export interface LiveBacktestCompareConfig {
  /** Divergence threshold (percentage) to trigger decay alert. Default: 20 */
  decayThresholdPct?: number;
}

// ── Service ─────────────────────────────────────────────────────

export class LiveBacktestCompareService {
  private readonly decayThresholdPct: number;
  private readonly recentDecayAlerts: DecayAlert[] = [];
  private static readonly MAX_DECAY_ALERTS = 200;

  constructor(
    private backtestStore: BacktestStore,
    private equityHistory: EquityHistory,
    private alertStore: AlertStore | null,
    private eventBus: EventBus | null,
    private notifyFn: ((msg: string) => void) | null = null,
    config?: LiveBacktestCompareConfig,
  ) {
    this.decayThresholdPct = config?.decayThresholdPct ?? 20;
  }

  /**
   * Compare live trading performance against the most recent backtest
   * for the given strategy within the specified date range.
   */
  compare(req: LiveBacktestCompareRequest): LiveBacktestCompareResult {
    // Find the best matching backtest result for this strategy
    const backtestResult = this.findBacktestResult(req.strategyId);
    if (!backtestResult) {
      throw new Error(`未找到策略 "${req.strategyId}" 的回测记录，请先运行回测`);
    }

    // Get live equity snapshots within the date range
    const snapshots = this.equityHistory.getRange(req.dateFrom, req.dateTo);
    if (snapshots.length < 2) {
      throw new Error(
        `日期范围 ${req.dateFrom} ~ ${req.dateTo} 内的实盘权益数据不足（需要至少2条记录）`,
      );
    }

    // Compute live performance metrics
    const liveSummary = this.computeLiveMetrics(snapshots);

    // Build per-metric comparison
    const metrics = this.buildMetricComparisons(backtestResult, liveSummary);

    // Detect decay
    const decayAlerts: string[] = [];
    for (const m of metrics) {
      if (m.decayed) {
        const msg = `策略衰减告警: ${req.strategyId} 的 ${m.label} 实盘 vs 回测偏离 ${m.divergencePct.toFixed(1)}%（阈值 ${this.decayThresholdPct}%）`;
        decayAlerts.push(msg);

        const alert: DecayAlert = {
          strategyId: req.strategyId,
          metric: m.metric,
          label: m.label,
          divergencePct: m.divergencePct,
          threshold: this.decayThresholdPct,
          message: msg,
          timestamp: new Date().toISOString(),
        };
        this.recentDecayAlerts.push(alert);
        if (this.recentDecayAlerts.length > LiveBacktestCompareService.MAX_DECAY_ALERTS) {
          this.recentDecayAlerts.shift();
        }

        // Emit to alert store and event bus
        this.alertStore?.add("warning", "strategy", msg, {
          type: "strategy_decay",
          strategyId: req.strategyId,
          metric: m.metric,
          divergencePct: m.divergencePct,
        });
        this.eventBus?.emit("alert", {
          severity: "warning",
          type: "strategy_decay",
          message: msg,
          strategyId: req.strategyId,
          metric: m.metric,
          divergencePct: m.divergencePct,
        });
      }
    }

    // Notify user if there are decay alerts
    if (decayAlerts.length > 0 && this.notifyFn) {
      this.notifyFn(
        `⚠️ 检测到 ${req.strategyId} 策略衰减:\n${decayAlerts.join("\n")}`,
      );
    }

    const overallDecayScore =
      metrics.length > 0
        ? metrics.reduce((sum, m) => sum + Math.abs(m.divergencePct), 0) / metrics.length
        : 0;

    return {
      strategyId: req.strategyId,
      dateFrom: req.dateFrom,
      dateTo: req.dateTo,
      backtestSummary: {
        totalReturn: backtestResult.totalReturn,
        annualizedReturn: backtestResult.annualizedReturn,
        maxDrawdown: backtestResult.maxDrawdown,
        sharpeRatio: backtestResult.sharpeRatio,
        winRate: backtestResult.winRate,
        totalTrades: backtestResult.totalTrades,
      },
      liveSummary,
      metrics,
      overallDecayScore,
      hasDecay: decayAlerts.length > 0,
      decayAlerts,
    };
  }

  /**
   * Get all recent decay alerts, optionally filtered by strategy.
   */
  getDecayAlerts(strategyId?: string): DecayAlert[] {
    if (strategyId) {
      return this.recentDecayAlerts.filter((a) => a.strategyId === strategyId);
    }
    return [...this.recentDecayAlerts];
  }

  // ── Private helpers ──────────────────────────────────────────────

  private findBacktestResult(strategyId: string): BacktestResult | null {
    // Query backtest store for the most recent result with this strategy
    const records = this.backtestStore.query({
      strategyId,
      limit: 1,
      sortBy: "createdAt",
      sortDir: "desc",
    });
    if (records.length === 0) return null;
    const full = this.backtestStore.getFullResult(records[0]!.id);
    return full?.result ?? null;
  }

  private computeLiveMetrics(snapshots: EquitySnapshot[]): LiveBacktestCompareResult["liveSummary"] {
    const sorted = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));
    const first = sorted[0]!;
    const last = sorted[sorted.length - 1]!;
    const equityStart = first.equity;
    const equityEnd = last.equity;
    const totalReturn = equityStart > 0 ? ((equityEnd - equityStart) / equityStart) * 100 : 0;

    // Compute max drawdown from equity curve
    let peak = equityStart;
    let maxDrawdown = 0;
    for (const snap of sorted) {
      if (snap.equity > peak) peak = snap.equity;
      const dd = peak > 0 ? ((peak - snap.equity) / peak) * 100 : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }

    return {
      totalReturn,
      equityStart,
      equityEnd,
      maxDrawdown,
      tradingDays: sorted.length,
    };
  }

  private buildMetricComparisons(
    backtest: BacktestResult,
    live: LiveBacktestCompareResult["liveSummary"],
  ): MetricComparison[] {
    const comparisons: Array<{
      metric: string;
      label: string;
      backtest: number;
      live: number;
      higherIsBetter: boolean;
    }> = [
      {
        metric: "totalReturn",
        label: "总收益率",
        backtest: backtest.totalReturn,
        live: live.totalReturn,
        higherIsBetter: true,
      },
      {
        metric: "maxDrawdown",
        label: "最大回撤",
        backtest: backtest.maxDrawdown,
        live: live.maxDrawdown,
        higherIsBetter: false, // lower drawdown is better
      },
    ];

    return comparisons.map(({ metric, label, backtest: bt, live: lv, higherIsBetter }) => {
      const divergencePct = this.computeDivergence(bt, lv, higherIsBetter);
      const decayed = Math.abs(divergencePct) > this.decayThresholdPct && divergencePct < 0;
      return { metric, label, backtest: bt, live: lv, divergencePct, decayed };
    });
  }

  /**
   * Compute percentage divergence. Negative = live is worse than backtest.
   */
  private computeDivergence(backtest: number, live: number, higherIsBetter: boolean): number {
    if (backtest === 0) return live === 0 ? 0 : live > 0 ? 100 : -100;
    const rawPct = ((live - backtest) / Math.abs(backtest)) * 100;
    // For "lower is better" metrics (like drawdown), flip the sign:
    // if live drawdown is higher than backtest drawdown, that's decay (negative)
    return higherIsBetter ? rawPct : -rawPct;
  }
}
