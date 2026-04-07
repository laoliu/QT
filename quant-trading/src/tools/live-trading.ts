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
 *   cancel_order         — cancel a specific open order
 *   cancel_all_orders    — cancel all open orders
 *   get_risk_snapshot    — risk metrics on current live portfolio
 *   list_confirmations   — list pending human confirmation requests
 *   batch_order          — place multiple orders in batch
 *   execute_rebalance    — convert rebalance plan to live batch orders
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderRequest } from "../broker/adapter.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { EquityHistory } from "../services/equity-history.js";
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
      "控制实盘交易引擎。启动/停止引擎、管理实盘策略、手动下单、查看风控快照。" +
      "action=engine_status 查看状态, action=start_engine 启动引擎, " +
      "action=stop_engine 停止引擎, action=pause_engine 暂停, action=resume_engine 恢复, " +
      "action=add_strategy 添加策略, action=remove_strategy 移除策略, " +
      "action=list_strategies 列出策略, action=force_order 强制下单, " +
      "action=cancel_order 撤单, action=cancel_all_orders 全撤, " +
      "action=get_risk_snapshot 风控快照, action=list_confirmations 待确认列表, " +
      "action=batch_order 批量下单(传入orders数组), " +
      "action=execute_rebalance 将调仓计划转为实盘批量下单。",
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
            "cancel_order",
            "cancel_all_orders",
            "get_risk_snapshot",
            "list_confirmations",
            "batch_order",
            "execute_rebalance",
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
        // For batch_order
        orders: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "标的代码" },
              side: {
                type: "string",
                enum: ["buy", "sell", "short", "cover"],
                description: "买卖方向",
              },
              order_type: {
                type: "string",
                enum: ["market", "limit"],
                description: "订单类型(默认limit)",
              },
              quantity: { type: "number", description: "数量（股）" },
              price: { type: "number", description: "价格（limit order 必填）" },
            },
          },
          description:
            "批量下单列表 [{symbol, side, quantity, price?, order_type?}, ...]",
        },
        // For execute_rebalance
        targets: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              weight: { type: "number", description: "目标权重(百分比如30, 或小数如0.3)" },
            },
          },
          description: "调仓目标配置: [{symbol, weight}, ...]",
        },
        cash_reserve_pct: {
          type: "number",
          description: "保留现金比例(0-100, 默认5 = 5%)",
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

        case "batch_order": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化，请先启动引擎" };

          const orders = args.orders as
            | {
                symbol: string;
                side: string;
                quantity: number;
                price?: number;
                order_type?: string;
              }[]
            | undefined;
          if (!orders || orders.length === 0) {
            return { error: "请提供 orders 数组，每项包含 symbol, side, quantity" };
          }

          const results: {
            symbol: string;
            ok: boolean;
            orderId?: string;
            error?: string;
          }[] = [];

          // Submit sell orders first (to free up cash), then buy orders
          const sellOrders = orders.filter(
            (o) => o.side === "sell" || o.side === "short" || o.side === "cover",
          );
          const buyOrders = orders.filter((o) => o.side === "buy");
          const orderedList = [...sellOrders, ...buyOrders];

          for (const o of orderedList) {
            if (!o.symbol || !o.side || !o.quantity) {
              results.push({
                symbol: o.symbol ?? "unknown",
                ok: false,
                error: "缺少 symbol, side 或 quantity",
              });
              continue;
            }
            const orderType = (o.order_type as "market" | "limit") ?? "limit";
            if (orderType === "limit" && !o.price) {
              results.push({ symbol: o.symbol, ok: false, error: "限价单请提供 price" });
              continue;
            }

            const req: OrderRequest = {
              symbol: o.symbol,
              market: detectMarketFromSymbol(o.symbol),
              side: o.side as "buy" | "sell" | "short" | "cover",
              orderType,
              quantity: o.quantity,
              price: o.price,
              strategyId: "batch_manual",
            };
            const res = await mgr.submit(req);
            if (res.ok) {
              results.push({ symbol: o.symbol, ok: true, orderId: res.order.orderId });
            } else {
              results.push({ symbol: o.symbol, ok: false, error: res.reason });
            }
          }

          const successCount = results.filter((r) => r.ok).length;
          return {
            message: `批量下单完成: ${successCount}/${results.length} 成功`,
            total: results.length,
            success: successCount,
            failed: results.length - successCount,
            details: results,
          };
        }

        case "execute_rebalance": {
          const engine = getEngine();
          if (!engine) return { error: "引擎未初始化" };
          const mgr = engine.orderManager;
          if (!mgr) return { error: "OrderManager 未初始化，请先启动引擎" };

          const rawTargets = args.targets as
            | { symbol: string; weight: number }[]
            | undefined;
          if (!rawTargets || rawTargets.length === 0) {
            return { error: "请提供 targets (目标配置), 格式: [{symbol, weight}, ...]" };
          }

          const cashReservePctRaw = (args.cash_reserve_pct as number) ?? 5;
          const cashReservePct =
            cashReservePctRaw > 1 ? cashReservePctRaw / 100 : cashReservePctRaw;

          // Normalize weights
          const targets = rawTargets.map((t) => ({
            symbol: t.symbol,
            weight: t.weight > 1 ? t.weight / 100 : t.weight,
          }));
          const totalWeight = targets.reduce((sum, t) => sum + t.weight, 0);
          if (Math.abs(totalWeight - 1.0) > 0.05) {
            return {
              error: `目标权重总和为 ${(totalWeight * 100).toFixed(1)}%, 应接近100%`,
            };
          }

          // Get account and positions
          let totalAssets: number;
          let positions: { symbol: string; quantity: number; marketValue: number }[];
          try {
            const [account, pos] = await Promise.all([
              mgr.broker.queryAccount(),
              mgr.broker.queryPositions(),
            ]);
            totalAssets =
              account.totalAssets ||
              account.cash + pos.reduce((s, p) => s + (p.marketValue ?? 0), 0);
            positions = pos.map((p) => ({
              symbol: p.symbol,
              quantity: p.quantity ?? 0,
              marketValue: p.marketValue ?? 0,
            }));
          } catch (err) {
            return { error: `无法获取账户信息: ${(err as Error).message}` };
          }

          const investableAssets = totalAssets * (1 - cashReservePct);

          // Get current prices for target symbols
          const { createDataProvider } = await import(
            "../data/market-data-provider.js"
          );
          const provider = createDataProvider(config);
          const prices = new Map<string, number>();
          for (const t of targets) {
            try {
              const market = detectMarketFromSymbol(t.symbol);
              const quote = await provider.getQuote(t.symbol, market as never);
              if (quote?.price) prices.set(t.symbol, quote.price);
            } catch {
              // skip
            }
          }

          // Compute trades
          interface RebalTrade {
            symbol: string;
            side: "buy" | "sell";
            quantity: number;
            reason: string;
          }
          const trades: RebalTrade[] = [];

          for (const target of targets) {
            const price = prices.get(target.symbol);
            if (!price || price <= 0) {
              trades.push({
                symbol: target.symbol,
                side: "buy",
                quantity: 0,
                reason: `无法获取 ${target.symbol} 的价格`,
              });
              continue;
            }
            const targetValue = investableAssets * target.weight;
            const currentPos = positions.find((p) => p.symbol === target.symbol);
            const currentValue = currentPos?.marketValue ?? 0;
            const diff = targetValue - currentValue;
            const rawQty = Math.abs(diff) / price;
            const quantity = Math.floor(rawQty / 100) * 100;
            if (quantity >= 100) {
              trades.push({
                symbol: target.symbol,
                side: diff > 0 ? "buy" : "sell",
                quantity,
                reason: `目标${(target.weight * 100).toFixed(0)}% (¥${targetValue.toFixed(0)}) vs 当前¥${currentValue.toFixed(0)}`,
              });
            }
          }

          // Positions not in targets → sell all
          for (const pos of positions) {
            if (
              pos.quantity > 0 &&
              !targets.some((t) => t.symbol === pos.symbol)
            ) {
              trades.push({
                symbol: pos.symbol,
                side: "sell",
                quantity: pos.quantity,
                reason: `不在目标配置中, 清仓`,
              });
            }
          }

          if (
            trades.length === 0 ||
            trades.every((t) => t.quantity === 0)
          ) {
            return { message: "当前持仓已接近目标配置，无需调仓" };
          }

          // Execute: sell first, then buy
          const results: {
            symbol: string;
            side: string;
            quantity: number;
            ok: boolean;
            reason: string;
            error?: string;
          }[] = [];

          const sellTrades = trades.filter(
            (t) => t.side === "sell" && t.quantity > 0,
          );
          const buyTrades = trades.filter(
            (t) => t.side === "buy" && t.quantity > 0,
          );

          for (const t of [...sellTrades, ...buyTrades]) {
            const market = detectMarketFromSymbol(t.symbol);
            const res = await mgr.submit({
              symbol: t.symbol,
              market,
              side: t.side,
              orderType: "market",
              quantity: t.quantity,
              strategyId: "rebalance",
              signalId: `rebal_${Date.now()}_${t.symbol}`,
            });
            results.push({
              symbol: t.symbol,
              side: t.side,
              quantity: t.quantity,
              ok: res.ok,
              reason: t.reason,
              error: res.ok ? undefined : res.reason,
            });
          }

          const successCount = results.filter((r) => r.ok).length;
          return {
            message: `调仓执行完成: ${successCount}/${results.length} 笔成功`,
            totalAssets: `¥${totalAssets.toLocaleString()}`,
            investableAssets: `¥${investableAssets.toLocaleString()}`,
            cashReservePct: `${(cashReservePct * 100).toFixed(0)}%`,
            total: results.length,
            success: successCount,
            failed: results.length - successCount,
            details: results,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
