/**
 * PnLMonitor — background service that periodically polls account & position
 * data and emits alerts at configurable severity levels.
 *
 * Alert levels:
 *   - info:     Daily PnL crosses ±1% (configurable), position updates
 *   - warning:  Daily PnL crosses warning threshold (default -3%)
 *   - critical: Daily PnL crosses circuit-breaker threshold (default -5%),
 *               triggers emergency pause on LiveEngine
 *
 * Runs every `pollIntervalSecs` (default 30s) while the market is open.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { BrokerAccount, LivePosition, QuantTradingConfig, TradeRecord } from "../types.js";
import { tryNotify } from "./notify-helper.js";
import type { OrderManager } from "./order-manager.js";

// ── Daily Settlement types ───────────────────────────────────────

export type DailySettlement = {
  /** ISO date string (YYYY-MM-DD) */
  date: string;
  /** Today's realized PnL from closed trades */
  realizedPnl: number;
  /** Change in unrealized PnL compared to previous close (estimated from positions) */
  unrealizedPnlChange: number;
  /** Win rate of today's closed trades (0-1) */
  winRate: number;
  /** Number of winning trades today */
  wins: number;
  /** Number of losing trades today */
  losses: number;
  /** Profit factor: gross wins / gross losses (Infinity if no losses) */
  profitFactor: number;
  /** Total commission + tax paid today */
  totalFees: number;
  /** Net P&L = realizedPnl - totalFees + unrealizedPnlChange */
  netPnl: number;
  /** Number of closed (round-trip) trades today */
  closedTrades: number;
  /** Total assets at settlement time */
  totalAssets: number;
  /** Net P&L as percentage of total assets */
  netPnlPct: number;
};

/**
 * Compute a structured daily settlement summary.
 *
 * Data sources:
 *   - BrokerAccount: todayProfit, totalAssets
 *   - LivePosition[]: unrealizedPnl per position
 *   - TradeRecord[]: today's closed trades for winRate / profitFactor / fees
 */
export function computeDailySettlement(
  account: BrokerAccount,
  positions: LivePosition[],
  todayTrades: TradeRecord[],
): DailySettlement {
  const today = new Date().toISOString().slice(0, 10);

  // Realized PnL from closed trades
  let grossWins = 0;
  let grossLosses = 0;
  let totalFees = 0;
  let wins = 0;
  let losses = 0;

  for (const t of todayTrades) {
    totalFees += t.commission;
    if (t.netPnl > 0) {
      grossWins += t.grossPnl;
      wins++;
    } else if (t.netPnl < 0) {
      grossLosses += Math.abs(t.grossPnl);
      losses++;
    }
  }

  const realizedPnl = todayTrades.reduce((sum, t) => sum + t.grossPnl, 0);
  const closedTrades = todayTrades.length;
  const winRate = closedTrades > 0 ? wins / closedTrades : 0;
  const profitFactor = grossLosses > 0 ? grossWins / grossLosses : grossWins > 0 ? Infinity : 0;

  // Unrealized PnL change: sum of current unrealized PnL across positions
  // (This approximates change since positions may have been opened/closed today;
  //  for a precise delta, a previous-close snapshot would be needed.)
  const unrealizedPnlChange = positions.reduce((sum, p) => sum + (p.unrealizedPnl ?? 0), 0);

  const netPnl = realizedPnl - totalFees + unrealizedPnlChange;
  const totalAssets = account.totalAssets;
  const netPnlPct = totalAssets > 0 ? netPnl / totalAssets : 0;

  return {
    date: today,
    realizedPnl,
    unrealizedPnlChange,
    winRate,
    wins,
    losses,
    profitFactor,
    totalFees,
    netPnl,
    closedTrades,
    totalAssets,
    netPnlPct,
  };
}

// ── Alert types ──────────────────────────────────────────────────

export type AlertSeverity = "info" | "warning" | "critical";

export type PnlAlert = {
  severity: AlertSeverity;
  message: string;
  timestamp: string;
  /** Current daily PnL percentage */
  pnlPct?: number;
  /** Which metric triggered the alert */
  trigger: "daily_pnl" | "position_loss" | "circuit_breaker";
};

// ── Config ───────────────────────────────────────────────────────

export type PnlMonitorConfig = {
  /** Polling interval when market is open (default 30s) */
  pollIntervalSecs?: number;
  /** Daily PnL warning threshold as negative pct (default -0.03 = -3%) */
  warningPct?: number;
  /** Daily PnL circuit-breaker threshold (default -0.05 = -5%) */
  circuitBreakerPct?: number;
  /** Per-position loss warning threshold (default -0.08 = -8%) */
  positionWarningPct?: number;
  /** Enable/disable the monitor (default true) */
  enabled?: boolean;
};

const DEFAULTS: Required<PnlMonitorConfig> = {
  pollIntervalSecs: 30,
  warningPct: -0.03,
  circuitBreakerPct: -0.05,
  positionWarningPct: -0.08,
  enabled: true,
};

// ── Emoji by severity ────────────────────────────────────────────

const SEVERITY_EMOJI: Record<AlertSeverity, string> = {
  info: "ℹ️",
  warning: "⚠️",
  critical: "🚨",
};

// ── Service factory ──────────────────────────────────────────────

export function createPnlMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getOrderManager: () => OrderManager | null,
  pauseEngine?: () => Promise<void>,
  /** Optional callback invoked on every alert (e.g. for persisting to AlertStore). */
  onAlert?: (alert: PnlAlert) => void,
): OpenClawPluginService {
  const monitorCfg: Required<PnlMonitorConfig> = {
    ...DEFAULTS,
    ...(config.pnlMonitor ?? {}),
  };

  let interval: ReturnType<typeof setInterval> | null = null;

  // Dedup: track which alert triggers have fired this trading day
  let firedToday = new Set<string>();
  let lastTradingDay = "";

  const resetDailyState = () => {
    const today = new Date().toISOString().slice(0, 10);
    if (today !== lastTradingDay) {
      firedToday = new Set();
      lastTradingDay = today;
    }
  };

  const emitAlert = async (alert: PnlAlert): Promise<void> => {
    const key = `${alert.trigger}:${alert.severity}`;
    // Only fire each severity+trigger combo once per day
    if (firedToday.has(key)) return;
    firedToday.add(key);

    const emoji = SEVERITY_EMOJI[alert.severity];
    const msg = `${emoji} [PnL Monitor] ${alert.message}`;
    api.logger.info(`PnlMonitor alert [${alert.severity}]: ${alert.message}`);

    // Notify alert store (if wired)
    onAlert?.(alert);

    await tryNotify(api, msg);
  };

  const checkPositionLosses = async (positions: LivePosition[]): Promise<void> => {
    for (const pos of positions) {
      if (pos.avgCost <= 0 || !pos.currentPrice) continue;
      const lossPct = (pos.currentPrice - pos.avgCost) / pos.avgCost;
      if (lossPct <= monitorCfg.positionWarningPct) {
        await emitAlert({
          severity: "warning",
          message:
            `${pos.symbol} 浮亏 ${(lossPct * 100).toFixed(1)}% ` +
            `(成本 ¥${pos.avgCost.toFixed(2)} → 现价 ¥${pos.currentPrice.toFixed(2)})`,
          timestamp: new Date().toISOString(),
          pnlPct: lossPct,
          trigger: "position_loss",
        });
      }
    }
  };

  const tick = async () => {
    if (!isMarketOpen()) return;

    resetDailyState();
    const mgr = getOrderManager();
    if (!mgr) return;

    let account: BrokerAccount;
    let positions: LivePosition[];
    try {
      [account, positions] = await Promise.all([
        mgr.broker.queryAccount(),
        mgr.broker.queryPositions(),
      ]);
    } catch (err) {
      api.logger.warn(`PnlMonitor: 无法获取账户数据: ${(err as Error).message}`);
      return;
    }

    // Daily PnL as percentage of total assets
    const dailyPnl = account.todayProfit ?? 0;
    const totalAssets = account.totalAssets;
    const dailyPnlPct = totalAssets > 0 ? dailyPnl / totalAssets : 0;

    // ── Circuit breaker (critical) ──
    if (dailyPnlPct <= monitorCfg.circuitBreakerPct) {
      await emitAlert({
        severity: "critical",
        message:
          `日内亏损 ${(dailyPnlPct * 100).toFixed(2)}% (¥${dailyPnl.toFixed(2)}) ` +
          `触及熔断阈值 ${(monitorCfg.circuitBreakerPct * 100).toFixed(0)}%，正在暂停交易引擎`,
        timestamp: new Date().toISOString(),
        pnlPct: dailyPnlPct,
        trigger: "circuit_breaker",
      });
      // Emergency pause
      if (pauseEngine) {
        try {
          await pauseEngine();
          api.logger.warn("PnlMonitor: 交易引擎已被熔断暂停");
        } catch (err) {
          api.logger.error(`PnlMonitor: 暂停引擎失败: ${(err as Error).message}`);
        }
      }
      return; // Skip lower-severity checks
    }

    // ── Warning level ──
    if (dailyPnlPct <= monitorCfg.warningPct) {
      await emitAlert({
        severity: "warning",
        message:
          `日内亏损 ${(dailyPnlPct * 100).toFixed(2)}% (¥${dailyPnl.toFixed(2)}) ` +
          `达到预警阈值 ${(monitorCfg.warningPct * 100).toFixed(0)}%，请关注风险`,
        timestamp: new Date().toISOString(),
        pnlPct: dailyPnlPct,
        trigger: "daily_pnl",
      });
    }

    // ── Per-position loss check ──
    await checkPositionLosses(positions);
  };

  return {
    id: "quant-pnl-monitor",
    async start() {
      if (!monitorCfg.enabled) {
        api.logger.info("PnlMonitor: disabled by config");
        return;
      }
      api.logger.info(
        `PnlMonitor: started (poll=${monitorCfg.pollIntervalSecs}s, ` +
          `warn=${(monitorCfg.warningPct * 100).toFixed(0)}%, ` +
          `breaker=${(monitorCfg.circuitBreakerPct * 100).toFixed(0)}%)`,
      );
      interval = setInterval(() => void tick(), monitorCfg.pollIntervalSecs * 1000);
      // Run first check immediately
      void tick();
    },
    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("PnlMonitor: stopped");
    },
  };
}
