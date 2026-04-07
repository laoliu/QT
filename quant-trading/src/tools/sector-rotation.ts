/**
 * sector_rotation tool — AI-callable for sector rotation strategy (板块轮动).
 *
 * Implements systematic sector rotation models:
 *   momentum       — momentum-based rotation (buy strong sectors)
 *   mean_reversion — buy oversold sectors, sell overbought
 *   multi_factor   — momentum + fund flow + breadth composite scoring
 *   economic_cycle — Merrill Clock economic cycle sector mapping
 *   signals        — combined rotation signals with allocation weights
 *   history        — sector rotation patterns over recent periods
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSectorRotationTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "sector_rotation",
    description:
      "板块轮动策略工具。系统化的板块轮动分析，帮助判断资金流向哪些板块、何时切换。\n" +
      "action=momentum 动量轮动（买入强势板块），\n" +
      "action=mean_reversion 均值回归轮动（买入超跌板块），\n" +
      "action=multi_factor 多因子轮动（动量+宽度+量能+一致性综合评分），\n" +
      "action=economic_cycle 美林时钟经济周期板块配置，\n" +
      "action=signals 综合轮动信号（融合多种方法生成最终推荐），\n" +
      "action=history 近期板块轮动历史（哪些板块在轮入/轮出）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["momentum", "mean_reversion", "multi_factor", "economic_cycle", "signals", "history"],
          description:
            "操作: momentum=动量轮动, mean_reversion=均值回归, multi_factor=多因子, " +
            "economic_cycle=经济周期, signals=综合信号, history=轮动历史",
        },
        topN: {
          type: "number",
          description: "推荐板块数量 (默认3)",
        },
        lookback: {
          type: "number",
          description: "回看天数 (action=momentum/mean_reversion, 默认20)",
        },
        days: {
          type: "number",
          description: "历史天数 (action=history, 默认60)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/sector_rotation.py",
        {
          action,
          topN: args.topN ?? 3,
          lookback: args.lookback ?? 20,
          days: args.days ?? 60,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        action,
        note: "板块轮动引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
