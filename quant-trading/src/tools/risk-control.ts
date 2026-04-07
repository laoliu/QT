/**
 * risk_control tool — AI-callable for live trading risk management (交易风控).
 *
 * Actions:
 *   check_order — Pre-trade risk check before placing an order
 *   status      — Current risk status (exposure, drawdown, daily P&L)
 *   set_limits  — Update risk limits
 *   reset       — Reset risk controller state
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createRiskControlTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "risk_control",
    description:
      "交易风控工具。下单前风险检查、风控状态查看、风控参数设置。\n" +
      "• action=check_order — 下单前风控检查（需提供order和portfolio）\n" +
      "• action=status — 查看当前风控状态（持仓集中度、回撤、今日盈亏）\n" +
      "• action=set_limits — 修改风控参数（如单票上限、日亏损上限等）\n" +
      "• action=reset — 重置风控状态",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["check_order", "status", "set_limits", "reset", "record_pnl"],
          description: "操作类型",
        },
        order: {
          type: "object",
          description: "订单信息 (check_order时必填): {symbol, direction, quantity, price}",
          properties: {
            symbol: { type: "string" },
            direction: { type: "string", enum: ["buy", "sell"] },
            quantity: { type: "number" },
            price: { type: "number" },
          },
        },
        portfolio: {
          type: "object",
          description: "组合信息: {totalAssets, positions: [{symbol, quantity, currentPrice, marketValue}]}",
        },
        limits: {
          type: "object",
          description: "风控参数 (set_limits时): {max_position_pct, max_daily_loss_pct, max_drawdown_pct, ...}",
        },
        pnl: {
          type: "number",
          description: "实现盈亏 (record_pnl时)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "status";

      const pyResult = await runPythonScript<{
        allowed?: boolean;
        reason?: string;
        warnings?: string[];
        ok?: boolean;
        limits?: Record<string, unknown>;
        error?: string;
      }>("engines/risk_control.py", args, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }
        return data;
      }

      return {
        action,
        note: "风控引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
