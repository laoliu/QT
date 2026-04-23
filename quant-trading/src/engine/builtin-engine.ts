import {
  createDataProvider,
  detectMarketFromSymbol,
  isKnownIndexCode,
  type MarketDataProvider,
} from "../data/market-data-provider.js";
/**
 * Builtin lightweight backtest engine (TypeScript).
 *
 * Suitable for daily-bar strategies. For intraday or complex strategies
 * (stop-trail, bracket orders, multi-timeframe), delegate to backtrader.
 *
 * Strategy implementations live in ./strategies/.
 */
import type { BacktestRequest, BacktestResult, MarketType, TradingSignal } from "../types.js";
import type { QuantTradingConfig } from "../types.js";

// ── A-share T+1 helper ────────────────────────────────────────────

/** Markets that enforce T+1 (cannot sell shares bought on the same day). */
function isTPlus1Market(market: MarketType): boolean {
  return market === "cn_stock" || market === "fund";
}

// Re-export strategies for backward compatibility
export type { BuiltinStrategy, BuiltinStrategyContext } from "./strategies/index.js";
export {
  dualMaCrossover,
  momentumBreakout,
  meanReversion,
  rsiFilter,
  rsiEnhanced,
  macdConfirmation,
  macdDivergence,
  volumeConfirmation,
  compositeStrategy,
  gatedStrategy,
  trendComboStrategy,
  scoredStrategy,
  enhancedCompositeStrategy,
  detectMarketRegime,
  rsiMacdCombined,
  ctaTrendFollowing,
  ctaDualThrust,
  spreadArbitrage,
  ctaTurtleBreakout,
  ctaMacdTrend,
  ctaRsiReversal,
  ctaBollingerBreakout,
  ctaKeltnerChannel,
  ctaRBreaker,
  ctaAberration,
  ctaTtmSqueeze,
  ctaDmiAdx,
  ctaParabolicSar,
  ctaVolatilityTarget,
  ctaMomentumRotation,
  ctaWilliamsR,
  ctaObvTrend,
  ctaIchimoku,
  ctaKdj,
  ctaMeanReversionZScore,
  ctaGridTrading,
  ctaMultiMaConvergence,
  ctaDonchianMidReversion,
  ctaPivotPoint,
  ctaChandelierExit,
  ctaRangeBreakout,
  ctaVwapReversion,
  ctaDoubleBottom,
  ctaRocMomentum,
  ctaCciDivergence,
  ctaEmaCrossover,
  ctaAtrTrailing,
  ctaStochasticRsi,
  ctaMacdHistogramReversal,
  ctaBollingerBandwidth,
  t0DayTrading,
  t0EnhancedStrategy,
  // Extended basic strategies
  trixMomentum,
  mfiOscillator,
  aroonTrend,
  cmfDivergence,
  elderRayStrategy,
  supertrendStrategy,
  hullMaCrossover,
  demaCrossover,
  tripleMaCrossover,
  maEnvelopeStrategy,
  connorsRsiStrategy,
  kamaStrategy,
  demarkerStrategy,
  // Extended CTA strategies
  ctaAwesomeOscillator,
  ctaElderForceIndex,
  ctaDemarkerCta,
  ctaUltimateOscillatorStrategy,
  ctaAdxBreakout,
  ctaTrixTrend,
  ctaHullMaTrend,
  ctaMassIndex,
  ctaChaikinOscillator,
  ctaCoppock,
  ctaRsiDivergence,
  ctaVolumeBreakout,
  ctaAdaptiveMa,
  ctaMaEnvelope,
  ctaConnorsRsi,
  ctaAroonOscillator,
  ctaMfiTrend,
  ctaDemaCrossover,
  ctaTripleMa,
  ctaSupertrendCta,
  swingOverlay,
} from "./strategies/index.js";

import type { BuiltinStrategy } from "./strategies/index.js";
import type { BuiltinStrategyContext } from "./strategies/index.js";
import { formatTimestampToDateCST } from "../utils/cst-time.js";

// ── Engine ────────────────────────────────────────────────────────

export async function runBuiltinBacktest(
  strategy: BuiltinStrategy,
  request: BacktestRequest,
  config: QuantTradingConfig,
  sharedProvider?: MarketDataProvider,
): Promise<BacktestResult> {
  if (!request.initialCash || !Number.isFinite(request.initialCash) || request.initialCash <= 0) {
    throw new Error(`initialCash 必须为正数，收到 ${request.initialCash}`);
  }

  const provider = sharedProvider ?? createDataProvider(config);
  const symbol = request.symbols[0] ?? "000300";
  const interval = request.interval ?? "1d";

  // Fetch data — use the requested interval (default daily)
  const bars = await provider.getKlines(symbol, interval, request.startDate, request.endDate);
  if (bars.length === 0) {
    throw new Error(
      `No data available for ${symbol} from ${request.startDate} to ${request.endDate}`,
    );
  }

  // ── A-share realistic fee structure ──────────────────────────────
  // Buy  cost: price×(1+slippage) × qty + max(commission×notional, minCommission) + transferFee×notional
  // Sell proceeds: price×(1-slippage) × qty - max(commission×notional, minCommission) - stampDuty×notional - transferFee×notional
  const commissionRate = request.commission ?? 0.0003; // 万3 bilateral
  const stampDutyRate = request.stampDuty ?? 0.0005; // 0.05% sell-only (post-2023-08)
  const transferFeeRate = request.transferFee ?? 0.00002; // 0.002% bilateral (SSE)
  const minCommission = request.minCommission ?? 5; // ¥5 floor per leg
  const slippageRate = request.slippage ?? 0.001; // 0.1% market impact

  /** Calculate all-in buy cost for qty shares at signal price */
  function calcBuyCost(qty: number, signalPrice: number): number {
    const execPrice = signalPrice * (1 + slippageRate); // pay a little more
    const notional = qty * execPrice;
    const comm = Math.max(notional * commissionRate, minCommission);
    const transfer = notional * transferFeeRate;
    return notional + comm + transfer;
  }

  /** Calculate all-in sell proceeds for qty shares at signal price */
  function calcSellProceeds(qty: number, signalPrice: number, skipStampDuty = false): number {
    const execPrice = signalPrice * (1 - slippageRate); // receive a little less
    const notional = qty * execPrice;
    const comm = Math.max(notional * commissionRate, minCommission);
    const stamp = skipStampDuty ? 0 : notional * stampDutyRate;
    const transfer = notional * transferFeeRate;
    return notional - comm - stamp - transfer;
  }

  let cash = request.initialCash;
  let position = 0;
  let totalFees = 0; // cumulative fees paid (CNY)
  let shortEntryNotional = 0; // tracks short entry value for P&L calc
  const signals: TradingSignal[] = [];
  const equityCurve: Array<{ date: string; value: number }> = [];
  let peakValue = cash;
  let maxDrawdown = 0;
  let maxDrawdownDuration = 0;
  let ddStart = 0;

  // T+1 tracking: bar index at which the current long position was opened
  const detectedMarket = request.market ?? detectMarketFromSymbol(symbol);
  const isIndex = isKnownIndexCode(symbol);
  // Indices are not directly tradable on A-share market — skip T+1 restriction
  const enforceT1 = !isIndex && isTPlus1Market(detectedMarket) && interval === "1d";
  let buyBarIndex = -1; // bar index of last buy

  // Engine-level stop-loss: extracted from request.parameters
  const params = request.parameters ?? {};
  const engineStopLossPct = typeof params.stopLossPct === "number" ? params.stopLossPct : 0;
  const engineTrailingStopPct =
    typeof params.trailingStopPct === "number" ? params.trailingStopPct : 0;
  let entryPrice = 0; // price at which the current position was opened
  let trailingHigh = 0; // highest price since position opened (for trailing stop)

  const ctx: BuiltinStrategyContext = {
    bars,
    index: 0,
    position: 0,
    cash,
    params: request.parameters ?? {},
    signals,
    closes: bars.map((b) => b.close),
  };

  // Init
  strategy.init?.(ctx);

  // Iterate
  for (let i = 0; i < bars.length; i++) {
    ctx.index = i;
    ctx.position = position;
    ctx.cash = cash;

    const bar = bars[i]!;

    // Engine-level stop-loss / trailing-stop checks (long positions only)
    if (position > 0 && entryPrice > 0 && !(enforceT1 && buyBarIndex === i)) {
      // Update trailing high
      if (bar.high > trailingHigh) trailingHigh = bar.high;

      let stopTriggered = false;
      let stopReason = "";

      // Fixed stop-loss: if price drops below entryPrice * (1 - stopLossPct)
      if (engineStopLossPct > 0) {
        const stopPrice = entryPrice * (1 - engineStopLossPct);
        if (bar.close <= stopPrice) {
          stopTriggered = true;
          stopReason = `[引擎止损] 价格 ¥${bar.close.toFixed(2)} 跌破止损线 ¥${stopPrice.toFixed(2)} (入场价 ¥${entryPrice.toFixed(2)} × ${(engineStopLossPct * 100).toFixed(0)}%)`;
        }
      }

      // Trailing stop: if price drops below trailingHigh * (1 - trailingStopPct)
      if (!stopTriggered && engineTrailingStopPct > 0 && trailingHigh > 0) {
        const trailPrice = trailingHigh * (1 - engineTrailingStopPct);
        if (bar.close <= trailPrice) {
          stopTriggered = true;
          stopReason = `[移动止损] 价格 ¥${bar.close.toFixed(2)} 从最高点 ¥${trailingHigh.toFixed(2)} 回撤 ${(engineTrailingStopPct * 100).toFixed(0)}%`;
        }
      }

      if (stopTriggered) {
        const stopSig: TradingSignal = {
          timestamp: bar.timestamp,
          symbol,
          direction: "sell",
          price: bar.close,
          reason: stopReason,
          strategyId: request.strategyId,
        };
        signals.push(stopSig);
        const isFutures = detectedMarket === "futures";
        const proceeds = calcSellProceeds(position, bar.close, isFutures || isIndex);
        totalFees += position * bar.close - proceeds;
        cash += proceeds;
        position = 0;
        entryPrice = 0;
        trailingHigh = 0;
      }
    }

    const result = strategy.next(ctx);

    if (result) {
      const sigArray = Array.isArray(result) ? result : [result];
      for (const sig of sigArray) {
        sig.symbol = symbol;
        signals.push(sig);

        // Execute signal
        // Futures/index mode: lot size = 1 unit; Stock mode: lot size = 100 shares
        const isFutures = detectedMarket === "futures";
        const lotSize = isFutures || isIndex ? 1 : 100;

        if (sig.direction === "buy" && position <= 0) {
          // If currently short, close the short first (realize P&L)
          if (position < 0 && !(enforceT1 && buyBarIndex === i)) {
            const absPos = Math.abs(position);
            const coverExecPrice = sig.price * (1 + slippageRate);
            const coverNotional = absPos * coverExecPrice;
            const coverComm = Math.max(coverNotional * commissionRate, minCommission);
            const coverTransfer = coverNotional * transferFeeRate;
            totalFees += coverComm + coverTransfer;
            const exitNotional = absPos * coverExecPrice;
            const pnl = shortEntryNotional - exitNotional;
            cash += pnl - coverComm - coverTransfer;
            position = 0;
            shortEntryNotional = 0;
          }
          // Now open long with available cash
          // sizePct: optional 1-100 pct of cash to deploy (used by scoredStrategy)
          const rawPct =
            typeof (sig as Record<string, unknown>).sizePct === "number"
              ? ((sig as Record<string, unknown>).sizePct as number)
              : 95;
          const deployPct = Math.min(100, Math.max(1, rawPct)) / 100;
          const execPrice = sig.price * (1 + slippageRate);
          const qty = Math.floor((cash * deployPct) / execPrice / lotSize) * lotSize;
          if (qty > 0) {
            const cost = calcBuyCost(qty, sig.price);
            totalFees += cost - qty * sig.price;
            cash -= cost;
            position += qty;
            buyBarIndex = i;
            entryPrice = sig.price;
            trailingHigh = sig.price;
          }
        } else if (sig.direction === "sell" && position > 0) {
          // T+1: skip sell if bought on the same bar (same trading day)
          if (enforceT1 && buyBarIndex === i) continue;
          const skipStamp = isFutures || isIndex;
          const proceeds = calcSellProceeds(position, sig.price, skipStamp);
          totalFees += position * sig.price - proceeds;
          cash += proceeds;
          position = 0;
          entryPrice = 0;
          trailingHigh = 0;
        } else if (sig.direction === "short" && position >= 0) {
          // Open short position (futures) — uses margin-based sizing
          const rawPct =
            typeof (sig as Record<string, unknown>).sizePct === "number"
              ? ((sig as Record<string, unknown>).sizePct as number)
              : 95;
          const deployPct = Math.min(100, Math.max(1, rawPct)) / 100;
          const execPrice = sig.price * (1 - slippageRate); // short entry gets slightly worse fill
          const qty = Math.floor((cash * deployPct) / execPrice / lotSize) * lotSize;
          if (qty > 0) {
            // Record the notional as "frozen margin" (simplified)
            const notional = qty * execPrice;
            const comm = Math.max(notional * commissionRate, minCommission);
            const transfer = notional * transferFeeRate;
            totalFees += comm + transfer;
            cash -= comm + transfer; // only deduct fees, notional is margin-frozen
            position -= qty; // negative = short
            shortEntryNotional = qty * execPrice; // track slippage-adjusted entry for P&L
          }
        } else if (sig.direction === "cover" && position < 0) {
          // Close short position — realize P&L
          const absPos = Math.abs(position);
          const execPrice = sig.price * (1 + slippageRate); // cover gets slightly worse fill
          const notional = absPos * execPrice;
          const comm = Math.max(notional * commissionRate, minCommission);
          const transfer = notional * transferFeeRate;
          totalFees += comm + transfer;
          // P&L from short: entry notional - exit notional (both slippage-adjusted)
          const exitNotional = absPos * execPrice;
          const pnl = shortEntryNotional - exitNotional;
          cash += pnl - comm - transfer;
          position = 0;
          shortEntryNotional = 0;
        }
      }
    }

    // Track equity — for short positions, unrealized P&L = entry - current
    const equity =
      position >= 0
        ? cash + position * bar.close
        : cash + (shortEntryNotional - Math.abs(position) * bar.close);
    const dateStr = formatTimestampToDateCST(bar.timestamp);
    equityCurve.push({ date: dateStr, value: equity });

    // Drawdown
    if (equity > peakValue) {
      peakValue = equity;
      ddStart = i;
    }
    const dd = (peakValue - equity) / peakValue;
    if (dd > maxDrawdown) {
      maxDrawdown = dd;
      maxDrawdownDuration = i - ddStart;
    }
  }

  const lastPrice = bars.at(-1)?.close ?? 0;
  const finalValue =
    position >= 0
      ? cash + position * lastPrice
      : cash + (shortEntryNotional - Math.abs(position) * lastPrice);
  const totalReturn = (finalValue - request.initialCash) / request.initialCash;
  const tradingDays = bars.length;
  const years = tradingDays / 252;
  const annualizedReturn = years > 0 ? (finalValue / request.initialCash) ** (1 / years) - 1 : 0;

  // ── Risk-adjusted metrics ─────────────────────────────────────

  const dailyReturns: number[] = [];
  for (let i = 1; i < equityCurve.length; i++) {
    const prevVal = equityCurve[i - 1]!.value;
    if (prevVal > 0) dailyReturns.push(equityCurve[i]!.value / prevVal - 1);
  }
  const avgReturn = dailyReturns.reduce((a, b) => a + b, 0) / (dailyReturns.length || 1);
  const stdDev = Math.sqrt(
    dailyReturns.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / (dailyReturns.length || 1),
  );
  // Sharpe (daily returns vs 0 risk-free rate, annualized)
  const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

  // Sortino (downside deviation only)
  const downsideReturns = dailyReturns.filter((r) => r < 0);
  const downsideDev = Math.sqrt(
    downsideReturns.reduce((sum, r) => sum + r ** 2, 0) / (dailyReturns.length || 1),
  );
  const sortinoRatio = downsideDev > 0 ? (avgReturn / downsideDev) * Math.sqrt(252) : 0;

  // Calmar (annualized return / max drawdown)
  const calmarRatio = maxDrawdown > 0 ? (annualizedReturn * 100) / (maxDrawdown * 100) : 0;

  // Win rate & Profit Factor — count both long and short round trips
  const buySignals = signals.filter((s) => s.direction === "buy");
  const sellSignals = signals.filter((s) => s.direction === "sell");
  const shortSignals = signals.filter((s) => s.direction === "short");
  const coverSignals = signals.filter((s) => s.direction === "cover");
  const longTrades = Math.min(buySignals.length, sellSignals.length);
  const shortTrades = Math.min(shortSignals.length, coverSignals.length);
  let wins = 0;
  let grossProfit = 0;
  let grossLoss = 0;
  for (let i = 0; i < longTrades; i++) {
    const pnl = sellSignals[i]!.price - buySignals[i]!.price;
    if (pnl > 0) {
      wins++;
      grossProfit += pnl;
    } else {
      grossLoss += Math.abs(pnl);
    }
  }
  for (let i = 0; i < shortTrades; i++) {
    const pnl = shortSignals[i]!.price - coverSignals[i]!.price;
    if (pnl > 0) {
      wins++;
      grossProfit += pnl;
    } else {
      grossLoss += Math.abs(pnl);
    }
  }
  const trades = longTrades + shortTrades;
  const winRate = trades > 0 ? wins / trades : 0;
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  // ── Drawdown curve ─────────────────────────────────────────────
  const drawdownCurve: Array<{ date: string; drawdown: number }> = [];
  let ddPeak = equityCurve[0]?.value ?? request.initialCash;
  for (const pt of equityCurve) {
    if (pt.value > ddPeak) ddPeak = pt.value;
    drawdownCurve.push({
      date: pt.date,
      drawdown: ddPeak > 0 ? ((ddPeak - pt.value) / ddPeak) * 100 : 0,
    });
  }

  // ── Monthly returns ────────────────────────────────────────────
  const monthlyMap = new Map<string, { startValue: number; endValue: number }>();
  for (const pt of equityCurve) {
    const month = pt.date.slice(0, 7); // "YYYY-MM"
    const entry = monthlyMap.get(month);
    if (!entry) {
      monthlyMap.set(month, { startValue: pt.value, endValue: pt.value });
    } else {
      entry.endValue = pt.value;
    }
  }
  const monthlyReturns = [...monthlyMap.entries()].map(([month, { startValue, endValue }]) => ({
    month,
    return: startValue > 0 ? ((endValue - startValue) / startValue) * 100 : 0,
  }));

  // ── Benchmark curve (if requested) ─────────────────────────────
  let benchmarkCurve: Array<{ date: string; value: number }> | undefined;
  if (request.benchmark) {
    try {
      const benchBars = await provider.getKlines(
        request.benchmark,
        interval,
        request.startDate,
        request.endDate,
      );
      if (benchBars.length > 0) {
        const benchStart = benchBars[0]!.close;
        benchmarkCurve = benchBars.map((b) => ({
          date: formatTimestampToDateCST(b.timestamp),
          value: (b.close / benchStart) * request.initialCash,
        }));
      }
    } catch {
      // Non-critical: benchmark fetch failure should not break the backtest
    }
  }

  return {
    strategyId: request.strategyId,
    symbols: request.symbols,
    startDate: request.startDate,
    endDate: request.endDate,
    initialCash: request.initialCash,
    finalValue,
    totalReturn: totalReturn * 100,
    annualizedReturn: annualizedReturn * 100,
    maxDrawdown: maxDrawdown * 100,
    maxDrawdownDuration,
    sharpeRatio,
    sortinoRatio,
    calmarRatio,
    winRate: winRate * 100,
    profitFactor: profitFactor === Infinity ? undefined : profitFactor,
    totalTrades: trades,
    totalFees: Math.round(totalFees * 100) / 100,
    feeImpact: (totalFees / request.initialCash) * 100,
    signals,
    equityCurve,
    benchmarkCurve,
    drawdownCurve,
    monthlyReturns,
  };
}

/**
 * Portfolio backtest — runs the same strategy on multiple symbols with
 * equal-weight capital allocation, then merges into a single portfolio result.
 *
 * Each symbol starts with initialCash / symbols.length.
 * The portfolio equity curve is the element-wise sum of all per-symbol curves.
 */
export async function runPortfolioBacktest(
  strategy: BuiltinStrategy,
  request: BacktestRequest,
  config: QuantTradingConfig,
  sharedProvider?: MarketDataProvider,
): Promise<
  BacktestResult & {
    perSymbol: Array<{ symbol: string; result: BacktestResult }>;
    failedSymbols?: Array<{ symbol: string; error: string }>;
  }
> {
  const symbols = request.symbols.length > 0 ? request.symbols : ["000300"];
  // Share one provider across all symbols to reuse the in-memory kline cache
  const provider = sharedProvider ?? createDataProvider(config);
  const perSymbolCash = request.initialCash / symbols.length;

  // Run each symbol in parallel — tolerate individual failures
  const settled = await Promise.allSettled(
    symbols.map(async (sym) => {
      const result = await runBuiltinBacktest(
        strategy,
        { ...request, symbols: [sym], initialCash: perSymbolCash },
        config,
        provider,
      );
      return { symbol: sym, result };
    }),
  );

  const perSymbolResults: Array<{ symbol: string; result: BacktestResult }> = [];
  const failedSymbols: Array<{ symbol: string; error: string }> = [];
  for (let i = 0; i < settled.length; i++) {
    const s = settled[i]!;
    if (s.status === "fulfilled") {
      perSymbolResults.push(s.value);
    } else {
      failedSymbols.push({
        symbol: symbols[i]!,
        error: s.reason instanceof Error ? s.reason.message : String(s.reason),
      });
    }
  }

  if (perSymbolResults.length === 0) {
    throw new Error(
      `所有股票数据获取失败: ${failedSymbols.map((f) => `${f.symbol}(${f.error})`).join("; ")}`,
    );
  }

  // Build merged equity curve (sum of all per-symbol curves aligned by date)
  const dateMap = new Map<string, number>();
  for (const { result } of perSymbolResults) {
    for (const { date, value } of result.equityCurve) {
      dateMap.set(date, (dateMap.get(date) ?? 0) + value);
    }
  }
  const uninvestedCashForCurve = request.initialCash - perSymbolCash * perSymbolResults.length;
  const equityCurve = [...dateMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, value]) => ({ date, value: value + uninvestedCashForCurve }));

  // Aggregate signals from all symbols
  const allSignals = perSymbolResults.flatMap(({ result }) => result.signals);
  allSignals.sort((a, b) => a.timestamp - b.timestamp);

  // Portfolio-level metrics — include uninvested cash from failed symbols
  const investedValue = perSymbolResults.reduce((sum, { result }) => sum + result.finalValue, 0);
  const actualInitialCash = perSymbolCash * perSymbolResults.length;
  const uninvestedCash = request.initialCash - actualInitialCash; // cash from failed symbols
  const finalValue = investedValue + uninvestedCash;
  const tradingDays = equityCurve.length;
  const years = tradingDays / 252;
  const annualizedReturn = years > 0 ? (investedValue / actualInitialCash) ** (1 / years) - 1 : 0;

  // Portfolio Sharpe from merged equity curve
  const dailyReturns: number[] = [];
  for (let i = 1; i < equityCurve.length; i++) {
    const prevVal = equityCurve[i - 1]!.value;
    if (prevVal > 0) dailyReturns.push(equityCurve[i]!.value / prevVal - 1);
  }
  const avgReturn = dailyReturns.reduce((a, b) => a + b, 0) / (dailyReturns.length || 1);
  const stdDev = Math.sqrt(
    dailyReturns.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / (dailyReturns.length || 1),
  );
  const sharpeRatio = stdDev > 0 ? (avgReturn / stdDev) * Math.sqrt(252) : 0;

  // Portfolio max drawdown from merged curve
  let peakValue = equityCurve[0]?.value ?? request.initialCash;
  let maxDrawdown = 0;
  let maxDrawdownDuration = 0;
  let ddStart = 0;
  for (let i = 0; i < equityCurve.length; i++) {
    const equity = equityCurve[i]!.value;
    if (equity > peakValue) {
      peakValue = equity;
      ddStart = i;
    }
    const dd = peakValue > 0 ? (peakValue - equity) / peakValue : 0;
    if (dd > maxDrawdown) {
      maxDrawdown = dd;
      maxDrawdownDuration = i - ddStart;
    }
  }

  // Average win rate and total trades across successful symbols
  const successCount = perSymbolResults.length;
  const totalTrades = perSymbolResults.reduce((sum, { result }) => sum + result.totalTrades, 0);
  const avgWinRate =
    perSymbolResults.reduce((sum, { result }) => sum + result.winRate, 0) / successCount;
  const totalFees =
    Math.round(
      perSymbolResults.reduce((sum, { result }) => sum + (result.totalFees ?? 0), 0) * 100,
    ) / 100;

  return {
    strategyId: request.strategyId,
    symbols: perSymbolResults.map(({ symbol }) => symbol),
    startDate: request.startDate,
    endDate: request.endDate,
    initialCash: request.initialCash,
    finalValue,
    totalReturn: ((finalValue - request.initialCash) / request.initialCash) * 100,
    annualizedReturn: annualizedReturn * 100,
    maxDrawdown: maxDrawdown * 100,
    maxDrawdownDuration,
    sharpeRatio,
    winRate: avgWinRate,
    totalTrades,
    totalFees,
    feeImpact: (totalFees / actualInitialCash) * 100,
    signals: allSignals,
    equityCurve,
    perSymbol: perSymbolResults,
    failedSymbols: failedSymbols.length > 0 ? failedSymbols : undefined,
  };
}
