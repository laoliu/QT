/**
 * watchlist tool — AI-callable for managing the user's watchlist (自选股).
 *
 * Actions:
 *   list   — View watchlist stocks (with optional real-time quotes)
 *   add    — Add stock(s) to watchlist
 *   remove — Remove stock(s) from watchlist
 *   clear  — Clear all watchlist stocks
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { QuantTradingConfig } from "../types.js";

export function createWatchlistTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const watchlistPath = path.join(dir, "watchlist.json");

  async function loadWatchlist(): Promise<string[]> {
    try {
      const raw = await fs.readFile(watchlistPath, "utf-8");
      return JSON.parse(raw) as string[];
    } catch {
      return [];
    }
  }

  async function saveWatchlist(list: string[]): Promise<void> {
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(watchlistPath, JSON.stringify(list, null, 2), "utf-8");
  }

  return {
    name: "watchlist",
    description:
      "自选股管理工具。查看、添加、删除自选股。\n" +
      "• action=list — 查看自选股列表\n" +
      "• action=add — 添加股票到自选股（需提供codes）\n" +
      "• action=remove — 从自选股移除（需提供codes）\n" +
      "• action=clear — 清空自选股",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "add", "remove", "clear"],
          description: "操作类型: list=查看, add=添加, remove=删除, clear=清空",
        },
        codes: {
          type: "array",
          items: { type: "string" },
          description: '股票代码列表（add/remove时使用），如 ["600519", "000858"]',
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const codes = ((args.codes as string[]) ?? []).map((c) => c.trim().toUpperCase());

      switch (action) {
        case "list": {
          const watchlist = await loadWatchlist();
          return {
            count: watchlist.length,
            codes: watchlist,
          };
        }

        case "add": {
          if (codes.length === 0) return { error: "请提供 codes 列表" };
          const watchlist = await loadWatchlist();
          const added: string[] = [];
          for (const code of codes) {
            if (!watchlist.includes(code)) {
              watchlist.push(code);
              added.push(code);
            }
          }
          if (added.length === 0)
            return { message: "所有代码已在自选股中", count: watchlist.length };
          await saveWatchlist(watchlist);
          return { message: `已添加 ${added.join(", ")}`, added, count: watchlist.length };
        }

        case "remove": {
          if (codes.length === 0) return { error: "请提供 codes 列表" };
          const watchlist = await loadWatchlist();
          const removed: string[] = [];
          for (const code of codes) {
            const idx = watchlist.indexOf(code);
            if (idx >= 0) {
              watchlist.splice(idx, 1);
              removed.push(code);
            }
          }
          if (removed.length === 0)
            return { message: "这些代码不在自选股中", count: watchlist.length };
          await saveWatchlist(watchlist);
          return { message: `已移除 ${removed.join(", ")}`, removed, count: watchlist.length };
        }

        case "clear": {
          const watchlist = await loadWatchlist();
          const count = watchlist.length;
          await saveWatchlist([]);
          return { message: `已清空自选股`, previousCount: count, count: 0 };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
