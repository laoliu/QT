/**
 * ml_ranking tool — AI-callable for Aipha ML stock ranking.
 *
 * Actions:
 *   predict     — Load a version-checked model and rank a Parquet feature panel
 *   train       — Walk-forward training from a frozen local Parquet panel
 *   evaluate    — IC/ICIR and cost-estimated evaluation
 *   export      — Export ranked target positions for paper review
 *   execute_paper — Submit a confirmed paper execution plan through the
 *                   canonical PaperGateway boundary
 *   list_models — Available model types
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMLRankingTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "ml_ranking",
    description:
      "A 股低频 ML 选股排序工具（统一研究入口）。当前只支持可审计的 LightGBM 排序模型和线性基线。\n" +
      "• action=predict — 使用已训练模型预测股票排名（返回 top-N）\n" +
      "• action=train — 从本地冻结 Parquet 特征快照执行 Walk-Forward\n" +
      "• action=train_tushare — 通过 TuShare PIT 适配器生成快照后训练\n" +
      "• action=predict — 校验特征版本后加载模型并排序\n" +
      "• action=evaluate — 评估 OOS RankIC/ICIR 与成本估计\n" +
      "• action=export — 导出目标持仓 JSON（仅供 Paper 审阅）\n" +
      "• action=execute_paper — 在显式 confirm=true 后提交 Paper 计划\n" +
      "• action=list_models — 列出规范模型类型",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["predict", "train", "train_tushare", "evaluate", "export", "execute_paper", "list_models"],
          description: "操作类型",
        },
        modelType: {
          type: "string",
          enum: ["lgbm", "linear"],
          description: "模型类型；默认 lgbm，linear 仅作基线",
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
        dataPath: {
          type: "string",
          description: "本地 Parquet 特征/行情快照路径（train/evaluate/export）",
        },
        featurePath: {
          type: "string",
          description: "本地 Parquet 特征快照路径（predict）",
        },
        predictionsPath: {
          type: "string",
          description: "本地 OOS 预测 Parquet 路径（evaluate/export）",
        },
        configPath: {
          type: "string",
          description: "A 股研究 YAML 配置路径；默认 aipha/configs/research/a_stock_ml.yaml",
        },
        outputDir: {
          type: "string",
          description: "不可变研究 run 产物根目录（train）",
        },
        outputPath: {
          type: "string",
          description: "目标持仓 JSON 输出路径（export）",
        },
        dataSnapshotId: {
          type: "string",
          description: "外部数据快照标识，用于复现实验",
        },
        startDate: { type: "string", description: "TuShare PIT 快照起始日 YYYYMMDD（train_tushare）" },
        endDate: { type: "string", description: "TuShare PIT 快照结束日 YYYYMMDD（train_tushare）" },
        indexCode: { type: "string", description: "历史指数成分代码（默认 000905.SH）" },
        benchmarkCode: { type: "string", description: "基准指数代码（默认与 indexCode 相同）" },
        cacheDir: { type: "string", description: "TuShare 本地缓存目录（train_tushare）" },
        useCache: { type: "boolean", description: "是否使用 TuShare 缓存（train_tushare）" },
        featureColumns: {
          type: "array",
          items: { type: "string" },
          description: "期望的模型特征列顺序；不匹配时拒绝预测",
        },
        runId: { type: "string", description: "来源研究 run ID（export）" },
        modelId: { type: "string", description: "来源模型 ID（export）" },
        planPath: { type: "string", description: "Paper 执行计划路径（execute_paper）" },
        prices: {
          type: "object",
          description: "Paper 执行价格映射（execute_paper）",
          additionalProperties: { type: "number" },
        },
        accountValue: { type: "number", description: "Paper 执行账户资产（execute_paper）" },
        confirm: { type: "boolean", description: "显式确认 Paper 执行" },
        maxPositionWeight: { type: "number", description: "单票权重风控上限" },
        maxTotalWeight: { type: "number", description: "总目标权重风控上限" },
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
        "aipha/engines/aipha_ml_engine.py",
        {
          action,
          modelType: args.modelType,
          modelPath: args.modelPath,
          dataPath: args.dataPath,
          featurePath: args.featurePath,
          predictionsPath: args.predictionsPath,
          configPath: args.configPath,
          outputDir: args.outputDir,
          outputPath: args.outputPath,
          dataSnapshotId: args.dataSnapshotId,
          startDate: args.startDate,
          endDate: args.endDate,
          indexCode: args.indexCode,
          benchmarkCode: args.benchmarkCode,
          cacheDir: args.cacheDir,
          useCache: args.useCache,
          featureColumns: args.featureColumns,
          runId: args.runId,
          modelId: args.modelId,
          planPath: args.planPath,
          prices: args.prices,
          accountValue: args.accountValue,
          confirm: args.confirm,
          maxPositionWeight: args.maxPositionWeight,
          maxTotalWeight: args.maxTotalWeight,
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
