/**
 * live_trading tool — AI-callable interface for the live trading engine.
 *
 * Requires a LiveEngine instance to be started via the plugin service lifecycle.
 * The tool receives a reference to the LiveEngine and delegates all actions.
 *
 * Actions:
 *   engine_status        — current engine state and summary stats
 *   start_engine         — start the live trading engine
 *   stop_engine          — stop the engine (cancels open orders first)
 *   pause_engine         — pause (quotes continue, no new orders)
 *   resume_engine        — resume after pause
 *   add_strategy         — register a strategy for live execution
 *   remove_strategy      — remove a live strategy
 *   list_strategies      — list all active live strategies
 *   force_order          — manually place an order (full risk check)
 *   basket_order         — batch submit multiple buy/sell orders (basket trading)
 *   cancel_order         — cancel a specific open order
 *   cancel_all_orders    — cancel all open orders
 *   get_risk_snapshot    — risk metrics on current live portfolio
 *   list_confirmations   — list pending human confirmation requests
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderRequest } from "../broker/adapter.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { BasketOrderItem } from "../services/order-manager.js";
import type { QuantTradingConfig } from "../types.js";

export function createLiveTradingTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  equityHistory?: EquityHistory,
) {
  return {
    name: "live_trading",
    description:
      "控制实盘交易引擎。启动/停止引擎、管理实盘策略、手动下单、篮子交易、查看风控快照。" +
      "action=engine_status 查看状态, action=start_engine 启动引擎, " +
      "action=stop_engine 停止引擎, action=pause_engine 暂停, action=resume_engine 恢复, " +
      "action=add_strategy 添加策略, action=remove_strategy 移除策略, " +
      "action=list_strategies 列出策略, action=force_order 强制下单, " +
      "action=basket_order 批量下单/篮子交易, " +
      "action=cancel_order 撤单, action=cancel_all_orders 全撤, " +
      "action=get_risk_snapshot 风控快照, action=list_confirmations 待确认列表。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "engine_status",
            "start_engine",
            "stop_engine",
            "pause_engine",
            "resume_engine",
            "add_strategy",
            "remove_strategy",
            "list_strategies",
            "force_order",
            "basket_order",
            "cancel_order",
            "cancel_all_orders",
            "get_risk_snapshot",
            "list_confirmations",
          ],
          description: "操作类型",
        },
        // For add_strategy / remove_strategy
        strategy_id: { type: "string", description: "策略ID (e.g. dual-ma-crossover)" },
        live_strategy_id: { type: "string", description: "实盘策略实例ID (e.g. ls-abc123)" },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "标的代码列表 (e.g. ['600519', '000300'])",
        },
        parameters: {
          type: "object",
          description: "策略参数 (e.g. {fast_period: 5, slow_period: 20})",
        },
        interval: {
          type: "string",
          description: "K线周期 (e.g. '5m', '15m', '1d'，默认 '1d')",
        },
        time_windows: {
          type: "array",
          items: { type: "string" },
          description:
            "交易时间窗口限制 (e.g. ['09:45-10:30', '14:00-15:00'])，" +
            "设置后策略仅在这些时段内生成信号，留空=全市场时段",
        },
        confirmation_bars: {
          type: "number",
          description:
            "突破确认K线数（默认0=不需要确认）。设置后信号需连续N个周期持续才确认，" +
            "例如 confirmation_bars=3 表示突破信号需连续3个检查周期出现才会触发",
        },
        regimes: {
          type: "string",
          description:
            "策略适用的市场环境(逗号分隔)。支持: bull,bear,sideways,volatile。" +
            "设置后当市场环境不匹配时策略自动暂停，匹配时自动恢复。留空=全环境运行",
        },
        // For force_order
        symbol: { type: "string", description: "标的代码" },
        side: {
          type: "string",
          enum: [
            "buy",
            "sell",
            "short",
            "cover",
            "buy_open",
            "sell_close",
            "buy_close",
            "sell_open",
          ],
          description: "买卖方向（期货/期权使用 buy_open/sell_close/sell_open/buy_close）",
        },
        order_type: {
          type: "string",
          enum: ["market", "limit"],
          description: "订单类型",
        },
        quantity: { type: "number", description: "数量（股）" },
        price: { type: "number", description: "价格（limit order 必填）" },
        expires_minutes: {
          type: "number",
          description: "订单有效期（分钟），超时自动撤单。如 30 = 30分钟后未成交则撤销",
        },
        // For cancel_order
        order_id: { type: "string", description: "订单ID" },
        // For basket_order
        orders: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "标的代码" },
              side: {
                type: "string",
                enum: ["buy", "sell", "short", "cover", "buy_open", "sell_close", "buy_close", "sell_open"],
                description: "买卖方向",
              },
              quantity: { type: "number", description: "数量（股），与 amount 二选一" },
              amount: { type: "number", description: "金额（元），与 quantity 二选一，按金额自动计算数量" },
              price: { type: "number", description: "限价（可选，不填则市价）" },
            },
            required: ["symbol", "side"],
          },
          description: "批量下单列表，每项包含：symbol, side, quantity/amount, price(可选)",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "engine_status": {
          const engine = getEngine();
          if (!engine) return { status: "stopped", message: "引擎未初始化" };
          const account = engine.getAccount();
          const positions = engine.getPositions();
          const strategies = engine.strategyRegistry.list();
          return {
            status: engine.getStatus(),
            mode: config.tradingAccount?.mode ?? "paper",
            broker: config.tradingAccount?.broker ?? "paper",
            account: account
              ? {
                  cash: `¥${account.cash.toFixed(2)}`,
                  totalAssets: `¥${account.totalAssets.toFixed(2)}`,
                  profit: `¥${account.profit.toFixed(2)}`,
                  profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
                  lastSync: account.lastSyncAt,
                }
              : null,
            positionCount: positions.length,
            activeStrategies: strategies.filter((s) => s.enabled).length,
            totalStrategies: strategies.length,
          };
        }

        case "start_engine": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化，请确认插件已正确加载" };
          const status = engine.getStatus();
          if (status === "running") return { message: "引擎已在运行中" };
          engine.start().catch((err) => {
            api.logger.error(`Engine startup failed: ${(err as Error).message}`);
          });
          return { message: "实盘引擎启动中…请稍后使用 engine_status 确认状态" };
        }

        case "stop_engine": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          await engine.stop();
          return { message: "实盘引擎已停止" };
        }

        case "pause_engine": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          engine.pause();
          return { message: "引擎已暂停。行情继续接收，不会产生新订单。使用 resume_engine 恢复。" };
        }

        case "resume_engine": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          engine.resume();
          return { message: "引擎已恢复，继续正常运行" };
        }

        case "add_strategy": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const stratId = args.strategy_id as string;
          const symbols = (args.symbols as string[]) ?? [];
          if (!stratId) return { error: "请提供 strategy_id" };
          if (symbols.length === 0) return { error: "请提供至少一个标的 symbols" };
          const timeWindows = (args.time_windows as string[] | undefined) ?? undefined;
          const interval = (args.interval as string | undefined) ?? undefined;
          const confirmationBars = (args.confirmation_bars as number | undefined) ?? undefined;
          const regimesRaw = args.regimes as string | undefined;
          const regimes = regimesRaw
            ? regimesRaw
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean)
            : undefined;
          const entry = await engine.addStrategy(
            stratId,
            symbols,
            args.parameters as Record<string, unknown> | undefined,
            { timeWindows, interval, confirmationBars, regimes },
          );
          return {
            message: `策略已添加到实盘引擎`,
            live_strategy_id: entry.id,
            strategy_id: entry.strategyId,
            symbols: entry.symbols,
            timeWindows: entry.timeWindows ?? "全市场时段",
            confirmationBars: entry.confirmationBars ?? 0,
            regimes: entry.regimes ?? "全环境运行",
          };
        }

        case "remove_strategy": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const lsId = args.live_strategy_id as string;
          if (!lsId) return { error: "请提供 live_strategy_id" };
          const removed = await engine.removeStrategy(lsId);
          return removed ? { message: `策略 ${lsId} 已移除` } : { error: `未找到策略 ${lsId}` };
        }

        case "list_strategies": {
          const engine = getEngine();
          if (!engine) return { strategies: [] };
          const strategies = engine.strategyRegistry.list();
          return {
            total: strategies.length,
            strategies: strategies.map((s) => ({
              id: s.id,
              strategyId: s.strategyId,
              symbols: s.symbols,
              enabled: s.enabled,
              addedAt: s.addedAt,
              parameters: s.parameters,
              interval: s.interval,
              timeWindows: s.timeWindows ?? "全市场时段",
            })),
          };
        }

        case "force_order": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化，请先启动引擎" };

          const symbol = args.symbol as string;
          const side = args.side as "buy" | "sell" | "short" | "cover";
          const orderType = (args.order_type as "market" | "limit") ?? "limit";
          const quantity = args.quantity as number;
          const price = args.price as number | undefined;

          if (!symbol || !side || !quantity) {
            return { error: "请提供 symbol, side, quantity" };
          }
          if (orderType === "limit" && !price) {
            return { error: "限价单请提供 price" };
          }

          const req: OrderRequest = {
            symbol,
            market: detectMarketFromSymbol(symbol),
            side,
            orderType,
            quantity,
            price,
            strategyId: "manual",
          };
          const expiresMins = args.expires_minutes as number | undefined;
          if (expiresMins && expiresMins > 0) {
            req.expiresAt = new Date(Date.now() + expiresMins * 60 * 1000).toISOString();
          }
          const result = await mgr.submit(req);
          return result.ok
            ? { message: "下单成功", orderId: result.order.orderId, status: result.order.status }
            : { error: result.reason, ruleId: result.ruleId };
        }

        case "basket_order": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化，请先启动引擎" };

          const orderItems = args.orders as Array<Record<string, unknown>> | undefined;
          if (!orderItems || !Array.isArray(orderItems) || orderItems.length === 0) {
            return { error: "请提供 orders 列表（至少包含一个订单项）" };
          }

          const basketItems: BasketOrderItem[] = orderItems.map((item) => ({
            symbol: item.symbol as string,
            side: item.side as BasketOrderItem["side"],
            quantity: item.quantity as number | undefined,
            amount: item.amount as number | undefined,
            price: item.price as number | undefined,
          }));

          // Validate each item has symbol and side
          for (let i = 0; i < basketItems.length; i++) {
            const item = basketItems[i];
            if (!item.symbol || !item.side) {
              return { error: `第 ${i + 1} 项订单缺少 symbol 或 side` };
            }
            if (!item.quantity && !item.amount) {
              return { error: `第 ${i + 1} 项订单 (${item.symbol}) 需要提供 quantity 或 amount` };
            }
          }

          const basketResult = await mgr.submitBasket(basketItems);
          return {
            message: basketResult.basketRiskError
              ? `批量风控拦截: ${basketResult.basketRiskError}`
              : `篮子交易完成: ${basketResult.succeeded}/${basketResult.total} 成功`,
            ...basketResult,
          };
        }

        case "cancel_order": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化" };
          const orderId = args.order_id as string;
          if (!orderId) return { error: "请提供 order_id" };
          await mgr.cancel(orderId);
          return { message: `订单 ${orderId.slice(0, 8)} 已撤销` };
        }

        case "cancel_all_orders": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化" };
          const symbol = args.symbol as string | undefined;
          await mgr.cancelAll(symbol);
          return { message: symbol ? `${symbol} 的所有挂单已撤销` : "所有挂单已撤销" };
        }

        case "get_risk_snapshot": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const account = engine.getAccount();
          const positions = engine.getPositions();
          if (!account) return { error: "账户数据尚未同步，请等待引擎完成首次刷新" };

          const limits = config.tradingAccount?.riskLimits ?? {};
          const maxDailyLoss = limits.maxDailyLoss ?? 50_000;
          const maxOrderValue = limits.maxOrderValue ?? 500_000;
          const maxPositionPct = (limits.maxPositionPct ?? 0.3) * 100;

          const positionSummary = positions.map((p) => ({
            symbol: p.symbol,
            quantity: p.quantity,
            marketValue: p.marketValue?.toFixed(2),
            pct:
              account.totalAssets > 0
                ? `${(((p.marketValue ?? 0) / account.totalAssets) * 100).toFixed(1)}%`
                : "N/A",
          }));

          const totalLoss = account.profit < 0 ? -account.profit : 0;
          const mgr = engine.orderManager;
          const dailyLoss = mgr ? await mgr.getDailyPnl().then((p) => (p < 0 ? -p : 0)) : 0;

          // Compute max drawdown from equity history if available
          let maxDrawdown: string | undefined;
          let maxDrawdownPct: string | undefined;
          if (equityHistory) {
            const summary = equityHistory.getSummary();
            if (summary.totalDays > 0) {
              maxDrawdown = `¥${summary.maxDrawdown.toFixed(2)}`;
              maxDrawdownPct = `${summary.maxDrawdownPct.toFixed(2)}%`;
            }
          }

          return {
            status: engine.getStatus(),
            account: {
              cash: `¥${account.cash.toFixed(2)}`,
              totalAssets: `¥${account.totalAssets.toFixed(2)}`,
              profit: `¥${account.profit.toFixed(2)}`,
              profitPct: `${(account.profitPct * 100).toFixed(2)}%`,
            },
            limits: {
              maxOrderValue: `¥${maxOrderValue.toFixed(2)}`,
              maxPositionPct: `${maxPositionPct.toFixed(0)}%`,
              maxDailyLoss: `¥${maxDailyLoss.toFixed(2)}`,
            },
            usage: {
              totalLoss: `¥${totalLoss.toFixed(2)}`,
              dailyLoss: `¥${dailyLoss.toFixed(2)}`,
              dailyLossUsed:
                maxDailyLoss > 0 ? `${((dailyLoss / maxDailyLoss) * 100).toFixed(1)}%` : "N/A",
            },
            maxDrawdown,
            maxDrawdownPct,
            positions: positionSummary,
          };
        }

        case "list_confirmations": {
          const engine = getEngine();
          if (!engine) return { confirmations: [] };
          const mgr = engine.orderManager;
          if (!mgr) return { confirmations: [] };
          const items = await mgr.confirmationManager.listPending();
          return { total: items.length, confirmations: items };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
