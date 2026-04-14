/**
 * Local Parquet data provider — reads tushare-format parquet files from disk.
 *
 * Provides fast historical kline data for A-shares by reading from
 * local parquet cache (default: G:/qtdata/cache/equity_tushare/).
 *
 * Supports daily, weekly, and monthly intervals.
 * Does NOT support intraday intervals or real-time quotes.
 */
import { runPythonScript } from "../bridge/python-bridge.js";
import type {
  KlineBar,
  KlineInterval,
  MarketType,
  QuantTradingConfig,
  QuoteSnapshot,
  SymbolInfo,
} from "../types.js";
import type { MarketDataProvider } from "./market-data-provider.js";

const DEFAULT_DATA_DIR = "G:/qtdata";

export class LocalParquetProvider implements MarketDataProvider {
  name = "local-parquet";
  private dataDir: string;

  constructor(private config: QuantTradingConfig) {
    this.dataDir =
      config.dataProviders?.localData?.dataDir ?? DEFAULT_DATA_DIR;
  }

  async getQuote(
    symbol: string,
    market?: MarketType,
  ): Promise<QuoteSnapshot | null> {
    const result = await runPythonScript<QuoteSnapshot>(
      "data_providers/local_parquet_provider.py",
      {
        action: "quote",
        symbol: stripCnSuffix(symbol),
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        data_dir: this.dataDir,
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
    // Only daily/weekly/monthly supported from local parquet
    if (!["1d", "1w", "1M"].includes(interval)) return [];

    const result = await runPythonScript<KlineBar[]>(
      "data_providers/local_parquet_provider.py",
      {
        action: "klines",
        symbol: stripCnSuffix(symbol),
        interval,
        start,
        end,
        market: market ?? this.config.defaultMarket ?? "cn_stock",
        data_dir: this.dataDir,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  async searchSymbols(
    keyword: string,
    _market?: MarketType,
  ): Promise<SymbolInfo[]> {
    const result = await runPythonScript<SymbolInfo[]>(
      "data_providers/local_parquet_provider.py",
      {
        action: "search",
        keyword,
        data_dir: this.dataDir,
      },
      this.config,
    );
    return result.ok ? (result.data ?? []) : [];
  }

  /** Check if local data exists for a specific symbol. */
  async checkAvailability(
    symbol: string,
    market?: MarketType,
  ): Promise<{ symbol: string; earliest: string; latest: string } | null> {
    const result = await runPythonScript<{
      symbol: string;
      earliest: string;
      latest: string;
    }>(
      "data_providers/local_parquet_provider.py",
      {
        action: "check",
        symbol: stripCnSuffix(symbol),
        market: market ?? "cn_stock",
        data_dir: this.dataDir,
      },
      this.config,
    );
    return result.ok ? (result.data ?? null) : null;
  }

  /** Health check — verify data directory. */
  async healthCheck(): Promise<{
    exists: boolean;
    daily_files: number;
    date_range: { from: string; to: string } | null;
  }> {
    const result = await runPythonScript<{
      exists: boolean;
      daily_files: number;
      date_range: { from: string; to: string } | null;
    }>(
      "data_providers/local_parquet_provider.py",
      { action: "health", data_dir: this.dataDir },
      this.config,
    );
    return (
      result.data ?? { exists: false, daily_files: 0, date_range: null }
    );
  }
}

/** Strip .SH/.SS/.SZ suffix — our Python provider expects plain codes. */
function stripCnSuffix(symbol: string): string {
  return symbol.replace(/\.(SH|SS|SZ)$/i, "");
}
