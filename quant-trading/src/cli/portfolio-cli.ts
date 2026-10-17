/**
 * CLI command: openclaw quant portfolio
 *
 * Shows current portfolio and can place simple buy/sell orders
 * by calling the running gateway's HTTP API.
 * Port: --port option → APP_PORT env → default 18789.
 */
import type { QuantTradingConfig } from "../types.js";

function gatewayBase(port?: number): string {
  const p = port ?? Number(process.env["APP_PORT"] ?? "18789");
  return `http://127.0.0.1:${p}`;
}

async function callApi(
  path: string,
  opts?: { method?: string; body?: unknown; port?: number; apiToken?: string },
): Promise<{ ok: boolean; data: unknown; error: string }> {
  const url = `${gatewayBase(opts?.port)}${path}`;
  try {
    const headers: Record<string, string> = {};
    if (opts?.body) headers["Content-Type"] = "application/json";
    if (opts?.apiToken) headers["Authorization"] = `Bearer ${opts.apiToken}`;
    const res = await fetch(url, {
      method: opts?.method ?? "GET",
      headers: Object.keys(headers).length > 0 ? headers : undefined,
      body: opts?.body ? JSON.stringify(opts.body) : undefined,
    });
    const json = await res.json();
    return { ok: res.ok, data: json, error: "" };
  } catch (err) {
    const msg = (err as Error).message;
    if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
      return {
        ok: false,
        data: null,
        error:
          "无法连接到网关。请确认网关正在运行：\n  openclaw gateway run\n\n" +
          `尝试连接: ${gatewayBase(opts?.port)}`,
      };
    }
    return { ok: false, data: null, error: msg };
  }
}

export async function runCliPortfolio(
  action: string,
  opts: { symbol?: string; quantity?: number; price?: number; port?: number },
  _config: QuantTradingConfig,
): Promise<void> {
  const apiToken = _config.apiToken;
  switch (action) {
    case "show": {
      const { ok, data, error } = await callApi("/quant/api/portfolio", {
        port: opts.port,
        apiToken,
      });
      if (!ok || !data) {
        console.error(`✗ ${error || "无法获取持仓数据"}`);
        return;
      }
      const d = data as Record<string, unknown>;
      const portfolio = (d["data"] as Record<string, unknown>) ?? d;
      const cash = (portfolio["cash"] as number) ?? 0;
      const positions = (portfolio["positions"] as Array<Record<string, unknown>>) ?? [];
      const totalValue = (portfolio["totalValue"] as number) ?? cash;

      console.log(`\n─── 当前持仓 ───`);
      console.log(`  现金:   ¥${cash.toFixed(2)}`);
      console.log(`  总资产: ¥${totalValue.toFixed(2)}`);

      if (positions.length === 0) {
        console.log(`  股票:   (无持仓)`);
      } else {
        console.log(
          `\n  ${"代码".padEnd(12)} ${"数量".padStart(8)} ${"成本价".padStart(10)} ${"现价".padStart(10)} ${"市值".padStart(12)} ${"盈亏".padStart(10)}`,
        );
        console.log(`  ${"─".repeat(66)}`);
        for (const p of positions) {
          const sym = String(p["symbol"] ?? p["code"] ?? "").padEnd(12);
          const qty = String(p["quantity"] ?? p["qty"] ?? 0).padStart(8);
          const cost = `¥${Number(p["avgCost"] ?? p["cost"] ?? 0).toFixed(2)}`.padStart(10);
          const price = `¥${Number(p["currentPrice"] ?? p["price"] ?? 0).toFixed(2)}`.padStart(10);
          const mv = `¥${Number(p["marketValue"] ?? 0).toFixed(2)}`.padStart(12);
          const pnl = Number(p["unrealizedPnl"] ?? p["pnl"] ?? 0);
          const pnlStr = (pnl >= 0 ? "+" : "") + `¥${pnl.toFixed(2)}`;
          console.log(`  ${sym} ${qty} ${cost} ${price} ${mv} ${pnlStr.padStart(10)}`);
        }
      }
      console.log("");
      break;
    }

    case "add": {
      if (!opts.symbol) {
        console.log(
          "Usage: openclaw quant portfolio add --symbol <code> --quantity <n> [--price <p>]",
        );
        return;
      }
      const order = {
        symbol: opts.symbol,
        direction: "buy" as const,
        orderType: opts.price ? ("limit" as const) : ("market" as const),
        quantity: opts.quantity ?? 100,
        price: opts.price,
      };
      console.log(
        `下单买入: ${order.symbol} × ${order.quantity} @ ${order.price ? `¥${order.price}` : "市价"}`,
      );
      const { ok, data, error } = await callApi("/quant/api/orders", {
        method: "POST",
        body: order,
        port: opts.port,
        apiToken,
      });
      if (!ok) {
        console.error(`✗ 下单失败: ${error}`);
        return;
      }
      const d = data as Record<string, unknown>;
      const result = (d["data"] as Record<string, unknown>) ?? d;
      console.log(`✓ 订单已提交 — ID: ${result["orderId"] ?? result["id"] ?? "—"}`);
      break;
    }

    case "remove": {
      if (!opts.symbol) {
        console.log(
          "Usage: openclaw quant portfolio remove --symbol <code> --quantity <n> [--price <p>]",
        );
        return;
      }
      const order = {
        symbol: opts.symbol,
        direction: "sell" as const,
        orderType: opts.price ? ("limit" as const) : ("market" as const),
        quantity: opts.quantity ?? 100,
        price: opts.price,
      };
      console.log(
        `下单卖出: ${order.symbol} × ${order.quantity} @ ${order.price ? `¥${order.price}` : "市价"}`,
      );
      const { ok, data, error } = await callApi("/quant/api/orders", {
        method: "POST",
        body: order,
        port: opts.port,
        apiToken,
      });
      if (!ok) {
        console.error(`✗ 下单失败: ${error}`);
        return;
      }
      const d = data as Record<string, unknown>;
      const result = (d["data"] as Record<string, unknown>) ?? d;
      console.log(`✓ 订单已提交 — ID: ${result["orderId"] ?? result["id"] ?? "—"}`);
      break;
    }

    case "rebalance":
      console.log(
        "再平衡请使用 AI Agent:\n" + '  openclaw agent --message "帮我按照目标权重再平衡组合"',
      );
      break;
    default:
      console.log(`Unknown action: ${action}`);
      console.log("Usage: openclaw quant portfolio [show|add|remove|rebalance]");
      console.log("  show                         — 查看当前持仓");
      console.log("  add  --symbol 600519 --quantity 100 [--price 1800]");
      console.log("  remove --symbol 600519 --quantity 100 [--price 1800]");
  }
}
