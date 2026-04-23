/**
 * Swing Overlay API routes (持仓波段套利)
 *
 * Provides endpoints for:
 *  - Scanning portfolio holdings for swing suitability
 *  - Activating swing overlay (creates grid conditional orders)
 *  - Deactivating swing overlay (removes grid conditional orders)
 *  - Querying active swing overlay status
 */
import { runPythonScript } from "../../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../../data/market-data-provider.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { json, parseObjectBody } from "./_helpers.js";

/** Compute YYYY-MM-DD date string offset from today. */
function dateOffset(daysBack: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return d.toISOString().slice(0, 10);
}

export function registerSwingOverlayRoutes(deps: AdminRouteDeps): void {
  const { api, config, conditionalOrderService, secure, getEngine } = deps;

  // ── Scan holdings for swing suitability ─────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/swing-overlay/scan",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = (body.symbols as string[]) ?? [];
        const symbolNames = (body.symbol_names as Record<string, string>) ?? {};
        const lookbackDays = (body.lookback_days as number) ?? 60;

        if (!symbols.length) {
          json(res, { error: "symbols 不能为空" }, 400);
          return;
        }

        // Fetch kline data for each symbol (parallel with per-symbol timeout)
        const provider = createDataProvider(config);
        const stockData: Record<string, { name: string; klines: unknown[] }> = {};
        const endDate = dateOffset(0);
        const startDate = dateOffset(Math.ceil(lookbackDays * 1.8) + 30);
        const targetSymbols = symbols.slice(0, 20);
        const PER_SYM_TIMEOUT = 20_000; // 20s per symbol max

        const timedGetKlines = (sym: string) =>
          new Promise<{ sym: string; klines: unknown[] }>((resolve) => {
            const market = detectMarketFromSymbol(sym);
            const timer = setTimeout(() => {
              console.log(`[swing-overlay/scan] ⚠ ${sym} (${market}) K线获取超时 (${PER_SYM_TIMEOUT / 1000}s), 跳过`);
              resolve({ sym, klines: [] });
            }, PER_SYM_TIMEOUT);
            provider
              .getKlines(sym, "1d", startDate, endDate, market)
              .then((klines) => {
                clearTimeout(timer);
                resolve({ sym, klines: klines ?? [] });
              })
              .catch(() => {
                clearTimeout(timer);
                resolve({ sym, klines: [] });
              });
          });

        // Fetch all in parallel (all at once — timeout protects against hangs)
        const fetched = await Promise.all(targetSymbols.map(timedGetKlines));
        for (const { sym, klines } of fetched) {
          if (klines.length > 0) {
            stockData[sym] = { name: symbolNames[sym] ?? sym, klines };
          }
        }

        // Run Python screener
        const result = await runPythonScript<{ results: unknown[] }>("scripts/swing_suitability_scan.py", {
          stock_data: stockData,
          lookback: lookbackDays,
        }, config);

        if (result.ok && result.data?.results) {
          json(res, { ok: true, results: result.data.results });
        } else {
          // Fallback: return basic results based on available data
          json(res, { ok: true, results: [] });
        }
      } catch (e) {
        json(res, { error: `扫描失败: ${String(e)}` }, 500);
      }
    }),
  });

  // ── Activate swing overlay for a symbol ─────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/swing-overlay/activate",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbol = body.symbol as string;
        const params = body.params as Record<string, number> | undefined;

        if (!symbol) {
          json(res, { error: "symbol 不能为空" }, 400);
          return;
        }

        const coreRatio = params?.core_ratio ?? 0.6;
        const gridPct = params?.grid_pct ?? 3.0;
        const gridCount = params?.grid_count ?? 3;
        const biasBuy = params?.bias_buy ?? -5.0;
        const biasSell = params?.bias_sell ?? 3.5;

        // Fee warning
        if (gridPct < 1.5) {
          json(res, {
            ok: false,
            error: `网格间距 ${gridPct}% 低于手续费成本线 (≈0.1%/往返)，建议 ≥ 1.5%`,
          });
          return;
        }

        // Remove existing swing grid orders for this symbol
        conditionalOrderService.removeSwingGridOrders(symbol);

        // Get current price for grid center
        const provider = createDataProvider(config);
        let currentPrice = 0;
        let maCenter = 0;
        try {
          const klines = await provider.getKlines(symbol, "1d", dateOffset(40), dateOffset(0), detectMarketFromSymbol(symbol));
          if (klines && klines.length > 0) {
            currentPrice = klines[klines.length - 1]!.close;
            // Compute MA20 for grid center
            const closes = klines.map((k: { close: number }) => k.close);
            const period = Math.min(20, closes.length);
            const maSlice = closes.slice(-period);
            maCenter = maSlice.reduce((a: number, b: number) => a + b, 0) / maSlice.length;
          }
        } catch {
          json(res, { error: "无法获取行情数据" }, 500);
          return;
        }

        if (currentPrice <= 0 || maCenter <= 0) {
          json(res, { error: "无法获取有效价格数据" }, 500);
          return;
        }

        // Build grid levels around current price (not MA center).
        // MA center is recorded for reference but using it as the grid center
        // causes all sell grids to instantly trigger when price is above MA.
        const gridCenter = currentPrice;
        const gridOrders: string[] = [];
        let skipped = 0;
        for (let i = -gridCount; i <= gridCount; i++) {
          if (i === 0) continue; // Skip center level
          const gridPrice = gridCenter * (1 + i * (gridPct / 100));
          if (gridPrice <= 0) continue;

          const direction = i < 0 ? "buy" : "sell";

          // Skip grid levels that would instantly trigger
          if (direction === "sell" && gridPrice <= currentPrice) { skipped++; continue; }
          if (direction === "buy" && gridPrice >= currentPrice) { skipped++; continue; }

          const quantity = 100; // Minimum lot for A-shares; actual sizing computed by user

          const orderId = conditionalOrderService.addSwingGrid({
            symbol,
            gridPrice,
            gridIndex: i + gridCount, // 0-based from lowest
            swingDirection: direction as "buy" | "sell",
            quantity,
            entryPrice: currentPrice,
            strategyId: "swing-overlay",
            gridPct,
            description:
              `波段${direction === "buy" ? "低吸" : "高抛"}: ${symbol} 第${Math.abs(i)}层${direction === "buy" ? "下" : "上"}网格 ¥${gridPrice.toFixed(2)}`,
          });
          gridOrders.push(orderId);
        }

        json(res, {
          ok: true,
          symbol,
          grid_orders: gridOrders,
          grid_count: gridOrders.length,
          skipped_instant: skipped,
          grid_center: gridCenter,
          ma_center: maCenter,
          current_price: currentPrice,
          params: { coreRatio, gridPct, gridCount, biasBuy, biasSell },
        });
      } catch (e) {
        json(res, { error: `激活失败: ${String(e)}` }, 500);
      }
    }),
  });

  // ── Deactivate swing overlay for a symbol ───────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/swing-overlay/deactivate",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbol = body.symbol as string;
        if (!symbol) {
          json(res, { error: "symbol 不能为空" }, 400);
          return;
        }

        const removedCount = conditionalOrderService.removeSwingGridOrders(symbol);
        json(res, { ok: true, symbol, removed_count: removedCount });
      } catch (e) {
        json(res, { error: `停止失败: ${String(e)}` }, 500);
      }
    }),
  });

  // ── Get status of all active swing overlays ─────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/swing-overlay/status",
    handler: secure(async (_req, res) => {
      try {
        // Get ALL swing orders (including triggered) for accurate status
        const allSwingOrders = conditionalOrderService.listAllSwingGridOrders();
        const activeSwingOrders = conditionalOrderService.listSwingGridOrders();

        // Group by symbol (use all orders for display, active for grid counts)
        const allBySymbol = new Map<string, typeof allSwingOrders>();
        for (const order of allSwingOrders) {
          const list = allBySymbol.get(order.symbol) ?? [];
          list.push(order);
          allBySymbol.set(order.symbol, list);
        }
        // Also include symbols that only have trade history (all orders cleaned up)
        const tradeLog = conditionalOrderService.getSwingTradeLog();
        for (const t of tradeLog) {
          if (!allBySymbol.has(t.symbol)) {
            allBySymbol.set(t.symbol, []);
          }
        }

        const overlays: Array<{
          symbol: string;
          name: string;
          current_price: number;
          ma_center: number;
          bias: number;
          core_shares: number;
          swing_shares: number;
          swing_total: number;
          active_grid_orders: number;
          grid_levels: number[];
          filled_grids: Record<number, boolean>;
          round_trips: number;
          swing_pnl: number;
        }> = [];

        const provider = createDataProvider(config);

        // Fetch broker/paper positions for core-shares lookup
        const engine = getEngine();
        const positions = engine.getPositions();
        const posMap = new Map<string, { quantity: number; name: string }>();
        for (const p of positions) {
          posMap.set(p.symbol, { quantity: p.quantity, name: p.name ?? p.symbol });
        }

        for (const [symbol, orders] of allBySymbol) {
          // Get active (non-triggered) orders for this symbol
          const activeOrders = orders.filter((o) => !o.triggered);

          let currentPrice = 0;
          try {
            const klines = await provider.getKlines(symbol, "1d", dateOffset(30), dateOffset(0), detectMarketFromSymbol(symbol));
            if (klines?.length) {
              currentPrice = klines[klines.length - 1]!.close;
            }
          } catch {
            // best-effort
          }

          const gridLevels = activeOrders
            .map((o) => o.gridPrice ?? o.triggerValue)
            .filter((p) => p > 0)
            .sort((a, b) => a - b);
          const centerLevel = gridLevels.length
            ? gridLevels[Math.floor(gridLevels.length / 2)]!
            : currentPrice;
          const bias = centerLevel > 0 ? ((currentPrice - centerLevel) / centerLevel) * 100 : 0;

          const filledGrids: Record<number, boolean> = {};
          for (const o of orders) {
            if (o.triggered) {
              filledGrids[o.gridIndex ?? 0] = true;
            }
          }

          // Compute PnL from persistent trade log
          const pnlSummary = conditionalOrderService.getSwingPnlSummary(symbol);

          // Position data: core shares from broker/paper, swing shares from trade log
          const pos = posMap.get(symbol);
          const totalHeld = pos?.quantity ?? 0;
          const posName = pos?.name ?? symbol;

          // Swing shares = net buys - net sells from grid trades
          const symbolTrades = conditionalOrderService.getSwingTradeLog(symbol);
          let swingNetShares = 0;
          for (const t of symbolTrades) {
            swingNetShares += t.direction === "buy" ? t.quantity : -t.quantity;
          }
          const swingShares = Math.max(0, swingNetShares);
          const coreShares = Math.max(0, totalHeld - swingShares);

          overlays.push({
            symbol,
            name: posName,
            current_price: currentPrice,
            ma_center: centerLevel,
            bias,
            core_shares: coreShares,
            swing_shares: swingShares,
            swing_total: totalHeld,
            active_grid_orders: activeOrders.length,
            grid_levels: gridLevels,
            filled_grids: filledGrids,
            round_trips: pnlSummary.roundTrips,
            swing_pnl: pnlSummary.pnlPct,
          });
        }

        json(res, { ok: true, overlays });
      } catch (e) {
        json(res, { error: `查询失败: ${String(e)}` }, 500);
      }
    }),
  });
}
