/**
 * PositionAlertService — 050248 (MW-2)
 *
 * 功能:
 *  - 02080705
 *  - 040710
 *  - 020802
 *  - 与 NotificationService + AlertStore 联动
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { AlertStore, AlertSeverity } from "./alert-store.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";

// ── Types ────────────────────────────────────────────────────

export interface PositionAlertConfig {
  enabled: boolean;
  /** 070105 (1901, 08 5) */
  priceMoveThreshold: number;
  /** 0407 (06, 08 5000) */
  dailyPnlThreshold: number;
  /** 单仓位占比上限 (1901, 08 30) */
  concentrationLimit: number;
  /** 检查间隔 (毫秒, 08 60000) */
  checkIntervalMs: number;
}

export interface PositionSnapshot {
  symbol: string;
  name?: string;
  quantity: number;
  costPrice: number;
  currentPrice: number;
  prevClosePrice: number;
  marketValue: number;
}

const DEFAULT_CONFIG: PositionAlertConfig = {
  enabled: true,
  priceMoveThreshold: 5,
  dailyPnlThreshold: 5000,
  concentrationLimit: 30,
  checkIntervalMs: 60_000,
};

// ── Service ──────────────────────────────────────────────────

export class PositionAlertService {
  private config: PositionAlertConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastAlerted = new Map<string, number>(); // symbol → last alert timestamp
  private readonly cooldownMs = 300_000; // 5 min cooldown per symbol

  constructor(
    private api: QuantTradingRuntime,
    private alertStore: AlertStore | null,
    private notificationService: NotificationService | null,
    config?: Partial<PositionAlertConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Start periodic monitoring */
  start(getPositions: () => PositionSnapshot[]): void {
    if (!this.config.enabled) return;
    this.stop();
    this.timer = setInterval(() => {
      void this.check(getPositions());
    }, this.config.checkIntervalMs);
    console.log("[PositionAlert] started");
  }

  /** Stop monitoring */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Update configuration */
  updateConfig(cfg: Partial<PositionAlertConfig>): void {
    Object.assign(this.config, cfg);
  }

  /** One-shot check */
  async check(positions: PositionSnapshot[]): Promise<void> {
    if (!positions.length) return;

    const totalMarketValue = positions.reduce((s, p) => s + p.marketValue, 0);

    for (const pos of positions) {
      const changePct = pos.prevClosePrice > 0
        ? ((pos.currentPrice - pos.prevClosePrice) / pos.prevClosePrice) * 100
        : 0;
      const dailyPnl = (pos.currentPrice - pos.prevClosePrice) * pos.quantity;
      const concentration = totalMarketValue > 0
        ? (pos.marketValue / totalMarketValue) * 100
        : 0;

      // Price move threshold
      if (Math.abs(changePct) >= this.config.priceMoveThreshold) {
        const dir = changePct > 0 ? "涨" : "跌";
        await this.emit(
          pos.symbol,
          Math.abs(changePct) >= this.config.priceMoveThreshold * 2 ? "critical" : "warning",
          `${pos.name ?? pos.symbol} 日内${dir}幅 ${changePct.toFixed(2)}%`,
          { symbol: pos.symbol, changePct, price: pos.currentPrice },
        );
      }

      // Daily PnL threshold
      if (Math.abs(dailyPnl) >= this.config.dailyPnlThreshold) {
        const dir = dailyPnl > 0 ? "07" : "亏损";
        await this.emit(
          `${pos.symbol}_pnl`,
          Math.abs(dailyPnl) >= this.config.dailyPnlThreshold * 2 ? "critical" : "warning",
          `${pos.name ?? pos.symbol} 当日浮动${dir} 06${Math.abs(dailyPnl).toFixed(2)}`,
          { symbol: pos.symbol, dailyPnl },
        );
      }

      // Concentration limit
      if (concentration >= this.config.concentrationLimit) {
        await this.emit(
          `${pos.symbol}_conc`,
          "warning",
          `${pos.name ?? pos.symbol} 0808 ${concentration.toFixed(1)}% 超过限制 ${this.config.concentrationLimit}%`,
          { symbol: pos.symbol, concentration },
        );
      }
    }
  }

  /** Emit an alert with cooldown */
  private async emit(
    key: string, severity: AlertSeverity,
    message: string, metadata: Record<string, unknown>,
  ): Promise<void> {
    const now = Date.now();
    const last = this.lastAlerted.get(key) ?? 0;
    if (now - last < this.cooldownMs) return;
    this.lastAlerted.set(key, now);

    // AlertStore
    this.alertStore?.add(severity, "pnl_monitor", message, metadata);

    // Notification push
    if (this.notificationService) {
      const msg: NotificationMessage = {
        title: "0248",
        content: message,
        severity,
        category: "alert",
      };
      void this.notificationService.send(msg);
    }

    console.log(`[PositionAlert] ${severity}: ${message}`);
  }
}
