/**
 * strategy_validation tool — 策略验证工具。
 *
 * Actions:
 *   quality_gates  — 运行质量门检验（DSR/PBO/HLZ）
 *   walk_forward   — Walk-Forward 时间序列交叉验证
 *   pbo            — Probability of Backtest Overfitting 检验
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStrategyValidationTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "strategy_validation",
    description:
      "策略验证工具（Aipha 验证框架）。\n" +
      "• action=quality_gates — 质量门检验（DSR Deflated Sharpe / PBO / HLZ 多重检验）\n" +
      "• action=walk_forward — Walk-Forward 滚动/扩展窗口验证\n" +
      "• action=pbo — 回测过拟合概率检验",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["quality_gates", "walk_forward", "pbo"],
          description: "操作类型",
        },
        strategyId: {
          type: "string",
          description: "策略 ID（用于从结果库加载回测数据）",
        },
        sharpe: {
          type: "number",
          description: "策略 Sharpe Ratio（action=quality_gates 时使用）",
        },
        nTrials: {
          type: "number",
          description: "尝试的策略变体数量（用于多重检验校正）",
        },
        nSplits: {
          type: "number",
          description: "Walk-Forward 折数（默认 5）",
        },
        trainSize: {
          type: "number",
          description: "训练窗口大小（交易日，默认 500）",
        },
        testSize: {
          type: "number",
          description: "测试窗口大小（交易日，默认 60）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "quality_gates";
      const strategyId = args.strategyId as string | undefined;
      const sharpe = args.sharpe as number | undefined;
      const nTrials = args.nTrials as number | undefined;
      const nSplits = args.nSplits as number | undefined;
      const trainSize = args.trainSize as number | undefined;
      const testSize = args.testSize as number | undefined;

      const pyResult = await runPythonScript<
        Record<string, unknown>
      >(
        "engines/aipha_validation_engine.py",
        {
          action,
          strategy_id: strategyId ?? "",
          sharpe: sharpe ?? 0,
          n_trials: nTrials ?? 1,
          n_splits: nSplits ?? 5,
          train_size: trainSize ?? 500,
          test_size: testSize ?? 60,
        },
        config,
        { timeoutMs: 120_000 },
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "Validation failed" };
      }
      return pyResult.data ?? {};
    },
  };
}
