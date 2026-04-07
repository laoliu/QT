/**
 * investment_planner tool — AI-callable for generating comprehensive investment plans.
 *
 * Actions:
 *   plan         — Generate a full investment plan (capital target, strategy, stock selection)
 *   screen       — Screen stocks based on the plan's strategy criteria
 *   monitor      — Check portfolio against plan and generate adjustment signals
 *   update_plan  — Update an existing plan with new market data
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createInvestmentPlannerTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "investment_planner",
    description:
      "投资计划生成器。根据初始资金、目标收益、投资期限和风险偏好，自动生成完整的投资计划（含选股、仓位配置、止盈止损规则和定期监控方案）。\n" +
      "• action=plan — 生成完整投资计划（需指定initial_capital和target_capital）\n" +
      "• action=screen — 根据计划策略筛选候选股票\n" +
      "• action=monitor — 检查持仓是否偏离计划并生成调整信号\n" +
      "• action=update_plan — 用最新市场数据更新计划",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["plan", "screen", "monitor", "update_plan"],
          description: "操作类型",
        },
        initial_capital: {
          type: "number",
          description: "初始资金（元），如 500000",
        },
        target_capital: {
          type: "number",
          description: "目标资金（元），如 1000000",
        },
        timeframe_months: {
          type: "number",
          description: "投资期限（月），默认12",
        },
        risk_tolerance: {
          type: "string",
          enum: ["low", "medium", "high", "aggressive"],
          description: "风险偏好: low=保守, medium=稳健, high=积极, aggressive=激进",
        },
        strategy_preference: {
          type: "string",
          enum: ["auto", "momentum", "value", "growth", "swing", "mixed"],
          description: "策略偏好: auto=自动推荐, momentum=动量, value=价值, growth=成长, swing=波段, mixed=混合",
        },
        plan_id: {
          type: "string",
          description: "已有计划ID（monitor/update_plan时使用）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "plan";

      if (action === "plan") {
        const initialCapital = args.initial_capital as number;
        const targetCapital = args.target_capital as number;
        if (!initialCapital || !targetCapital) {
          return { error: "请提供 initial_capital（初始资金）和 target_capital（目标资金）" };
        }

        const timeframeMonths = (args.timeframe_months as number) ?? 12;
        const riskTolerance = (args.risk_tolerance as string) ?? "medium";
        const strategyPreference = (args.strategy_preference as string) ?? "auto";

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "scripts/investment_planner.py",
          {
            action: "plan",
            initial_capital: initialCapital,
            target_capital: targetCapital,
            timeframe_months: timeframeMonths,
            risk_tolerance: riskTolerance,
            strategy_preference: strategyPreference,
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "投资计划生成失败" };
        }

        return {
          action: "plan",
          ...pyResult.data,
          disclaimer: "以上投资计划仅供参考，不构成投资建议。请根据个人实际情况做出投资决策。",
        };
      }

      if (action === "screen") {
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "scripts/investment_planner.py",
          {
            action: "screen",
            plan_id: args.plan_id,
            risk_tolerance: args.risk_tolerance ?? "medium",
            strategy_preference: args.strategy_preference ?? "auto",
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "计划选股失败" };
        }

        return { action: "screen", ...pyResult.data };
      }

      if (action === "monitor") {
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "scripts/investment_planner.py",
          {
            action: "monitor",
            plan_id: args.plan_id,
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "计划监控检查失败" };
        }

        return { action: "monitor", ...pyResult.data };
      }

      if (action === "update_plan") {
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "scripts/investment_planner.py",
          {
            action: "update_plan",
            plan_id: args.plan_id,
          },
          config,
        );

        if (!pyResult.ok) {
          return { error: pyResult.error ?? "计划更新失败" };
        }

        return { action: "update_plan", ...pyResult.data };
      }

      return { error: `未知操作: ${action}` };
    },
  };
}
