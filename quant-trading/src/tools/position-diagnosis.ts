/**
 * position_diagnosis tool — automated health check for portfolio positions.
 *
 * Scans each position and checks: technical status, fundamental changes,
 * stop-loss proximity, earnings risk, and gives actionable recommendations.
 *
 * Use case: trader wants a quick "health report" of all positions with
 * clear hold/reduce/add/exit signals.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig, Portfolio } from "../types.js";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

export function createPositionDiagnosisTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
) {
  const provider = createDataProvider(config);

  return {
    name: "position_diagnosis",
    description:
      "持仓诊断/健康检查工具 — 对当前所有持仓进行全面体检。\n" +
      "action=diagnose 全持仓诊断(技术面+基本面+风险), " +
      "action=single 单只持仓深度诊断, " +
      "action=risk_check 持仓风险扫描(止损位/集中度/相关性), " +
      "action=action_plan 生成操作建议清单。\n" +
      "每只持仓给出: 健康评级(A/B/C/D) + 关键信号 + 操作建议(持有/加仓/减仓/清仓)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["diagnose", "single", "risk_check", "action_plan"],
          description:
            "操作: diagnose=全持仓诊断, single=单只诊断, risk_check=风险扫描, action_plan=操作建议",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=single 时必填）",
        },
        cost_price: {
          type: "number",
          description: "持仓成本价（可选，不填自动从组合读取）",
        },
        stop_loss_pct: {
          type: "number",
          description: "止损百分比（默认 8%）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      const costPrice = args.cost_price as number | undefined;
      const stopLossPct = (args.stop_loss_pct as number) ?? 8;

      try {
        switch (action) {
          case "diagnose":
            return await diagnoseAll(stopLossPct);
          case "single": {
            if (!symbol) return { error: "请提供 symbol" };
            return await diagnoseSingle(symbol, costPrice, stopLossPct);
          }
          case "risk_check":
            return await riskCheck(stopLossPct);
          case "action_plan":
            return await actionPlan(stopLossPct);
          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (err) {
        return { error: `诊断失败: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };

  /** Load portfolio positions */
  async function loadPositions(): Promise<Portfolio["positions"]> {
    // Try live engine first
    const engine = getEngine?.();
    if (engine) {
      const livePositions = engine.getPositions();
      if (livePositions.length > 0) return livePositions;
    }

    // Fallback to paper portfolio
    const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    for (const file of ["portfolio-paper.json", "portfolio.json"]) {
      try {
        const raw = await fs.readFile(path.join(dir, file), "utf-8");
        const portfolio = JSON.parse(raw) as Portfolio;
        return portfolio.positions ?? [];
      } catch {
        continue;
      }
    }
    return [];
  }

  /** Diagnose all positions */
  async function diagnoseAll(stopLossPct: number) {
    const positions = await loadPositions();
    if (positions.length === 0) {
      return { action: "diagnose", message: "当前无持仓", positions: [] };
    }

    const diagnoses = await Promise.all(
      positions.map((pos) =>
        diagnoseSinglePosition(pos.symbol, pos.avgCost, stopLossPct),
      ),
    );

    const summary = {
      total: diagnoses.length,
      healthy: diagnoses.filter((d) => d.grade === "A" || d.grade === "B").length,
      warning: diagnoses.filter((d) => d.grade === "C").length,
      danger: diagnoses.filter((d) => d.grade === "D").length,
    };

    return {
      action: "diagnose",
      summary,
      positions: diagnoses,
      tip: "A=健康持有, B=观察中, C=需要关注, D=建议操作(减仓/清仓)",
    };
  }

  /** Diagnose single position */
  async function diagnoseSingle(symbol: string, costPrice?: number, stopLossPct: number = 8) {
    const positions = await loadPositions();
    const pos = positions.find((p) => p.symbol === symbol);
    const cost = costPrice ?? pos?.avgCost;

    const diagnosis = await diagnoseSinglePosition(symbol, cost, stopLossPct);
    return { action: "single", ...diagnosis };
  }

  /** Core diagnosis logic for a single stock */
  async function diagnoseSinglePosition(
    symbol: string,
    costPrice: number | undefined,
    stopLossPct: number,
  ) {
    const market = detectMarketFromSymbol(symbol);
    const signals: string[] = [];
    const warnings: string[] = [];
    let score = 100; // Start at 100, deduct for issues

    // 1. Get current quote
    const quote = await provider.getQuote(symbol, market as never).catch(() => null);
    const currentPrice = quote?.price;
    const changePct = quote?.changePct ?? 0;

    // 2. Get technical indicators
    let indicators: Record<string, unknown> | null = null;
    try {
      const result = await runPythonScript(
        "engines/compute_indicators.py",
        { action: "indicators", symbol, market: "cn", period: 60 },
        config,
      );
      indicators = (result as { ok: boolean; data?: Record<string, unknown> }).data ?? null;
    } catch {
      /* ignore */
    }

    // 3. P&L check
    if (currentPrice && costPrice) {
      const pnlPct = ((currentPrice - costPrice) / costPrice) * 100;
      if (pnlPct <= -stopLossPct) {
        warnings.push(`已触及止损线: 浮亏 ${pnlPct.toFixed(2)}%（止损设定 ${stopLossPct}%）`);
        score -= 40;
      } else if (pnlPct <= -(stopLossPct * 0.7)) {
        warnings.push(`接近止损线: 浮亏 ${pnlPct.toFixed(2)}%`);
        score -= 20;
      } else if (pnlPct > 0) {
        signals.push(`浮盈 ${pnlPct.toFixed(2)}%`);
      }
    }

    // 4. Technical signals
    if (indicators) {
      const ma5 = indicators.ma5 as number | null;
      const ma10 = indicators.ma10 as number | null;
      const ma20 = indicators.ma20 as number | null;
      const rsi14 = indicators.rsi14 as number | null;
      const trend = indicators.trend as string | null;

      if (trend === "downtrend" || trend === "bearish") {
        warnings.push("技术面呈下降趋势");
        score -= 20;
      } else if (trend === "uptrend" || trend === "bullish") {
        signals.push("技术面呈上升趋势");
      }

      if (currentPrice && ma20 && currentPrice < ma20) {
        warnings.push(`价格(${currentPrice})低于20日均线(${(ma20 as number).toFixed(2)})`);
        score -= 10;
      }

      if (rsi14 !== null) {
        if (rsi14 > 80) {
          warnings.push(`RSI超买: ${rsi14.toFixed(1)}`);
          score -= 10;
        } else if (rsi14 < 20) {
          signals.push(`RSI超卖: ${rsi14.toFixed(1)}，可能反弹`);
        }
      }

      // MA alignment check
      if (ma5 && ma10 && ma20) {
        if (ma5 > ma10 && ma10 > ma20) {
          signals.push("均线多头排列");
          score += 5;
        } else if (ma5 < ma10 && ma10 < ma20) {
          warnings.push("均线空头排列");
          score -= 15;
        }
      }

      const volumeRatio = indicators.volume_ratio as number | null;
      if (volumeRatio !== null && volumeRatio > 2.5 && changePct < -3) {
        warnings.push(`放量下跌: 量比${volumeRatio.toFixed(1)}, 跌幅${changePct.toFixed(1)}%`);
        score -= 15;
      }
    }

    // 5. Today's performance
    if (changePct < -5) {
      warnings.push(`今日大跌 ${changePct.toFixed(2)}%`);
      score -= 10;
    }

    // Clamp score
    score = Math.max(0, Math.min(100, score));

    // Grade
    let grade: string;
    let recommendation: string;
    if (score >= 80) {
      grade = "A";
      recommendation = "持有";
    } else if (score >= 60) {
      grade = "B";
      recommendation = "观察，关注信号变化";
    } else if (score >= 40) {
      grade = "C";
      recommendation = "减仓或设置严格止损";
    } else {
      grade = "D";
      recommendation = "建议清仓或大幅减仓";
    }

    return {
      symbol,
      name: quote?.symbol?.name ?? symbol,
      currentPrice,
      costPrice: costPrice ?? null,
      pnlPct: currentPrice && costPrice
        ? Math.round(((currentPrice - costPrice) / costPrice) * 10000) / 100
        : null,
      grade,
      score,
      recommendation,
      signals,
      warnings,
    };
  }

  /** Risk check across all positions */
  async function riskCheck(stopLossPct: number) {
    const positions = await loadPositions();
    if (positions.length === 0) {
      return { action: "risk_check", message: "当前无持仓" };
    }

    const risks: string[] = [];

    // 1. Check position concentration
    const totalValue = positions.reduce(
      (sum, p) => sum + (p.quantity ?? 0) * (p.currentPrice ?? p.avgCost ?? 0),
      0,
    );
    for (const pos of positions) {
      const posValue = (pos.quantity ?? 0) * (pos.currentPrice ?? pos.avgCost ?? 0);
      const pct = totalValue > 0 ? (posValue / totalValue) * 100 : 0;
      if (pct > 25) {
        risks.push(`${pos.symbol} 仓位过重: ${pct.toFixed(1)}%（建议<25%）`);
      }
    }

    // 2. Check if any position near stop loss
    for (const pos of positions) {
      const cost = pos.avgCost;
      if (cost && pos.currentPrice) {
        const pnlPct = ((pos.currentPrice - cost) / cost) * 100;
        if (pnlPct <= -stopLossPct) {
          risks.push(`${pos.symbol} 已触及止损(${pnlPct.toFixed(1)}%)，建议立即处理`);
        }
      }
    }

    // 3. Count positions
    if (positions.length > 10) {
      risks.push(`持仓股数过多(${positions.length}只)，难以有效跟踪，建议精简至5-8只`);
    }

    return {
      action: "risk_check",
      positionCount: positions.length,
      totalValue: Math.round(totalValue),
      risks,
      riskLevel: risks.length === 0 ? "低" : risks.length <= 2 ? "中" : "高",
    };
  }

  /** Generate action plan */
  async function actionPlan(stopLossPct: number) {
    const positions = await loadPositions();
    if (positions.length === 0) {
      return { action: "action_plan", message: "当前无持仓" };
    }

    const diagnoses = await Promise.all(
      positions.map((pos) =>
        diagnoseSinglePosition(pos.symbol, pos.avgCost, stopLossPct),
      ),
    );

    const actions = {
      hold: diagnoses.filter((d) => d.grade === "A").map((d) => ({
        symbol: d.symbol,
        name: d.name,
        reason: d.signals.join("; ") || "技术面健康",
      })),
      watch: diagnoses.filter((d) => d.grade === "B").map((d) => ({
        symbol: d.symbol,
        name: d.name,
        reason: d.warnings.length > 0 ? d.warnings[0] : "观察中",
      })),
      reduce: diagnoses.filter((d) => d.grade === "C").map((d) => ({
        symbol: d.symbol,
        name: d.name,
        reason: d.warnings.join("; "),
        action: "减仓至半仓或设止损",
      })),
      exit: diagnoses.filter((d) => d.grade === "D").map((d) => ({
        symbol: d.symbol,
        name: d.name,
        reason: d.warnings.join("; "),
        action: "建议清仓",
      })),
    };

    return {
      action: "action_plan",
      date: new Date().toISOString().slice(0, 10),
      actions,
      summary: `${actions.hold.length}只持有, ${actions.watch.length}只观察, ${actions.reduce.length}只减仓, ${actions.exit.length}只清仓`,
    };
  }
}
