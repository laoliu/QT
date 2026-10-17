/**
 * CLI command: openclaw quant live
 *
 * Controls the live trading engine by calling the running gateway's HTTP API.
 * The gateway must be running (`openclaw gateway run`) for most operations.
 * Port is resolved from: --port option → APP_PORT env → default 18789.
 */
import type { QuantTradingConfig } from "../types.js";

function gatewayBase(portOption?: number): string {
  const port = portOption ?? Number(process.env["APP_PORT"] ?? "18789");
  return `http://127.0.0.1:${port}`;
}

type ApiResponse = { status: string; data?: unknown; message?: string };

async function callApi(
  path: string,
  opts?: { method?: string; body?: unknown; port?: number; apiToken?: string },
): Promise<{ ok: boolean; data: ApiResponse | null; error: string }> {
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
    const json = (await res.json()) as ApiResponse;
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

export async function runCliLive(
  action: string,
  opts: {
    strategy?: string;
    symbols?: string;
    broker?: string;
    status?: string;
    port?: number;
  },
  _config: QuantTradingConfig,
): Promise<void> {
  const port = opts.port;
  const apiToken = _config.apiToken;

  switch (action) {
    case "start": {
      if (!opts.strategy) {
        console.error("Error: --strategy <id> is required");
        console.log(
          "Usage: openclaw quant live start --strategy dual-ma-crossover --symbols 600519,000001",
        );
        process.exit(1);
      }
      // Step 1: start engine
      const startRes = await callApi("/quant/api/engine/start", {
        method: "POST",
        port,
        apiToken,
        body: opts.broker ? { broker: opts.broker } : {},
      });
      if (!startRes.ok) {
        console.error(startRes.error || JSON.stringify(startRes.data));
        process.exit(1);
      }
      console.log("引擎启动中…");

      // Step 2: add strategy
      const symbols = (opts.symbols ?? "000300").split(",").map((s) => s.trim());
      const addRes = await callApi("/quant/api/strategies", {
        method: "POST",
        port,
        apiToken,
        body: { strategyId: opts.strategy, symbols },
      });
      if (!addRes.ok) {
        console.error(addRes.error || JSON.stringify(addRes.data));
        process.exit(1);
      }
      console.log(`✓ 策略已添加: ${opts.strategy}  符号: ${symbols.join(", ")}`);
      console.log("运行 `openclaw quant live status` 查看状态");
      break;
    }

    case "stop": {
      const res = await callApi("/quant/api/engine/stop", { method: "POST", port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      console.log("✓ 实盘引擎已停止");
      break;
    }

    case "pause": {
      const res = await callApi("/quant/api/engine/pause", { method: "POST", port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      console.log("⏸ 引擎已暂停");
      break;
    }

    case "resume": {
      const res = await callApi("/quant/api/engine/resume", { method: "POST", port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      console.log("▶️ 引擎已恢复");
      break;
    }

    case "status": {
      const res = await callApi("/quant/api/live/status", { port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      const d = res.data?.data as Record<string, unknown> | undefined;
      if (!d) {
        console.log("引擎未运行");
        break;
      }
      const engStatus = d["status"] ?? "unknown";
      console.log(`状态: ${engStatus}   模式: ${d["mode"] ?? "-"}   Broker: ${d["broker"] ?? "-"}`);
      if (d["account"]) {
        const acc = d["account"] as Record<string, unknown>;
        console.log(
          `账户: 总资产 ${acc["totalAssets"]}  现金 ${acc["cash"]}  盈亏 ${acc["profit"]} (${acc["profitPct"]})`,
        );
      }
      console.log(
        `策略: ${d["activeStrategies"]} 运行 / ${d["totalStrategies"]} 总数   持仓: ${d["positionCount"]} 个`,
      );
      break;
    }

    case "orders": {
      const qs = opts.status ? `?status=${opts.status}` : "";
      const res = await callApi(`/quant/api/orders${qs}`, { port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      const orders = res.data?.data as Array<Record<string, unknown>> | undefined;
      if (!orders?.length) {
        console.log("今日暂无订单");
        break;
      }
      console.log(`今日订单 (${orders.length} 条):`);
      for (const o of orders) {
        const side = o["side"] === "buy" ? "买" : "卖";
        const filled = (o["filledQty"] as number) > 0 ? ` 已成${o["filledQty"]}` : "";
        console.log(
          `  [${o["status"]}] ${side} ${o["symbol"]} ${o["quantity"]}股${filled}  #${(o["orderId"] as string).slice(-6)}`,
        );
      }
      break;
    }

    case "pnl": {
      const res = await callApi("/quant/api/pnl", { port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      const pnl = res.data?.data as Record<string, unknown> | undefined;
      if (!pnl) {
        console.log("暂无盈亏数据");
        break;
      }
      console.log(`📊 今日盈亏 (${pnl["date"]}): ¥${(pnl["netPnl"] as number).toFixed(2)}`);
      break;
    }

    case "strategies": {
      const res = await callApi("/quant/api/strategies", { port, apiToken });
      if (!res.ok) {
        console.error(res.error || JSON.stringify(res.data));
        process.exit(1);
      }
      const strats = res.data?.data as Array<Record<string, unknown>> | undefined;
      if (!strats?.length) {
        console.log("暂无活跃策略");
        break;
      }
      console.log(`活跃策略 (${strats.length} 条):`);
      for (const s of strats) {
        const enabled = s["enabled"] ? "运行" : "停用";
        const syms = Array.isArray(s["symbols"]) ? (s["symbols"] as string[]).join(",") : "-";
        console.log(`  [${enabled}] ${s["strategyId"]}  符号: ${syms}  ID: ${s["id"]}`);
      }
      break;
    }

    default: {
      console.log(`Unknown live action: ${action}`);
      console.log("");
      console.log("Usage: openclaw quant live <action> [options]");
      console.log("");
      console.log("Actions:");
      console.log("  start      Start a live strategy (--strategy, --symbols, --broker)");
      console.log("  stop       Stop the live engine");
      console.log("  status     Show engine and strategy status");
      console.log("  orders     Show recent orders (--status filter)");
      console.log("  pnl        Show today's PnL");
      console.log("  pause      Pause signal processing");
      console.log("  resume     Resume signal processing");
      console.log("  strategies List active live strategies");
      console.log("");
      console.log("Options:");
      console.log("  --port <n>      Gateway HTTP port (default: $APP_PORT or 18789)");
      console.log("");
      console.log("Available built-in strategies:");
      console.log("  dual-ma-crossover  — 双均线交叉");
      console.log("  momentum-breakout  — 动量突破");
      console.log("  rsi                — RSI 策略");
    }
  }
}
