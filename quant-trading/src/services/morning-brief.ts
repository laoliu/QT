/**
 * MorningBriefService — 盘前简报 (RE-3)
 *
 * 功能:
 *  - 07010202
 *  - 包含: 全球市场概览、重要新闻/050005
 *  - 000004
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { NotificationService, NotificationMessage } from "./notification-service.js";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// ── Types ────────────────────────────────────────────────────

export interface MorningBriefConfig {
  enabled: boolean;
  /** 生成时间 (HH:mm, 08 "08:30") */
  generateTime: string;
  /** 0704 */
  outputDir: string;
  /** 090004 */
  pushNotification: boolean;
  /** 000107 */
  watchSymbols: string[];
}

const DEFAULT_CONFIG: MorningBriefConfig = {
  enabled: true,
  generateTime: "08:30",
  outputDir: "",
  pushNotification: true,
  watchSymbols: [],
};

// ── Service ──────────────────────────────────────────────────

export class MorningBriefService {
  private config: MorningBriefConfig;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastGenDate: string | null = null;

  constructor(
    private api: OpenClawPluginApi,
    private tradingConfig: QuantTradingConfig,
    private notificationService: NotificationService | null,
    config?: Partial<MorningBriefConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    if (!this.config.outputDir && tradingConfig.dataDir) {
      this.config.outputDir = join(tradingConfig.dataDir, "reports");
    }
  }

  setWatchSymbols(symbols: string[]): void {
    this.config.watchSymbols = [...symbols];
  }

  start(): void {
    if (!this.config.enabled) return;
    this.stop();
    this.timer = setInterval(() => {
      void this.tryAutoGenerate();
    }, 60_000);
    console.log("[MorningBrief] started");
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
      const day = now.getDay();
      if (day === 0 || day === 6) return;
      this.lastGenDate = today;
      await this.generateBrief();
    }
  }

  /** Generate morning brief */
  async generateBrief(): Promise<string> {
    const globalMarkets = await this.fetchGlobalMarkets();
    const watchData = await this.fetchWatchlistStatus();

    const date = new Date().toISOString().slice(0, 10);
    const brief = this.formatBrief(date, globalMarkets, watchData);

    // Save
    if (this.config.outputDir) {
      if (!existsSync(this.config.outputDir)) {
        mkdirSync(this.config.outputDir, { recursive: true });
      }
      const filePath = join(this.config.outputDir, `morning-brief-${date}.md`);
      writeFileSync(filePath, brief, "utf-8");
    }

    // Push
    if (this.config.pushNotification && this.notificationService) {
      const msg: NotificationMessage = {
        title: `盘前简报 ${date}`,
        content: brief.slice(0, 500),
        severity: "info",
        category: "report",
        markdown: brief,
      };
      void this.notificationService.send(msg);
    }

    return brief;
  }

  private async fetchGlobalMarkets(): Promise<Record<string, { close: number; changePct: number }>> {
    const script = `
import akshare as ak
import json

result = {}

try:
    # US indices
    try:
        us = ak.index_us_stock_sina()
        if us is not None and len(us) > 0:
            for _, row in us.head(5).iterrows():
                name = str(row.get("名称", ""))
                result[name] = {
                    "close": round(float(row.get("04", 0) or 0), 2),
                    "changePct": round(float(row.get("07", 0) or 0), 2),
                }
    except:
        pass

    # HK
    try:
        hk = ak.stock_hk_index_spot_em()
        if hk is not None and len(hk) > 0:
            for _, row in hk.head(3).iterrows():
                name = str(row.get("名称", ""))
                result[name] = {
                    "close": round(float(row.get("04", 0) or 0), 2),
                    "changePct": round(float(row.get("07", 0) or 0), 2),
                }
    except:
        pass

    print(json.dumps(result, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

    try {
      const result = await runInlinePython<Record<string, { close: number; changePct: number }>>(script, this.tradingConfig);
      if (result.ok && result.data) {
        return result.data;
      }
      return {};
    } catch {
      return {};
    }
  }

  private async fetchWatchlistStatus(): Promise<Array<{ symbol: string; name: string; prevClose: number }>> {
    if (this.config.watchSymbols.length === 0) return [];

    const symbolList = JSON.stringify(this.config.watchSymbols);
    const script = `
import akshare as ak
import json

symbols = ${symbolList}

try:
    df = ak.stock_zh_a_spot_em()
    results = []
    if df is not None:
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            if code in symbols:
                results.append({
                    "symbol": code,
                    "name": str(row.get("名称", "")),
                    "prevClose": round(float(row.get("昨收", 0) or 0), 2),
                })
    print(json.dumps(results, ensure_ascii=False))
except Exception as e:
    print(json.dumps([]))
`;

    try {
      const result = await runInlinePython(script, this.tradingConfig);
      return Array.isArray(result) ? result as Array<{ symbol: string; name: string; prevClose: number }> : [];
    } catch {
      return [];
    }
  }

  private formatBrief(
    date: string,
    globalMarkets: Record<string, { close: number; changePct: number }>,
    watchData: Array<{ symbol: string; name: string; prevClose: number }>,
  ): string {
    const lines: string[] = [];

    lines.push(`# 盘前简报 ${date}\n`);

    // Global markets
    const globalEntries = Object.entries(globalMarkets);
    if (globalEntries.length > 0) {
      lines.push("## 00\n");
      lines.push("| г | 收盘 | 07 |");
      lines.push("|------|------|--------|");
      for (const [name, data] of globalEntries) {
        lines.push(`| ${name} | ${data.close} | ${data.changePct >= 0 ? "+" : ""}${data.changePct}% |`);
      }
      lines.push("");
    }

    // Watchlist
    if (watchData.length > 0) {
      lines.push("## 关注个股\n");
      lines.push("| 代码 | 名称 | 昨收 |");
      lines.push("|------|------|------|");
      for (const w of watchData) {
        lines.push(`| ${w.symbol} | ${w.name} | ${w.prevClose} |`);
      }
      lines.push("");
    }

    lines.push("## 今日提示\n");
    lines.push("- 000901");
    lines.push("- 990248");
    lines.push("- 060409\n");

    lines.push(`---\n*生成时间: ${new Date().toLocaleString("zh-CN")}*`);
    return lines.join("\n");
  }
}
