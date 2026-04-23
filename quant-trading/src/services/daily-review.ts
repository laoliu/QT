/**
 * DailyReviewService — 070405 (RE-1)
 *
 * 功能:
 *  - 0704080505
 *  - 包含: 02020709051705040500
 *  - Markdown 格式输出
 *  - 可推送至通知渠道
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { todayCST } from "../utils/cst-time.js";

// ── Types ────────────────────────────────────────────────────

export interface DailyReviewConfig {
  enabled: boolean;
  /** 0802 (HH:mm, 08 "15:30") */
  generateTime: string;
  /** 0704 */
  outputDir: string;
  /** 090004 */
  pushNotification: boolean;
}

export interface DailyReviewData {
  date: string;
  market: {
    shIndex: { close: number; changePct: number };
    szIndex: { close: number; changePct: number };
    cyIndex: { close: number; changePct: number };
    advancers: number;
    decliners: number;
    limitUp: number;
    limitDown: number;
    totalVolume: number; // 06
  };
  portfolio: {
    totalValue: number;
    dailyPnl: number;
    dailyPnlPct: number;
    positions: Array<{
      symbol: string;
      name: string;
      changePct: number;
      pnl: number;
    }>;
  };
  trades: Array<{
    symbol: string;
    name: string;
    side: string;
    price: number;
    quantity: number;
    time: string;
  }>;
  notes: string[];
}

const DEFAULT_CONFIG: DailyReviewConfig = {
  enabled: true,
  generateTime: "15:30",
  outputDir: "",
  pushNotification: true,
};

// ── Service ──────────────────────────────────────────────────

export class DailyReviewService {
  private config: DailyReviewConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastGenDate: string | null = null;

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private notificationService: NotificationService | null,
    config?: Partial<DailyReviewConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    if (!this.config.outputDir && tradingConfig.dataDir) {
      this.config.outputDir = join(tradingConfig.dataDir, "reports");
    }
  }

  /** Start auto-generation scheduler */
  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    // Check every minute
    this.timer = setInterval(() => {
      void this.tryAutoGenerate();
    }, 60_000);
    console.log("[DailyReview] started");
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async tryAutoGenerate(): Promise<void> {
    const now = new Date();
    const hhmm = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;
    const today = now.toISOString().slice(0, 10);

    if (hhmm === this.config.generateTime && this.lastGenDate !== today) {
      // Only on weekdays
      const day = now.getDay();
      if (day === 0 || day === 6) return;

      this.lastGenDate = today;
      await this.generateReport();
    }
  }

  /** Generate daily review report */
  async generateReport(
    getPortfolio?: () => DailyReviewData["portfolio"],
    getTrades?: () => DailyReviewData["trades"],
  ): Promise<string> {
    // Fetch market data
    const marketData = await this.fetchMarketOverview();
    const portfolio = getPortfolio?.() ?? { totalValue: 0, dailyPnl: 0, dailyPnlPct: 0, positions: [] };
    const trades = getTrades?.() ?? [];

    const date = todayCST();
    const report = this.formatReport(date, marketData, portfolio, trades);

    // Save to file
    if (this.config.outputDir) {
      if (!existsSync(this.config.outputDir)) {
        mkdirSync(this.config.outputDir, { recursive: true });
      }
      const filePath = join(this.config.outputDir, `daily-review-${date}.md`);
      writeFileSync(filePath, report, "utf-8");
      console.log(`[DailyReview] saved to ${filePath}`);
    }

    // Push notification
    if (this.config.pushNotification && this.notificationService) {
      const msg: NotificationMessage = {
        title: `0704 ${date}`,
        content: report.slice(0, 500),
        severity: "info",
        category: "report",
        markdown: report,
      };
      void this.notificationService.send(msg);
    }

    return report;
  }

  /** Fetch market overview data */
  private async fetchMarketOverview(): Promise<DailyReviewData["market"]> {
    const script = `
import akshare as ak
import json

try:
    # Major indices
    sh = ak.stock_zh_index_spot_em()
    sh_row = sh[sh["代码"] == "000001"].iloc[0] if sh is not None and len(sh[sh["代码"] == "000001"]) > 0 else None
    sz_row = sh[sh["代码"] == "399001"].iloc[0] if sh is not None and len(sh[sh["代码"] == "399001"]) > 0 else None
    cy_row = sh[sh["代码"] == "399006"].iloc[0] if sh is not None and len(sh[sh["代码"] == "399006"]) > 0 else None

    def idx_data(row):
        if row is None:
            return {"close": 0, "changePct": 0}
        return {
            "close": round(float(row.get("04", 0) or 0), 2),
            "changePct": round(float(row.get("07", 0) or 0), 2),
        }

    # Market breadth
    spot = ak.stock_zh_a_spot_em()
    advancers = len(spot[spot["07"] > 0]) if spot is not None else 0
    decliners = len(spot[spot["07"] < 0]) if spot is not None else 0
    limit_up = len(spot[spot["07"] >= 9.9]) if spot is not None else 0
    limit_down = len(spot[spot["07"] <= -9.9]) if spot is not None else 0
    total_vol = round(float(spot["00"].sum()) / 1e8, 2) if spot is not None else 0

    print(json.dumps({
        "shIndex": idx_data(sh_row),
        "szIndex": idx_data(sz_row),
        "cyIndex": idx_data(cy_row),
        "advancers": advancers,
        "decliners": decliners,
        "limitUp": limit_up,
        "limitDown": limit_down,
        "totalVolume": total_vol,
    }, ensure_ascii=False))

except Exception as e:
    print(json.dumps({
        "shIndex": {"close": 0, "changePct": 0},
        "szIndex": {"close": 0, "changePct": 0},
        "cyIndex": {"close": 0, "changePct": 0},
        "advancers": 0, "decliners": 0, "limitUp": 0, "limitDown": 0, "totalVolume": 0,
        "error": str(e),
    }))
`;

    try {
      const result = await runInlinePython<DailyReviewData["market"]>(script, this.tradingConfig);
      return result.data as DailyReviewData["market"];
    } catch {
      return {
        shIndex: { close: 0, changePct: 0 },
        szIndex: { close: 0, changePct: 0 },
        cyIndex: { close: 0, changePct: 0 },
        advancers: 0, decliners: 0, limitUp: 0, limitDown: 0, totalVolume: 0,
      };
    }
  }

  /** Format report as Markdown */
  private formatReport(
    date: string,
    market: DailyReviewData["market"],
    portfolio: DailyReviewData["portfolio"],
    trades: DailyReviewData["trades"],
  ): string {
    const lines: string[] = [];

    lines.push(`# 070405 ${date}\n`);

    // Market overview
    lines.push("## 大盘概览\n");
    lines.push("| 08 | 收盘 | 07 |");
    lines.push("|------|------|--------|");
    lines.push(`| 0808 | ${market.shIndex.close} | ${market.shIndex.changePct >= 0 ? "+" : ""}${market.shIndex.changePct}% |`);
    lines.push(`| 0808 | ${market.szIndex.close} | ${market.szIndex.changePct >= 0 ? "+" : ""}${market.szIndex.changePct}% |`);
    lines.push(`| 0908 | ${market.cyIndex.close} | ${market.cyIndex.changePct >= 0 ? "+" : ""}${market.cyIndex.changePct}% |`);
    lines.push("");
    lines.push(`- 上涨: ${market.advancers} 家 | 08: ${market.decliners} 家`);
    lines.push(`- 05: ${market.limitUp} 家 | 05: ${market.limitDown} 家`);
    lines.push(`- 0500: ${market.totalVolume} 06\n`);

    // Portfolio
    lines.push("## 020807\n");
    lines.push(`- 05: 06${portfolio.totalValue.toLocaleString()}`);
    lines.push(`- 当日盈亏: 06${portfolio.dailyPnl.toLocaleString()} (${portfolio.dailyPnlPct >= 0 ? "+" : ""}${portfolio.dailyPnlPct.toFixed(2)}%)\n`);

    if (portfolio.positions.length > 0) {
      lines.push("| 07 | 代码 | 07 | 07 |");
      lines.push("|------|------|--------|------|");
      for (const p of portfolio.positions) {
        lines.push(`| ${p.name} | ${p.symbol} | ${p.changePct >= 0 ? "+" : ""}${p.changePct.toFixed(2)}% | 06${p.pnl.toFixed(2)} |`);
      }
      lines.push("");
    }

    // Trades
    if (trades.length > 0) {
      lines.push("## 今日交易\n");
      lines.push("| 02 | 07 | 方向 | 18 | 数量 |");
      lines.push("|------|------|------|------|------|");
      for (const t of trades) {
        lines.push(`| ${t.time} | ${t.name}(${t.symbol}) | ${t.side} | ${t.price} | ${t.quantity} |`);
      }
      lines.push("");
    }

    lines.push(`---\n*生成时间: ${new Date().toLocaleString("zh-CN")}*`);
    return lines.join("\n");
  }
}
