/**
 * trade_journal tool — AI-callable interface for trade notes and annotations.
 *
 * Actions:
 *   add_note     — write a note about a trade/symbol
 *   update_note  — update an existing note
 *   delete_note  — remove a note
 *   view         — view a specific note or notes by trade/symbol/date
 *   search       — search notes by keyword
 *   recent       — list recent notes
 *   summary      — overall journal statistics
 *   tags         — list all unique tags
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { TradeJournal } from "../services/trade-journal.js";

export function createTradeJournalTool(_api: OpenClawPluginApi, journal: TradeJournal) {
  return {
    name: "trade_journal",
    description:
      "交易笔记/复盘记录。记录每笔交易的思考和总结。" +
      "action=add_note 添加笔记, action=update_note 修改, action=delete_note 删除, " +
      "action=view 查看(按trade_id/symbol/date), action=search 搜索, " +
      "action=recent 最近笔记, action=summary 统计概览, action=tags 标签列表, " +
      "action=periodic_report 生成周报/月报(需指定period=week/month)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "add_note",
            "update_note",
            "delete_note",
            "view",
            "search",
            "recent",
            "summary",
            "tags",
            "periodic_report",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码" },
        content: { type: "string", description: "笔记内容" },
        trade_id: { type: "string", description: "交易ID" },
        note_id: { type: "string", description: "笔记ID (update/delete)" },
        tags: {
          type: "array",
          items: { type: "string" },
          description: "标签 (e.g. ['止损', '追涨'])",
        },
        rating: { type: "number", description: "自评分 1-5" },
        lesson: { type: "string", description: "经验教训/关键总结" },
        strategy_id: { type: "string", description: "策略ID" },
        direction: {
          type: "string",
          enum: ["buy", "sell", "short", "cover"],
          description: "交易方向",
        },
        entry_price: { type: "number", description: "入场价" },
        exit_price: { type: "number", description: "出场价" },
        pnl: { type: "number", description: "盈亏金额" },
        date: { type: "string", description: "日期 YYYY-MM-DD (默认今天)" },
        query: { type: "string", description: "搜索关键词 (search)" },
        limit: { type: "number", description: "返回数量" },
        period: {
          type: "string",
          enum: ["week", "month"],
          description: "报告周期 (periodic_report): week=周报, month=月报",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const limit = (args.limit as number) ?? 20;

      switch (action) {
        case "add_note": {
          const symbol = args.symbol as string;
          const content = args.content as string;
          if (!symbol || !content) return { error: "请提供 symbol 和 content" };
          const id = journal.addNote({
            symbol,
            content,
            tradeId: args.trade_id as string | undefined,
            tags: (args.tags as string[]) ?? [],
            rating: args.rating as number | undefined,
            strategyId: args.strategy_id as string | undefined,
            direction: args.direction as "buy" | "sell" | "short" | "cover" | undefined,
            entryPrice: args.entry_price as number | undefined,
            exitPrice: args.exit_price as number | undefined,
            pnl: args.pnl as number | undefined,
            lesson: args.lesson as string | undefined,
            date: args.date as string | undefined,
          });
          return { message: "笔记已保存", id };
        }

        case "update_note": {
          const noteId = args.note_id as string;
          if (!noteId) return { error: "请提供 note_id" };
          const ok = journal.updateNote(noteId, {
            content: args.content as string | undefined,
            tags: args.tags as string[] | undefined,
            rating: args.rating as number | undefined,
            lesson: args.lesson as string | undefined,
          });
          return ok ? { message: `笔记 ${noteId} 已更新` } : { error: `未找到笔记 ${noteId}` };
        }

        case "delete_note": {
          const noteId = args.note_id as string;
          if (!noteId) return { error: "请提供 note_id" };
          const ok = journal.deleteNote(noteId);
          return ok ? { message: `笔记 ${noteId} 已删除` } : { error: `未找到笔记 ${noteId}` };
        }

        case "view": {
          const tradeId = args.trade_id as string;
          const symbol = args.symbol as string;
          const date = args.date as string;
          const noteId = args.note_id as string;

          if (noteId) {
            const note = journal.getNote(noteId);
            return note ?? { error: `未找到笔记 ${noteId}` };
          }
          if (tradeId) {
            const notes = journal.getByTradeId(tradeId);
            return { count: notes.length, notes };
          }
          if (symbol) {
            const notes = journal.getBySymbol(symbol, limit);
            return { count: notes.length, notes };
          }
          if (date) {
            const notes = journal.getByDate(date);
            return { count: notes.length, notes };
          }
          return { error: "请提供 note_id, trade_id, symbol, 或 date" };
        }

        case "search": {
          const query = args.query as string;
          if (!query) return { error: "请提供 query 搜索关键词" };
          const results = journal.search(query, limit);
          return { count: results.length, notes: results };
        }

        case "recent": {
          const notes = journal.getRecent(limit);
          return {
            count: notes.length,
            notes: notes.map((n) => ({
              id: n.id,
              symbol: n.symbol,
              date: n.date,
              content: n.content.slice(0, 100) + (n.content.length > 100 ? "…" : ""),
              tags: n.tags,
              rating: n.rating,
              pnl: n.pnl != null ? `¥${n.pnl.toFixed(2)}` : undefined,
            })),
          };
        }

        case "summary": {
          return journal.getSummary();
        }

        case "tags": {
          return { tags: journal.getAllTags() };
        }

        case "periodic_report": {
          const period = (args.period as string) ?? "week";
          const now = new Date();
          let startDate: string;
          let periodLabel: string;

          if (period === "month") {
            const start = new Date(now.getFullYear(), now.getMonth(), 1);
            startDate = start.toISOString().slice(0, 10);
            periodLabel = `${now.getFullYear()}年${now.getMonth() + 1}月`;
          } else {
            // Default to current week (Monday to today)
            const day = now.getDay();
            const diff = day === 0 ? 6 : day - 1; // Monday = 0 offset
            const start = new Date(now);
            start.setDate(now.getDate() - diff);
            startDate = start.toISOString().slice(0, 10);
            periodLabel = `${startDate} ~ ${now.toISOString().slice(0, 10)}`;
          }

          const endDate = now.toISOString().slice(0, 10);
          const notes = journal.getByDateRange(startDate, endDate);

          if (notes.length === 0) {
            return {
              period: periodLabel,
              message: "该周期内没有交易笔记",
              totalTrades: 0,
            };
          }

          // Compute stats
          const withPnl = notes.filter((n) => n.pnl != null);
          const totalPnl = withPnl.reduce((sum, n) => sum + n.pnl!, 0);
          const winTrades = withPnl.filter((n) => n.pnl! > 0);
          const lossTrades = withPnl.filter((n) => n.pnl! < 0);
          const winRate = withPnl.length > 0 ? (winTrades.length / withPnl.length) * 100 : 0;
          const avgWin =
            winTrades.length > 0
              ? winTrades.reduce((s, n) => s + n.pnl!, 0) / winTrades.length
              : 0;
          const avgLoss =
            lossTrades.length > 0
              ? lossTrades.reduce((s, n) => s + n.pnl!, 0) / lossTrades.length
              : 0;
          const profitFactor =
            avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : avgWin > 0 ? Infinity : 0;

          // Symbol breakdown
          const symbolMap = new Map<string, { count: number; pnl: number }>();
          for (const n of notes) {
            const entry = symbolMap.get(n.symbol) ?? { count: 0, pnl: 0 };
            entry.count++;
            if (n.pnl != null) entry.pnl += n.pnl;
            symbolMap.set(n.symbol, entry);
          }
          const symbolBreakdown = [...symbolMap.entries()]
            .sort((a, b) => b[1].pnl - a[1].pnl)
            .map(([sym, data]) => ({
              symbol: sym,
              trades: data.count,
              pnl: `¥${data.pnl.toFixed(2)}`,
            }));

          // Collect lessons and top tags
          const lessons = notes
            .filter((n) => n.lesson)
            .map((n) => ({ date: n.date, symbol: n.symbol, lesson: n.lesson! }));
          const tagCounts = new Map<string, number>();
          for (const n of notes) {
            for (const t of n.tags) {
              tagCounts.set(t, (tagCounts.get(t) ?? 0) + 1);
            }
          }
          const topTags = [...tagCounts.entries()]
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5)
            .map(([tag, count]) => ({ tag, count }));

          // Rating distribution
          const rated = notes.filter((n) => n.rating != null);
          const avgRating =
            rated.length > 0
              ? rated.reduce((sum, n) => sum + n.rating!, 0) / rated.length
              : 0;

          return {
            period: periodLabel,
            totalNotes: notes.length,
            tradesWithPnl: withPnl.length,
            totalPnl: `¥${totalPnl.toFixed(2)}`,
            winRate: `${winRate.toFixed(1)}%`,
            avgWin: `¥${avgWin.toFixed(2)}`,
            avgLoss: `¥${avgLoss.toFixed(2)}`,
            profitFactor: profitFactor === Infinity ? "∞" : profitFactor.toFixed(2),
            avgRating: avgRating > 0 ? avgRating.toFixed(1) : "N/A",
            symbolBreakdown,
            topTags,
            keyLessons: lessons.slice(0, 10),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
