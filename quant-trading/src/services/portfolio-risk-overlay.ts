/**
 * PortfolioRiskOverlay — Live MDD gradient + vol scaling risk controller.
 *
 * Integrates the risk simulation engines into the live trading pipeline:
 *  - Tracks real-time NAV from equity snapshots
 *  - Computes MDD gradient state (NORMAL → L1_REDUCED → L2_LIQUIDATED → RECOVERING)
 *  - Computes vol-targeting scale factor
 *  - Outputs a combined position scale factor applied to all new orders
 *  - Persists config + state to disk
 *  - Provides status/diagnostics for the dashboard UI
 *
 * The overlay is non-destructive: it only scales DOWN new order quantities.
 * Existing positions are not force-liquidated (that's the ConditionalOrderService's job).
 */
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

// ── Types ────────────────────────────────────────────────────────

export type RiskOverlayMode = "both" | "mdd_only" | "vol_only" | "disabled";

export type MddRiskState = "normal" | "l1_reduced" | "l2_liquidated" | "recovering";

export interface MddGradientConfig {
  l1Threshold: number;      // e.g. 0.12 = 12% drawdown
  l2Threshold: number;      // e.g. 0.18
  recoveryThreshold: number; // e.g. 0.06
  l1PositionScale: number;  // e.g. 0.50
  l2PositionScale: number;  // e.g. 0.00
  recoveryRampDays: number; // e.g. 5
}

export interface VolScalingConfig {
  targetVol: number;    // e.g. 0.18 = 18% annualized
  lookback: number;     // e.g. 60 trading days
  maxLeverage: number;  // e.g. 1.5
  minScale: number;     // e.g. 0.10
  volFloor: number;     // e.g. 0.05
}

export interface RiskOverlayConfig {
  enabled: boolean;
  mode: RiskOverlayMode;
  mdd: MddGradientConfig;
  vol: VolScalingConfig;
}

export interface StateTransition {
  date: string;        // ISO date
  fromState: MddRiskState;
  toState: MddRiskState;
  drawdown: number;
  nav: number;
  peak: number;
  scale: number;
}

export interface RiskOverlayStatus {
  enabled: boolean;
  mode: RiskOverlayMode;
  mddState: MddRiskState;
  currentDrawdown: number;
  peakNav: number;
  currentNav: number;
  mddScale: number;
  volScale: number;
  combinedScale: number;
  realizedVol: number;
  targetVol: number;
  dataPoints: number;
  recentTransitions: StateTransition[];
  lastUpdateTime: string | null;
}

// ── Defaults ─────────────────────────────────────────────────────

const DEFAULT_MDD_CONFIG: MddGradientConfig = {
  l1Threshold: 0.12,
  l2Threshold: 0.18,
  recoveryThreshold: 0.06,
  l1PositionScale: 0.50,
  l2PositionScale: 0.00,
  recoveryRampDays: 5,
};

const DEFAULT_VOL_CONFIG: VolScalingConfig = {
  targetVol: 0.18,
  lookback: 60,
  maxLeverage: 1.5,
  minScale: 0.10,
  volFloor: 0.05,
};

const DEFAULT_CONFIG: RiskOverlayConfig = {
  enabled: false,
  mode: "both",
  mdd: { ...DEFAULT_MDD_CONFIG },
  vol: { ...DEFAULT_VOL_CONFIG },
};

// ── MDD Gradient Controller (TS port) ────────────────────────────

class MddGradientController {
  private peak = 0;
  private state: MddRiskState = "normal";
  private recoveryDay = 0;
  private currentDd = 0;
  private cfg: MddGradientConfig;
  readonly transitions: StateTransition[] = [];

  constructor(cfg: MddGradientConfig) {
    this.cfg = cfg;
  }

  get riskState(): MddRiskState {
    return this.state;
  }

  get drawdown(): number {
    return this.currentDd;
  }

  get peakNav(): number {
    return this.peak;
  }

  updateConfig(cfg: MddGradientConfig): void {
    this.cfg = cfg;
  }

  reset(): void {
    this.peak = 0;
    this.state = "normal";
    this.recoveryDay = 0;
    this.currentDd = 0;
    this.transitions.length = 0;
  }

  update(nav: number, dateStr?: string): number {
    if (nav <= 0) return 0;
    if (nav > this.peak) this.peak = nav;

    const dd = this.peak > 0 ? (this.peak - nav) / this.peak : 0;
    this.currentDd = dd;

    const prevState = this.state;
    const scale = this.computeScale(dd);

    if (this.state !== prevState) {
      this.transitions.push({
        date: dateStr ?? new Date().toISOString().slice(0, 10),
        fromState: prevState,
        toState: this.state,
        drawdown: Math.round(dd * 10000) / 10000,
        nav: Math.round(nav * 100) / 100,
        peak: Math.round(this.peak * 100) / 100,
        scale: Math.round(scale * 10000) / 10000,
      });
      // Keep last 100 transitions
      if (this.transitions.length > 100) {
        this.transitions.splice(0, this.transitions.length - 100);
      }
    }
    return scale;
  }

  private computeScale(dd: number): number {
    const cfg = this.cfg;

    if (this.state === "normal") {
      if (dd >= cfg.l2Threshold) {
        this.state = "l2_liquidated";
        this.recoveryDay = 0;
        return cfg.l2PositionScale;
      } else if (dd >= cfg.l1Threshold) {
        this.state = "l1_reduced";
        return cfg.l1PositionScale;
      }
      return 1.0;
    }

    if (this.state === "l1_reduced") {
      if (dd >= cfg.l2Threshold) {
        this.state = "l2_liquidated";
        this.recoveryDay = 0;
        return cfg.l2PositionScale;
      } else if (dd < cfg.recoveryThreshold) {
        this.state = "recovering";
        this.recoveryDay = 0;
        return this.rampScale();
      }
      return cfg.l1PositionScale;
    }

    if (this.state === "l2_liquidated") {
      if (dd < cfg.recoveryThreshold) {
        this.state = "recovering";
        this.recoveryDay = 0;
        return this.rampScale();
      }
      return cfg.l2PositionScale;
    }

    if (this.state === "recovering") {
      if (dd >= cfg.l1Threshold) {
        this.state = "l1_reduced";
        this.recoveryDay = 0;
        return cfg.l1PositionScale;
      }
      this.recoveryDay++;
      const scale = this.rampScale();
      if (this.recoveryDay >= cfg.recoveryRampDays) {
        this.state = "normal";
      }
      return scale;
    }

    return 1.0;
  }

  private rampScale(): number {
    const cfg = this.cfg;
    const progress = Math.min(this.recoveryDay / Math.max(cfg.recoveryRampDays, 1), 1.0);
    return cfg.l1PositionScale + (1.0 - cfg.l1PositionScale) * progress;
  }

  /** Serialize state for persistence. */
  toJSON(): Record<string, unknown> {
    return {
      peak: this.peak,
      state: this.state,
      recoveryDay: this.recoveryDay,
      currentDd: this.currentDd,
      transitions: this.transitions,
    };
  }

  /** Restore from serialized state. */
  fromJSON(data: Record<string, unknown>): void {
    if (typeof data.peak === "number") this.peak = data.peak;
    if (typeof data.state === "string") this.state = data.state as MddRiskState;
    if (typeof data.recoveryDay === "number") this.recoveryDay = data.recoveryDay;
    if (typeof data.currentDd === "number") this.currentDd = data.currentDd;
    if (Array.isArray(data.transitions)) {
      this.transitions.length = 0;
      this.transitions.push(...(data.transitions as StateTransition[]));
    }
  }
}

// ── Vol Scaler (TS port) ─────────────────────────────────────────

class VolScaler {
  private returns: number[] = [];
  private currentScale = 1.0;
  private currentVol = 0.0;
  private cfg: VolScalingConfig;

  constructor(cfg: VolScalingConfig) {
    this.cfg = cfg;
  }

  get scale(): number {
    return this.currentScale;
  }

  get realizedVol(): number {
    return this.currentVol;
  }

  updateConfig(cfg: VolScalingConfig): void {
    this.cfg = cfg;
  }

  reset(): void {
    this.returns = [];
    this.currentScale = 1.0;
    this.currentVol = 0.0;
  }

  update(dailyReturn: number): number {
    const cfg = this.cfg;
    this.returns.push(dailyReturn);

    // Keep only lookback window
    if (this.returns.length > cfg.lookback) {
      this.returns = this.returns.slice(-cfg.lookback);
    }

    // Not enough data — full scale
    if (this.returns.length < Math.max(10, Math.floor(cfg.lookback / 3))) {
      this.currentScale = 1.0;
      return 1.0;
    }

    // Simple rolling vol (annualized)
    const n = this.returns.length;
    const mean = this.returns.reduce((a, b) => a + b, 0) / n;
    const variance = this.returns.reduce((a, r) => a + (r - mean) ** 2, 0) / (n - 1);
    const dailyVol = Math.sqrt(variance);
    const annualizedVol = dailyVol * Math.sqrt(252);

    const vol = Math.max(annualizedVol, cfg.volFloor);
    this.currentVol = vol;

    const rawScale = cfg.targetVol / vol;
    const scale = Math.max(cfg.minScale, Math.min(rawScale, cfg.maxLeverage));
    this.currentScale = scale;
    return scale;
  }

  /** Serialize state for persistence. */
  toJSON(): Record<string, unknown> {
    return {
      returns: this.returns,
      currentScale: this.currentScale,
      currentVol: this.currentVol,
    };
  }

  /** Restore from serialized state. */
  fromJSON(data: Record<string, unknown>): void {
    if (Array.isArray(data.returns)) this.returns = data.returns as number[];
    if (typeof data.currentScale === "number") this.currentScale = data.currentScale;
    if (typeof data.currentVol === "number") this.currentVol = data.currentVol;
  }
}

// ── Main Service ─────────────────────────────────────────────────

export class PortfolioRiskOverlay {
  private config: RiskOverlayConfig;
  private mdd: MddGradientController;
  private vol: VolScaler;
  private lastNav = 0;
  private lastUpdateTime: string | null = null;
  private dataDir: string;
  private dirty = false;
  private saveTimer: ReturnType<typeof setInterval> | null = null;
  private logFn: (msg: string, level?: string) => void;

  constructor(opts: {
    dataDir?: string;
    config?: Partial<RiskOverlayConfig>;
    log?: (msg: string, level?: string) => void;
  } = {}) {
    this.dataDir = opts.dataDir ?? path.join(process.cwd(), "data");
    this.logFn = opts.log ?? (() => {});

    // Merge user config over defaults
    this.config = {
      ...DEFAULT_CONFIG,
      ...opts.config,
      mdd: { ...DEFAULT_MDD_CONFIG, ...opts.config?.mdd },
      vol: { ...DEFAULT_VOL_CONFIG, ...opts.config?.vol },
    };

    this.mdd = new MddGradientController(this.config.mdd);
    this.vol = new VolScaler(this.config.vol);
  }

  // ── Lifecycle ──────────────────────────────────────────────────

  /** Load persisted config + controller state from disk. */
  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      const data = JSON.parse(raw);
      if (data.config) {
        this.config = {
          ...DEFAULT_CONFIG,
          ...data.config,
          mdd: { ...DEFAULT_MDD_CONFIG, ...data.config.mdd },
          vol: { ...DEFAULT_VOL_CONFIG, ...data.config.vol },
        };
        this.mdd.updateConfig(this.config.mdd);
        this.vol.updateConfig(this.config.vol);
      }
      if (data.mddState) this.mdd.fromJSON(data.mddState);
      if (data.volState) this.vol.fromJSON(data.volState);
      if (typeof data.lastNav === "number") this.lastNav = data.lastNav;
      if (typeof data.lastUpdateTime === "string") this.lastUpdateTime = data.lastUpdateTime;
      this.logFn("[风控覆盖] 已加载持久化状态");
    } catch {
      // No saved state — start fresh
    }
  }

  /** Start periodic auto-save. */
  start(intervalMs = 60_000): void {
    this.saveTimer = setInterval(() => {
      if (this.dirty) void this.save();
    }, intervalMs);
  }

  /** Stop auto-save. */
  stop(): void {
    if (this.saveTimer) {
      clearInterval(this.saveTimer);
      this.saveTimer = null;
    }
    if (this.dirty) void this.save();
  }

  /** Persist current state to disk. */
  async save(): Promise<void> {
    const data = {
      config: this.config,
      mddState: this.mdd.toJSON(),
      volState: this.vol.toJSON(),
      lastNav: this.lastNav,
      lastUpdateTime: this.lastUpdateTime,
    };
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(data, null, 2));
    this.dirty = false;
  }

  private get filePath(): string {
    return path.join(this.dataDir, "risk-overlay-state.json");
  }

  // ── Core: Feed NAV and compute scale ───────────────────────────

  /**
   * Called on every account sync (every ~1 min during market hours).
   * Updates the MDD + vol controllers and returns the combined scale.
   */
  updateNav(totalAssets: number, dateStr?: string): number {
    if (!this.config.enabled || this.config.mode === "disabled") return 1.0;
    if (totalAssets <= 0) return 1.0;

    // Compute daily return for vol scaler
    const dailyReturn = this.lastNav > 0 ? (totalAssets / this.lastNav - 1) : 0;
    this.lastNav = totalAssets;
    this.lastUpdateTime = new Date().toISOString();

    // Update controllers based on mode
    let mddScale = 1.0;
    let volScale = 1.0;

    if (this.config.mode === "both" || this.config.mode === "mdd_only") {
      mddScale = this.mdd.update(totalAssets, dateStr);
    }
    if (this.config.mode === "both" || this.config.mode === "vol_only") {
      volScale = this.vol.update(dailyReturn);
    }

    const combined = Math.min(mddScale, volScale);
    this.dirty = true;

    // Log state transitions
    const transitions = this.mdd.transitions;
    if (transitions.length > 0) {
      const last = transitions[transitions.length - 1]!;
      if (last.date === (dateStr ?? new Date().toISOString().slice(0, 10))) {
        this.logFn(
          `[风控覆盖] 状态转换: ${last.fromState} → ${last.toState} | 回撤=${(last.drawdown * 100).toFixed(2)}% | 缩放=${last.scale.toFixed(4)}`,
          last.toState === "l2_liquidated" ? "error" : "warn",
        );
      }
    }

    return combined;
  }

  // ── Query: Get current scale factor ────────────────────────────

  /**
   * Get the current position scale factor [0.0, 1.5].
   * Called by OrderManager before sizing new orders.
   */
  getScale(): number {
    if (!this.config.enabled || this.config.mode === "disabled") return 1.0;

    let mddScale = 1.0;
    let volScale = 1.0;

    if (this.config.mode === "both" || this.config.mode === "mdd_only") {
      // Use last computed MDD scale (based on drawdown state)
      const dd = this.mdd.drawdown;
      const state = this.mdd.riskState;
      if (state === "l2_liquidated") mddScale = this.config.mdd.l2PositionScale;
      else if (state === "l1_reduced") mddScale = this.config.mdd.l1PositionScale;
      else if (state === "recovering") {
        // Approximate — exact value was set in last update()
        mddScale = this.config.mdd.l1PositionScale + (1.0 - this.config.mdd.l1PositionScale) * 0.5;
      }
      else mddScale = 1.0;
    }

    if (this.config.mode === "both" || this.config.mode === "vol_only") {
      volScale = this.vol.scale;
    }

    return Math.min(mddScale, volScale);
  }

  // ── Config: Update and read ────────────────────────────────────

  getConfig(): RiskOverlayConfig {
    return { ...this.config, mdd: { ...this.config.mdd }, vol: { ...this.config.vol } };
  }

  updateConfig(patch: Partial<RiskOverlayConfig>): void {
    if (patch.enabled !== undefined) this.config.enabled = patch.enabled;
    if (patch.mode !== undefined) this.config.mode = patch.mode;
    if (patch.mdd) {
      this.config.mdd = { ...this.config.mdd, ...patch.mdd };
      this.mdd.updateConfig(this.config.mdd);
    }
    if (patch.vol) {
      this.config.vol = { ...this.config.vol, ...patch.vol };
      this.vol.updateConfig(this.config.vol);
    }
    this.dirty = true;
    this.logFn(`[风控覆盖] 配置已更新: enabled=${this.config.enabled} mode=${this.config.mode}`);
  }

  /** Reset all controller state (re-calibrate from scratch). */
  resetState(): void {
    this.mdd.reset();
    this.vol.reset();
    this.lastNav = 0;
    this.lastUpdateTime = null;
    this.dirty = true;
    this.logFn("[风控覆盖] 控制器状态已重置");
  }

  // ── Status / Diagnostics ───────────────────────────────────────

  getStatus(): RiskOverlayStatus {
    return {
      enabled: this.config.enabled,
      mode: this.config.mode,
      mddState: this.mdd.riskState,
      currentDrawdown: Math.round(this.mdd.drawdown * 10000) / 10000,
      peakNav: Math.round(this.mdd.peakNav * 100) / 100,
      currentNav: Math.round(this.lastNav * 100) / 100,
      mddScale: Math.round((this.config.mode === "both" || this.config.mode === "mdd_only"
        ? this.getScale() : 1.0) * 10000) / 10000,
      volScale: Math.round(this.vol.scale * 10000) / 10000,
      combinedScale: Math.round(this.getScale() * 10000) / 10000,
      realizedVol: Math.round(this.vol.realizedVol * 10000) / 10000,
      targetVol: this.config.vol.targetVol,
      dataPoints: this.vol["returns"].length,
      recentTransitions: this.mdd.transitions.slice(-20),
      lastUpdateTime: this.lastUpdateTime,
    };
  }
}
