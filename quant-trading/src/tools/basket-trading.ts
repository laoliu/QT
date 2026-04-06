/**
 * basket_trading tool — AI-callable interface for basket (batch) order management.
 *
 * Allows a trader to:
 *   - Create and manage basket templates (reusable stock groups)
 *   - Execute a basket of orders in one shot (buy/sell multiple stocks)
 *   - Allocate by amount or weight across basket items
 *   - Preview execution plan before committing (dry run)
 *   - Cancel all orders in a basket at once
 *   - View basket execution history
 *
 * Actions:
 *   create_template    — save a reusable basket template
 *   list_templates     — list saved templates
 *   delete_template    — remove a template
 *   update_template    — modify a template
 *   preview            — dry-run: show what orders would be placed
 *   execute            — submit all basket orders to the broker
 *   execute_template   — execute a saved template
 *   cancel_basket      — cancel all orders in a basket
 *   list_baskets       — view basket execution history
 *   get_basket         — view a specific basket's status
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderRequest } from "../broker/adapter.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BasketOrderService } from "../services/basket-order.js";
import type { FavoritesStore } from "../services/favorites-store.js";
import type { BasketItem, QuantTradingConfig } from "../types.js";

export function createBasketTradingTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  basketService: BasketOrderService,
  getEngine: () => LiveEngine | null,
  favoritesStore?: FavoritesStore,
) {
  // Shared quote fetcher
  const provider = createDataProvider(config);
  async function fetchQuote(symbol: string) {
    try {
      const market = detectMarketFromSymbol(symbol);
      return await provider.getQuote(symbol, market as never);
    } catch {
      return null;
    }
  }

  // Shared order submitter (delegates to live engine's OrderManager)
  function getSubmitter(): ((req: OrderRequest) => Promise<{ ok: true; orderId: string } | { ok: false; reason: string }>) | null {
    const engine = getEngine();
    if (!engine) return null;
    const mgr = engine.orderManager;
    if (!mgr) return null;
    return async (req: OrderRequest) => {
      const result = await mgr.submit(req);
      if (result.ok) {
        return { ok: true as const, orderId: result.order.orderId };
      }
      return { ok: false as const, reason: result.reason };
    };
  }

  return {
    name: "basket_trading",
    description:
      "篮子交易工具 — 批量下单、模板管理、一键买卖多只股票。\n" +
      "action=create_template 创建篮子模板, action=list_templates 列出模板, " +
      "action=delete_template 删除模板, action=update_template 修改模板, " +
      "action=clone_template 复制模板, " +
      "action=preview 预览篮子下单计划(不下单), action=execute 执行篮子下单, " +
      "action=execute_template 执行已保存的模板, action=cancel_basket 撤销整个篮子, " +
      "action=list_baskets 查看篮子执行历史, action=get_basket 查看篮子详情, " +
      "action=basket_from_portfolio 从当前持仓创建篮子(一键清仓/批量卖出), " +
      "action=basket_from_favorites 从自选股创建篮子, " +
      "action=reverse 反转篮子买卖方向, action=adjust 批量调整数量/价格。\n\n" +
      "items 格式: [{symbol:'600519',side:'buy',quantity:100}, {symbol:'000001',side:'buy',amount:50000}]\n" +
      "支持按数量(quantity)、按金额(amount)、按权重(weight+total_amount)三种方式指定。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "create_template",
            "list_templates",
            "delete_template",
            "update_template",
            "clone_template",
            "preview",
            "execute",
            "execute_template",
            "cancel_basket",
            "list_baskets",
            "get_basket",
            "basket_from_portfolio",
            "basket_from_favorites",
            "reverse",
            "adjust",
          ],
          description: "操作类型",
        },
        name: { type: "string", description: "篮子名称（用于 create_template/execute）" },
        description: { type: "string", description: "篮子描述（用于 create_template）" },
        items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              symbol: { type: "string", description: "股票代码" },
              name: { type: "string", description: "股票名称(可选)" },
              side: { type: "string", enum: ["buy", "sell"], description: "买卖方向(默认buy)" },
              quantity: { type: "number", description: "数量(股)" },
              amount: { type: "number", description: "金额(元，与quantity二选一)" },
              price: { type: "number", description: "限价(可选，不填为市价)" },
              weight: { type: "number", description: "权重(0-1，与total_amount配合使用)" },
            },
          },
          description: "篮子中的股票列表",
        },
        total_amount: {
          type: "number",
          description: "总金额(元)，配合items中的weight按比例分配",
        },
        template_id: { type: "string", description: "模板ID（用于 execute_template/delete/update/clone）" },
        basket_id: { type: "string", description: "篮子ID（用于 cancel_basket/get_basket）" },
        side: { type: "string", enum: ["buy", "sell"], description: "买卖方向（用于 basket_from_portfolio/basket_from_favorites）" },
        category: { type: "string", description: "自选股分类（用于 basket_from_favorites，可选）" },
        tags: { type: "array", items: { type: "string" }, description: "自选股标签（用于 basket_from_favorites，可选）" },
        quantity_multiplier: { type: "number", description: "数量/金额倍数（用于 adjust，如0.5=减半，2=翻倍）" },
        price_multiplier: { type: "number", description: "价格倍数（用于 adjust，如1.02=涨2%）" },
        price_offset: { type: "number", description: "价格偏移量（用于 adjust，如-0.5=降0.5元）" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        // ── Template management ──────────────────────────────

        case "create_template": {
          const name = args.name as string;
          const items = args.items as BasketItem[] | undefined;
          const description = args.description as string | undefined;
          if (!name) return { error: "请提供篮子名称 name" };
          if (!items || items.length === 0) return { error: "请提供至少一个标的 items" };
          const normalized = items.map((it) => ({
            ...it,
            side: it.side || ("buy" as const),
          }));
          const tpl = await basketService.createTemplate(name, normalized, description);
          return {
            message: `篮子模板已创建`,
            templateId: tpl.templateId,
            name: tpl.name,
            itemCount: tpl.items.length,
          };
        }

        case "list_templates": {
          const templates = await basketService.listTemplates();
          if (templates.length === 0) return { templates: [], message: "暂无保存的篮子模板" };
          return {
            total: templates.length,
            templates: templates.map((t) => ({
              templateId: t.templateId,
              name: t.name,
              description: t.description,
              itemCount: t.items.length,
              symbols: t.items.map((i) => `${i.side === "sell" ? "卖" : "买"}${i.symbol}`).join(", "),
              updatedAt: t.updatedAt,
            })),
          };
        }

        case "delete_template": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const ok = await basketService.deleteTemplate(tid);
          return ok ? { message: `模板 ${tid} 已删除` } : { error: `未找到模板 ${tid}` };
        }

        case "update_template": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const items = args.items as BasketItem[] | undefined;
          const normalized = items?.map((it) => ({ ...it, side: it.side || ("buy" as const) }));
          const updated = await basketService.updateTemplate(tid, {
            name: args.name as string | undefined,
            description: args.description as string | undefined,
            items: normalized,
          });
          return updated
            ? { message: `模板已更新`, templateId: updated.templateId, name: updated.name }
            : { error: `未找到模板 ${tid}` };
        }

        // ── Basket preview & execution ───────────────────────

        case "preview": {
          const name = (args.name as string) ?? "预览篮子";
          const items = args.items as BasketItem[] | undefined;
          const totalAmount = args.total_amount as number | undefined;
          if (!items || items.length === 0) return { error: "请提供 items" };
          const normalized = items.map((it) => ({ ...it, side: it.side || ("buy" as const) }));

          const basket = await basketService.createBasketOrder({
            name,
            items: normalized,
            totalAmount,
            dryRun: true,
            fetchQuote,
          });

          const lines: string[] = [`篮子预览: ${name} (${basket.basketId})\n`];
          let totalCost = 0;
          for (const r of basket.results ?? []) {
            const cost = r.requestedQty * (r.price ?? 0);
            totalCost += r.side === "buy" ? cost : -cost;
            const icon = r.status === "skipped" ? "⚪" : r.side === "sell" ? "🔴" : "🟢";
            const priceStr = r.price ? `@ ¥${r.price.toFixed(2)}` : "@ 市价";
            lines.push(
              r.status === "skipped"
                ? `  ${icon} ${r.symbol} — 跳过: ${r.reason}`
                : `  ${icon} ${r.side === "sell" ? "卖出" : "买入"} ${r.symbol} ${r.requestedQty}股 ${priceStr} ≈ ¥${cost.toFixed(0)}`,
            );
          }
          lines.push(`\n预计总金额: ¥${Math.abs(totalCost).toLocaleString()}`);
          lines.push("使用 action=execute 执行下单");

          return {
            basketId: basket.basketId,
            preview: lines.join("\n"),
            itemCount: basket.results?.length ?? 0,
            estimatedCost: Math.abs(totalCost),
          };
        }

        case "execute": {
          const name = (args.name as string) ?? "篮子下单";
          const items = args.items as BasketItem[] | undefined;
          const totalAmount = args.total_amount as number | undefined;
          if (!items || items.length === 0) return { error: "请提供 items" };
          const normalized = items.map((it) => ({ ...it, side: it.side || ("buy" as const) }));

          const submitOrder = getSubmitter();
          if (!submitOrder) {
            return { error: "交易引擎未启动，请先启动实盘引擎 (live_trading action=start_engine)" };
          }

          const basket = await basketService.createBasketOrder({
            name,
            items: normalized,
            totalAmount,
            dryRun: false,
            fetchQuote,
            submitOrder,
          });

          return formatBasketResult(basket);
        }

        case "execute_template": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const tpl = await basketService.getTemplate(tid);
          if (!tpl) return { error: `未找到模板 ${tid}` };

          const totalAmount = args.total_amount as number | undefined;
          const submitOrder = getSubmitter();
          if (!submitOrder) {
            return { error: "交易引擎未启动，请先启动实盘引擎" };
          }

          const basket = await basketService.createBasketOrder({
            name: tpl.name,
            items: tpl.items,
            totalAmount,
            dryRun: false,
            fetchQuote,
            submitOrder,
          });

          return formatBasketResult(basket);
        }

        // ── Basket cancel & history ──────────────────────────

        case "cancel_basket": {
          const bid = args.basket_id as string;
          if (!bid) return { error: "请提供 basket_id" };
          const engine = getEngine();
          if (!engine?.orderManager) {
            return { error: "交易引擎未启动" };
          }
          const mgr = engine.orderManager;
          const basket = await basketService.cancelBasket(bid, (orderId) => mgr.cancel(orderId));
          if (!basket) return { error: `未找到篮子 ${bid}` };
          return {
            message: `篮子 ${bid} 已撤销`,
            basketId: basket.basketId,
            status: basket.executionStatus,
          };
        }

        case "list_baskets": {
          const baskets = await basketService.listBasketOrders();
          if (baskets.length === 0) return { baskets: [], message: "暂无篮子执行记录" };
          return {
            total: baskets.length,
            baskets: baskets.map((b) => ({
              basketId: b.basketId,
              name: b.name,
              itemCount: b.items.length,
              status: b.executionStatus,
              submitted: b.results?.filter((r) => r.status === "submitted").length ?? 0,
              failed: b.results?.filter((r) => r.status === "failed").length ?? 0,
              createdAt: b.createdAt,
              executedAt: b.executedAt,
            })),
          };
        }

        case "get_basket": {
          const bid = args.basket_id as string;
          if (!bid) return { error: "请提供 basket_id" };
          const basket = await basketService.getBasketOrder(bid);
          if (!basket) return { error: `未找到篮子 ${bid}` };
          return {
            basketId: basket.basketId,
            name: basket.name,
            status: basket.executionStatus,
            createdAt: basket.createdAt,
            executedAt: basket.executedAt,
            results: basket.results?.map((r) => ({
              symbol: r.symbol,
              side: r.side,
              quantity: r.requestedQty,
              price: r.price ? `¥${r.price.toFixed(2)}` : "市价",
              status: r.status,
              orderId: r.orderId?.slice(0, 8),
              reason: r.reason,
            })),
          };
        }

        case "clone_template": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const newName = args.name as string | undefined;
          const cloned = await basketService.cloneTemplate(tid, newName);
          if (!cloned) return { error: `未找到模板 ${tid}` };
          return {
            message: "模板已复制",
            templateId: cloned.templateId,
            name: cloned.name,
            itemCount: cloned.items.length,
          };
        }

        // ── Basket from portfolio / favorites ────────────────

        case "basket_from_portfolio": {
          const engine = getEngine();
          if (!engine?.orderManager?.broker) {
            return { error: "交易引擎未启动，无法获取持仓" };
          }
          const positions = await engine.orderManager.broker.queryPositions();
          if (positions.length === 0) return { error: "当前无持仓" };

          const side = (args.side as string) ?? "sell";
          const items: BasketItem[] = positions
            .filter((p) => p.availableQty > 0)
            .map((p) => ({
              symbol: p.symbol,
              name: p.name,
              side: side as "buy" | "sell",
              quantity: p.availableQty,
            }));

          if (items.length === 0) return { error: "无可操作持仓（可能全部冻结）" };

          const basketName = (args.name as string) ?? (side === "sell" ? "一键清仓" : "持仓加仓");
          const tpl = await basketService.createTemplate(basketName, items);
          return {
            message: `已从持仓创建篮子模板 (${items.length}只)`,
            templateId: tpl.templateId,
            name: tpl.name,
            items: items.map((it) => ({
              symbol: it.symbol,
              name: it.name,
              side: it.side,
              quantity: it.quantity,
            })),
            hint: "使用 action=preview template_id=... 预览下单计划，或 action=execute_template 直接执行",
          };
        }

        case "basket_from_favorites": {
          if (!favoritesStore) {
            return { error: "自选股服务不可用" };
          }
          const category = args.category as string | undefined;
          const tags = args.tags as string[] | undefined;
          const allFavs = favoritesStore.listFavorites({ category, tags });
          if (allFavs.length === 0) {
            return { error: category || tags ? "没有匹配的自选股" : "自选股列表为空" };
          }

          const side = (args.side as string) ?? "buy";
          const items: BasketItem[] = allFavs.map((f) => ({
            symbol: f.code,
            name: f.name,
            side: side as "buy" | "sell",
          }));

          const label = category ? `自选-${category}` : "自选股篮子";
          const basketName = (args.name as string) ?? label;
          const tpl = await basketService.createTemplate(basketName, items);
          return {
            message: `已从自选股创建篮子模板 (${items.length}只)`,
            templateId: tpl.templateId,
            name: tpl.name,
            items: items.map((it) => ({ symbol: it.symbol, name: it.name, side: it.side })),
            hint: "请为每只股票补充 quantity 或 amount，然后 action=execute_template 执行",
          };
        }

        // ── Transform actions ────────────────────────────────

        case "reverse": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const tpl = await basketService.getTemplate(tid);
          if (!tpl) return { error: `未找到模板 ${tid}` };

          const reversed = basketService.reverseItems(tpl.items);
          const newName = (args.name as string) ?? `${tpl.name} (反转)`;
          const newTpl = await basketService.createTemplate(newName, reversed, tpl.description);
          return {
            message: `已创建反转模板`,
            templateId: newTpl.templateId,
            name: newTpl.name,
            items: reversed.map((it) => ({
              symbol: it.symbol,
              side: it.side,
              quantity: it.quantity,
              amount: it.amount,
            })),
          };
        }

        case "adjust": {
          const tid = args.template_id as string;
          if (!tid) return { error: "请提供 template_id" };
          const tpl = await basketService.getTemplate(tid);
          if (!tpl) return { error: `未找到模板 ${tid}` };

          const qtyMul = args.quantity_multiplier as number | undefined;
          const priceMul = args.price_multiplier as number | undefined;
          const priceOff = args.price_offset as number | undefined;

          if (qtyMul === undefined && priceMul === undefined && priceOff === undefined) {
            return { error: "请至少提供 quantity_multiplier、price_multiplier 或 price_offset 之一" };
          }

          const adjusted = basketService.adjustItems(tpl.items, {
            quantityMultiplier: qtyMul,
            priceMultiplier: priceMul,
            priceOffset: priceOff,
          });

          const updated = await basketService.updateTemplate(tid, { items: adjusted });
          if (!updated) return { error: "更新失败" };

          const changes: string[] = [];
          if (qtyMul !== undefined) changes.push(`数量×${qtyMul}`);
          if (priceMul !== undefined) changes.push(`价格×${priceMul}`);
          if (priceOff !== undefined) changes.push(`价格${priceOff >= 0 ? "+" : ""}${priceOff}`);

          return {
            message: `模板已调整 (${changes.join(", ")})`,
            templateId: updated.templateId,
            name: updated.name,
            items: adjusted.map((it) => ({
              symbol: it.symbol,
              side: it.side,
              quantity: it.quantity,
              amount: it.amount,
              price: it.price,
            })),
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Helpers ────────────────────────────────────────────────────────

function formatBasketResult(basket: import("../types.js").BasketOrder) {
  const submitted = basket.results?.filter((r) => r.status === "submitted") ?? [];
  const failed = basket.results?.filter((r) => r.status === "failed") ?? [];
  const skipped = basket.results?.filter((r) => r.status === "skipped") ?? [];

  const lines: string[] = [];
  for (const r of submitted) {
    lines.push(
      `✅ ${r.side === "sell" ? "卖出" : "买入"} ${r.symbol} ${r.requestedQty}股 — 已提交 [${r.orderId?.slice(0, 8)}]`,
    );
  }
  for (const r of failed) {
    lines.push(`❌ ${r.symbol} — 失败: ${r.reason}`);
  }
  for (const r of skipped) {
    lines.push(`⚪ ${r.symbol} — 跳过: ${r.reason}`);
  }

  return {
    basketId: basket.basketId,
    name: basket.name,
    status: basket.executionStatus,
    summary: `提交 ${submitted.length}, 失败 ${failed.length}, 跳过 ${skipped.length}`,
    details: lines.join("\n"),
  };
}
