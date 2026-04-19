/**
 * aipha_backtest tool — AI-callable for advanced backtesting (Aipha engine).
 *
 * Actions:
 *   run           — Run backtest with inline params
 *   run_from_yaml — Run from YAML config file
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createAiphaBacktestTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "aipha_backtest",
    description:
      "高级回测工具（Aipha 回测引擎）。事件驱动回测引擎，支持 T+N 交割、多成交模式、精确费用模型。\n" +
      "• action=run — 直接运行回测（指定策略、股票、区间）\n" +
      "• action=run_from_yaml — 从 YAML 配置文件运行\n\n" +
      "特性：NextBarOpen / VWAP 成交模式，A股 T+1 / 可转债 T+0 交割规则，\n" +
      "佣金+印花税+过户费精确计算，滑点模型（固定/线性冲击/平方根冲击），风控约束。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["run", "run_from_yaml"],
          description: "操作类型: run=直接运行, run_from_yaml=YAML配置运行",
        },
        strategyId: {
          type: "string",
          description: "策略名称（action=run 时使用）",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "股票代码列表，如 ['600519', '000858']",
        },
        startDate: {
          type: "string",
          description: "回测开始日期（YYYY-MM-DD）",
        },
        endDate: {
          type: "string",
          description: "回测结束日期（YYYY-MM-DD）",
        },
        initialCash: {
          type: "number",
          description: "初始资金（默认100万）",
        },
        fillModel: {
          type: "string",
          enum: ["next_bar_open", "next_bar_close", "vwap"],
          description: "成交模式（默认 next_bar_open）",
        },
        slippageBps: {
          type: "number",
          description: "滑点（基点，默认10）",
        },
        dataSource: {
          type: "string",
          enum: ["akshare", "tushare"],
          description: "数据源（默认 akshare）",
        },
        parameters: {
          type: "object",
          description: "策略参数",
        },
        yamlPath: {
          type: "string",
          description: "YAML 配置文件路径（action=run_from_yaml 时使用）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "run";

      const pyResult = await runPythonScript<{
          strategyId?: string;
          startDate?: string;
          endDate?: string;
          initialCapital?: number;
          finalValue?: number;
          totalReturn?: number;
          totalTrades?: number;
          totalFees?: number;
          sharpeRatio?: number;
          maxDrawdown?: number;
          dailySnapshots?: Array<Record<string, unknown>>;
          trades?: Array<Record<string, unknown>>;
      }>(
        "engines/aipha_backtest_engine.py",
        {
          action,
          strategyId: args.strategyId,
          symbols: args.symbols,
          startDate: args.startDate,
          endDate: args.endDate,
          initialCash: args.initialCash,
          fillModel: args.fillModel,
          slippageBps: args.slippageBps,
          dataSource: args.dataSource,
          parameters: args.parameters,
          yamlPath: args.yamlPath,
        },
        config,
        { timeoutMs: 300_000 },
      );

      if (pyResult.ok && pyResult.data) {
        const d = pyResult.data;
        // 添加展示提示
        const result: Record<string, unknown> = { ...d };
        if (d.dailySnapshots && d.dailySnapshots.length > 30) {
          result.displayHint = `回测共${String(d.dailySnapshots.length)}个交易日，建议展示关键指标概要+收益曲线描述。`;
        }
        return result;
      }

      return {
        error: pyResult.error ?? "Aipha 回测引擎执行失败",
      };
    },
  };
}
