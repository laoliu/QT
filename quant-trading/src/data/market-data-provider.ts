import { runPythonScript } from "../bridge/python-bridge.js";
import { LocalParquetProvider } from "./local-parquet-provider.js";
/**
 * Data provider abstraction — fetches market data from various sources.
 *
 * Supported backends:
 *   - local-parquet (A-share daily from G:/qtdata — fastest, disk only)
 *   - akshare (A-share, futures, fund — via Python bridge)
 *   - tushare (A-share — via Python bridge)
 *   - Built-in mock/demo provider (for testing)
 *   - options_provider (ETF/index/commodity options — via Python bridge)
 *
 * All providers conform to a unified interface so the rest of the
 * plugin doesn't need to know which backend is active.
 */
import type {
  IVSurface,
  KlineBar,
  KlineInterval,
  MarketType,
  OptionChain,
  OptionContract,
  OptionQuote,
  QuantTradingConfig,
  QuoteSnapshot,
  SymbolInfo,
} from "../types.js";
import { formatTimestampToDateCST } from "../utils/cst-time.js";

// ── Provider Interface ───────────────────────────────────────────

export interface MarketDataProvider {
  name: string;
  /** Fetch real-time (or latest) quote snapshot. */
  getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null>;
  /** Fetch historical kline/OHLCV data. */
  getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]>;
  /** Search for symbols by keyword. */
  searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]>;
}

// ── Options Data Provider Interface ──────────────────────────────

/**
 * Options-specific data provider.
 * Extends base market data with option chain, greeks, IV surface.
 */
export interface OptionsDataProvider {
  /** Get T-shaped option chain for an underlying */
  getOptionChain(
    underlying: string,
    expiry?: string,
    market?: MarketType,
  ): Promise<OptionChain | null>;
  /** Get single option contract quote */
  getOptionQuote(symbol: string): Promise<OptionQuote | null>;
  /** List available option contracts for an underlying */
  getOptionContracts(underlying: string, market?: MarketType): Promise<OptionContract[]>;
  /** Get historical klines for an option contract */
  getOptionKlines(symbol: string, start: string, end: string): Promise<KlineBar[]>;
  /** Get IV surface data for an underlying */
  getIVSurface(underlying: string, market?: MarketType): Promise<IVSurface | null>;
  /** Search available option underlyings by keyword */
  searchOptionUnderlyings(keyword: string): Promise<
    Array<{
      code: string;
      name: string;
      market: MarketType;
      exchange: string;
      multiplier: number;
    }>
  >;
}

// ── AKShare Provider (Python) ────────────────────────────────────

class AKShareProvider implements MarketDataProvider {
  name = "akshare";

  constructor(private config: QuantTradingConfig) {}

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    // Strip .HK/.SH/.SS/.SZ suffix — akshare/Xueqiu expects plain codes
    const plain = symbol.replace(/\.(HK|SH|SS|SZ)$/i, "");
    const result = await runPythonScript<QuoteSnapshot>(
      "data_providers/akshare_provider.py",
      {
        action: "quote",
        symbol: plain,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
      },
      this.config,
    );
    return result.ok ? (result.data ?? null) : null;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const result = await runPythonScript<KlineBar[]>(
      "data_providers/akshare_provider.py",
      {
        action: "klines",
        symbol: stripCnSuffix(symbol),
        interval,
        start,
        end,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    const result = await runPythonScript<SymbolInfo[]>(
      "data_providers/akshare_provider.py",
      { action: "search", keyword, market: market ?? this.config.defaultMarket },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }
}

// ── Tushare Provider (Python) ────────────────────────────────────

class TushareProvider implements MarketDataProvider {
  name = "tushare";

  constructor(private config: QuantTradingConfig) {}

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const result = await runPythonScript<QuoteSnapshot>(
      "data_providers/tushare_provider.py",
      {
        action: "quote",
        symbol,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        token: this.config.dataProviders?.tushare?.token,
      },
      this.config,
    );
    return result.ok ? (result.data ?? null) : null;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const result = await runPythonScript<KlineBar[]>(
      "data_providers/tushare_provider.py",
      {
        action: "klines",
        symbol,
        interval,
        start,
        end,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        token: this.config.dataProviders?.tushare?.token,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    const result = await runPythonScript<SymbolInfo[]>(
      "data_providers/tushare_provider.py",
      {
        action: "search",
        keyword,
        market: market ?? this.config.defaultMarket,
        token: this.config.dataProviders?.tushare?.token,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }
}

// ── Yahoo Finance Provider (Python) ──────────────────────────────

class YFinanceProvider implements MarketDataProvider {
  name = "yfinance";

  constructor(private config: QuantTradingConfig) {}

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const result = await runPythonScript<QuoteSnapshot>(
      "data_providers/yfinance_provider.py",
      { action: "quote", symbol, market: market ?? this.config.defaultMarket ?? "cn_stock" },
      this.config,
    );
    if (!result.ok) {
      console.error(`[yfinance] getQuote failed for ${symbol}: ${result.error}`);
    }
    return result.ok ? (result.data ?? null) : null;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const result = await runPythonScript<KlineBar[]>(
      "data_providers/yfinance_provider.py",
      {
        action: "klines",
        symbol,
        interval,
        start,
        end,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    const result = await runPythonScript<SymbolInfo[]>(
      "data_providers/yfinance_provider.py",
      { action: "search", keyword, market: market ?? this.config.defaultMarket },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }
}

// ── JQData Provider (Python) ────────────────────────────────────

class JQDataProvider implements MarketDataProvider {
  name = "jqdata";

  constructor(private config: QuantTradingConfig) {}

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const result = await runPythonScript<QuoteSnapshot>(
      "data_providers/jqdata_provider.py",
      {
        action: "quote",
        symbol,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        username: this.config.dataProviders?.jqdata?.username,
        password: this.config.dataProviders?.jqdata?.password,
      },
      this.config,
    );
    return result.ok ? (result.data ?? null) : null;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const result = await runPythonScript<KlineBar[]>(
      "data_providers/jqdata_provider.py",
      {
        action: "klines",
        symbol,
        interval,
        start,
        end,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        username: this.config.dataProviders?.jqdata?.username,
        password: this.config.dataProviders?.jqdata?.password,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    const result = await runPythonScript<SymbolInfo[]>(
      "data_providers/jqdata_provider.py",
      {
        action: "search",
        keyword,
        market: market ?? this.config.defaultMarket,
        username: this.config.dataProviders?.jqdata?.username,
        password: this.config.dataProviders?.jqdata?.password,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }
}

// ── Demo / Mock Provider ─────────────────────────────────────────

class DemoProvider implements MarketDataProvider {
  name = "demo";

  async getQuote(symbol: string): Promise<QuoteSnapshot | null> {
    // Return a simple fake quote for development/testing
    return {
      symbol: { code: symbol, name: symbol, market: "cn_stock" },
      price: 100 + Math.random() * 50,
      change: (Math.random() - 0.5) * 10,
      changePct: (Math.random() - 0.5) * 5,
      open: 100,
      high: 110,
      low: 95,
      prevClose: 100,
      volume: 1000000,
      amount: 100000000,
      timestamp: Date.now(),
    };
  }

  async getKlines(
    _symbol: string,
    _interval: KlineInterval,
    start: string,
    end: string,
  ): Promise<KlineBar[]> {
    const bars: KlineBar[] = [];
    const startMs = new Date(start).getTime();
    const endMs = new Date(end).getTime();
    const dayMs = 86400000;
    let price = 100;

    for (let t = startMs; t <= endMs; t += dayMs) {
      const change = (Math.random() - 0.48) * 3;
      price = Math.max(1, price + change);
      bars.push({
        timestamp: t,
        open: Math.max(0.01, price - Math.random()),
        high: price + Math.random() * 2,
        low: Math.max(0.01, price - Math.random() * 2),
        close: price,
        volume: Math.floor(Math.random() * 1000000),
      });
    }
    return bars;
  }

  async searchSymbols(keyword: string): Promise<SymbolInfo[]> {
    const demoSymbols: SymbolInfo[] = [
      { code: "600519", name: "贵州茅台", market: "cn_stock", exchange: "SSE" },
      { code: "000858", name: "五粮液", market: "cn_stock", exchange: "SZSE" },
      { code: "601318", name: "中国平安", market: "cn_stock", exchange: "SSE" },
    ];
    return demoSymbols.filter((s) => s.code.includes(keyword) || s.name.includes(keyword));
  }
}

// ── QMT Bridge Provider (data via Windows QMT ContextInfo) ───────

const QMT_QUOTE_TIMEOUT_MS = 10_000;
const QMT_KLINE_TIMEOUT_MS = 15_000;

/** Well-known Shanghai-listed index codes (000xxx series on SSE). */
const _SH_INDEX_CODES = new Set([
  "000001",
  "000002",
  "000003",
  "000016",
  "000300",
  "000688",
  "000852",
  "000903",
  "000905",
  "000906",
  "000985",
]);

/** Well-known Shenzhen-listed index codes (399xxx series on SZSE). */
const _SZ_INDEX_CODES = new Set(["399001", "399005", "399006", "399106", "399300", "399673"]);

/** Check whether a 6-digit code is a known A-share index (not a tradable stock). */
export function isKnownIndexCode(symbol: string): boolean {
  const code = symbol.replace(/\.(SH|SZ|BJ|SS)$/i, "").trim();
  return _SH_INDEX_CODES.has(code) || _SZ_INDEX_CODES.has(code);
}

function toQmtSymbol(symbol: string): string {
  const code = symbol.replace(/\.(SH|SZ|BJ|SS|HK)$/i, "").replace(/[^0-9A-Za-z]/g, "");
  if (/^\d{6}$/.test(code)) {
    // Shanghai-listed indices starting with 000xxx
    if (_SH_INDEX_CODES.has(code)) return `${code}.SH`;
    if (code.startsWith("6")) return `${code}.SH`;
    if (code.startsWith("0") || code.startsWith("3")) return `${code}.SZ`;
    if (code.startsWith("4") || code.startsWith("8") || code.startsWith("9")) return `${code}.BJ`;
  }
  return symbol;
}

/**
 * Fetches market data from Windows QMT via direct HTTP or the trading bridge.
 *
 * Direct mode (windowsBridgeUrl configured):
 *   POST http://windows:58088/quote  → real-time quotes
 *   POST http://windows:58088/klines → K-line data
 *
 * Fallback mode: Uses trading-bridge-api polling relay.
 */
class QMTBridgeProvider implements MarketDataProvider {
  name = "qmt-bridge";

  constructor(private readonly bridgeUrl: string = "") {}

  private async remoteCall<T = unknown>(
    method: "get_quote" | "get_klines",
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<T | null> {
    if (this.bridgeUrl) {
      return this.directCall<T>(method, params, timeoutMs);
    }
    // Legacy polling mode
    const { submitTradingRequest, waitForTradingResponse } =
      await import("../http/trading-bridge-api.js");
    const requestId = submitTradingRequest(method, params);
    const resp = await waitForTradingResponse(requestId, timeoutMs);
    if (!resp.ok) {
      console.warn(`[QMTBridgeProvider] ${method} failed: ${resp.error ?? "unknown"}`);
      return null;
    }
    return resp.data as T;
  }

  /** Direct HTTP to Windows qmt_data_bridge.py */
  private async directCall<T = unknown>(
    method: "get_quote" | "get_klines",
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<T | null> {
    const base = this.bridgeUrl.replace(/\/$/, "");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      if (method === "get_quote") {
        const codes = params.stock_code
          ? [params.stock_code as string]
          : ((params.codes as string[]) ?? []);
        const resp = await fetch(`${base}/quote`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ codes }),
          signal: controller.signal,
        });
        const data = (await resp.json()) as {
          ok?: boolean;
          status?: string;
          data: Record<string, unknown>;
        };
        if (!data.ok && data.status !== "ok") return null;
        // AlphQ bridge nests under "quotes", legacy puts directly in data
        let quoteData = data.data ?? {};
        if (quoteData.quotes && typeof quoteData.quotes === "object") {
          quoteData = quoteData.quotes as Record<string, unknown>;
        }
        const entries = Object.entries(quoteData);
        if (entries.length === 0) return null;
        const [, q] = entries[0] as [string, Record<string, unknown>];
        return {
          // Normalize: AlphQ uses lastPrice/lastClose, legacy uses price/prevClose
          price: q.lastPrice ?? q.price,
          prev_close: q.lastClose ?? q.prevClose,
          open: q.open,
          high: q.high,
          low: q.low,
          volume: q.volume,
          amount: q.amount,
          name: q.name,
          bid1: (q.bidPrice as number[])?.[0] ?? (q.bids as Array<{ price: number }>)?.[0]?.price,
          ask1: (q.askPrice as number[])?.[0] ?? (q.asks as Array<{ price: number }>)?.[0]?.price,
        } as T;
      }
      // get_klines
      const codes = params.codes ?? (params.stock_code ? [params.stock_code] : []);
      const resp = await fetch(`${base}/klines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codes,
          period: params.period ?? "1d",
          count: params.count ?? 120,
        }),
        signal: controller.signal,
      });
      const data = (await resp.json()) as {
        ok?: boolean;
        status?: string;
        data: Record<string, unknown>;
      };
      if (!data.ok && data.status !== "ok") return null;
      // AlphQ bridge nests under "bars", legacy puts directly in data
      let klineData = data.data ?? {};
      if (klineData.bars && typeof klineData.bars === "object") {
        klineData = klineData.bars as Record<string, unknown>;
      }
      return klineData as T;
    } catch (err) {
      console.warn(`[QMTBridgeProvider] direct ${method} failed: ${(err as Error).message}`);
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const params = { stock_code: toQmtSymbol(symbol) };
    // Retry once on transient failure (network blip, brief bridge restart)
    let raw = await this.remoteCall<Record<string, unknown>>(
      "get_quote",
      params,
      QMT_QUOTE_TIMEOUT_MS,
    );
    if (!raw) {
      raw = await this.remoteCall<Record<string, unknown>>(
        "get_quote",
        params,
        QMT_QUOTE_TIMEOUT_MS,
      );
    }
    if (!raw) return null;
    const price = (raw.price as number) ?? 0;
    const prevClose = (raw.prev_close as number) ?? 0;
    const code = symbol.replace(/\.(SH|SZ|BJ|SS)$/i, "");
    return {
      symbol: { code, name: (raw.name as string) ?? "", market: market ?? "cn_stock" },
      price,
      change: prevClose > 0 ? price - prevClose : 0,
      changePct: prevClose > 0 ? ((price - prevClose) / prevClose) * 100 : 0,
      open: (raw.open as number) ?? 0,
      high: (raw.high as number) ?? 0,
      low: (raw.low as number) ?? 0,
      prevClose,
      volume: (raw.volume as number) ?? 0,
      amount: (raw.amount as number) ?? 0,
      timestamp: Date.now(),
      bid1: (raw.bid1 as number) ?? undefined,
      ask1: (raw.ask1 as number) ?? undefined,
    };
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    _start: string,
    _end: string,
    _market?: MarketType,
  ): Promise<KlineBar[]> {
    // Map interval to QMT period format
    const periodMap: Record<string, string> = {
      "1m": "1m",
      "5m": "5m",
      "15m": "15m",
      "30m": "30m",
      "60m": "1h",
      "1d": "1d",
      "1w": "1w",
      "1M": "1mon",
    };
    const period = periodMap[interval] ?? "1d";
    // Estimate count from date range
    const startMs = new Date(_start).getTime();
    const endMs = new Date(_end).getTime();
    const daysDiff = Math.max(1, Math.ceil((endMs - startMs) / 86_400_000));
    const count = interval === "1d" ? daysDiff + 5 : daysDiff * 240;

    const raw = await this.remoteCall<Record<string, Array<Record<string, unknown>>>>(
      "get_klines",
      { stock_code: toQmtSymbol(symbol), codes: [toQmtSymbol(symbol)], period, count },
      QMT_KLINE_TIMEOUT_MS,
    );
    if (!raw) return [];

    // raw is {stockId: [{date, open, high, low, close, volume}, ...]}
    const code = symbol.replace(/\.(SH|SZ|BJ|SS)$/i, "");
    const bars = raw[code] ?? Object.values(raw)[0] ?? [];
    return bars
      .filter((b) => (b.close as number) > 0)
      .map((b) => {
        const dateStr = String(b.date ?? "");
        const ts =
          dateStr.length === 8
            ? new Date(
                `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`,
              ).getTime()
            : Date.now();
        return {
          timestamp: ts,
          open: (b.open as number) ?? 0,
          high: (b.high as number) ?? 0,
          low: (b.low as number) ?? 0,
          close: (b.close as number) ?? 0,
          volume: (b.volume as number) ?? 0,
        };
      })
      .filter((b) => {
        const bDate = formatTimestampToDateCST(b.timestamp);
        return bDate >= _start && bDate <= _end;
      });
  }

  async searchSymbols(_keyword: string, _market?: MarketType): Promise<SymbolInfo[]> {
    // QMT bridge doesn't support symbol search; return empty
    return [];
  }
}

/** Strip .SH/.SS/.SZ suffix from CN stock codes — akshare expects plain 6-digit codes. */
function stripCnSuffix(symbol: string): string {
  return symbol.replace(/\.(SH|SS|SZ)$/i, "");
}

// ── Market Detection ─────────────────────────────────────────────

/** Auto-detect market type from symbol format.
 *  Logs a warning to console when a symbol doesn't match any known pattern. */
export function detectMarketFromSymbol(symbol: string): MarketType {
  const sym = symbol.trim().toUpperCase();
  if (sym.endsWith(".HK")) return "hk_stock";
  // .SS = SSE (Yahoo), .SH = SSE (QMT/Wind/东财), .SZ = SZSE
  if (sym.endsWith(".SS") || sym.endsWith(".SH") || sym.endsWith(".SZ")) return "cn_stock";
  // ETF option codes: 8-digit starting with 1000/9000
  if (/^\d{8}$/.test(sym) && (sym.startsWith("1000") || sym.startsWith("9000")))
    return "etf_option";
  // CFFEX index options: e.g. IO2406-C-4000
  if (sym.includes("IO") && (sym.includes("-C-") || sym.includes("-P-"))) return "index_option";
  // Commodity options — various exchange conventions:
  //   DCE:  m2404-C-3200, c2405-P-2600 (豆粕/玉米 etc.)
  //   SHFE: cu2404C52000, au2406C500, ru2405C14500 (ͭ/金/橡胶)
  //   CZCE: SR405C5600, CF405P14000 (白糖/棉花 — 3-digit contract month)
  if (/^[A-Z]{1,2}\d{4}-[CP]-/.test(sym)) return "commodity_option"; // DCE style
  if (/^[A-Z]{1,2}\d{3,4}[CP]\d+$/.test(sym)) return "commodity_option"; // SHFE/CZCE style
  // CN futures: letter prefix + contract digits (e.g. IF2503, RB2505, AU2406, IC, M2409)
  if (
    /^(IF|IH|IC|IM|TS|TF|TL|T\d|RB|HC|CU|AL|ZN|NI|SN|PB|RU|FU|BU|AU|AG|NR|SS|BC|LU|PG|EB|SP|SC|UR|SA|MA|CF|SR|ZC|JD|LH|AP|CJ|PF|PK|SI|SH|AO|BR|EC|LC|M|Y|P|A|B|C|CS|J|JM|I|L|V|PP|EG|TA|OI|RM|FG|WH|PM|RI|RS|LR|SF|SM|CY|PX|SJ)(\d{3,4})?$/i.test(
      sym,
    )
  )
    return "futures";
  // Pure 6-digit numeric
  if (/^\d{6}$/.test(sym)) {
    // ETF/LOF fund codes:
    //   SH (SSE): 51xxxx, 56xxxx, 58xxxx, 50xxxx, 52xxxx
    //   SZ (SZSE): 159xxx, 15xxxx
    if (/^(51|56|58|50|52)\d{4}$/.test(sym) || /^15\d{4}$/.test(sym)) return "fund";
    return "cn_stock";
  }
  // 4-5 digit numeric with .HK or bare → HK stock  (e.g. 0777, 00700)
  if (/^\d{4,5}$/.test(sym)) return "hk_stock";
  // Alphabetic → US stock
  if (/^[A-Z]+$/.test(sym)) return "us_stock";
  // Fallback — log a warning so users can debug misclassified tickers
  console.warn(
    `[quant-trading] detectMarketFromSymbol: unknown pattern "${sym}", defaulting to cn_stock`,
  );
  return "cn_stock";
}

/**
 * Minimum order lot size for a given symbol.
 *
 * - STAR Market (科创板, 688xxx): 200 shares minimum, then 1-share increments
 * - Other CN stocks / funds: 100 shares (standard lot)
 * - Everything else: 1 share
 */
export function getLotSize(symbol: string, market?: MarketType): number {
  const m = market ?? detectMarketFromSymbol(symbol);
  if (m === "cn_stock" || m === "fund") {
    // 科创板 688xxx: min 200 shares
    const bare = symbol.replace(/\.(SH|SS|SZ)$/i, "");
    if (/^688\d{3}$/.test(bare)) return 200;
    return 100;
  }
  return 1;
}

// ── Smart Provider (auto-routes by market) ───────────────────────

/**
 * Routes data requests to the appropriate backend by market type:
 *   - cn_stock / futures / fund → AKShare (Chinese finance APIs)
 *   - hk_stock / us_stock / others → YFinance (Yahoo Finance)
 *
 * When AKShare fails for cn_stock, falls back through Tushare → JQData → YFinance.
 */
class SmartProvider implements MarketDataProvider {
  name = "smart";
  private localParquet: LocalParquetProvider | null;
  private qmt: QMTBridgeProvider | null;
  private akshare: AKShareProvider;
  private yfinance: YFinanceProvider;
  private tushare: TushareProvider;
  private jqdata: JQDataProvider;

  constructor(private config: QuantTradingConfig) {
    // Local parquet is enabled by default when not explicitly disabled
    const localCfg = config.dataProviders?.localData;
    this.localParquet =
      localCfg?.enabled === false ? null : new LocalParquetProvider(config);
    // Use QMT bridge for real-time data when windowsBridgeUrl is configured
    const qmtUrl = config.tradingAccount?.qmt?.windowsBridgeUrl;
    this.qmt = qmtUrl ? new QMTBridgeProvider(qmtUrl) : null;
    this.akshare = new AKShareProvider(config);
    this.yfinance = new YFinanceProvider(config);
    this.tushare = new TushareProvider(config);
    this.jqdata = new JQDataProvider(config);
  }

  private pick(market?: MarketType): MarketDataProvider {
    if (market === "us_stock") return this.yfinance;
    // hk_stock + cn_stock + futures + fund → akshare (Xueqiu API, no rate limit)
    return this.akshare;
  }

  /** Get ordered fallback providers for CN markets. */
  private cnFallbacks(): MarketDataProvider[] {
    const fallbacks: MarketDataProvider[] = [];
    if (this.config.dataProviders?.tushare?.enabled && this.config.dataProviders.tushare.token) {
      fallbacks.push(this.tushare);
    }
    if (this.config.dataProviders?.jqdata?.enabled) {
      fallbacks.push(this.jqdata);
    }
    fallbacks.push(this.yfinance);
    return fallbacks;
  }

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const m = market ?? detectMarketFromSymbol(symbol);

    // Priority 1: QMT bridge for CN real-time quotes (direct xtdata, fastest for real-time)
    if (this.qmt && (m === "cn_stock" || m === "futures" || m === "fund")) {
      const qmtQuote = await this.qmt.getQuote(symbol, m);
      if (qmtQuote) return qmtQuote;
    }

    // Priority 2: AKShare (Sina/Xueqiu)
    const quote = await this.pick(m).getQuote(symbol, m);
    if (quote) return quote;

    // Fallback chain for CN markets
    if (m === "cn_stock" || m === "hk_stock" || m === "fund") {
      for (const provider of this.cnFallbacks()) {
        const fallbackQuote = await provider.getQuote(symbol, m);
        if (fallbackQuote) return fallbackQuote;
      }
    }
    return null;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const m = market ?? detectMarketFromSymbol(symbol);

    // Priority 1: local parquet for CN markets (fastest — disk read, no API)
    if (this.localParquet && (m === "cn_stock" || m === "fund") && ["1d", "1w", "1M"].includes(interval)) {
      const localBars = await this.localParquet.getKlines(symbol, interval, start, end, m);
      if (localBars.length > 0) return localBars;
    }

    // Priority 2: QMT bridge (xtdata, local network)
    if (this.qmt && (m === "cn_stock" || m === "futures" || m === "fund")) {
      const qmtBars = await this.qmt.getKlines(symbol, interval, start, end, m);
      if (qmtBars.length > 0) return qmtBars;
    }

    // Priority 3: AKShare (remote API)
    const bars = await this.pick(m).getKlines(symbol, interval, start, end, m);
    if (bars.length > 0) return bars;

    // Fallback chain for CN markets
    if (m === "cn_stock" || m === "fund") {
      for (const provider of this.cnFallbacks()) {
        const fallbackBars = await provider.getKlines(symbol, interval, start, end, m);
        if (fallbackBars.length > 0) return fallbackBars;
      }
    }
    return [];
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    // Search both providers and merge
    const [akResults, yfResults] = await Promise.all([
      this.akshare.searchSymbols(keyword, market),
      this.yfinance.searchSymbols(keyword, market),
    ]);
    // Deduplicate by code
    const seen = new Set(akResults.map((s) => s.code));
    return [...akResults, ...yfResults.filter((s) => !seen.has(s.code))];
  }
}

// ── LRU Cache Layer ──────────────────────────────────────────────

interface CacheEntry<T> {
  value: T;
  expiresAt: number;
}

/**
 * Simple time-based LRU cache for market data.
 * Reduces repeated Python bridge calls for frequently-accessed quotes/klines.
 */
export class LRUCache<T> {
  private cache = new Map<string, CacheEntry<T>>();

  constructor(
    private maxSize: number,
    private ttlMs: number,
  ) {}

  get(key: string): T | undefined {
    const entry = this.cache.get(key);
    if (!entry) return undefined;
    if (Date.now() > entry.expiresAt) {
      this.cache.delete(key);
      return undefined;
    }
    // Move to end (most-recently used)
    this.cache.delete(key);
    this.cache.set(key, entry);
    return entry.value;
  }

  set(key: string, value: T): void {
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.maxSize) {
      // Evict oldest (first entry)
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) this.cache.delete(firstKey);
    }
    this.cache.set(key, { value, expiresAt: Date.now() + this.ttlMs });
  }

  clear(): void {
    this.cache.clear();
  }

  get size(): number {
    return this.cache.size;
  }
}

/** Default cache settings */
const QUOTE_CACHE_SIZE = 200;
const QUOTE_CACHE_TTL_MS = 30_000; // 30 seconds — quotes are time-sensitive
const KLINE_CACHE_SIZE = 100;
const KLINE_CACHE_TTL_MS = 300_000; // 5 minutes — klines change less often

/**
 * Wraps any MarketDataProvider with an LRU cache for getQuote and getKlines.
 * searchSymbols is not cached (infrequent, user-interactive).
 */
class CachedProvider implements MarketDataProvider {
  name: string;
  private quoteCache = new LRUCache<QuoteSnapshot | null>(QUOTE_CACHE_SIZE, QUOTE_CACHE_TTL_MS);
  private klineCache = new LRUCache<KlineBar[]>(KLINE_CACHE_SIZE, KLINE_CACHE_TTL_MS);

  constructor(private inner: MarketDataProvider) {
    this.name = `cached(${inner.name})`;
  }

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const key = `${symbol}:${market ?? ""}`;
    const cached = this.quoteCache.get(key);
    if (cached !== undefined) return cached;

    const result = await this.inner.getQuote(symbol, market);
    // Only cache non-null results — transient failures should not lock out the symbol
    if (result !== null) {
      this.quoteCache.set(key, result);
    }
    return result;
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const key = `${symbol}:${interval}:${start}:${end}:${market ?? ""}`;
    const cached = this.klineCache.get(key);
    if (cached !== undefined) return cached;

    const result = await this.inner.getKlines(symbol, interval, start, end, market);
    // Only cache non-empty results — transient failures should not lock out the symbol
    if (result.length > 0) {
      this.klineCache.set(key, result);
    }
    return result;
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    return this.inner.searchSymbols(keyword, market);
  }

  /** Expose for testing / manual invalidation. */
  clearCache(): void {
    this.quoteCache.clear();
    this.klineCache.clear();
  }
}

// ── Options Smart Provider (Python bridge) ──────────────────────

/**
 * Options data provider that delegates to the Python options_provider.py
 * via the JSON subprocess bridge.
 *
 * Auto-detects option market from underlying code.
 * Caches: option chain (60s), option quote (15s), IV surface (300s).
 */
class OptionsSmartProvider implements OptionsDataProvider {
  private chainCache = new LRUCache<OptionChain | null>(50, 60_000);
  private quoteCache = new LRUCache<OptionQuote | null>(200, 15_000);
  private ivCache = new LRUCache<IVSurface | null>(20, 300_000);

  constructor(private config: QuantTradingConfig) {}

  async getOptionChain(
    underlying: string,
    expiry?: string,
    market?: MarketType,
  ): Promise<OptionChain | null> {
    const key = `${underlying}:${expiry ?? ""}:${market ?? ""}`;
    const cached = this.chainCache.get(key);
    if (cached !== undefined) return cached;

    const result = await runPythonScript<OptionChain>(
      "data_providers/options_provider.py",
      { action: "chain", underlying, expiry, market },
      this.config,
    );
    const data = result.ok ? (result.data ?? null) : null;
    // Only cache successful results so transient errors don't block retries
    if (data !== null) this.chainCache.set(key, data);
    return data;
  }

  async getOptionQuote(symbol: string): Promise<OptionQuote | null> {
    const cached = this.quoteCache.get(symbol);
    if (cached !== undefined) return cached;

    const result = await runPythonScript<OptionQuote>(
      "data_providers/options_provider.py",
      { action: "quote", symbol },
      this.config,
    );
    const data = result.ok ? (result.data ?? null) : null;
    // Only cache successful results so transient errors don't block retries
    if (data !== null) this.quoteCache.set(symbol, data);
    return data;
  }

  async getOptionContracts(underlying: string, market?: MarketType): Promise<OptionContract[]> {
    const result = await runPythonScript<OptionContract[]>(
      "data_providers/options_provider.py",
      { action: "contracts", underlying, market },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async getOptionKlines(symbol: string, start: string, end: string): Promise<KlineBar[]> {
    const result = await runPythonScript<KlineBar[]>(
      "data_providers/options_provider.py",
      { action: "klines", symbol, startDate: start, endDate: end },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async getIVSurface(underlying: string, market?: MarketType): Promise<IVSurface | null> {
    const key = `${underlying}:${market ?? ""}`;
    const cached = this.ivCache.get(key);
    if (cached !== undefined) return cached;

    const result = await runPythonScript<IVSurface>(
      "data_providers/options_provider.py",
      { action: "iv_surface", underlying, market },
      this.config,
    );
    const data = result.ok ? (result.data ?? null) : null;
    if (data !== null) this.ivCache.set(key, data);
    return data;
  }

  async searchOptionUnderlyings(keyword: string): Promise<
    Array<{
      code: string;
      name: string;
      market: MarketType;
      exchange: string;
      multiplier: number;
    }>
  > {
    const result = await runPythonScript<
      Array<{
        code: string;
        name: string;
        market: MarketType;
        exchange: string;
        multiplier: number;
      }>
    >("data_providers/options_provider.py", { action: "search", keyword }, this.config);
    return result.ok ? (result.data ?? []) : [];
  }
}

// ── Factory ──────────────────────────────────────────────────────

export function createOptionsDataProvider(config: QuantTradingConfig): OptionsDataProvider {
  return new OptionsSmartProvider(config);
}

export function createDataProvider(config: QuantTradingConfig): MarketDataProvider {
  // Shorthand selector: `dataProvider: "demo"` returns offline demo data
  if (config.dataProvider === "demo") {
    return new DemoProvider();
  }

  // Local parquet provider (direct disk reads, no API)
  if (config.dataProvider === "local") {
    return new CachedProvider(new LocalParquetProvider(config));
  }

  // When broker is remote-qmt, use QMT bridge for CN stock data with akshare fallback
  const isRemoteQmt = config.tradingAccount?.broker === "remote-qmt";

  let inner: MarketDataProvider;
  if (isRemoteQmt) {
    // QMT bridge as primary for CN stocks, with SmartProvider fallback for
    // search, HK/US, and when QMT bridge times out
    inner = new QMTFallbackProvider(config);
  } else if (config.dataProvider === "jqdata" || config.dataProviders?.jqdata?.enabled) {
    inner = new JQDataProvider(config);
  } else if (config.dataProvider === "tushare" || config.dataProviders?.tushare?.enabled) {
    inner = new TushareProvider(config);
  } else if (config.dataProvider === "yfinance") {
    inner = new YFinanceProvider(config);
  } else if (config.dataProvider === "akshare") {
    inner = new AKShareProvider(config);
  } else {
    inner = new SmartProvider(config);
  }

  // Wrap with LRU cache to reduce repeated bridge calls
  return new CachedProvider(inner);
}

/**
 * Uses QMT bridge as primary data source for CN stocks,
 * falls back to SmartProvider (akshare/yfinance) on failure.
 */
class QMTFallbackProvider implements MarketDataProvider {
  name = "qmt-fallback";
  private qmt: QMTBridgeProvider;
  private smart: SmartProvider;
  private localParquet: LocalParquetProvider | null;

  constructor(config: QuantTradingConfig) {
    this.qmt = new QMTBridgeProvider(config.tradingAccount?.qmt?.windowsBridgeUrl ?? "");
    this.smart = new SmartProvider(config);
    const localCfg = config.dataProviders?.localData;
    this.localParquet =
      localCfg?.enabled === false ? null : new LocalParquetProvider(config);
  }

  async getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    const m = market ?? detectMarketFromSymbol(symbol);
    // Only route CN stocks through QMT
    if (m === "cn_stock" || m === "futures" || m === "fund") {
      const quote = await this.qmt.getQuote(symbol, m);
      if (quote) return quote;
    }
    return this.smart.getQuote(symbol, market);
  }

  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
    market?: MarketType,
  ): Promise<KlineBar[]> {
    const m = market ?? detectMarketFromSymbol(symbol);

    // Priority 1: local parquet (disk read, fastest)
    if (this.localParquet && (m === "cn_stock" || m === "fund") && ["1d", "1w", "1M"].includes(interval)) {
      const localBars = await this.localParquet.getKlines(symbol, interval, start, end, m);
      if (localBars.length > 0) return localBars;
    }

    // Priority 2: QMT bridge
    if (m === "cn_stock" || m === "futures" || m === "fund") {
      const bars = await this.qmt.getKlines(symbol, interval, start, end, m);
      if (bars.length > 0) return bars;
    }

    // Priority 3: SmartProvider (AKShare → tushare → jqdata → yfinance)
    return this.smart.getKlines(symbol, interval, start, end, market);
  }

  async searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    return this.smart.searchSymbols(keyword, market);
  }
}
