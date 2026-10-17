/**
 * ReportScheduler — 报告自动推送调度器
 *
 * 0105:
 *  - DailyReviewService (RE-1 0704)
 *  - MorningBriefService (RE-3 盘前简报)
 *  - 08G02
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { QuantTradingConfig } from "../types.js";
import type { NotificationService } from "./notification-service.js";
import { DailyReviewService } from "./daily-review.js";
import { MorningBriefService } from "./morning-brief.js";
import type { ReportTemplate } from "./report-templates.js";

// ── Types ────────────────────────────────────────────────────

export interface ReportSchedulerConfig {
  enabled: boolean;
  dailyReview: {
    enabled: boolean;
    generateTime: string; // "HH:mm"
  };
  morningBrief: {
    enabled: boolean;
    generateTime: string; // "HH:mm?
    watchSymbols: string[];
  };
  monthlyReport: {
    enabled: boolean;
    dayOfMonth: number; // 1-28, default 1
    generateTime: string; // "HH:mm"
  };
}

const DEFAULT_CONFIG: ReportSchedulerConfig = {
  enabled: true,
  dailyReview: { enabled: true, generateTime: "15:30" },
  morningBrief: { enabled: true, generateTime: "08:30", watchSymbols: [] },
  monthlyReport: { enabled: true, dayOfMonth: 1, generateTime: "09:00" },
};

// ── Service ──────────────────────────────────────────────────

export class ReportScheduler {
  private config: ReportSchedulerConfig;
  private dailyReview: DailyReviewService;
  private morningBrief: MorningBriefService;
  private monthlyTimer: ReturnType<typeof setInterval> | null = null;
  private tradingConfig: QuantTradingConfig;
  private executeTool: ((name: string, args: Record<string, unknown>) => Promise<string>) | null;

  constructor(
    api: QuantTradingRuntime,
    tradingConfig: QuantTradingConfig,
    notificationService: NotificationService | null,
    config?: Partial<ReportSchedulerConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.tradingConfig = tradingConfig;
    this.executeTool = null;

    this.dailyReview = new DailyReviewService(api, tradingConfig, notificationService, {
      enabled: this.config.dailyReview.enabled,
      generateTime: this.config.dailyReview.generateTime,
    });

    this.morningBrief = new MorningBriefService(api, tradingConfig, notificationService, {
      enabled: this.config.morningBrief.enabled,
      generateTime: this.config.morningBrief.generateTime,
      watchSymbols: this.config.morningBrief.watchSymbols,
    });
  }

  /** Start all scheduled report generators */
  start(): void {
    if (!this.config.enabled) return;
    if (this.config.dailyReview.enabled) this.dailyReview.start();
    if (this.config.morningBrief.enabled) this.morningBrief.start();
    if (this.config.monthlyReport.enabled) this.startMonthlyScheduler();
  }

  /** Stop all scheduled generators */
  stop(): void {
    this.dailyReview.stop();
    this.morningBrief.stop();
    if (this.monthlyTimer) {
      clearInterval(this.monthlyTimer);
      this.monthlyTimer = null;
    }
  }

  /** Manually trigger daily review */
  async triggerDailyReview(): Promise<string> {
    return this.dailyReview.generateReport();
  }

  /** Manually trigger morning brief */
  async triggerMorningBrief(): Promise<string> {
    return this.morningBrief.generateBrief();
  }

  /** Update watch symbols for morning brief */
  setWatchSymbols(symbols: string[]): void {
    this.morningBrief.setWatchSymbols(symbols);
  }

  /** Get service instances for custom wiring */
  getDailyReviewService(): DailyReviewService {
    return this.dailyReview;
  }

  getMorningBriefService(): MorningBriefService {
    return this.morningBrief;
  }

  /** Manually trigger monthly report */
  async triggerMonthlyReport(): Promise<string> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod: any = await import("./report-templates.js");
    const templates = (mod.REPORT_TEMPLATES ?? []) as ReportTemplate[];
    const tpl = templates.find((t) => t.id === "monthly_report");
    if (!tpl) return "090600ā09";
    if (!this.executeTool) return "executeTool ā0000 setExecuteTool()";
    return tpl.generate(this.executeTool, this.tradingConfig);
  }

  /** Set the tool execution function (called from index.ts after tools are registered) */
  setExecuteTool(fn: (name: string, args: Record<string, unknown>) => Promise<string>): void {
    this.executeTool = fn;
  }

  private startMonthlyScheduler(): void {
    const { dayOfMonth, generateTime } = this.config.monthlyReport;
    const [h, m] = generateTime.split(":").map(Number);

    // Check every hour if it's time to generate
    this.monthlyTimer = setInterval(() => {
      const now = new Date();
      if (now.getDate() === dayOfMonth && now.getHours() === h && now.getMinutes() >= m && now.getMinutes() < m + 60) {
        this.triggerMonthlyReport().catch(() => {});
      }
    }, 60 * 60 * 1000);
  }
}
