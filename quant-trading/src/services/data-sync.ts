import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * DataSync service — periodically fetches and caches market data
 * for symbols in the paper portfolio and watchlist during CN market hours.
 *
 * Emits events to EventBus:
 *  - `data_sync` on each sync cycle (refreshed/failed/total counts)
 *  - `alert` on per-symbol failures (severity: warning)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { runPythonScript } from "../bridge/python-bridge.js";
import { isMarketOpen } from "../broker/paper-broker.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";
import type { EventBus } from "./event-bus.js";

function getPortfolioPaths(config: QuantTradingConfig): string[] {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return [
    path.join(dir, "portfolio-paper.json"),
    path.join(dir, "portfolio-live.json"),
    path.join(dir, "portfolio.json"), // legacy fallback
  ];
}

function getWatchlistPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "watchlist.json");
}

async function loadWatchedSymbols(config: QuantTradingConfig): Promise<string[]> {
  const seen = new Set<string>();
  // Portfolio positions (all modes)
  for (const pfPath of getPortfolioPaths(config)) {
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
    const raw = await fs.readFile(getWatchlistPath(config), "utf-8");
    const list = JSON.parse(raw) as string[];
    for (const s of list) seen.add(s);
  } catch {
    // no watchlist file
  }
  return [...seen];
}

export function createDataSyncService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  eventBus?: EventBus,
): OpenClawPluginService {
  let interval: ReturnType<typeof setInterval> | null = null;
  let extendedInterval: ReturnType<typeof setInterval> | null = null;
  // Reuse the same provider across ticks to preserve caches/connections
  let provider: ReturnType<typeof createDataProvider> | null = null;

  return {
    id: "quant-data-sync",
    async start() {
      api.logger.info("DataSync service started");
      provider = createDataProvider(config);

      // Run an immediate tick, then repeat every 5 minutes
      const tick = async () => {
        try {
          const symbols = await loadWatchedSymbols(config);
          if (symbols.length === 0) {
            api.logger.debug?.("DataSync: no symbols to sync, skipping");
            eventBus?.emit("data_sync", { status: "empty", refreshed: 0, total: 0 });
            return;
          }

          // Check if any market is open based on the actual symbols we hold
          const markets = new Set(symbols.map((s) => detectMarketFromSymbol(s)));
          const anyOpen = [...markets].some((m) => isMarketOpen(m));
          if (!anyOpen) {
            api.logger.debug?.("DataSync: all markets closed, skipping sync");
            eventBus?.emit("data_sync", { status: "market_closed" });
            return;
          }

          const dp = provider ?? createDataProvider(config);
          let refreshed = 0;
          let failed = 0;
          const failures: string[] = [];
          for (const sym of symbols) {
            try {
              await dp.getQuote(sym);
              refreshed++;
            } catch (err) {
              failed++;
              failures.push(sym);
              api.logger.warn?.(`DataSync: failed to refresh ${sym}: ${(err as Error).message}`);
              eventBus?.emit("alert", {
                severity: "warning",
                type: "data_sync_failure",
                message: `数据同步失败: ${sym} — ${(err as Error).message}`,
                symbol: sym,
              });
            }
          }
          api.logger.debug?.(`DataSync: refreshed ${refreshed}/${symbols.length} quotes`);
          eventBus?.emit("data_sync", {
            status: "completed",
            refreshed,
            failed,
            total: symbols.length,
            ...(failures.length > 0 ? { failures } : {}),
          });
        } catch (err) {
          api.logger.error(`DataSync error: ${(err as Error).message}`);
          eventBus?.emit("alert", {
            severity: "error",
            type: "data_sync_error",
            message: `DataSync 异常: ${(err as Error).message}`,
          });
        }
      };

      // Immediate first tick
      void tick();

      interval = setInterval(tick, 5 * 60 * 1000); // 5 min

      // ── Extended data sync: macro, earnings, sentiment ─────────────
      // Runs once per hour during market hours, warming caches for new data tools.
      const extendedTick = async () => {
        try {
          if (!isMarketOpen()) return;

          // Pre-warm macro regime (daily frequency, cheap to cache)
          try {
            await runPythonScript(
              "engines/macro_economic.py",
              { action: "regime", market: "cn" },
              config,
            );
            api.logger.debug?.("DataSync: macro regime cache warmed");
          } catch {
            // best-effort
          }

          // Pre-warm upcoming earnings calendar
          try {
            const earningsResult = await runPythonScript<{
              earnings?: Array<{ symbol?: string; disclosure_date?: string }>;
            }>(
              "engines/earnings_calendar.py",
              { action: "upcoming", market: "cn", days: 7, limit: 50 },
              config,
            );

            // Emit earnings_approaching events for stocks in portfolio/watchlist
            if (earningsResult.ok && earningsResult.data?.earnings) {
              const symbols = await loadWatchedSymbols(config);
              const watchSet = new Set(symbols);
              for (const e of earningsResult.data.earnings) {
                if (e.symbol && watchSet.has(e.symbol)) {
                  eventBus?.emit("alert", {
                    severity: "info",
                    type: "earnings_approaching",
                    message: `📅 ${e.symbol} 将于 ${e.disclosure_date ?? "近期"} 披露财报`,
                    symbol: e.symbol,
                  });
                }
              }
            }
            api.logger.debug?.("DataSync: earnings calendar cache warmed");
          } catch {
            // best-effort
          }

          // Pre-warm hot stocks sentiment (market hours only)
          try {
            await runPythonScript(
              "engines/social_sentiment.py",
              { action: "hot_stocks", market: "cn", limit: 20 },
              config,
            );
            api.logger.debug?.("DataSync: sentiment hot stocks cache warmed");
          } catch {
            // best-effort
          }
        } catch (err) {
          api.logger.debug?.(`DataSync extended tick error: ${(err as Error).message}`);
        }
      };

      // Run extended sync once on start, then every hour
      void extendedTick();
      extendedInterval = setInterval(extendedTick, 60 * 60 * 1000); // 1 hour
    },

    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      if (extendedInterval) {
        clearInterval(extendedInterval);
        extendedInterval = null;
      }
      api.logger.info("DataSync service stopped");
    },
  };
}
