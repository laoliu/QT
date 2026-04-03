/**
 * chip_accumulation tool — AI-callable for detecting institutional accumulation (主力吸筹).
 *
 * Actions:
 *   detect — Analyze a single stock for accumulation signals
 *   scan   — Scan multiple stocks (or top active) for accumulation
 *   detail — Detailed analysis with chart-ready time series
 *   ambush_scan — Scan pre-surge ambush-style accumulation candidates
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";
import { toNumberOrDefault, toOptionalNumber } from "../utils/number-utils.js";

export function createChipAccumulationTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "chip_accumulation",
    description:
      "主力吸筹检测工具。通过资金流向、筹码分布、量价结构、换手率和大单净量五个维度综合判断个股是否有主力吸筹迹象。\n" +
      "• action=detect — 分析单只股票的吸筹信号（需指定symbol）\n" +
      "• action=scan — 批量扫描多只股票（可指定symbols，留空则扫描当日活跃股）\n" +
      "• action=detail — 详细分析含时间序列数据（需指定symbol）\n" +
      "• action=ambush_scan — 潜伏吸筹扫描（识别启动前异动候选）\n" +
      "ambush_scan 结果重点字段: signal_type(潜伏/底部/箱体/观察), reasons(入选理由), risk_flags(风险提示), score_components(分项得分)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["detect", "scan", "detail", "ambush_scan"],
          description:
            "操作类型: detect=单股吸筹检测, scan=批量吸筹扫描, detail=详细分析含图表数据, ambush_scan=潜伏吸筹扫描",
        },
        symbol: {
          type: "string",
          description: "股票代码（detect/detail时必填），如 600519",
        },
        symbols: {
          type: "string",
          description:
            "批量扫描的股票代码列表（逗号分隔），如 600519,000858,601318。留空则自动选取活跃股",
        },
        lookback: {
          type: "number",
          description: "分析回看天数，默认30",
        },
        min_confidence: {
          type: "number",
          description: "scan时的最低分数阈值（0-100），默认40",
        },
        as_of_date: {
          type: "string",
          description: "历史回放日期（可选），格式 YYYY-MM-DD；为空时默认最新交易日",
        },
        ambush_volume_ratio: {
          type: "number",
          description: "潜伏扫描参数: 放量倍率阈值（默认1.3）",
        },
        ambush_pre_days: {
          type: "number",
          description: "潜伏扫描参数: 前期统计天数（默认15）",
        },
        ambush_pre_hits: {
          type: "number",
          description: "潜伏扫描参数: 前期放量命中次数（默认1）",
        },
        ambush_change_max: {
          type: "number",
          description: "潜伏扫描参数: 当日涨幅上限%（默认5）",
        },
        ambush_change_min: {
          type: "number",
          description: "潜伏扫描参数: 当日涨幅下限%（默认-4）",
        },
        ambush_turnover_min: {
          type: "number",
          description: "潜伏扫描参数: 换手率下限%（默认0.2）",
        },
        ambush_turnover_max: {
          type: "number",
          description: "潜伏扫描参数: 换手率上限%（默认20）",
        },
        ambush_ma_period: {
          type: "number",
          description: "潜伏扫描参数: 均线周期（默认20）",
        },
        ambush_ma_deviation_max: {
          type: "number",
          description: "潜伏扫描参数: 价格偏离均线上限%（默认12）",
        },
        ambush_shrink_ratio: {
          type: "number",
          description: "潜伏扫描参数: 缩量比例（默认0.95）",
        },
        ambush_stop_volatility: {
          type: "number",
          description: "潜伏扫描参数: 止跌波动阈值%（默认1.5）",
        },
        ambush_near_low_max: {
          type: "number",
          description: "潜伏扫描参数: 距近期低点上限%（默认8）",
        },
        ambush_box_days: {
          type: "number",
          description: "潜伏扫描参数: 箱体统计天数（默认10）",
        },
        ambush_box_amplitude: {
          type: "number",
          description: "潜伏扫描参数: 箱体振幅上限%（默认22）",
        },
        ambush_ma_convergence: {
          type: "number",
          description: "潜伏扫描参数: 均线收敛阈值%（默认5）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "detect";
      const symbol = args.symbol as string | undefined;
      const symbols = args.symbols as string | undefined;
      const lookback = toNumberOrDefault(args.lookback, 30);
      const minConfidence = toNumberOrDefault(args.min_confidence, 40);
      const asOfDate =
        typeof args.as_of_date === "string"
          ? args.as_of_date
          : typeof args.asOfDate === "string"
            ? args.asOfDate
            : undefined;
      const ambushParams = {
        ambush_volume_ratio: toOptionalNumber(args.ambush_volume_ratio),
        ambush_pre_days: toOptionalNumber(args.ambush_pre_days),
        ambush_pre_hits: toOptionalNumber(args.ambush_pre_hits),
        ambush_change_max: toOptionalNumber(args.ambush_change_max),
        ambush_change_min: toOptionalNumber(args.ambush_change_min),
        ambush_turnover_min: toOptionalNumber(args.ambush_turnover_min),
        ambush_turnover_max: toOptionalNumber(args.ambush_turnover_max),
        ambush_ma_period: toOptionalNumber(args.ambush_ma_period),
        ambush_ma_deviation_max: toOptionalNumber(args.ambush_ma_deviation_max),
        ambush_shrink_ratio: toOptionalNumber(args.ambush_shrink_ratio),
        ambush_stop_volatility: toOptionalNumber(args.ambush_stop_volatility),
        ambush_near_low_max: toOptionalNumber(args.ambush_near_low_max),
        ambush_box_days: toOptionalNumber(args.ambush_box_days),
        ambush_box_amplitude: toOptionalNumber(args.ambush_box_amplitude),
        ambush_ma_convergence: toOptionalNumber(args.ambush_ma_convergence),
      };

      const pyResult = await runPythonScript<{
        action?: string;
        symbol?: string;
        name?: string;
        total_score?: number;
        level?: string;
        conclusion?: string;
        advice?: string;
        dimension_scores?: Record<string, number>;
        signals?: Array<Record<string, unknown>>;
        stocks?: Array<Record<string, unknown>>;
        signal_type?: string;
        reasons?: string[];
        risk_flags?: string[];
        score_components?: Record<string, number>;
        count?: number;
        error?: string;
        errors?: string[];
      }>(
        "engines/chip_accumulation.py",
        {
          action,
          symbol,
          symbols,
          lookback,
          min_confidence: minConfidence,
          as_of_date: asOfDate,
          ...ambushParams,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        // Display hints
        if ((action === "scan" || action === "ambush_scan") && data.count && data.count > 10) {
          (data as Record<string, unknown>).displayHint =
            `共检出${String(data.count)}只吸筹候选，按得分从高到低展示，重点关注得分≥70的「明显吸筹」股票。`;
        }

        return data;
      }

      return {
        action,
        note: "主力吸筹检测引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
