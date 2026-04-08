/**
 * favorites tool — AI-callable for managing stock collections (股票收藏).
 *
 * Actions:
 *   list      — View all favorites with optional filtering
 *   add       — Add a stock to favorites with metadata
 *   update    — Update a favorite's notes, tags, priority, etc.
 *   remove    — Remove a stock from favorites
 *   search    — Search favorites by code or name
 *   tags      — Get all available tags
 *   categories — Get all available categories
 *   clear     — Clear all favorites
 *
 * Example:
 *   action=add, codes=["600519"], name="ę́", category="blue-chip", priority=5
 *   action=update, codes=["600519"], notes="看好长期", tags=["consumer","blue-chip"]
 *   action=list, category="blue-chip"
 */
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { FavoritesStore } from "../services/favorites-store.js";
import type { QuantTradingConfig } from "../types.js";

export function createFavoritesTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const store = new FavoritesStore({ dataDir: dir });

  return {
    name: "favorites",
    description:
      "股票收藏管理工具。添加、查看、更新和删除股票收藏。支持标签、分类、优先级、笔记等。\n" +
      "• action=list — 查看收藏列表（支持按分类或标签筛选）\n" +
      "• action=add — 添加股票到收藏（需提供 codes）\n" +
      "• action=update — 更新股票的笔记、标签、优先级等\n" +
      "• action=remove — 移除股票\n" +
      "• action=search — 搜索股票\n" +
      "• action=tags — 查看所有标签\n" +
      "• action=categories — 查看所有分类\n" +
      "• action=clear — 清空所有收藏",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "add", "update", "remove", "search", "tags", "categories", "clear"],
          description: "操作类型",
        },
        codes: {
          type: "array",
          items: { type: "string" },
          description: '股票代码列表，如 ["600519", "000858"]',
        },
        name: {
          type: "string",
          description: "股票名称（add/update时使用），如 'ę́'",
        },
        notes: {
          type: "string",
          description: "用户笔记（add/update时使用）",
        },
        tags: {
          type: "array",
          items: { type: "string" },
          description: '标签列表，如 ["蓝筹", "消费"]',
        },
        category: {
          type: "string",
          description: "分类: watchlist(自选股), ideas(想法), portfolio(持仓), shortlist(候选)",
        },
        priority: {
          type: "number",
          description: "优先级 (1-5, 1=低, 5=高)",
        },
        priceTarget: {
          type: "number",
          description: "目标价格",
        },
        convictionScore: {
          type: "number",
          description: "信心分数 (1-10)",
        },
        query: {
          type: "string",
          description: "search 操作的查询字符串",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const codes = ((args.codes as string[]) ?? []).map((c) => c.trim().toUpperCase());

      switch (action) {
        case "list": {
          const favorites = store.listFavorites({
            category: (args.category as string) || undefined,
            tags: (args.tags as string[]) || undefined,
            priority: (args.priority as number) || undefined,
          });

          return {
            count: favorites.length,
            favorites: favorites.map((f) => ({
              code: f.code,
              name: f.name,
              category: f.category,
              priority: f.priority,
              notes: f.notes,
              tags: f.tags,
              addedAt: f.addedAt,
              priceTarget: f.priceTarget,
              convictionScore: f.convictionScore,
            })),
          };
        }

        case "add": {
          if (codes.length === 0) return { error: "请提供 codes 列表" };

          const added: unknown[] = [];
          for (const code of codes) {
            const fav = await store.addFavorite(code, {
              name: (args.name as string) || undefined,
              notes: (args.notes as string) || undefined,
              tags: (args.tags as string[]) || undefined,
              category: (args.category as string) || undefined,
              priority: (args.priority as number) || undefined,
              priceTarget: (args.priceTarget as number) || undefined,
              convictionScore: (args.convictionScore as number) || undefined,
            });
            added.push({
              code: fav.code,
              name: fav.name,
              category: fav.category,
              priority: fav.priority,
            });
          }

          return {
            message: `已添加 ${codes.join(", ")} 到收藏`,
            added,
            totalCount: store.size(),
          };
        }

        case "update": {
          if (codes.length === 0) return { error: "请提供 codes 列表" };

          const updated: unknown[] = [];
          for (const code of codes) {
            const existing = store.getFavorite(code);
            if (!existing) {
              return { error: `股票 ${code} 不在收藏中` };
            }

            const fav = await store.addFavorite(code, {
              name: (args.name as string) || existing.name,
              notes: (args.notes as string) || existing.notes,
              tags: (args.tags as string[]) || existing.tags,
              category: (args.category as string) || existing.category,
              priority: (args.priority as number) ?? existing.priority,
              priceTarget: (args.priceTarget as number) ?? existing.priceTarget,
              convictionScore: (args.convictionScore as number) ?? existing.convictionScore,
            });
            updated.push({
              code: fav.code,
              name: fav.name,
              category: fav.category,
              priority: fav.priority,
              notes: fav.notes,
            });
          }

          return {
            message: `已更新 ${codes.join(", ")}`,
            updated,
          };
        }

        case "remove": {
          if (codes.length === 0) return { error: "请提供 codes 列表" };

          const removed: string[] = [];
          for (const code of codes) {
            if (await store.removeFavorite(code)) {
              removed.push(code);
            }
          }

          if (removed.length === 0) {
            return { message: "这些代码不在收藏中", count: store.size() };
          }

          return {
            message: `已移除 ${removed.join(", ")}`,
            removed,
            totalCount: store.size(),
          };
        }

        case "search": {
          const query = (args.query as string) || "";
          if (!query) return { error: "请提供 query 参数" };

          const results = store.search(query);
          return {
            query,
            count: results.length,
            results: results.map((f) => ({
              code: f.code,
              name: f.name,
              category: f.category,
              priority: f.priority,
              notes: f.notes,
            })),
          };
        }

        case "tags": {
          const tags = store.getAllTags();
          return {
            count: tags.length,
            tags,
          };
        }

        case "categories": {
          const categories = store.getAllCategories();
          const counts = store.countByCategory();
          return {
            categories: categories.map((cat) => ({
              name: cat,
              count: counts[cat] ?? 0,
            })),
            totalCount: store.size(),
          };
        }

        case "clear": {
          const count = await store.clear();
          return {
            message: "已清空所有收藏",
            clearedCount: count,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
