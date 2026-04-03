import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider } from "../data/market-data-provider.js";
/**
 * CLI command: openclaw quant data
 */
import type { QuantTradingConfig } from "../types.js";

export async function runCliData(
  action: string,
  opts: { symbol?: string; market?: string; table?: string },
  config: QuantTradingConfig,
): Promise<void> {
  const provider = createDataProvider(config);

  switch (action) {
    case "fetch": {
      if (!opts.symbol) {
        console.log("Usage: openclaw quant data fetch --symbol <code>");
        return;
      }
      console.log(`Fetching data for ${opts.symbol}...`);
      const quote = await provider.getQuote(opts.symbol, opts.market as never);
      if (quote) {
        console.log(`  ${quote.symbol.name} (${quote.symbol.code})`);
        console.log(`  Price: ¥${quote.price.toFixed(2)}`);
        console.log(`  Change: ${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`);
      } else {
        console.log(`  No data found for ${opts.symbol}`);
      }
      break;
    }
    case "list":
      console.log("Data providers:");
      console.log(`  Active: ${provider.name}`);
      console.log(`  Available: akshare, tushare, demo`);
      break;
    case "clean": {
      // Clear all local cache via the Python cache script
      const table = opts.table;
      console.log(table ? `Clearing cache table: ${table}…` : "Clearing all cache tables…");
      const result = await runPythonScript<{
        status?: string;
        cleared?: string;
        error?: string;
      }>("cache/stock_cache.py", { action: "clear", table }, config);

      if (result.ok && result.data) {
        const { status, cleared, error: pyErr } = result.data;
        if (pyErr) {
          console.error(`  Cache cleanup failed: ${pyErr}`);
        } else {
          console.log(`  ✓ ${status ?? "Done"}`);
          if (cleared) console.log(`  Cleared: ${cleared}`);
        }
      } else {
        // Python not available or script not found — try deleting cache dir directly
        const { default: fs } = await import("node:fs/promises");
        const { default: path } = await import("node:path");
        const { homedir } = await import("node:os");
        const cacheDir = path.join(homedir(), ".openclaw", "extensions", "quant-trading", "cache");
        try {
          await fs.rm(cacheDir, { recursive: true, force: true });
          console.log(`  ✓ 已清理缓存目录: ${cacheDir}`);
        } catch {
          console.log(`  缓存目录不存在或已清空 (${cacheDir})`);
          if (result.error) {
            console.log(`  Python cache script: ${result.error}`);
          }
        }
      }
      break;
    }
    default:
      console.log(`Unknown action: ${action}`);
      console.log("Usage: openclaw quant data <fetch|list|clean>");
  }
}
