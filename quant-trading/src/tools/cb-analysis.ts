/**
 * cb_analysis tool — 可转债分析工具。
 *
 * Actions:
 *   list_bonds  — 获取上市可转债列表
 *   metrics     — 计算转股指标（溢价率/双低/Delta）
 *   factors     — 计算 CB 8 因子
 *   select      — 策略选债（10+ 策略可选）
 *   delta_zone  — Delta 分区分析
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createCBAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "cb_analysis",
    description:
      "可转债分析工具（Aipha 可转债套件）。\n" +
      "• action=list_bonds — 获取上市可转债列表\n" +
      "• action=metrics — 计算转股指标（溢价率/双低/Delta/纯债价值/YTM）\n" +
      "• action=factors — 计算 CB 8 因子（iv_bias/gamma/credit_spread/liquidity 等）\n" +
      "• action=select — 策略选债（支持 cb_double_low/cb_multi_factor/cb_gamma_convexity 等 10+ 策略）\n" +
      "• action=delta_zone — Delta 分区分析（债性/平衡/股性）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list_bonds", "metrics", "factors", "select", "delta_zone"],
          description: "操作类型",
        },
        strategy: {
          type: "string",
          description:
            "策略名称（action=select 时使用）。可选: cb_double_low, cb_multi_factor, cb_gamma_convexity, " +
            "cb_credit_spread, cb_momentum, cb_mean_reversion, cb_small_size, cb_delta_balanced, " +
            "cb_value_momentum, cb_rotation",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "可转债代码列表（action=metrics/factors/delta_zone 时使用）",
        },
        topK: {
          type: "number",
          description: "选债数量（action=select 时使用），默认 20",
        },
        maxPrice: {
          type: "number",
          description: "最大价格过滤（默认 130）",
        },
        source: {
          type: "string",
          enum: ["tushare", "akshare"],
          description: "数据源（默认 tushare）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list_bonds";
      const strategy = args.strategy as string | undefined;
      const symbols = args.symbols as string[] | undefined;
      const topK = args.topK as number | undefined;
      const maxPrice = args.maxPrice as number | undefined;
      const source = args.source as string | undefined;

      const pyResult = await runPythonScript<
        Record<string, unknown>
      >(
        "engines/aipha_cb_engine.py",
        {
          action,
          strategy: strategy ?? "cb_double_low",
          symbols: symbols ?? [],
          top_k: topK ?? 20,
          max_price: maxPrice ?? 130,
          source: source ?? "tushare",
        },
        config,
        { timeoutMs: 60_000 },
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "CB analysis failed" };
      }
      return pyResult.data ?? {};
    },
  };
}
