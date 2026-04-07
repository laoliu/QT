/**
 * AuctionMonitor — 集合竞价异动监控服务
 *
 * 监控 09:15-09:25 集合竞价时段，检测自选股和持仓股的异动信号：
 *   1. 高开 > 3%
 *   2. 低开 > 3%（即跌幅 > 3%）
 *   3. 竞价量异常放大（> 5日均量的50%）
 *   4. 封单变化（涨停/跌停封单出现或消失）
 *
 * 交易日 09:15 自动启动，09:25 自动停止，每 30 秒轮询一次。
 * 通过 notifyFn 推送异动提醒。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

// ── Types ───────────────────────────────────────────────────────

export interface AuctionQuote {
  symbol: string;
  name?: string;
  /** 当前竞价价格 */
  price: number;
  /** 前收盘价 */
  prevClose: number;
  /** 竞价成交量（手） */
  volume: number;
  /** 涨跌幅百分比 */
  changePct: number;
  /** 买一价 */
  bid1?: number;
  /** 卖一价 */
  ask1?: number;
  /** 买一量（封单量参考） */
  bidVolume1?: number;
  /** 卖一量（封单量参考） */
  askVolume1?: number;
}

export type AuctionAlertType =
  | "high_open"       // 高开 > 3%
  | "low_open"        // 低开 > 3%
  | "volume_surge"    // 竞价量异常放大
  | "limit_up_bid"    // 涨停封单出现
  | "limit_down_bid"  // 跌停封单出现
  | "limit_bid_gone"; // 封单消失

export interface AuctionAlert {
  id: string;
  symbol: string;
  name?: string;
  type: AuctionAlertType;
  message: string;
  /** 触发时的价格 */
  price: number;
  /** 涨跌幅 */
  changePct: number;
  /** 竞价量 */
  volume: number;
  /** 触发时间 ISO */
  triggeredAt: string;
}

export interface AuctionMonitorOptions {
  /** 轮询间隔，默认 30 秒 */
  checkIntervalMs?: number;
  /** 高开/低开阈值百分比，默认 3 */
  gapThresholdPct?: number;
  /** 竞价量异常倍数（相对于5日均量的比例），默认 0.5（50%） */
  volumeSurgeRatio?: number;
  /** 数据目录（持久化历史均量数据） */
  dataDir?: string | null;
}

// ── AuctionMonitor ──────────────────────────────────────────────

export class AuctionMonitor {
  private timer: ReturnType<typeof setInterval> | null = null;
  private schedulerTimer: ReturnType<typeof setInterval> | null = null;
  private readonly checkIntervalMs: number;
  private readonly gapThresholdPct: number;
  private readonly volumeSurgeRatio: number;
  private readonly dataDir: string | null;

  /** 今日已发送的告警（避免同一信号重复推送） */
  private sentAlerts = new Set<string>();
  /** 上一轮的封单状态（用于检测封单消失） */
  private lastLimitState = new Map<string, "limit_up" | "limit_down" | "none">();
  /** 今日告警记录 */
  private todayAlerts: AuctionAlert[] = [];
  private idCounter = 0;

  // ── 5日均量缓存 ──
  private avgVolume5d = new Map<string, number>();

  // ── Callbacks ──
  private quoteFn?: (symbols: string[]) => Promise<AuctionQuote[]>;
  private watchlistFn?: () => string[];
  private positionsFn?: () => string[];
  private notifyFn?: (message: string) => void | Promise<void>;
  private avgVolumeFn?: (symbol: string) => Promise<number | null>;
  private alertCallback?: (
    severity: "info" | "warning",
    message: string,
    metadata: Record<string, unknown>,
  ) => void;

  constructor(opts: AuctionMonitorOptions = {}) {
    this.checkIntervalMs = opts.checkIntervalMs ?? 30_000;
    this.gapThresholdPct = opts.gapThresholdPct ?? 3;
    this.volumeSurgeRatio = opts.volumeSurgeRatio ?? 0.5;
    this.dataDir = opts.dataDir ?? null;
    this.loadAvgVolumes();
  }

  // ── Setter wiring ─────────────────────────────────────────────

  /** 设置批量行情获取函数 */
  setQuoteFn(fn: (symbols: string[]) => Promise<AuctionQuote[]>): void {
    this.quoteFn = fn;
  }

  /** 设置自选股列表获取函数 */
  setWatchlistFn(fn: () => string[]): void {
    this.watchlistFn = fn;
  }

  /** 设置持仓股列表获取函数 */
  setPositionsFn(fn: () => string[]): void {
    this.positionsFn = fn;
  }

  /** 设置通知回调 */
  setNotifyFn(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  /** 设置5日均量获取函数 */
  setAvgVolumeFn(fn: (symbol: string) => Promise<number | null>): void {
    this.avgVolumeFn = fn;
  }

  /** 设置告警回调（接入 AlertStore + EventBus） */
  setAlertCallback(
    fn: (severity: "info" | "warning", message: string, metadata: Record<string, unknown>) => void,
  ): void {
    this.alertCallback = fn;
  }

  // ── Lifecycle ─────────────────────────────────────────────────

  /**
   * 启动调度器：每分钟检查是否在集合竞价时段，自动开启/关闭监控。
   */
  startScheduler(): void {
    if (this.schedulerTimer) return;

    // 立即检查一次
    this.checkAndToggle();

    // 每 30 秒检查是否需要开启/关闭
    this.schedulerTimer = setInterval(() => {
      this.checkAndToggle();
    }, 30_000);
  }

  /** 停止调度器和监控 */
  stopScheduler(): void {
    if (this.schedulerTimer) {
      clearInterval(this.schedulerTimer);
      this.schedulerTimer = null;
    }
    this.stopMonitoring();
  }

  /** 获取今日告警列表 */
  getTodayAlerts(): AuctionAlert[] {
    return [...this.todayAlerts];
  }

  /** 销毁所有定时器和状态 */
  destroy(): void {
    this.stopScheduler();
    this.sentAlerts.clear();
    this.lastLimitState.clear();
    this.todayAlerts = [];
    this.avgVolume5d.clear();
  }

  // ── Core check loop ───────────────────────────────────────────

  /** 执行一轮竞价异动检查 */
  async checkOnce(): Promise<AuctionAlert[]> {
    if (!this.quoteFn) return [];

    // 收集监控标的：自选股 + 持仓股（去重）
    const symbols = this.getMonitorSymbols();
    if (symbols.length === 0) return [];

    // 确保均量数据已加载
    await this.ensureAvgVolumes(symbols);

    // 批量获取行情
    let quotes: AuctionQuote[];
    try {
      quotes = await this.quoteFn(symbols);
    } catch {
      return [];
    }

    const alerts: AuctionAlert[] = [];

    for (const quote of quotes) {
      if (!quote.price || !quote.prevClose) continue;

      const changePct = ((quote.price - quote.prevClose) / quote.prevClose) * 100;

      // 1. 高开检测
      if (changePct >= this.gapThresholdPct) {
        const alert = this.createAlert(quote, "high_open", changePct,
          `${quote.name ?? quote.symbol} 集合竞价高开 ${changePct.toFixed(2)}%，竞价价 ¥${quote.price.toFixed(2)}`);
        if (alert) alerts.push(alert);
      }

      // 2. 低开检测
      if (changePct <= -this.gapThresholdPct) {
        const alert = this.createAlert(quote, "low_open", changePct,
          `${quote.name ?? quote.symbol} 集合竞价低开 ${changePct.toFixed(2)}%，竞价价 ¥${quote.price.toFixed(2)}`);
        if (alert) alerts.push(alert);
      }

      // 3. 竞价量异常放大
      const avgVol = this.avgVolume5d.get(quote.symbol);
      if (avgVol && avgVol > 0 && quote.volume > 0) {
        const ratio = quote.volume / avgVol;
        if (ratio >= this.volumeSurgeRatio) {
          const alert = this.createAlert(quote, "volume_surge", changePct,
            `${quote.name ?? quote.symbol} 竞价量异常放大，当前 ${this.formatVolume(quote.volume)}，为5日均量的 ${(ratio * 100).toFixed(0)}%`);
          if (alert) alerts.push(alert);
        }
      }

      // 4. 封单变化检测
      const limitAlerts = this.checkLimitBidChange(quote, changePct);
      alerts.push(...limitAlerts);
    }

    // 推送告警
    for (const alert of alerts) {
      this.todayAlerts.push(alert);
      await this.emitAlert(alert);
    }

    return alerts;
  }

  // ── Internal ──────────────────────────────────────────────────

  private getMonitorSymbols(): string[] {
    const symbolSet = new Set<string>();

    const watchlist = this.watchlistFn?.() ?? [];
    for (const s of watchlist) symbolSet.add(s);

    const positions = this.positionsFn?.() ?? [];
    for (const s of positions) symbolSet.add(s);

    return Array.from(symbolSet);
  }

  private createAlert(
    quote: AuctionQuote,
    type: AuctionAlertType,
    changePct: number,
    message: string,
  ): AuctionAlert | null {
    const key = `${quote.symbol}:${type}`;
    if (this.sentAlerts.has(key)) return null;
    this.sentAlerts.add(key);

    const id = `auction-${String(++this.idCounter).padStart(4, "0")}`;
    return {
      id,
      symbol: quote.symbol,
      name: quote.name,
      type,
      message,
      price: quote.price,
      changePct,
      volume: quote.volume,
      triggeredAt: new Date().toISOString(),
    };
  }

  private checkLimitBidChange(quote: AuctionQuote, changePct: number): AuctionAlert[] {
    const alerts: AuctionAlert[] = [];
    const prevState = this.lastLimitState.get(quote.symbol) ?? "none";

    // 判断当前封单状态
    let currentState: "limit_up" | "limit_down" | "none" = "none";

    // A股涨跌停板一般为10%（ST为5%），这里用 9.8% 作为阈值
    const limitUpThreshold = 9.8;
    const limitDownThreshold = -9.8;

    if (changePct >= limitUpThreshold && quote.bidVolume1 && quote.bidVolume1 > 0) {
      currentState = "limit_up";
    } else if (changePct <= limitDownThreshold && quote.askVolume1 && quote.askVolume1 > 0) {
      currentState = "limit_down";
    }

    // 检测状态变化
    if (currentState === "limit_up" && prevState !== "limit_up") {
      const alert = this.createAlert(quote, "limit_up_bid", changePct,
        `${quote.name ?? quote.symbol} 集合竞价涨停封单，封单量 ${this.formatVolume(quote.bidVolume1 ?? 0)}`);
      if (alert) alerts.push(alert);
    }
    if (currentState === "limit_down" && prevState !== "limit_down") {
      const alert = this.createAlert(quote, "limit_down_bid", changePct,
        `${quote.name ?? quote.symbol} 集合竞价跌停封单，封单量 ${this.formatVolume(quote.askVolume1 ?? 0)}`);
      if (alert) alerts.push(alert);
    }
    if (currentState === "none" && (prevState === "limit_up" || prevState === "limit_down")) {
      const direction = prevState === "limit_up" ? "涨停" : "跌停";
      const alert = this.createAlert(quote, "limit_bid_gone", changePct,
        `${quote.name ?? quote.symbol} 集合竞价${direction}封单消失，当前价 ¥${quote.price.toFixed(2)}`);
      if (alert) alerts.push(alert);
    }

    this.lastLimitState.set(quote.symbol, currentState);
    return alerts;
  }

  private async emitAlert(alert: AuctionAlert): Promise<void> {
    const emoji = this.getAlertEmoji(alert.type);
    const msg = `${emoji} ${alert.message}`;

    // 通知用户
    if (this.notifyFn) {
      try {
        await this.notifyFn(msg);
      } catch {
        // best-effort
      }
    }

    // 持久化到 AlertStore + EventBus
    if (this.alertCallback) {
      const severity = alert.type === "low_open" || alert.type === "limit_down_bid"
        ? "warning" as const
        : "info" as const;
      this.alertCallback(severity, msg, {
        alertType: alert.type,
        symbol: alert.symbol,
        price: alert.price,
        changePct: alert.changePct,
        volume: alert.volume,
        source: "auction-monitor",
      });
    }
  }

  private getAlertEmoji(type: AuctionAlertType): string {
    switch (type) {
      case "high_open": return "🔴 竞价高开";
      case "low_open": return "🟢 竞价低开";
      case "volume_surge": return "🔊 竞价量异动";
      case "limit_up_bid": return "🔒📈 涨停封单";
      case "limit_down_bid": return "🔒📉 跌停封单";
      case "limit_bid_gone": return "🔓 封单消失";
      default: return "📊";
    }
  }

  /** 格式化成交量（万手/手） */
  private formatVolume(vol: number): string {
    if (vol >= 10000) {
      return `${(vol / 10000).toFixed(1)}万手`;
    }
    return `${vol}手`;
  }

  // ── Scheduling ────────────────────────────────────────────────

  private checkAndToggle(): void {
    if (this.isAuctionTime()) {
      this.startMonitoring();
    } else {
      this.stopMonitoring();
      // 每天重置已发送告警（在集合竞价结束后清除）
      if (this.isPostAuctionReset()) {
        this.sentAlerts.clear();
        this.lastLimitState.clear();
      }
    }
  }

  /** 判断当前是否在集合竞价时段 (09:15-09:25 CST) */
  isAuctionTime(): boolean {
    const now = new Date();
    const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const day = cst.getUTCDay();
    // 周末不开市
    if (day === 0 || day === 6) return false;

    const h = cst.getUTCHours();
    const m = cst.getUTCMinutes();
    const hm = h * 100 + m;

    // 09:15 - 09:25
    return hm >= 915 && hm <= 925;
  }

  /** 判断是否在竞价刚结束的重置窗口 (09:26-09:30) */
  private isPostAuctionReset(): boolean {
    const now = new Date();
    const cst = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const h = cst.getUTCHours();
    const m = cst.getUTCMinutes();
    const hm = h * 100 + m;
    return hm >= 926 && hm <= 930;
  }

  private startMonitoring(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.checkOnce();
    }, this.checkIntervalMs);
    // 立即检查一次
    void this.checkOnce();
  }

  private stopMonitoring(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  // ── 5日均量管理 ───────────────────────────────────────────────

  /** 确保指定标的的5日均量已加载 */
  private async ensureAvgVolumes(symbols: string[]): Promise<void> {
    if (!this.avgVolumeFn) return;

    const missing = symbols.filter((s) => !this.avgVolume5d.has(s));
    if (missing.length === 0) return;

    await Promise.all(
      missing.map(async (symbol) => {
        try {
          const avg = await this.avgVolumeFn!(symbol);
          if (avg != null && avg > 0) {
            this.avgVolume5d.set(symbol, avg);
          }
        } catch {
          // best-effort
        }
      }),
    );

    this.saveAvgVolumes();
  }

  // ── Persistence ───────────────────────────────────────────────

  private get persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/auction-avg-volumes.json`;
  }

  private saveAvgVolumes(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const data: Record<string, number> = {};
      for (const [k, v] of this.avgVolume5d) data[k] = v;
      writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // best-effort
    }
  }

  private loadAvgVolumes(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as Record<string, number>;
      for (const [k, v] of Object.entries(data)) {
        if (typeof v === "number" && v > 0) {
          this.avgVolume5d.set(k, v);
        }
      }
    } catch {
      // corrupt — start fresh
    }
  }
}
