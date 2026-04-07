/**
 * data_export tool — AI-callable for exporting trading data to CSV/XLSX files.
 *
 * Actions:
 *   export   — Export data to file (positions/orders/trades/backtest/screen/equity)
 *   list     — List available export files
 *   cleanup  — Remove expired export files
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { DataExportService, ExportFormat, ExportTarget } from "../services/data-export.js";

const VALID_TARGETS: ExportTarget[] = [
  "positions",
  "orders",
  "trades",
  "backtest",
  "screen",
  "equity",
];
const VALID_FORMATS: ExportFormat[] = ["csv", "xlsx"];

export function createDataExportTool(
  _api: OpenClawPluginApi,
  exportService: DataExportService,
) {
  return {
    name: "data_export",
    description:
      "数据导出工具。将交易数据导出为 CSV 或 Excel 文件。\n" +
      "• action=export — 导出数据（需提供 target 和 format）\n" +
      "  target: positions(持仓) / orders(订单) / trades(成交) / backtest(回测) / screen(选股) / equity(权益曲线)\n" +
      "  format: csv / xlsx\n" +
      "• action=list — 列出已有导出文件\n" +
      "• action=cleanup — 清理过期导出文件",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["export", "list", "cleanup"],
          description: "操作类型: export=导出, list=列表, cleanup=清理",
        },
        target: {
          type: "string",
          enum: VALID_TARGETS,
          description:
            "导出目标: positions=持仓, orders=订单, trades=成交, backtest=回测, screen=选股, equity=权益曲线",
        },
        format: {
          type: "string",
          enum: VALID_FORMATS,
          description: "导出格式: csv 或 xlsx (默认 csv)",
        },
        id: {
          type: "string",
          description: "指定导出某次回测(backtest)或选股(screen)的详细数据，传入对应 ID",
        },
        mode: {
          type: "string",
          enum: ["paper", "live"],
          description: "数据模式：paper=模拟盘, live=实盘（默认 paper）",
        },
        content: {
          type: "string",
          enum: ["signals", "equity"],
          description: "回测导出内容：signals=信号列表（默认）, equity=权益曲线",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "export";

      switch (action) {
        case "export": {
          const target = args.target as ExportTarget | undefined;
          if (!target || !VALID_TARGETS.includes(target)) {
            return {
              error: `请指定有效的 target: ${VALID_TARGETS.join(", ")}`,
            };
          }
          const format = (args.format as ExportFormat) ?? "csv";
          if (!VALID_FORMATS.includes(format)) {
            return { error: `format 必须是 csv 或 xlsx` };
          }

          const options: Record<string, unknown> = {};
          if (args.id) options.id = args.id;
          if (args.mode) options.mode = args.mode;
          if (args.content) options.content = args.content;

          const result = await exportService.export(target, format, options);
          if (!result.ok) {
            return { error: result.error };
          }
          return {
            message: `已导出 ${result.rowCount} 条 ${target} 数据`,
            filename: result.filename,
            rowCount: result.rowCount,
            downloadUrl: `/quant/export/${result.filename}`,
          };
        }

        case "list": {
          const fs = await import("node:fs/promises");
          const dir = exportService.getExportDir();
          try {
            const files = await fs.readdir(dir);
            const fileInfos = await Promise.all(
              files
                .filter((f) => f.endsWith(".csv") || f.endsWith(".xlsx"))
                .map(async (f) => {
                  const stat = await fs.stat(`${dir}/${f}`);
                  return {
                    filename: f,
                    size: stat.size,
                    createdAt: stat.mtime.toISOString(),
                    downloadUrl: `/quant/export/${f}`,
                  };
                }),
            );
            return { count: fileInfos.length, files: fileInfos };
          } catch {
            return { count: 0, files: [] };
          }
        }

        case "cleanup": {
          const removed = await exportService.cleanup();
          return { message: `已清理 ${removed} 个过期文件` };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
