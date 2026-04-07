/**
 * holding_diagnosis tool — AI-callable one-click portfolio health check.
 *
 * A stock trader needs a quick "doctor's checkup" for their holdings:
 * - Which positions are healthy, which are deteriorating?
 * - Are there over-concentrated sectors or correlated risks?
 * - Which positions should be cut, held, or added to?
 *
 * Actions:
 *   diagnose       — full portfolio diagnosis (per-position + overall)
 *   position_check — diagnose a single position in depth
 *   concentration  — sector/industry concentration analysis
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";

interface PositionDiagnosis {
  symbol: string;
  name: string;
  quantity: number;
  avgCost: number;
  currentPrice: number | null;
  pnlPct: number | null;
  pnlAmount: number | null;
  weight: number | null;
  healthScore: number; // 0-100
  signals: string[];
  suggestion: "hold" | "add" | "reduce" | "cut" | "watch";
  reasons: string[];
}

interface ConcentrationItem {
  sector: string;
  weight: number;
  symbols: string[];
  risk: "low" | "medium" | "high";
}

export function createHoldingDiagnosisTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dp = createDataProvider(config);

  function getPortfolioPath(): string {
    const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    return path.join(dir, "portfolio-paper.json");
  }

  function getLegacyPortfolioPath(): string {
    const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    return path.join(dir, "portfolio.json");
  }

  async function loadPortfolio(): Promise<Portfolio> {
    for (const filePath of [getPortfolioPath(), getLegacyPortfolioPath()]) {
      try {
        const raw = await fs.readFile(filePath, "utf-8");
        return JSON.parse(raw) as Portfolio;
      } catch {
        continue;
      }
    }
    return { id: "default", name: "默认组合", cash: 0, totalValue: 0, positions: [], createdAt: "", updatedAt: "" };
  }

  async function diagnosePosition(
    pos: { symbol: string; name?: string; quantity: number; avgCost: number },
    totalValue: number,
  ): Promise<PositionDiagnosis> {
    const market = detectMarketFromSymbol(pos.symbol);
    let currentPrice: number | null = null;
    let name = pos.name || pos.symbol;
    const signals: string[] = [];
    const reasons: string[] = [];
    let healthScore = 50; // start neutral

    // Fetch current quote
    try {
      const quote = await dp.getQuote(pos.symbol, market as never);
      if (quote) {
        currentPrice = quote.price;
        name = quote.symbol?.name || name;
        if (quote.changePct != null) {
          if (quote.changePct < -5) signals.push("今日大跌");
          if (quote.changePct > 5) signals.push("今日大涨");
        }
      }
    } catch {
      // unavailable
    }

    const pnlPct = currentPrice != null && pos.avgCost > 0
      ? ((currentPrice - pos.avgCost) / pos.avgCost) * 100
      : null;
    const pnlAmount = currentPrice != null ? (currentPrice - pos.avgCost) * pos.quantity : null;
    const positionValue = currentPrice != null ? currentPrice * pos.quantity : pos.avgCost * pos.quantity;
    const weight = totalValue > 0 ? (positionValue / totalValue) * 100 : null;

    // Scoring based on P&L
    if (pnlPct != null) {
      if (pnlPct > 20) { healthScore += 20; signals.push("大幅盈利"); }
      else if (pnlPct > 5) { healthScore += 10; signals.push("盈利中"); }
      else if (pnlPct > -5) { healthScore += 0; }
      else if (pnlPct > -15) { healthScore -= 15; signals.push("亏损中"); }
      else { healthScore -= 30; signals.push("深度亏损"); }
    }

    // Fetch fundamental health
    try {
      const result = (await runPythonScript(
        "financial_provider.py",
        { action: "fundamental", symbol: pos.symbol, market },
        config,
      )) as Record<string, unknown>;
      if (result) {
        const roe = result.roe != null ? Number(result.roe) : null;
        const pe = result.pe != null ? Number(result.pe) : null;
        if (roe != null) {
          if (roe > 15) { healthScore += 10; reasons.push("ROE优秀(>" + roe.toFixed(0) + "%)"); }
          else if (roe < 5) { healthScore -= 10; reasons.push("ROE偏低(" + roe.toFixed(0) + "%)"); }
        }
        if (pe != null && pe > 0) {
          if (pe > 80) { healthScore -= 10; reasons.push("估值偏高(PE=" + pe.toFixed(0) + ")"); }
          else if (pe < 15) { healthScore += 5; reasons.push("估值合理(PE=" + pe.toFixed(0) + ")"); }
        }
        const revGrowth = result.revenue_growth_yoy != null ? Number(result.revenue_growth_yoy) : null;
        if (revGrowth != null) {
          if (revGrowth > 20) { healthScore += 10; reasons.push("营收高增长"); }
          else if (revGrowth < -10) { healthScore -= 10; reasons.push("营收下滑"); }
        }
      }
    } catch {
      // fundamentals unavailable
    }

    // Clamp score
    healthScore = Math.max(0, Math.min(100, healthScore));

    // Determine suggestion
    let suggestion: PositionDiagnosis["suggestion"] = "hold";
    if (healthScore >= 75 && pnlPct != null && pnlPct > -5) {
      suggestion = "add";
      reasons.push("基本面良好，可适当加仓");
    } else if (healthScore < 30) {
      suggestion = "cut";
      reasons.push("多项指标不佳，建议止损");
    } else if (healthScore < 45) {
      suggestion = "reduce";
      reasons.push("基本面转弱，建议减仓");
    } else if (pnlPct != null && pnlPct < -10 && healthScore < 55) {
      suggestion = "reduce";
      reasons.push("亏损较大且基本面一般，建议减仓");
    } else if (pnlPct != null && pnlPct > 30 && healthScore < 60) {
      suggestion = "reduce";
      reasons.push("涨幅较大但基本面一般，可止盈");
    } else {
      suggestion = "hold";
      reasons.push("综合持有观察");
    }

    // Check concentration risk
    if (weight != null && weight > 25) {
      signals.push("仓位过重");
      reasons.push(`占比${weight.toFixed(1)}%超过25%上限`);
      if (suggestion === "hold" || suggestion === "add") suggestion = "reduce";
    }

    return {
      symbol: pos.symbol,
      name,
      quantity: pos.quantity,
      avgCost: pos.avgCost,
      currentPrice,
      pnlPct: pnlPct != null ? Number(pnlPct.toFixed(2)) : null,
      pnlAmount: pnlAmount != null ? Number(pnlAmount.toFixed(2)) : null,
      weight: weight != null ? Number(weight.toFixed(1)) : null,
      healthScore,
      signals,
      suggestion,
      reasons,
    };
  }

  return {
    name: "holding_diagnosis",
    description:
      "持仓诊断工具。一键检查所有持仓的健康度，给出保留/加仓/减仓/清仓建议。" +
      "action=diagnose 全持仓诊断, action=position_check 单只持仓深度诊断, " +
      "action=concentration 行业集中度分析。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["diagnose", "position_check", "concentration"],
          description: "操作类型",
        },
        symbol: {
          type: "string",
          description: "股票代码（仅 position_check 时需要）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "diagnose";

      const portfolio = await loadPortfolio();
      const positions = portfolio.positions || [];

      if (positions.length === 0) {
        return { action, message: "当前没有持仓，无法进行诊断" };
      }

      if (action === "position_check") {
        const symbol = (args.symbol as string)?.trim();
        if (!symbol) return { error: "请提供 symbol" };
        const pos = positions.find(
          (p: { symbol: string }) =>
            p.symbol === symbol || p.symbol === symbol.toUpperCase(),
        );
        if (!pos) return { error: `未找到持仓 ${symbol}` };
        const diag = await diagnosePosition(pos, portfolio.totalValue || 0);
        return { action: "position_check", diagnosis: diag };
      }

      if (action === "concentration") {
        // Fetch industry info for each position
        const sectorMap = new Map<string, { weight: number; symbols: string[] }>();
        let totalValue = 0;

        for (const pos of positions) {
          const val = pos.avgCost * pos.quantity;
          totalValue += val;
        }

        for (const pos of positions) {
          const market = detectMarketFromSymbol(pos.symbol);
          let industry = "未知";
          try {
            const result = (await runPythonScript(
              "financial_provider.py",
              { action: "fundamental", symbol: pos.symbol, market },
              config,
            )) as Record<string, unknown>;
            if (result?.industry) industry = String(result.industry);
          } catch {
            // ignore
          }
          const val = pos.avgCost * pos.quantity;
          const weight = totalValue > 0 ? (val / totalValue) * 100 : 0;
          const entry = sectorMap.get(industry) || { weight: 0, symbols: [] };
          entry.weight += weight;
          entry.symbols.push(pos.symbol);
          sectorMap.set(industry, entry);
        }

        const concentration: ConcentrationItem[] = [...sectorMap.entries()].map(
          ([sector, data]) => ({
            sector,
            weight: Number(data.weight.toFixed(1)),
            symbols: data.symbols,
            risk: data.weight > 40 ? "high" : data.weight > 25 ? "medium" : "low",
          }),
        );
        concentration.sort((a, b) => b.weight - a.weight);

        return {
          action: "concentration",
          totalPositions: positions.length,
          sectors: concentration,
          overConcentrated: concentration.filter((c) => c.risk === "high").map((c) => c.sector),
        };
      }

      // Full diagnose
      const totalValue = portfolio.totalValue || portfolio.cash || 0;
      const diagnoses = await Promise.all(
        positions.map((pos: { symbol: string; name?: string; quantity: number; avgCost: number }) =>
          diagnosePosition(pos, totalValue),
        ),
      );

      const healthy = diagnoses.filter((d) => d.healthScore >= 60);
      const warning = diagnoses.filter((d) => d.healthScore >= 40 && d.healthScore < 60);
      const critical = diagnoses.filter((d) => d.healthScore < 40);

      const totalPnl = diagnoses.reduce((s, d) => s + (d.pnlAmount ?? 0), 0);
      const avgHealth = diagnoses.length > 0
        ? Number((diagnoses.reduce((s, d) => s + d.healthScore, 0) / diagnoses.length).toFixed(0))
        : 0;

      return {
        action: "diagnose",
        overview: {
          totalPositions: diagnoses.length,
          avgHealthScore: avgHealth,
          overallPnl: Number(totalPnl.toFixed(2)),
          healthy: healthy.length,
          warning: warning.length,
          critical: critical.length,
          cashRatio: portfolio.cash != null && totalValue > 0
            ? Number(((portfolio.cash / totalValue) * 100).toFixed(1))
            : null,
        },
        positions: diagnoses.sort((a, b) => a.healthScore - b.healthScore),
        actionItems: [
          ...critical.map((d) => `⚠️ ${d.symbol} ${d.name}: ${d.suggestion} — ${d.reasons.join("; ")}`),
          ...warning.map((d) => `⚡ ${d.symbol} ${d.name}: ${d.suggestion} — ${d.reasons.join("; ")}`),
        ],
      };
    },
  };
}
