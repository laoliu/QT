/**
 * peer_compare tool — AI-callable for same-industry peer comparison.
 *
 * Actions:
 *   compare — Compare a stock with its industry peers on fundamentals and technicals
 *   rank    — Rank peers by a specific metric (PE, PB, ROE, revenue growth, etc.)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createPeerCompareTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "peer_compare",
    description:
      "同业对比分析工具。将目标股票与同行业公司在估值、盈利能力、成长性等维度进行横向对比，判断相对估值高低。\n" +
      "• action=compare — 全面对比（PE/PB/ROE/毛利率/营收增速等）\n" +
      "• action=rank — 按单一指标排名（需指定metric参数）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "rank"],
          description: "操作类型: compare=全面对比, rank=单指标排名",
        },
        symbol: {
          type: "string",
          description: "目标股票代码，如 600519",
        },
        metric: {
          type: "string",
          enum: ["pe", "pb", "roe", "revenue_growth", "net_profit_growth", "gross_margin", "market_cap"],
          description: "排名指标（rank时使用）",
        },
        limit: {
          type: "number",
          description: "返回同行数量，默认10",
        },
      },
      required: ["action", "symbol"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "compare";
      const symbol = args.symbol as string;
      const limit = (args.limit as number) ?? 10;

      if (!symbol) return { error: "请提供股票代码 (symbol)" };

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/peer_compare.py",
        {
          code: symbol,
          market: "cn_stock",
          limit,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "同业对比数据获取失败" };
      }

      const data = pyResult.data ?? {};

      if (action === "rank" && args.metric) {
        const metric = args.metric as string;
        const peers = (data.peers as Array<Record<string, unknown>>) ?? [];
        const target = data.target as Record<string, unknown> | undefined;

        // Sort peers by the specified metric
        const sorted = [...peers].sort((a, b) => {
          const va = typeof a[metric] === "number" ? (a[metric] as number) : -Infinity;
          const vb = typeof b[metric] === "number" ? (b[metric] as number) : -Infinity;
          // For PE/PB lower is better; for ROE/growth higher is better
          if (metric === "pe" || metric === "pb") return va - vb;
          return vb - va;
        });

        // Find target's rank
        let targetRank = -1;
        if (target) {
          const targetVal = typeof target[metric] === "number" ? (target[metric] as number) : null;
          if (targetVal !== null) {
            targetRank = sorted.findIndex(
              (p) => (p.code as string) === symbol || (p.code as string) === (target.code as string),
            );
          }
        }

        return {
          action: "rank",
          symbol,
          metric,
          industry: data.industry,
          targetValue: target ? target[metric] : null,
          targetRank: targetRank >= 0 ? targetRank + 1 : null,
          totalPeers: sorted.length,
          ranking: sorted.slice(0, limit).map((p, i) => ({
            rank: i + 1,
            code: p.code,
            name: p.name,
            [metric]: p[metric],
            isTarget: (p.code as string) === symbol,
          })),
        };
      }

      // Full comparison
      const comparison = data.comparison as Record<string, unknown> | undefined;
      return {
        action: "compare",
        symbol,
        industry: data.industry,
        target: data.target,
        peerCount: ((data.peers as unknown[]) ?? []).length,
        comparison: comparison ?? {},
        peers: ((data.peers as Array<Record<string, unknown>>) ?? []).slice(0, limit),
        interpretation: comparison
          ? generateInterpretation(
              data.target as Record<string, unknown>,
              comparison,
            )
          : "数据不足，无法生成解读",
      };
    },
  };
}

function generateInterpretation(
  target: Record<string, unknown> | undefined,
  comparison: Record<string, unknown>,
): string {
  if (!target) return "目标数据缺失";

  const parts: string[] = [];

  const pePosition = comparison.pe_percentile as number | undefined;
  if (typeof pePosition === "number") {
    if (pePosition < 30) parts.push("PE估值在行业中偏低，具有估值优势");
    else if (pePosition > 70) parts.push("PE估值在行业中偏高，注意估值风险");
    else parts.push("PE估值处于行业中等水平");
  }

  const roePosition = comparison.roe_percentile as number | undefined;
  if (typeof roePosition === "number") {
    if (roePosition > 70) parts.push("ROE在行业中靠前，盈利能力突出");
    else if (roePosition < 30) parts.push("ROE在行业中偏低，盈利能力待提升");
  }

  return parts.length > 0 ? parts.join("；") : "综合指标处于行业中等水平";
}
