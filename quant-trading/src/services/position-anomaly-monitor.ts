/**
 * PositionAnomalyMonitor — background service that periodically scans
 * all held positions for abnormal conditions:
 *
 * 1. 急跌 (Sharp Drop):   当日跌幅超过阈值 (default -5%)
 * 2. 放量 (Volume Spike):  当日成交量 > 5日均量 × 倍数 (default 2x)
 * 3. 均线破位 (MA Break):  当前价格跌破 MA20
 *
 * Runs every 60s while market is open. Alerts are deduplicated per
 * symbol+trigger per trading day.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { KlineBar, LivePosition, MarketType, QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";

// ── Config ───────────────────────────────────────────────────

export type PositionAnomalyConfig = {
  /** Polling interval in seconds (default 60) */
  pollIntervalSecs?: number;
  /** Sharp-drop threshold as negative pct (default -0.05 = -5%) */
  sharpDropPct?: number;
  /** Volume spike multiplier over 5-day average (default 2) */
  volumeSpikeMultiplier?: number;
  /** MA period for break detection (default 20) */
  maPeriod?: number;
  /** Enable/disable the monitor (default true) */
  enabled?: boolean;
};

const DEFAULTS: Required<PositionAnomalyConfig> = {
  pollIntervalSecs: 60,
  sharpDropPct: -0.05,
  volumeSpikeMultiplier: 2,
  maPeriod: 20,
  enabled: true,
};

// ── Anomaly types ────────────────────────────────────────────

type AnomalyTrigger = "sharp_drop" | "volume_spike" | "ma_break";

// ── Service factory ──────────────────────────────────────────

export function createPositionAnomalyMonitorService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
  getQuote: (symbol: string, market: MarketType) => Promise<{
    price: number;
    changePct: number;
    volume: number;
  } | null>,
  getKlines: (
    symbol: string,
    interval: string,
    startDate: string,
    endDate: string,
    market: MarketType,
  ) => Promise<KlineBar[]>,
): OpenClawPluginService {
  const cfg: Required<PositionAnomalyConfig> = {
    ...DEFAULTS,
    ...((config as Record<string, unknown>).positionAnomalyMonitor as Partial<PositionAnomalyConfig> ?? {}),
  };

  let interval: ReturnType<typeof setInterval> | null = null;

  // Dedup: track which (symbol, trigger) combos have fired today
  let firedToday = new Set<string>();
  let lastTradingDay = "";

  const resetDailyState = () => {
    const today = new Date().toISOString().slice(0, 10);
    if (today !== lastTradingDay) {
      firedToday = new Set();
      lastTradingDay = today;
    }
  };

  const alert = async (symbol: string, trigger: AnomalyTrigger, message: string) => {
    const key = `${symbol}:${trigger}`;
    if (firedToday.has(key)) return;
    firedToday.add(key);

    const emoji =
      trigger === "sharp_drop" ? "📉" : trigger === "volume_spike" ? "📊" : "⚠️";
    const msg = `${emoji} [持仓异动] ${message}`;
    api.logger.info(`PositionAnomalyMonitor: ${message}`);
    await tryNotify(api, msg);
  };

  const tick = async () => {
    if (!isMarketOpen()) return;
    resetDailyState();

    const positions = getPositions();
    if (!positions || positions.length === 0) return;

    for (const pos of positions) {
      if (pos.quantity <= 0) continue;

      try {
        // Fetch real-time quote
        const quote = await getQuote(pos.symbol, pos.market);
        if (!quote) continue;

        // ── 1. Sharp drop check ──
        if (quote.changePct <= cfg.sharpDropPct * 100) {
          await alert(
            pos.symbol,
            "sharp_drop",
            `${pos.name ?? pos.symbol} 急跌 ${quote.changePct.toFixed(2)}%，` +
              `现价 ${quote.price.toFixed(2)}，持仓 ${pos.quantity} 股`,
          );
        }

        // ── 2. Volume spike check ──
        // Need recent 5-day klines to compute average volume
        const end = new Date().toISOString().slice(0, 10);
        const start = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000)
          .toISOString()
          .slice(0, 10);
        let bars: KlineBar[] = [];
        try {
          bars = await getKlines(pos.symbol, "1d", start, end, pos.market);
        } catch {
          // kline fetch failed — skip volume and MA checks for this symbol
          continue;
        }

        if (bars.length >= 6) {
          // Exclude the last bar (today) — compute average of preceding 5 bars
          const recentBars = bars.slice(-6, -1);
          const avgVolume =
            recentBars.reduce((sum, b) => sum + b.volume, 0) / recentBars.length;

          if (avgVolume > 0 && quote.volume > avgVolume * cfg.volumeSpikeMultiplier) {
            const ratio = (quote.volume / avgVolume).toFixed(1);
            await alert(
              pos.symbol,
              "volume_spike",
              `${pos.name ?? pos.symbol} 放量异动，` +
                `当日成交量是5日均量的 ${ratio} 倍，持仓 ${pos.quantity} 股`,
            );
          }
        }

        // ── 3. MA break check ──
        if (bars.length >= cfg.maPeriod) {
          const closes = bars.map((b) => b.close);
          const maPeriodSlice = closes.slice(-cfg.maPeriod);
          const maValue =
            maPeriodSlice.reduce((sum, c) => sum + c, 0) / cfg.maPeriod;

          if (quote.price < maValue) {
            await alert(
              pos.symbol,
              "ma_break",
              `${pos.name ?? pos.symbol} 跌破MA${cfg.maPeriod}` +
                `（MA${cfg.maPeriod}=${maValue.toFixed(2)}，现价=${quote.price.toFixed(2)}），` +
                `持仓 ${pos.quantity} 股`,
            );
          }
        }
      } catch (err) {
        api.logger.warn(
          `PositionAnomalyMonitor: ${pos.symbol} 检测失败: ${(err as Error).message}`,
        );
      }
    }
  };

  return {
    id: "quant-position-anomaly-monitor",
    async start() {
      if (!cfg.enabled) {
        api.logger.info("PositionAnomalyMonitor: disabled by config");
        return;
      }
      api.logger.info(
        `PositionAnomalyMonitor: started (poll=${cfg.pollIntervalSecs}s, ` +
          `dropThreshold=${(cfg.sharpDropPct * 100).toFixed(0)}%, ` +
          `volumeSpike=${cfg.volumeSpikeMultiplier}x, ` +
          `maPeriod=${cfg.maPeriod})`,
      );
      interval = setInterval(() => void tick(), cfg.pollIntervalSecs * 1000);
      void tick();
    },
    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("PositionAnomalyMonitor: stopped");
    },
  };
}
