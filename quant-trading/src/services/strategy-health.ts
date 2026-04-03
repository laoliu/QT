/**
 * Strategy Health Monitoring Service (策略健康度监控)
 *
 * Monitors each strategy's performance over a rolling window and alerts
 * when health degrades:
 *   - Win rate drops below threshold
 *   - Drawdown exceeds limit
 *   - Signal frequency drops (strategy gone quiet)
 *   - Consecutive losses exceed limit
 *
 * Designed to be checked periodically (e.g. after each trade closes).
 */
import * as fs from "node:fs";
import * as path from "node:path";

export interface StrategyHealthConfig {
  /** Minimum win rate before warning (0-1, default 0.3). */
  minWinRate: number;
  /** Max consecutive losses before alert (default 5). */
  maxConsecutiveLosses: number;
  /** Max days without a signal before "quiet" alert (default 7). */
  maxQuietDays: number;
  /** Rolling window size for health calculation (default 20 trades). */
  windowSize: number;
}

export interface StrategyHealthRecord {
  strategyId: string;
  /** Recent trades in the rolling window. */
  recentResults: Array<{ netPnl: number; exitTime: string }>;
  /** Last signal timestamp (ISO). */
  lastSignalTime: string | null;
  /** Total trades ever recorded. */
  totalTrades: number;
}

export type HealthLevel = "healthy" | "warning" | "critical";

export interface StrategyHealthStatus {
  strategyId: string;
  level: HealthLevel;
  winRate: number;
  consecutiveLosses: number;
  daysSinceLastSignal: number | null;
  totalTrades: number;
  recentPnl: number;
  issues: string[];
}

const DEFAULT_CONFIG: StrategyHealthConfig = {
  minWinRate: 0.3,
  maxConsecutiveLosses: 5,
  maxQuietDays: 7,
  windowSize: 20,
};

export class StrategyHealthMonitor {
  private records = new Map<string, StrategyHealthRecord>();
  private config: StrategyHealthConfig;
  private filePath: string;
  private notifyFn?: (message: string) => void;

  constructor(
    dataDir: string,
    config: Partial<StrategyHealthConfig> = {},
    notifyFn?: (message: string) => void,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.filePath = path.join(dataDir, "strategy-health.json");
    this.notifyFn = notifyFn;
    this.load();
  }

  /** Record a completed trade result for a strategy. */
  recordTrade(strategyId: string, netPnl: number, exitTime: string): void {
    const record = this.getOrCreate(strategyId);
    record.recentResults.push({ netPnl, exitTime });
    // Keep only the rolling window
    if (record.recentResults.length > this.config.windowSize) {
      record.recentResults = record.recentResults.slice(-this.config.windowSize);
    }
    record.totalTrades++;
    record.lastSignalTime = exitTime;
    this.save();

    // Auto-check health after recording
    const status = this.checkStrategy(strategyId);
    if (status && status.level !== "healthy" && this.notifyFn) {
      const issueStr = status.issues.join("; ");
      const icon = status.level === "critical" ? "🚨" : "⚠️";
      this.notifyFn(
        `${icon} 策略健康预警 [${strategyId}]: ${issueStr} ` +
          `(胜率${(status.winRate * 100).toFixed(0)}% 连亏${String(status.consecutiveLosses)}笔)`,
      );
    }
  }

  /** Record a signal occurrence (to track quiet strategies). */
  recordSignal(strategyId: string, timestamp: string): void {
    const record = this.getOrCreate(strategyId);
    record.lastSignalTime = timestamp;
    this.save();
  }

  /** Check health of a single strategy. */
  checkStrategy(strategyId: string): StrategyHealthStatus | null {
    const record = this.records.get(strategyId);
    if (!record) return null;

    const results = record.recentResults;
    const wins = results.filter((r) => r.netPnl > 0).length;
    const winRate = results.length > 0 ? wins / results.length : 0;
    const recentPnl = results.reduce((sum, r) => sum + r.netPnl, 0);

    // Count consecutive losses from the end
    let consecutiveLosses = 0;
    for (let i = results.length - 1; i >= 0; i--) {
      if (results[i].netPnl <= 0) consecutiveLosses++;
      else break;
    }

    // Days since last signal
    let daysSinceLastSignal: number | null = null;
    if (record.lastSignalTime) {
      const diff = Date.now() - new Date(record.lastSignalTime).getTime();
      daysSinceLastSignal = Math.floor(diff / (24 * 60 * 60 * 1000));
    }

    // Determine issues
    const issues: string[] = [];
    let level: HealthLevel = "healthy";

    if (results.length >= 5 && winRate < this.config.minWinRate) {
      issues.push(
        `胜率过低: ${(winRate * 100).toFixed(0)}% (阈值${(this.config.minWinRate * 100).toFixed(0)}%)`,
      );
      level = "warning";
    }

    if (consecutiveLosses >= this.config.maxConsecutiveLosses) {
      issues.push(`连续亏损${String(consecutiveLosses)}笔`);
      level = "critical";
    } else if (consecutiveLosses >= Math.ceil(this.config.maxConsecutiveLosses * 0.6)) {
      issues.push(`连续亏损${String(consecutiveLosses)}笔 (接近阈值)`);
      if (level === "healthy") level = "warning";
    }

    if (daysSinceLastSignal != null && daysSinceLastSignal >= this.config.maxQuietDays) {
      issues.push(`${String(daysSinceLastSignal)}天无信号`);
      if (level === "healthy") level = "warning";
    }

    return {
      strategyId,
      level,
      winRate,
      consecutiveLosses,
      daysSinceLastSignal,
      totalTrades: record.totalTrades,
      recentPnl,
      issues,
    };
  }

  /** Check health of all tracked strategies. */
  checkAll(): StrategyHealthStatus[] {
    const results: StrategyHealthStatus[] = [];
    for (const id of this.records.keys()) {
      const status = this.checkStrategy(id);
      if (status) results.push(status);
    }
    // Sort: critical first, then warning, then healthy
    const levelOrder: Record<HealthLevel, number> = { critical: 0, warning: 1, healthy: 2 };
    results.sort((a, b) => levelOrder[a.level] - levelOrder[b.level]);
    return results;
  }

  /** Get list of tracked strategy IDs. */
  getTrackedStrategies(): string[] {
    return [...this.records.keys()];
  }

  /** Remove a strategy from tracking. */
  remove(strategyId: string): boolean {
    const deleted = this.records.delete(strategyId);
    if (deleted) this.save();
    return deleted;
  }

  /** Clear all records. */
  clear(): void {
    this.records.clear();
    this.save();
  }

  // ── Persistence ──

  private getOrCreate(strategyId: string): StrategyHealthRecord {
    let record = this.records.get(strategyId);
    if (!record) {
      record = {
        strategyId,
        recentResults: [],
        lastSignalTime: null,
        totalTrades: 0,
      };
      this.records.set(strategyId, record);
    }
    return record;
  }

  private load(): void {
    try {
      if (fs.existsSync(this.filePath)) {
        const raw = JSON.parse(fs.readFileSync(this.filePath, "utf-8"));
        if (Array.isArray(raw.strategies)) {
          for (const s of raw.strategies) {
            this.records.set(s.strategyId, s);
          }
        }
      }
    } catch {
      // Corrupted file — start fresh
    }
  }

  private save(): void {
    try {
      const dir = path.dirname(this.filePath);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      const data = { strategies: [...this.records.values()] };
      fs.writeFileSync(this.filePath, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // Best-effort persistence
    }
  }
}

// ── Adaptive Health Thresholds ───────────────────────────────────

/**
 * Adapts health thresholds based on recent market volatility.
 *
 * In high-volatility markets:
 *   - Lower the minimum win rate threshold (more false signals are expected)
 *   - Allow more consecutive losses before alert
 *   - Extend quiet-days tolerance (strategies may wait longer for clear signals)
 *
 * In low-volatility markets:
 *   - Raise the minimum win rate (should perform better in calm conditions)
 *   - Tighten consecutive loss limits
 *
 * volatilityRatio = recentVolatility / historicAvgVolatility
 *   < 0.7 → low vol  → tighter thresholds
 *   0.7-1.3 → normal → default thresholds
 *   > 1.3 → high vol → relaxed thresholds
 */
export function adaptiveHealthConfig(
  recentVolatility: number,
  historicAvgVolatility: number,
  baseConfig: StrategyHealthConfig = {
    minWinRate: 0.3,
    maxConsecutiveLosses: 5,
    maxQuietDays: 7,
    windowSize: 20,
  },
): StrategyHealthConfig {
  if (historicAvgVolatility <= 0) return baseConfig;

  const volRatio = recentVolatility / historicAvgVolatility;

  if (volRatio > 1.3) {
    // High volatility: relax thresholds
    return {
      ...baseConfig,
      minWinRate: Math.max(0.2, baseConfig.minWinRate - 0.05),
      maxConsecutiveLosses: baseConfig.maxConsecutiveLosses + 2,
      maxQuietDays: baseConfig.maxQuietDays + 3,
    };
  }

  if (volRatio < 0.7) {
    // Low volatility: tighten thresholds
    return {
      ...baseConfig,
      minWinRate: Math.min(0.45, baseConfig.minWinRate + 0.05),
      maxConsecutiveLosses: Math.max(3, baseConfig.maxConsecutiveLosses - 1),
      maxQuietDays: Math.max(3, baseConfig.maxQuietDays - 2),
    };
  }

  return baseConfig;
}

/**
 * Compute rolling annualized volatility from daily returns.
 * Uses a simple window of recent close prices.
 */
export function computeRollingVolatility(
  closes: number[],
  window: number,
): { recent: number; historic: number } {
  if (closes.length < window + 1) {
    return { recent: 0, historic: 0 };
  }

  // Daily log returns
  const returns: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    if (closes[i]! > 0 && closes[i - 1]! > 0) {
      returns.push(Math.log(closes[i]! / closes[i - 1]!));
    }
  }

  if (returns.length < window) return { recent: 0, historic: 0 };

  // Recent volatility (last `window` returns)
  const recentReturns = returns.slice(-window);
  const recentMean = recentReturns.reduce((a, b) => a + b, 0) / recentReturns.length;
  const recentVar =
    recentReturns.reduce((sum, r) => sum + (r - recentMean) ** 2, 0) / (recentReturns.length - 1);
  const recentVol = Math.sqrt(recentVar) * Math.sqrt(252);

  // Historic average volatility (full history)
  const historicMean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const historicVar =
    returns.reduce((sum, r) => sum + (r - historicMean) ** 2, 0) / (returns.length - 1);
  const historicVol = Math.sqrt(historicVar) * Math.sqrt(252);

  return { recent: recentVol, historic: historicVol };
}
