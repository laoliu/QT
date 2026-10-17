/**
 * options_trading tool — AI-callable tool for option order management.
 *
 * Provides option-specific order placement (buy/sell open/close),
 * option position management, exercise, and option-specific risk views.
 * Delegates to the LiveEngine's broker adapter.
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { OrderRequest } from "../broker/adapter.js";
import { createOptionsDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type {
  LivePosition,
  MarketType,
  Order,
  OptionType,
  OrderSide,
  QuantTradingConfig,
} from "../types.js";

export function createOptionsTradingTool(
  api: QuantTradingRuntime,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
) {
  const optionsProvider = createOptionsDataProvider(config);

  return {
    name: "options_trading",
    description:
      "期权交易管理工具：期权下单（买开/卖开/买平/卖平）、持仓查看、行权、组合下单。\n" +
      "支持 ETF期权、股指期权、商品期权的实盘/模拟交易。\n\n" +
      "action=place_order 期权下单，\n" +
      "action=place_spread 组合/价差下单（多腿），\n" +
      "action=close_position 平仓持仓，\n" +
      "action=exercise 行权（仅美式期权），\n" +
      "action=positions 查看期权持仓，\n" +
      "action=orders 查看期权委托，\n" +
      "action=risk_summary 期权风险概览。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "place_order",
            "place_spread",
            "close_position",
            "exercise",
            "positions",
            "orders",
            "risk_summary",
          ],
          description:
            "操作类型: place_order=下单, place_spread=组合下单, " +
            "close_position=平仓, exercise=行权, positions=持仓, orders=委托, risk_summary=风险概览",
        },
        // ── place_order fields ─────────────────────────────────
        symbol: {
          type: "string",
          description: "期权合约代码",
        },
        side: {
          type: "string",
          enum: ["buy_open", "sell_open", "buy_close", "sell_close"],
          description:
            "交易方向: buy_open=买入开仓, sell_open=卖出开仓, " +
            "buy_close=买入平仓, sell_close=卖出平仓",
        },
        quantity: {
          type: "number",
          description: "合约张数",
        },
        price: {
          type: "number",
          description: "委托价格（限价单必填）",
        },
        orderType: {
          type: "string",
          enum: ["limit", "market"],
          description: "订单类型，默认 limit (限价单)",
        },
        // ── place_spread fields (multi-leg) ────────────────────
        legs: {
          type: "string",
          description:
            "组合腿（JSON数组），每腿: {symbol, side, quantity, price}。" +
            "用于 action=place_spread。",
        },
        spreadName: {
          type: "string",
          description: "组合策略名称（可选，仅记录用）",
        },
        // ── close_position fields ──────────────────────────────
        closeQuantity: {
          type: "number",
          description: "平仓张数（不填则全平）",
        },
        // ── option metadata (for place_order) ─────────────────
        strikePrice: {
          type: "number",
          description: "行权价",
        },
        expiry: {
          type: "string",
          description: "到期日 YYYY-MM-DD",
        },
        // ── general filters ────────────────────────────────────
        underlying: {
          type: "string",
          description: "标的代码（筛选持仓/委托时使用）",
        },
        optionType: {
          type: "string",
          enum: ["call", "put"],
          description: "期权类型筛选",
        },
        orderId: {
          type: "string",
          description: "订单ID（撤单/查询时使用）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── Place Single Option Order ─────────────────────────
        case "place_order": {
          const engine = getEngine();
          if (!engine) return { error: "交易引擎未启动，请先启动实盘引擎" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化" };

          const symbol = args.symbol as string;
          const side = args.side as string;
          const quantity = args.quantity as number;
          const price = args.price as number | undefined;
          const orderType = (args.orderType as "limit" | "market") ?? "limit";

          if (!symbol) return { error: "请提供期权合约代码 (symbol)" };
          if (!side)
            return { error: "请提供交易方向 (side): buy_open/sell_open/buy_close/sell_close" };

          const VALID_OPTION_SIDES = ["buy_open", "sell_open", "buy_close", "sell_close"];
          if (!VALID_OPTION_SIDES.includes(side)) {
            return {
              error: `无效的期权交易方向 "${side}"，请使用: buy_open(买入开仓)/sell_open(卖出开仓)/buy_close(买入平仓)/sell_close(卖出平仓)`,
            };
          }

          if (!quantity || quantity <= 0) return { error: "请提供合约张数 (quantity)" };
          if (orderType === "limit" && !price) return { error: "限价单请提供委托价格 (price)" };

          const market = detectMarketFromSymbol(symbol);
          const req: OrderRequest = {
            symbol,
            market,
            side: side as OrderSide,
            orderType,
            quantity,
            price,
            strategyId: "manual_option",
            strike: args.strikePrice as number | undefined,
            expiry: args.expiry as string | undefined,
            optionType: args.optionType as "call" | "put" | undefined,
            underlying: args.underlying as string | undefined,
          };

          const result = await mgr.submit(req);
          if (!result.ok) {
            return { error: result.reason, ruleId: result.ruleId };
          }

          return {
            message: "期权下单成功",
            orderId: result.order.orderId,
            symbol,
            side,
            quantity,
            price: price?.toFixed(4),
            status: result.order.status,
          };
        }

        // ── Place Multi-leg Spread ────────────────────────────
        case "place_spread": {
          const engine = getEngine();
          if (!engine) return { error: "交易引擎未启动" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化" };

          const legsStr = args.legs as string;
          if (!legsStr) return { error: "请提供组合腿 (legs) JSON数组" };

          let legs: Array<{
            symbol: string;
            side: string;
            quantity: number;
            price?: number;
            strike?: number;
            expiry?: string;
            optionType?: string;
            underlying?: string;
          }>;
          try {
            legs = JSON.parse(legsStr);
          } catch {
            return { error: "legs 格式错误，请提供合法 JSON 数组" };
          }

          if (!Array.isArray(legs) || legs.length === 0) {
            return { error: "至少需要一腿" };
          }

          const results: Array<{
            symbol: string;
            orderId?: string;
            status?: string;
            error?: string;
          }> = [];

          const VALID_SPREAD_SIDES = ["buy_open", "sell_open", "buy_close", "sell_close"];
          for (const leg of legs) {
            if (!leg.symbol || !leg.side || !leg.quantity) {
              results.push({ symbol: leg.symbol ?? "?", error: "缺少必要字段" });
              continue;
            }
            if (!VALID_SPREAD_SIDES.includes(leg.side)) {
              results.push({
                symbol: leg.symbol,
                error: `无效的交易方向 "${leg.side}"，请使用: buy_open/sell_open/buy_close/sell_close`,
              });
              continue;
            }
            const req: OrderRequest = {
              symbol: leg.symbol,
              market: detectMarketFromSymbol(leg.symbol),
              side: leg.side as OrderSide,
              orderType: leg.price ? "limit" : "market",
              quantity: leg.quantity,
              price: leg.price,
              strategyId: `spread_${args.spreadName ?? "manual"}`,
              strike: leg.strike as number | undefined,
              expiry: leg.expiry as string | undefined,
              optionType: leg.optionType as "call" | "put" | undefined,
              underlying: (leg.underlying ?? args.underlying) as string | undefined,
            };
            const result = await mgr.submit(req);
            if (result.ok) {
              results.push({
                symbol: leg.symbol,
                orderId: result.order.orderId,
                status: result.order.status,
              });
            } else {
              results.push({ symbol: leg.symbol, error: result.reason });
            }
          }

          const successCount = results.filter((r) => r.orderId).length;
          return {
            message: `组合下单完成: ${successCount}/${legs.length} 成功`,
            spreadName: args.spreadName,
            legs: results,
          };
        }

        // ── Close Position ────────────────────────────────────
        case "close_position": {
          const engine = getEngine();
          if (!engine) return { error: "交易引擎未启动" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化" };

          const symbol = args.symbol as string;
          if (!symbol) return { error: "请提供期权合约代码 (symbol)" };

          // Find matching option position
          const positions = engine.getPositions();
          const pos = positions.find((p) => p.symbol === symbol && p.quantity !== 0);
          if (!pos) return { error: `未找到 ${symbol} 的持仓` };

          const closeQty = (args.closeQuantity as number) ?? pos.quantity;
          const price = args.price as number | undefined;
          // Determine side: if we hold long, we sell_close; if short, buy_close
          const closeSide: OrderSide = pos.quantity > 0 ? "sell_close" : "buy_close";

          const req: OrderRequest = {
            symbol,
            market: detectMarketFromSymbol(symbol),
            side: closeSide,
            orderType: price ? "limit" : "market",
            quantity: Math.abs(closeQty),
            price,
            strategyId: "manual_option_close",
          };
          const result = await mgr.submit(req);
          if (!result.ok) return { error: result.reason, ruleId: result.ruleId };

          return {
            message: `期权平仓下单成功`,
            orderId: result.order.orderId,
            symbol,
            side: closeSide,
            quantity: Math.abs(closeQty),
            status: result.order.status,
          };
        }

        // ── Exercise (American option only) ───────────────────
        case "exercise": {
          const engine = getEngine();
          if (!engine) return { error: "交易引擎未启动" };

          const symbol = args.symbol as string;
          const quantity = args.quantity as number;
          if (!symbol) return { error: "请提供期权合约代码 (symbol)" };
          if (!quantity || quantity <= 0) return { error: "请提供行权张数 (quantity)" };

          // Exercise is broker-specific — delegate through broker adapter
          const broker = engine.broker;
          if (!broker) return { error: "券商未连接" };
          if (!broker.exerciseOption) {
            return { error: "当前券商不支持期权行权操作" };
          }

          try {
            const result = await broker.exerciseOption(symbol, quantity);
            return result;
          } catch (err) {
            return { error: `行权请求失败: ${err instanceof Error ? err.message : String(err)}` };
          }
        }

        // ── View Option Positions ─────────────────────────────
        case "positions": {
          const engine = getEngine();
          if (!engine) return { positions: [], message: "引擎未启动" };

          const allPositions = engine.getPositions();
          let optionPositions = allPositions.filter(isOptionPosition);

          // Optional filters
          const underlying = args.underlying as string | undefined;
          const optionType = args.optionType as OptionType | undefined;

          if (underlying) {
            optionPositions = optionPositions.filter(
              (p) => p.underlying === underlying || p.symbol.includes(underlying),
            );
          }
          if (optionType) {
            optionPositions = optionPositions.filter((p) => p.optionType === optionType);
          }

          if (optionPositions.length === 0) {
            return { message: "当前无期权持仓", count: 0, positions: [] };
          }

          // Try to enrich with Greeks from data provider
          const enrichedPositions = await Promise.all(
            optionPositions.map(async (p) => {
              const base: Record<string, unknown> = {
                symbol: p.symbol,
                quantity: p.quantity,
                avgCost: p.avgCost?.toFixed(4),
                marketValue: p.marketValue?.toFixed(2),
                unrealizedPnl: p.unrealizedPnl?.toFixed(2),
                optionType: p.optionType,
                strike: p.strike,
                expiry: p.expiry,
                underlying: p.underlying,
              };
              if (p.greeks) {
                base.greeks = {
                  delta: p.greeks.delta?.toFixed(4),
                  gamma: p.greeks.gamma?.toFixed(6),
                  theta: p.greeks.theta?.toFixed(4),
                  vega: p.greeks.vega?.toFixed(4),
                  rho: p.greeks.rho?.toFixed(4),
                  charm: p.greeks.charm?.toFixed(6),
                  vanna: p.greeks.vanna?.toFixed(6),
                  vomma: p.greeks.vomma?.toFixed(6),
                };
              }
              return base;
            }),
          );

          // Aggregate portfolio Greeks
          const aggDelta = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.delta ?? 0) * p.quantity,
            0,
          );
          const aggGamma = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.gamma ?? 0) * p.quantity,
            0,
          );
          const aggTheta = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.theta ?? 0) * p.quantity,
            0,
          );
          const aggVega = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.vega ?? 0) * p.quantity,
            0,
          );

          return {
            count: enrichedPositions.length,
            positions: enrichedPositions,
            portfolioGreeks: {
              delta: aggDelta.toFixed(4),
              gamma: aggGamma.toFixed(6),
              theta: aggTheta.toFixed(4),
              vega: aggVega.toFixed(4),
            },
          };
        }

        // ── View Option Orders ────────────────────────────────
        case "orders": {
          const engine = getEngine();
          if (!engine) return { orders: [], message: "引擎未启动" };
          const mgr = engine.orderManager;
          if (!mgr) return { orders: [] };

          const allOrders = await mgr.getOrders();
          const optionOrders = allOrders.filter((o: Order) => isOptionSide(o.side));

          const symbol = args.symbol as string | undefined;
          const filtered = symbol
            ? optionOrders.filter((o: Order) => o.symbol === symbol)
            : optionOrders;

          return {
            count: filtered.length,
            orders: filtered.slice(-50).map((o: Order) => ({
              orderId: o.orderId.slice(0, 8),
              symbol: o.symbol,
              side: o.side,
              orderType: o.orderType,
              quantity: o.quantity,
              price: o.price?.toFixed(4),
              status: o.status,
              filledQty: o.filledQty,
              avgFillPrice: o.avgFillPrice?.toFixed(4),
              createdAt: o.createdAt,
            })),
          };
        }

        // ── Option Risk Summary ───────────────────────────────
        case "risk_summary": {
          const engine = getEngine();
          if (!engine) return { error: "交易引擎未启动" };

          const account = engine.getAccount();
          const positions = engine.getPositions();
          if (!account) return { error: "账户数据未同步" };

          const optionPositions = positions.filter(isOptionPosition);
          if (optionPositions.length === 0) {
            return { message: "当前无期权持仓，无需风控概览" };
          }

          // Aggregated Greeks
          const totalDelta = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.delta ?? 0) * p.quantity,
            0,
          );
          const totalGamma = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.gamma ?? 0) * p.quantity,
            0,
          );
          const totalTheta = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.theta ?? 0) * p.quantity,
            0,
          );
          const totalVega = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.vega ?? 0) * p.quantity,
            0,
          );

          const totalRho = optionPositions.reduce(
            (sum, p) => sum + (p.greeks?.rho ?? 0) * p.quantity,
            0,
          );

          // Count positions by type
          const callCount = optionPositions.filter((p) => p.optionType === "call").length;
          const putCount = optionPositions.filter((p) => p.optionType === "put").length;
          const longCount = optionPositions.filter((p) => p.quantity > 0).length;
          const shortPositions = optionPositions.filter((p) => p.quantity < 0);
          const shortCount = shortPositions.length;
          const shortContractCount = shortPositions.reduce(
            (sum, p) => sum + Math.abs(p.quantity),
            0,
          );

          // Margin warnings
          const riskLimits = config.tradingAccount?.riskLimits;
          const warnings: string[] = [];

          if (riskLimits) {
            if (riskLimits.maxNakedShort != null && shortContractCount > riskLimits.maxNakedShort) {
              warnings.push(
                `卖出合约数 ${shortContractCount} 张超过限制 ${riskLimits.maxNakedShort}`,
              );
            }
            if (
              riskLimits.maxDeltaExposure != null &&
              Math.abs(totalDelta) > riskLimits.maxDeltaExposure
            ) {
              warnings.push(
                `Delta暴露 ${totalDelta.toFixed(2)} 超过限制 ±${riskLimits.maxDeltaExposure}`,
              );
            }
            if (
              riskLimits.maxVegaExposure != null &&
              Math.abs(totalVega) > riskLimits.maxVegaExposure
            ) {
              warnings.push(
                `Vega暴露 ${totalVega.toFixed(2)} 超过限制 ±${riskLimits.maxVegaExposure}`,
              );
            }
            if (
              riskLimits.maxThetaExposure != null &&
              riskLimits.maxThetaExposure > 0 &&
              Math.abs(totalTheta) > riskLimits.maxThetaExposure
            ) {
              warnings.push(
                `Theta暴露 ${totalTheta.toFixed(2)}/天 超过限制 ±${riskLimits.maxThetaExposure}`,
              );
            }
          }

          // Time decay warning: highlight positions expiring within 7 days
          const now = Date.now();
          const expiringPositions = optionPositions.filter((p) => {
            if (!p.expiry) return false;
            const expiryDate = new Date(p.expiry).getTime();
            return expiryDate - now < 7 * 24 * 60 * 60 * 1000;
          });

          return {
            account: {
              cash: `¥${account.cash.toFixed(2)}`,
              totalAssets: `¥${account.totalAssets.toFixed(2)}`,
            },
            optionPositions: {
              total: optionPositions.length,
              calls: callCount,
              puts: putCount,
              long: longCount,
              short: shortCount,
            },
            portfolioGreeks: {
              delta: totalDelta.toFixed(4),
              gamma: totalGamma.toFixed(6),
              theta: `${totalTheta.toFixed(2)} (每天)`,
              vega: totalVega.toFixed(4),
              rho: totalRho.toFixed(4),
            },
            expiringIn7Days: expiringPositions.length,
            warnings: warnings.length > 0 ? warnings : undefined,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Helpers ──────────────────────────────────────────────────────

/** Check if a position looks like an option position.
 * Option positions typically have strike/expiry/optionType fields set. */
function isOptionPosition(p: LivePosition): boolean {
  return !!(p.strike || p.optionType || p.expiry);
}

/** Check if an order side is option-specific. */
function isOptionSide(side: string): boolean {
  return ["buy_open", "sell_open", "buy_close", "sell_close"].includes(side);
}
