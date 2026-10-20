/**
 * position_sizing tool — AI-callable tool for configuring and computing
 * intelligent position sizing for automated trading.
 *
 * Actions:
 *   compute  — Calculate optimal quantity for a given stock/price/account
 *   config   — View or update the position sizing configuration
 *
 * Methods:
 *   fixed_amount — Buy a fixed CNY amount (e.g. ¥10000 per trade)
 *   fixed_pct    — Buy a fixed % of total equity (e.g. 10%)
 *   risk_pct     — Risk N% of equity per trade (qty = riskAmt / stopDist)
 *   kelly        — Kelly criterion (fraction of bankroll based on win rate)
 *   atr_vol      — Volatility-adjusted (qty = riskAmt / ATR×multiplier)
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { computePositionSize, type SizingParams } from "../services/position-sizer.js";
import type { QuantTradingConfig, PositionSizingConfig } from "../types.js";

export function createPositionSizingTool(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
  getEquity: (strict?: boolean) => Promise<number>,
) {
  return {
    name: "position_sizing",
    description:
      "仓位管理工具。计算最优下单数量或查看/更新仓位管理配置。\n" +
      "支持方法: fixed_amount(固定金额), fixed_pct(固定比例), risk_pct(风险比例), kelly(凯利公式), atr_vol(ATR波动率)\n\n" +
      "action=compute 计算指定股票/价格的最优下单数量\n" +
      "action=config  查看当前仓位管理配置\n\n" +
      "示例: 用2%风险比例, 5%止损, 计算600519在¥1800时应该买多少股",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compute", "config"],
          description: "compute=计算下单数量, config=查看配置",
        },
        // ── compute params ──
        symbol: { type: "string", description: "股票代码 (compute时使用)" },
        price: { type: "number", description: "当前/目标价格 (compute时使用)" },
        method: {
          type: "string",
          enum: ["fixed_amount", "fixed_pct", "risk_pct", "kelly", "atr_vol"],
          description: "仓位计算方法 (不填则用全局配置)",
        },
        amount: { type: "number", description: "固定金额 (fixed_amount方法)" },
        pct: { type: "number", description: "权益比例 0-1 (fixed_pct方法)" },
        risk_pct: { type: "number", description: "风险比例 0-1 (risk_pct/kelly/atr_vol)" },
        stop_pct: { type: "number", description: "止损比例 0-1 (risk_pct方法)" },
        stop_distance: { type: "number", description: "止损距离(Ԫ) (risk_pct方法)" },
        win_rate: { type: "number", description: "历史胜率 0-1 (kelly方法)" },
        win_loss_ratio: { type: "number", description: "盈亏比 (kelly方法)" },
        kelly_divisor: { type: "number", description: "Kelly除数, 2=半Kelly (kelly方法)" },
        atr_value: { type: "number", description: "ATRֵ (atr_vol方法)" },
        atr_multiplier: { type: "number", description: "ATR倍数 (atr_vol方法)" },
        max_position_pct: { type: "number", description: "最大持仓比例 0-1" },
        max_quantity: { type: "number", description: "最大股数(绝对上限)" },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>): Promise<string> {
      const action = args.action as string;

      if (action === "config") {
        const sizing = config.tradingAccount?.positionSizing;
        if (!sizing) {
          return (
            "当前未配置仓位管理，使用默认值: 每笔固定100股。\n\n" +
            "可在 quant-trading.json 中配置 tradingAccount.positionSizing:\n" +
            '```json\n{\n  "tradingAccount": {\n    "positionSizing": {\n' +
            '      "method": "risk_pct",\n' +
            '      "riskPct": 0.02,\n' +
            '      "stopPct": 0.05,\n' +
            '      "maxPositionPct": 0.3\n' +
            "    }\n  }\n}\n```\n\n" +
            "支持方法: fixed_amount, fixed_pct, risk_pct, kelly, atr_vol"
          );
        }
        const lines = [`当前仓位管理配置:`, `  方法: ${sizing.method}`];
        if (sizing.amount !== undefined) lines.push(`  固定金额: ¥${sizing.amount}`);
        if (sizing.pct !== undefined) lines.push(`  权益比例: ${(sizing.pct * 100).toFixed(1)}%`);
        if (sizing.riskPct !== undefined)
          lines.push(`  风险比例: ${(sizing.riskPct * 100).toFixed(1)}%`);
        if (sizing.stopPct !== undefined)
          lines.push(`  止损比例: ${(sizing.stopPct * 100).toFixed(1)}%`);
        if (sizing.winRate !== undefined)
          lines.push(`  胜率: ${(sizing.winRate * 100).toFixed(0)}%`);
        if (sizing.winLossRatio !== undefined) lines.push(`  盈亏比: ${sizing.winLossRatio}`);
        if (sizing.kellyDivisor !== undefined) lines.push(`  Kelly除数: ${sizing.kellyDivisor}`);
        if (sizing.atrMultiplier !== undefined) lines.push(`  ATR倍数: ${sizing.atrMultiplier}`);
        if (sizing.maxPositionPct !== undefined)
          lines.push(`  最大持仓比例: ${(sizing.maxPositionPct * 100).toFixed(1)}%`);
        if (sizing.maxQuantity !== undefined) lines.push(`  最大股数: ${sizing.maxQuantity}`);
        return lines.join("\n");
      }

      if (action === "compute") {
        const price = args.price as number | undefined;
        if (!price || price <= 0) return "请提供有效价格 (price > 0)";

        let equity: number;
        try {
          equity = await getEquity(args.require_equity === true);
        } catch {
          if (args.require_equity === true) return "账户权益不可用，无法给出真实仓位数量；请先确认账户资产。";
          equity = 100_000; // fallback
        }
        if (args.require_equity === true && (!Number.isFinite(equity) || equity <= 0)) return "账户权益无效，无法计算仓位。";

        const sizingCfg = config.tradingAccount?.positionSizing;
        const method = (args.method as string) ?? sizingCfg?.method ?? "fixed_amount";

        const params: SizingParams = {
          method: method as SizingParams["method"],
          equity,
          price,
          amount: (args.amount as number) ?? sizingCfg?.amount,
          pct: (args.pct as number) ?? sizingCfg?.pct,
          riskPct: (args.risk_pct as number) ?? sizingCfg?.riskPct,
          stopPct: (args.stop_pct as number) ?? sizingCfg?.stopPct,
          stopDistance: args.stop_distance as number | undefined,
          winRate: (args.win_rate as number) ?? sizingCfg?.winRate,
          winLossRatio: (args.win_loss_ratio as number) ?? sizingCfg?.winLossRatio,
          kellyDivisor: (args.kelly_divisor as number) ?? sizingCfg?.kellyDivisor,
          atrValue: args.atr_value as number | undefined,
          atrMultiplier: (args.atr_multiplier as number) ?? sizingCfg?.atrMultiplier,
          maxPositionPct: (args.max_position_pct as number) ?? sizingCfg?.maxPositionPct,
          maxQuantity: (args.max_quantity as number) ?? sizingCfg?.maxQuantity,
        };

        const result = computePositionSize(params);
        const symbol = (args.symbol as string) ?? "目标股票";

        const lines = [
          `📊 ${symbol} 仓位计算结果:`,
          `  方法: ${result.method}`,
          `  当前权益: ¥${equity.toLocaleString()}`,
          `  股价: ¥${price.toFixed(2)}`,
          `  建议数量: ${result.quantity} 股`,
          `  仓位价值: ¥${result.positionValue.toLocaleString()}`,
          `  占总权益: ${((result.positionValue / equity) * 100).toFixed(1)}%`,
        ];
        if (result.riskAmount !== undefined) {
          lines.push(`  单笔风险: ¥${result.riskAmount.toFixed(0)}`);
        }
        if (result.kellyFraction !== undefined) {
          lines.push(`  Kelly比例: ${(result.kellyFraction * 100).toFixed(1)}%`);
        }
        lines.push(`  说明: ${result.explanation}`);
        return lines.join("\n");
      }

      return `未知操作: ${action}。支持: compute, config`;
    },
  };
}
