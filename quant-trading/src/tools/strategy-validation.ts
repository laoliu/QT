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
      "• action=quality_gates — 真实回测指标质量门，提供试验次数/收益矩阵时附加DSR/PBO\n" +
      "• action=walk_forward — 基于真实多策略收益矩阵的训练选优/样本外评价；无矩阵时仅返回切分方案\n" +
      "• action=pbo — 基于真实多策略收益矩阵的抽样CSCV过拟合概率估计。缺失数据不视为检验通过。",
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
          description: "策略标签；不会自动加载历史数据，需提供真实统计量或收益矩阵",
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
        maxDrawdown: { type: "number", minimum: 0, maximum: 1, description: "最大回撤小数，如0.15" },
        totalTrades: { type: "integer", minimum: 0, description: "真实回测成交次数" },
        nObs: { type: "integer", minimum: 2, description: "真实收益观测数量/交易日" },
        returnsMatrix: { type: "array", items: { type: "array", items: { type: "number" } }, description: "按时间升序排列的日收益矩阵，行=观测，列=候选策略；小数收益率" },
        nPartitions: { type: "integer", minimum: 2, maximum: 20, description: "PBO分区数，默认10，须为偶数" },
        totalSize: { type: "integer", minimum: 1, description: "仅生成Walk-Forward方案时的真实数据长度" },
        purgeGap: { type: "integer", minimum: 0, description: "训练与测试隔离观测数，默认5" },
        mode: { type: "string", enum: ["rolling", "expanding"] },
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
          sharpe,
          n_trials: nTrials,
          n_splits: nSplits ?? 5,
          train_size: trainSize ?? 500,
          test_size: testSize ?? 60,
          max_drawdown: args.maxDrawdown,
          total_trades: args.totalTrades,
          n_obs: args.nObs,
          returns_matrix: args.returnsMatrix,
          n_partitions: args.nPartitions,
          total_size: args.totalSize,
          purge_gap: args.purgeGap,
          mode: args.mode,
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
