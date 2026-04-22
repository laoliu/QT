/**
 * AutoTradeScheduler — orchestrates daily automated trading workflow.
 *
 * Workflow (weekdays only):
 *   1. Screen phase (configurable, default 07:30): run daily composite picks
 *   2. Recommend phase: generate position allocations from picks
 *   3. Execute phase (configurable, default 09:20): submit orders + conditional exits
 *
 * Persistence: config saved to disk, survives restarts.
 * Safety: only runs on trading days; dry-run mode available; respects market hours.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import http from "node:http";
import { isTradingDay } from "../utils/trading-calendar.js";

// ── Types ────────────────────────────────────────────────────────

export interface AutoTradeConfig {
  /** Whether the auto-trade scheduler is enabled. */
  enabled: boolean;
  /** Cron expression for the screening phase (default: "30 7 * * 1-5" = weekdays 07:30). */
  screenCron: string;
  /** Cron expression for the execution phase (default: "31 9 * * 1-5" = weekdays 09:31). */
  executeCron: string;
  /** Execute in dry-run mode (validate only, no real orders). */
  dryRun: boolean;
  /** Execute percentage for first batch (10-100, default 60). */
  executePct: number;
  /** Whether to auto-create conditional stop/profit orders. */
  createConditionalOrders: boolean;
  /** Trailing stop percentage (2-20, default 8). */
  trailingStopPct: number;
  /** Initial capital for screening. */
  initialCapital: number;
  /** Risk tolerance: low | medium | high | aggressive */
  riskTolerance: string;
  /** Backtest start date (YYYY-MM-DD). */
  startDate: string;
  /** Strategies to use (empty = all default). */
  strategies: string[];
  /** Top N candidates per strategy. */
  topNPerStrategy: number;
  /** Number of top strategies to select. */
  topStrategyCount: number;
  /** Final stock limit (3-8). */
  finalStockLimit: number;
  /** Force pre-market orders (bypass market-hours check). */
  forcePremarket: boolean;
  /** Rebalance days for backtest. */
  rebalanceDays: number;
  /** Recommend strategy key. */
  recommendStrategy: string;
}

export interface AutoTradeStatus {
  enabled: boolean;
  config: AutoTradeConfig;
  lastScreenAt?: string;
  lastScreenResult?: string;
  lastExecuteAt?: string;
  lastExecuteResult?: string;
  lastError?: string;
  screenCronId?: string;
  executeCronId?: string;
  /** Symbols from last screening. */
  pendingSymbols: string[];
  /** Allocations from last recommend (pending execution). */
  pendingAllocations: unknown[];
}

type NotifyFn = (message: string) => void | Promise<void>;

const DEFAULT_CONFIG: AutoTradeConfig = {
  enabled: false,
  screenCron: "30 7 * * 1-5",
  executeCron: "31 9 * * 1-5",
  dryRun: true,
  executePct: 60,
  createConditionalOrders: true,
  trailingStopPct: 8,
  initialCapital: 500_000,
  riskTolerance: "high",
  startDate: "",
  strategies: [],
  topNPerStrategy: 8,
  topStrategyCount: 3,
  finalStockLimit: 8,
  forcePremarket: false,
  rebalanceDays: 5,
  recommendStrategy: "mixed",
};

// ── Service ──────────────────────────────────────────────────────

export class AutoTradeScheduler {
  private config: AutoTradeConfig;
  private status: Omit<AutoTradeStatus, "enabled" | "config">;
  private dataDir: string | null;
  private notifyFn?: NotifyFn;
  private screenTimerId: ReturnType<typeof setInterval> | null = null;
  private executeTimerId: ReturnType<typeof setInterval> | null = null;
  private baseUrl: string;
  private authToken: string;
  private running = false;

  constructor(opts: {
    dataDir?: string | null;
    baseUrl: string;
    authToken: string;
  }) {
    this.dataDir = opts.dataDir ?? null;
    this.baseUrl = opts.baseUrl;
    this.authToken = opts.authToken;
    this.config = { ...DEFAULT_CONFIG };
    this.status = {
      pendingSymbols: [],
      pendingAllocations: [],
    };
  }

  setNotify(fn: NotifyFn): void {
    this.notifyFn = fn;
  }

  // ── Lifecycle ──────────────────────────────────────────────

  /** Load config from disk and start if enabled. */
  start(): void {
    this.loadFromDisk();
    if (this.config.enabled) {
      this.startTimers();
      // Check if we missed today's scheduled phases (e.g. system started late)
      setTimeout(() => void this.catchUpIfNeeded(), 5_000);
    }
  }

  /** Stop all timers. */
  stop(): void {
    this.stopTimers();
  }

  /** Destroy (alias for stop). */
  destroy(): void {
    this.stop();
  }

  // ── API ────────────────────────────────────────────────────

  /** Get current status. */
  getStatus(): AutoTradeStatus {
    return {
      enabled: this.config.enabled,
      config: { ...this.config },
      ...this.status,
    };
  }

  /** Update configuration. */
  updateConfig(partial: Partial<AutoTradeConfig>): AutoTradeConfig {
    const prev = this.config.enabled;
    Object.assign(this.config, partial);

    // Clamp values
    this.config.executePct = Math.max(10, Math.min(100, this.config.executePct));
    this.config.trailingStopPct = Math.max(2, Math.min(20, this.config.trailingStopPct));
    this.config.topNPerStrategy = Math.max(5, Math.min(20, this.config.topNPerStrategy));
    this.config.topStrategyCount = Math.max(2, Math.min(5, this.config.topStrategyCount));
    this.config.finalStockLimit = Math.max(3, Math.min(8, this.config.finalStockLimit));
    this.config.initialCapital = Math.max(10_000, this.config.initialCapital);

    this.saveToDisk();

    // Toggle timers based on enabled state
    if (this.config.enabled && !prev) {
      this.startTimers();
      this.notify("🤖 自动交易已启用 — 筛选: " + this.config.screenCron + " | 执行: " + this.config.executeCron);
      // Check if we need to catch up on missed phases today
      setTimeout(() => void this.catchUpIfNeeded(), 3_000);
    } else if (!this.config.enabled && prev) {
      this.stopTimers();
      this.notify("⏸️ 自动交易已暂停");
    } else if (this.config.enabled) {
      // Restart timers if cron changed
      this.stopTimers();
      this.startTimers();
    }

    return { ...this.config };
  }

  /** Manually trigger the screening phase. */
  async triggerScreen(): Promise<{ ok: boolean; symbols: string[]; error?: string }> {
    return this.runScreenPhase();
  }

  /** Manually trigger the execution phase (requires pending allocations). */
  async triggerExecute(): Promise<{ ok: boolean; result?: unknown; error?: string }> {
    return this.runExecutePhase();
  }

  // ── Internal: Timer management ─────────────────────────────

  private startTimers(): void {
    // Check every 30 seconds if it's time to fire
    if (!this.screenTimerId) {
      this.screenTimerId = setInterval(() => {
        void this.checkAndRunScreen();
      }, 30_000);
    }
    if (!this.executeTimerId) {
      this.executeTimerId = setInterval(() => {
        void this.checkAndRunExecute();
      }, 30_000);
    }
    console.log("[AutoTrade] timers started — screen:", this.config.screenCron, "| execute:", this.config.executeCron);
  }

  private stopTimers(): void {
    if (this.screenTimerId) {
      clearInterval(this.screenTimerId);
      this.screenTimerId = null;
    }
    if (this.executeTimerId) {
      clearInterval(this.executeTimerId);
      this.executeTimerId = null;
    }
  }

  // ── Startup catch-up: run missed phases ────────────────────

  /**
   * On startup, check if today's scheduled phases were missed.
   * E.g. screenCron = "30 7 * * 1-5" but system started at 8:00 or 9:30.
   *
   * Rules:
   *  - Only on trading days.
   *  - Screen phase: catch up if current time > screenCron time AND
   *    no screen was done today yet.
   *  - Execute phase: catch up if current time > executeCron time AND
   *    there are pending allocations (from a previous screen) AND
   *    no execution was done today yet.
   *  - Execute phase without prior screen: if current time > executeCron
   *    AND no screen today AND current time <= a cutoff (default 14:30),
   *    run screen first, then execute.
   */
  private async catchUpIfNeeded(): Promise<void> {
    if (this.running) return;

    const now = new Date();
    const today = this.formatDate(now);

    if (!isTradingDay(today)) return;

    const hm = now.getHours() * 100 + now.getMinutes();
    const screenHm = this.parseCronTime(this.config.screenCron);
    const executeHm = this.parseCronTime(this.config.executeCron);

    // Check if screen cron applies today (weekday match)
    if (!this.matchesCron(this.config.screenCron, now) &&
        !this.cronMatchesDay(this.config.screenCron, now)) {
      return;
    }

    const screenedToday = this.status.lastScreenAt?.startsWith(today) ?? false;
    const executedToday = this.status.lastExecuteAt?.startsWith(today) ?? false;

    // Don't catch up if it's too late in the day (after 14:30)
    if (hm > 1430) {
      console.log("[AutoTrade] catch-up skipped — too late in day (after 14:30)");
      return;
    }

    // Case 1: Missed screening, haven't screened today
    if (hm > screenHm && !screenedToday) {
      console.log(`[AutoTrade] catch-up: missed screen (${screenHm}), running now at ${hm}`);
      this.notify(`🔄 自动交易补偿：系统启动时已过筛选时间(${this.formatHm(screenHm)})，正在补跑选股…`);
      await this.runScreenPhase();
    }

    // Case 2: Missed execution, have pending allocations, haven't executed today
    if (hm > executeHm && !executedToday && this.status.pendingAllocations.length > 0) {
      console.log(`[AutoTrade] catch-up: missed execute (${executeHm}), running now at ${hm}`);
      this.notify(`🔄 自动交易补偿：系统启动时已过下单时间(${this.formatHm(executeHm)})，正在补跑执行…`);
      await this.runExecutePhase();
    }
  }

  /** Parse cron "M H * * *" to HHMM integer. Returns 0 if unparseable. */
  private parseCronTime(expr: string): number {
    const parts = expr.trim().split(/\s+/);
    if (parts.length < 2) return 0;
    const m = parseInt(parts[0]!, 10);
    const h = parseInt(parts[1]!, 10);
    if (isNaN(m) || isNaN(h)) return 0;
    return h * 100 + m;
  }

  /** Check if a cron expression's day-of-week field matches the given date. */
  private cronMatchesDay(expr: string, date: Date): boolean {
    const parts = expr.trim().split(/\s+/);
    if (parts.length < 5) return false;
    return this.matchCronField(parts[4]!, date.getDay());
  }

  /** Format HHMM integer to "HH:MM" string. */
  private formatHm(hm: number): string {
    const h = Math.floor(hm / 100);
    const m = hm % 100;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }

  /** Track last-fired minute to avoid double-fire. */
  private lastScreenMinute = "";
  private lastExecuteMinute = "";

  private async checkAndRunScreen(): Promise<void> {
    if (this.running) return;
    const now = new Date();
    const minuteKey = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}-${now.getHours()}-${now.getMinutes()}`;
    if (this.lastScreenMinute === minuteKey) return;

    if (!this.matchesCron(this.config.screenCron, now)) return;

    // Check if today is a trading day
    const dateStr = this.formatDate(now);
    if (!isTradingDay(dateStr)) {
      console.log("[AutoTrade] skipping screen — not a trading day:", dateStr);
      return;
    }

    this.lastScreenMinute = minuteKey;
    console.log("[AutoTrade] screen phase triggered at", now.toISOString());
    await this.runScreenPhase();
  }

  private async checkAndRunExecute(): Promise<void> {
    if (this.running) return;
    const now = new Date();
    const minuteKey = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}-${now.getHours()}-${now.getMinutes()}`;
    if (this.lastExecuteMinute === minuteKey) return;

    if (!this.matchesCron(this.config.executeCron, now)) return;

    const dateStr = this.formatDate(now);
    if (!isTradingDay(dateStr)) {
      console.log("[AutoTrade] skipping execute — not a trading day:", dateStr);
      return;
    }

    this.lastExecuteMinute = minuteKey;

    if (this.status.pendingAllocations.length === 0) {
      console.log("[AutoTrade] no pending allocations — skipping execute");
      this.notify("⚠️ 自动交易：无待执行的推荐，跳过下单阶段。请检查筛选是否成功运行。");
      return;
    }

    console.log("[AutoTrade] execute phase triggered at", now.toISOString());
    await this.runExecutePhase();
  }

  // ── Internal: Phases ───────────────────────────────────────

  private async runScreenPhase(): Promise<{ ok: boolean; symbols: string[]; error?: string; allocations?: Array<{ code: string; name: string; shares: number; price: number; amount: number }> }> {
    this.running = true;
    this.status.lastScreenAt = new Date().toISOString();
    this.notify("🔍 自动交易：开始每日综合选股…");

    try {
      // Step 1: Run daily picks
      const startDate = this.config.startDate || this.defaultStartDate();
      const pickBody: Record<string, unknown> = {
        strategies: this.config.strategies.length > 0 ? this.config.strategies : undefined,
        top_n_per_strategy: this.config.topNPerStrategy,
        top_strategy_count: this.config.topStrategyCount,
        final_stock_limit: this.config.finalStockLimit,
        initial_capital: this.config.initialCapital,
        risk_tolerance: this.config.riskTolerance,
        rebalance_days: this.config.rebalanceDays,
        start_date: startDate,
      };

      const pickResult = await this.callApi("/quant/admin/api/daily-picks", pickBody);
      if (pickResult.error) {
        const err = `筛选失败: ${pickResult.error}`;
        this.status.lastScreenResult = err;
        this.status.lastError = err;
        this.notify("❌ 自动交易筛选失败: " + pickResult.error);
        return { ok: false, symbols: [], error: err };
      }

      const finalSymbols: string[] = Array.isArray(pickResult.finalSymbols) ? pickResult.finalSymbols : [];
      if (finalSymbols.length === 0) {
        const err = "筛选完成但无入围股票";
        this.status.lastScreenResult = err;
        this.status.pendingSymbols = [];
        this.status.pendingAllocations = [];
        this.notify("⚠️ 自动交易筛选完成，但无股票入围。");
        return { ok: false, symbols: [], error: err };
      }

      this.status.pendingSymbols = finalSymbols;

      // Get best strategy from the picks
      const ranking: Array<{ rank?: number; strategy?: string }> = Array.isArray(pickResult.strategyRanking) ? pickResult.strategyRanking : [];
      const bestStrategy = ranking.find(r => r.rank === 1)?.strategy || this.config.recommendStrategy;

      // Step 2: Run recommendation
      const recBody = {
        capital: this.config.initialCapital,
        strategy: bestStrategy,
        risk_tolerance: this.config.riskTolerance,
        rebalance_days: this.config.rebalanceDays,
        start_date: startDate,
        symbols: finalSymbols.join(","),
      };

      const recResult = await this.callApi("/quant/admin/api/recommend", recBody);
      if (recResult.error) {
        const err = `推荐生成失败: ${recResult.error}`;
        this.status.lastScreenResult = err;
        this.status.lastError = err;
        this.notify("❌ 自动交易推荐失败: " + recResult.error);
        return { ok: false, symbols: finalSymbols, error: err };
      }

      const allocations: unknown[] = Array.isArray(recResult.allocations) ? recResult.allocations : [];
      this.status.pendingAllocations = allocations;
      this.status.lastScreenResult = `筛选成功: ${finalSymbols.length} 只入围, ${allocations.length} 只已生成推荐`;
      this.status.lastError = undefined;
      this.saveToDisk();

      const symbolList = finalSymbols.join(", ");
      this.notify(
        `✅ 自动交易筛选完成\n入围 ${finalSymbols.length} 只: ${symbolList}\n推荐 ${allocations.length} 只已就绪，等待 ${this.config.executeCron} 执行下单${this.config.dryRun ? "（试运行模式）" : ""}`,
      );

      // Return allocation summaries for UI display
      const allocSummary = allocations.map((a: unknown) => {
        const r = a as Record<string, unknown>;
        return { code: String(r.code ?? ""), name: String(r.name ?? ""), shares: Number(r.shares ?? 0), price: Number(r.price ?? 0), amount: Number(r.amount ?? 0) };
      });

      return { ok: true, symbols: finalSymbols, allocations: allocSummary };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.status.lastScreenResult = `异常: ${msg}`;
      this.status.lastError = msg;
      this.notify("❌ 自动交易筛选异常: " + msg);
      return { ok: false, symbols: [], error: msg };
    } finally {
      this.running = false;
    }
  }

  private async runExecutePhase(): Promise<{ ok: boolean; result?: unknown; error?: string }> {
    this.running = true;
    this.status.lastExecuteAt = new Date().toISOString();

    if (this.status.pendingAllocations.length === 0) {
      this.running = false;
      const err = "无待执行推荐";
      this.status.lastExecuteResult = err;
      return { ok: false, error: err };
    }

    this.notify(
      `🚀 自动交易：开始${this.config.dryRun ? "试运行" : "执行"}下单（${this.status.pendingAllocations.length} 只）…`,
    );

    try {
      // Smart premarket: if configured time is before 9:30, auto-enable
      // force_premarket so orders are accepted as pre-market limit orders.
      const now = new Date();
      const hm = now.getHours() * 100 + now.getMinutes();
      const needsPremarket = this.config.forcePremarket || hm < 930;

      const execBody = {
        dry_run: this.config.dryRun,
        force_premarket: needsPremarket,
        execute_pct: this.config.executePct,
        create_conditional_orders: this.config.createConditionalOrders,
        trailing_stop_pct: this.config.trailingStopPct,
        allocations: this.status.pendingAllocations,
      };

      const result = await this.callApi("/quant/admin/api/recommend-execute", execBody);
      if (result.error) {
        const err = `执行失败: ${result.error}`;
        this.status.lastExecuteResult = err;
        this.status.lastError = err;
        this.notify("❌ 自动交易执行失败: " + result.error);
        return { ok: false, error: err };
      }

      const exec = (result.execution || {}) as Record<string, unknown>;
      const submitted = Number(exec.submitted) || 0;
      const rejected = Number(exec.rejected) || 0;
      const conds = Number(exec.conditionalOrders) || 0;

      if (result.dryRun) {
        const plan = (result.plan || {}) as Record<string, unknown>;
        this.status.lastExecuteResult = `试运行完成: 计划 ${plan.totalOrders || 0} 笔, 金额 ¥${plan.estimatedAmount || 0}`;
      } else {
        this.status.lastExecuteResult = `执行完成: 提交 ${submitted} 笔, 失败 ${rejected} 笔, 条件单 ${conds} 个`;
        // Clear pending after real execution
        this.status.pendingAllocations = [];
        this.status.pendingSymbols = [];
      }

      this.status.lastError = rejected > 0 ? `${rejected} 笔下单失败` : undefined;
      this.saveToDisk();

      this.notify(
        `${result.dryRun ? "🧪" : "✅"} 自动交易${result.dryRun ? "试运行" : "执行"}完成\n${this.status.lastExecuteResult}`,
      );

      return { ok: true, result };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.status.lastExecuteResult = `异常: ${msg}`;
      this.status.lastError = msg;
      this.notify("❌ 自动交易执行异常: " + msg);
      return { ok: false, error: msg };
    } finally {
      this.running = false;
    }
  }

  // ── Internal: HTTP helper ──────────────────────────────────

  private callApi(path: string, body: unknown): Promise<Record<string, unknown>> {
    return new Promise((resolve) => {
      const data = JSON.stringify(body);
      const url = new URL(path, this.baseUrl);

      const req = http.request(
        {
          hostname: url.hostname,
          port: url.port,
          path: url.pathname,
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(data),
            Authorization: `Bearer ${this.authToken}`,
          },
          timeout: 1_800_000, // 30 min for screening
        },
        (res) => {
          const chunks: Buffer[] = [];
          res.on("data", (chunk: Buffer) => chunks.push(chunk));
          res.on("end", () => {
            try {
              const text = Buffer.concat(chunks).toString("utf-8");
              resolve(JSON.parse(text) as Record<string, unknown>);
            } catch {
              resolve({ error: "响应解析失败" });
            }
          });
        },
      );

      req.on("error", (err) => {
        resolve({ error: `HTTP 请求失败: ${err.message}` });
      });
      req.on("timeout", () => {
        req.destroy();
        resolve({ error: "请求超时（30分钟）" });
      });
      req.write(data);
      req.end();
    });
  }

  // ── Internal: Cron matching ────────────────────────────────

  /** Simple 5-field cron match: minute hour dom month dow. */
  private matchesCron(expr: string, now: Date): boolean {
    const parts = expr.trim().split(/\s+/);
    if (parts.length < 5) return false;
    const fields = [
      now.getMinutes(),
      now.getHours(),
      now.getDate(),
      now.getMonth() + 1,
      now.getDay(),
    ];
    return parts.every((p, i) => this.matchCronField(p!, fields[i]!));
  }

  private matchCronField(pattern: string, value: number): boolean {
    if (pattern === "*") return true;
    // Handle lists: 1,3,5
    if (pattern.includes(",")) {
      return pattern.split(",").some((p) => this.matchCronField(p, value));
    }
    // Handle ranges: 1-5
    if (pattern.includes("-")) {
      const [lo, hi] = pattern.split("-").map(Number);
      return value >= lo! && value <= hi!;
    }
    // Handle step: */5
    if (pattern.startsWith("*/")) {
      const step = Number(pattern.slice(2));
      return step > 0 && value % step === 0;
    }
    return Number(pattern) === value;
  }

  // ── Persistence ────────────────────────────────────────────

  private get persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/auto-trade-config.json`;
  }

  private loadFromDisk(): void {
    const p = this.persistPath;
    if (!p || !existsSync(p)) return;
    try {
      const raw = readFileSync(p, "utf-8");
      const data = JSON.parse(raw) as Partial<AutoTradeConfig & {
        pendingSymbols?: string[];
        pendingAllocations?: unknown[];
        lastScreenAt?: string;
        lastScreenResult?: string;
        lastExecuteAt?: string;
        lastExecuteResult?: string;
      }>;
      // Restore config fields
      for (const key of Object.keys(DEFAULT_CONFIG) as Array<keyof AutoTradeConfig>) {
        if (data[key] !== undefined) {
          (this.config as unknown as Record<string, unknown>)[key] = data[key];
        }
      }
      // Restore status
      if (data.pendingSymbols) this.status.pendingSymbols = data.pendingSymbols;
      if (data.pendingAllocations) this.status.pendingAllocations = data.pendingAllocations;
      if (data.lastScreenAt) this.status.lastScreenAt = data.lastScreenAt;
      if (data.lastScreenResult) this.status.lastScreenResult = data.lastScreenResult;
      if (data.lastExecuteAt) this.status.lastExecuteAt = data.lastExecuteAt;
      if (data.lastExecuteResult) this.status.lastExecuteResult = data.lastExecuteResult;
    } catch {
      // Corrupt — use defaults
    }
  }

  private saveToDisk(): void {
    const p = this.persistPath;
    if (!p) return;
    try {
      const dir = dirname(p);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(
        p,
        JSON.stringify(
          {
            ...this.config,
            pendingSymbols: this.status.pendingSymbols,
            pendingAllocations: this.status.pendingAllocations,
            lastScreenAt: this.status.lastScreenAt,
            lastScreenResult: this.status.lastScreenResult,
            lastExecuteAt: this.status.lastExecuteAt,
            lastExecuteResult: this.status.lastExecuteResult,
          },
          null,
          2,
        ),
        "utf-8",
      );
    } catch {
      // Best-effort
    }
  }

  // ── Helpers ────────────────────────────────────────────────

  private defaultStartDate(): string {
    const d = new Date();
    d.setMonth(d.getMonth() - 12);
    return this.formatDate(d);
  }

  private formatDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  private notify(msg: string): void {
    if (this.notifyFn) {
      void this.notifyFn(msg);
    }
  }
}
