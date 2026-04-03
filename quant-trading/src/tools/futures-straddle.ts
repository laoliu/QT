/**
 * futures_straddle tool — AI-callable for futures spread and arbitrage analysis.
 *
 * Supports:
 *   - Butterfly spread analysis (buy near + buy far, sell 2× mid)
 *   - Premium/discount (basis) arbitrage detection
 *   - Historical basis & spread analysis
 *
 * Delegates to the Python futures_straddle engine (migrated from jtstock).
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createFuturesStraddleTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "futures_straddle",
    description:
      "期货套利分析工具。\n" +
      "action=butterfly  蝶式套利（买近月+买远月，卖2×中间月）分析\n" +
      "action=premium    期现基差套利（升贴水收敛策略）\n" +
      "action=basis      快速基差检查\n" +
      "支持 IC/IF/IH 中国指数期货合约。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["butterfly", "premium", "basis"],
          description: "分析类型: butterfly=蝶式套利, premium=基差套利, basis=快速基差检查",
        },
        product: {
          type: "string",
          enum: ["IC", "IF", "IH"],
          description: "期货品种 (IC=中证500, IF=沪深300, IH=上证50)",
        },
        nearPrice: { type: "number", description: "近月合约价格" },
        midPrice: { type: "number", description: "中间月合约价格 (蝶式)" },
        farPrice: { type: "number", description: "远月合约价格" },
        futuresPrice: { type: "number", description: "期货价格 (基差)" },
        spotPrice: { type: "number", description: "现货指数价格 (基差)" },
        lots: { type: "number", description: "手数，默认1" },
        nearExitPrice: { type: "number", description: "近月平仓价 (计算盈亏)" },
        midExitPrice: { type: "number", description: "中间月平仓价 (计算盈亏)" },
        farExitPrice: { type: "number", description: "远月平仓价 (计算盈亏)" },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const product = (args.product as string) ?? "IC";

      // Build config for Python engine
      const pyArgs: Record<string, unknown> = {
        action,
        product,
        lots: (args.lots as number) ?? 1,
      };

      if (action === "butterfly") {
        pyArgs.nearPrice = args.nearPrice;
        pyArgs.midPrice = args.midPrice;
        pyArgs.farPrice = args.farPrice;
        if (args.nearExitPrice != null) {
          pyArgs.nearExitPrice = args.nearExitPrice;
          pyArgs.midExitPrice = args.midExitPrice;
          pyArgs.farExitPrice = args.farExitPrice;
        }
      } else if (action === "premium" || action === "basis") {
        pyArgs.futuresPrice = args.futuresPrice;
        pyArgs.spotPrice = args.spotPrice;
        pyArgs.action = action === "basis" ? "basis_check" : "premium";
      }

      const result = await runPythonScript<Record<string, unknown>>(
        "engines/futures_straddle.py",
        pyArgs,
        config,
      );

      if (result.ok && result.data) {
        return { action, product, ...result.data };
      }

      return {
        error: `期货分析引擎错误: ${result.error ?? "未知错误"}`,
        hint: "请确保 Python 已安装并配置 pythonPath",
      };
    },
  };
}
