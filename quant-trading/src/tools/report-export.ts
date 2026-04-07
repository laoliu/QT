/**
 * report_export tool — export trading reports to CSV / JSON files.
 *
 * Actions:
 *   trades      — export trade records (from live engine or paper portfolio)
 *   positions   — export current position snapshot
 *   backtest    — export backtest result (equity curve + signals + summary)
 *   equity      — export daily equity (net value) history
 *
 * Output formats: CSV (default, best compatibility), JSON (optional).
 * Files are saved to a user-specified path or the data directory.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import type {
  Portfolio,
  QuantTradingConfig,
  TradeRecord,
} from "../types.js";

// ── Helpers ─────────────────────────────────────────────────────

function getDataDir(config: QuantTradingConfig): string {
  return config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

function getPortfolioPath(config: QuantTradingConfig): string {
  return path.join(getDataDir(config), "portfolio-paper.json");
}

async function loadPaperPortfolio(config: QuantTradingConfig): Promise<Portfolio | null> {
  try {
    const raw = await fs.readFile(getPortfolioPath(config), "utf-8");
    return JSON.parse(raw) as Portfolio;
  } catch {
    return null;
  }
}

/** Escape a value for CSV: wrap in quotes if it contains comma, quote, or newline. */
function csvEscape(value: unknown): string {
  if (value == null) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/** Convert an array of objects to a CSV string. */
function toCsv(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return "";
  const headers = Object.keys(rows[0]);
  const lines = [
    headers.join(","),
    ...rows.map((row) => headers.map((h) => csvEscape(row[h])).join(",")),
  ];
  return lines.join("\n") + "\n";
}

/** Resolve the output file path. */
function resolveOutputPath(
  config: QuantTradingConfig,
  outputPath: string | undefined,
  defaultName: string,
): string {
  if (outputPath) {
    // If user provided a directory, append default name
    if (outputPath.endsWith("/") || outputPath.endsWith("\\")) {
      return path.join(outputPath, defaultName);
    }
    // Resolve relative paths against data dir
    if (!path.isAbsolute(outputPath)) {
      return path.join(getDataDir(config), outputPath);
    }
    return outputPath;
  }
  return path.join(getDataDir(config), defaultName);
}

function timestamp(): string {
  return new Date().toISOString().slice(0, 10).replace(/-/g, "");
}

// ── Tool factory ────────────────────────────────────────────────

export function createReportExportTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  backtestStore?: BacktestStore,
  equityHistory?: EquityHistory,
) {
  return {
    name: "report_export",
    description:
      "交易报表导出工具。将交易记录、持仓快照、回测结果、每日净值导出为 CSV 或 JSON 文件。\n" +
      "• action=trades — 导出交易明细（日期范围内所有买卖记录）\n" +
      "• action=positions — 导出当前持仓快照\n" +
      "• action=backtest — 导出回测结果（需提供 backtest_id）\n" +
      "• action=equity — 导出每日净值曲线",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["trades", "positions", "backtest", "equity"],
          description: "导出类型",
        },
        format: {
          type: "string",
          enum: ["csv", "json"],
          description: "输出格式，默认 csv",
        },
        output_path: {
          type: "string",
          description:
            "输出文件路径（绝对或相对于 data 目录）。不指定则自动保存到 data 目录",
        },
        start_date: {
          type: "string",
          description: "起始日期 YYYY-MM-DD（trades/equity 可选过滤）",
        },
        end_date: {
          type: "string",
          description: "结束日期 YYYY-MM-DD（trades/equity 可选过滤）",
        },
        backtest_id: {
          type: "string",
          description: "回测记录ID（action=backtest 时必填）",
        },
        symbol: {
          type: "string",
          description: "按标的过滤（trades/positions 可选）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const format = (args.format as string) ?? "csv";
      const outputPath = args.output_path as string | undefined;
      const startDate = args.start_date as string | undefined;
      const endDate = args.end_date as string | undefined;
      const symbol = args.symbol as string | undefined;

      switch (action) {
        // ── Export trade records ───────────────────────────────
        case "trades": {
          let trades: TradeRecord[] = [];

          // Try live engine first
          const engine = getEngine?.();
          if (engine && engine.orderManager) {
            trades = await engine.orderManager.getTrades(symbol);
          }

          // Filter by date range
          if (startDate || endDate) {
            trades = trades.filter((t) => {
              if (startDate && t.entryTime < startDate) return false;
              if (endDate && t.entryTime > endDate + "T23:59:59") return false;
              return true;
            });
          }

          if (trades.length === 0) {
            return { error: "没有找到交易记录。请确认实盘引擎已启动且有成交记录" };
          }

          const rows = trades.map((t) => ({
            交易ID: t.tradeId,
            标的代码: t.symbol,
            标的名称: t.name ?? "",
            市场: t.market,
            方向: t.side,
            入场时间: t.entryTime,
            出场时间: t.exitTime ?? "",
            入场价: t.entryPrice,
            出场价: t.exitPrice ?? "",
            数量: t.quantity,
            毛利: t.grossPnl,
            手续费: t.commission,
            净利: t.netPnl,
            持仓天数: t.holdingDays ?? "",
            策略ID: t.strategyId ?? "",
          }));

          const defaultName = `trades_${timestamp()}.${format}`;
          const filePath = resolveOutputPath(config, outputPath, defaultName);
          const content =
            format === "json" ? JSON.stringify(rows, null, 2) : toCsv(rows);

          await fs.mkdir(path.dirname(filePath), { recursive: true });
          await fs.writeFile(filePath, content, "utf-8");

          return {
            success: true,
            message: `已导出 ${trades.length} 条交易记录`,
            file: filePath,
            format,
            count: trades.length,
          };
        }

        // ── Export position snapshot ───────────────────────────
        case "positions": {
          type PositionRow = {
            标的代码: string;
            标的名称: string;
            市场: string;
            数量: number;
            可用数量: number | string;
            成本价: number;
            现价: string;
            市值: string;
            浮动盈亏: string;
            盈亏比例: string;
          };

          let rows: PositionRow[] = [];

          // Try live engine
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const positions = engine.getPositions();
            rows = positions.map((p) => ({
              标的代码: p.symbol,
              标的名称: p.name ?? "",
              市场: p.market,
              数量: p.quantity,
              可用数量: p.availableQty,
              成本价: p.avgCost,
              现价: p.currentPrice != null ? p.currentPrice.toFixed(3) : "N/A",
              市值: p.marketValue != null ? p.marketValue.toFixed(2) : "N/A",
              浮动盈亏:
                p.unrealizedPnl != null ? p.unrealizedPnl.toFixed(2) : "N/A",
              盈亏比例:
                p.unrealizedPnlPct != null
                  ? (p.unrealizedPnlPct * 100).toFixed(2) + "%"
                  : "N/A",
            }));
          } else {
            // Fallback to paper portfolio
            const portfolio = await loadPaperPortfolio(config);
            if (portfolio) {
              rows = portfolio.positions.map((p) => ({
                标的代码: p.symbol,
                标的名称: p.name ?? "",
                市场: p.market,
                数量: p.quantity,
                可用数量: "N/A",
                成本价: p.avgCost,
                现价:
                  p.currentPrice != null ? p.currentPrice.toFixed(3) : "N/A",
                市值:
                  p.marketValue != null ? p.marketValue.toFixed(2) : "N/A",
                浮动盈亏:
                  p.unrealizedPnl != null
                    ? p.unrealizedPnl.toFixed(2)
                    : "N/A",
                盈亏比例:
                  p.unrealizedPnlPct != null
                    ? p.unrealizedPnlPct.toFixed(2) + "%"
                    : "N/A",
              }));
            }
          }

          // Filter by symbol if provided
          if (symbol) {
            rows = rows.filter((r) => r.标的代码 === symbol);
          }

          if (rows.length === 0) {
            return { error: "当前没有持仓记录" };
          }

          const defaultName = `positions_${timestamp()}.${format}`;
          const filePath = resolveOutputPath(config, outputPath, defaultName);
          const content =
            format === "json" ? JSON.stringify(rows, null, 2) : toCsv(rows);

          await fs.mkdir(path.dirname(filePath), { recursive: true });
          await fs.writeFile(filePath, content, "utf-8");

          return {
            success: true,
            message: `已导出 ${rows.length} 条持仓记录`,
            file: filePath,
            format,
            count: rows.length,
          };
        }

        // ── Export backtest result ─────────────────────────────
        case "backtest": {
          const backtestId = args.backtest_id as string;
          if (!backtestId) {
            return { error: "请提供 backtest_id" };
          }
          if (!backtestStore) {
            return { error: "回测存储未初始化" };
          }

          const record = backtestStore.getFullResult(backtestId);
          if (!record) {
            return { error: `未找到回测记录: ${backtestId}` };
          }

          const result = record.result;

          if (format === "json") {
            const filePath = resolveOutputPath(
              config,
              outputPath,
              `backtest_${backtestId}_${timestamp()}.json`,
            );
            await fs.mkdir(path.dirname(filePath), { recursive: true });
            await fs.writeFile(
              filePath,
              JSON.stringify(
                {
                  id: record.id,
                  createdAt: record.createdAt,
                  engine: record.engine,
                  summary: {
                    strategyId: result.strategyId,
                    symbols: result.symbols,
                    startDate: result.startDate,
                    endDate: result.endDate,
                    initialCash: result.initialCash,
                    finalValue: result.finalValue,
                    totalReturn: result.totalReturn,
                    annualizedReturn: result.annualizedReturn,
                    maxDrawdown: result.maxDrawdown,
                    sharpeRatio: result.sharpeRatio,
                    winRate: result.winRate,
                    totalTrades: result.totalTrades,
                    totalFees: result.totalFees,
                  },
                  equityCurve: result.equityCurve,
                  signals: result.signals,
                  monthlyReturns: result.monthlyReturns,
                },
                null,
                2,
              ),
              "utf-8",
            );

            return {
              success: true,
              message: `已导出回测报告 (JSON)`,
              file: filePath,
              format: "json",
            };
          }

          // CSV: export equity curve as the primary sheet
          const equityRows = (result.equityCurve ?? []).map((e) => ({
            日期: e.date,
            净值: e.value,
          }));

          const signalRows = result.signals.map((s) => ({
            日期: new Date(s.timestamp).toISOString().slice(0, 10),
            标的: s.symbol,
            方向: s.direction,
            价格: s.price,
            原因: s.reason ?? "",
            策略: s.strategyId,
          }));

          // Combine summary + equity curve + signals into a single CSV
          const summarySection = [
            `# 回测报告: ${result.strategyId}`,
            `# 标的: ${result.symbols.join(",")}`,
            `# 期间: ${result.startDate} ~ ${result.endDate}`,
            `# 初始资金: ${result.initialCash}`,
            `# 最终价值: ${result.finalValue}`,
            `# 总收益率: ${result.totalReturn}%`,
            `# 年化收益率: ${result.annualizedReturn}%`,
            `# 最大回撤: ${result.maxDrawdown}%`,
            `# 夏普比率: ${result.sharpeRatio}`,
            `# 胜率: ${result.winRate}%`,
            `# 总交易次数: ${result.totalTrades}`,
            "",
          ].join("\n");

          const csvContent =
            summarySection +
            "## 净值曲线\n" +
            toCsv(equityRows) +
            "\n## 交易信号\n" +
            toCsv(signalRows);

          const filePath = resolveOutputPath(
            config,
            outputPath,
            `backtest_${backtestId}_${timestamp()}.csv`,
          );
          await fs.mkdir(path.dirname(filePath), { recursive: true });
          await fs.writeFile(filePath, csvContent, "utf-8");

          return {
            success: true,
            message: `已导出回测报告 (CSV): ${result.strategyId} / ${result.symbols.join(",")}`,
            file: filePath,
            format: "csv",
            equityPoints: equityRows.length,
            signalCount: signalRows.length,
          };
        }

        // ── Export equity (net value) history ──────────────────
        case "equity": {
          if (!equityHistory) {
            return { error: "净值历史服务未初始化" };
          }

          let snapshots = equityHistory.getAll();

          // Filter by date range
          if (startDate || endDate) {
            snapshots = snapshots.filter((s) => {
              const d = s.date.slice(0, 10);
              if (startDate && d < startDate) return false;
              if (endDate && d > endDate) return false;
              return true;
            });
          }

          if (snapshots.length === 0) {
            return { error: "没有净值历史数据" };
          }

          const rows = snapshots.map((s) => ({
            日期: s.date,
            总权益: s.equity,
            现金: s.cash,
            市值: s.marketValue,
            当日盈亏: s.dailyPnl,
            持仓数: s.positionCount,
            基准: s.benchmark ?? "",
          }));

          const defaultName = `equity_${timestamp()}.${format}`;
          const filePath = resolveOutputPath(config, outputPath, defaultName);
          const content =
            format === "json" ? JSON.stringify(rows, null, 2) : toCsv(rows);

          await fs.mkdir(path.dirname(filePath), { recursive: true });
          await fs.writeFile(filePath, content, "utf-8");

          return {
            success: true,
            message: `已导出 ${snapshots.length} 条净值记录`,
            file: filePath,
            format,
            count: snapshots.length,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
