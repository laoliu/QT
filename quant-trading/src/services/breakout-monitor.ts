/**
 * BreakoutMonitor — compound breakout entry automation.
 *
 * Monitors breakout setups with multiple conditions that must ALL be satisfied:
 *   1. Price breakout: price >= trigger price
 *   2. Volume confirmation: recent volume spike above average (optional)
 *   3. Hold duration: price stays above level for N consecutive checks (optional)
 *   4. Time window: only trigger within specified intraday windows (optional)
 *
 * Also supports:
 *   - Pullback re-entry: after initial breakout, if price pulls back to level
 *     and holds, auto-enters at the pullback bounce
 *   - EOD MA-based auto-exit: scheduled check near market close, exits
 *     positions that closed below a specified moving average
 *
 * Persists setups to disk for crash recovery.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export type BreakoutSetupStatus =
  | "watching" // waiting for breakout
  | "confirming" // price broke out, counting hold bars
  | "triggered" // all conditions met, order submitted
  | "pullback_wait" // initial breakout seen, waiting for pullback entry
  | "expired" // setup expired without triggering
  | "cancelled"; // manually cancelled

export interface VolumeCondition {
  /** Minimum volume ratio (current period volume / average volume). E.g. 1.5 = 150% of avg. */
  minRatio: number;
  /** Period in minutes for "current" volume window (e.g. 5 = last 5 minutes). */
  periodMinutes: number;
  /** How many periods to average for the baseline (e.g. 6 = last 30 minutes if period=5). */
  baselinePeriods: number;
}

export interface BreakoutSetup {
  id: string;
  symbol: string;
  /** Trigger price for breakout (price must >= this). */
  triggerPrice: number;
  /** Human-readable description. */
  description: string;
  /** Created timestamp (ISO). */
  createdAt: string;
  /** Current status. */
  status: BreakoutSetupStatus;
  /** Buy at triggerPrice + bufferPct (0.002 = +0.2%). */
  bufferPct: number;

  // ── Volume confirmation (optional) ──
  volumeCondition?: VolumeCondition;

  // ── Hold confirmation ──
  /** Number of consecutive checks price must stay above trigger (default 1). */
  holdChecks: number;
  /** Current consecutive count above trigger. */
  currentHoldCount: number;

  // ── Time window (optional) ──
  /** Allowed intraday windows, e.g. ["09:45-10:30", "14:00-15:00"]. */
  timeWindows?: string[];

  // ── Order parameters ──
  /** Order type for entry. */
  orderType: "market" | "limit" | "stop_limit";
  /** Quantity to buy (computed from position sizing if 0). */
  quantity: number;
  /** Stop-loss price (optional, auto-sets conditional stop after fill). */
  stopLossPrice?: number;

  // ── Pullback re-entry ──
  /** Whether to enable pullback re-entry mode after initial breakout. */
  enablePullback: boolean;
  /** How far price must pull back toward trigger (0-1, e.g. 0.005 = 0.5%). */
  pullbackPct?: number;
  /** Whether the initial breakout has been seen (transitions to pullback_wait). */
  initialBreakoutSeen?: boolean;

  // ── Expiry ──
  /** Expiry timestamp (ISO). Setup auto-expires after this time. */
  expiresAt?: string;

  // ── Result ──
  triggeredAt?: string;
  orderResult?: string;
}

export interface EodExitRule {
  id: string;
  symbol: string;
  /** Moving average period for EOD check (e.g. 10 = 10-day MA). */
  maPeriod: number;
  /** MA type. */
  maType: "sma" | "ema";
  /** Buffer below MA before triggering exit (0-1, e.g. 0.005 = 0.5%). */
  bufferPct: number;
  /** "close_all" or "reduce_half". */
  exitAction: "close_all" | "reduce_half";
  /** Active flag. */
  active: boolean;
  /** Created timestamp. */
  createdAt: string;
  /** Description. */
  description: string;
}

/** Volume sample for intraday tracking. */
interface VolumeSample {
  timestamp: number;
  cumulativeVolume: number;
}

export interface BreakoutMonitorOptions {
  checkIntervalMs?: number;
  maxSetups?: number;
  dataDir?: string | null;
}

export class BreakoutMonitor {
  private setups = new Map<string, BreakoutSetup>();
  private eodRules = new Map<string, EodExitRule>();
  private idCounter = 0;
  private eodIdCounter = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly checkIntervalMs: number;
  private readonly maxSetups: number;
  private readonly dataDir: string | null;

  // Intraday volume tracking: symbol → recent volume samples
  private volumeHistory = new Map<string, VolumeSample[]>();

  // Callbacks
  private quoteFn?: (
    symbol: string,
  ) => Promise<{ price: number; volume: number; changePct: number } | null>;
  private orderFn?: (params: {
    symbol: string;
    side: "buy" | "sell";
    orderType: "market" | "limit" | "stop_limit";
    quantity: number;
    price?: number;
    stopPrice?: number;
    strategyId?: string;
  }) => Promise<{ ok: boolean; reason?: string; orderId?: string }>;
  private notifyFn?: (message: string) => void | Promise<void>;
  private maFn?: (symbol: string, period: number, maType: "sma" | "ema") => Promise<number | null>;
  private positionFn?: (symbol: string) => Promise<{ quantity: number } | null>;

  constructor(opts: BreakoutMonitorOptions = {}) {
    this.checkIntervalMs = opts.checkIntervalMs ?? 30_000;
    this.maxSetups = opts.maxSetups ?? 20;
    this.dataDir = opts.dataDir ?? null;
    this.loadFromDisk();
  }

  setQuoteFn(
    fn: (symbol: string) => Promise<{ price: number; volume: number; changePct: number } | null>,
  ): void {
    this.quoteFn = fn;
  }

  setOrderFn(
    fn: (params: {
      symbol: string;
      side: "buy" | "sell";
      orderType: "market" | "limit" | "stop_limit";
      quantity: number;
      price?: number;
      stopPrice?: number;
      strategyId?: string;
    }) => Promise<{ ok: boolean; reason?: string; orderId?: string }>,
  ): void {
    this.orderFn = fn;
  }

  setNotifyFn(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  setMaFn(
    fn: (symbol: string, period: number, maType: "sma" | "ema") => Promise<number | null>,
  ): void {
    this.maFn = fn;
  }

  setPositionFn(fn: (symbol: string) => Promise<{ quantity: number } | null>): void {
    this.positionFn = fn;
  }

  // ── Setup management ────────────────────────────────────────

  addSetup(params: {
    symbol: string;
    triggerPrice: number;
    description?: string;
    bufferPct?: number;
    volumeCondition?: VolumeCondition;
    holdChecks?: number;
    timeWindows?: string[];
    orderType?: "market" | "limit" | "stop_limit";
    quantity: number;
    stopLossPrice?: number;
    enablePullback?: boolean;
    pullbackPct?: number;
    expiresAt?: string;
  }): string {
    if (this.setups.size >= this.maxSetups) {
      // Evict expired/triggered/cancelled setups
      for (const [id, s] of this.setups) {
        if (s.status === "expired" || s.status === "triggered" || s.status === "cancelled") {
          this.setups.delete(id);
          if (this.setups.size < this.maxSetups) break;
        }
      }
    }
    if (this.setups.size >= this.maxSetups) {
      throw new Error(`突破监控数量已达上限 (${String(this.maxSetups)})`);
    }

    const id = `bo-${String(++this.idCounter).padStart(4, "0")}`;
    const setup: BreakoutSetup = {
      id,
      symbol: params.symbol,
      triggerPrice: params.triggerPrice,
      description: params.description ?? `${params.symbol} 突破 ¥${params.triggerPrice.toFixed(2)}`,
      createdAt: new Date().toISOString(),
      status: "watching",
      bufferPct: params.bufferPct ?? 0.002,
      volumeCondition: params.volumeCondition,
      holdChecks: params.holdChecks ?? 1,
      currentHoldCount: 0,
      timeWindows: params.timeWindows,
      orderType: params.orderType ?? "limit",
      quantity: params.quantity,
      stopLossPrice: params.stopLossPrice,
      enablePullback: params.enablePullback ?? false,
      pullbackPct: params.pullbackPct,
      expiresAt: params.expiresAt,
    };

    this.setups.set(id, setup);
    this.saveToDisk();
    return id;
  }

  cancelSetup(id: string): boolean {
    const setup = this.setups.get(id);
    if (!setup || setup.status === "triggered" || setup.status === "cancelled") return false;
    setup.status = "cancelled";
    this.saveToDisk();
    return true;
  }

  listSetups(activeOnly = false): BreakoutSetup[] {
    const all = [...this.setups.values()];
    if (activeOnly) {
      return all.filter(
        (s) => s.status === "watching" || s.status === "confirming" || s.status === "pullback_wait",
      );
    }
    return all;
  }

  // ── EOD exit rules ──────────────────────────────────────────

  addEodRule(params: {
    symbol: string;
    maPeriod: number;
    maType?: "sma" | "ema";
    bufferPct?: number;
    exitAction?: "close_all" | "reduce_half";
    description?: string;
  }): string {
    const id = `eod-${String(++this.eodIdCounter).padStart(4, "0")}`;
    const rule: EodExitRule = {
      id,
      symbol: params.symbol,
      maPeriod: params.maPeriod,
      maType: params.maType ?? "sma",
      bufferPct: params.bufferPct ?? 0,
      exitAction: params.exitAction ?? "close_all",
      active: true,
      createdAt: new Date().toISOString(),
      description:
        params.description ??
        `${params.symbol} 收盘跌破${params.maPeriod}日均线 → ${params.exitAction === "reduce_half" ? "减半" : "清仓"}`,
    };
    this.eodRules.set(id, rule);
    this.saveToDisk();
    return id;
  }

  removeEodRule(id: string): boolean {
    const removed = this.eodRules.delete(id);
    if (removed) this.saveToDisk();
    return removed;
  }

  listEodRules(): EodExitRule[] {
    return [...this.eodRules.values()];
  }

  // ── Volume tracking ─────────────────────────────────────────

  /** Record a volume sample (called on each quote update). */
  recordVolume(symbol: string, cumulativeVolume: number): void {
    const samples = this.volumeHistory.get(symbol) ?? [];
    samples.push({ timestamp: Date.now(), cumulativeVolume });
    // Keep last 120 samples (~2 hours at 1-min intervals)
    if (samples.length > 120) samples.splice(0, samples.length - 120);
    this.volumeHistory.set(symbol, samples);
  }

  /** Compute volume ratio for a symbol: recent period volume / baseline average. */
  computeVolumeRatio(symbol: string, vc: VolumeCondition): number | null {
    const samples = this.volumeHistory.get(symbol);
    if (!samples || samples.length < 2) return null;

    const now = Date.now();
    const periodMs = vc.periodMinutes * 60_000;
    const baselineMs = periodMs * vc.baselinePeriods;

    // Recent period volume (last periodMinutes)
    const recentStart = now - periodMs;
    const recentSamples = samples.filter((s) => s.timestamp >= recentStart);
    if (recentSamples.length < 1) return null;

    const recentVolume =
      recentSamples[recentSamples.length - 1]!.cumulativeVolume -
      recentSamples[0]!.cumulativeVolume;

    // Baseline: volume in the preceding baselinePeriods × periodMinutes
    const baselineStart = recentStart - baselineMs;
    const baselineSamples = samples.filter(
      (s) => s.timestamp >= baselineStart && s.timestamp < recentStart,
    );
    if (baselineSamples.length < 2) return null;

    const baselineVolume =
      baselineSamples[baselineSamples.length - 1]!.cumulativeVolume -
      baselineSamples[0]!.cumulativeVolume;

    // Average per period
    const avgPerPeriod = baselineVolume / vc.baselinePeriods;
    if (avgPerPeriod <= 0) return null;

    return recentVolume / avgPerPeriod;
  }

  // ── Core check loop ─────────────────────────────────────────

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.checkAll();
    }, this.checkIntervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  destroy(): void {
    this.stop();
    this.setups.clear();
    this.eodRules.clear();
    this.volumeHistory.clear();
  }

  async checkAll(): Promise<string[]> {
    if (!this.quoteFn) return [];

    const triggered: string[] = [];
    const now = new Date();

    for (const setup of this.listSetups(true)) {
      try {
        // Check expiry
        if (setup.expiresAt && new Date(setup.expiresAt) < now) {
          setup.status = "expired";
          this.saveToDisk();
          continue;
        }

        // Check time window
        if (setup.timeWindows && setup.timeWindows.length > 0) {
          if (!this.isWithinTimeWindows(now, setup.timeWindows)) continue;
        }

        const quote = await this.quoteFn(setup.symbol);
        if (!quote) continue;

        // Record volume sample for ratio computation
        this.recordVolume(setup.symbol, quote.volume);

        const result = await this.checkSetup(setup, quote);
        if (result) {
          triggered.push(setup.id);
        }
      } catch {
        // Skip failed setups
      }
    }

    // EOD check (14:50-14:59 CST)
    const shanghaiHour = this.getShanghaiHour(now);
    const shanghaiMinute = this.getShanghaiMinute(now);
    if (shanghaiHour === 14 && shanghaiMinute >= 50) {
      await this.runEodChecks();
    }

    return triggered;
  }

  private async checkSetup(
    setup: BreakoutSetup,
    quote: { price: number; volume: number },
  ): Promise<boolean> {
    const priceAboveTrigger = quote.price >= setup.triggerPrice;

    switch (setup.status) {
      case "watching": {
        if (!priceAboveTrigger) {
          setup.currentHoldCount = 0;
          return false;
        }

        // Volume check
        if (setup.volumeCondition) {
          const ratio = this.computeVolumeRatio(setup.symbol, setup.volumeCondition);
          if (ratio === null || ratio < setup.volumeCondition.minRatio) {
            // Price broke out but volume insufficient — notify but don't trigger
            return false;
          }
        }

        // Hold confirmation
        setup.currentHoldCount++;
        if (setup.currentHoldCount < setup.holdChecks) {
          if (setup.currentHoldCount === 1) {
            setup.status = "confirming";
            void this.notify(
              `📊 ${setup.symbol} 价格突破 ¥${setup.triggerPrice.toFixed(2)}，确认中 (${setup.currentHoldCount}/${setup.holdChecks})`,
            );
          }
          return false;
        }

        // All conditions met!
        if (setup.enablePullback) {
          // Enter pullback-wait mode instead of immediate entry
          setup.status = "pullback_wait";
          setup.initialBreakoutSeen = true;
          void this.notify(
            `📊 ${setup.symbol} 突破确认，等待回踩入场 (目标回踩至 ¥${(setup.triggerPrice * (1 + (setup.pullbackPct ?? 0.005))).toFixed(2)} 附近)`,
          );
          return false;
        }

        // Direct entry
        return this.executeEntry(setup, quote.price);
      }

      case "confirming": {
        if (!priceAboveTrigger) {
          // Failed confirmation — reset
          setup.status = "watching";
          setup.currentHoldCount = 0;
          void this.notify(
            `❌ ${setup.symbol} 突破确认失败 (价格回落至 ¥${quote.price.toFixed(2)})`,
          );
          return false;
        }

        // Volume re-check during confirmation
        if (setup.volumeCondition) {
          const ratio = this.computeVolumeRatio(setup.symbol, setup.volumeCondition);
          if (ratio === null || ratio < setup.volumeCondition.minRatio) {
            return false; // still waiting for volume
          }
        }

        setup.currentHoldCount++;
        if (setup.currentHoldCount < setup.holdChecks) {
          return false;
        }

        if (setup.enablePullback) {
          setup.status = "pullback_wait";
          setup.initialBreakoutSeen = true;
          void this.notify(`📊 ${setup.symbol} 突破确认完成，等待回踩入场`);
          return false;
        }

        return this.executeEntry(setup, quote.price);
      }

      case "pullback_wait": {
        // Wait for price to pull back to near trigger, then bounce
        const pullbackTarget = setup.triggerPrice * (1 + (setup.pullbackPct ?? 0.005));

        if (quote.price <= pullbackTarget && quote.price >= setup.triggerPrice) {
          // In pullback zone, above support — enter
          void this.notify(
            `📊 ${setup.symbol} 回踩到位 ¥${quote.price.toFixed(2)} (触发线 ¥${setup.triggerPrice.toFixed(2)})，执行入场`,
          );
          return this.executeEntry(setup, quote.price);
        }

        if (quote.price < setup.triggerPrice * 0.99) {
          // Pullback too deep — failed
          setup.status = "expired";
          void this.notify(
            `❌ ${setup.symbol} 回踩过深 ¥${quote.price.toFixed(2)} (跌破触发线)，放弃入场`,
          );
          this.saveToDisk();
          return false;
        }

        return false; // still waiting
      }

      default:
        return false;
    }
  }

  private async executeEntry(setup: BreakoutSetup, currentPrice: number): Promise<boolean> {
    if (!this.orderFn) {
      setup.status = "triggered";
      setup.triggeredAt = new Date().toISOString();
      setup.orderResult = "no_order_fn";
      this.saveToDisk();
      return true;
    }

    // Calculate entry price with buffer
    const entryPrice = setup.triggerPrice * (1 + setup.bufferPct);

    try {
      const result = await this.orderFn({
        symbol: setup.symbol,
        side: "buy",
        orderType: setup.orderType,
        quantity: setup.quantity,
        price: setup.orderType === "market" ? undefined : entryPrice,
        stopPrice: setup.orderType === "stop_limit" ? setup.triggerPrice : undefined,
        strategyId: "breakout_monitor",
      });

      setup.status = "triggered";
      setup.triggeredAt = new Date().toISOString();
      setup.orderResult = result.ok ? `ok:${result.orderId ?? ""}` : `fail:${result.reason ?? ""}`;
      this.saveToDisk();

      const msg = result.ok
        ? `✅ 突破入场: ${setup.symbol} 买入${setup.quantity}股 @ ¥${entryPrice.toFixed(2)} (${setup.orderType})`
        : `⚠️ 突破入场失败: ${setup.symbol} — ${result.reason ?? "unknown"}`;
      void this.notify(msg);

      return result.ok;
    } catch (err) {
      setup.orderResult = `error:${(err as Error).message}`;
      void this.notify(`⚠️ 突破入场异常: ${setup.symbol} — ${(err as Error).message}`);
      this.saveToDisk();
      return false;
    }
  }

  // ── EOD checks ──────────────────────────────────────────────

  private async runEodChecks(): Promise<void> {
    if (!this.maFn || !this.orderFn || !this.positionFn) return;

    for (const rule of this.eodRules.values()) {
      if (!rule.active) continue;

      try {
        const [quote, maValue, position] = await Promise.all([
          this.quoteFn?.(rule.symbol),
          this.maFn(rule.symbol, rule.maPeriod, rule.maType),
          this.positionFn(rule.symbol),
        ]);

        if (!quote || maValue == null || !position || position.quantity <= 0) continue;

        const threshold = maValue * (1 - rule.bufferPct);
        if (quote.price < threshold) {
          const sellQty =
            rule.exitAction === "reduce_half"
              ? Math.floor(position.quantity / 2 / 100) * 100 || position.quantity
              : position.quantity;

          const result = await this.orderFn({
            symbol: rule.symbol,
            side: "sell",
            orderType: "market",
            quantity: sellQty,
            strategyId: "eod_ma_exit",
          });

          const actionStr = rule.exitAction === "reduce_half" ? "减半" : "清仓";
          const msg = result.ok
            ? `🌙 收盘${actionStr}: ${rule.symbol} 卖出${sellQty}股 (价格 ¥${quote.price.toFixed(2)} < ${rule.maPeriod}日MA ¥${maValue.toFixed(2)})`
            : `⚠️ 收盘${actionStr}失败: ${rule.symbol} — ${result.reason ?? "unknown"}`;
          void this.notify(msg);
        }
      } catch {
        // Skip failed checks
      }
    }
  }

  // ── Helpers ─────────────────────────────────────────────────

  private isWithinTimeWindows(now: Date, windows: string[]): boolean {
    const hh = this.getShanghaiHour(now);
    const mm = this.getShanghaiMinute(now);
    const current = hh * 60 + mm;

    for (const w of windows) {
      const [startStr, endStr] = w.split("-");
      if (!startStr || !endStr) continue;
      const [sh, sm] = startStr.split(":").map(Number);
      const [eh, em] = endStr.split(":").map(Number);
      if (sh == null || sm == null || eh == null || em == null) continue;
      const start = sh * 60 + sm;
      const end = eh * 60 + em;
      if (current >= start && current <= end) return true;
    }
    return false;
  }

  private getShanghaiHour(d: Date): number {
    return Number(
      d.toLocaleString("en-US", { hour: "numeric", hour12: false, timeZone: "Asia/Shanghai" }),
    );
  }

  private getShanghaiMinute(d: Date): number {
    return Number(d.toLocaleString("en-US", { minute: "numeric", timeZone: "Asia/Shanghai" }));
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

  // ── Persistence ─────────────────────────────────────────────

  private get persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/breakout-setups.json`;
  }

  private saveToDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const data = {
        idCounter: this.idCounter,
        eodIdCounter: this.eodIdCounter,
        setups: [...this.setups.values()],
        eodRules: [...this.eodRules.values()],
      };
      writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // best-effort
    }
  }

  private loadFromDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as {
        idCounter?: number;
        eodIdCounter?: number;
        setups?: BreakoutSetup[];
        eodRules?: EodExitRule[];
      };
      this.idCounter = data.idCounter ?? 0;
      this.eodIdCounter = data.eodIdCounter ?? 0;
      for (const s of data.setups ?? []) this.setups.set(s.id, s);
      for (const r of data.eodRules ?? []) this.eodRules.set(r.id, r);
    } catch {
      // corrupt — start fresh
    }
  }
}
