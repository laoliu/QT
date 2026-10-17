/**
 * ml_ranking tool — AI-callable for Aipha ML stock ranking.
 *
 * Actions:
 *   predict     — Load model → predict → return top-N
 *   train       — Walk-forward training
 *   evaluate    — IC/IR evaluation
 *   list_models — Available model types
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMLRankingTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "ml_ranking",
    description:
      "ML 选股排序工具（Aipha 机器学习框架）。支持 LightGBM/GRU/Ensemble 多模型打分选股。\n" +
      "• action=predict — 使用已训练模型预测股票排名（返回 top-N）\n" +
      "• action=train — Walk-Forward 训练 LightGBM 排序模型\n" +
      "• action=evaluate — 评估预测质量（IC/IR）\n" +
      "• action=list_models — 列出可用模型类型和集成方法",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["predict", "train", "evaluate", "list_models"],
          description: "操作类型",
        },
        modelType: {
          type: "string",
          enum: ["lgbm", "gru", "ensemble"],
          description: "模型类型（action=train 时使用）",
        },
        modelPath: {
          type: "string",
          description: "模型文件路径（action=predict 时使用）",
        },
        topN: {
          type: "number",
          description: "返回前N只股票（默认30）",
        },
        modelParams: {
          type: "object",
          description: "模型超参数（action=train 时使用）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list_models";

      const pyResult = await runPythonScript<{
        ok: boolean;
        data?: Record<string, unknown>;
        error?: string;
      }>(
        "engines/aipha_ml_engine.py",
        {
          action,
          modelType: args.modelType,
          modelPath: args.modelPath,
          topN: (args.topN as number) ?? 30,
          modelParams: args.modelParams,
        },
        config,
        { timeoutMs: 300_000 },
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        error: pyResult.error ?? "ML 排序引擎执行失败",
      };
    },
  };
}
