import type { OrderRequest } from "../broker/adapter.js";
import { isMarketOpen } from "../broker/paper-broker.js";
import fs from "node:fs/promises";
import path from "node:path";
/**
 * RiskGate — pre-trade risk checks.
 *
 * Every order request passes through RiskGate.check() before being submitted
 * to the broker. The gate enforces hard limits (reject) and soft limits
 * (require human confirmation).
 *
 * Rules evaluated in order:
 *  1. BLOCKED_SYMBOL        — blacklist check
 *  2. ALLOWED_SYMBOL        — whitelist check
 *  3. MARKET_HOURS          — trading session check
 *  4. INSUFFICIENT_CASH     — available cash vs order cost
 *  5. INSUFFICIENT_POSITION — available qty vs sell order
 *  6. MAX_ORDER_VALUE       — single order notional limit
 *  7. MAX_POSITION_PCT      — concentration limit
 *  8. MAX_DAILY_LOSS        — daily loss stop
 *  9. MAX_TOTAL_LOSS        — total loss stop
 * 10. STOP_LOSS             — per-position trailing stop-loss
 * 11. MAX_OPEN_ORDERS       — maximum concurrent open orders
 * 12. PRICE_LIMIT           — price deviation / 涨跌停 sanity check
 * 13. Option-specific rules  — naked short, delta/gamma/vega exposure, near-expiry
 * 14. Futures-specific rules — margin utilization, delivery proximity, max lots
 * 15. ORDER_FREQUENCY       — sliding-window order rate limit
 * 16. MAX_DAILY_TRADES      — daily round-trip trade cap
 * 17. Soft: price above confirmationThreshold → needsConfirmation = true
 */
import type {
  BrokerAccount,
  LivePosition,
  MarketType,
  Order,
  RiskCheckResult,
  RiskLimitsConfig,
  RiskRuleId,
} from "../types.js";
import type { MarketContext } from "./market-context.js";
import { todayCST } from "../utils/cst-time.js";

// Default limits (conservative)
const DEFAULT_LIMITS: Required<RiskLimitsConfig> = {
  maxOrderValue: 500_000, // ¥50万
  maxPositionPct: 0.3, // 单标的不超过总资产 30%
  maxDailyLoss: 50_000, // 日亏损超 ¥5万停止买入
  maxTotalLoss: 200_000, // 总亏损超 ¥20万全面停止
  confirmationThreshold: 100_000, // ¥10万以上需确认
  confirmationTimeoutSecs: 120,
  allowedSymbols: [],
  blockedSymbols: [],
  stopLossPct: 0.08, // 单持仓亏损超 8% 禁止加仓
  maxOpenOrders: 10, // 最多同时挂单 10 个
  priceLimitPct: 0.1, // 限价偏离最新价超 10% 拒绝（A股涨跌停）
  maxNakedShort: 0, // 裸卖空禁止
  maxDeltaExposure: 500,
  maxGammaExposure: 100,
  maxVegaExposure: 10_000,
  nearExpiryDays: 3,
  // Futures defaults
  maxMarginUtilization: 0.8, // 保证金占用率不超过 80%
  deliveryGuardDays: 5, // 交割月前 5 天禁止新开仓
  maxFuturesLots: 100, // 单合约最大持仓 100 手
  // Frequency / over-trading defaults
  maxOrdersPerMinute: 10, // 每分钟最多下 10 单
  maxDailyTrades: 50, // 每日最多 50 笔成交
  maxThetaExposure: 0, // 0 = unlimited
};

function pass(): RiskCheckResult {
  return { passed: true };
}

function fail(rule: RiskRuleId, reason: string): RiskCheckResult {
  return { passed: false, failedRule: rule, reason };
}

function softConfirm(reason: string): RiskCheckResult {
  return { passed: true, needsConfirmation: true, confirmationReason: reason };
}

export class RiskGate {
  private limits: Required<RiskLimitsConfig>;
  private dataDir: string | null;

  // ── Frequency tracking state ─────────────────────────────────
  /** Sliding window of recent order timestamps (epoch ms). */
  private recentOrderTimestamps: number[] = [];
  /** Number of round-trip trades recorded today (YYYY-MM-DD key). */
  private dailyTradeCount = 0;
  /** Calendar date string for the current trading day. */
  private dailyTradeDate = "";

  constructor(limits?: RiskLimitsConfig, dataDir?: string | null) {
    this.limits = { ...DEFAULT_LIMITS, ...(limits ?? {}) };
    this.dataDir = dataDir ?? null;
    void this.loadDailyTradeCount();
  }

  /**
   * Record a completed round-trip trade (call after a fill closes a position).
   * This updates the daily trade counter used by the MAX_DAILY_TRADES rule.
   */
  recordTrade(): void {
    const today = todayCST();
    if (today !== this.dailyTradeDate) {
      this.dailyTradeDate = today;
      this.dailyTradeCount = 0;
    }
    this.dailyTradeCount++;
    void this.saveDailyTradeCount();
  }

  // ── Persistence for daily trade count ─────────────────────────

  private get tradeCountPath(): string | null {
    if (!this.dataDir) return null;
    return path.join(this.dataDir, "risk-gate-state.json");
  }

  private async loadDailyTradeCount(): Promise<void> {
    const filePath = this.tradeCountPath;
    if (!filePath) return;
    try {
      const raw = await fs.readFile(filePath, "utf-8");
      const state = JSON.parse(raw) as { date?: string; count?: number };
      const today = todayCST();
      if (state.date === today) {
        this.dailyTradeDate = state.date;
        this.dailyTradeCount = state.count ?? 0;
      }
    } catch {
      // File doesn't exist or is corrupted — start fresh
    }
  }

  private async saveDailyTradeCount(): Promise<void> {
    const filePath = this.tradeCountPath;
    if (!filePath) return;
    try {
      await fs.writeFile(
        filePath,
        JSON.stringify({ date: this.dailyTradeDate, count: this.dailyTradeCount }),
      );
    } catch {
      // Best-effort persistence
    }
  }

  /**
   * Run all risk checks for an order.
   *
   * @param order       - Proposed order request
   * @param account     - Current account snapshot (cash, totalAssets, etc.)
   * @param positions   - Current live positions
   * @param dailyLoss   - Today's realised loss (positive = loss)
   * @param market      - Market type (for hours check); defaults to cn_stock
   * @param openOrders  - Currently open (unfilled) orders for max-open-orders rule
   * @param overrides   - Per-strategy overrides for maxOrderValue / maxPositionPct
   * @returns           - First failing result, or the last result (which may be
   *                      a soft confirmation request), or a simple pass.
   */
  check(
    order: OrderRequest,
    account: BrokerAccount,
    positions: LivePosition[],
    dailyLoss: number,
    market?: MarketType,
    openOrders?: Order[],
    overrides?: { maxOrderValue?: number; maxPositionPct?: number; skipMarketHours?: boolean },
    marketContext?: MarketContext,
  ): RiskCheckResult {
    const isBuy =
      order.side === "buy" ||
      order.side === "cover" ||
      order.side === "buy_open" ||
      order.side === "buy_close";
    const isSell =
      order.side === "sell" ||
      order.side === "short" ||
      order.side === "sell_open" ||
      order.side === "sell_close";
    const isOptionOrder =
      (order.side === "buy_open" ||
        order.side === "sell_open" ||
        order.side === "buy_close" ||
        order.side === "sell_close") &&
      market !== "futures"; // Futures use same sides but option-specific rules don't apply
    const price = order.price ?? 0;
    let orderValue = price * order.quantity;
    // Cache position lookup — avoids repeated O(n) scans
    const pos = positions.find((p) => p.symbol === order.symbol) ?? null;
    // For market orders (price=0), estimate order value from position data
    // so that value-based risk rules (rules 6, 7, etc.) are not silently skipped.
    const isMarketOrder = order.orderType === "market" || price === 0;
    if (isMarketOrder && orderValue === 0) {
      const estPrice = pos?.currentPrice ?? pos?.avgCost ?? 0;
      if (estPrice > 0) {
        orderValue = estPrice * order.quantity;
      }
    }
    const limits = overrides ? { ...this.limits, ...overrides } : this.limits;
    // Whether this order opens new risk exposure (vs closing/reducing)
    const isRiskOpening =
      order.side === "buy" ||
      order.side === "buy_open" ||
      order.side === "short" ||
      order.side === "sell_open";

    // 1. Blacklist
    if (limits.blockedSymbols.length > 0 && limits.blockedSymbols.includes(order.symbol)) {
      return fail("BLOCKED_SYMBOL", `${order.symbol} 在黑名单中，禁止交易`);
    }

    // 2. Whitelist
    if (limits.allowedSymbols.length > 0 && !limits.allowedSymbols.includes(order.symbol)) {
      return fail(
        "ALLOWED_SYMBOL",
        `${order.symbol} 不在白名单中，禁止交易。白名单: ${limits.allowedSymbols.join(", ")}`,
      );
    }

    // 3. Market hours (can be skipped for pre-market limit orders)
    if (!overrides?.skipMarketHours && !isMarketOpen(market ?? order.market)) {
      return fail("MARKET_HOURS", "当前非交易时段");
    }

    // 4. Cash check (only for buy orders opening new positions)
    // Skip for cover/buy_close (they release margin, not consume)
    if (isBuy && order.side !== "cover" && order.side !== "buy_close") {
      if (isMarketOrder && orderValue === 0) {
        // Market order with no price — require at least estimating from current position
        const estimatedPrice = pos?.currentPrice ?? pos?.avgCost ?? 0;
        if (estimatedPrice > 0) {
          const estValue = estimatedPrice * order.quantity;
          const estimatedCommission = estValue * 0.0003;
          const isFuturesMarket = market === "futures";
          const estimatedMarginRate = 0.1;
          const cashNeeded = isFuturesMarket
            ? estValue * estimatedMarginRate + estimatedCommission
            : estValue + estimatedCommission;
          if (cashNeeded > account.cash) {
            return fail(
              "INSUFFICIENT_CASH",
              `资金不足。需要约 ¥${cashNeeded.toFixed(2)}（市价估算），可用现金 ¥${account.cash.toFixed(2)}`,
            );
          }
        }
      } else if (orderValue > 0) {
        const estimatedCommission = orderValue * 0.0003;
        const isFuturesMarket = market === "futures";
        const estimatedMarginRate = 0.1;
        const cashNeeded = isFuturesMarket
          ? orderValue * estimatedMarginRate + estimatedCommission
          : orderValue + estimatedCommission;
        if (cashNeeded > account.cash) {
          return fail(
            "INSUFFICIENT_CASH",
            `资金不足。需要 ¥${cashNeeded.toFixed(2)}，可用现金 ¥${account.cash.toFixed(2)}`,
          );
        }
      }
    }

    // 5. Position check (for sell orders closing existing positions, and cover/buy_close)
    // sell_open opens a new short position — no existing position required
    // cover / buy_close reduce short positions — check abs(short qty)
    if (isSell && order.side !== "sell_open") {
      const available = pos?.availableQty ?? 0;
      if (order.quantity > available) {
        return fail(
          "INSUFFICIENT_POSITION",
          `持仓不足。需卖 ${order.quantity} 股，可用持仓 ${available} 股`,
        );
      }
    }
    if ((order.side === "cover" || order.side === "buy_close") && isBuy) {
      // cover / buy_close need a short position to close
      const shortQty = pos && pos.quantity < 0 ? Math.abs(pos.quantity) : 0;
      const available = pos?.availableQty ?? shortQty;
      if (order.quantity > available) {
        return fail(
          "INSUFFICIENT_POSITION",
          `空头持仓不足。需平 ${order.quantity} 手，可用空头 ${available} 手`,
        );
      }
    }

    // 6. Max single order value (uses estimated price for market orders)
    if (orderValue > 0 && orderValue > limits.maxOrderValue) {
      return fail(
        "MAX_ORDER_VALUE",
        `单笔订单金额 ¥${orderValue.toFixed(2)} 超过上限 ¥${limits.maxOrderValue.toFixed(2)}`,
      );
    }

    // 7. Max position concentration (for orders opening new exposure: buy, buy_open, sell_open)
    if ((isBuy || order.side === "sell_open") && account.totalAssets > 0 && isRiskOpening) {
      const currentValue = Math.abs(pos?.marketValue ?? 0);
      const addedValue = orderValue > 0 ? orderValue : 0;
      const newValue = currentValue + addedValue;
      const newPct = newValue / account.totalAssets;
      if (newPct > limits.maxPositionPct) {
        return fail(
          "MAX_POSITION_PCT",
          `${order.side === "sell_open" ? "做空" : "买入"}后 ${order.symbol} 持仓占比 ${(newPct * 100).toFixed(1)}% 超过上限 ${(limits.maxPositionPct * 100).toFixed(0)}%`,
        );
      }
    }

    // 8. Daily loss stop — block NEW exposure (buy, buy_open, sell_open)
    if (dailyLoss >= limits.maxDailyLoss && isRiskOpening) {
      return fail(
        "MAX_DAILY_LOSS",
        `今日已亏损 ¥${dailyLoss.toFixed(2)} 超过日亏损上限 ¥${limits.maxDailyLoss.toFixed(2)}，暂停开仓`,
      );
    }

    // 9. Total loss stop (unrealized + realized)
    const totalLoss = Math.max(0, account.profit < 0 ? -account.profit : 0);
    if (totalLoss >= limits.maxTotalLoss) {
      return fail(
        "MAX_TOTAL_LOSS",
        `总亏损 ¥${totalLoss.toFixed(2)} 超过上限 ¥${limits.maxTotalLoss.toFixed(2)}，禁止所有交易`,
      );
    }

    // 10. Per-position stop-loss: if a position is already losing > stopLossPct,
    //     block additional exposure (buy_open or sell_open) on the same symbol.
    //     Does NOT block cover/buy_close — those REDUCE risk and should always be allowed.
    if (isRiskOpening && limits.stopLossPct > 0) {
      if (pos && pos.avgCost > 0 && price > 0) {
        // For short positions (quantity < 0): loss when price rises above avgCost
        // For long positions (quantity > 0): loss when price drops below avgCost
        const lossPct =
          pos.quantity < 0
            ? (price - pos.avgCost) / pos.avgCost // short: price up = loss
            : (pos.avgCost - price) / pos.avgCost; // long: price down = loss
        if (lossPct >= limits.stopLossPct) {
          return fail(
            "STOP_LOSS",
            `${order.symbol} 当前浮亏 ${(lossPct * 100).toFixed(1)}% 超过止损线 ${(limits.stopLossPct * 100).toFixed(0)}%，禁止加仓`,
          );
        }
      }
    }

    // 11. Max concurrent open orders
    if (openOrders && openOrders.length >= limits.maxOpenOrders) {
      return fail(
        "MAX_OPEN_ORDERS",
        `当前挂单数 ${openOrders.length} 已达上限 ${limits.maxOpenOrders}，请等待现有订单成交或取消`,
      );
    }

    // 12. Price limit / 涨跌停 sanity check
    // Reject if the limit price deviates more than priceLimitPct from the
    // position's current price or avg cost. For A-shares the daily limit is
    // typically ±10% (±20% for ChiNext/STAR).
    if (order.orderType === "limit" && price > 0 && limits.priceLimitPct > 0) {
      const refPrice = pos?.currentPrice ?? pos?.avgCost ?? 0;
      if (refPrice > 0) {
        const deviation = Math.abs(price - refPrice) / refPrice;
        if (deviation > limits.priceLimitPct) {
          return fail(
            "PRICE_LIMIT",
            `限价 ¥${price.toFixed(2)} 偏离参考价 ¥${refPrice.toFixed(2)} 达 ${(deviation * 100).toFixed(1)}%，` +
              `超过 ${(limits.priceLimitPct * 100).toFixed(0)}% 上限（涨跌停保护）`,
          );
        }
      }
    }

    // 13. Option-specific risk rules
    if (isOptionOrder) {
      // 13a. Max naked short option contracts
      if (order.side === "sell_open" && limits.maxNakedShort != null) {
        const currentShort = positions
          .filter((p) => p.quantity < 0 && (p.optionType || p.strike))
          .reduce((sum, p) => sum + Math.abs(p.quantity), 0);
        if (currentShort + order.quantity > limits.maxNakedShort) {
          return fail(
            "MAX_NAKED_SHORT",
            `卖出开仓后空头合约数 ${currentShort + order.quantity} 将超过上限 ${limits.maxNakedShort}`,
          );
        }
      }

      // 13b. Delta exposure check
      if (limits.maxDeltaExposure != null) {
        const totalDelta = positions.reduce(
          (sum, p) => sum + (p.greeks?.delta ?? 0) * p.quantity,
          0,
        );
        if (Math.abs(totalDelta) > limits.maxDeltaExposure) {
          return fail(
            "MAX_DELTA_EXPOSURE",
            `组合Delta暴露 ${totalDelta.toFixed(2)} 超过限制 ±${limits.maxDeltaExposure}`,
          );
        }
      }

      // 13b2. Gamma exposure check
      if (limits.maxGammaExposure != null) {
        const totalGamma = positions.reduce(
          (sum, p) => sum + (p.greeks?.gamma ?? 0) * p.quantity,
          0,
        );
        if (Math.abs(totalGamma) > limits.maxGammaExposure) {
          return fail(
            "MAX_GAMMA_EXPOSURE",
            `组合Gamma暴露 ${totalGamma.toFixed(2)} 超过限制 ±${limits.maxGammaExposure}`,
          );
        }
      }

      // 13c. Vega exposure check
      if (limits.maxVegaExposure != null) {
        const totalVega = positions.reduce((sum, p) => sum + (p.greeks?.vega ?? 0) * p.quantity, 0);
        if (Math.abs(totalVega) > limits.maxVegaExposure) {
          return fail(
            "MAX_VEGA_EXPOSURE",
            `组合Vega暴露 ${totalVega.toFixed(2)} 超过限制 ±${limits.maxVegaExposure}`,
          );
        }
      }

      // 13d. Near-expiry trading guard: block sell_open on near-expiry options
      if (limits.nearExpiryDays != null && order.side === "sell_open") {
        // Requires order to carry expiry info — skip if not present
        const orderExpiry = (order as OrderRequest & { expiry?: string }).expiry;
        if (orderExpiry) {
          const dte = (new Date(orderExpiry).getTime() - Date.now()) / (24 * 60 * 60 * 1000);
          if (dte <= limits.nearExpiryDays) {
            return fail(
              "NEAR_EXPIRY_WARNING",
              `期权合约距到期仅 ${dte.toFixed(0)} 天，不建议卖出开仓（阈值 ${limits.nearExpiryDays} 天）`,
            );
          }
        }
      }
    }

    // 14. Futures-specific risk rules
    const isFuturesOrder = market === "futures";
    if (isFuturesOrder) {
      // 14a. Max margin utilization — ensure total frozen margin doesn't exceed threshold
      if (limits.maxMarginUtilization != null && limits.maxMarginUtilization > 0 && isBuy) {
        const totalFrozenMargin = positions
          .filter((p) => p.market === "futures")
          .reduce((sum, p) => sum + (p.frozenMargin ?? 0), 0);
        const totalEquity = account.totalAssets;
        // Estimate margin for the new order (use 10% default margin rate)
        const estimatedMarginRate = 0.1;
        const newOrderMargin = orderValue * estimatedMarginRate;
        if (totalEquity > 0) {
          const utilization = (totalFrozenMargin + newOrderMargin) / totalEquity;
          if (utilization > limits.maxMarginUtilization) {
            return fail(
              "MAX_MARGIN_UTILIZATION",
              `保证金占用率 ${(utilization * 100).toFixed(1)}% 将超过上限 ${(limits.maxMarginUtilization * 100).toFixed(0)}%，禁止开仓`,
            );
          }
        }
      }

      // 14b. Delivery proximity guard — block new opens near delivery month
      if (limits.deliveryGuardDays != null && limits.deliveryGuardDays > 0) {
        if (order.side === "buy_open" || order.side === "sell_open") {
          // Extract delivery month from symbol (e.g. "IF2503" → "2503", "TA405" → "405")
          const monthMatch = order.symbol.match(/(\d{3,4})$/);
          if (monthMatch) {
            const digits = monthMatch[1];
            let year: number;
            let month: number;
            if (digits.length === 4) {
              // YYMM format: "2503" → 2025-03
              year = 2000 + Number(digits.slice(0, 2));
              month = Number(digits.slice(2, 4));
            } else {
              // YMM format: "405" → last digit of year + 2-digit month
              // Use current decade to infer full year
              const currentYear = new Date().getFullYear();
              const decade = Math.floor(currentYear / 10) * 10;
              year = decade + Number(digits[0]);
              month = Number(digits.slice(1, 3));
            }
            // Delivery is typically the 3rd Friday or last trading day of the month
            // Approximate: last day of the month
            if (month >= 1 && month <= 12) {
              const deliveryDate = new Date(year, month, 0); // last day of delivery month
              const daysToDelivery = (deliveryDate.getTime() - Date.now()) / (24 * 60 * 60 * 1000);
              if (daysToDelivery <= limits.deliveryGuardDays && daysToDelivery >= 0) {
                return fail(
                  "DELIVERY_PROXIMITY",
                  `合约 ${order.symbol} 距交割仅 ${Math.ceil(daysToDelivery)} 天（阈值 ${limits.deliveryGuardDays} 天），禁止新开仓`,
                );
              }
            }
          }
        }
      }

      // 14c. Max lots per contract
      if (limits.maxFuturesLots != null && limits.maxFuturesLots > 0) {
        if (order.side === "buy_open" || order.side === "sell_open") {
          const currentLots = pos ? Math.abs(pos.quantity) : 0;
          if (currentLots + order.quantity > limits.maxFuturesLots) {
            return fail(
              "MAX_FUTURES_LOTS",
              `${order.symbol} 持仓 ${currentLots} + 新增 ${order.quantity} 手将超过单合约上限 ${limits.maxFuturesLots} 手`,
            );
          }
        }
      }
    }

    // 15. Order frequency limit — sliding 60-second window
    if (limits.maxOrdersPerMinute > 0) {
      const now = Date.now();
      const oneMinuteAgo = now - 60_000;
      // Prune old timestamps
      this.recentOrderTimestamps = this.recentOrderTimestamps.filter((t) => t > oneMinuteAgo);
      if (this.recentOrderTimestamps.length >= limits.maxOrdersPerMinute) {
        return fail(
          "ORDER_FREQUENCY",
          `近 1 分钟内已下单 ${this.recentOrderTimestamps.length} 次，超过频率上限 ${limits.maxOrdersPerMinute} 次/分钟`,
        );
      }
      // Record this order attempt
      this.recentOrderTimestamps.push(now);
    }

    // 16. Max daily trades (round-trips)
    if (limits.maxDailyTrades > 0 && isRiskOpening) {
      const today = todayCST();
      if (today !== this.dailyTradeDate) {
        this.dailyTradeDate = today;
        this.dailyTradeCount = 0;
      }
      if (this.dailyTradeCount >= limits.maxDailyTrades) {
        return fail(
          "MAX_DAILY_TRADES",
          `今日已完成 ${this.dailyTradeCount} 笔交易，超过日交易上限 ${limits.maxDailyTrades} 笔，暂停开仓`,
        );
      }
    }

    // ── Market Context Enrichment Rules (18-22) ─────────────────
    // These rules use real-time market sentiment, lianban, catalyst, ST, and
    // macro regime data from MarketContextService. They only fire when context
    // is provided and only block risk-opening orders.
    if (marketContext && isRiskOpening) {
      // 18. Sentiment overheat — extreme greed → restrict new exposure
      if (marketContext.sentiment && marketContext.sentiment.score >= 80) {
        return fail(
          "SENTIMENT_OVERHEAT",
          `市场情绪过热（${marketContext.sentiment.score.toFixed(0)}分/${marketContext.sentiment.tone}），` +
            `炸板率 ${((marketContext.sentiment.zbgcRate ?? 0) * 100).toFixed(0)}%，暂停新开仓`,
        );
      }

      // 19. Lianban crash risk — stock in limit-up pool with high board failure rate
      if (marketContext.lianban?.inPool && marketContext.sentiment) {
        const zbgc = marketContext.sentiment.zbgcRate ?? 0;
        if (zbgc > 0.3) {
          return fail(
            "LIANBAN_CRASH_RISK",
            `${order.symbol} 在连板池（${marketContext.lianban.level}板），` +
              `当前炸板率 ${(zbgc * 100).toFixed(0)}% 偏高，限制买入`,
          );
        }
      }

      // 20. ST event proximity — restrict exposure near restructuring events
      if (marketContext.stStatus) {
        const riskLevel = marketContext.stStatus.riskLevel;
        if (riskLevel === "extreme" || riskLevel === "high") {
          return fail(
            "ST_EVENT_PROXIMITY",
            `${order.symbol} 处于ST重整阶段 ${marketContext.stStatus.stage}（${riskLevel}风险），` +
              `限制新开仓`,
          );
        }
      }

      // 21. Catalyst proximity — restrict new exposure near major events
      if (marketContext.catalyst) {
        const days = marketContext.catalyst.daysUntil;
        if (days >= 0 && days <= 3 && marketContext.catalyst.impact === "high") {
          return fail(
            "CATALYST_PROXIMITY",
            `${order.symbol} 距催化事件仅 ${days} 天（${marketContext.catalyst.description}），` +
              `高影响事件前限制新仓`,
          );
        }
      }

      // 22. Regime mismatch — restrict aggressive exposure in BEAR/VOLATILE regimes
      if (
        marketContext.macroRegime === "BEAR" ||
        marketContext.volatilityRegime === "EXTREME"
      ) {
        return fail(
          "REGIME_MISMATCH",
          `当前宏观环境 ${marketContext.macroRegime ?? "unknown"}，` +
            `波动率 ${marketContext.volatilityRegime ?? "unknown"}，限制新开仓规模`,
        );
      }
    }

    // 23. Soft confirmation threshold
    if (orderValue >= limits.confirmationThreshold) {
      return softConfirm(
        `订单金额 ¥${orderValue.toFixed(2)} 超过确认阈值 ¥${limits.confirmationThreshold.toFixed(2)}，需要人工确认`,
      );
    }

    return pass();
  }
}
