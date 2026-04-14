/**
 * portfolio_optimizer tool — AI-callable for portfolio optimization & validation.
 *
 * Actions:
 *   optimize          — Portfolio weight optimization (equal/risk_parity/min_variance/max_sharpe)
 *   combine_strategies — Multi-strategy combination
 *   detect_regime     — Market regime detection
 *   validate          — Backtest quality validation (DSR/PBO/quality gates)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createPortfolioOptimizerTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "portfolio_optimizer",
    description:
      "组合优化与验证工具（Aipha 组合框架）。\n" +
      "• action=optimize — 组合权重优化（等权/风险平价/最小方差/最大夏普）\n" +
      "• action=combine_strategies — 多策略组合（等权/风险平价/动量倾斜/滚动优化）\n" +
      "• action=detect_regime — 市场状态识别（牛市/熊市/震荡/高波动）\n" +
      "• action=validate — 回测质量验证（缩水夏普比/过拟合概率/质量门禁）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["optimize", "combine_strategies", "detect_regime", "validate"],
          description: "操作类型",
        },
        method: {
          type: "string",
          enum: ["equal", "risk_parity", "min_variance", "max_sharpe", "equal_weight", "momentum_tilt", "performance_weighted"],
          description: "优化/组合方法",
        },
        // optimize 参数
        returns: {
          type: "object",
          description: "收益率数据（optimize 时使用），格式: {symbol: [returns...]}",
        },
        // combine_strategies 参数
        strategyNavs: {
          type: "object",
          description: "策略净值数据（combine_strategies 时使用），格式: {strategyName: [navs...]}",
        },
        // detect_regime 参数
        prices: {
          type: "array",
          items: { type: "number" },
          description: "价格序列（detect_regime 时使用）",
        },
        // validate 参数
        sharpe: {
          type: "number",
          description: "策略夏普比（validate 时使用）",
        },
        nTrials: {
          type: "number",
          description: "尝试次数（validate 时使用，计算缩水夏普比）",
        },
        nObs: {
          type: "number",
          description: "观测值数量（validate 时使用）",
        },
        maxDrawdown: {
          type: "number",
          description: "最大回撤（validate 时使用）",
        },
        totalTrades: {
          type: "number",
          description: "总交易次数（validate 时使用）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "optimize";

      const pyResult = await runPythonScript<{
        ok: boolean;
        data?: Record<string, unknown>;
        error?: string;
      }>(
        "engines/aipha_portfolio_engine.py",
        { ...args },
        config,
        { timeoutMs: 120_000 },
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        error: pyResult.error ?? "组合优化引擎执行失败",
      };
    },
  };
}
