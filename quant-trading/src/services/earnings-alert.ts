/**
 * EarningsAlertService — proactive background service that monitors
 * holdings and watchlist stocks for upcoming earnings reports and other
 * important stock events.
 *
 * Features:
 *   - Daily scan of holdings + watchlist for upcoming events
 *   - Push notifications 3 days and 1 day before events
 *   - Event types: earnings, restricted share lift-ban, dividends
 *   - Registers with TaskScheduler for persistence across restarts
 *
 * SOUL.md: "持仓股即将发布财报时，提前提醒并给出预期分析"
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import type { FavoritesStore } from "./favorites-store.js";
import { tryNotify } from "./notify-helper.js";
import type { TaskScheduler } from "./task-scheduler.js";

// ── Types ────────────────────────────────────────────────────────

export interface StockEvent {
  event_type: "earnings" | "lift_ban" | "dividend" | "shareholder_meeting";
  event_name: string;
  event_date: string;
  days_until: number;
  stock_name: string;
  details?: Record<string, unknown>;
}

export interface EarningsAlertConfig {
  /** Enable/disable the service (default true) */
  enabled?: boolean;
  /** How often to check in ms (default: 24h = 86_400_000). Minimum 1h. */
  checkIntervalMs?: number;
  /** Days before event to send first reminder (default 3) */
  firstReminderDays?: number;
  /** Days before event to send urgent reminder (default 1) */
  urgentReminderDays?: number;
  /** How far ahead to look for events in days (default 30) */
  lookaheadDays?: number;
}

const DEFAULTS: Required<EarningsAlertConfig> = {
  enabled: true,
  checkIntervalMs: 86_400_000, // 24 hours
  firstReminderDays: 3,
  urgentReminderDays: 1,
  lookaheadDays: 30,
};

const EVENT_TYPE_EMOJI: Record<string, string> = {
  earnings: "📊",
  lift_ban: "🔓",
  dividend: "💰",
  shareholder_meeting: "🏛️",
};

const EVENT_SEVERITY: Record<string, string> = {
  earnings: "重要",
  lift_ban: "关注",
  dividend: "提醒",
  shareholder_meeting: "提醒",
};

// ── Service factory ──────────────────────────────────────────────

export function createEarningsAlertService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  loadPortfolio: () => Promise<Portfolio>,
  favoritesStore: FavoritesStore,
  taskScheduler?: TaskScheduler,
): OpenClawPluginService {
  const alertCfg: Required<EarningsAlertConfig> = {
    ...DEFAULTS,
    ...(config.earningsAlert ?? {}),
  };

  let interval: ReturnType<typeof setInterval> | null = null;

  // Track which alerts have been sent to avoid duplicates
  // Key: `${symbol}:${event_type}:${event_date}:${reminder_type}`
  const sentAlerts = new Set<string>();
  let lastResetDate = "";

  const resetDailyState = () => {
    const today = new Date().toISOString().slice(0, 10);
    if (today !== lastResetDate) {
      // Keep sent alerts for 7 days, then clear
      const sevenDaysAgo = new Date(Date.now() - 7 * 86_400_000)
        .toISOString()
        .slice(0, 10);
      if (lastResetDate && lastResetDate < sevenDaysAgo) {
        sentAlerts.clear();
      }
      lastResetDate = today;
    }
  };

  /** Collect all symbols from holdings and watchlist. */
  const collectSymbols = async (): Promise<string[]> => {
    const symbolSet = new Set<string>();

    // 1. Paper portfolio positions
    try {
      const portfolio = await loadPortfolio();
      for (const pos of portfolio.positions) {
        if (pos.symbol) symbolSet.add(pos.symbol);
      }
    } catch {
      // Portfolio may not exist yet
    }

    // 2. Favorites / watchlist
    try {
      const favorites = favoritesStore.listFavorites();
      for (const fav of favorites) {
        if (fav.code) symbolSet.add(fav.code);
      }
    } catch {
      // Favorites may not exist yet
    }

    return Array.from(symbolSet);
  };

  /** Fetch events from Python engine. */
  const fetchEvents = async (
    symbols: string[],
  ): Promise<Record<string, StockEvent[]>> => {
    if (symbols.length === 0) return {};

    const result = await runPythonScript<{
      events?: Record<string, StockEvent[]>;
      error?: string;
    }>(
      "engines/stock_events.py",
      {
        action: "check_events",
        symbols,
        days: alertCfg.lookaheadDays,
      },
      config,
      { timeoutMs: 60_000 },
    );

    if (!result.ok || !result.data?.events) {
      api.logger.warn(
        `EarningsAlert: 获取事件数据失败: ${result.error ?? result.data?.error ?? "unknown"}`,
      );
      return {};
    }

    return result.data.events;
  };

  /** Format and send notifications for events. */
  const processEvents = async (
    events: Record<string, StockEvent[]>,
  ): Promise<void> => {
    const firstReminder: StockEvent[] = [];
    const urgentReminder: StockEvent[] = [];

    for (const [symbol, symbolEvents] of Object.entries(events)) {
      for (const event of symbolEvents) {
        const daysUntil = event.days_until;

        // First reminder: N days before
        if (
          daysUntil <= alertCfg.firstReminderDays &&
          daysUntil > alertCfg.urgentReminderDays
        ) {
          const key = `${symbol}:${event.event_type}:${event.event_date}:first`;
          if (!sentAlerts.has(key)) {
            sentAlerts.add(key);
            firstReminder.push(event);
          }
        }

        // Urgent reminder: 1 day before or same day
        if (daysUntil <= alertCfg.urgentReminderDays) {
          const key = `${symbol}:${event.event_type}:${event.event_date}:urgent`;
          if (!sentAlerts.has(key)) {
            sentAlerts.add(key);
            urgentReminder.push(event);
          }
        }
      }
    }

    // Send urgent alerts
    if (urgentReminder.length > 0) {
      const lines = urgentReminder.map((e) => {
        const emoji = EVENT_TYPE_EMOJI[e.event_type] ?? "📌";
        const daysText =
          e.days_until === 0 ? "今天" : `${e.days_until}天后`;
        return `${emoji} ${e.stock_name || ""}(${e.event_name}) — ${daysText} (${e.event_date})`;
      });
      const msg =
        `🚨 **股票事件紧急提醒**\n\n` +
        `以下持仓/自选股即将发生重要事件：\n\n` +
        lines.join("\n") +
        `\n\n建议提前做好应对准备。`;
      await tryNotify(api, msg);
    }

    // Send first-reminder alerts
    if (firstReminder.length > 0) {
      const lines = firstReminder.map((e) => {
        const emoji = EVENT_TYPE_EMOJI[e.event_type] ?? "📌";
        const severity = EVENT_SEVERITY[e.event_type] ?? "提醒";
        return `${emoji} [${severity}] ${e.stock_name || ""}(${e.event_name}) — ${e.days_until}天后 (${e.event_date})`;
      });
      const msg =
        `📅 **股票事件预告**\n\n` +
        `以下持仓/自选股近期有重要事件：\n\n` +
        lines.join("\n") +
        `\n\n请关注并提前规划。`;
      await tryNotify(api, msg);
    }
  };

  /** Main check routine. */
  const runCheck = async (): Promise<string> => {
    resetDailyState();

    const symbols = await collectSymbols();
    if (symbols.length === 0) {
      api.logger.debug("EarningsAlert: 无持仓或自选股，跳过检查");
      return "无持仓或自选股，跳过事件检查";
    }

    api.logger.info(
      `EarningsAlert: 检查 ${symbols.length} 只股票的事件 (前瞻 ${alertCfg.lookaheadDays} 天)`,
    );

    const events = await fetchEvents(symbols);

    // Count total events
    let totalEvents = 0;
    for (const symbolEvents of Object.values(events)) {
      totalEvents += symbolEvents.length;
    }

    if (totalEvents === 0) {
      api.logger.info("EarningsAlert: 未发现近期事件");
      return `已检查 ${symbols.length} 只股票，未发现近期事件`;
    }

    api.logger.info(
      `EarningsAlert: 发现 ${totalEvents} 个事件，处理提醒`,
    );

    await processEvents(events);

    return `已检查 ${symbols.length} 只股票，发现 ${totalEvents} 个近期事件`;
  };

  return {
    id: "quant-earnings-alert",
    async start() {
      if (!alertCfg.enabled) {
        api.logger.info("EarningsAlert: disabled by config");
        return;
      }

      api.logger.info(
        `EarningsAlert: started (interval=${Math.round(alertCfg.checkIntervalMs / 3600_000)}h, ` +
          `lookahead=${alertCfg.lookaheadDays}d, ` +
          `reminders=${alertCfg.firstReminderDays}d/${alertCfg.urgentReminderDays}d)`,
      );

      // Register with TaskScheduler for persistence (daily at 8:00 AM)
      if (taskScheduler) {
        const now = new Date();
        const nextRun = new Date(now);
        nextRun.setHours(8, 0, 0, 0);
        if (nextRun.getTime() <= now.getTime()) {
          nextRun.setDate(nextRun.getDate() + 1);
        }
        const delayMs = nextRun.getTime() - now.getTime();

        taskScheduler.scheduleDelay(
          "持仓股事件预警检查",
          delayMs,
          runCheck,
          {
            intervalMs: alertCfg.checkIntervalMs,
            meta: {
              toolName: "earnings_alert",
              toolArgs: { action: "check" },
            },
          },
        );
      }

      // Also set up an interval as a fallback
      interval = setInterval(
        () => void runCheck(),
        alertCfg.checkIntervalMs,
      );

      // Run first check after a short delay (let other services initialize)
      setTimeout(() => void runCheck(), 10_000);
    },
    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("EarningsAlert: stopped");
    },
  };
}
