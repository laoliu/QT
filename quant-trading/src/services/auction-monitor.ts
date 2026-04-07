/**
 * AuctionMonitor service — monitors call auction phase (集合竞价) data
 * during 9:15-9:25 for CN A-shares.
 *
 * Polls auction data every 10 seconds via market_data quote action.
 * When abnormal signals are detected (volume ratio > 3, price up > 5%,
 * price down > 3%), emits alerts through EventBus.
 *
 * Maintains a watchlist of symbols to monitor and tracks their auction state.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";
import type { EventBus } from "./event-bus.js";

// ── Types ───────────────────────────────────────────────────────

export interface AuctionQuote {
  symbol: string;
  name?: string;
  price: number;
  prevClose: number;
  changePct: number;
  volume: number;
  amount: number;
  /** Volume ratio compared to recent average auction volume */
  volumeRatio?: number;
  /** Bid/ask ratio (委买卖比) — >1 means more buyers */
  bidAskRatio?: number;
  timestamp: number;
}

export interface AuctionAlert {
  symbol: string;
  name?: string;
  type: "volume_spike" | "price_surge" | "price_drop" | "bid_ask_abnormal";
  message: string;
  value: number;
  threshold: number;
  timestamp: number;
}

export interface AuctionMonitorConfig {
  /** Poll interval in ms during auction phase (default: 10_000 = 10s) */
  pollIntervalMs?: number;
  /** Volume ratio threshold for alert (default: 3) */
  volumeRatioThreshold?: number;
  /** Price surge threshold in % (default: 5) */
  priceSurgeThreshold?: number;
  /** Price drop threshold in % (default: 3) */
  priceDropThreshold?: number;
}

// ── Service ─────────────────────────────────────────────────────

export class AuctionMonitorService {
  private watchlist = new Set<string>();
  private latestQuotes = new Map<string, AuctionQuote>();
  private alerts: AuctionAlert[] = [];
  private interval: ReturnType<typeof setInterval> | null = null;
  private readonly pollIntervalMs: number;
  private readonly volumeRatioThreshold: number;
  private readonly priceSurgeThreshold: number;
  private readonly priceDropThreshold: number;

  constructor(
    private readonly api: OpenClawPluginApi,
    private readonly config: QuantTradingConfig,
    private readonly eventBus?: EventBus,
    opts?: AuctionMonitorConfig,
  ) {
    this.pollIntervalMs = opts?.pollIntervalMs ?? 10_000;
    this.volumeRatioThreshold = opts?.volumeRatioThreshold ?? 3;
    this.priceSurgeThreshold = opts?.priceSurgeThreshold ?? 5;
    this.priceDropThreshold = opts?.priceDropThreshold ?? 3;
  }

  // ── Watchlist Management ──────────────────────────────────────

  /** Add symbols to the auction watchlist. */
  addSymbols(symbols: string[]): string[] {
    const added: string[] = [];
    for (const s of symbols) {
      const sym = s.trim();
      if (sym && !this.watchlist.has(sym)) {
        this.watchlist.add(sym);
        added.push(sym);
      }
    }
    return added;
  }

  /** Remove symbols from the auction watchlist. */
  removeSymbols(symbols: string[]): string[] {
    const removed: string[] = [];
    for (const s of symbols) {
      const sym = s.trim();
      if (this.watchlist.delete(sym)) {
        removed.push(sym);
      }
    }
    return removed;
  }

  /** Get all watched symbols. */
  getWatchlist(): string[] {
    return [...this.watchlist];
  }

  // ── Status & Alerts ───────────────────────────────────────────

  /** Get latest auction quotes for all watched symbols. */
  getStatus(): AuctionQuote[] {
    return [...this.latestQuotes.values()];
  }

  /** Get single symbol status. */
  getSymbolStatus(symbol: string): AuctionQuote | null {
    return this.latestQuotes.get(symbol) ?? null;
  }

  /** Get all triggered alerts (most recent first). */
  getAlerts(limit?: number): AuctionAlert[] {
    const sorted = [...this.alerts].sort((a, b) => b.timestamp - a.timestamp);
    return limit ? sorted.slice(0, limit) : sorted;
  }

  /** Clear alerts history. */
  clearAlerts(): void {
    this.alerts = [];
  }

  // ── Auction Time Check ────────────────────────────────────────

  /** Check if current time is within CN A-share call auction phase (9:15-9:25 Beijing time). */
  isAuctionPhase(): boolean {
    const now = new Date();
    // Convert to Beijing time (UTC+8)
    const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
    const beijingTime = new Date(utcMs + 8 * 60 * 60_000);
    const hours = beijingTime.getHours();
    const minutes = beijingTime.getMinutes();
    const day = beijingTime.getDay();

    // Skip weekends
    if (day === 0 || day === 6) return false;

    // 9:15 - 9:25
    if (hours === 9 && minutes >= 15 && minutes < 25) return true;

    return false;
  }

  // ── Polling Logic ─────────────────────────────────────────────

  /** Start the auction monitor polling. */
  start(): void {
    if (this.interval) return;
    this.api.logger.info("[AuctionMonitor] service started");

    const tick = async () => {
      if (!this.isAuctionPhase()) return;
      if (this.watchlist.size === 0) return;

      const provider = createDataProvider(this.config);
      const symbols = [...this.watchlist];

      for (const symbol of symbols) {
        try {
          const market = detectMarketFromSymbol(symbol);
          const quote = await provider.getQuote(symbol, market as never);
          if (!quote) continue;

          const auctionQuote: AuctionQuote = {
            symbol,
            name: quote.symbol?.name,
            price: quote.price,
            prevClose: quote.prevClose,
            changePct: quote.changePct,
            volume: quote.volume,
            amount: quote.amount,
            timestamp: Date.now(),
          };

          // Calculate volume ratio if we have historical data
          const prevQuote = this.latestQuotes.get(symbol);
          if (prevQuote && prevQuote.volume > 0) {
            auctionQuote.volumeRatio = quote.volume / prevQuote.volume;
          }

          this.latestQuotes.set(symbol, auctionQuote);

          // Check alert conditions
          this.checkAlerts(auctionQuote);
        } catch (err) {
          this.api.logger.warn?.(
            `[AuctionMonitor] failed to fetch ${symbol}: ${(err as Error).message}`,
          );
        }
      }
    };

    // Immediate tick then interval
    void tick();
    this.interval = setInterval(() => void tick(), this.pollIntervalMs);
  }

  /** Stop the auction monitor polling. */
  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
    this.api.logger.info("[AuctionMonitor] service stopped");
  }

  /** Destroy the service (clears all state). */
  destroy(): void {
    this.stop();
    this.watchlist.clear();
    this.latestQuotes.clear();
    this.alerts = [];
  }

  // ── Alert Detection ───────────────────────────────────────────

  private checkAlerts(quote: AuctionQuote): void {
    const now = Date.now();

    // Volume ratio spike
    if (quote.volumeRatio != null && quote.volumeRatio > this.volumeRatioThreshold) {
      this.emitAlert({
        symbol: quote.symbol,
        name: quote.name,
        type: "volume_spike",
        message: `🔴 ${quote.symbol}${quote.name ? `(${quote.name})` : ""} 竞价量比 ${quote.volumeRatio.toFixed(1)} 超过阈值 ${this.volumeRatioThreshold}`,
        value: quote.volumeRatio,
        threshold: this.volumeRatioThreshold,
        timestamp: now,
      });
    }

    // Price surge (涨幅 > 5%)
    if (quote.changePct > this.priceSurgeThreshold) {
      this.emitAlert({
        symbol: quote.symbol,
        name: quote.name,
        type: "price_surge",
        message: `🔥 ${quote.symbol}${quote.name ? `(${quote.name})` : ""} 竞价涨幅 +${quote.changePct.toFixed(2)}% 超过 ${this.priceSurgeThreshold}%`,
        value: quote.changePct,
        threshold: this.priceSurgeThreshold,
        timestamp: now,
      });
    }

    // Price drop (跌幅 > 3%)
    if (quote.changePct < -this.priceDropThreshold) {
      this.emitAlert({
        symbol: quote.symbol,
        name: quote.name,
        type: "price_drop",
        message: `💧 ${quote.symbol}${quote.name ? `(${quote.name})` : ""} 竞价跌幅 ${quote.changePct.toFixed(2)}% 超过 -${this.priceDropThreshold}%`,
        value: Math.abs(quote.changePct),
        threshold: this.priceDropThreshold,
        timestamp: now,
      });
    }
  }

  private emitAlert(alert: AuctionAlert): void {
    // Avoid duplicate alerts for the same symbol+type within 30 seconds
    const recentDup = this.alerts.find(
      (a) =>
        a.symbol === alert.symbol &&
        a.type === alert.type &&
        alert.timestamp - a.timestamp < 30_000,
    );
    if (recentDup) return;

    this.alerts.push(alert);
    // Keep last 500 alerts
    if (this.alerts.length > 500) {
      this.alerts = this.alerts.slice(-500);
    }

    this.api.logger.info(`[AuctionMonitor] alert: ${alert.message}`);

    // Emit to EventBus
    this.eventBus?.emit("alert", {
      severity: "warning",
      type: "auction_alert",
      message: alert.message,
      symbol: alert.symbol,
      alertType: alert.type,
      value: alert.value,
      threshold: alert.threshold,
    });
  }
}

// ── Service factory (OpenClawPluginService interface) ────────────

export function createAuctionMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  eventBus?: EventBus,
  opts?: AuctionMonitorConfig,
): { service: OpenClawPluginService; monitor: AuctionMonitorService } {
  const monitor = new AuctionMonitorService(api, config, eventBus, opts);

  const service: OpenClawPluginService = {
    id: "auction-monitor",
    async start() {
      monitor.start();
    },
    async stop() {
      monitor.destroy();
    },
  };

  return { service, monitor };
}
