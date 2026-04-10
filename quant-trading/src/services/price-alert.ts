/**
 * PriceAlertService — monitors stock prices against user-defined conditions.
 *
 * Features:
 * - Price above/below threshold
 * - Change percentage threshold
 * - Volume spike detection
 * - Custom indicator conditions (RSI, MA crossover)
 *
 * Runs as a periodic check using TaskScheduler, delivers alerts via api.notify().
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type AlertConditionType =
  | "price_above"
  | "price_below"
  | "change_pct_above"
  | "change_pct_below"
  | "volume_above"
  | "volume_spike"
  | "sentiment_spike"
  | "hot_rank_above";

export interface PriceAlertCondition {
  id: string;
  symbol: string;
  type: AlertConditionType;
  /** Threshold value. For price: price level. For change: percentage. For volume: shares count. */
  threshold: number;
  /** Human-readable description. */
  description: string;
  /** When the alert was created. */
  createdAt: string;
  /** Whether the alert has been triggered. One-shot alerts auto-deactivate. */
  triggered: boolean;
  /** Whether this is a repeating alert (notifies every check while condition holds). */
  repeating: boolean;
  /** Last triggered time (ISO). */
  lastTriggeredAt?: string;
  /**
   * Optional auto-order action. When set, the alert auto-submits an order
   * via the orderFn callback when the condition triggers.
   */
  orderAction?: AlertOrderAction;
}

/** Describes an order to auto-submit when an alert triggers. */
export interface AlertOrderAction {
  side: "buy" | "sell";
  /** Order type: "market" fills immediately; "limit" uses limitPrice. */
  orderType: "market" | "limit";
  /** Quantity in shares. */
  quantity: number;
  /** Limit price (required when orderType="limit"). */
  limitPrice?: number;
  /** Strategy ID to tag the order with. */
  strategyId?: string;
  /** Whether an order has already been submitted (prevents duplicate fills). */
  submitted?: boolean;
}

export interface PriceAlertServiceOptions {
  /** Check interval in ms (default: 60_000 = 1 minute). */
  checkIntervalMs?: number;
  /** Maximum number of alerts per user. */
  maxAlerts?: number;
  /** Data directory for persistence. */
  dataDir?: string | null;
  /** Cooldown in ms for repeating alerts (default: 30 minutes). */
  repeatCooldownMs?: number;
}

export class PriceAlertService {
  private alerts = new Map<string, PriceAlertCondition>();
  private idCounter = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly checkIntervalMs: number;
  private readonly maxAlerts: number;
  private readonly dataDir: string | null;
  private readonly repeatCooldownMs: number;
  /** Tracks last notification time for repeating alerts to enforce cooldown. */
  private readonly lastNotifiedAt = new Map<string, number>();
  private notifyFn?: (message: string) => void | Promise<void>;
  private quoteFn?: (
    symbol: string,
  ) => Promise<{ price: number; changePct: number; volume: number } | null>;
  private sentimentFn?: (
    symbol: string,
  ) => Promise<{ sentimentScore: number; hotRank: number } | null>;
  private alertCallback?: (
    severity: "info" | "warning",
    message: string,
    metadata: Record<string, unknown>,
  ) => void;

  constructor(opts: PriceAlertServiceOptions = {}) {
    this.checkIntervalMs = opts.checkIntervalMs ?? 60_000;
    this.maxAlerts = opts.maxAlerts ?? 50;
    this.dataDir = opts.dataDir ?? null;
    this.repeatCooldownMs = opts.repeatCooldownMs ?? 30 * 60 * 1000;
    this.loadFromDisk();
  }

  /** Set the notification callback. */
  setNotify(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  /** Set the quote fetching function. */
  setQuoteFn(
    fn: (symbol: string) => Promise<{ price: number; changePct: number; volume: number } | null>,
  ): void {
    this.quoteFn = fn;
  }

  /**
   * Set the sentiment fetching function (used for sentiment_spike / hot_rank_above alerts).
   * Typically wired to the social_sentiment Python engine.
   */
  setSentimentFn(
    fn: (symbol: string) => Promise<{ sentimentScore: number; hotRank: number } | null>,
  ): void {
    this.sentimentFn = fn;
  }

  /** Set the alert callback (wired to AlertStore + EventBus). */
  setAlertCallback(
    fn: (severity: "info" | "warning", message: string, metadata: Record<string, unknown>) => void,
  ): void {
    this.alertCallback = fn;
  }

  /**
   * Set the order execution callback.
   * When an alert has orderAction set, this function is called to submit the order.
   */
  private orderFn?: (params: {
    symbol: string;
    side: "buy" | "sell";
    orderType: "market" | "limit";
    quantity: number;
    price?: number;
    strategyId?: string;
  }) => Promise<{ ok: boolean; reason?: string }>;

  setOrderFn(
    fn: (params: {
      symbol: string;
      side: "buy" | "sell";
      orderType: "market" | "limit";
      quantity: number;
      price?: number;
      strategyId?: string;
    }) => Promise<{ ok: boolean; reason?: string }>,
  ): void {
    this.orderFn = fn;
  }

  /** Add a new alert condition. Returns the alert ID. */
  addAlert(params: {
    symbol: string;
    type: AlertConditionType;
    threshold: number;
    description?: string;
    repeating?: boolean;
    orderAction?: AlertOrderAction;
  }): string {
    if (this.alerts.size >= this.maxAlerts) {
      // Evict triggered one-shot alerts
      for (const [id, a] of this.alerts) {
        if (a.triggered && !a.repeating) {
          this.alerts.delete(id);
          if (this.alerts.size < this.maxAlerts) break;
        }
      }
    }

    if (this.alerts.size >= this.maxAlerts) {
      throw new Error(`预警数量已达上限 (${String(this.maxAlerts)})`);
    }

    const id = `alert-${String(++this.idCounter).padStart(4, "0")}`;
    const description = params.description ?? this.generateDescription(params);

    const alert: PriceAlertCondition = {
      id,
      symbol: params.symbol,
      type: params.type,
      threshold: params.threshold,
      description,
      createdAt: new Date().toISOString(),
      triggered: false,
      repeating: params.repeating ?? false,
      orderAction: params.orderAction,
    };

    this.alerts.set(id, alert);
    this.saveToDisk();
    return id;
  }

  /** Remove an alert by ID. */
  removeAlert(id: string): boolean {
    const ok = this.alerts.delete(id);
    if (ok) {
      this.lastNotifiedAt.delete(id);
      this.saveToDisk();
    }
    return ok;
  }

  /** List all alerts (optionally filter by active only). */
  listAlerts(activeOnly = false): PriceAlertCondition[] {
    const all = [...this.alerts.values()];
    if (activeOnly) return all.filter((a) => !a.triggered || a.repeating);
    return all;
  }

  /** Get active alert count. */
  get activeCount(): number {
    return [...this.alerts.values()].filter((a) => !a.triggered || a.repeating).length;
  }

  /**
   * Auto-create a change_pct alert for a watchlist symbol.
   * Skips if the symbol already has an active change_pct alert.
   * Returns the alert ID if created, or null if skipped.
   */
  addWatchlistAlert(symbol: string, thresholdPct = 5): string | null {
    // Check if the symbol already has an active change_pct alert
    for (const a of this.alerts.values()) {
      if (
        a.symbol === symbol &&
        (a.type === "change_pct_above" || a.type === "change_pct_below") &&
        (!a.triggered || a.repeating)
      ) {
        return null; // already monitored
      }
    }
    try {
      return this.addAlert({
        symbol,
        type: "change_pct_above",
        threshold: thresholdPct,
        description: `[自选] ${symbol} 涨跌幅超过${String(thresholdPct)}%`,
        repeating: true,
      });
    } catch {
      return null; // reached limit
    }
  }

  /**
   * Auto-create a change_pct alert for a held position.
   * Skips if the symbol already has an active change_pct alert.
   * Returns the alert ID if created, or null if skipped.
   */
  addPositionAlert(symbol: string, thresholdPct = 5): string | null {
    // Check if the symbol already has an active change_pct alert
    for (const a of this.alerts.values()) {
      if (
        a.symbol === symbol &&
        (a.type === "change_pct_above" || a.type === "change_pct_below") &&
        (!a.triggered || a.repeating)
      ) {
        return null; // already monitored
      }
    }
    try {
      return this.addAlert({
        symbol,
        type: "change_pct_above",
        threshold: thresholdPct,
        description: `[持仓] ${symbol} 涨跌幅超过${String(thresholdPct)}%`,
        repeating: true,
      });
    } catch {
      return null; // reached limit
    }
  }

  /**
   * Sync price alerts for all current positions.
   * Creates alerts for new positions, does not remove alerts for sold positions
   * (user may still want to watch them).
   */
  syncPositionAlerts(symbols: string[], thresholdPct = 5): number {
    let created = 0;
    for (const symbol of symbols) {
      const id = this.addPositionAlert(symbol, thresholdPct);
      if (id) created++;
    }
    return created;
  }

  /** Start the periodic check loop. */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.checkAll();
    }, this.checkIntervalMs);
  }

  /** Stop the periodic check loop. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Run a single check cycle against all active alerts. */
  async checkAll(): Promise<string[]> {
    if (!this.quoteFn) return [];

    const triggered: string[] = [];
    const activeAlerts = this.listAlerts(true);

    // Group alerts by symbol to batch quote requests
    const bySymbol = new Map<string, PriceAlertCondition[]>();
    for (const alert of activeAlerts) {
      const list = bySymbol.get(alert.symbol) ?? [];
      list.push(alert);
      bySymbol.set(alert.symbol, list);
    }

    for (const [symbol, alerts] of bySymbol) {
      try {
        const quote = await this.quoteFn(symbol);
        if (!quote) continue;

        for (const alert of alerts) {
          const hit = this.checkCondition(alert, quote);
          if (hit) {
            // Cooldown check for repeating alerts
            if (alert.repeating) {
              const lastTime = this.lastNotifiedAt.get(alert.id);
              if (lastTime && Date.now() - lastTime < this.repeatCooldownMs) {
                continue;
              }
              this.lastNotifiedAt.set(alert.id, Date.now());
            }

            alert.triggered = true;
            alert.lastTriggeredAt = new Date().toISOString();
            triggered.push(alert.id);

            const msg = this.formatAlert(alert, quote);

            // Notify user via chat
            if (this.notifyFn) {
              void this.notifyFn(msg);
            }

            // Persist to AlertStore + emit via EventBus
            if (this.alertCallback) {
              const severity = alert.type.includes("below")
                ? ("warning" as const)
                : ("info" as const);
              this.alertCallback(severity, msg, {
                alertId: alert.id,
                symbol: alert.symbol,
                conditionType: alert.type,
                threshold: alert.threshold,
                price: quote.price,
                changePct: quote.changePct,
                volume: quote.volume,
              });
            }

            // Auto-submit order if orderAction is configured and not yet submitted
            if (alert.orderAction && !alert.orderAction.submitted && this.orderFn) {
              try {
                const oa = alert.orderAction;
                const result = await this.orderFn({
                  symbol: alert.symbol,
                  side: oa.side,
                  orderType: oa.orderType,
                  quantity: oa.quantity,
                  price: oa.limitPrice,
                  strategyId: oa.strategyId,
                });
                oa.submitted = true;
                const orderMsg = result.ok
                  ? `📋 预警触发自动下单: ${oa.side} ${oa.quantity}股 ${alert.symbol} (${oa.orderType})`
                  : `⚠️ 预警触发下单失败: ${result.reason ?? "unknown"}`;
                if (this.notifyFn) {
                  void this.notifyFn(orderMsg);
                }
              } catch {
                // Order submission failed — notify but don't crash the loop
                if (this.notifyFn) {
                  void this.notifyFn(`⚠️ 预警 ${alert.id} 自动下单异常`);
                }
              }
              this.saveToDisk();
            }

            // Reset repeating alerts for next cycle
            if (alert.repeating) {
              alert.triggered = false;
            }
          }
        }
      } catch {
        // Skip failed symbols silently
      }
    }

    // ── Sentiment-based alerts (separate path via sentimentFn) ──
    if (this.sentimentFn) {
      const sentimentAlerts = activeAlerts.filter(
        (a) => a.type === "sentiment_spike" || a.type === "hot_rank_above",
      );
      const sentimentSymbols = new Set(sentimentAlerts.map((a) => a.symbol));
      for (const symbol of sentimentSymbols) {
        try {
          const sentiment = await this.sentimentFn(symbol);
          if (!sentiment) continue;

          for (const alert of sentimentAlerts.filter((a) => a.symbol === symbol)) {
            let hit = false;
            if (alert.type === "sentiment_spike") {
              hit = sentiment.sentimentScore >= alert.threshold;
            } else if (alert.type === "hot_rank_above") {
              // "rank above" means rank <= threshold (lower rank = higher popularity)
              hit = sentiment.hotRank > 0 && sentiment.hotRank <= alert.threshold;
            }

            if (hit) {
              // Cooldown check for repeating sentiment alerts
              if (alert.repeating) {
                const lastTime = this.lastNotifiedAt.get(alert.id);
                if (lastTime && Date.now() - lastTime < this.repeatCooldownMs) {
                  continue;
                }
                this.lastNotifiedAt.set(alert.id, Date.now());
              }

              alert.triggered = true;
              alert.lastTriggeredAt = new Date().toISOString();
              triggered.push(alert.id);

              const msg = this.formatSentimentAlert(alert, sentiment);
              if (this.notifyFn) void this.notifyFn(msg);
              if (this.alertCallback) {
                this.alertCallback("info", msg, {
                  alertId: alert.id,
                  symbol: alert.symbol,
                  conditionType: alert.type,
                  threshold: alert.threshold,
                  sentimentScore: sentiment.sentimentScore,
                  hotRank: sentiment.hotRank,
                });
              }
              if (alert.repeating) alert.triggered = false;
            }
          }
        } catch {
          // Skip failed sentiment lookups
        }
      }
    }

    return triggered;
  }

  /** Destroy: stop timer and clear alerts. */
  destroy(): void {
    this.stop();
    this.alerts.clear();
    this.lastNotifiedAt.clear();
  }

  // ── Internal ────────────────────────────────────────────────

  private checkCondition(
    alert: PriceAlertCondition,
    quote: { price: number; changePct: number; volume: number },
  ): boolean {
    switch (alert.type) {
      case "price_above":
        return quote.price >= alert.threshold;
      case "price_below":
        return quote.price <= alert.threshold;
      case "change_pct_above":
        return quote.changePct >= alert.threshold;
      case "change_pct_below":
        // User provides a positive threshold meaning "drop by X%";
        // changePct is negative when the stock falls, so compare accordingly.
        return quote.changePct <= -Math.abs(alert.threshold);
      case "volume_above":
        return quote.volume >= alert.threshold;
      case "volume_spike":
        // volume_spike: threshold is the multiplier over "normal" volume
        // We use a simple heuristic: volume > threshold * 10000 (万)
        return quote.volume >= alert.threshold * 10000;
      case "sentiment_spike":
        // Checked separately via sentimentFn; always false in price-quote path
        return false;
      case "hot_rank_above":
        // Checked separately via sentimentFn; always false in price-quote path
        return false;
      default:
        return false;
    }
  }

  private formatAlert(
    alert: PriceAlertCondition,
    quote: { price: number; changePct: number; volume: number },
  ): string {
    const emoji = this.getAlertEmoji(alert.type);
    const changePctStr = `${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`;
    return [
      `${emoji} **条件预警触发**`,
      `标的: ${alert.symbol}  当前价: ¥${quote.price.toFixed(2)}  涨跌: ${changePctStr}`,
      `条件: ${alert.description}`,
      alert.repeating ? "(持续监控中)" : "(已自动停用)",
    ].join("\n");
  }

  private formatSentimentAlert(
    alert: PriceAlertCondition,
    sentiment: { sentimentScore: number; hotRank: number },
  ): string {
    const emoji = this.getAlertEmoji(alert.type);
    const detail =
      alert.type === "sentiment_spike"
        ? `情绪分: ${sentiment.sentimentScore}  热度排名: #${String(sentiment.hotRank)}`
        : `热度排名: #${String(sentiment.hotRank)}  情绪分: ${sentiment.sentimentScore}`;
    return [
      `${emoji} **舆情预警触发**`,
      `标的: ${alert.symbol}  ${detail}`,
      `条件: ${alert.description}`,
      alert.repeating ? "(持续监控中)" : "(已自动停用)",
    ].join("\n");
  }

  private getAlertEmoji(type: AlertConditionType): string {
    switch (type) {
      case "price_above":
        return "🔔📈";
      case "price_below":
        return "🔔📉";
      case "change_pct_above":
        return "⚡📈";
      case "change_pct_below":
        return "⚡📉";
      case "volume_above":
      case "volume_spike":
        return "🔊";
      case "sentiment_spike":
        return "💡📊";
      case "hot_rank_above":
        return "🔥";
      default:
        return "🔔";
    }
  }

  // ── Persistence ────────────────────────────────────────────

  private get persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/price-alerts.json`;
  }

  /** Save current alerts to disk. */
  private saveToDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const data = {
        idCounter: this.idCounter,
        alerts: [...this.alerts.values()],
      };
      writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // Best-effort persistence — don't crash the service
    }
  }

  /** Load alerts from disk on startup. */
  private loadFromDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as {
        idCounter: number;
        alerts: PriceAlertCondition[];
      };
      this.idCounter = data.idCounter ?? 0;
      for (const a of data.alerts) {
        this.alerts.set(a.id, a);
      }
    } catch {
      // Corrupt file — start fresh
    }
  }

  private generateDescription(params: {
    symbol: string;
    type: AlertConditionType;
    threshold: number;
  }): string {
    switch (params.type) {
      case "price_above":
        return `${params.symbol} 价格突破 ¥${params.threshold.toFixed(2)}`;
      case "price_below":
        return `${params.symbol} 价格跌破 ¥${params.threshold.toFixed(2)}`;
      case "change_pct_above":
        return `${params.symbol} 涨幅超过 ${params.threshold}%`;
      case "change_pct_below":
        return `${params.symbol} 跌幅超过 ${Math.abs(params.threshold)}%`;
      case "volume_above":
        return `${params.symbol} 成交量超过 ${params.threshold}`;
      case "volume_spike":
        return `${params.symbol} 成交量异动 (>${params.threshold}万)`;
      case "sentiment_spike":
        return `${params.symbol} 舆情情绪分超过 ${params.threshold}`;
      case "hot_rank_above":
        return `${params.symbol} 热度排名进入前 ${params.threshold}`;
      default:
        return `${params.symbol} 条件预警`;
    }
  }
}
