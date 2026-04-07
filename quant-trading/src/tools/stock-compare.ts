/**
 * stock_compare tool — AI-callable for horizontal comparison of multiple stocks.
 *
 * Supports comparing 2-5 stocks across:
 *   - Fundamentals: PE / PB / ROE / market cap / revenue growth
 *   - Technicals: N-day return / MA position / RSI
 *   - Capital flow: main net inflow / northbound holdings
 *   - Outputs comparison table + composite score ranking
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";

// ── Types ──────────────────────────────────────────────────────────
interface FundamentalMetrics {
  symbol: string;
  name?: string;
  pe?: number | null;
  pb?: number | null;
  roe?: number | null;
  marketCap?: number | null;
  revenueGrowth?: number | null;
}

interface TechnicalMetrics {
  symbol: string;
  returnPct?: number | null;
  maPosition?: string | null; // "above" | "below" | "crossing"
  rsi14?: number | null;
  volatility?: number | null;
}

interface CapitalFlowMetrics {
  symbol: string;
  mainNetInflow?: number | null;
  mainNetInflowPct?: number | null;
  northboundHolding?: number | null;
}

interface StockCompareRow {
  rank: number;
  symbol: string;
  name?: string;
  // Fundamentals
  pe?: number | null;
  pb?: number | null;
  roe?: number | null;
  marketCap?: number | null;
  revenueGrowth?: number | null;
  // Technicals
  returnPct?: number | null;
  maPosition?: string | null;
  rsi14?: number | null;
  volatility?: number | null;
  // Capital flow
  mainNetInflow?: number | null;
  mainNetInflowPct?: number | null;
  northboundHolding?: number | null;
  // Scoring
  fundamentalScore: number;
  technicalScore: number;
  capitalFlowScore: number;
  compositeScore: number;
}

// ── Score helpers ──────────────────────────────────────────────────

/** Rank values from best to worst, return normalized score 0-100. Lower rank = higher score. */
function rankScore(values: (number | null | undefined)[], ascending: boolean): number[] {
  const indexed = values.map((v, i) => ({ v, i }));
  const valid = indexed.filter((x) => x.v != null) as { v: number; i: number }[];
  valid.sort((a, b) => (ascending ? a.v - b.v : b.v - a.v));

  const scores = new Array<number>(values.length).fill(50); // default for null
  const n = valid.length;
  for (let rank = 0; rank < n; rank++) {
    scores[valid[rank].i] = n > 1 ? Math.round(100 * (1 - rank / (n - 1))) : 100;
  }
  return scores;
}

function average(nums: number[]): number {
  if (nums.length === 0) return 0;
  return Math.round((nums.reduce((a, b) => a + b, 0) / nums.length) * 100) / 100;
}

// ── Main tool factory ─────────────────────────────────────────────

export function createStockCompareTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const provider = createDataProvider(config);

  return {
    name: "stock_compare",
    description:
      "个股横向对比工具。支持2-5只股票的多维度并排对比：\n" +
      "• 基本面对比：PE/PB/ROE/总市值/营收增速\n" +
      "• 技术面对比：近N日涨跌幅/均线位置/RSI/波动率\n" +
      "• 资金面对比：主力净流入/主力净流入占比/北向持仓\n" +
      "• 输出对比表格 + 综合评分排名\n" +
      "适用场景：在候选股票间做横向对比以辅助买卖决策。",
    parameters: {
      type: "object" as const,
      properties: {
        symbols: {
          type: "string",
          description:
            '股票代码列表(逗号分隔或JSON数组)，2-5只。例如: "600519,000858,000568" 或 ["600519","000858"]',
        },
        period: {
          type: "number",
          description: "技术面对比的回看天数，默认20（近20个交易日涨跌幅）",
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock", "hk_stock"],
          description: "市场类型，默认根据代码自动检测",
        },
        weights: {
          type: "string",
          description:
            '评分权重(JSON对象)，默认 {"fundamental":0.4,"technical":0.3,"capitalFlow":0.3}。' +
            "三项之和应为1。",
        },
      },
      required: ["symbols"],
    },
    async execute(args: Record<string, unknown>) {
      // ── Parse symbols ──
      let symbols: string[] = [];
      const rawSymbols = args.symbols as string;
      if (!rawSymbols) return { error: "请提供股票代码列表 (symbols)" };

      try {
        const parsed = JSON.parse(rawSymbols);
        if (Array.isArray(parsed)) symbols = parsed.map(String);
        else symbols = rawSymbols.split(",").map((s) => s.trim());
      } catch {
        symbols = rawSymbols.split(",").map((s) => s.trim());
      }
      symbols = symbols.filter(Boolean);

      if (symbols.length < 2) return { error: "至少需要2只股票进行对比" };
      if (symbols.length > 5) return { error: "最多支持5只股票对比" };

      const period = (args.period as number) ?? 20;

      // ── Parse weights ──
      let weights = { fundamental: 0.4, technical: 0.3, capitalFlow: 0.3 };
      if (args.weights) {
        try {
          const parsed = JSON.parse(args.weights as string);
          if (parsed.fundamental != null) weights.fundamental = parsed.fundamental;
          if (parsed.technical != null) weights.technical = parsed.technical;
          if (parsed.capitalFlow != null) weights.capitalFlow = parsed.capitalFlow;
        } catch {
          // use defaults
        }
      }

      // ── Fetch data in parallel ──
      const fundamentalsArr: FundamentalMetrics[] = [];
      const techArr: TechnicalMetrics[] = [];
      const flowArr: CapitalFlowMetrics[] = [];
      const errors: string[] = [];

      await Promise.all(
        symbols.map(async (symbol) => {
          const market =
            (args.market as string) ?? detectMarketFromSymbol(symbol) ?? config.defaultMarket;

          // 1. Fundamentals
          const fundamental: FundamentalMetrics = { symbol };
          try {
            if (market === "cn_stock") {
              const plainCode = symbol.replace(/\.(SH|SS|SZ)$/i, "");
              const result = await runPythonScript(
                "data_providers.financial_provider",
                { action: "fetch", codes: [plainCode], market: "cn_stock" },
                config,
              );
              if (result.ok && result.data) {
                const data = result.data as { results?: Record<string, Record<string, unknown>> };
                const snap = data?.results?.[plainCode];
                if (snap) {
                  fundamental.name = snap.name as string | undefined;
                  fundamental.pe = snap.pe as number | undefined ?? snap.PE as number | undefined ?? null;
                  fundamental.pb = snap.pb as number | undefined ?? snap.PB as number | undefined ?? null;
                  fundamental.roe = snap.roe as number | undefined ?? snap.ROE as number | undefined ?? null;
                  fundamental.marketCap =
                    (snap.total_market_val as number | undefined) ??
                    (snap.marketCap as number | undefined) ??
                    null;
                  fundamental.revenueGrowth =
                    (snap.revenue_growth as number | undefined) ??
                    (snap.revenueGrowth as number | undefined) ??
                    null;
                }
              }
            } else {
              const result = await runPythonScript(
                "data_providers.yfinance_provider",
                { action: "fundamentals", symbol, market },
                config,
              );
              if (result.ok && result.data) {
                const snap = result.data as Record<string, unknown>;
                fundamental.name = snap.name as string | undefined;
                fundamental.pe = snap.pe as number | undefined ?? snap.PE as number | undefined ?? null;
                fundamental.pb = snap.pb as number | undefined ?? snap.PB as number | undefined ?? null;
                fundamental.roe = snap.roe as number | undefined ?? snap.ROE as number | undefined ?? null;
                fundamental.marketCap = snap.marketCap as number | undefined ?? null;
                fundamental.revenueGrowth = snap.revenueGrowth as number | undefined ?? null;
              }
            }
          } catch (e) {
            errors.push(`${symbol}: 基本面数据获取失败 - ${String(e)}`);
          }
          fundamentalsArr.push(fundamental);

          // 2. Technicals (via klines)
          const tech: TechnicalMetrics = { symbol };
          try {
            const endDate = new Date().toISOString().slice(0, 10);
            const startDate = new Date(Date.now() - (period + 60) * 24 * 60 * 60 * 1000)
              .toISOString()
              .slice(0, 10);
            const bars = await provider.getKlines(
              symbol,
              "1d" as never,
              startDate,
              endDate,
              market as never,
            );

            if (bars.length >= 2) {
              const recent = bars.slice(-period);
              const firstClose = recent[0]?.close ?? bars[bars.length - period - 1]?.close;
              const lastClose = recent[recent.length - 1]?.close ?? bars[bars.length - 1]?.close;
              if (firstClose && lastClose && firstClose > 0) {
                tech.returnPct = Math.round(((lastClose - firstClose) / firstClose) * 10000) / 100;
              }

              // MA20 position
              if (bars.length >= 20) {
                const closes = bars.map((b) => b.close);
                const ma20 =
                  closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
                const currentClose = closes[closes.length - 1];
                if (currentClose > ma20 * 1.02) tech.maPosition = "above";
                else if (currentClose < ma20 * 0.98) tech.maPosition = "below";
                else tech.maPosition = "crossing";
              }

              // RSI14
              if (bars.length >= 15) {
                const closes = bars.map((b) => b.close);
                const recentCloses = closes.slice(-(14 + 1));
                let gains = 0;
                let losses = 0;
                for (let i = 1; i < recentCloses.length; i++) {
                  const d = recentCloses[i] - recentCloses[i - 1];
                  if (d > 0) gains += d;
                  else losses -= d;
                }
                const avgGain = gains / 14;
                const avgLoss = losses / 14;
                tech.rsi14 =
                  avgLoss === 0 ? 100 : Math.round((100 - 100 / (1 + avgGain / avgLoss)) * 100) / 100;
              }

              // Volatility
              if (bars.length >= 20) {
                const closes = bars.map((b) => b.close);
                const logReturns: number[] = [];
                for (let i = 1; i < closes.length; i++) {
                  if (closes[i] > 0 && closes[i - 1] > 0) {
                    logReturns.push(Math.log(closes[i] / closes[i - 1]));
                  }
                }
                if (logReturns.length >= 2) {
                  const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
                  const variance =
                    logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / logReturns.length;
                  tech.volatility =
                    Math.round(Math.sqrt(variance) * Math.sqrt(252) * 10000) / 100;
                }
              }
            }
          } catch (e) {
            errors.push(`${symbol}: 技术面数据获取失败 - ${String(e)}`);
          }
          techArr.push(tech);

          // 3. Capital flow
          const flow: CapitalFlowMetrics = { symbol };
          try {
            const plainCode = symbol.replace(/\.(SH|SS|SZ)$/i, "");
            const pyResult = await runPythonScript<{
              flows?: Array<Record<string, unknown>>;
              error?: string;
            }>("engines/fund_flow.py", { action: "stock", symbol: plainCode, limit: 5 }, config);

            if (pyResult.ok && pyResult.data && pyResult.data.flows && pyResult.data.flows.length > 0) {
              const latest = pyResult.data.flows[0];
              flow.mainNetInflow =
                (latest.main_net_inflow as number | undefined) ??
                (latest.mainNetInflow as number | undefined) ??
                null;
              flow.mainNetInflowPct =
                (latest.main_net_inflow_pct as number | undefined) ??
                (latest.mainNetInflowPct as number | undefined) ??
                null;
            }

            // Northbound holding (best-effort)
            const nbResult = await runPythonScript<{
              flows?: Array<Record<string, unknown>>;
              error?: string;
            }>("engines/fund_flow.py", { action: "northbound", limit: 1 }, config);
            if (nbResult.ok && nbResult.data && nbResult.data.flows) {
              // northbound data is aggregated, try to extract symbol-specific
              const matched = nbResult.data.flows.find(
                (f) =>
                  (f.code as string) === plainCode || (f.symbol as string) === plainCode,
              );
              if (matched) {
                flow.northboundHolding =
                  (matched.holding as number | undefined) ??
                  (matched.northbound_holding as number | undefined) ??
                  null;
              }
            }
          } catch (e) {
            errors.push(`${symbol}: 资金面数据获取失败 - ${String(e)}`);
          }
          flowArr.push(flow);
        }),
      );

      // ── Compute scores ──
      // Fundamental: lower PE better, lower PB better, higher ROE better, higher marketCap neutral, higher revenue growth better
      const peScores = rankScore(
        fundamentalsArr.map((f) => f.pe),
        true,
      ); // lower is better
      const pbScores = rankScore(
        fundamentalsArr.map((f) => f.pb),
        true,
      );
      const roeScores = rankScore(
        fundamentalsArr.map((f) => f.roe),
        false,
      ); // higher is better
      const revenueScores = rankScore(
        fundamentalsArr.map((f) => f.revenueGrowth),
        false,
      );

      // Technical: higher return better, RSI moderate is best (transform), lower volatility better
      const returnScores = rankScore(
        techArr.map((t) => t.returnPct),
        false,
      );
      // RSI: prefer 40-60 range, penalize extremes
      const rsiScores = techArr.map((t) => {
        if (t.rsi14 == null) return 50;
        const deviation = Math.abs(t.rsi14 - 50);
        return Math.max(0, Math.round(100 - deviation * 2));
      });
      const volScores = rankScore(
        techArr.map((t) => t.volatility),
        true,
      ); // lower is better

      // Capital flow: higher main net inflow better
      const inflowScores = rankScore(
        flowArr.map((f) => f.mainNetInflow),
        false,
      );
      const inflowPctScores = rankScore(
        flowArr.map((f) => f.mainNetInflowPct),
        false,
      );

      // ── Build result rows ──
      const rows: StockCompareRow[] = symbols.map((symbol, i) => {
        const fundamental = fundamentalsArr.find((f) => f.symbol === symbol) ?? { symbol };
        const tech = techArr.find((t) => t.symbol === symbol) ?? { symbol };
        const flow = flowArr.find((f) => f.symbol === symbol) ?? { symbol };

        const fundamentalScore = Math.round(
          average([peScores[i], pbScores[i], roeScores[i], revenueScores[i]]),
        );
        const technicalScore = Math.round(
          average([returnScores[i], rsiScores[i], volScores[i]]),
        );
        const capitalFlowScore = Math.round(average([inflowScores[i], inflowPctScores[i]]));

        const compositeScore = Math.round(
          fundamentalScore * weights.fundamental +
            technicalScore * weights.technical +
            capitalFlowScore * weights.capitalFlow,
        );

        return {
          rank: 0, // will be assigned after sorting
          symbol,
          name: (fundamental as FundamentalMetrics).name,
          pe: (fundamental as FundamentalMetrics).pe,
          pb: (fundamental as FundamentalMetrics).pb,
          roe: (fundamental as FundamentalMetrics).roe,
          marketCap: (fundamental as FundamentalMetrics).marketCap,
          revenueGrowth: (fundamental as FundamentalMetrics).revenueGrowth,
          returnPct: (tech as TechnicalMetrics).returnPct,
          maPosition: (tech as TechnicalMetrics).maPosition,
          rsi14: (tech as TechnicalMetrics).rsi14,
          volatility: (tech as TechnicalMetrics).volatility,
          mainNetInflow: (flow as CapitalFlowMetrics).mainNetInflow,
          mainNetInflowPct: (flow as CapitalFlowMetrics).mainNetInflowPct,
          northboundHolding: (flow as CapitalFlowMetrics).northboundHolding,
          fundamentalScore,
          technicalScore,
          capitalFlowScore,
          compositeScore,
        };
      });

      // Sort by composite score descending and assign ranks
      rows.sort((a, b) => b.compositeScore - a.compositeScore);
      rows.forEach((r, i) => {
        r.rank = i + 1;
      });

      return {
        action: "compare",
        symbols,
        period,
        weights,
        count: rows.length,
        comparison: rows,
        summary: {
          bestOverall: rows[0]?.symbol,
          bestFundamental: [...rows].sort((a, b) => b.fundamentalScore - a.fundamentalScore)[0]
            ?.symbol,
          bestTechnical: [...rows].sort((a, b) => b.technicalScore - a.technicalScore)[0]?.symbol,
          bestCapitalFlow: [...rows].sort((a, b) => b.capitalFlowScore - a.capitalFlowScore)[0]
            ?.symbol,
        },
        errors: errors.length > 0 ? errors : undefined,
        displayHint:
          "请以对比表格形式展示结果，包含基本面、技术面、资金面三大维度的指标和评分，" +
          "最后给出综合排名和买卖建议。",
      };
    },
  };
}
