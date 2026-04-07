/**
 * data_export tool — AI-callable interface for exporting trading data.
 *
 * Actions:
 *   export_portfolio   — 导出持仓数据
 *   export_trades      — 导出成交记录
 *   export_journal     — 导出交易笔记
 *   export_backtest    — 导出回测结果
 *   export_equity      — 导出权益曲线
 *   export_signals     — 导出交易信号
 *   list_fields        — 列出可用字段（用于字段筛选提示）
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { BacktestStore } from "../services/backtest-store.js";
import { DataExportService, type ExportFormat } from "../services/data-export.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { TradeJournal } from "../services/trade-journal.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { Portfolio, QuantTradingConfig, TradingSignal } from "../types.js";

async function loadPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  for (const name of ["portfolio-paper.json", "portfolio.json"]) {
    try {
      const raw = await fs.readFile(path.join(dir, name), "utf-8");
      return JSON.parse(raw) as Portfolio;
    } catch {
      continue;
    }
  }
  return { id: "default", name: "默认组合", cash: 0, totalValue: 0, positions: [], createdAt: "", updatedAt: "" };
}

async function loadSignals(config: QuantTradingConfig): Promise<TradingSignal[]> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  try {
    const raw = await fs.readFile(path.join(dir, "signals.json"), "utf-8");
    return JSON.parse(raw) as TradingSignal[];
  } catch {
    return [];
  }
}

export function createDataExportTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  deps: {
    tradeJournal: TradeJournal;
    backtestStore: BacktestStore;
    equityHistory: EquityHistory;
    getEngine: () => LiveEngine | null;
  },
) {
  const dataDir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const exportService = new DataExportService(dataDir);

  return {
    name: "data_export",
    description:
      "导出交易数据为CSV/Excel/JSON文件。\n" +
      "• action=export_portfolio — 导出持仓数据\n" +
      "• action=export_trades — 导出成交记录（需实盘引擎运行）\n" +
      "• action=export_journal — 导出交易笔记\n" +
      "• action=export_backtest — 导出回测结果（需指定 backtest_id）\n" +
      "• action=export_equity — 导出权益曲线\n" +
      "• action=export_signals — 导出交易信号\n" +
      "• action=list_fields — 列出指定数据源的可用字段\n" +
      "支持自定义字段筛选（fields参数）和时间范围过滤（start_date/end_date）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "export_portfolio",
            "export_trades",
            "export_journal",
            "export_backtest",
            "export_equity",
            "export_signals",
            "list_fields",
          ],
          description: "操作类型",
        },
        format: {
          type: "string",
          enum: ["csv", "excel", "json"],
          description: "导出格式 (默认 csv)",
        },
        fields: {
          type: "string",
          description: "要导出的字段，逗号分隔 (如 'symbol,name,quantity,pnl')。留空导出全部字段",
        },
        start_date: {
          type: "string",
          description: "开始日期 YYYY-MM-DD（过滤时间范围）",
        },
        end_date: {
          type: "string",
          description: "结束日期 YYYY-MM-DD（过滤时间范围）",
        },
        backtest_id: {
          type: "string",
          description: "回测ID（export_backtest 时必填）",
        },
        data_source: {
          type: "string",
          enum: ["portfolio", "trades", "journal", "backtest", "equity", "signals"],
          description: "数据源（list_fields 时使用）",
        },
        output_path: {
          type: "string",
          description: "自定义输出文件路径（可选）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const format = (args.format as ExportFormat) ?? "csv";
      const fields = args.fields
        ? (args.fields as string).split(",").map((f) => f.trim()).filter(Boolean)
        : undefined;
      const startDate = args.start_date as string | undefined;
      const endDate = args.end_date as string | undefined;
      const outputPath = args.output_path as string | undefined;

      const opts = { format, fields, startDate, endDate, outputPath };

      try {
        switch (action) {
          case "export_portfolio": {
            const engine = deps.getEngine();
            let data: Record<string, unknown>[];
            if (engine && engine.getStatus() === "running") {
              const positions = engine.getPositions();
              data = positions.map((p) => ({ ...p } as unknown as Record<string, unknown>));
            } else {
              const portfolio = await loadPortfolio(config);
              data = portfolio.positions.map(
                (p) => ({ ...p } as unknown as Record<string, unknown>),
              );
            }
            if (!data.length) return { error: "持仓为空，无数据可导出" };
            const result = exportService.export(data, opts);
            return {
              message: `持仓数据已导出`,
              ...result,
            };
          }

          case "export_trades": {
            const engine = deps.getEngine();
            if (!engine || engine.getStatus() !== "running") {
              return { error: "实盘引擎未运行，无法获取成交记录。请先启动 live_trading" };
            }
            const mgr = engine.orderManager;
            if (!mgr) return { error: "订单管理器未初始化" };
            const trades = await mgr.getTrades();
            if (!trades.length) return { error: "暂无成交记录" };
            const data = trades.map(
              (t) => ({ ...t } as unknown as Record<string, unknown>),
            );
            const result = exportService.export(data, opts);
            return {
              message: `成交记录已导出`,
              ...result,
            };
          }

          case "export_journal": {
            const notes = deps.tradeJournal.getRecent(9999);
            if (!notes.length) return { error: "交易笔记为空" };
            const data = notes.map(
              (n) => ({ ...n } as unknown as Record<string, unknown>),
            );
            const result = exportService.export(data, opts);
            return {
              message: `交易笔记已导出`,
              ...result,
            };
          }

          case "export_backtest": {
            const backtestId = args.backtest_id as string;
            if (!backtestId) return { error: "请提供 backtest_id" };
            const record = deps.backtestStore.getFullResult(backtestId);
            if (!record) return { error: `未找到回测记录: ${backtestId}` };

            // Export signals as the main data rows
            const signals = record.result.signals;
            if (!signals.length) return { error: "该回测无交易信号" };
            const data = signals.map(
              (s) => ({ ...s } as unknown as Record<string, unknown>),
            );

            // Add backtest summary as metadata
            const result = exportService.export(data, opts);
            return {
              message: `回测 ${backtestId} 信号已导出`,
              backtestSummary: {
                strategyId: record.result.strategyId,
                totalReturn: `${record.result.totalReturn.toFixed(2)}%`,
                sharpeRatio: record.result.sharpeRatio.toFixed(2),
                maxDrawdown: `${record.result.maxDrawdown.toFixed(2)}%`,
                totalTrades: record.result.totalTrades,
              },
              ...result,
            };
          }

          case "export_equity": {
            const snapshots = deps.equityHistory.getAll();
            if (!snapshots.length) return { error: "权益曲线为空" };
            const data = snapshots.map(
              (s) => ({ ...s } as unknown as Record<string, unknown>),
            );
            const result = exportService.export(data, opts);
            return {
              message: `权益曲线已导出`,
              ...result,
            };
          }

          case "export_signals": {
            const signals = await loadSignals(config);
            if (!signals.length) return { error: "暂无交易信号" };
            const data = signals.map(
              (s) => ({ ...s } as unknown as Record<string, unknown>),
            );
            const result = exportService.export(data, opts);
            return {
              message: `交易信号已导出`,
              ...result,
            };
          }

          case "list_fields": {
            const source = (args.data_source as string) ?? "portfolio";
            let sampleData: Record<string, unknown>[];

            switch (source) {
              case "portfolio": {
                const portfolio = await loadPortfolio(config);
                sampleData = portfolio.positions.map(
                  (p) => ({ ...p } as unknown as Record<string, unknown>),
                );
                break;
              }
              case "journal": {
                const notes = deps.tradeJournal.getRecent(5);
                sampleData = notes.map(
                  (n) => ({ ...n } as unknown as Record<string, unknown>),
                );
                break;
              }
              case "equity": {
                const snaps = deps.equityHistory.getAll().slice(0, 5);
                sampleData = snaps.map(
                  (s) => ({ ...s } as unknown as Record<string, unknown>),
                );
                break;
              }
              case "signals": {
                const sigs = await loadSignals(config);
                sampleData = sigs.slice(0, 5).map(
                  (s) => ({ ...s } as unknown as Record<string, unknown>),
                );
                break;
              }
              default:
                return { error: `未知数据源: ${source}` };
            }

            if (!sampleData.length) return { error: `${source} 暂无数据` };
            const availableFields = exportService.getAvailableFields(sampleData);
            return { dataSource: source, fields: availableFields };
          }

          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: msg };
      }
    },
  };
}
