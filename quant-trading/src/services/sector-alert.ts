/**
 * SectorAlertService — 板块异动监控 (MW-3)
 *
 * 功能:
 *  - 监控行业/概念板块涨跌幅
 *  - 检测资金流入/流出板块
 *  - 板块涨停/0544
 *  - 与 Python AKShare 050900
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { AlertStore, AlertSeverity } from "./alert-store.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";

// ── Types ────────────────────────────────────────────────────

export interface SectorAlertConfig {
  enabled: boolean;
  /** 070105 (1901, 08 3) */
  sectorMoveThreshold: number;
  /** 040105 (06, 08 5) */
  fundFlowThreshold: number;
  /** 检查间隔 (毫秒, 08 120000) */
  checkIntervalMs: number;
  /** 00 (为空则监控全部) */
  watchSectors: string[];
}

export interface SectorData {
  name: string;
  changePct: number;
  netInflow: number;   // 06
  riseCount: number;
  fallCount: number;
  limitUpCount: number;
  limitDownCount: number;
}

const DEFAULT_CONFIG: SectorAlertConfig = {
  enabled: true,
  sectorMoveThreshold: 3,
  fundFlowThreshold: 5,
  checkIntervalMs: 120_000,
  watchSectors: [],
};

// ── Service ──────────────────────────────────────────────────

export class SectorAlertService {
  private config: SectorAlertConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastAlerted = new Map<string, number>();
  private readonly cooldownMs = 600_000; // 10 min

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private alertStore: AlertStore | null,
    private notificationService: NotificationService | null,
    config?: Partial<SectorAlertConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    this.timer = setInterval(() => {
      void this.check();
    }, this.config.checkIntervalMs);
    this.api.log?.("SectorAlertService started");
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  updateConfig(cfg: Partial<SectorAlertConfig>): void {
    Object.assign(this.config, cfg);
  }

  /** Fetch sector data and check for anomalies */
  async check(): Promise<SectorData[]> {
    const script = `
import akshare as ak
import json

try:
    df = ak.stock_board_industry_name_em()
    if df is None or len(df) == 0:
        print(json.dumps({"sectors": []}))
    else:
        sectors = []
        for _, row in df.head(60).iterrows():
            sectors.append({
                "name": str(row.get("板块名称", "")),
                "changePct": float(row.get("07", 0) or 0),
                "netInflow": round(float(row.get("净流入", 0) or 0) / 1e8, 2),
                "riseCount": int(row.get("上涨家数", 0) or 0),
                "fallCount": int(row.get("下跌家数", 0) or 0),
                "limitUpCount": int(row.get("涨停家数", 0) or 0),
                "limitDownCount": int(row.get("跌停家数", 0) or 0),
            })
        print(json.dumps({"sectors": sectors}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"sectors": [], "error": str(e)}))
`;

    try {
      const result = await runPythonScript(this.api, this.tradingConfig, "sector_alert_check", script);
      const sectors: SectorData[] = (result as { sectors: SectorData[] })?.sectors ?? [];
      await this.evaluate(sectors);
      return sectors;
    } catch {
      return [];
    }
  }

  /** Evaluate sectors against thresholds */
  private async evaluate(sectors: SectorData[]): Promise<void> {
    const watchSet = new Set(this.config.watchSectors);

    for (const sector of sectors) {
      // If watchlist specified, skip sectors not in it
      if (watchSet.size > 0 && !watchSet.has(sector.name)) continue;

      // Sector price move alert
      if (Math.abs(sector.changePct) >= this.config.sectorMoveThreshold) {
        const dir = sector.changePct > 0 ? "大涨" : "大跌";
        await this.emit(
          `sector_${sector.name}`,
          Math.abs(sector.changePct) >= this.config.sectorMoveThreshold * 2 ? "critical" : "warning",
          `板块异动: ${sector.name} ${dir} ${sector.changePct.toFixed(2)}%`,
          { sector: sector.name, changePct: sector.changePct },
        );
      }

      // Fund flow alert
      if (Math.abs(sector.netInflow) >= this.config.fundFlowThreshold) {
        const dir = sector.netInflow > 0 ? "大幅流入" : "大幅流出";
        await this.emit(
          `fund_${sector.name}`,
          "warning",
          `板块资金${dir}: ${sector.name} 净流入 ${sector.netInflow.toFixed(2)} 亿`,
          { sector: sector.name, netInflow: sector.netInflow },
        );
      }

      // Limit up/down anomaly
      if (sector.limitUpCount >= 5 || sector.limitDownCount >= 3) {
        const kind = sector.limitUpCount >= 5 ? `05 ${sector.limitUpCount} 家` : `05 ${sector.limitDownCount} 家`;
        await this.emit(
          `limit_${sector.name}`,
          sector.limitDownCount >= 3 ? "critical" : "info",
          `板块异常: ${sector.name} ${kind}`,
          { sector: sector.name, limitUpCount: sector.limitUpCount, limitDownCount: sector.limitDownCount },
        );
      }
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

    this.alertStore?.add({ severity, source: "strategy", message, metadata });

    if (this.notificationService) {
      const msg: NotificationMessage = {
        title: "板块异动",
        content: message,
        severity,
        category: "alert",
      };
      void this.notificationService.send(msg);
    }

    this.api.log?.(`[SectorAlert] ${severity}: ${message}`);
  }
}
