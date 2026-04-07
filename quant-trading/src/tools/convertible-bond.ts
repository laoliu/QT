/**
 * convertible_bond tool — AI-callable for A-share convertible bond analysis.
 *
 * Actions:
 *   list          — List all tradable convertible bonds
 *   detail        — Detailed analysis for a specific CB
 *   dual_low      — Dual-low strategy screening (双低策略)
 *   forced_redeem — Forced redemption alert (强赎预警)
 *   arbitrage     — Conversion arbitrage opportunities (折价转股套利)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createConvertibleBondTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "convertible_bond",
    description:
      "可转债分析工具。查看可转债列表、双低策略筛选、强赎预警、折价套利。\n" +
      "• action=list — 可转债市场列表\n" +
      "• action=detail — 单只可转债详情（需指定symbol）\n" +
      "• action=dual_low — 双低策略筛选（低价格+低溢价率）\n" +
      "• action=forced_redeem — 强赎预警（转股价值>130元）\n" +
      "• action=arbitrage — 折价转股套利机会（负溢价率）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "detail", "dual_low", "forced_redeem", "arbitrage"],
          description:
            "操作类型: list=列表, detail=详情, dual_low=双低筛选, forced_redeem=强赎预警, arbitrage=套利",
        },
        symbol: {
          type: "string",
          description: "可转债代码（action=detail时必填），如 113009",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认30",
        },
        max_price: {
          type: "number",
          description: "最高价格（仅action=dual_low），默认130元",
        },
        max_premium: {
          type: "number",
          description: "最高溢价率（仅action=dual_low），默认30%",
        },
        sort_by: {
          type: "string",
          enum: ["price", "premium"],
          description: "排序方式（仅action=list），默认按价格",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 30;
      const maxPrice = (args.max_price as number) ?? 130;
      const maxPremium = (args.max_premium as number) ?? 30;
      const sortBy = (args.sort_by as string) ?? "price";

      if (action === "detail" && !symbol) {
        return { error: "请提供 symbol 参数（可转债代码）" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/convertible_bond.py",
        { action, symbol, limit, max_price: maxPrice, max_premium: maxPremium, sort_by: sortBy },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }

        // Display hints for large results
        const bonds = data.bonds as unknown[] | undefined;
        if (bonds && bonds.length > 15) {
          (data as Record<string, unknown>).displayHint =
            `共${String(bonds.length)}只可转债，请分组展示关键指标。`;
        }

        return data;
      }

      return {
        error: pyResult.error ?? "可转债数据获取失败",
      };
    },
  };
}
