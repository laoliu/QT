/**
 * EarningsAlertService — 0700 (MW-4)
 *
 * 功能:
 *  - 1102/05020407
 *  - 0900 (00/00/09/01) 提醒
 *  - 094148
 *  - 07080509150407
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { AlertStore } from "./alert-store.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";

// ── Types ────────────────────────────────────────────────────

export interface EarningsAlertConfig {
  enabled: boolean;
  /** 预警天数 — 0702N天提醒 (08 3) */
  daysAhead: number;
  /** 检查间隔 (毫秒, 08 3600000 = 102) */
  checkIntervalMs: number;
  /** 000107 */
  watchSymbols: string[];
}

export interface EarningsEvent {
  symbol: string;
  name: string;
  reportDate: string;  // YYYY-MM-DD
  type: string;  // 00/00/09/01/略增/04/续亏/07/02
  description: string;
}

const DEFAULT_CONFIG: EarningsAlertConfig = {
  enabled: true,
  daysAhead: 3,
  checkIntervalMs: 3_600_000,
  watchSymbols: [],
};

// ── Service ──────────────────────────────────────────────────

export class EarningsAlertService {
  private config: EarningsAlertConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private alertedKeys = new Set<string>();

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private alertStore: AlertStore | null,
    private notificationService: NotificationService | null,
    config?: Partial<EarningsAlertConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Set watch symbols (e.g. from portfolio) */
  setWatchSymbols(symbols: string[]): void {
    this.config.watchSymbols = [...symbols];
  }

  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    // Initial check
    void this.check();
    this.timer = setInterval(() => {
      void this.check();
    }, this.config.checkIntervalMs);
    console.log("[EarningsAlert] started");
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Check for upcoming earnings events */
  async check(): Promise<EarningsEvent[]> {
    if (this.config.watchSymbols.length === 0) return [];

    const symbolList = JSON.stringify(this.config.watchSymbols);
    const script = `
import akshare as ak
import json, sys
from datetime import datetime, timedelta

symbols = ${symbolList}
days_ahead = ${this.config.daysAhead}

events = []
today = datetime.now()
cutoff = today + timedelta(days=days_ahead)

try:
    # 0900
    try:
        df = ak.stock_yjyg_em(date=today.strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                code = str(row.get("股票代码", "")).strip()
                if code in symbols:
                    events.append({
                        "symbol": code,
                        "name": str(row.get("股票简称", "")),
                        "reportDate": str(row.get("公告日期", ""))[:10],
                        "type": str(row.get("预告类型", "")),
                        "description": str(row.get("095209", ""))[:200],
                    })
    except:
        pass

    # 0941
    try:
        df2 = ak.stock_yjkb_em(date=today.strftime("%Y%m%d"))
        if df2 is not None and len(df2) > 0:
            for _, row in df2.iterrows():
                code = str(row.get("股票代码", "")).strip()
                if code in symbols:
                    events.append({
                        "symbol": code,
                        "name": str(row.get("股票简称", "")),
                        "reportDate": str(row.get("公告日期", ""))[:10],
                        "type": "41",
                        "description": f"02: {row.get('0209', '')}  净利润: {row.get('净利润', '')}",
                    })
    except:
        pass

    print(json.dumps({"events": events}, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"events": [], "error": str(e)}))
`;

    try {
      const result = await runInlinePython<{ events: EarningsEvent[] }>(script, this.tradingConfig);
      const events: EarningsEvent[] = result.data?.events ?? [];

      for (const ev of events) {
        const key = `${ev.symbol}_${ev.reportDate}_${ev.type}`;
        if (this.alertedKeys.has(key)) continue;
        this.alertedKeys.add(key);

        const severity = ev.type === "09" || ev.type === "00" ? "warning" as const : "info" as const;
        const message = `[0700] ${ev.name}(${ev.symbol}) ${ev.type} — ${ev.reportDate} — ${ev.description}`;

        this.alertStore?.add(severity, "strategy", message, ev as unknown as Record<string, unknown>);

        if (this.notificationService) {
          const msg: NotificationMessage = {
            title: "0700",
            content: message,
            severity,
            category: "alert",
          };
          void this.notificationService.send(msg);
        }

        console.log(`[EarningsAlert] ${severity}: ${message}`);
      }

      return events;
    } catch {
      return [];
    }
  }
}
