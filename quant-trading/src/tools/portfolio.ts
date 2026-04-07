import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
/**
 * portfolio tool — AI-callable for portfolio management.
 * Supports both paper portfolio (local JSON) and live broker positions.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { Portfolio, PortfolioPosition, QuantTradingConfig } from "../types.js";

function getPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio-paper.json");
}

/** Legacy path — used only for migration fallback */
function getLegacyPortfolioPath(config: QuantTradingConfig): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "portfolio.json");
}

async function loadPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
  // Try mode-specific file first, then legacy
  for (const filePath of [getPortfolioPath(config), getLegacyPortfolioPath(config)]) {
    try {
      const raw = await fs.readFile(filePath, "utf-8");
      return JSON.parse(raw) as Portfolio;
    } catch {
      continue;
    }
  }
  return {
    id: "default",
    name: "默认组合",
    cash: 100000,
    totalValue: 100000,
    positions: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

async function savePortfolio(portfolio: Portfolio, config: QuantTradingConfig): Promise<void> {
  const filePath = getPortfolioPath(config);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  portfolio.updatedAt = new Date().toISOString();
  await fs.writeFile(filePath, JSON.stringify(portfolio, null, 2));
}

export function createPortfolioTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
) {
  return {
    name: "portfolio",
    description:
      "管理投资组合（模拟 & 实盘）。" +
      "action=show 查看持仓, action=buy 买入, action=sell 卖出, " +
      "action=add_cash 追加资金, action=summary 组合摘要, " +
      "action=diagnose 逐只持仓诊断（技术面+资金面+健康度评分+操作建议+止损止盈位）, " +
      "action=live_sync 同步实盘持仓, action=live_orders 查看实盘订单, " +
      "action=live_trades 查看成交记录, action=daily_pnl 当日盈亏。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "show",
            "buy",
            "sell",
            "add_cash",
            "summary",
            "diagnose",
            "live_sync",
            "live_orders",
            "live_trades",
            "cancel_order",
            "daily_pnl",
          ],
          description: "操作类型",
        },
        symbol: { type: "string", description: "标的代码" },
        name: { type: "string", description: "标的名称(可选)" },
        quantity: { type: "number", description: "数量（股/手/份）" },
        price: { type: "number", description: "价格" },
        amount: { type: "number", description: "金额（用于 add_cash）" },
        order_id: { type: "string", description: "订单ID（用于 cancel_order）" },
        status_filter: {
          type: "string",
          description: "订单状态过滤 (submitted|filled|cancelled|rejected)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "show":
        case "view": {
          // Prefer live broker data when engine is running
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const account = engine.getAccount();
            const positions = engine.getPositions();
            if (account) {
              return {
                name: account.brokerType === "paper" ? "模拟组合" : "实盘组合",
                source: account.brokerType,
                cash: `¥${account.cash.toFixed(2)}`,
                totalValue: `¥${account.totalAssets.toFixed(2)}`,
                marketValue: `¥${account.marketValue.toFixed(2)}`,
                profit: `¥${account.profit.toFixed(2)}`,
                profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                positions: positions.map((p) => ({
                  symbol: p.symbol,
                  name: p.name,
                  quantity: p.quantity,
                  availableQty: p.availableQty,
                  avgCost: `¥${p.avgCost.toFixed(3)}`,
                  currentPrice: p.currentPrice != null ? `¥${p.currentPrice.toFixed(3)}` : "N/A",
                  marketValue: p.marketValue != null ? `¥${p.marketValue.toFixed(2)}` : "N/A",
                  pnl: p.unrealizedPnl != null ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
                  pnlPct:
                    p.unrealizedPnlPct != null
                      ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%`
                      : "N/A",
                })),
              };
            }
          }
          // Fallback to paper portfolio file
          const portfolio = await loadPortfolio(config);
          return {
            name: portfolio.name,
            source: "paper",
            cash: `¥${portfolio.cash.toFixed(2)}`,
            totalValue: `¥${portfolio.totalValue.toFixed(2)}`,
            positions: portfolio.positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              avgCost: `¥${p.avgCost.toFixed(2)}`,
              marketValue: p.marketValue ? `¥${p.marketValue.toFixed(2)}` : "N/A",
              pnl: p.unrealizedPnl ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
              pnlPct: p.unrealizedPnlPct ? `${p.unrealizedPnlPct.toFixed(2)}%` : "N/A",
            })),
          };
        }

        case "buy": {
          const portfolio = await loadPortfolio(config);
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const price = args.price as number;
          if (!symbol || !quantity || !price) {
            return { error: "请提供 symbol, quantity, price" };
          }
          const cost = quantity * price;
          if (cost > portfolio.cash) {
            return {
              error: `资金不足。需要 ¥${cost.toFixed(2)}，可用 ¥${portfolio.cash.toFixed(2)}`,
            };
          }

          portfolio.cash -= cost;
          const existing = portfolio.positions.find((p) => p.symbol === symbol);
          if (existing) {
            const totalQty = existing.quantity + quantity;
            existing.avgCost = (existing.avgCost * existing.quantity + price * quantity) / totalQty;
            existing.quantity = totalQty;
          } else {
            portfolio.positions.push({
              symbol,
              name: (args.name as string) ?? symbol,
              market: "cn_stock",
              quantity,
              avgCost: price,
            });
          }
          portfolio.totalValue =
            portfolio.cash +
            portfolio.positions.reduce((s, p) => s + p.quantity * (p.currentPrice ?? p.avgCost), 0);
          await savePortfolio(portfolio, config);
          return {
            success: true,
            message: `买入 ${symbol} ${quantity}股 @ ¥${price}`,
            cash: `¥${portfolio.cash.toFixed(2)}`,
          };
        }

        case "sell": {
          const portfolio = await loadPortfolio(config);
          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          const price = args.price as number;
          if (!symbol || !quantity || !price) {
            return { error: "请提供 symbol, quantity, price" };
          }
          const pos = portfolio.positions.find((p) => p.symbol === symbol);
          if (!pos || pos.quantity < quantity) {
            return { error: `持仓不足: ${symbol} 当前持有 ${pos?.quantity ?? 0}股` };
          }

          const proceeds = quantity * price;
          portfolio.cash += proceeds;
          pos.quantity -= quantity;
          if (pos.quantity === 0) {
            portfolio.positions = portfolio.positions.filter((p) => p.symbol !== symbol);
          }
          portfolio.totalValue =
            portfolio.cash +
            portfolio.positions.reduce((s, p) => s + p.quantity * (p.currentPrice ?? p.avgCost), 0);
          await savePortfolio(portfolio, config);
          return {
            success: true,
            message: `卖出 ${symbol} ${quantity}股 @ ¥${price}`,
            cash: `¥${portfolio.cash.toFixed(2)}`,
          };
        }

        case "add_cash": {
          const portfolio = await loadPortfolio(config);
          const amount = args.amount as number;
          if (!amount || amount <= 0) return { error: "请提供正数金额" };
          portfolio.cash += amount;
          portfolio.totalValue += amount;
          await savePortfolio(portfolio, config);
          return { success: true, cash: `¥${portfolio.cash.toFixed(2)}` };
        }

        case "summary": {
          // Prefer live broker data when engine is running
          const engine = getEngine?.();
          if (engine && engine.getStatus() === "running") {
            const account = engine.getAccount();
            const positions = engine.getPositions();
            if (account) {
              return {
                name: account.brokerType === "paper" ? "模拟组合" : "实盘组合",
                source: account.brokerType,
                cash: `¥${account.cash.toFixed(2)}`,
                marketValue: `¥${account.marketValue.toFixed(2)}`,
                totalValue: `¥${account.totalAssets.toFixed(2)}`,
                profit: `¥${account.profit.toFixed(2)}`,
                profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                positionCount: positions.length,
              };
            }
          }
          const portfolio = await loadPortfolio(config);
          const totalCost = portfolio.positions.reduce((s, p) => s + p.quantity * p.avgCost, 0);
          return {
            name: portfolio.name,
            source: "paper",
            cash: `¥${portfolio.cash.toFixed(2)}`,
            stockValue: `¥${totalCost.toFixed(2)}`,
            totalValue: `¥${(portfolio.cash + totalCost).toFixed(2)}`,
            positionCount: portfolio.positions.length,
          };
        }

        case "live_sync": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动，请先执行 start_engine" };
          // Auto-start engine if it's not running yet
          const status = engine.getStatus();
          if (status === "stopped" || status === "error") {
            try {
              await engine.start();
            } catch (err) {
              return {
                error: `实盘引擎启动失败: ${(err as Error).message}。请检查 tradingAccount 配置（broker/mode/qmt 等）`,
              };
            }
          }
          // Force a fresh broker sync instead of reading possibly-stale cache
          const { account, positions } = await engine.syncNow();
          if (!account) {
            return {
              error:
                '未能获取实盘账户数据。请检查：1) tradingAccount.broker 是否设为 "qmt"；' +
                '2) tradingAccount.mode 是否设为 "live"；3) miniQMT 是否正在运行。' +
                `当前引擎状态: ${engine.getStatus()}`,
            };
          }
          return {
            account: account
              ? {
                  cash: `¥${account.cash.toFixed(2)}`,
                  frozenCash: `¥${account.frozenCash.toFixed(2)}`,
                  totalAssets: `¥${account.totalAssets.toFixed(2)}`,
                  marketValue: `¥${account.marketValue.toFixed(2)}`,
                  profit: `¥${account.profit.toFixed(2)}`,
                  profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                }
              : null,
            positions: positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              availableQty: p.availableQty,
              avgCost: `¥${p.avgCost.toFixed(3)}`,
              currentPrice: p.currentPrice != null ? `¥${p.currentPrice.toFixed(3)}` : "N/A",
              marketValue: p.marketValue != null ? `¥${p.marketValue.toFixed(2)}` : "N/A",
              unrealizedPnl: p.unrealizedPnl != null ? `¥${p.unrealizedPnl.toFixed(2)}` : "N/A",
              unrealizedPnlPct:
                p.unrealizedPnlPct != null ? `${(p.unrealizedPnlPct * 100).toFixed(2)}%` : "N/A",
            })),
          };
        }

        case "live_orders": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const statusFilter = args.status_filter as string | undefined;
          const symbol = args.symbol as string | undefined;
          const orders = await engine.orderManager.getOrders({
            ...(symbol && { symbol }),
          });
          const filtered = statusFilter ? orders.filter((o) => o.status === statusFilter) : orders;
          return {
            count: filtered.length,
            orders: filtered.slice(0, 50).map((o) => ({
              orderId: o.orderId,
              symbol: o.symbol,
              side: o.side,
              type: o.orderType,
              qty: o.quantity,
              filledQty: o.filledQty,
              price: o.price ?? "市价",
              avgFillPrice: o.avgFillPrice ?? "-",
              status: o.status,
              createdAt: o.createdAt,
            })),
          };
        }

        case "live_trades": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const sym = args.symbol as string | undefined;
          const trades = await engine.orderManager.getTrades(sym);
          return {
            count: trades.length,
            trades: trades.slice(0, 50).map((t) => ({
              symbol: t.symbol,
              entryPrice: `¥${t.entryPrice.toFixed(3)}`,
              exitPrice: t.exitPrice != null ? `¥${t.exitPrice.toFixed(3)}` : "持仓中",
              quantity: t.quantity,
              netPnl: `¥${t.netPnl.toFixed(2)}`,
              exitTime: t.exitTime ?? "-",
            })),
          };
        }

        case "cancel_order": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const orderId = args.order_id as string | undefined;
          if (!orderId) return { error: "请提供 order_id" };
          try {
            await engine.orderManager.cancel(orderId);
            return { success: true, message: `订单 ${orderId} 已请求撤单` };
          } catch (e) {
            return { error: (e as Error).message };
          }
        }

        case "daily_pnl": {
          const engine = getEngine?.();
          if (!engine) return { error: "实盘引擎未启动" };
          if (!engine.orderManager) return { error: "订单管理器未就绪" };
          const netPnl = await engine.orderManager.getDailyPnl();
          return {
            date: new Date().toISOString().slice(0, 10),
            netPnl: `¥${netPnl.toFixed(2)}`,
          };
        }

        case "diagnose": {
          return await diagnosePositions(config, getEngine, args.symbol as string | undefined);
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Diagnose helpers ──────────────────────────────────────────────

interface PositionDiagnosis {
  symbol: string;
  name: string;
  quantity: number;
  avgCost: number;
  currentPrice: number | null;
  pnlPct: number | null;
  healthScore: number;
  healthLabel: string;
  recommendation: "持有" | "加仓" | "减仓" | "清仓";
  reasons: string[];
  stopLoss: number | null;
  takeProfit: number | null;
  technicals: {
    rsi14: number | null;
    maAlignment: string;
    priceVsMa20: string;
    atr14: number | null;
    volatility: number | null;
  };
  fundFlow: {
    mainNetInflow: string | null;
    trend: string | null;
  };
}

type PositionLike = {
  symbol: string;
  name?: string;
  quantity: number;
  avgCost: number;
  currentPrice?: number | null;
};

async function diagnosePositions(
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  filterSymbol?: string,
): Promise<Record<string, unknown>> {
  // 1. Collect positions from live engine or paper portfolio
  let positions: PositionLike[] = [];
  let source = "paper";

  const engine = getEngine?.();
  if (engine && engine.getStatus() === "running") {
    const enginePositions = engine.getPositions();
    if (enginePositions.length > 0) {
      positions = enginePositions.map((p) => ({
        symbol: p.symbol,
        name: p.name,
        quantity: p.quantity,
        avgCost: p.avgCost,
        currentPrice: p.currentPrice,
      }));
      source = "live";
    }
  }

  if (positions.length === 0) {
    const portfolio = await loadPortfolio(config);
    positions = portfolio.positions.map((p) => ({
      symbol: p.symbol,
      name: p.name,
      quantity: p.quantity,
      avgCost: p.avgCost,
      currentPrice: p.currentPrice,
    }));
  }

  if (filterSymbol) {
    positions = positions.filter((p) => p.symbol === filterSymbol);
  }

  if (positions.length === 0) {
    return { error: filterSymbol ? `未找到 ${filterSymbol} 的持仓` : "当前无持仓" };
  }

  // 2. Diagnose each position concurrently
  const provider = createDataProvider(config);
  const diagnoses = await Promise.all(
    positions.map((pos) => diagnoseSingle(pos, provider, config)),
  );

  // 3. Portfolio-level risk summary
  const avgHealth =
    diagnoses.reduce((sum, d) => sum + d.healthScore, 0) / diagnoses.length;
  const clearCount = diagnoses.filter((d) => d.recommendation === "清仓").length;
  const reduceCount = diagnoses.filter((d) => d.recommendation === "减仓").length;
  const addCount = diagnoses.filter((d) => d.recommendation === "加仓").length;
  const holdCount = diagnoses.filter((d) => d.recommendation === "持有").length;

  return {
    source,
    diagnosedAt: new Date().toISOString(),
    positionCount: diagnoses.length,
    portfolioHealthScore: Math.round(avgHealth),
    portfolioHealthLabel: getHealthLabel(avgHealth),
    summary: {
      加仓: addCount,
      持有: holdCount,
      减仓: reduceCount,
      清仓: clearCount,
    },
    positions: diagnoses,
  };
}

async function diagnoseSingle(
  pos: PositionLike,
  provider: ReturnType<typeof createDataProvider>,
  config: QuantTradingConfig,
): Promise<PositionDiagnosis> {
  const symbol = pos.symbol;
  const market = detectMarketFromSymbol(symbol);

  // Fetch klines with indicators and fund flow concurrently
  const startDate = new Date();
  startDate.setMonth(startDate.getMonth() - 18);
  const startStr = startDate.toISOString().slice(0, 10);
  const endStr = new Date().toISOString().slice(0, 10);

  const [bars, fundFlowResult, quoteResult] = await Promise.all([
    provider
      .getKlines(symbol, "1d" as never, startStr, endStr, market as never)
      .catch(() => [] as Array<{ timestamp: number; open: number; high: number; low: number; close: number; volume: number }>),
    runPythonScript<{
      flows?: Array<Record<string, unknown>>;
      error?: string;
    }>("engines/fund_flow.py", { action: "stock", symbol: symbol.replace(/\.(SH|SS|SZ)$/i, ""), limit: 5 }, config).catch(
      () => ({ ok: false as const, data: null, error: "unavailable" }),
    ),
    provider.getQuote(symbol, market as never).catch(() => null),
  ]);

  const currentPrice = quoteResult?.price ?? pos.currentPrice ?? null;
  const pnlPct =
    currentPrice != null && pos.avgCost > 0
      ? ((currentPrice - pos.avgCost) / pos.avgCost) * 100
      : null;

  // Compute technical indicators from bars
  const indicators = computeBasicIndicators(bars);

  // Parse fund flow
  const fundFlowData = parseFundFlow(fundFlowResult);

  // Calculate health score (0-100)
  const healthScore = calculateHealthScore(indicators, fundFlowData, pnlPct);
  const recommendation = deriveRecommendation(healthScore, indicators, pnlPct);
  const reasons = buildReasons(indicators, fundFlowData, pnlPct, recommendation);

  // Stop-loss / take-profit based on ATR
  const stopLoss = computeStopLoss(currentPrice, indicators.atr14, pos.avgCost);
  const takeProfit = computeTakeProfit(currentPrice, indicators.atr14, pos.avgCost);

  return {
    symbol,
    name: pos.name ?? symbol,
    quantity: pos.quantity,
    avgCost: round2(pos.avgCost),
    currentPrice: currentPrice != null ? round2(currentPrice) : null,
    pnlPct: pnlPct != null ? round2(pnlPct) : null,
    healthScore: Math.round(healthScore),
    healthLabel: getHealthLabel(healthScore),
    recommendation,
    reasons,
    stopLoss: stopLoss != null ? round2(stopLoss) : null,
    takeProfit: takeProfit != null ? round2(takeProfit) : null,
    technicals: {
      rsi14: indicators.rsi14 != null ? round2(indicators.rsi14) : null,
      maAlignment: indicators.maAlignment,
      priceVsMa20: indicators.priceVsMa20,
      atr14: indicators.atr14 != null ? round2(indicators.atr14) : null,
      volatility: indicators.volatility != null ? round2(indicators.volatility) : null,
    },
    fundFlow: {
      mainNetInflow: fundFlowData.mainNetInflow,
      trend: fundFlowData.trend,
    },
  };
}

// ── Indicator computation ──────────────────────────────────────

interface BasicIndicators {
  rsi14: number | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
  atr14: number | null;
  volatility: number | null;
  lastClose: number | null;
  maAlignment: string;
  priceVsMa20: string;
}

function computeBasicIndicators(
  bars: Array<{ timestamp: number; open: number; high: number; low: number; close: number; volume: number }>,
): BasicIndicators {
  const closes = bars.map((b) => b.close);
  const n = closes.length;

  const sma = (period: number): number | null => {
    if (n < period) return null;
    return closes.slice(-period).reduce((a, b) => a + b, 0) / period;
  };

  // RSI14
  let rsi14: number | null = null;
  if (n >= 15) {
    const recent = closes.slice(-15);
    let gains = 0;
    let losses = 0;
    for (let i = 1; i < recent.length; i++) {
      const d = recent[i] - recent[i - 1];
      if (d > 0) gains += d;
      else losses -= d;
    }
    const avgGain = gains / 14;
    const avgLoss = losses / 14;
    rsi14 = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }

  // ATR14
  let atr14: number | null = null;
  if (n >= 15) {
    let trSum = 0;
    for (let i = n - 14; i < n; i++) {
      const h = bars[i].high;
      const l = bars[i].low;
      const pc = bars[i - 1].close;
      trSum += Math.max(h - l, Math.abs(h - pc), Math.abs(pc - l));
    }
    atr14 = trSum / 14;
  }

  // Volatility (annualized)
  let volatility: number | null = null;
  if (n >= 20) {
    const logReturns: number[] = [];
    for (let i = 1; i < n; i++) {
      if (closes[i] > 0 && closes[i - 1] > 0) {
        logReturns.push(Math.log(closes[i] / closes[i - 1]));
      }
    }
    if (logReturns.length >= 2) {
      const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
      const variance = logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / logReturns.length;
      volatility = Math.sqrt(variance) * Math.sqrt(252) * 100; // annualized %
    }
  }

  const ma5 = sma(5);
  const ma10 = sma(10);
  const ma20 = sma(20);
  const ma60 = sma(60);
  const lastClose = n > 0 ? closes[n - 1] : null;

  // MA alignment: bullish (5>10>20>60), bearish (5<10<20<60), mixed
  let maAlignment = "数据不足";
  if (ma5 != null && ma10 != null && ma20 != null && ma60 != null) {
    if (ma5 > ma10 && ma10 > ma20 && ma20 > ma60) {
      maAlignment = "多头排列";
    } else if (ma5 < ma10 && ma10 < ma20 && ma20 < ma60) {
      maAlignment = "空头排列";
    } else {
      maAlignment = "均线交织";
    }
  }

  // Price vs MA20
  let priceVsMa20 = "数据不足";
  if (lastClose != null && ma20 != null) {
    const diff = ((lastClose - ma20) / ma20) * 100;
    if (diff > 5) priceVsMa20 = `高于MA20 ${round2(diff)}%`;
    else if (diff > 0) priceVsMa20 = `略高于MA20 ${round2(diff)}%`;
    else if (diff > -5) priceVsMa20 = `略低于MA20 ${round2(diff)}%`;
    else priceVsMa20 = `低于MA20 ${round2(diff)}%`;
  }

  return { rsi14, ma5, ma10, ma20, ma60, atr14, volatility, lastClose, maAlignment, priceVsMa20 };
}

// ── Fund flow parsing ──────────────────────────────────────────

interface FundFlowSummary {
  mainNetInflow: string | null;
  trend: string | null;
  recentDaysPositive: number;
  recentDaysTotal: number;
}

function parseFundFlow(result: {
  ok: boolean;
  data?: { flows?: Array<Record<string, unknown>>; error?: string } | null;
  error?: string;
}): FundFlowSummary {
  if (!result.ok || !result.data?.flows || result.data.flows.length === 0) {
    return { mainNetInflow: null, trend: null, recentDaysPositive: 0, recentDaysTotal: 0 };
  }

  const flows = result.data.flows;
  const latest = flows[0];
  const mainNet = latest?.["主力净流入"] ?? latest?.["main_net_inflow"] ?? null;

  let positiveDays = 0;
  for (const f of flows) {
    const val = (f["主力净流入"] ?? f["main_net_inflow"] ?? 0) as number;
    if (val > 0) positiveDays++;
  }

  let trend: string | null = null;
  if (flows.length >= 3) {
    if (positiveDays >= flows.length - 1) trend = "持续流入";
    else if (positiveDays <= 1) trend = "持续流出";
    else trend = "资金分歧";
  }

  return {
    mainNetInflow: mainNet != null ? formatLargeNumber(mainNet as number) : null,
    trend,
    recentDaysPositive: positiveDays,
    recentDaysTotal: flows.length,
  };
}

// ── Health scoring ──────────────────────────────────────────────

function calculateHealthScore(
  indicators: BasicIndicators,
  fundFlow: FundFlowSummary,
  pnlPct: number | null,
): number {
  let score = 50; // neutral starting point

  // Technical dimension (max ±25)
  if (indicators.rsi14 != null) {
    if (indicators.rsi14 >= 30 && indicators.rsi14 <= 70) score += 10; // healthy RSI
    else if (indicators.rsi14 > 70) score -= 5; // overbought
    else if (indicators.rsi14 < 30) score -= 10; // oversold (risk but may bounce)
  }

  if (indicators.maAlignment === "多头排列") score += 15;
  else if (indicators.maAlignment === "空头排列") score -= 15;

  // Fund flow dimension (max ±15)
  if (fundFlow.trend === "持续流入") score += 15;
  else if (fundFlow.trend === "持续流出") score -= 15;
  else if (fundFlow.trend === "资金分歧") score -= 5;

  // P&L dimension (max ±10)
  if (pnlPct != null) {
    if (pnlPct > 20) score += 5; // winning position
    else if (pnlPct > 0) score += 10; // moderately positive
    else if (pnlPct > -10) score -= 5; // small loss
    else if (pnlPct > -20) score -= 10; // significant loss
    else score -= 15; // deep loss
  }

  return Math.max(0, Math.min(100, score));
}

function getHealthLabel(score: number): string {
  if (score >= 80) return "优秀";
  if (score >= 60) return "良好";
  if (score >= 40) return "一般";
  if (score >= 20) return "较差";
  return "危险";
}

function deriveRecommendation(
  healthScore: number,
  indicators: BasicIndicators,
  pnlPct: number | null,
): "持有" | "加仓" | "减仓" | "清仓" {
  // Deep loss + bad technicals → clear
  if (healthScore < 20) return "清仓";
  if (healthScore < 35) {
    // Check if deeply underwater
    if (pnlPct != null && pnlPct < -20) return "清仓";
    return "减仓";
  }
  if (healthScore < 50) return "减仓";
  if (healthScore >= 75) {
    // Good health + oversold RSI or bullish alignment → add
    if (
      indicators.rsi14 != null &&
      indicators.rsi14 < 40 &&
      indicators.maAlignment === "多头排列"
    ) {
      return "加仓";
    }
    if (indicators.maAlignment === "多头排列") return "加仓";
  }
  return "持有";
}

function buildReasons(
  indicators: BasicIndicators,
  fundFlow: FundFlowSummary,
  pnlPct: number | null,
  recommendation: string,
): string[] {
  const reasons: string[] = [];

  // Technicals
  if (indicators.maAlignment !== "数据不足") {
    reasons.push(`均线: ${indicators.maAlignment}`);
  }
  if (indicators.rsi14 != null) {
    if (indicators.rsi14 > 70) reasons.push(`RSI ${round2(indicators.rsi14)} 超买区间，注意回调风险`);
    else if (indicators.rsi14 < 30) reasons.push(`RSI ${round2(indicators.rsi14)} 超卖区间，可能存在反弹机会`);
    else reasons.push(`RSI ${round2(indicators.rsi14)} 处于正常区间`);
  }
  if (indicators.priceVsMa20 !== "数据不足") {
    reasons.push(`价格位置: ${indicators.priceVsMa20}`);
  }

  // Fund flow
  if (fundFlow.trend) {
    reasons.push(`近期资金: ${fundFlow.trend}（${fundFlow.recentDaysPositive}/${fundFlow.recentDaysTotal}日净流入）`);
  }

  // P&L
  if (pnlPct != null) {
    if (pnlPct > 0) reasons.push(`浮盈 ${round2(pnlPct)}%`);
    else if (pnlPct < 0) reasons.push(`浮亏 ${round2(pnlPct)}%`);
  }

  if (recommendation === "清仓") {
    reasons.push("⚠️ 建议止损离场，控制风险");
  } else if (recommendation === "加仓") {
    reasons.push("技术面和资金面共振向好，可考虑逢低加仓");
  }

  return reasons;
}

// ── Stop-loss / Take-profit ─────────────────────────────────────

function computeStopLoss(
  currentPrice: number | null,
  atr14: number | null,
  avgCost: number,
): number | null {
  if (currentPrice == null) return null;
  // ATR-based: 2x ATR below current price, but not above avg cost for losing positions
  if (atr14 != null && atr14 > 0) {
    const atrStop = currentPrice - 2 * atr14;
    // For positions in loss, also consider a fixed 8% stop below cost
    const costStop = avgCost * 0.92;
    return Math.max(atrStop, costStop);
  }
  // Fallback: 8% below cost
  return avgCost * 0.92;
}

function computeTakeProfit(
  currentPrice: number | null,
  atr14: number | null,
  avgCost: number,
): number | null {
  if (currentPrice == null) return null;
  // ATR-based: 3x ATR above current price
  if (atr14 != null && atr14 > 0) {
    return currentPrice + 3 * atr14;
  }
  // Fallback: 20% above cost
  return avgCost * 1.2;
}

// ── Utilities ───────────────────────────────────────────────────

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function formatLargeNumber(n: number): string {
  const abs = Math.abs(n);
  const sign = n >= 0 ? "+" : "-";
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(2)}万`;
  return `${sign}${abs.toFixed(2)}`;
}
