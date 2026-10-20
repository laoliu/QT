import type { MarketType, SymbolInfo } from "../types.js";

const INDICES = [
  { code: "000001.SH", name: "上证指数", aliases: ["上证综指", "沪指"] },
  { code: "000002.SH", name: "上证A指", aliases: ["上证A股指数"] },
  { code: "000003.SH", name: "上证B指", aliases: ["上证B股指数"] },
  { code: "399001.SZ", name: "深证成指", aliases: ["深成指", "深圳成指"] },
  { code: "399006.SZ", name: "创业板指", aliases: ["创业板指数"] },
  { code: "000300.SH", name: "沪深300", aliases: ["沪深300指数", "CSI300"] },
  { code: "000016.SH", name: "上证50", aliases: ["上证50指数"] },
  { code: "000905.SH", name: "中证500", aliases: ["中证500指数"] },
  { code: "000852.SH", name: "中证1000", aliases: ["中证1000指数"] },
  { code: "000688.SH", name: "科创50", aliases: ["科创50指数"] },
  { code: "000903.SH", name: "中证100", aliases: ["中证100指数"] },
  { code: "000906.SH", name: "中证800", aliases: ["中证800指数"] },
  { code: "000985.SH", name: "中证全指", aliases: ["中证全指指数"] },
  { code: "399106.SZ", name: "深证综指", aliases: ["深圳综指"] },
  { code: "399300.SZ", name: "沪深300（深市）", aliases: [] },
  { code: "399005.SZ", name: "中小100", aliases: ["中小板指"] },
  { code: "399673.SZ", name: "创业板50", aliases: ["创业板50指数"] },
];

function qualifiedCode(value: string): string {
  const text = value.trim().toUpperCase();
  const prefix = /^(SH|SZ|BJ)(\d{6})$/.exec(text);
  return prefix ? `${prefix[2]}.${prefix[1]}` : text.replace(/\.SS$/, ".SH");
}

/** Preserve exchange-qualified identity; a bare 000001 is never inferred to be an index. */
export function normalizeCnInstrument(symbol: string, market?: MarketType): { symbol: string; market?: MarketType } {
  if (market && !["cn_stock", "index", "fund"].includes(market)) return { symbol, market };
  const code = qualifiedCode(symbol);
  const named = INDICES.find(i => [i.name, ...i.aliases].some(n => n.toUpperCase() === code));
  if (named) return { symbol: named.code, market: "index" };
  if (/^\d{6}\.(SH|SZ|BJ)$/.test(code)) {
    const isIndex = INDICES.some(i => i.code === code)
      || /^399\d{3}\.SZ$/.test(code)
      || (market === "index");
    return { symbol: code, market: isIndex ? "index" : (market ?? "cn_stock") };
  }
  if (/^\d{6}$/.test(code) && (market === "index" || code.startsWith("399"))) {
    return { symbol: `${code}.${code.startsWith("399") ? "SZ" : "SH"}`, market: "index" };
  }
  return { symbol: symbol.trim(), market };
}

/** Local index names work even when external stock-search services are unavailable. */
export function searchCnIndices(keyword: string, market?: MarketType): SymbolInfo[] {
  if (market && market !== "cn_stock" && market !== "index") return [];
  const query = qualifiedCode(keyword);
  if (!query) return [];
  // An explicitly requested stock code must not turn into the same-code index.
  if (market === "cn_stock" && /^\d{6}$/.test(query)) return [];
  return INDICES.filter(i => i.code.includes(query)
    || [i.name, ...i.aliases].some(n => n.toUpperCase().includes(query)))
    .map(i => ({ code: i.code, name: i.name, market: "index", exchange: i.code.endsWith(".SH") ? "SSE" : "SZSE" }));
}

export function parseQuoteDataTimestamp(value: unknown): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "number" || (typeof value === "string" && /^\d{10,13}$/.test(value))) {
    const n = Number(value);
    const ms = n < 100_000_000_000 ? n * 1000 : n;
    return Number.isFinite(ms) && ms > 0 && ms < 8.64e15 ? ms : null;
  }
  // Accept ISO timestamps only with an explicit timezone; never assume host-local time.
  if (typeof value === "string" && /T.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms : null;
  }
  return null;
}
