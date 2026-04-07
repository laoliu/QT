/**
 * stock_calendar tool — AI-callable for comprehensive stock event calendar.
 *
 * Aggregates events beyond earnings: restricted share unlocks, shareholder
 * meetings, dividend ex-dates, insider buy/sell, repurchases, etc.
 *
 * Actions:
 *   upcoming_events   — All upcoming events across market (next N days)
 *   stock_events      — Events for specific stock(s)
 *   watchlist_events  — Events for watchlist stocks with alerts
 *   portfolio_events  — Events for portfolio holdings with risk alerts
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCalendarTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dataDir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const watchlistPath = path.join(dataDir, "watchlist.json");
  const portfolioPath = path.join(dataDir, "portfolio-paper.json");

  return {
    name: "stock_calendar",
    description:
      "个股事件日历与催化剂追踪工具。聚合限售股解禁、股东大会、财报披露、分红除权、增减持公告、股份回购等关键事件。\n" +
      "• action=upcoming_events — 近期（默认30天）所有类型事件日历\n" +
      "• action=stock_events — 指定股票的事件日历（需提供symbols）\n" +
      "• action=watchlist_events — 自选股事件日历+预警\n" +
      "• action=portfolio_events — 持仓股事件日历+风险预警",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming_events", "stock_events", "watchlist_events", "portfolio_events"],
          description:
            "操作类型: upcoming_events=近期事件, stock_events=个股事件, " +
            "watchlist_events=自选股事件, portfolio_events=持仓事件",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: '股票代码列表（stock_events时必填），如 ["600519", "000858"]',
        },
        days: {
          type: "number",
          description: "查询未来多少天的事件（默认30）",
        },
        event_types: {
          type: "array",
          items: { type: "string" },
          description:
            '事件类型过滤，可选: ["unlock", "dividend", "shareholder_meeting", "insider", "earnings", "repurchase"]。默认全部',
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认50",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "upcoming_events";
      const days = (args.days as number) ?? 30;
      const limit = (args.limit as number) ?? 50;

      const pyArgs: Record<string, unknown> = {
        action,
        days,
        limit,
        symbols: args.symbols,
        event_types: args.event_types,
        watchlist_path: watchlistPath,
        portfolio_path: portfolioPath,
      };

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/stock_calendar_engine.py",
        pyArgs,
        config,
        { timeoutMs: 60_000 },
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "事件日历数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
