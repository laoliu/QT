/**
 * institutional_flow tool — AI-callable for institutional holdings & insider trades.
 *
 * Actions:
 *   fund_holdings     — Which funds/institutions hold a stock
 *   holding_changes   — Quarter-over-quarter institutional holding delta
 *   block_trades      — Recent block trades (大宗交易)
 *   insider_trades    — Executive/management buy/sell transactions
 *   smart_money_signal — Composite institutional conviction score
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createInstitutionalFlowTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "institutional_flow",
    description:
      "机构持仓与内部交易分析工具。查看基金持仓、机构增减持变动、大宗交易、高管买卖，以及综合机构信念评分。\n" +
      "• action=fund_holdings — 查看哪些机构/基金持有该股票（需指定symbol）\n" +
      "• action=holding_changes — 机构持仓季度环比变化（需指定symbol）\n" +
      "• action=block_trades — 近期大宗交易明细（可不指定symbol查全市场）\n" +
      "• action=insider_trades — 高管/内部人买卖记录（需指定symbol）\n" +
      "• action=smart_money_signal — 综合Smart Money信号评分（需指定symbol）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "fund_holdings",
            "holding_changes",
            "block_trades",
            "insider_trades",
            "smart_money_signal",
          ],
          description:
            "操作类型: fund_holdings=基金持仓, holding_changes=持仓变动, " +
            "block_trades=大宗交易, insider_trades=内部交易, smart_money_signal=机构信念评分",
        },
        symbol: {
          type: "string",
          description: "股票代码（大部分action必填），如 600519",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "fund_holdings";
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/institutional_flow.py",
        {
          action,
          symbol: args.symbol,
          limit,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "机构数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
