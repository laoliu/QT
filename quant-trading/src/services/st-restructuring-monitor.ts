/**
 * STRestructuringMonitor — ST030215
 *
 * 功能:
 *  - 每个交易日14:30080917
 *  - 跟踪ST070000 → 正式重整 → 0405
 *  - 000004
 *  - 010609
 *  - 09160300
 *
 * 重整阶段:
 *  PRE-0 ~ PRE-6  0000050504070405
 *  MAIN-1 ~ MAIN-7 04040718060405
 *  REMOVED        0009/0005
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { QuantTradingConfig } from "../types.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import { tryNotify } from "./notify-helper.js";
import { todayCST } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────

export interface STMonitorConfig {
  /** Enable/disable the monitor (default true) */
  enabled: boolean;
  /** Auto-scan time on trading days — "HH:mm? (default ?14:30") */
  scanTime: string;
  /** Look back N days for announcements (default 30) */
  scanDays: number;
  /** Push notification on stage transitions (default true) */
  pushNotification: boolean;
}

export interface STRestructuringStock {
  code: string;
  name: string;
  current_stage: string;
  stage_label: string;
  days_in_process: number;
  risk_level: string;
  is_st: boolean;
  announcements: Array<{ title: string; date: string; url: string }>;
  stage_history: Array<{ stage: string; label: string; date: string; title: string }>;
}

export interface STScanResult {
  action: string;
  total: number;
  records: Record<string, STRestructuringStock>;
}

export interface STStatusResult {
  total_tracked: number;
  stage_summary: Record<string, number>;
  executing_plan: Array<{ code: string; name: string; days_in_process: number }>;
  completed: Array<{ code: string; name: string }>;
  risk_removed: Array<{ code: string; name: string }>;
}

const DEFAULT_CONFIG: STMonitorConfig = {
  enabled: true,
  scanTime: "14:30",
  scanDays: 30,
  pushNotification: true,
};

// ── Service ──────────────────────────────────────────────────

export class STRestructuringMonitor {
  private config: STMonitorConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastScanDate: string | null = null;
  private lastResult: STScanResult | null = null;
  private previousRecords: Record<string, STRestructuringStock> = {};

  constructor(
    private api: QuantTradingRuntime,
    private tradingConfig: QuantTradingConfig,
    private notificationService: NotificationService | null,
    config?: Partial<STMonitorConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Start the auto-scan scheduler — checks every minute if it's scan time */
  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    this.timer = setInterval(() => {
      void this.tryAutoScan();
    }, 60_000);
    console.log("[ST Monitor] started — scan at " + this.config.scanTime);
  }

  /** Stop the scheduler */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Check if it's time to run the daily scan */
  private async tryAutoScan(): Promise<void> {
    const now = new Date();
    const hhmm = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
    const today = now.toISOString().slice(0, 10);

    if (hhmm !== this.config.scanTime || this.lastScanDate === today) return;

    // Skip weekends
    const day = now.getDay();
    if (day === 0 || day === 6) return;

    this.lastScanDate = today;
    console.log(`[ST Monitor] Auto-scan triggered at ${hhmm}`);

    try {
      const result = await this.scan();
      const alerts = this.detectTransitions(result);

      if (alerts.length > 0) {
        const alertMsg = this.formatAlertMessage(alerts);
        await this.pushAlert(alertMsg);
      }

      // Summary notification
      const summary = this.formatSummary(result);
      await tryNotify(this.api, summary);
    } catch (err) {
      console.log(`[ST Monitor] Scan failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** Manually trigger a scan */
  async scan(days?: number): Promise<STScanResult> {
    const scanDays = days ?? this.config.scanDays;
    console.log(`[ST Monitor] Scanning ${scanDays} days of announcements...`);

    const result = await runPythonScript(
      "engines/st_restructuring.py",
      { action: "scan_all", days: scanDays },
      this.tradingConfig,
      { timeoutMs: 600_000 },
    );

    if (!result.ok) {
      throw new Error(result.error ?? "ST restructuring scan failed");
    }

    const scanResult = result.data as STScanResult;

    // Store for transition detection
    if (this.lastResult) {
      this.previousRecords = this.lastResult.records ?? {};
    }
    this.lastResult = scanResult;

    return scanResult;
  }

  /** Track a specific stock's restructuring timeline */
  async trackStock(symbol: string, days = 365): Promise<STRestructuringStock | null> {
    const result = await runPythonScript(
      "engines/st_restructuring.py",
      { action: "track_stock", symbol, days },
      this.tradingConfig,
    );
    if (!result.ok) return null;
    return (result.data as { record: STRestructuringStock }).record;
  }

  /** Get current tracking status */
  async getStatus(): Promise<STStatusResult | null> {
    const result = await runPythonScript(
      "engines/st_restructuring.py",
      { action: "status" },
      this.tradingConfig,
    );
    if (!result.ok) return null;
    return result.data as STStatusResult;
  }

  /** Get the last scan result */
  getLastResult(): STScanResult | null {
    return this.lastResult;
  }

  /** Detect stage transitions between scans */
  private detectTransitions(
    current: STScanResult,
  ): Array<{ code: string; name: string; oldStage: string; newStage: string; newLabel: string }> {
    const alerts: Array<{
      code: string;
      name: string;
      oldStage: string;
      newStage: string;
      newLabel: string;
    }> = [];

    for (const [code, record] of Object.entries(current.records ?? {})) {
      const prev = this.previousRecords[code];
      if (prev && prev.current_stage !== record.current_stage) {
        alerts.push({
          code,
          name: record.name,
          oldStage: prev.current_stage,
          newStage: record.current_stage,
          newLabel: record.stage_label,
        });
      }
    }

    return alerts;
  }

  /** Format alert message for stage transitions */
  private formatAlertMessage(
    alerts: Array<{ code: string; name: string; oldStage: string; newStage: string; newLabel: string }>,
  ): string {
    const lines = ["73 **ST00**\n"];
    for (const a of alerts) {
      lines.push(`- **${a.code} ${a.name}**: ${a.oldStage} → ${a.newStage} (${a.newLabel})`);
    }
    return lines.join("\n");
  }

  /** Format summary notification */
  private formatSummary(result: STScanResult): string {
    const records = Object.values(result.records ?? {});
    const executing = records.filter((r) => r.current_stage === "MAIN-6");
    const completed = records.filter((r) => r.current_stage === "MAIN-7");
    const removed = records.filter((r) => r.current_stage === "REMOVED");
    const preStage = records.filter((r) => r.current_stage.startsWith("PRE-"));

    const lines = [
      `96 **ST重整日报** (${todayCST()})`,
      `09 ${result.total} 0107`,
    ];

    if (executing.length > 0) {
      lines.push(`7515 04: ${executing.map((r) => `${r.code} ${r.name}`).join(", ")}`);
    }
    if (completed.length > 0) {
      lines.push(`73 已完成: ${completed.map((r) => `${r.code} ${r.name}`).join(", ")}`);
    }
    if (removed.length > 0) {
      lines.push(`95 0009: ${removed.map((r) => `${r.code} ${r.name}`).join(", ")}`);
    }
    if (preStage.length > 0) {
      lines.push(`77 00: ${preStage.length} 01`);
    }

    return lines.join("\n");
  }

  /** Push alert via notification service */
  private async pushAlert(message: string): Promise<void> {
    // Via API notification
    await tryNotify(this.api, message);

    // Via dedicated notification service
    if (this.config.pushNotification && this.notificationService) {
      const msg: NotificationMessage = {
        title: "ST00",
        content: message,
        severity: "warning",
        category: "alert",
        markdown: message,
      };
      await this.notificationService.send(msg).catch(() => {});
    }
  }
}
