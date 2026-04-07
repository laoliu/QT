/**
 * auction_monitor tool — AI-callable tool for monitoring call auction phase (集合竞价).
 *
 * Actions:
 *   watch   — add/remove symbols to/from the auction watchlist (supports batch)
 *   status  — view current auction status (volume ratio, change%, bid/ask ratio)
 *   alerts  — view triggered auction anomaly alerts
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { AuctionMonitorService } from "../services/auction-monitor.js";
import type { QuantTradingConfig } from "../types.js";

export function createAuctionMonitorTool(
  api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  monitor: AuctionMonitorService,
) {
  return {
    name: "auction_monitor",
    description:
      "集合竞价监控工具 — 监控A股9:15-9:25集合竞价阶段的异动信号。\n" +
      "action=watch 添加/移除竞价监控标的（支持批量，逗号分隔）;\n" +
      "action=status 查看当前竞价状态（量比、涨跌幅、委买卖比）;\n" +
      "action=alerts 查看竞价异动告警列表。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["watch", "status", "alerts"],
          description:
            "操作类型: watch=添加/移除监控标的, status=查看竞价状态, alerts=查看异动告警",
        },
        symbols: {
          type: "string",
          description:
            "股票代码，逗号分隔。action=watch 时为要添加的标的；" +
            "action=status 时可选，不传则返回全部监控标的状态。例: '600519,000858,300750'",
        },
        operation: {
          type: "string",
          enum: ["add", "remove", "list"],
          description:
            "watch 操作类型: add=添加监控(默认), remove=移除监控, list=查看监控列表",
        },
        favorites: {
          type: "boolean",
          description:
            "action=watch 且 operation=add 时，设为 true 可将自选股全部添加到竞价监控",
        },
        limit: {
          type: "number",
          description: "action=alerts 时，返回最近 N 条告警（默认 20）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── watch: manage auction watchlist ──────────────────────
        case "watch": {
          const operation = (args.operation as string) ?? "add";
          const symbolsStr = args.symbols as string | undefined;
          const useFavorites = args.favorites as boolean | undefined;

          if (operation === "list") {
            const list = monitor.getWatchlist();
            return {
              action: "watch",
              operation: "list",
              count: list.length,
              symbols: list,
              isAuctionPhase: monitor.isAuctionPhase(),
            };
          }

          // Collect symbols to add/remove
          let symbols: string[] = [];
          if (symbolsStr) {
            symbols = symbolsStr
              .split(/[,，\s]+/)
              .map((s) => s.trim())
              .filter(Boolean);
          }

          // If favorites flag is set, load favorites and add them
          if (useFavorites && operation === "add") {
            try {
              const fs = await import("node:fs/promises");
              const path = await import("node:path");
              const os = await import("node:os");
              const dir =
                _config.dataDir ??
                path.default.join(os.default.homedir(), ".openclaw", "quant-trading");
              const favPath = path.default.join(dir, "favorites.json");
              const raw = await fs.default.readFile(favPath, "utf-8");
              const favData = JSON.parse(raw) as {
                items?: Array<{ code?: string; symbol?: string }>;
              };
              if (favData.items) {
                for (const item of favData.items) {
                  const code = item.code ?? item.symbol;
                  if (code) symbols.push(code);
                }
              }
            } catch {
              // No favorites file, proceed with provided symbols
            }
          }

          if (symbols.length === 0) {
            return { error: "请提供股票代码 (symbols) 或使用 favorites=true 添加自选股" };
          }

          if (operation === "remove") {
            const removed = monitor.removeSymbols(symbols);
            return {
              action: "watch",
              operation: "remove",
              removed,
              removedCount: removed.length,
              currentWatchlist: monitor.getWatchlist(),
            };
          }

          // Default: add
          const added = monitor.addSymbols(symbols);
          return {
            action: "watch",
            operation: "add",
            added,
            addedCount: added.length,
            alreadyWatched: symbols.filter((s) => !added.includes(s)),
            currentWatchlist: monitor.getWatchlist(),
            isAuctionPhase: monitor.isAuctionPhase(),
          };
        }

        // ── status: current auction state ───────────────────────
        case "status": {
          const symbolsStr = args.symbols as string | undefined;
          const isAuction = monitor.isAuctionPhase();

          if (symbolsStr) {
            const symbols = symbolsStr
              .split(/[,，\s]+/)
              .map((s) => s.trim())
              .filter(Boolean);
            const statuses = symbols
              .map((s) => monitor.getSymbolStatus(s))
              .filter((q): q is NonNullable<typeof q> => q != null);
            return {
              action: "status",
              isAuctionPhase: isAuction,
              count: statuses.length,
              quotes: statuses.map(formatQuote),
            };
          }

          const allStatus = monitor.getStatus();
          return {
            action: "status",
            isAuctionPhase: isAuction,
            watchlistCount: monitor.getWatchlist().length,
            count: allStatus.length,
            quotes: allStatus.map(formatQuote),
            hint: !isAuction
              ? "当前不在集合竞价时段(9:15-9:25)，显示的是上次竞价数据"
              : undefined,
          };
        }

        // ── alerts: anomaly alerts ──────────────────────────────
        case "alerts": {
          const limit = (args.limit as number) ?? 20;
          const alerts = monitor.getAlerts(limit);
          return {
            action: "alerts",
            count: alerts.length,
            alerts: alerts.map((a) => ({
              symbol: a.symbol,
              name: a.name,
              type: a.type,
              message: a.message,
              value: a.value,
              threshold: a.threshold,
              time: new Date(a.timestamp).toLocaleTimeString("zh-CN", {
                timeZone: "Asia/Shanghai",
              }),
            })),
            thresholds: {
              volumeRatio: "量比 > 3",
              priceSurge: "涨幅 > 5%",
              priceDrop: "跌幅 > 3%",
            },
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Helpers ──────────────────────────────────────────────────────

function formatQuote(q: {
  symbol: string;
  name?: string;
  price: number;
  prevClose: number;
  changePct: number;
  volume: number;
  amount: number;
  volumeRatio?: number;
  bidAskRatio?: number;
  timestamp: number;
}) {
  return {
    symbol: q.symbol,
    name: q.name,
    price: q.price.toFixed(2),
    changePct: `${q.changePct >= 0 ? "+" : ""}${q.changePct.toFixed(2)}%`,
    volume: q.volume,
    amount: q.amount,
    volumeRatio: q.volumeRatio?.toFixed(2) ?? "-",
    bidAskRatio: q.bidAskRatio?.toFixed(2) ?? "-",
    time: new Date(q.timestamp).toLocaleTimeString("zh-CN", {
      timeZone: "Asia/Shanghai",
    }),
  };
}
