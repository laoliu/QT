import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function collectTechMoatInputs(payload: Record<string, unknown>): {
  companyName: string;
  newsTitles: string[];
} {
  const companyName = String(payload.name ?? payload.symbol ?? "").trim();
  const rows: string[] = [];

  const pushRow = (value: unknown): void => {
    const text = String(value ?? "").trim();
    if (text) rows.push(text);
  };

  const news = Array.isArray(payload.news) ? payload.news : [];
  for (const item of news) {
    const row = item as Record<string, unknown>;
    const title = String(row.title ?? "").trim();
    const content = String(row.content ?? "").trim();
    if (title && content) pushRow(`${title} ${content}`);
    else pushRow(title || content);
  }

  const research = Array.isArray(payload.research) ? payload.research : [];
  for (const item of research) {
    const row = item as Record<string, unknown>;
    const parts = [row.title, row.rating, row.source]
      .map((value) => String(value ?? "").trim())
      .filter(Boolean);
    if (parts.length > 0) pushRow(parts.join(" "));
  }

  const seen = new Set<string>();
  const newsTitles = rows.filter((entry) => {
    if (seen.has(entry)) return false;
    seen.add(entry);
    return true;
  });

  return { companyName, newsTitles };
}

export async function loadTechMoatInputsForSymbol(
  symbol: string,
  config: QuantTradingConfig,
  market: string = "cn_stock",
): Promise<{
  companyName: string;
  newsTitles: string[];
  detailPayload: Record<string, unknown> | null;
}> {
  const detailResult = await runPythonScript<Record<string, unknown>>(
    "scripts/stock_detail.py",
    {
      action: "detail",
      symbol,
      market,
      kline_days: 120,
      kline_interval: "1d",
    },
    config,
  );

  if (!detailResult.ok || !detailResult.data || typeof detailResult.data !== "object") {
    return { companyName: "", newsTitles: [], detailPayload: null };
  }

  const detailPayload = detailResult.data as Record<string, unknown>;
  const inputs = collectTechMoatInputs(detailPayload);
  return {
    companyName: inputs.companyName,
    newsTitles: inputs.newsTitles,
    detailPayload,
  };
}

export async function analyzeTechMoat(
  symbol: string,
  companyName: string,
  newsTitles: string[],
  config: QuantTradingConfig,
): Promise<Record<string, unknown> | null> {
  if (newsTitles.length === 0) return null;
  const moatResult = await runPythonScript<Record<string, unknown>>(
    "engines/tech_moat_engine.py",
    {
      action: "analyze",
      symbol,
      company_name: companyName,
      news_titles: newsTitles.slice(0, 24),
    },
    config,
  );
  if (!moatResult.ok || !moatResult.data || typeof moatResult.data !== "object") {
    return null;
  }
  return moatResult.data as Record<string, unknown>;
}
