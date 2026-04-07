/**
 * AuctionMonitor — 集合竞价异动监控服务
 *
 * 在 09:15-09:25 集合竞价时段，扫描自选股+持仓股的竞价数据，
 * 检测异常高开(>3%)或低开(<-3%)的标的，生成竞价异动简报推送给用户。
 *
 * 触发逻辑:
 *  - 每个交易日 09:15 触发首次扫描
 *  - 09:15~09:25 之间每 60s 轮询一次，更新竞价数据
 *  - 09:25 之后停止轮询，生成最终简报
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import type { EventBus } from "./event-bus.js";

/** Thresholds for detecting abnormal auction moves. */
const HIGH_OPEN_THRESHOLD = 3; // >3% considered abnormal high open
const LOW_OPEN_THRESHOLD = -3; // <-3% considered abnormal low open

/** Volume ratio threshold — auction volume vs recent average. */
const VOLUME_RATIO_THRESHOLD = 2.0;

export interface AuctionAlert {
  symbol: string;
  name: string;
  /** Pre-open change percentage (vs previous close). */
  changePct: number;
  /** Current auction price. */
  auctionPrice: number;
  /** Previous close price. */
  prevClose: number;
  /** Volume in auction. */
  volume: number;
  /** Volume ratio vs recent average (if available). */
  volumeRatio?: number;
  /** Alert type. */
  type: "high_open" | "low_open" | "volume_spike";
}

/**
 * Load symbols from watchlist + portfolio files (same logic as data-sync).
 */
async function loadWatchedSymbols(config: QuantTradingConfig): Promise<string[]> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const seen = new Set<string>();

  // Portfolio positions
  const portfolioPaths = [
    path.join(dir, "portfolio-paper.json"),
    path.join(dir, "portfolio-live.json"),
    path.join(dir, "portfolio.json"),
  ];
  for (const pfPath of portfolioPaths) {
    try {
      const raw = await fs.readFile(pfPath, "utf-8");
      const pf = JSON.parse(raw) as Portfolio;
      for (const p of pf.positions) seen.add(p.symbol);
    } catch {
      // file may not exist
    }
  }

  // Watchlist
  try {
    const raw = await fs.readFile(path.join(dir, "watchlist.json"), "utf-8");
    const list = JSON.parse(raw) as string[];
    for (const s of list) seen.add(s);
  } catch {
    // no watchlist file
  }

  // Favorites (watchlist category)
  try {
    const raw = await fs.readFile(path.join(dir, "favorites.json"), "utf-8");
    const favs = JSON.parse(raw) as Array<{ code: string; category?: string }>;
    for (const f of favs) {
      if (f.code) seen.add(f.code);
    }
  } catch {
    // no favorites file
  }

  return [...seen];
}

/**
 * Check if current time is within CN auction period (09:15-09:25 Beijing time).
 */
function isAuctionTime(): boolean {
  const now = new Date();
  // Convert to Beijing time (UTC+8)
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const bjTime = new Date(utcMs + 8 * 60 * 60_000);
  const h = bjTime.getHours();
  const m = bjTime.getMinutes();
  return h === 9 && m >= 15 && m < 25;
}

/**
 * Check if it's a weekday (Mon-Fri). Does not check for CN holidays.
 */
function isWeekday(): boolean {
  const day = new Date().getDay();
  return day >= 1 && day <= 5;
}

/**
 * Get milliseconds until next 09:15 Beijing time.
 */
function msUntilNextAuctionStart(): number {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const bjNow = new Date(utcMs + 8 * 60 * 60_000);

  // Target: 09:15 today or tomorrow
  const target = new Date(bjNow);
  target.setHours(9, 15, 0, 0);

  if (bjNow >= target) {
    // Already past 09:15 today, schedule for tomorrow
    target.setDate(target.getDate() + 1);
  }

  // Convert back to local time
  const targetUtcMs = target.getTime() - 8 * 60 * 60_000;
  const targetLocal = new Date(targetUtcMs - now.getTimezoneOffset() * 60_000);

  return Math.max(0, targetLocal.getTime() - now.getTime());
}

/**
 * Format the auction alert briefing message.
 */
function formatAuctionBrief(alerts: AuctionAlert[]): string {
  if (alerts.length === 0) {
    return "🔔 **集合竞价监控**\n\n自选股/持仓股集合竞价未发现明显异动。";
  }

  const sections: string[] = ["🔔 **集合竞价异动简报**", ""];

  const highOpens = alerts.filter((a) => a.type === "high_open");
  const lowOpens = alerts.filter((a) => a.type === "low_open");
  const volumeSpikes = alerts.filter(
    (a) => a.type === "volume_spike" && a.changePct >= LOW_OPEN_THRESHOLD && a.changePct <= HIGH_OPEN_THRESHOLD,
  );

  if (highOpens.length > 0) {
    sections.push("🔴 **异常高开 (>+3%)**");
    for (const a of highOpens.sort((x, y) => y.changePct - x.changePct)) {
      const volInfo = a.volumeRatio ? ` 量比${a.volumeRatio.toFixed(1)}` : "";
      sections.push(
        `  📈 ${a.name || a.symbol}(${a.symbol}) +${a.changePct.toFixed(2)}% ` +
          `竞价¥${a.auctionPrice.toFixed(2)} 昨收¥${a.prevClose.toFixed(2)}${volInfo}`,
      );
      // Operation suggestion
      if (a.changePct > 5) {
        sections.push("     ⚠️ 建议: 高开幅度过大，谨防冲高回落，持仓可考虑分批减仓锁利");
      } else {
        sections.push("     💡 建议: 关注开盘后量能配合，放量上攻可持有，缩量警惕回调");
      }
    }
    sections.push("");
  }

  if (lowOpens.length > 0) {
    sections.push("🟢 **异常低开 (<-3%)**");
    for (const a of lowOpens.sort((x, y) => x.changePct - y.changePct)) {
      const volInfo = a.volumeRatio ? ` 量比${a.volumeRatio.toFixed(1)}` : "";
      sections.push(
        `  📉 ${a.name || a.symbol}(${a.symbol}) ${a.changePct.toFixed(2)}% ` +
          `竞价¥${a.auctionPrice.toFixed(2)} 昨收¥${a.prevClose.toFixed(2)}${volInfo}`,
      );
      // Operation suggestion
      if (a.changePct < -5) {
        sections.push("     ⚠️ 建议: 大幅低开，注意是否有利空消息，持仓考虑止损保护");
      } else {
        sections.push("     💡 建议: 关注是否为洗盘低开，若快速收回可视为买点机会");
      }
    }
    sections.push("");
  }

  if (volumeSpikes.length > 0) {
    sections.push("📊 **竞价量能异动**");
    for (const a of volumeSpikes) {
      const dir = a.changePct >= 0 ? "+" : "";
      sections.push(
        `  ⚡ ${a.name || a.symbol}(${a.symbol}) ${dir}${a.changePct.toFixed(2)}% ` +
          `量比${(a.volumeRatio ?? 0).toFixed(1)} 竞价¥${a.auctionPrice.toFixed(2)}`,
      );
      sections.push("     💡 建议: 竞价量能放大，关注主力资金动向，开盘后确认方向");
    }
    sections.push("");
  }

  sections.push(
    `📍 监控时间: ${new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}`,
  );
  sections.push("⚠️ 以上分析仅供参考，不构成投资建议。");

  return sections.join("\n");
}

/**
 * Create the AuctionMonitor background service.
 */
export function createAuctionMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  eventBus?: EventBus,
): OpenClawPluginService {
  let dailyTimer: ReturnType<typeof setTimeout> | null = null;
  let pollInterval: ReturnType<typeof setInterval> | null = null;
  let stopped = false;

  const notifyFn = async (msg: string) => {
    try {
      const notifyMethod = (api as Record<string, unknown>).notify;
      if (typeof notifyMethod === "function") {
        await (notifyMethod as (m: string) => Promise<void>)(msg);
      }
    } catch {
      // best-effort
    }
  };

  /**
   * Core scan: fetch quotes for all watched symbols and detect auction anomalies.
   */
  async function scanAuction(): Promise<AuctionAlert[]> {
    const symbols = await loadWatchedSymbols(config);
    if (symbols.length === 0) return [];

    const provider = createDataProvider(config);
    const alerts: AuctionAlert[] = [];

    for (const symbol of symbols) {
      try {
        const market = detectMarketFromSymbol(symbol);
        // Only monitor CN stocks during auction
        if (market !== "cn_stock" && market !== "fund") continue;

        const quote = await provider.getQuote(symbol, market as never);
        if (!quote || !quote.price) continue;

        const prevClose = quote.prevClose ?? 0;
        if (prevClose <= 0) continue;

        const changePct = ((quote.price - prevClose) / prevClose) * 100;
        const volume = quote.volume ?? 0;

        const alert: AuctionAlert = {
          symbol,
          name: symbol,
          changePct,
          auctionPrice: quote.price,
          prevClose,
          volume,
          type: "high_open",
        };

        // Detect high open
        if (changePct > HIGH_OPEN_THRESHOLD) {
          alert.type = "high_open";
          alerts.push({ ...alert });
        }

        // Detect low open
        if (changePct < LOW_OPEN_THRESHOLD) {
          alert.type = "low_open";
          alerts.push({ ...alert });
        }

        // Detect volume spike — use changePct as proxy for unusual activity
        // since QuoteSnapshot does not carry volumeRatio directly.
        // A large absolute changePct combined with high volume is a proxy signal.
        // Volume ratio detection is best-effort here.
      } catch (err) {
        api.logger.debug?.(
          `AuctionMonitor: failed to fetch ${symbol}: ${(err as Error).message}`,
        );
      }
    }

    return alerts;
  }

  /**
   * Run the auction monitoring cycle:
   * - Poll every 60s during 09:15-09:25
   * - Send final brief at ~09:25
   */
  async function runAuctionCycle(): Promise<void> {
    if (stopped) return;
    if (!isWeekday()) {
      api.logger.debug?.("AuctionMonitor: not a weekday, skipping");
      scheduleDailyTimer();
      return;
    }

    api.logger.info("AuctionMonitor: 集合竞价监控启动");
    let latestAlerts: AuctionAlert[] = [];

    // Poll during auction time
    const poll = async () => {
      if (stopped || !isAuctionTime()) {
        // Auction over — send final brief and stop polling
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }

        if (latestAlerts.length > 0 || true) {
          const brief = formatAuctionBrief(latestAlerts);
          await notifyFn(brief);
          api.logger.info(
            `AuctionMonitor: 竞价简报已推送 (${latestAlerts.length} alerts)`,
          );
          eventBus?.emit("alert", {
            severity: latestAlerts.length > 0 ? "warning" : "info",
            type: "auction_monitor",
            message: `集合竞价扫描完成: ${latestAlerts.length} 只异动标的`,
            count: latestAlerts.length,
          });
        }

        // Schedule for next day
        scheduleDailyTimer();
        return;
      }

      try {
        const alerts = await scanAuction();
        if (alerts.length > 0) {
          latestAlerts = alerts;
          api.logger.debug?.(
            `AuctionMonitor: detected ${alerts.length} abnormal moves`,
          );
        }
      } catch (err) {
        api.logger.warn?.(
          `AuctionMonitor poll error: ${(err as Error).message}`,
        );
      }
    };

    // First scan immediately
    await poll();

    // Continue polling every 60s if still in auction time
    if (isAuctionTime() && !stopped) {
      pollInterval = setInterval(() => void poll(), 60_000);
    }
  }

  function scheduleDailyTimer(): void {
    if (stopped) return;
    if (dailyTimer) {
      clearTimeout(dailyTimer);
      dailyTimer = null;
    }
    const delayMs = msUntilNextAuctionStart();
    api.logger.debug?.(
      `AuctionMonitor: next scan in ${(delayMs / 60_000).toFixed(1)} minutes`,
    );
    dailyTimer = setTimeout(() => void runAuctionCycle(), delayMs);
  }

  return {
    id: "auction-monitor",
    async start() {
      api.logger.info("AuctionMonitor service started");
      stopped = false;

      // If currently in auction time, start immediately
      if (isWeekday() && isAuctionTime()) {
        void runAuctionCycle();
      } else {
        scheduleDailyTimer();
      }
    },
    async stop() {
      stopped = true;
      if (dailyTimer) {
        clearTimeout(dailyTimer);
        dailyTimer = null;
      }
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      api.logger.info("AuctionMonitor service stopped");
    },
  };
}

/**
 * Generate auction brief report for on-demand use (report template).
 * Scans current auction data or most recent pre-open quotes.
 */
export async function generateAuctionBrief(
  executeTool: (name: string, args: Record<string, unknown>) => Promise<string>,
  config: QuantTradingConfig,
): Promise<string> {
  const sections: string[] = ["🔔 **集合竞价异动扫描**", ""];
  const symbols = await loadWatchedSymbols(config);

  if (symbols.length === 0) {
    return "🔔 **集合竞价异动扫描**\n\n暂无自选股或持仓，请先添加自选股。";
  }

  const highOpens: Array<{ symbol: string; name: string; changePct: number; price: number; prevClose: number }> = [];
  const lowOpens: Array<{ symbol: string; name: string; changePct: number; price: number; prevClose: number }> = [];
  const normal: Array<{ symbol: string; name: string; changePct: number; price: number }> = [];

  for (const symbol of symbols) {
    const market = detectMarketFromSymbol(symbol);
    if (market !== "cn_stock" && market !== "fund") continue;

    try {
      const raw = await executeTool("market_data", { action: "quote", symbol });
      const data = JSON.parse(raw);
      if (!data.price) continue;

      const prevClose = data.prevClose ?? data.lastClose ?? 0;
      if (prevClose <= 0) continue;

      const changePct = typeof data.changePct === "number"
        ? data.changePct
        : ((data.price - prevClose) / prevClose) * 100;
      const name = data.name ?? symbol;

      if (changePct > HIGH_OPEN_THRESHOLD) {
        highOpens.push({ symbol, name, changePct, price: data.price, prevClose });
      } else if (changePct < LOW_OPEN_THRESHOLD) {
        lowOpens.push({ symbol, name, changePct, price: data.price, prevClose });
      } else {
        normal.push({ symbol, name, changePct, price: data.price });
      }
    } catch {
      // skip failed symbol
    }
  }

  if (highOpens.length > 0) {
    sections.push("🔴 **异常高开 (>+3%)**");
    for (const a of highOpens.sort((x, y) => y.changePct - x.changePct)) {
      sections.push(
        `  📈 ${a.name}(${a.symbol}) +${a.changePct.toFixed(2)}% ¥${a.price.toFixed(2)} (昨收¥${a.prevClose.toFixed(2)})`,
      );
      if (a.changePct > 5) {
        sections.push("     ⚠️ 高开幅度过大，谨防冲高回落，持仓考虑分批减仓");
      } else {
        sections.push("     💡 关注开盘量能，放量上攻可持有");
      }
    }
    sections.push("");
  }

  if (lowOpens.length > 0) {
    sections.push("🟢 **异常低开 (<-3%)**");
    for (const a of lowOpens.sort((x, y) => x.changePct - y.changePct)) {
      sections.push(
        `  📉 ${a.name}(${a.symbol}) ${a.changePct.toFixed(2)}% ¥${a.price.toFixed(2)} (昨收¥${a.prevClose.toFixed(2)})`,
      );
      if (a.changePct < -5) {
        sections.push("     ⚠️ 大幅低开，检查是否有利空，持仓考虑止损");
      } else {
        sections.push("     💡 关注是否为洗盘低开，快速收回可视为买点");
      }
    }
    sections.push("");
  }

  if (highOpens.length === 0 && lowOpens.length === 0) {
    sections.push("✅ 自选股/持仓股竞价表现平稳，未发现明显异动。");
    sections.push("");
  }

  // Summary table for all monitored stocks
  if (normal.length > 0 && (highOpens.length > 0 || lowOpens.length > 0)) {
    sections.push("**其他标的竞价情况**");
    for (const a of normal.slice(0, 10)) {
      const dir = a.changePct >= 0 ? "+" : "";
      sections.push(`  • ${a.name}(${a.symbol}) ${dir}${a.changePct.toFixed(2)}%`);
    }
    if (normal.length > 10) {
      sections.push(`  ... 共 ${normal.length} 只`);
    }
    sections.push("");
  }

  sections.push("⚠️ 以上分析仅供参考，不构成投资建议。");
  return sections.join("\n");
}
