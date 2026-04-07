/**
 * execution_analysis tool — AI-callable for trade execution quality analysis (成交分析).
 *
 * Analyzes executed trades against market benchmarks:
 *   analyze_order     — single order execution quality (VWAP, slippage, market impact)
 *   slippage_report   — aggregate slippage analysis across trades
 *   vwap_compare      — compare fills vs VWAP
 *   execution_summary — overall execution quality summary with recommendations
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig, TradeRecord } from "../types.js";

function getTradesPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "trades.json");
}

async function loadTrades(config: QuantTradingConfig): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(getTradesPath(config), "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

export function createExecutionAnalysisTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "execution_analysis",
    description:
      "成交分析工具。分析交易执行质量，衡量滑点、VWAP对比、市场冲击。\n" +
      "action=analyze_order 单笔订单执行质量分析（VWAP/TWAP/滑点/市场冲击），\n" +
      "action=slippage_report 多笔交易的滑点汇总报告，\n" +
      "action=vwap_compare 成交价vs VWAP对比分析，\n" +
      "action=execution_summary 整体执行质量摘要（佣金/时段分布/改进建议）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["analyze_order", "slippage_report", "vwap_compare", "execution_summary"],
          description:
            "操作: analyze_order=单笔分析, slippage_report=滑点报告, " +
            "vwap_compare=VWAP对比, execution_summary=执行摘要",
        },
        symbol: {
          type: "string",
          description: "股票代码 (action=analyze_order)",
        },
        fillPrice: {
          type: "number",
          description: "成交价格 (action=analyze_order)",
        },
        orderPrice: {
          type: "number",
          description: "委托价格 (action=analyze_order)",
        },
        side: {
          type: "string",
          enum: ["buy", "sell"],
          description: "买卖方向 (action=analyze_order)",
        },
        quantity: {
          type: "number",
          description: "成交数量 (action=analyze_order)",
        },
        from: {
          type: "string",
          description: "起始日期 YYYY-MM-DD (过滤历史交易)",
        },
        to: {
          type: "string",
          description: "结束日期 YYYY-MM-DD (过滤历史交易)",
        },
        limit: {
          type: "number",
          description: "分析交易数量限制 (默认50)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      if (action === "analyze_order") {
        // Single order analysis
        const trade = {
          symbol: args.symbol as string,
          fillPrice: args.fillPrice as number,
          orderPrice: args.orderPrice as number,
          side: (args.side as string) ?? "buy",
          quantity: (args.quantity as number) ?? 100,
          fillTime: args.fillTime as string,
        };

        if (!trade.symbol || !trade.fillPrice) {
          return { error: "需要 symbol 和 fillPrice 参数" };
        }

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/execution_analysis.py",
          { action: "analyze_order", trade },
          config,
        );

        if (pyResult.ok && pyResult.data) return pyResult.data;
        return {
          action,
          note: "成交分析引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
        };
      }

      // For aggregate reports, load trades from history
      let trades = await loadTrades(config);
      const limit = (args.limit as number) ?? 50;

      // Filter by date range
      if (args.from) {
        const fromDate = args.from as string;
        trades = trades.filter((t) => {
          const ts = t.exitTime ?? t.entryTime ?? "";
          return ts >= fromDate;
        });
      }
      if (args.to) {
        const toDate = args.to as string;
        trades = trades.filter((t) => {
          const ts = t.exitTime ?? t.entryTime ?? "";
          return ts <= toDate + "T23:59:59";
        });
      }

      // Map to the format expected by Python engine
      const tradeDicts = trades.slice(-limit).map((t) => ({
        symbol: t.symbol,
        fillPrice: t.exitPrice ?? t.entryPrice,
        orderPrice: t.entryPrice,
        side: t.side === "buy" ? "buy" : "sell",
        quantity: t.quantity,
        fillTime: t.exitTime ?? t.entryTime,
        commission: t.commission,
        status: "filled",
        orderType: "limit",
      }));

      if (tradeDicts.length === 0) {
        return {
          action,
          note: "没有找到交易记录。请先进行交易或检查日期范围。",
          count: 0,
        };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/execution_analysis.py",
        { action, trades: tradeDicts },
        config,
      );

      if (pyResult.ok && pyResult.data) return pyResult.data;
      return {
        action,
        note: "成交分析引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
