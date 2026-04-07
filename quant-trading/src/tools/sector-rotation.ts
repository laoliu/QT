/**
 * sector_rotation tool — AI-callable interface for sector rotation analysis.
 *
 * Provides momentum-based sector rotation signals that go beyond the existing
 * sector_analysis tool (which only shows daily rankings):
 *
 * Actions:
 *   momentum       — multi-period momentum ranking of sectors (4w, 8w, 12w relative strength)
 *   rotation_signal — identify sectors entering/exiting leadership
 *   portfolio_check — check which of your held positions are in leading vs lagging sectors
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";

export function createSectorRotationTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
) {
  return {
    name: "sector_rotation",
    description:
      "板块轮动分析工具。基于多周期动量分析板块轮动趋势。\n" +
      "• action=momentum — 多周期(1周/4周/12周)板块动量排名，发现强势/弱势板块\n" +
      "• action=rotation_signal — 板块轮动信号，识别正在进入/退出领涨的板块\n" +
      "• action=portfolio_check — 检查持仓股票所在板块的强弱表现",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["momentum", "rotation_signal", "portfolio_check"],
          description: "操作类型",
        },
        limit: {
          type: "number",
          description: "返回板块数量限制 (默认20)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const limit = (args.limit as number) ?? 20;

      switch (action) {
        case "momentum": {
          try {
            const result = await runPythonScript<{
              sectors?: Array<{
                name: string;
                change_1w?: number;
                change_4w?: number;
                change_12w?: number;
                momentum_score?: number;
                trend?: string;
                leader_stock?: string;
              }>;
            }>(
              "engines/sector_rotation.py",
              { action: "momentum", limit },
              config,
            );

            if (!result.ok || !result.data?.sectors) {
              return { error: "获取板块动量数据失败", detail: result.error };
            }

            return {
              message: `板块多周期动量排名 (Top ${limit})`,
              sectors: result.data.sectors,
              note: "momentum_score 综合1周/4周/12周涨幅加权计算，trend: rising=走强, falling=走弱, stable=震荡",
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        case "rotation_signal": {
          try {
            const result = await runPythonScript<{
              entering_leadership?: Array<{
                name: string;
                signal: string;
                short_momentum: number;
                long_momentum: number;
              }>;
              exiting_leadership?: Array<{
                name: string;
                signal: string;
                short_momentum: number;
                long_momentum: number;
              }>;
              summary?: string;
            }>(
              "engines/sector_rotation.py",
              { action: "rotation_signal", limit },
              config,
            );

            if (!result.ok) {
              return { error: "获取轮动信号失败", detail: result.error };
            }

            return {
              entering_leadership: result.data?.entering_leadership ?? [],
              exiting_leadership: result.data?.exiting_leadership ?? [],
              summary: result.data?.summary ?? "无明显轮动信号",
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        case "portfolio_check": {
          const positions = getPositions().filter(
            (p) => p.quantity > 0 && p.market === "cn_stock",
          );

          if (positions.length === 0) {
            return { message: "当前无A股持仓" };
          }

          try {
            const symbols = positions.map((p) => p.symbol);
            const result = await runPythonScript<{
              positions?: Array<{
                symbol: string;
                name?: string;
                sector?: string;
                sector_momentum?: number;
                sector_trend?: string;
                sector_rank?: number;
                suggestion?: string;
              }>;
              summary?: {
                leading_count: number;
                lagging_count: number;
                neutral_count: number;
              };
            }>(
              "engines/sector_rotation.py",
              {
                action: "portfolio_check",
                symbols: symbols.join(","),
              },
              config,
            );

            if (!result.ok || !result.data?.positions) {
              return { error: "获取持仓板块分析失败", detail: result.error };
            }

            return {
              positions: result.data.positions,
              summary: result.data.summary,
              positionsCount: positions.length,
            };
          } catch (err) {
            return { error: `查询失败: ${(err as Error).message}` };
          }
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
