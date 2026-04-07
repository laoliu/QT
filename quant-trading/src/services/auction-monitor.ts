/**
 * AuctionMonitor — Call auction (集合竞价) anomaly detection service.
 *
 * Monitors the call auction phase (09:15-09:25) for A-share markets and
 * detects anomaly patterns:
 *   1. High open > +3% (大幅高开)
 *   2. Low open < -3% (大幅低开)
 *   3. Abnormal auction volume (竞价量异常放大)
 *   4. Late withdrawal anomaly (尾盘撤单异动, 09:20-09:25)
 *
 * Automatically monitors:
 *   - Held positions (持仓股)
 *   - Watchlist / favorites (自选股)
 *
 * Results are delivered via the notify callback to the user's active chat.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

// ── Types ────────────────────────────────────────────────────────

export interface AuctionQuote {
  symbol: string;
  name?: string;
  /** Current auction indicative price */
  price: number;
  /** Previous close price */
  prevClose: number;
  /** Auction indicative volume (cumulative) */
  volume: number;
  /** Change percentage vs previous close */
  changePct: number;
}

export type AuctionAnomalyType =
  | "high_open"       // 大幅高开 > +3%
  | "low_open"        // 大幅低开 < -3%
  | "volume_surge"    // 竞价量异常放大
  | "late_withdrawal"; // 尾盘撤单异动 (09:20-09:25 volume drop)

export interface AuctionAnomaly {
  symbol: string;
  name?: string;
  type: AuctionAnomalyType;
  /** Auction indicative price */
  price: number;
  prevClose: number;
  changePct: number;
  volume: number;
  /** Human-readable description */
  description: string;
  /** ISO timestamp when detected */
  detectedAt: string;
}

export interface AuctionMonitorResult {
  /** Monitoring session date (YYYY-MM-DD) */
  date: string;
  /** All detected anomalies */
  anomalies: AuctionAnomaly[];
  /** Symbols that were monitored */
  monitoredSymbols: string[];
  /** Start time of monitoring */
  startedAt: string;
  /** End time of monitoring */
  endedAt?: string;
}

export interface AuctionMonitorOptions {
  /** Polling interval during auction phase in ms (default: 5000 = 5s) */
  pollIntervalMs?: number;
  /** High open threshold percentage (default: 3) */
  highOpenPct?: number;
  /** Low open threshold percentage (default: -3) */
  lowOpenPct?: number;
  /** Volume surge ratio vs average (default: 3.0 = 3x average) */
  volumeSurgeRatio?: number;
  /** Volume drop ratio for late withdrawal detection (default: 0.5 = 50% drop) */
  lateWithdrawalDropRatio?: number;
  /** Data directory for persistence */
  dataDir?: string | null;
}

// ── AuctionMonitor ──────────────────────────────────────────────

export class AuctionMonitor {
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly pollIntervalMs: number;
  private readonly highOpenPct: number;
  private readonly lowOpenPct: number;
  private readonly volumeSurgeRatio: number;
  private readonly lateWithdrawalDropRatio: number;
  private readonly dataDir: string | null;

  /** Volume snapshots per symbol during current session (for withdrawal detection) */
  private volumeSnapshots = new Map<string, Array<{ timestamp: number; volume: number }>>();
  /** Average daily auction volume per symbol (loaded from history) */
  private avgAuctionVolumes = new Map<string, number>();
  /** Anomalies detected in current session */
  private currentAnomalies: AuctionAnomaly[] = [];
  /** Symbols already alerted this session (avoid duplicate notifications) */
  private alertedKeys = new Set<string>();
  /** Current session start time */
  private sessionStartedAt: string | null = null;

  // ── Callbacks ────────────────────────────────────────────────

  /** Fetch auction quote for a symbol */
  private quoteFn?: (symbol: string) => Promise<AuctionQuote | null>;
  /** Get list of held position symbols */
  private positionSymbolsFn?: () => Promise<string[]>;
  /** Get list of watchlist/favorites symbols */
  private favoritesSymbolsFn?: () => string[];
  /** Notification callback */
  private notifyFn?: (message: string) => void | Promise<void>;

  constructor(opts: AuctionMonitorOptions = {}) {
    this.pollIntervalMs = opts.pollIntervalMs ?? 5_000;
    this.highOpenPct = opts.highOpenPct ?? 3;
    this.lowOpenPct = opts.lowOpenPct ?? -3;
    this.volumeSurgeRatio = opts.volumeSurgeRatio ?? 3.0;
    this.lateWithdrawalDropRatio = opts.lateWithdrawalDropRatio ?? 0.5;
    this.dataDir = opts.dataDir ?? null;
    this.loadAvgVolumes();
  }

  // ── Callback setters ─────────────────────────────────────────

  setQuoteFn(fn: (symbol: string) => Promise<AuctionQuote | null>): void {
    this.quoteFn = fn;
  }

  setPositionSymbolsFn(fn: () => Promise<string[]>): void {
    this.positionSymbolsFn = fn;
  }

  setFavoritesSymbolsFn(fn: () => string[]): void {
    this.favoritesSymbolsFn = fn;
  }

  setNotifyFn(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  // ── Start / Stop ─────────────────────────────────────────────

  /**
   * Start monitoring. Called at 09:15 by the task scheduler.
   * Polls auction data every `pollIntervalMs` until 09:25.
   */
  start(): void {
    if (this.timer) return;

    // Reset session state
    this.volumeSnapshots.clear();
    this.currentAnomalies = [];
    this.alertedKeys.clear();
    this.sessionStartedAt = new Date().toISOString();

    void this.notify("🔔 集合竞价监控已启动 (09:15-09:25)");

    // Immediately run first check
    void this.pollOnce();

    // Schedule periodic polling
    this.timer = setInterval(() => {
      // Auto-stop after 09:25 (Shanghai time)
      if (this.isAfterAuctionEnd()) {
        this.stop();
        return;
      }
      void this.pollOnce();
    }, this.pollIntervalMs);
  }

  /** Stop monitoring and generate summary. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }

    // Save volume data for future reference
    this.saveSessionData();

    // Generate and send summary
    void this.sendSummary();
  }

  /** Clean up resources. */
  destroy(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.volumeSnapshots.clear();
    this.currentAnomalies = [];
    this.alertedKeys.clear();
  }

  /** Whether the monitor is currently running. */
  get isRunning(): boolean {
    return this.timer !== null;
  }

  /** Get anomalies detected in the current/last session. */
  getAnomalies(): AuctionAnomaly[] {
    return [...this.currentAnomalies];
  }

  // ── Core polling ─────────────────────────────────────────────

  async pollOnce(): Promise<AuctionAnomaly[]> {
    if (!this.quoteFn) return [];

    const symbols = await this.collectMonitoredSymbols();
    if (symbols.length === 0) return [];

    const newAnomalies: AuctionAnomaly[] = [];

    for (const symbol of symbols) {
      try {
        const quote = await this.quoteFn(symbol);
        if (!quote) continue;

        // Record volume snapshot for withdrawal detection
        this.recordVolumeSnapshot(symbol, quote.volume);

        // Detect anomalies
        const anomalies = this.detectAnomalies(quote);
        for (const anomaly of anomalies) {
          const key = `${anomaly.symbol}:${anomaly.type}`;
          if (!this.alertedKeys.has(key)) {
            this.alertedKeys.add(key);
            this.currentAnomalies.push(anomaly);
            newAnomalies.push(anomaly);
            void this.notifyAnomaly(anomaly);
          }
        }
      } catch {
        // Skip failed symbols
      }
    }

    return newAnomalies;
  }

  // ── Anomaly detection ────────────────────────────────────────

  private detectAnomalies(quote: AuctionQuote): AuctionAnomaly[] {
    const anomalies: AuctionAnomaly[] = [];
    const now = new Date().toISOString();
    const base = {
      symbol: quote.symbol,
      name: quote.name,
      price: quote.price,
      prevClose: quote.prevClose,
      changePct: quote.changePct,
      volume: quote.volume,
      detectedAt: now,
    };

    // 1. High open detection (大幅高开)
    if (quote.changePct >= this.highOpenPct) {
      anomalies.push({
        ...base,
        type: "high_open",
        description: `${quote.name ?? quote.symbol} 竞价高开 ${quote.changePct.toFixed(2)}% (¥${quote.price.toFixed(2)})`,
      });
    }

    // 2. Low open detection (大幅低开)
    if (quote.changePct <= this.lowOpenPct) {
      anomalies.push({
        ...base,
        type: "low_open",
        description: `${quote.name ?? quote.symbol} 竞价低开 ${quote.changePct.toFixed(2)}% (¥${quote.price.toFixed(2)})`,
      });
    }

    // 3. Volume surge detection (竞价量异常放大)
    const avgVol = this.avgAuctionVolumes.get(quote.symbol);
    if (avgVol && avgVol > 0 && quote.volume > avgVol * this.volumeSurgeRatio) {
      const ratio = (quote.volume / avgVol).toFixed(1);
      anomalies.push({
        ...base,
        type: "volume_surge",
        description: `${quote.name ?? quote.symbol} 竞价量异常放大 ${ratio}倍 (${this.formatVolume(quote.volume)})`,
      });
    }

    // 4. Late withdrawal detection (尾盘撤单异动, 09:20-09:25)
    if (this.isInLateAuctionPhase()) {
      const withdrawal = this.detectLateWithdrawal(quote.symbol);
      if (withdrawal) {
        anomalies.push({
          ...base,
          type: "late_withdrawal",
          description: `${quote.name ?? quote.symbol} 竞价尾段撤单异动，量能大幅萎缩 ${withdrawal.dropPct.toFixed(0)}%`,
        });
      }
    }

    return anomalies;
  }

  // ── Volume tracking for withdrawal detection ─────────────────

  private recordVolumeSnapshot(symbol: string, volume: number): void {
    const snapshots = this.volumeSnapshots.get(symbol) ?? [];
    snapshots.push({ timestamp: Date.now(), volume });
    // Keep last 60 snapshots (~5 minutes at 5s intervals)
    if (snapshots.length > 60) snapshots.splice(0, snapshots.length - 60);
    this.volumeSnapshots.set(symbol, snapshots);
  }

  private detectLateWithdrawal(
    symbol: string,
  ): { dropPct: number } | null {
    const snapshots = this.volumeSnapshots.get(symbol);
    if (!snapshots || snapshots.length < 4) return null;

    // Compare recent volume to peak volume
    const peakVolume = Math.max(...snapshots.map((s) => s.volume));
    const latestVolume = snapshots[snapshots.length - 1]!.volume;

    if (peakVolume <= 0) return null;

    // Check if volume dropped significantly from peak (indicates withdrawal)
    const dropRatio = (peakVolume - latestVolume) / peakVolume;
    if (dropRatio >= this.lateWithdrawalDropRatio) {
      return { dropPct: dropRatio * 100 };
    }

    return null;
  }

  // ── Symbol collection ────────────────────────────────────────

  private async collectMonitoredSymbols(): Promise<string[]> {
    const symbolSet = new Set<string>();

    // Add position symbols
    if (this.positionSymbolsFn) {
      try {
        const positions = await this.positionSymbolsFn();
        for (const s of positions) symbolSet.add(s);
      } catch {
        // best-effort
      }
    }

    // Add watchlist/favorites symbols
    if (this.favoritesSymbolsFn) {
      try {
        const favorites = this.favoritesSymbolsFn();
        for (const s of favorites) symbolSet.add(s);
      } catch {
        // best-effort
      }
    }

    return [...symbolSet];
  }

  // ── Time helpers (Shanghai timezone) ─────────────────────────

  private getShanghaiTime(d: Date = new Date()): { hour: number; minute: number } {
    const hour = Number(
      d.toLocaleString("en-US", { hour: "numeric", hour12: false, timeZone: "Asia/Shanghai" }),
    );
    const minute = Number(
      d.toLocaleString("en-US", { minute: "numeric", timeZone: "Asia/Shanghai" }),
    );
    return { hour, minute };
  }

  private isAfterAuctionEnd(): boolean {
    const { hour, minute } = this.getShanghaiTime();
    // After 09:25
    return hour > 9 || (hour === 9 && minute >= 26);
  }

  private isInLateAuctionPhase(): boolean {
    const { hour, minute } = this.getShanghaiTime();
    // 09:20 - 09:25 (no cancellation allowed in A-share)
    return hour === 9 && minute >= 20 && minute <= 25;
  }

  // ── Notification ─────────────────────────────────────────────

  private async notifyAnomaly(anomaly: AuctionAnomaly): Promise<void> {
    const emoji = {
      high_open: "🔴",
      low_open: "🟢",
      volume_surge: "📊",
      late_withdrawal: "⚠️",
    }[anomaly.type];

    const msg = `${emoji} 竞价异动: ${anomaly.description}`;
    await this.notify(msg);
  }

  private async sendSummary(): Promise<void> {
    if (this.currentAnomalies.length === 0) {
      await this.notify("✅ 集合竞价监控结束，未发现明显异动");
      return;
    }

    const lines: string[] = ["📋 **集合竞价异动汇总**\n"];

    // Group by anomaly type
    const byType = new Map<AuctionAnomalyType, AuctionAnomaly[]>();
    for (const a of this.currentAnomalies) {
      const list = byType.get(a.type) ?? [];
      list.push(a);
      byType.set(a.type, list);
    }

    const typeLabels: Record<AuctionAnomalyType, string> = {
      high_open: "大幅高开 (>+3%)",
      low_open: "大幅低开 (<-3%)",
      volume_surge: "竞价量异常放大",
      late_withdrawal: "尾盘撤单异动",
    };

    for (const [type, anomalies] of byType) {
      lines.push(`**${typeLabels[type]}**:`);
      for (const a of anomalies) {
        lines.push(`  • ${a.description}`);
      }
      lines.push("");
    }

    lines.push(`共 ${this.currentAnomalies.length} 条异动信号`);
    await this.notify(lines.join("\n"));
  }

  private async notify(message: string): Promise<void> {
    if (this.notifyFn) {
      try {
        await this.notifyFn(message);
      } catch {
        // best-effort
      }
    }
  }

  // ── Formatting ───────────────────────────────────────────────

  private formatVolume(vol: number): string {
    if (vol >= 1_0000_0000) return `${(vol / 1_0000_0000).toFixed(2)}亿`;
    if (vol >= 1_0000) return `${(vol / 1_0000).toFixed(0)}万`;
    return String(vol);
  }

  // ── Persistence ──────────────────────────────────────────────

  private get avgVolumesPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/auction-avg-volumes.json`;
  }

  private get sessionHistoryPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/auction-history.json`;
  }

  private loadAvgVolumes(): void {
    const path = this.avgVolumesPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as Record<string, number>;
      for (const [symbol, vol] of Object.entries(data)) {
        this.avgAuctionVolumes.set(symbol, vol);
      }
    } catch {
      // start fresh
    }
  }

  private saveAvgVolumes(): void {
    const path = this.avgVolumesPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const data: Record<string, number> = {};
      for (const [symbol, vol] of this.avgAuctionVolumes) {
        data[symbol] = vol;
      }
      writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // best-effort
    }
  }

  /** Save session data and update rolling average volumes. */
  private saveSessionData(): void {
    // Update rolling average volumes from this session
    for (const [symbol, snapshots] of this.volumeSnapshots) {
      if (snapshots.length === 0) continue;
      const sessionMaxVolume = Math.max(...snapshots.map((s) => s.volume));
      const currentAvg = this.avgAuctionVolumes.get(symbol) ?? 0;
      // Exponential moving average with alpha = 0.3
      const newAvg = currentAvg > 0 ? currentAvg * 0.7 + sessionMaxVolume * 0.3 : sessionMaxVolume;
      this.avgAuctionVolumes.set(symbol, newAvg);
    }
    this.saveAvgVolumes();

    // Save session history
    const histPath = this.sessionHistoryPath;
    if (!histPath) return;
    try {
      const dir = dirname(histPath);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });

      let history: AuctionMonitorResult[] = [];
      try {
        if (existsSync(histPath)) {
          history = JSON.parse(readFileSync(histPath, "utf-8")) as AuctionMonitorResult[];
        }
      } catch {
        // start fresh
      }

      const today = new Date().toISOString().slice(0, 10);
      const symbols = [...this.volumeSnapshots.keys()];

      history.push({
        date: today,
        anomalies: this.currentAnomalies,
        monitoredSymbols: symbols,
        startedAt: this.sessionStartedAt ?? new Date().toISOString(),
        endedAt: new Date().toISOString(),
      });

      // Keep last 30 sessions
      if (history.length > 30) history = history.slice(-30);
      writeFileSync(histPath, JSON.stringify(history, null, 2), "utf-8");
    } catch {
      // best-effort
    }
  }
}
