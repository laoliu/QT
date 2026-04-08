/**
 * MarketAnomalyService — 大盘急跌/急涨预警 (MW-8)
 *
 * 功能:
 *  - 06021208 (080809) 13
 *  - 020705
 *  - 05080505
 *  - 连接 NotificationService 推送
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { AlertStore, AlertSeverity } from "./alert-store.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";

// ── Types ────────────────────────────────────────────────────

export interface MarketAnomalyConfig {
  enabled: boolean;
  /** 080105 (1901, 08 -2) */
  dropThreshold: number;
  /** 08000105 (1901, 08 2) */
  surgeThreshold: number;
  /** 0918020805 */
  autoPauseOnDrop: boolean;
  /** 检查间隔 (毫秒, 08 30000) */
  checkIntervalMs: number;
}

interface IndexSnapshot {
  code: string;
  name: string;
  currentPrice: number;
  changePct: number;
}

const DEFAULT_CONFIG: MarketAnomalyConfig = {
  enabled: true,
  dropThreshold: -2,
  surgeThreshold: 2,
  autoPauseOnDrop: false,
  checkIntervalMs: 30_000,
};

// ── Service ──────────────────────────────────────────────────

export class MarketAnomalyService {
  private config: MarketAnomalyConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastAlerted = new Map<string, number>();
  private readonly cooldownMs = 600_000; // 10 min
  private onPauseCallback: (() => void) | null = null;

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private alertStore: AlertStore | null,
    private notificationService: NotificationService | null,
    config?: Partial<MarketAnomalyConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Set callback for auto-pause */
  onPause(cb: () => void): void {
    this.onPauseCallback = cb;
  }

  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    this.timer = setInterval(() => {
      void this.check();
    }, this.config.checkIntervalMs);
    this.api.log?.("MarketAnomalyService started");
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  updateConfig(cfg: Partial<MarketAnomalyConfig>): void {
    Object.assign(this.config, cfg);
  }

  /** Check index status */
  async check(): Promise<IndexSnapshot[]> {
    const script = `
import akshare as ak
import json

try:
    df = ak.stock_zh_index_spot_em()
    if df is None or len(df) == 0:
        print(json.dumps({"indices": []}))
    else:
        indices = []
        for code in ["000001", "399001", "399006"]:
            rows = df[df["代码"] == code]
            if len(rows) > 0:
                row = rows.iloc[0]
                indices.append({
                    "code": code,
                    "name": str(row.get("名称", "")),
                    "currentPrice": round(float(row.get("04", 0) or 0), 2),
                    "changePct": round(float(row.get("07", 0) or 0), 2),
                })
        print(json.dumps({"indices": indices}, ensure_ascii=False))

except Exception as e:
    print(json.dumps({"indices": [], "error": str(e)}))
`;

    try {
      const result = await runPythonScript(this.api, this.tradingConfig, "market_anomaly_check", script);
      const indices: IndexSnapshot[] = (result as { indices: IndexSnapshot[] })?.indices ?? [];
      await this.evaluate(indices);
      return indices;
    } catch {
      return [];
    }
  }

  private async evaluate(indices: IndexSnapshot[]): Promise<void> {
    let triggeredDrop = false;

    for (const idx of indices) {
      // Drop alert
      if (idx.changePct <= this.config.dropThreshold) {
        triggeredDrop = true;
        await this.emit(
          `drop_${idx.code}`,
          idx.changePct <= this.config.dropThreshold * 2 ? "critical" : "warning",
          `大盘急跌: ${idx.name} 跌幅 ${idx.changePct.toFixed(2)}% 02 ${idx.currentPrice}`,
          { code: idx.code, name: idx.name, changePct: idx.changePct },
        );
      }

      // Surge alert
      if (idx.changePct >= this.config.surgeThreshold) {
        await this.emit(
          `surge_${idx.code}`,
          "info",
          `大盘急涨: ${idx.name} 09 ${idx.changePct.toFixed(2)}% 02 ${idx.currentPrice}`,
          { code: idx.code, name: idx.name, changePct: idx.changePct },
        );
      }
    }

    // Auto-pause on market drop
    if (triggeredDrop && this.config.autoPauseOnDrop && this.onPauseCallback) {
      this.api.log?.("[MarketAnomaly] Triggering auto-pause due to market drop");
      this.onPauseCallback();
    }
  }

  private async emit(
    key: string, severity: AlertSeverity,
    message: string, metadata: Record<string, unknown>,
  ): Promise<void> {
    const now = Date.now();
    const last = this.lastAlerted.get(key) ?? 0;
    if (now - last < this.cooldownMs) return;
    this.lastAlerted.set(key, now);

    this.alertStore?.add({ severity, source: "system", message, metadata });

    if (this.notificationService) {
      const msg: NotificationMessage = {
        title: "4800",
        content: message,
        severity,
        category: "alert",
      };
      void this.notificationService.send(msg);
    }

    this.api.log?.(`[MarketAnomaly] ${severity}: ${message}`);
  }
}
