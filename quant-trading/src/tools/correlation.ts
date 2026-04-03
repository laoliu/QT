import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import {
  computeCorrelation,
  computeCorrelationMatrix,
  computeSpreadZScore,
  findCorrelatedPairs,
} from "../services/correlation.js";
/**
 * AI-callable tool for correlation & pair analysis (相关性分析).
 */
import type { QuantTradingConfig } from "../types.js";

type QuoteFn = (symbol: string) => Promise<{ klines: Array<{ close: number }> } | null>;

export function createCorrelationTool(
  _api: OpenClawPluginApi,
  _config: QuantTradingConfig,
  getKlinesFn: QuoteFn,
) {
  return {
    name: "correlation_analysis",
    description:
      "相关性与配对分析。计算标的之间的相关系数，寻找配对交易候选。\n" +
      "action=pair 计算两个标的的相关系数，\n" +
      "action=matrix 计算多个标的的相关矩阵，\n" +
      "action=find_pairs 从一组标的中寻找高相关配对，\n" +
      "action=spread 计算配对价差的Z-Score（均值回归信号）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["pair", "matrix", "find_pairs", "spread"],
          description: "操作: pair=两两相关, matrix=相关矩阵, find_pairs=寻找配对, spread=价差Z值",
        },
        symbolA: {
          type: "string",
          description: "标的A代码 (action=pair/spread)",
        },
        symbolB: {
          type: "string",
          description: "标的B代码 (action=pair/spread)",
        },
        symbols: {
          type: "string",
          description:
            "标的代码列表，逗号分隔 (action=matrix/find_pairs)，如 '600519,000858,000333'",
        },
        minCorrelation: {
          type: "number",
          description: "最小相关系数阈值 (action=find_pairs, 默认0.7)",
        },
        lookback: {
          type: "number",
          description: "Z-Score回看窗口 (action=spread, 默认全部数据)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "pair": {
          const symbolA = args.symbolA as string;
          const symbolB = args.symbolB as string;
          if (!symbolA || !symbolB)
            return JSON.stringify({ error: "需要 symbolA 和 symbolB 参数" });

          const [dataA, dataB] = await Promise.all([getKlinesFn(symbolA), getKlinesFn(symbolB)]);
          if (!dataA?.klines?.length || !dataB?.klines?.length) {
            return JSON.stringify({ error: "无法获取K线数据" });
          }

          const pricesA = dataA.klines.map((k) => k.close);
          const pricesB = dataB.klines.map((k) => k.close);
          const result = computeCorrelation(pricesA, pricesB, symbolA, symbolB);
          return JSON.stringify({
            ...result,
            correlation: Number(result.correlation.toFixed(4)),
            interpretation:
              Math.abs(result.correlation) >= 0.8
                ? "强相关"
                : Math.abs(result.correlation) >= 0.5
                  ? "中等相关"
                  : "弱相关",
          });
        }

        case "matrix": {
          const symbolsStr = args.symbols as string;
          if (!symbolsStr) return JSON.stringify({ error: "需要 symbols 参数 (逗号分隔)" });
          const symbols = symbolsStr
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
          if (symbols.length < 2) return JSON.stringify({ error: "至少需要2个标的" });

          const priceMap = new Map<string, number[]>();
          for (const sym of symbols) {
            const data = await getKlinesFn(sym);
            if (data?.klines?.length) {
              priceMap.set(
                sym,
                data.klines.map((k) => k.close),
              );
            }
          }

          if (priceMap.size < 2) return JSON.stringify({ error: "获取到的K线数据不足" });

          const { matrix } = computeCorrelationMatrix(priceMap);
          // Format as readable matrix
          const formatted = matrix.map((e) => ({
            ...e,
            value: Number(e.value.toFixed(4)),
          }));
          return JSON.stringify({ symbols: [...priceMap.keys()], correlations: formatted });
        }

        case "find_pairs": {
          const symbolsStr = args.symbols as string;
          if (!symbolsStr) return JSON.stringify({ error: "需要 symbols 参数 (逗号分隔)" });
          const symbols = symbolsStr
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
          if (symbols.length < 2) return JSON.stringify({ error: "至少需要2个标的" });

          const minCorr = (args.minCorrelation as number) ?? 0.7;
          const priceMap = new Map<string, number[]>();
          for (const sym of symbols) {
            const data = await getKlinesFn(sym);
            if (data?.klines?.length) {
              priceMap.set(
                sym,
                data.klines.map((k) => k.close),
              );
            }
          }

          if (priceMap.size < 2) return JSON.stringify({ error: "获取到的K线数据不足" });

          const pairs = findCorrelatedPairs(priceMap, minCorr);
          return JSON.stringify({
            count: pairs.length,
            minCorrelation: minCorr,
            pairs: pairs.map((p) => ({
              ...p,
              correlation: Number(p.correlation.toFixed(4)),
            })),
          });
        }

        case "spread": {
          const symbolA = args.symbolA as string;
          const symbolB = args.symbolB as string;
          if (!symbolA || !symbolB)
            return JSON.stringify({ error: "需要 symbolA 和 symbolB 参数" });

          const [dataA, dataB] = await Promise.all([getKlinesFn(symbolA), getKlinesFn(symbolB)]);
          if (!dataA?.klines?.length || !dataB?.klines?.length) {
            return JSON.stringify({ error: "无法获取K线数据" });
          }

          const pricesA = dataA.klines.map((k) => k.close);
          const pricesB = dataB.klines.map((k) => k.close);
          const spread = computeSpreadZScore(pricesA, pricesB, symbolA, symbolB, {
            lookback: args.lookback as number | undefined,
          });
          return JSON.stringify({
            ...spread,
            currentSpread: Number(spread.currentSpread.toFixed(6)),
            meanSpread: Number(spread.meanSpread.toFixed(6)),
            stdSpread: Number(spread.stdSpread.toFixed(6)),
            zScore: Number(spread.zScore.toFixed(4)),
          });
        }

        default:
          return JSON.stringify({ error: `未知操作: ${action}` });
      }
    },
  };
}
