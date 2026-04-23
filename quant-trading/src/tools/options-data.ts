/**
 * options_data tool — AI-callable tool for fetching options market data.
 *
 * Supports ETF options, index options, and commodity options on the Chinese market.
 * Data sourced via AKShare Python bridge (options_provider.py).
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createOptionsDataProvider } from "../data/market-data-provider.js";
import type {
  GreeksSnapshot,
  IVSurface,
  MarketType,
  OptionChain,
  OptionContract,
  OptionQuote,
  QuantTradingConfig,
} from "../types.js";
import { todayCST } from "../utils/cst-time.js";

export function createOptionsDataTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const provider = createOptionsDataProvider(config);

  return {
    name: "options_data",
    description:
      "获取期权市场数据：期权链、实时行情、合约信息、K线、IV曲面、希腊值。\n" +
      "支持 ETF期权（50ETF/300ETF/500ETF/创业板ETF/科创50ETF等）、\n" +
      "股指期权（沪深300/中证1000/中证500/上证50）、\n" +
      "商品期权（豆粕/白糖/ͭ/黄金/铁矿等）。\n\n" +
      "action=chain 获取期权链（T型报价），\n" +
      "action=quote 获取期权合约实时行情，\n" +
      "action=contracts 获取合约列表，\n" +
      "action=klines 获取期权合约K线，\n" +
      "action=iv_surface 获取IV曲面/微笑曲线，\n" +
      "action=greeks 计算希腊值（Delta/Gamma/Theta/Vega/Rho），\n" +
      "action=search 搜索期权标的。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["chain", "quote", "contracts", "klines", "iv_surface", "greeks", "search"],
          description:
            "操作类型: chain=期权链, quote=合约行情, contracts=合约列表, " +
            "klines=合约K线, iv_surface=IV曲面, greeks=希腊值, search=搜索标的",
        },
        underlying: {
          type: "string",
          description:
            "标的代码，如 510050（50ETF）、510300（300ETF）、000300（沪深300股指）、" +
            "m（豆粕）、cu（铜）、au（黄金）",
        },
        symbol: {
          type: "string",
          description: "期权合约代码（仅 action=quote/klines/greeks 时使用）",
        },
        expiry: {
          type: "string",
          description: "到期月份 YYYY-MM 或 YYYYMM（仅 action=chain 时可选，筛选特定到期日）",
        },
        market: {
          type: "string",
          enum: ["etf_option", "index_option", "commodity_option"],
          description: "期权市场类型（通常可自动检测，有歧义时指定）",
        },
        startDate: {
          type: "string",
          description: "开始日期 YYYY-MM-DD（仅 action=klines）",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD（仅 action=klines）",
        },
        keyword: {
          type: "string",
          description: "搜索关键词（仅 action=search）",
        },
        // Greeks calculation arguments
        spotPrice: {
          type: "number",
          description: "标的现价（action=greeks 时必填）",
        },
        strikePrice: {
          type: "number",
          description: "行权价（action=greeks 时使用）",
        },
        optionType: {
          type: "string",
          enum: ["call", "put"],
          description: "期权类型（action=greeks 时使用）",
        },
        daysToExpiry: {
          type: "number",
          description: "剩余天数（action=greeks 时使用）",
        },
        volatility: {
          type: "number",
          description: "波动率（action=greeks 时使用，0.25 表示 25%；若不提供则用历史波动率）",
        },
        riskFreeRate: {
          type: "number",
          description: "无风险利率（action=greeks 时可选，默认用配置值）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── Option Chain ──────────────────────────────────────────
        case "chain": {
          const underlying = args.underlying as string;
          if (!underlying) return { error: "请提供标的代码 (underlying)，如 510050、000300、m" };

          const chain = await provider.getOptionChain(
            underlying,
            args.expiry as string | undefined,
            args.market as MarketType | undefined,
          );
          if (!chain) return { error: `无法获取 ${underlying} 的期权链数据` };

          return formatChain(chain);
        }

        // ── Option Quote ──────────────────────────────────────────
        case "quote": {
          const symbol = args.symbol as string;
          if (!symbol) return { error: "请提供期权合约代码 (symbol)" };

          const quote = await provider.getOptionQuote(symbol);
          if (!quote) return { error: `无法获取 ${symbol} 的期权行情` };
          return formatQuote(quote);
        }

        // ── Option Contracts ──────────────────────────────────────
        case "contracts": {
          const underlying = args.underlying as string;
          if (!underlying) return { error: "请提供标的代码 (underlying)" };

          const contracts = await provider.getOptionContracts(
            underlying,
            args.market as MarketType | undefined,
          );
          if (contracts.length === 0) return { error: `未找到 ${underlying} 的期权合约` };

          return {
            underlying,
            count: contracts.length,
            contracts: contracts.map(formatContract),
          };
        }

        // ── Option Klines ─────────────────────────────────────────
        case "klines": {
          const symbol = args.symbol as string;
          if (!symbol) return { error: "请提供期权合约代码 (symbol)" };

          const start = (args.startDate as string) ?? getDefaultStartDate(60);
          const end = (args.endDate as string) ?? todayStr();
          const bars = await provider.getOptionKlines(symbol, start, end);
          if (bars.length === 0) return { error: `无法获取 ${symbol} 的K线数据` };

          return {
            symbol,
            count: bars.length,
            data: bars.map((b) => {
              // Python may return `date` (string) or `timestamp` (number)
              const raw = b as { date?: string };
              const dateStr = raw.date
                ? raw.date
                : new Date(b.timestamp).toISOString().slice(0, 10);
              return {
                date: dateStr,
                open: b.open.toFixed(4),
                high: b.high.toFixed(4),
                low: b.low.toFixed(4),
                close: b.close.toFixed(4),
                volume: b.volume,
              };
            }),
          };
        }

        // ── IV Surface ────────────────────────────────────────────
        case "iv_surface": {
          const underlying = args.underlying as string;
          if (!underlying) return { error: "请提供标的代码 (underlying)" };

          const surface = await provider.getIVSurface(
            underlying,
            args.market as MarketType | undefined,
          );
          if (!surface) return { error: `无法获取 ${underlying} 的IV曲面` };

          return formatIVSurface(surface);
        }

        // ── Greeks Calculation ─────────────────────────────────────
        case "greeks": {
          const strike = args.strikePrice as number;
          const optType = args.optionType as string;
          const dte = args.daysToExpiry as number;
          if (!strike || !optType || !dte) {
            return {
              error:
                "计算希腊值需要: strikePrice (行权价), optionType (call/put), daysToExpiry (剩余天数)",
            };
          }

          const r = (args.riskFreeRate as number) ?? config.optionsConfig?.riskFreeRate ?? 0.025;
          const sigma = args.volatility as number | undefined;
          const spot = args.spotPrice as number | undefined;

          const result = await runPythonScript<GreeksSnapshot>(
            "engines/greeks_engine.py",
            {
              action: "greeks",
              spotPrice: spot,
              strike,
              optionType: optType,
              timeToExpiry: dte / 365,
              riskFreeRate: r,
              volatility: sigma,
              dividendYield: config.optionsConfig?.dividendYield ?? 0,
            },
            config,
          );

          if (!result.ok || !result.data) {
            return { error: result.error ?? "希腊值计算失败" };
          }

          return {
            strike,
            optionType: optType,
            daysToExpiry: dte,
            ...formatGreeks(result.data),
          };
        }

        // ── Search Underlyings ────────────────────────────────────
        case "search": {
          const keyword = (args.keyword as string) ?? (args.underlying as string);
          if (!keyword) return { error: "请提供搜索关键词 (keyword)" };

          const results = await provider.searchOptionUnderlyings(keyword);
          if (results.length === 0) return { message: `未找到与 "${keyword}" 相关的期权标的` };

          return {
            count: results.length,
            results: results.map((r) => ({
              code: r.code,
              name: r.name,
              market: r.market,
              exchange: r.exchange,
              multiplier: r.multiplier,
            })),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Formatting helpers ───────────────────────────────────────────

function formatChain(chain: OptionChain) {
  return {
    underlying: chain.underlying,
    underlyingPrice: chain.underlyingPrice,
    currentExpiry: chain.currentExpiry,
    expiryDates: chain.expiryDates,
    totalContracts: chain.calls.length + chain.puts.length,
    calls: chain.calls.slice(0, 30).map(formatQuote),
    puts: chain.puts.slice(0, 30).map(formatQuote),
    hint:
      chain.calls.length > 30
        ? `共 ${chain.calls.length} 个看涨 + ${chain.puts.length} 个看跌合约，已显示前30个`
        : undefined,
  };
}

function formatContract(c: OptionContract) {
  return {
    symbol: c.symbol,
    underlying: c.underlying,
    underlyingName: c.underlyingName,
    strike: c.strike,
    expiry: c.expiry,
    optionType: c.optionType,
    exerciseType: c.exerciseType,
    multiplier: c.contractMultiplier,
    exchange: c.exchange,
  };
}

function formatQuote(q: OptionQuote) {
  return {
    symbol: q.symbol,
    name: q.name,
    price: q.price.toFixed(4),
    change: q.change != null ? `${q.change >= 0 ? "+" : ""}${q.change.toFixed(4)}` : undefined,
    changePct:
      q.changePct != null ? `${q.changePct >= 0 ? "+" : ""}${q.changePct.toFixed(2)}%` : undefined,
    bid: q.bid1?.toFixed(4),
    bidSize: q.bidVol1,
    ask: q.ask1?.toFixed(4),
    askSize: q.askVol1,
    volume: q.volume,
    openInterest: q.openInterest,
    impliedVol: q.impliedVol != null ? (q.impliedVol * 100).toFixed(2) + "%" : undefined,
    underlying: q.underlying,
    strike: q.strike,
    expiry: q.expiry,
    optionType: q.optionType,
    delta: q.delta?.toFixed(4),
    gamma: q.gamma?.toFixed(6),
    theta: q.theta?.toFixed(4),
    vega: q.vega?.toFixed(4),
    intrinsicValue: q.intrinsicValue?.toFixed(4),
    timeValue: q.timeValue?.toFixed(4),
  };
}

function formatGreeks(g: GreeksSnapshot) {
  return {
    delta: g.delta?.toFixed(4),
    gamma: g.gamma?.toFixed(6),
    theta: g.theta?.toFixed(4),
    vega: g.vega?.toFixed(4),
    rho: g.rho?.toFixed(4),
    impliedVol: g.impliedVol != null ? (g.impliedVol * 100).toFixed(2) + "%" : undefined,
    charm: g.charm?.toFixed(6),
    vanna: g.vanna?.toFixed(6),
    vomma: g.vomma?.toFixed(6),
  };
}

function formatIVSurface(surface: IVSurface) {
  return {
    underlying: surface.underlying,
    underlyingPrice: surface.underlyingPrice,
    timestamp: new Date(surface.timestamp).toISOString(),
    dataCount: surface.data.length,
    data: surface.data.slice(0, 100).map((d) => ({
      expiry: d.expiry,
      strike: d.strike,
      iv: (d.iv * 100).toFixed(2) + "%",
      delta: d.delta?.toFixed(4),
      moneyness: d.moneyness?.toFixed(4),
    })),
    hint:
      surface.data.length > 100 ? `共 ${surface.data.length} 个数据点，已显示前100个` : undefined,
  };
}

function todayStr(): string {
  return todayCST();
}

function getDefaultStartDate(daysBack: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toISOString().slice(0, 10);
}
