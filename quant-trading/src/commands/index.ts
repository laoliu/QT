/**
 * Chat commands for the quant-trading extension.
 * These bypass the LLM and respond directly.
 */
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import { getStrategiesDir } from "../bridge/python-dir.js";
import { createDataProvider } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import { listStrategies } from "../engine/strategy-registry.js";
import type { PriceAlertService, AlertConditionType } from "../services/price-alert.js";
import type { TaskScheduler } from "../services/task-scheduler.js";
import type { QuantTradingConfig } from "../types.js";
import {
  findCategory,
  formatCategoryDetail,
  formatCategoryOverview,
  showCategoryHelp,
} from "./categories.js";
import { todayCST, formatDateCST, formatTimestampToDateCST } from "../utils/cst-time.js";

/**
 * Run qmt_screener_mac.py with --stdout flag.
 * Returns the selected stock list on success, or throws on failure.
 */
/** Maximum time (ms) for the screener subprocess before we kill it.
 *  600s allows for: bridge probe (15s) + akshare fallback (~300s) + F-Score enrichment. */
const SCREENER_TIMEOUT_MS = 600_000;

interface ScreenerStockResult {
  code: string;
  name: string;
  score: number;
  signals: string[];
  factors: Record<string, unknown>;
}

async function runScreener(
  config: QuantTradingConfig,
): Promise<{ stocks: string[]; results: ScreenerStockResult[]; log: string }> {
  const scriptDir = getStrategiesDir();
  const scriptPath = path.join(scriptDir, "qmt_screener_mac.py");

  return new Promise((resolve, reject) => {
    // Strip proxy env vars for the screener subprocess — Chinese financial APIs
    // (Eastmoney, Sina, Xueqiu) are domestic and get blocked/rate-limited via proxy.
    const childEnv = { ...process.env };
    for (const k of [
      "http_proxy",
      "https_proxy",
      "HTTP_PROXY",
      "HTTPS_PROXY",
      "all_proxy",
      "ALL_PROXY",
    ]) {
      delete childEnv[k];
    }
    childEnv["no_proxy"] = "*";

    const proc = spawn(
      config.pythonPath ?? process.env["PYTHON_BIN"] ?? "python3",
      [scriptPath, "--save-only"],
      {
        cwd: scriptDir,
        env: childEnv,
      },
    );
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        proc.kill("SIGKILL");
        reject(new Error(`筛选脚本超时 (${String(SCREENER_TIMEOUT_MS / 1000)}s)`));
      }
    }, SCREENER_TIMEOUT_MS);
    const stdout: string[] = [];
    const stderr: string[] = [];
    proc.stdout.on("data", (d: Buffer) => stdout.push(d.toString()));
    proc.stderr.on("data", (d: Buffer) => stderr.push(d.toString()));
    proc.on("close", async (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const log = [...stdout, ...stderr].join("").trim();
      if (code !== 0) {
        reject(new Error(`筛选脚本退出码 ${String(code)}:\n${log.slice(-1500)}`));
        return;
      }
      // Last line of stdout should be the JSON output
      const outText = stdout.join("").trim();
      const jsonLine = outText
        .split("\n")
        .reverse()
        .find((l) => l.trim().startsWith("[") || l.trim().startsWith("{"));
      if (!jsonLine) {
        reject(new Error(`筛选脚本未输出股票列表:\n${log.slice(0, 400)}`));
        return;
      }

      // Parse output — new format: {codes: [...], results: [...]} or legacy: [...]
      let parsed: string[] | { codes: string[]; results: ScreenerStockResult[] };
      try {
        parsed = JSON.parse(jsonLine) as
          | string[]
          | { codes: string[]; results: ScreenerStockResult[] };
      } catch (parseErr) {
        reject(
          new Error(
            `筛选脚本输出解析失败: ${(parseErr as Error).message}\n${jsonLine.slice(0, 500)}`,
          ),
        );
        return;
      }
      const stocks = Array.isArray(parsed) ? parsed : parsed.codes;
      const results: ScreenerStockResult[] = Array.isArray(parsed) ? [] : (parsed.results ?? []);

      // Persist to stock-list.json
      const dir = config.dataDir ?? path.join(process.cwd(), "data");
      await fs.mkdir(dir, { recursive: true });
      await fs.writeFile(
        path.join(dir, "stock-list.json"),
        JSON.stringify(stocks, null, 2),
        "utf-8",
      );
      resolve({ stocks, results, log });
    });
    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
  });
}

type CommandDefinition = {
  name: string;
  aliases?: string[];
  description: string;
  /** If true, execute() returns an immediate ack and the real work runs in the background. */
  background?: boolean;
  execute: (args: string) => Promise<string>;
};

export function createQuantCommands(
  _api: QuantTradingRuntime,
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  priceAlertService?: PriceAlertService,
  taskScheduler?: TaskScheduler,
): CommandDefinition[] {
  const provider = createDataProvider(config);

  return [
    {
      name: "/选股",
      aliases: ["/screen-qmt", "/qmt选股"],
      description:
        "分类选股。用法: /选股 [类别]\n类别: 高成长/稳健白马/价值洼地/强势动能/低风险/综合优选",
      background: true,
      async execute(args: string) {
        try {
          const { stocks, results } = await runScreener(config);
          if (stocks.length === 0) return "⚠️ 筛选结果为空，保持上次股票池不变";

          if (results.length === 0) {
            const codeLines = stocks.map((s, i) => `${String(i + 1).padStart(2, " ")}. ${s}`);
            return [`选股完成，共 ${String(stocks.length)} 只`, "", ...codeLines].join("\n");
          }

          // Category-based display: /选股 高成长, /选股 稳健, etc.
          const catArg = args.trim();
          const matched = catArg ? findCategory(catArg) : undefined;
          if (catArg && !matched) return showCategoryHelp(catArg);
          if (matched) return formatCategoryDetail(matched, results, stocks.length);
          return formatCategoryOverview(results, stocks.length);
        } catch (err) {
          return `❌ 选股失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── 真龙战法 ────────────────────────────────────────────────────────
    {
      name: "/真龙",
      aliases: ["/dragon", "/龙头战法"],
      description: "真龙战法选股。用法: /真龙 [板块]\n识别大主线→定位龙一→3板打板最佳买点",
      background: true,
      async execute(args: string) {
        try {
          const params: Record<string, string | number> = {
            action: "full",
            top: 3,
          };
          const sector = args.trim();
          if (sector) params.sector = sector;

          const pyResult = await runPythonScript<{
            summary?: string;
            themes?: Array<{
              sector: string;
              theme_score: number;
              is_super_theme: boolean;
              dragons: Array<{
                code: string;
                name: string;
                dragon_rank: number;
                dragon_score: number;
                factors: Record<string, unknown>;
                signals: string[];
              }>;
              fallback: { recommendation?: string };
              buy_signals: Array<{
                code: string;
                name: string;
                dragon_rank: number;
                board_number: number;
                action: string;
                is_best_timing: boolean;
                confidence: number;
                volume_signal: string;
                risk_level: string;
                reason: string;
              }>;
            }>;
            top_picks?: Array<{
              code: string;
              name: string;
              dragon_rank: number;
              board_number: number;
              confidence: number;
              sector: string;
            }>;
            market_summary?: { total_zt_today: number; super_theme_count: number };
          }>("engines/dragon_master.py", params, config);

          if (!pyResult.ok || !pyResult.data)
            return `❌ 真龙战法分析失败: ${pyResult.error ?? "未知错误"}`;

          const d = pyResult.data;
          const lines: string[] = [];

          // Summary
          lines.push(`🐉 ${d.summary ?? "真龙战法分析完成"}`);
          lines.push("");

          // Themes
          for (const t of d.themes ?? []) {
            const tag = t.is_super_theme ? "🔥超级主线" : "📊主线";
            lines.push(
              `${tag}【${t.sector}】评分${t.theme_score.toFixed(0)} | 涨停${(t as Record<string, unknown>).today_zt_count ?? "?"}只`,
            );

            // Dragons
            for (const dr of t.dragons.slice(0, 3)) {
              const f = dr.factors;
              const rank =
                dr.dragon_rank === 1 ? "🥇龙一" : dr.dragon_rank === 2 ? "🥈龙二" : "🥉龙三";
              lines.push(
                `  ${rank} ${dr.code} ${dr.name} ` +
                  `评分${dr.dragon_score.toFixed(0)} | ` +
                  `${f.lian_ban_count ?? 0}板 | ` +
                  `首封${String(f.first_board_time ?? "?")} | ` +
                  `封单${typeof f.seal_amount_yi === "number" ? f.seal_amount_yi.toFixed(1) : "?"}亿`,
              );
            }

            // Buy signals
            for (const bs of t.buy_signals.filter((s) => s.action === "buy")) {
              const best = bs.is_best_timing ? " ★最佳时机" : "";
              lines.push(
                `  ⏰ ${bs.code} ${bs.name} ${bs.board_number}板打板 ` +
                  `置信度${bs.confidence.toFixed(0)}%${best} (${bs.volume_signal})`,
              );
            }

            // Fallback
            if (t.fallback?.recommendation) {
              lines.push(`  💡 ${t.fallback.recommendation}`);
            }
            lines.push("");
          }

          // Top picks
          if ((d.top_picks ?? []).length > 0) {
            lines.push("🎯 首选标的:");
            for (const p of d.top_picks ?? []) {
              lines.push(
                `  ${p.code} ${p.name} [${p.sector}] ` +
                  `龙${p.dragon_rank} ${p.board_number}板 置信度${p.confidence.toFixed(0)}%`,
              );
            }
          }

          return lines.join("\n");
        } catch (err) {
          return `❌ 真龙战法失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Market data ────────────────────────────────────────────────────────
    {
      name: "/quote",
      aliases: ["/q", "/行情"],
      description: "快速查询实时行情。用法: /quote 600519 或 /quote HK0777 或 /quote 网龙 香港",
      async execute(args: string) {
        let symbol = args.trim();
        if (!symbol)
          return "用法: /quote <代码或名称>\n例如: /quote 600519, /quote HK0777, /quote AAPL\n     /quote ę́, /quote 网龙 香港, /quote 比亚迪 港股\n提示: 港股用 HK0777 格式可避免飞书404预览";

        // Feishu (and other IM clients) auto-link "0777.HK" into markdown or
        // plain-text URLs like "(http://0777.hk/)" or "http://0777.hk/".
        // Extract the hostname and convert back to an uppercase stock code.
        const urlMatch = symbol.match(/\(?https?:\/\/([^/)\s]+)\/?[)\s]?/i);
        if (urlMatch) {
          symbol = urlMatch[1].toUpperCase(); // "0777.hk" → "0777.HK"
        }
        // Also strip stray parentheses / brackets left by markdown
        symbol = symbol.replace(/^[([<]+|[)\]>]+$/g, "");

        // Support prefix-based formats that avoid Feishu auto-linking:
        //   HK0777 → 0777.HK,  HK:0777 → 0777.HK,  US:AAPL → AAPL
        const prefixMatch = symbol.match(/^(HK|US)[:\s]?(\w+)$/i);
        if (prefixMatch) {
          const mkt = prefixMatch[1].toUpperCase();
          const code = prefixMatch[2].toUpperCase();
          symbol = mkt === "HK" ? `${code}.HK` : code;
        }

        const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");

        // If input contains Chinese characters → treat as name search.
        // Extract optional market hint: 香港/港股 → hk_stock, 美股/美国 → us_stock
        const hasChinese = /[\u4e00-\u9fff]/.test(symbol);
        if (hasChinese) {
          let hintMarket: import("../types.js").MarketType | undefined;
          let keyword = symbol;
          const marketHints: [RegExp, import("../types.js").MarketType][] = [
            [/\s*(香港|港股|hk)\s*/i, "hk_stock"],
            [/\s*(美股|美国|us)\s*/i, "us_stock"],
            [/\s*(中国|A股|a股|沪深|国内)\s*/i, "cn_stock"],
          ];
          for (const [re, mt] of marketHints) {
            if (re.test(keyword)) {
              hintMarket = mt;
              keyword = keyword.replace(re, "").trim();
              break;
            }
          }
          if (keyword) {
            const hits = await provider.searchSymbols(keyword, hintMarket);
            if (hits.length > 0) {
              symbol = hits[0].code;
              // Append .HK for HK stocks so detectMarketFromSymbol routes correctly
              if (hits[0].market === "hk_stock" && !symbol.endsWith(".HK")) {
                symbol = `${symbol}.HK`;
              }
            } else {
              return `未找到「${keyword}」相关股票${hintMarket ? `（${hintMarket === "hk_stock" ? "港股" : "美股"}）` : ""}`;
            }
          }
        }

        const market = detectMarketFromSymbol(symbol);
        console.error(`[/quote] symbol=${symbol} market=${market} provider=${provider.name}`);
        const quote = await provider.getQuote(symbol, market);
        if (!quote) {
          console.error(`[/quote] getQuote returned null for ${symbol} (market=${market})`);
          return `无法获取 ${symbol} 的行情`;
        }
        const arrow = quote.changePct >= 0 ? "📈" : "📉";
        const ccy = market === "hk_stock" ? "HK$" : market === "us_stock" ? "$" : "¥";
        // Wrap stock code in backticks so Feishu renders it as inline code
        // and does NOT auto-link ".HK" suffixes as URLs (prevents 404 preview).
        const safeCode = `\`${quote.symbol.code}\``;
        const vol =
          quote.volume >= 100000000
            ? `${(quote.volume / 100000000).toFixed(2)}亿`
            : `${(quote.volume / 10000).toFixed(0)}万`;
        const amt =
          quote.amount >= 100000000
            ? `${ccy}${(quote.amount / 100000000).toFixed(2)}亿`
            : quote.amount > 0
              ? `${ccy}${(quote.amount / 10000).toFixed(0)}万`
              : "--";
        const lines = [
          `${arrow} **${quote.symbol.name}** (${safeCode})`,
          `价格: ${ccy}${quote.price.toFixed(2)}  涨跌: ${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`,
          `开: ${ccy}${quote.open.toFixed(2)}  高: ${ccy}${quote.high.toFixed(2)}  低: ${ccy}${quote.low.toFixed(2)}`,
          `成交量: ${vol}  成交额: ${amt}`,
        ];
        // Show market cap if available
        const mc = (quote as Record<string, unknown>).marketCap as number | undefined;
        if (mc && mc > 0) {
          const mcStr =
            mc >= 100000000 ? `${(mc / 100000000).toFixed(2)}亿` : `${(mc / 10000).toFixed(0)}万`;
          lines.push(`总市值: ${ccy}${mcStr}`);
        }
        return lines.join("\n");
      },
    },
    {
      name: "/screen",
      aliases: ["/ɸѡ"],
      description: "简单股票筛选。用法: /screen pe<20 roe>15",
      async execute(args: string) {
        if (!args.trim())
          return "用法: /screen pe<20 roe>15\n支持条件: pe, pb, roe, market_cap, volume_ratio";
        try {
          // Parse simple conditions like "pe<20 roe>15"
          const conditions = args
            .trim()
            .split(/\s+/)
            .map((c) => {
              const m = c.match(/^(\w+)([<>=!]+)([\d.]+)$/);
              if (!m) return null;
              return { field: m[1], op: m[2], value: parseFloat(m[3]) };
            })
            .filter(Boolean);

          if (conditions.length === 0) return "❌ 无法解析筛选条件。格式: pe<20 roe>15";

          const pyResult = await runPythonScript<{
            results: Array<{ code: string; name: string; [k: string]: unknown }>;
            count: number;
          }>(
            "engines/screener_engine.py",
            {
              action: "run_screener",
              screenerId: "fundamental",
              market: config.defaultMarket ?? "cn_stock",
              params: Object.fromEntries(
                conditions.map((c) => [
                  `${c!.field}_${c!.op.includes("<") ? "max" : "min"}`,
                  c!.value,
                ]),
              ),
              limit: 20,
            },
            config,
          );

          if (!pyResult.ok || !pyResult.data?.results?.length) {
            return `筛选条件: ${args}\n结果: 0 只符合条件\n${pyResult.error ? `错误: ${pyResult.error}` : ""}`;
          }

          const lines = [`📋 **筛选结果** (${String(pyResult.data.count)} 只)`, ""];
          for (const [i, s] of pyResult.data.results.slice(0, 20).entries()) {
            lines.push(`${String(i + 1).padStart(2, " ")}. ${s.code} ${s.name}`);
          }
          return lines.join("\n");
        } catch (err) {
          return `筛选条件: ${args}\n(筛选引擎未就绪: ${err instanceof Error ? err.message : String(err)})`;
        }
      },
    },
    {
      name: "/backtest",
      aliases: ["/bt", "/回测"],
      description: "快速回测。用法: /backtest dual-ma-crossover 600519",
      async execute(args: string) {
        const parts = args.trim().split(/\s+/);
        if (parts.length < 1 || !parts[0]) {
          const strategies = listStrategies();
          return [
            "可用策略:",
            ...strategies.map((s) => `  • **${s.id}** — ${s.name}`),
            "",
            "用法: /backtest <策略ID> [标的代码] [--start YYYY-MM-DD] [--end YYYY-MM-DD]",
          ].join("\n");
        }

        const strategyId = parts[0];
        // Parse optional flags: --start, --end
        let symbolArg = "";
        let startDate = "";
        let endDate = "";
        for (let i = 1; i < parts.length; i++) {
          if (parts[i] === "--start" && parts[i + 1]) {
            startDate = parts[++i];
          } else if (parts[i] === "--end" && parts[i + 1]) {
            endDate = parts[++i];
          } else if (!symbolArg) {
            symbolArg = parts[i];
          }
        }

        const symbol = symbolArg || "000300";
        if (!endDate) endDate = todayCST();
        if (!startDate) {
          // Default: 6 months back
          const d = new Date();
          d.setMonth(d.getMonth() - 6);
          startDate = formatDateCST(d);
        }

        try {
          const { runBacktest } = await import("../engine/strategy-registry.js");
          const result = await runBacktest(
            {
              strategyId,
              symbols: [symbol],
              startDate,
              endDate,
              initialCash: 100_000,
            },
            config,
          );

          // Format summary
          const pnlAbs = result.finalValue - result.initialCash;
          const pnlPct = result.totalReturn ?? 0;
          const maxDD = result.maxDrawdown ?? 0;
          const sharpe = result.sharpeRatio ?? 0;
          const trades = result.totalTrades ?? 0;
          const winRate = result.winRate ?? 0;
          const emoji = pnlPct >= 0 ? "📈" : "📉";

          const lines = [
            `${emoji} **回测结果** — ${strategyId}`,
            `标的: ${symbol}  周期: ${startDate} ~ ${endDate}`,
            "",
            `初始资金: ¥100,000  终值: ¥${result.finalValue.toFixed(2)}`,
            `总收益: ¥${pnlAbs.toFixed(2)} (${pnlPct >= 0 ? "+" : ""}${(pnlPct * 100).toFixed(2)}%)`,
            `最大回撤: ${(maxDD * 100).toFixed(2)}%`,
            `夏普比率: ${sharpe.toFixed(2)}`,
            `交易次数: ${String(trades)}  胜率: ${(winRate * 100).toFixed(1)}%`,
          ];

          // Show last few signals
          if (result.signals?.length) {
            lines.push("", "**最近信号:**");
            for (const sig of result.signals.slice(-5)) {
              const dir = sig.direction === "buy" ? "🟢 买" : "🔴 卖";
              const dt = formatTimestampToDateCST(sig.timestamp);
              lines.push(
                `  ${dir} ${dt} ¥${sig.price.toFixed(2)}${sig.reason ? ` (${sig.reason})` : ""}`,
              );
            }
          }

          return lines.join("\n");
        } catch (err) {
          return `❌ 回测失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },
    {
      name: "/portfolio",
      aliases: ["/持仓", "/pf"],
      description: "查看持仓（实盘优先，回退模拟组合）。用法: /portfolio",
      async execute() {
        // Prefer live engine positions when running
        const engine = getEngine?.();
        if (engine && (engine.getStatus?.() === "running" || engine.getStatus?.() === "paused")) {
          const account = engine.getAccount?.();
          const positions = engine.getPositions?.() ?? [];
          if (account) {
            const lines = [
              `📊 **实盘持仓** (${account.brokerType})`,
              `资产: ¥${account.totalAssets.toFixed(2)}  现金: ¥${account.cash.toFixed(2)}  市值: ¥${account.marketValue.toFixed(2)}`,
              `今日盈亏: ${account.todayProfit != null ? `¥${account.todayProfit.toFixed(2)}` : "--"}  总浮盈: ${account.profitPct >= 0 ? "+" : ""}${(account.profitPct * 100).toFixed(2)}%`,
              "",
            ];
            if (positions.length === 0) {
              lines.push("空仓");
            } else {
              for (const pos of positions) {
                const name = pos.name ?? pos.symbol;
                const price = pos.currentPrice ? `¥${pos.currentPrice.toFixed(2)}` : "--";
                const pnl =
                  pos.unrealizedPnlPct != null
                    ? `${pos.unrealizedPnlPct >= 0 ? "+" : ""}${(pos.unrealizedPnlPct * 100).toFixed(2)}%`
                    : "";
                const mv = pos.marketValue ? `  市值¥${pos.marketValue.toFixed(0)}` : "";
                lines.push(
                  `  • ${pos.symbol} ${name}  ${String(pos.quantity)}股  均价¥${pos.avgCost.toFixed(2)}  现价${price} ${pnl}${mv}`,
                );
              }
            }
            lines.push(
              "",
              `同步: ${account.lastSyncAt ? new Date(account.lastSyncAt).toLocaleString("zh-CN") : "--"}`,
            );
            return lines.join("\n");
          }
        }
        // Fallback to paper portfolio
        try {
          const dir = config.dataDir ?? path.join(process.cwd(), "data");
          const pfPath = path.join(dir, "portfolio.json");
          const raw = await fs.readFile(pfPath, "utf-8");
          const pf = JSON.parse(raw) as import("../types.js").Portfolio;
          if (pf.positions.length === 0) {
            return `📊 **${pf.name}** — 空仓\n现金: ¥${pf.cash.toFixed(2)}\n总值: ¥${pf.totalValue.toFixed(2)}`;
          }
          const lines = [
            `📊 **模拟组合: ${pf.name}**`,
            `现金: ¥${pf.cash.toFixed(2)}  总值: ¥${pf.totalValue.toFixed(2)}`,
            "",
          ];
          for (const pos of pf.positions) {
            const name = pos.name ?? pos.symbol;
            const price = pos.currentPrice ? `¥${pos.currentPrice.toFixed(2)}` : "--";
            const pnl =
              pos.unrealizedPnlPct != null
                ? `${pos.unrealizedPnlPct >= 0 ? "+" : ""}${(pos.unrealizedPnlPct * 100).toFixed(2)}%`
                : "";
            lines.push(
              `  • ${pos.symbol} ${name}  ${String(pos.quantity)}股  均价¥${pos.avgCost.toFixed(2)}  现价${price} ${pnl}`,
            );
          }
          return lines.join("\n");
        } catch {
          return "📊 暂无持仓数据。交易后持仓信息将自动更新。";
        }
      },
    },
    {
      name: "/signal",
      aliases: ["/信号"],
      description: "查看最近交易信号。用法: /signal",
      async execute() {
        try {
          const dir = config.dataDir ?? path.join(process.cwd(), "data");
          const signalPath = path.join(dir, "signals.json");
          const raw = await fs.readFile(signalPath, "utf-8");
          const signals = (
            JSON.parse(raw) as Array<{
              timestamp: string;
              symbol: string;
              action: string;
              strategy: string;
              confidence?: number;
              reason?: string;
            }>
          ).slice(-15);

          if (signals.length === 0) return "📡 暂无交易信号";

          const lines = [`📡 **最近交易信号** (${String(signals.length)} 条)`, ""];
          for (const s of signals) {
            const icon = s.action === "buy" ? "🟢" : s.action === "sell" ? "🔴" : "⚪";
            const conf =
              s.confidence != null ? ` (${String(Math.round(s.confidence * 100))}%)` : "";
            const time = new Date(s.timestamp).toLocaleString("zh-CN", {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            });
            lines.push(
              `  ${icon} ${time}  ${s.action.toUpperCase()} ${s.symbol}  ${s.strategy}${conf}`,
            );
            if (s.reason) lines.push(`     ${s.reason}`);
          }
          return lines.join("\n");
        } catch {
          return "📡 暂无交易信号。策略激活后信号将自动记录。";
        }
      },
    },

    // ── Live trading commands ──────────────────────────────────────────────

    {
      name: "/confirm",
      aliases: ["/确认"],
      description: "确认待审订单。用法: /confirm <confirmId>",
      async execute(args: string) {
        const id = args.trim();
        if (!id) return "用法: /confirm <confirmId>  例如: /confirm a3f9";
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动，无法确认订单";
        if (!engine.orderManager) return "❌ 订单管理器未就绪";
        try {
          const msg = await engine.orderManager.confirmationManager.confirm(id);
          return `✅ ${msg}`;
        } catch {
          return `❌ 未找到确认ID #${id}（可能已超时）`;
        }
      },
    },
    {
      name: "/reject",
      aliases: ["/拒绝"],
      description: "拒绝待审订单。用法: /reject <confirmId>",
      async execute(args: string) {
        const id = args.trim();
        if (!id) return "用法: /reject <confirmId>  例如: /reject a3f9";
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动";
        if (!engine.orderManager) return "❌ 订单管理器未就绪";
        try {
          const msg = await engine.orderManager.confirmationManager.reject(id);
          return `🚫 ${msg}`;
        } catch {
          return `❌ 未找到确认ID #${id}（可能已超时）`;
        }
      },
    },
    {
      name: "/pause",
      aliases: ["/暂停"],
      description: "暂停实盘引擎（停止接受新信号，现有订单不受影响）。",
      async execute() {
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动";
        engine.pause();
        return "⏸ 实盘引擎已暂停，新信号将被忽略。使用 /resume 恢复。";
      },
    },
    {
      name: "/resume",
      aliases: ["/恢复"],
      description: "恢复实盘引擎。",
      async execute() {
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动";
        engine.resume();
        return "▶️ 实盘引擎已恢复运行。";
      },
    },
    {
      name: "/orders",
      aliases: ["/订单"],
      description: "查看今日实盘订单摘要。用法: /orders [symbol]",
      async execute(args: string) {
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动";
        if (!engine.orderManager) return "❌ 订单管理器未就绪";
        const symbol = args.trim() || undefined;
        const orders = await engine.orderManager.getOrders({ ...(symbol && { symbol }) });
        if (orders.length === 0) return "今日暂无订单";
        const lines = orders.slice(0, 20).map((o) => {
          const side = o.side === "buy" ? "买" : "卖";
          const filled = o.filledQty > 0 ? ` 已成${o.filledQty}` : "";
          return `• [${o.status}] ${side} ${o.symbol} ${o.quantity}股${filled}  #${o.orderId.slice(-6)}`;
        });
        if (orders.length > 20) lines.push(`…共 ${orders.length} 条，其余请使用 AI 对话模式查看`);
        return lines.join("\n");
      },
    },
    {
      name: "/pnl",
      aliases: ["/盈亏"],
      description: "查看今日盈亏（含持仓浮盈明细）。",
      async execute() {
        const engine = getEngine?.();
        if (!engine) return "❌ 实盘引擎未启动";
        if (!engine.orderManager) return "❌ 订单管理器未就绪";
        const netPnl = await engine.orderManager.getDailyPnl();
        const date = todayCST();
        const account = engine.getAccount?.();
        const positions = engine.getPositions?.() ?? [];
        const lines = [`📊 **今日盈亏** (${date})`];
        if (account) {
          lines.push(
            `总资产: ¥${account.totalAssets.toFixed(2)}  今日实现: ¥${netPnl.toFixed(2)}`,
            `浮动盈亏: ${account.profitPct >= 0 ? "+" : ""}${(account.profitPct * 100).toFixed(2)}%`,
          );
        } else {
          lines.push(`净盈亏: ¥${netPnl.toFixed(2)}`);
        }
        if (positions.length > 0) {
          lines.push("", "**持仓浮盈:**");
          const sorted = [...positions].sort(
            (a, b) => (b.unrealizedPnl ?? 0) - (a.unrealizedPnl ?? 0),
          );
          for (const pos of sorted.slice(0, 15)) {
            const name = pos.name ?? pos.symbol;
            const pnlAbs = pos.unrealizedPnl != null ? `¥${pos.unrealizedPnl.toFixed(0)}` : "--";
            const pnlPct =
              pos.unrealizedPnlPct != null
                ? `${pos.unrealizedPnlPct >= 0 ? "+" : ""}${(pos.unrealizedPnlPct * 100).toFixed(2)}%`
                : "";
            const icon = (pos.unrealizedPnl ?? 0) >= 0 ? "📈" : "📉";
            lines.push(`  ${icon} ${pos.symbol} ${name}  ${pnlAbs} ${pnlPct}`);
          }
          if (positions.length > 15) lines.push(`  …共 ${String(positions.length)} 只持仓`);
        }
        return lines.join("\n");
      },
    },

    // ── Watchlist ────────────────────────────────────────────────────────
    {
      name: "/watchlist",
      aliases: ["/自选", "/wl"],
      description:
        "自选股管理。用法:\n" +
        "  /自选             — 查看自选股列表\n" +
        "  /自选 add 600519  — 添加\n" +
        "  /自选 del 600519  — 删除\n" +
        "  /自选 clear       — 清空",
      async execute(args: string) {
        const dir = config.dataDir ?? path.join(process.cwd(), "data");
        const watchlistPath = path.join(dir, "watchlist.json");

        // Load existing watchlist
        let watchlist: string[] = [];
        try {
          const raw = await fs.readFile(watchlistPath, "utf-8");
          watchlist = JSON.parse(raw) as string[];
        } catch {
          // File doesn't exist yet — empty watchlist
        }

        const parts = args.trim().split(/\s+/);
        const subCmd = parts[0]?.toLowerCase() ?? "";
        const codes = parts
          .slice(1)
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean);

        if (subCmd === "add" || subCmd === "+" || subCmd === "添加") {
          if (codes.length === 0) return "用法: /自选 add <代码> [代码...]";
          const added: string[] = [];
          for (const code of codes) {
            if (!watchlist.includes(code)) {
              watchlist.push(code);
              added.push(code);
            }
          }
          await fs.mkdir(dir, { recursive: true });
          await fs.writeFile(watchlistPath, JSON.stringify(watchlist, null, 2), "utf-8");
          if (added.length === 0) return "这些代码已在自选股中";

          // Auto-create price alerts for newly added symbols
          const alertMsgs: string[] = [];
          if (priceAlertService) {
            for (const code of added) {
              const alertId = priceAlertService.addWatchlistAlert(code);
              if (alertId) alertMsgs.push(`  🔔 ${code} 自动添加涨跌幅预警`);
            }
          }

          const lines = [
            `✅ 已添加 ${added.join(", ")} 到自选股（共 ${String(watchlist.length)} 只）`,
          ];
          if (alertMsgs.length > 0) lines.push(...alertMsgs);
          return lines.join("\n");
        }

        if (subCmd === "del" || subCmd === "rm" || subCmd === "-" || subCmd === "删除") {
          if (codes.length === 0) return "用法: /自选 del <代码> [代码...]";
          const removed: string[] = [];
          for (const code of codes) {
            const idx = watchlist.indexOf(code);
            if (idx >= 0) {
              watchlist.splice(idx, 1);
              removed.push(code);
            }
          }
          await fs.writeFile(watchlistPath, JSON.stringify(watchlist, null, 2), "utf-8");
          if (removed.length === 0) return "这些代码不在自选股中";
          return `🗑️ 已移除 ${removed.join(", ")}（剩余 ${String(watchlist.length)} 只）`;
        }

        if (subCmd === "clear" || subCmd === "清空") {
          const count = watchlist.length;
          await fs.writeFile(watchlistPath, "[]", "utf-8");
          return `🗑️ 已清空自选股（原有 ${String(count)} 只）`;
        }

        // Default: show watchlist, optionally with real-time quotes
        if (watchlist.length === 0) {
          return [
            "📋 **自选股** — 空",
            "",
            "用法:",
            "  /自选 add 600519 000858 — 添加",
            "  /自选 del 600519       — 删除",
          ].join("\n");
        }

        // Try to fetch real-time quotes for watchlist stocks
        const lines = [`📋 **自选股** (${String(watchlist.length)} 只)`];
        try {
          const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");
          for (const code of watchlist.slice(0, 30)) {
            const market = detectMarketFromSymbol(code);
            try {
              const quote = await provider.getQuote(code, market as never);
              if (quote) {
                const arrow = quote.changePct >= 0 ? "📈" : "📉";
                const chg = `${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`;
                lines.push(
                  `  ${arrow} ${code} ${quote.symbol.name ?? ""}  ¥${quote.price.toFixed(2)} ${chg}`,
                );
              } else {
                lines.push(`  • ${code}`);
              }
            } catch {
              lines.push(`  • ${code}`);
            }
          }
          if (watchlist.length > 30) {
            lines.push(`  …另有 ${String(watchlist.length - 30)} 只未显示`);
          }
        } catch {
          // Fallback: just list codes
          for (const code of watchlist) {
            lines.push(`  • ${code}`);
          }
        }

        return lines.join("\n");
      },
    },

    // ── Sector Analysis ─────────────────────────────────────────────────
    {
      name: "/板块",
      aliases: ["/sector", "/bk"],
      description:
        "板块/行业分析。用法:\n" +
        "  /板块            — 查看板块涨跌排名\n" +
        "  /板块 半导体      — 查看特定板块详情\n" +
        "  /板块 top5       — 涨幅前5板块",
      async execute(args: string) {
        const arg = args.trim();
        try {
          // If arg looks like "topN", show ranking
          const topMatch = arg.match(/^top(\d+)$/i);
          const limit = topMatch ? parseInt(topMatch[1], 10) : arg ? undefined : 10;

          if (!arg || topMatch) {
            // Ranking mode
            const pyResult = await runPythonScript<{
              sectors: Array<{
                name: string;
                change_pct: number;
                leader: string;
                leader_change: number;
              }>;
            }>("engines/sector_analysis.py", { action: "ranking", limit: limit ?? 10 }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "板块数据获取失败"}`;
            const result = pyResult.data;
            if (!result.sectors?.length) return "暂无板块数据";
            const lines = ["📊 **板块涨跌排名**", ""];
            for (const [i, s] of result.sectors.entries()) {
              const arrow = s.change_pct >= 0 ? "📈" : "📉";
              const chg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
              lines.push(
                `${String(i + 1).padStart(2, " ")}. ${arrow} **${s.name}** ${chg}  领涨: ${s.leader}(${s.leader_change >= 0 ? "+" : ""}${s.leader_change.toFixed(2)}%)`,
              );
            }
            return lines.join("\n");
          }

          // Detail mode — specific sector
          const pyResult = await runPythonScript<{
            name: string;
            change_pct: number;
            source?: string;
            stocks: Array<{ code: string; name: string; change_pct: number; price: number }>;
          }>("engines/sector_analysis.py", { action: "detail", sector: arg }, config);
          if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "板块数据获取失败"}`;
          const result = pyResult.data;
          const chg = `${result.change_pct >= 0 ? "+" : ""}${result.change_pct.toFixed(2)}%`;
          if (!result.stocks?.length) {
            // Xueqiu fallback: board overview without constituent stocks
            if (result.name) {
              return `📊 **${result.name}** ${chg}\n\n（仅板块概览，成分股数据暂不可用）`;
            }
            return `未找到板块「${arg}」的数据`;
          }
          const lines = [`📊 **${result.name}** ${chg}`, ""];
          for (const s of result.stocks.slice(0, 15)) {
            const arrow = s.change_pct >= 0 ? "📈" : "📉";
            const sChg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
            lines.push(`  ${arrow} ${s.code} ${s.name}  ¥${s.price.toFixed(2)} ${sChg}`);
          }
          if (result.stocks.length > 15) {
            lines.push(`  …共 ${String(result.stocks.length)} 只成分股`);
          }
          return lines.join("\n");
        } catch (err) {
          return `❌ 板块分析失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Fund Flow ───────────────────────────────────────────────────────
    {
      name: "/资金",
      aliases: ["/fund-flow", "/zj"],
      description:
        "资金流向查询。用法:\n" +
        "  /资金            — 今日主力资金排名\n" +
        "  /资金 600519     — 查看个股资金流向",
      async execute(args: string) {
        const symbol = args.trim();
        try {
          if (!symbol) {
            // Rank mode
            const pyResult = await runPythonScript<{
              stocks: Array<{ code: string; name: string; net_inflow: number; change_pct: number }>;
            }>("engines/fund_flow.py", { action: "rank", limit: 15 }, config);
            if (!pyResult.ok || !pyResult.data)
              return `❌ ${pyResult.error ?? "资金流向数据获取失败"}`;
            const result = pyResult.data;
            if (!result.stocks?.length) return "暂无资金流向数据";
            const lines = ["💰 **主力资金净流入排名**", ""];
            for (const [i, s] of result.stocks.entries()) {
              const inflow =
                s.net_inflow >= 0
                  ? `+${(s.net_inflow / 10000).toFixed(0)}万`
                  : `${(s.net_inflow / 10000).toFixed(0)}万`;
              const chg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
              lines.push(
                `${String(i + 1).padStart(2, " ")}. ${s.code} ${s.name}  净流入: ${inflow}  涨跌: ${chg}`,
              );
            }
            return lines.join("\n");
          }

          // Stock detail mode
          const pyResult = await runPythonScript<{
            code: string;
            name: string;
            main_inflow: number;
            main_outflow: number;
            net_inflow: number;
            retail_net: number;
          }>("engines/fund_flow.py", { action: "stock", symbol }, config);
          if (!pyResult.ok || !pyResult.data)
            return `❌ ${pyResult.error ?? "资金流向数据获取失败"}`;
          const result = pyResult.data;
          const netStr =
            result.net_inflow >= 0
              ? `+${(result.net_inflow / 10000).toFixed(0)}万`
              : `${(result.net_inflow / 10000).toFixed(0)}万`;
          const lines = [
            `💰 **${result.name}** (${result.code}) 资金流向`,
            `主力流入: ${(result.main_inflow / 10000).toFixed(0)}万  流出: ${(result.main_outflow / 10000).toFixed(0)}万`,
            `主力净流入: ${netStr}`,
            `散户净流入: ${((result.retail_net ?? 0) / 10000).toFixed(0)}万`,
          ];
          return lines.join("\n");
        } catch (err) {
          return `❌ 资金流向查询失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Chip Accumulation (主力吸筹) ────────────────────────────────────
    {
      name: "/xichou",
      aliases: ["/chip-acc", "/accumulation"],
      description:
        "主力吸筹检测。用法:\n" +
        "  /xichou 600519     — 检测个股吸筹信号\n" +
        "  /xichou scan       — 批量扫描活跃股吸筹",
      async execute(args: string) {
        const arg = args.trim();
        try {
          if (arg === "scan" || !arg) {
            const pyResult = await runPythonScript<{
              count: number;
              stocks: Array<{
                symbol: string;
                name: string;
                total_score: number;
                level: string;
                conclusion: string;
              }>;
            }>(
              "engines/chip_accumulation.py",
              { action: "scan", lookback: 30, min_confidence: 50 },
              config,
            );
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "吸筹扫描失败"}`;
            const result = pyResult.data;
            if (!result.stocks?.length) return "未发现明显吸筹候选";
            const lines = [`🧲 **吸筹候选** (${result.count} 只，分数≥50)`, ""];
            for (const s of result.stocks.slice(0, 15)) {
              const icon = s.total_score >= 70 ? "🟢" : s.total_score >= 50 ? "🟡" : "⚪";
              lines.push(
                `${icon} **${s.name}** (${s.symbol}) — ${s.total_score.toFixed(1)}分 ${s.conclusion}`,
              );
            }
            return lines.join("\n");
          }
          // Single stock detect
          const symbol = arg.replace(/[^\d]/g, "");
          if (!symbol || symbol.length < 6) return "请输入6位股票代码，如: /xichou 600519";
          const pyResult = await runPythonScript<{
            symbol: string;
            name: string;
            price: number;
            total_score: number;
            level: string;
            conclusion: string;
            advice: string;
            dimension_scores: Record<string, number>;
            signals: Array<{ dimension: string; description: string }>;
          }>("engines/chip_accumulation.py", { action: "detect", symbol, lookback: 30 }, config);
          if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "吸筹检测失败"}`;
          const d = pyResult.data;
          const icon =
            d.total_score >= 70
              ? "🟢"
              : d.total_score >= 50
                ? "🟡"
                : d.total_score >= 30
                  ? "⚪"
                  : "🔴";
          const dimLabels: Record<string, string> = {
            fund_flow: "资金流",
            chip: "筹码",
            volume_price: "量价",
            turnover: "换手",
            large_order: "大单",
          };
          const dims = d.dimension_scores ?? {};
          const dimLine = Object.entries(dimLabels)
            .map(([k, label]) => `${label}:${(dims[k] ?? 0).toFixed(0)}`)
            .join(" | ");
          const lines = [
            `${icon} **${d.name ?? d.symbol}** 吸筹检测 — **${d.total_score.toFixed(1)}分** ${d.conclusion}`,
            `现价: ${d.price}  |  ${dimLine}`,
            `💡 ${d.advice}`,
          ];
          if (d.signals?.length) {
            lines.push("", "**信号明细:**");
            for (const s of d.signals.slice(0, 8)) {
              lines.push(`  • [${s.dimension}] ${s.description}`);
            }
          }
          return lines.join("\n");
        } catch (err) {
          return `❌ 吸筹检测失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Dragon Tiger Board ──────────────────────────────────────────────
    {
      name: "/龙虎榜",
      aliases: ["/lhb", "/dragon_tiger"],
      description:
        "龙虎榜查询。用法:\n" +
        "  /龙虎榜          — 今日龙虎榜\n" +
        "  /龙虎榜 600519   — 个股龙虎榜详情",
      async execute(args: string) {
        const symbol = args.trim();
        try {
          if (!symbol) {
            // Daily ranking
            const pyResult = await runPythonScript<{
              date: string;
              stocks: Array<{
                code: string;
                name: string;
                change_pct: number;
                net_buy: number;
                reason: string;
              }>;
            }>("engines/dragon_tiger.py", { action: "daily" }, config);
            if (!pyResult.ok || !pyResult.data)
              return `❌ ${pyResult.error ?? "龙虎榜数据获取失败"}`;
            const result = pyResult.data;
            if (!result.stocks?.length) return "今日暂无龙虎榜数据";
            const lines = [`🐉 **龙虎榜** (${result.date})`, ""];
            for (const s of result.stocks.slice(0, 20)) {
              const chg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
              const buy =
                s.net_buy >= 0
                  ? `+${(s.net_buy / 10000).toFixed(0)}万`
                  : `${(s.net_buy / 10000).toFixed(0)}万`;
              lines.push(`  • ${s.code} **${s.name}** ${chg}  净买入: ${buy}`);
              if (s.reason) lines.push(`    ${s.reason}`);
            }
            if (result.stocks.length > 20) {
              lines.push(`  …共 ${String(result.stocks.length)} 只`);
            }
            return lines.join("\n");
          }

          // Stock detail
          const pyResult = await runPythonScript<{
            code: string;
            name: string;
            records: Array<{
              date: string;
              reason: string;
              buy_total: number;
              sell_total: number;
            }>;
          }>("engines/dragon_tiger.py", { action: "stock", symbol }, config);
          if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "龙虎榜数据获取失败"}`;
          const result = pyResult.data;
          if (!result.records?.length) return `${symbol} 近期无龙虎榜记录`;
          const lines = [`🐉 **${result.name}** (${result.code}) 龙虎榜记录`, ""];
          for (const r of result.records.slice(0, 10)) {
            const net = r.buy_total - r.sell_total;
            const netStr =
              net >= 0 ? `+${(net / 10000).toFixed(0)}万` : `${(net / 10000).toFixed(0)}万`;
            lines.push(`  ${r.date}  净买入: ${netStr}  ${r.reason}`);
          }
          return lines.join("\n");
        } catch (err) {
          return `❌ 龙虎榜查询失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Lianban (连板梯队 & 二进三候选) ─────────────────────────────────
    {
      name: "/连板",
      aliases: ["/lianban", "/board-ladder"],
      description:
        "连板梯队查询。用法:\n" +
        "  /连板          — 当日连板梯队全景\n" +
        "  /连板 600519   — 个股连板轨迹分析",
      async execute(args: string) {
        const symbol = args.trim();
        try {
          if (!symbol) {
            // Full ladder scan
            const pyResult = await runPythonScript<{
              summary: {
                total_zt: number;
                highest_board: number;
                ladder: Record<string, number>;
                sentiment_score: number;
                sentiment_level: string;
                zt_dt_ratio: number;
                failure_rate: number;
              };
              stocks_by_level: Record<
                string,
                Array<{
                  code: string;
                  name: string;
                  industry: string;
                  turnover_rate: string;
                  amount_yi: string;
                }>
              >;
              promotion: Record<
                string,
                { rate: string; promoted: number; failed: number; total: number }
              >;
              detail: string;
            }>("scripts/lianban_dashboard.py", { action: "scan" }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "连板数据获取失败"}`;
            const r = pyResult.data;
            const s = r.summary;

            const lines = [
              `📊 **连板梯队** — 涨停${s.total_zt}家, 最高${s.highest_board}板`,
              `情绪评分: ${s.sentiment_score}/100 (${s.sentiment_level}), 涨跌停比: ${s.zt_dt_ratio}, 炸板率: ${(s.failure_rate * 100).toFixed(0)}%`,
              "",
            ];

            // Ladder summary
            const ladder = s.ladder;
            if (ladder && Object.keys(ladder).length > 0) {
              const ladderStr = Object.entries(ladder)
                .sort(([a], [b]) => Number(b) - Number(a))
                .map(([k, v]) => `${k}板: ${v}家`)
                .join(" | ");
              lines.push(`梯队: ${ladderStr}`, "");
            }

            // Stocks by level (2板+)
            for (const [level, stocks] of Object.entries(r.stocks_by_level || {}).sort(([a], [b]) =>
              b.localeCompare(a),
            )) {
              lines.push(`**${level}**`);
              for (const st of stocks.slice(0, 8)) {
                lines.push(
                  `  • ${st.code} **${st.name}** ${st.industry} ${st.turnover_rate} ${st.amount_yi}`,
                );
              }
              if (stocks.length > 8) lines.push(`  ... 等 ${stocks.length} 只`);
              lines.push("");
            }

            // Promotion rates
            for (const [key, p] of Object.entries(r.promotion || {})) {
              lines.push(`${key}晋级率: ${p.rate} (${p.promoted}/${p.total})`);
            }

            return lines.join("\n");
          } else {
            // Single stock detail
            const pyResult = await runPythonScript<{
              symbol: string;
              board_count: number;
              quality: {
                total_score: number;
                board_count: number;
                factors: Record<string, unknown>;
                boards: Array<{
                  date: string;
                  board_type: string;
                  turnover_rate: number;
                  close: number;
                }>;
                warnings: string[];
              };
              pool_info: { name: string; industry: string; seal_amount: number } | null;
            }>("scripts/lianban_dashboard.py", { action: "detail", symbol }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "连板数据获取失败"}`;
            const r = pyResult.data;
            const q = r.quality;

            const lines = [
              `📊 **${r.symbol}** ${r.pool_info?.name ?? ""} — ${r.board_count}连板`,
              `板质评分: ${q?.total_score ?? 0}/100`,
              "",
            ];
            if (q?.boards?.length) {
              lines.push("**连板轨迹:**");
              for (const b of q.boards) {
                lines.push(
                  `  ${b.date} ${b.board_type} 换手${b.turnover_rate?.toFixed(1) ?? "?"}% @${b.close?.toFixed(2) ?? "?"}`,
                );
              }
              lines.push("");
            }
            if (q?.warnings?.length) {
              lines.push("⚠️ " + q.warnings.join("; "));
            }

            return lines.join("\n");
          }
        } catch (err) {
          return `❌ 连板查询失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },
    {
      name: "/二进三",
      aliases: ["/ejs", "/relay", "/2to3"],
      description:
        "二进三候选列表。用法:\n" +
        "  /二进三         — 今日二进三候选 (评分排序)\n" +
        "  /二进三 3       — 三进四候选",
      async execute(args: string) {
        const minBoards = parseInt(args.trim(), 10) || 2;
        try {
          const pyResult = await runPythonScript<{
            environment: { score: number; level: string; suitable: boolean; detail: string };
            candidates: Array<{
              rank: number;
              code: string;
              name: string;
              lian_ban_count: number;
              score: number;
              turnover_rate: number;
              amount_yi: number;
              seal_amount_yi: number | null;
              open_count: number;
              industry: string;
              signals: string[];
            }>;
            total_candidates: number;
          }>(
            "scripts/lianban_dashboard.py",
            { action: "candidates", min_boards: minBoards, limit: 20 },
            config,
          );
          if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "候选数据获取失败"}`;
          const r = pyResult.data;
          const env = r.environment;

          const label = `${minBoards}进${minBoards + 1}`;
          const lines = [
            `🎯 **${label}候选** — ${r.total_candidates}只`,
            `市场情绪: ${env.score}/100 (${env.level}) ${env.suitable ? "✅适合接力" : "⚠️谨慎操作"}`,
            env.detail,
            "",
          ];

          if (!r.candidates?.length) {
            lines.push(`今日暂无${label}候选`);
            return lines.join("\n");
          }

          for (const c of r.candidates) {
            const seal = c.seal_amount_yi ? ` 封单${c.seal_amount_yi}亿` : "";
            const openCnt = c.open_count > 0 ? ` 开板${c.open_count}次` : "";
            lines.push(
              `  ${c.rank}. ${c.code} **${c.name}** ${c.lian_ban_count}板 评分${c.score} ` +
                `换手${c.turnover_rate}%${seal}${openCnt} ${c.industry}`,
            );
          }

          return lines.join("\n");
        } catch (err) {
          return `❌ 候选查询失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Sector Synergy (板块协同追涨) ───────────────────────────────────
    {
      name: "/追涨",
      aliases: ["/synergy", "/bkxt", "/sector-synergy"],
      description:
        "板块协同追涨分析。用法:\n" +
        "  /追涨              — 当前板块协同概览（各板块涨停数）\n" +
        "  /追涨 hot          — 热门板块详情（涨停≥2的板块）\n" +
        "  /追涨 002378,600519 — 分析指定股票的板块协同条件\n" +
        "  /追涨 config       — 查看策略参数说明",
      async execute(args: string) {
        const arg = args.trim();
        try {
          // Config mode
          if (arg === "config" || arg === "参数") {
            const pyResult = await runPythonScript<{
              strategy_id: string;
              name: string;
              description: string;
              buy_conditions: Array<{
                name: string;
                param: string;
                default: string | number;
                description: string;
              }>;
              sell_rules: Array<{ rule: string; trigger: string; action: string }>;
            }>("scripts/sector_synergy.py", { action: "config" }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "获取配置失败"}`;
            const r = pyResult.data;
            const lines = [`📋 **${r.name}**`, r.description, "", "**买入条件（全部满足）：**"];
            for (const c of r.buy_conditions) {
              lines.push(`  ${c.name}: ${String(c.default)} — ${c.description}`);
            }
            lines.push("", "**卖出规则：**");
            for (const s of r.sell_rules) {
              lines.push(`  ${s.rule}: ${s.trigger} → ${s.action}`);
            }
            return lines.join("\n");
          }

          // Hot mode
          if (arg === "hot" || arg === "热门") {
            const pyResult = await runPythonScript<{
              total_zt: number;
              hot_sector_count: number;
              hot_sectors: Array<{
                name: string;
                zt_count: number;
                total_amount_yi: number;
                stocks: Array<{
                  code: string;
                  name: string;
                  change_pct: number;
                  turnover_rate: number;
                }>;
              }>;
            }>("scripts/sector_synergy.py", { action: "hot", min_zt: 2 }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "板块数据获取失败"}`;
            const r = pyResult.data;
            if (!r.hot_sectors?.length) return `当前无热门板块（涨停≥2的板块）`;
            const lines = [
              `🔥 **热门板块** — 全市场涨停${r.total_zt}家, ${r.hot_sector_count}个热门板块`,
              "",
            ];
            for (const s of r.hot_sectors.slice(0, 10)) {
              lines.push(
                `**${s.name}** — ${s.zt_count}只涨停, 总成交${s.total_amount_yi.toFixed(1)}亿`,
              );
              for (const st of s.stocks.slice(0, 5)) {
                const chg = `${st.change_pct >= 0 ? "+" : ""}${st.change_pct.toFixed(1)}%`;
                lines.push(`  • ${st.code} ${st.name} ${chg} 换手${st.turnover_rate.toFixed(1)}%`);
              }
              if (s.stocks.length > 5) lines.push(`  …共 ${String(s.stocks.length)} 只`);
              lines.push("");
            }
            return lines.join("\n");
          }

          // Analyze mode — if arg contains comma or looks like stock code
          if (arg && /[\d,]/.test(arg) && arg !== "hot" && arg !== "config") {
            const symbols = arg;
            const pyResult = await runPythonScript<{
              total_zt: number;
              hot_sector_count: number;
              analysis: Array<{
                symbol: string;
                name: string;
                sector: string;
                sector_zt_count: number;
                in_hot_sector: boolean;
                change_pct: number;
                turnover_rate: number;
                eligible_reasons: string[];
                ineligible_reasons: string[];
                is_zt: boolean;
              }>;
            }>("scripts/sector_synergy.py", { action: "analyze", symbols }, config);
            if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "分析失败"}`;
            const r = pyResult.data;
            const lines = [
              `📊 **板块协同分析** — 全市场涨停${r.total_zt}家, ${r.hot_sector_count}个热门板块`,
              "",
            ];
            for (const a of r.analysis) {
              const status = a.in_hot_sector ? "✅" : "❌";
              const name = a.name ? ` ${a.name}` : "";
              lines.push(`${status} **${a.symbol}**${name} [${a.sector || "未知板块"}]`);
              if (a.eligible_reasons.length > 0) {
                lines.push(`  ✓ ${a.eligible_reasons.join(", ")}`);
              }
              if (a.ineligible_reasons.length > 0) {
                lines.push(`  ✗ ${a.ineligible_reasons.join(", ")}`);
              }
              lines.push("");
            }
            return lines.join("\n");
          }

          // Default: scan overview
          const pyResult = await runPythonScript<{
            total_zt: number;
            hot_sector_count: number;
            hot_sectors: Array<{
              name: string;
              zt_count: number;
              total_amount_yi: number;
              stocks: Array<{ code: string; name: string }>;
            }>;
            sentiment: { score?: number; level?: string };
          }>("scripts/sector_synergy.py", { action: "scan" }, config);
          if (!pyResult.ok || !pyResult.data) return `❌ ${pyResult.error ?? "板块数据获取失败"}`;
          const r = pyResult.data;
          const sent = r.sentiment;
          const lines = [
            `📊 **板块协同概览** — 涨停${r.total_zt}家, ${r.hot_sector_count}个热门板块` +
              (sent?.score ? ` | 情绪${sent.score}/100 (${sent.level ?? ""})` : ""),
            "",
          ];
          if (!r.hot_sectors?.length) {
            lines.push("当前无热门板块（无板块涨停≥2）");
          } else {
            for (const s of r.hot_sectors.slice(0, 8)) {
              const stockNames = s.stocks
                .slice(0, 4)
                .map((st) => st.name || st.code)
                .join(", ");
              lines.push(
                `  🔥 **${s.name}** ${s.zt_count}只涨停 ${s.total_amount_yi.toFixed(1)}亿  ` +
                  `(${stockNames}${s.stocks.length > 4 ? "…" : ""})`,
              );
            }
          }
          lines.push("", "💡 用法: `/追涨 hot` 看详情, `/追涨 002378,600519` 分析个股");
          return lines.join("\n");
        } catch (err) {
          return `❌ 板块协同分析失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },

    // ── Price Alerts ────────────────────────────────────────────────────
    {
      name: "/预警",
      aliases: ["/alert", "/yj"],
      description:
        "条件预警管理。用法:\n" +
        "  /预警                           — 查看所有预警\n" +
        "  /预警 add 600519 price_above 30 — 添加预警\n" +
        "  /预警 del alert-0001            — 删除预警",
      async execute(args: string) {
        if (!priceAlertService) return "❌ 条件预警服务未启动";
        const parts = args.trim().split(/\s+/);
        const subCmd = parts[0]?.toLowerCase() ?? "";

        if (subCmd === "add" || subCmd === "+" || subCmd === "添加") {
          // /预警 add <symbol> <type> <threshold> [repeating]
          const symbol = parts[1]?.toUpperCase();
          const type = parts[2] as AlertConditionType | undefined;
          const threshold = parseFloat(parts[3] ?? "");

          if (!symbol || !type || isNaN(threshold)) {
            return [
              "用法: /预警 add <代码> <条件类型> <阈值> [repeat]",
              "",
              "条件类型:",
              "  price_above  — 价格突破",
              "  price_below  — 价格跌破",
              "  change_pct_above — 涨幅超过",
              "  change_pct_below — 跌幅超过",
              "  volume_above — 成交量超过",
              "  volume_spike — 成交量异动",
              "",
              "示例: /预警 add 600519 price_above 2000",
            ].join("\n");
          }

          const validTypes: AlertConditionType[] = [
            "price_above",
            "price_below",
            "change_pct_above",
            "change_pct_below",
            "volume_above",
            "volume_spike",
          ];
          if (!validTypes.includes(type)) {
            return `❌ 无效条件类型: ${type}\n支持: ${validTypes.join(", ")}`;
          }

          const repeating = parts[4]?.toLowerCase() === "repeat";
          try {
            const id = priceAlertService.addAlert({ symbol, type, threshold, repeating });
            return `✅ 预警已添加 (${id}): ${symbol} ${type} ${String(threshold)}${repeating ? " [持续]" : ""}`;
          } catch (err) {
            return `❌ ${err instanceof Error ? err.message : String(err)}`;
          }
        }

        if (subCmd === "del" || subCmd === "rm" || subCmd === "-" || subCmd === "删除") {
          const id = parts[1];
          if (!id) return "用法: /预警 del <预警ID>  例如: /预警 del alert-0001";
          const ok = priceAlertService.removeAlert(id);
          return ok ? `🗑️ 已删除预警 ${id}` : `❌ 未找到预警 ${id}`;
        }

        // Default: list alerts
        const alerts = priceAlertService.listAlerts();
        if (alerts.length === 0) {
          return [
            "🔔 **条件预警** — 空",
            "",
            "用法:",
            "  /预警 add 600519 price_above 2000 — 添加",
            "  /预警 del alert-0001              — 删除",
          ].join("\n");
        }

        const lines = [
          `🔔 **条件预警** (${String(alerts.length)} 条, 活跃 ${String(priceAlertService.activeCount)} 条)`,
          "",
        ];
        for (const a of alerts) {
          const status = a.triggered && !a.repeating ? "✅已触发" : "⏳监控中";
          const repeat = a.repeating ? " [持续]" : "";
          lines.push(`  ${a.id}  ${status}  ${a.description}${repeat}`);
        }
        return lines.join("\n");
      },
    },

    // ── Scheduled Tasks ─────────────────────────────────────────────────
    {
      name: "/task",
      aliases: ["/定时", "/tasks"],
      description:
        "定时任务管理。用法:\n" +
        "  /task            — 查看所有任务\n" +
        "  /task cancel <id> — 取消任务",
      async execute(args: string) {
        if (!taskScheduler) return "❌ 定时任务服务未启动";
        const parts = args.trim().split(/\s+/);
        const subCmd = parts[0]?.toLowerCase() ?? "";

        if (subCmd === "cancel" || subCmd === "del" || subCmd === "取消") {
          const id = parts[1];
          if (!id) return "用法: /task cancel <任务ID>  例如: /task cancel task-1";
          const ok = taskScheduler.cancel(id);
          return ok ? `🗑️ 已取消任务 ${id}` : `❌ 无法取消任务 ${id}（可能已完成或不存在）`;
        }

        const tasks = taskScheduler.list();
        if (tasks.length === 0) {
          return "📋 **定时任务** — 空\n\n提示: 使用 AI 对话模式创建定时任务（scheduled_task 工具）";
        }

        const statusIcons: Record<string, string> = {
          pending: "⏳",
          completed: "✅",
          failed: "❌",
          cancelled: "🚫",
        };

        const lines = [
          `📋 **定时任务** (${String(tasks.length)} 条, 待执行 ${String(taskScheduler.pendingCount)} 条)`,
          "",
        ];
        for (const t of tasks.slice(0, 20)) {
          const icon = statusIcons[t.status] ?? "•";
          const meta = t.meta?.toolName
            ? ` [${t.meta.toolName}]`
            : t.meta?.reportId
              ? ` [${t.meta.reportId}]`
              : "";
          lines.push(`  ${icon} ${t.id}  ${t.description}${meta}`);
          if (t.scheduledAt) {
            lines.push(`     执行时间: ${new Date(t.scheduledAt).toLocaleString("zh-CN")}`);
          }
          if (t.lastError) lines.push(`     错误: ${t.lastError}`);
        }
        if (tasks.length > 20) {
          lines.push(`  …共 ${String(tasks.length)} 条`);
        }
        return lines.join("\n");
      },
    },

    // ── Option Commands ───────────────────────────────────────────────
    {
      name: "/期权链",
      aliases: ["/option-chain", "/oc"],
      description: "查看期权链。用法: /期权链 510050 或 /期权链 000300",
      async execute(args: string) {
        const underlying = args.trim();
        if (!underlying)
          return "用法: /期权链 <标的代码>  例如 /期权链 510050 (50ETF), /期权链 000300 (沪深300)";

        const { createOptionsDataProvider } = await import("../data/market-data-provider.js");
        const optProvider = createOptionsDataProvider(config);
        const chain = await optProvider.getOptionChain(underlying);
        if (!chain) return `无法获取 ${underlying} 的期权链数据`;

        const lines = [
          `📋 **${underlying} 期权链**`,
          `标的价格: ¥${chain.underlyingPrice.toFixed(4)}`,
          `到期日: ${chain.expiryDates.join(", ")}`,
          `合约总数: ${chain.calls.length} 看涨 + ${chain.puts.length} 看跌`,
          "",
          "**平值附近看涨合约:**",
        ];

        // Show ATM-nearby calls (5 above + 5 below)
        const sorted = chain.calls
          .filter((c) => c.strike > 0)
          .sort(
            (a, b) =>
              Math.abs(a.strike - chain.underlyingPrice) -
              Math.abs(b.strike - chain.underlyingPrice),
          )
          .slice(0, 10);

        for (const c of sorted) {
          const iv = c.impliedVol != null ? ` IV=${(c.impliedVol * 100).toFixed(1)}%` : "";
          lines.push(
            `  K=${c.strike.toFixed(4)} | ${c.price?.toFixed(4) ?? "--"} | 量=${c.volume ?? 0} | 持仓=${c.openInterest ?? 0}${iv}`,
          );
        }

        return lines.join("\n");
      },
    },
    {
      name: "/期权行情",
      aliases: ["/option-quote", "/oq"],
      description: "查看期权合约行情。用法: /期权行情 <合约代码>",
      async execute(args: string) {
        const symbol = args.trim();
        if (!symbol) return "用法: /期权行情 <合约代码>";

        const { createOptionsDataProvider } = await import("../data/market-data-provider.js");
        const optProvider = createOptionsDataProvider(config);
        const quote = await optProvider.getOptionQuote(symbol);
        if (!quote) return `无法获取 ${symbol} 的期权行情`;

        const lines = [`📊 **${quote.name ?? symbol}**`, `最新价: ${quote.price.toFixed(4)}`];
        if (quote.change != null) {
          lines.push(
            `涨跌: ${quote.change >= 0 ? "+" : ""}${quote.change.toFixed(4)} (${quote.changePct?.toFixed(2) ?? "--"}%)`,
          );
        }
        lines.push(`买一: ${quote.bid1?.toFixed(4) ?? "--"} x ${quote.bidVol1 ?? "--"}`);
        lines.push(`卖一: ${quote.ask1?.toFixed(4) ?? "--"} x ${quote.askVol1 ?? "--"}`);
        lines.push(`成交量: ${quote.volume ?? 0}  持仓量: ${quote.openInterest ?? 0}`);
        if (quote.impliedVol != null) {
          lines.push(`隐含波动率: ${(quote.impliedVol * 100).toFixed(2)}%`);
        }
        if (quote.delta != null) {
          lines.push(
            `Greeks: Δ=${quote.delta?.toFixed(4)} Γ=${quote.gamma?.toFixed(6)} Θ=${quote.theta?.toFixed(4)} V=${quote.vega?.toFixed(4)}`,
          );
        }
        return lines.join("\n");
      },
    },
    {
      name: "/希腊值",
      aliases: ["/greeks"],
      description: "计算期权希腊值。用法: /希腊值 call 3.0 0.05 30 (类型 行权价 现价 天数)",
      async execute(args: string) {
        const parts = args.trim().split(/\s+/);
        if (parts.length < 3) {
          return "用法: /希腊值 <call|put> <行权价> <现价> [天数] [波动率]\n例: /希腊值 call 3.0 3.1 30 0.25";
        }
        const optionType = parts[0];
        const strike = parseFloat(parts[1]);
        const spot = parseFloat(parts[2]);
        const dte = parseFloat(parts[3] ?? "30");
        const vol = parseFloat(parts[4] ?? "0.25");

        if (isNaN(strike) || isNaN(spot)) return "行权价和现价必须是数字";

        const result = await runPythonScript(
          "engines/greeks_engine.py",
          {
            action: "greeks",
            spot_price: spot,
            strike_price: strike,
            option_type: optionType,
            time_to_expiry: dte / 365,
            risk_free_rate: config.optionsConfig?.riskFreeRate ?? 0.025,
            volatility: vol,
          },
          config,
        );
        if (!result.ok || !result.data) return `计算失败: ${result.error ?? "未知错误"}`;

        const g = result.data as Record<string, number>;
        return [
          `📐 **Greeks** (${optionType} K=${strike} S=${spot} DTE=${dte} σ=${(vol * 100).toFixed(0)}%)`,
          `  Delta: ${g.delta?.toFixed(4)}`,
          `  Gamma: ${g.gamma?.toFixed(6)}`,
          `  Theta: ${g.theta?.toFixed(4)} /天`,
          `  Vega:  ${g.vega?.toFixed(4)} /1%`,
          `  Rho:   ${g.rho?.toFixed(4)}`,
          g.theoretical_price != null ? `  理论价: ${g.theoretical_price.toFixed(4)}` : "",
        ]
          .filter(Boolean)
          .join("\n");
      },
    },

    // ── Status Dashboard ──────────────────────────────────────────────
    {
      name: "/qstatus",
      aliases: ["/状态", "/dashboard"],
      description: "总览面板。用法: /qstatus",
      async execute() {
        const engine = getEngine?.();
        const engineStatus = engine?.getStatus?.() ?? "stopped";
        const statusLabel: Record<string, string> = {
          stopped: "⏹ 已停止",
          starting: "🔄 启动中",
          running: "▶ 运行中",
          paused: "⏸ 已暂停",
          error: "❌ 异常",
        };
        const alertCount = priceAlertService?.activeCount ?? 0;
        const taskCount = taskScheduler?.pendingCount ?? 0;

        const lines = [
          `🏠 **量化交易面板** (${new Date().toLocaleString("zh-CN")})`,
          "",
          `引擎: ${statusLabel[engineStatus] ?? engineStatus}`,
        ];

        // Account summary when engine is active
        if (engine && (engineStatus === "running" || engineStatus === "paused")) {
          const account = engine.getAccount?.();
          const positions = engine.getPositions?.() ?? [];
          if (account) {
            const todayPnl =
              account.todayProfit != null ? `¥${account.todayProfit.toFixed(0)}` : "--";
            lines.push(
              `资产: ¥${account.totalAssets.toFixed(0)}  今日: ${todayPnl}  持仓: ${String(positions.length)}只`,
            );
          }
        } else {
          // Fallback: read paper portfolio
          try {
            const dir = config.dataDir ?? path.join(process.cwd(), "data");
            const raw = await fs.readFile(path.join(dir, "portfolio.json"), "utf-8");
            const pf = JSON.parse(raw) as import("../types.js").Portfolio;
            lines.push(
              `模拟: ¥${pf.totalValue.toFixed(0)}  持仓: ${String(pf.positions.length)}只`,
            );
          } catch {
            // No portfolio data
          }
        }

        // Watchlist count
        try {
          const dir = config.dataDir ?? path.join(process.cwd(), "data");
          const wl = JSON.parse(
            await fs.readFile(path.join(dir, "watchlist.json"), "utf-8"),
          ) as string[];
          if (wl.length > 0) lines.push(`自选: ${String(wl.length)}只`);
        } catch {
          // No watchlist
        }

        lines.push(`预警: ${String(alertCount)}条  定时: ${String(taskCount)}条`);

        // Signals count
        try {
          const dir = config.dataDir ?? path.join(process.cwd(), "data");
          const sigs = JSON.parse(
            await fs.readFile(path.join(dir, "signals.json"), "utf-8"),
          ) as unknown[];
          const today = todayCST();
          const todayCount = sigs.filter(
            (s) =>
              typeof s === "object" &&
              s !== null &&
              "timestamp" in s &&
              String((s as Record<string, unknown>).timestamp).startsWith(today),
          ).length;
          if (todayCount > 0) lines.push(`今日信号: ${String(todayCount)}条`);
        } catch {
          // No signals
        }

        lines.push("", "快捷: /持仓 /盈亏 /自选 /预警 /简报 /quant(帮助)");

        return lines.join("\n");
      },
    },

    // ── 估值 (Value Investing / Financial Calculator) ────────────────────
    {
      name: "/估值",
      aliases: ["/valuation", "/value"],
      description: "快速估值分析。用法: /估值 600519",
      async execute(rawArgs: string) {
        const sym = rawArgs?.trim();
        if (!sym) return "用法: /估值 <代码>  例: /估值 600519";
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/financial_calculator_engine.py",
          { action: "compare", symbol: sym, growthRate: 0.08, discountRate: 0.1 },
          config,
        );
        if (!result.ok) return `估值失败: ${result.error ?? "未知错误"}`;
        const d = result.data as Record<string, unknown>;
        const name = (d.name as string) || sym;
        const price = d.currentPrice as number;
        const models = d.models as Record<string, Record<string, unknown>> | undefined;
        const lines = [`📊 **${name}** (${sym}) 多模型估值 — 当前价 ¥${String(price ?? "—")}`];
        if (models) {
          const dcf = models.dcf;
          if (dcf && !dcf.error)
            lines.push(`  DCF: ¥${String(dcf.perShare ?? "—")}  (${String(dcf.upside ?? 0)}%)`);
          const pe = models.pe;
          if (pe)
            lines.push(`  PE: ${String(pe.currentPE ?? "—")}x  ${String(pe.pePercentile ?? "")}`);
          const pb = models.pb;
          if (pb)
            lines.push(`  PB: ${String(pb.currentPB ?? "—")}x  ${String(pb.interpretation ?? "")}`);
          const peg = models.peg;
          if (peg)
            lines.push(`  PEG: ${String(peg.peg ?? "—")}  ${String(peg.interpretation ?? "")}`);
        }
        return lines.join("\n");
      },
    },

    // ── 新闻 (News Summary) ─────────────────────────────────────────────
    {
      name: "/新闻",
      aliases: ["/news"],
      description: "财经新闻摘要。用法: /新闻 或 /新闻 600519",
      async execute(rawArgs: string) {
        const sym = rawArgs?.trim();
        const action = sym ? "stock_news" : "market_news";
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/news_engine.py",
          { action, symbol: sym, market: "cn", limit: 8 },
          config,
        );
        if (!result.ok) return `新闻获取失败: ${result.error ?? "未知错误"}`;
        const d = result.data as Record<string, unknown>;
        const news = d.news as
          | Array<{ title?: string; source?: string; time?: string }>
          | undefined;
        if (!news || news.length === 0) return "暂无新闻";
        const lines = [`📰 **最新财经新闻** ${sym ? `(${sym})` : ""}`];
        for (const [i, n] of news.entries()) {
          lines.push(`${String(i + 1)}. ${n.title ?? ""}  — ${n.source ?? ""} ${n.time ?? ""}`);
        }
        return lines.join("\n");
      },
    },

    // ── CFO分析 ──────────────────────────────────────────────────────────
    {
      name: "/cfo",
      aliases: ["/财报", "/dupont"],
      description: "CFO级财报分析。用法: /cfo 600519",
      async execute(rawArgs: string) {
        const sym = rawArgs?.trim();
        if (!sym) return "用法: /cfo <代码>  例: /cfo 600519";
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/cfo_engine.py",
          { action: "comprehensive", symbol: sym, years: 3, period: "annual" },
          config,
        );
        if (!result.ok) return `分析失败: ${result.error ?? "未知错误"}`;
        const d = result.data as Record<string, unknown>;
        const name = (d.name as string) || sym;
        const lines = [`📋 **${name}** (${sym}) CFO分析`];
        const ratios = (d.ratios as Record<string, unknown>) ?? {};
        const profit = ratios.profitability as Record<string, number> | undefined;
        if (profit) {
          lines.push(
            `  毛利率: ${String(profit.grossMargin ?? "—")}%  净利率: ${String(profit.netMargin ?? "—")}%`,
          );
          lines.push(`  ROE: ${String(profit.roe ?? "—")}%  ROA: ${String(profit.roa ?? "—")}%`);
        }
        const flags = (d.redFlags as Record<string, unknown>) ?? {};
        const riskLevel = flags.riskLevel as string;
        if (riskLevel)
          lines.push(`  风险等级: ${riskLevel} (${String(flags.flagCount ?? 0)}项预警)`);
        return lines.join("\n");
      },
    },

    // ── Help ─────────────────────────────────────────────────────────────
    {
      name: "/quant",
      aliases: ["/quant-help", "/量化", "/帮助"],
      description: "量化交易插件帮助。用法: /quant 或 /帮助",
      async execute() {
        const alertCount = priceAlertService?.activeCount ?? 0;
        const engineStatus = getEngine?.()?.getStatus?.() ?? "未启动";

        return [
          "📊 **量化交易插件** — 命令列表",
          "",
          "**行情分析**",
          "  `/quote <代码>`    — 实时行情 (如 /quote 600519)",
          "  `/自选`            — 自选股行情 (add/del/clear)",
          "  `/板块 [名称]`     — 板块涨跌排名 / 成分股详情",
          "  `/资金 [代码]`     — 主力资金流向 / 北向资金",
          "  `/龙虎榜 [代码]`   — 龙虎榜数据",
          "  `/连板 [代码]`     — 连板梯队 / 个股连板分析",
          "  `/二进三 [N]`      — N进N+1候选列表 (默认二进三)",
          "",
          "**筛选与策略**",
          "  `/选股 [类别]`     — 多因子智能选股",
          "  `/screen <条件>`   — 简单条件筛选 (如 pe<20 roe>15)",
          "  `/backtest <策略>` — 快速策略回测",
          "  `/信号`            — 查看最近交易信号",
          "",
          "**实盘交易**",
          "  `/持仓`            — 查看当前持仓与盈亏",
          "  `/orders [代码]`   — 今日订单",
          "  `/pnl`             — 今日实盘盈亏",
          "  `/confirm <id>`    — 确认待审订单",
          "  `/reject <id>`     — 拒绝待审订单",
          "  `/pause` / `/resume` — 暂停/恢复引擎",
          "",
          "**监控与预警**",
          `  \`/预警\`            — 条件预警 (活跃: ${String(alertCount)} 条)`,
          `  \`/task\`            — 定时任务 (待执行: ${String(taskScheduler?.pendingCount ?? 0)} 条)`,
          "",
          "**报告快捷**",
          "  `/简报`            — 早盘简报 (板块+资金+持仓)",
          "  `/收盘`            — 收盘总结 (指数+龙虎榜+北向)",
          "  `/日志`            — 每日交易日志",
          "  `/周报`            — 周度回顾",
          "  `/月报`            — 月度绩效报告",
          "  `/选股报告`        — 每日选股扫描结果",
          "",
          "**复盘与分析** (对话模式，直接提问即可)",
          "  交易笔记 — 记录买卖心得、标签、评分",
          "  绩效归因 — 按策略/标的/时间分析盈亏来源",
          "  策略健康 — 胜率追踪、连亏预警、沉寂检测",
          "  相关性分析 — 标的配对、价差Zֵ",
          "",
          "**诊断工具**",
          "  `/大盘诊断`        — 指数+板块+资金+情绪 一键综合诊断",
          "  `/诊断 <代码>`     — 个股综合诊断 (行情+估值+资金+吸筹+财务)",
          "  `/持仓诊断`        — 逐只持仓诊断 (盈亏+资金+操作建议)",
          "  `/同类对比 <代码>` — 同行业估值对比",
          "  `/交易记录 [代码]` — 查看历史成交记录",
          "",
          "**估值与财报**",
          "  `/估值 <代码>`     — 多模型估值 (DCF/PE/PB/PEG)",
          "  `/cfo <代码>`      — CFO级财报分析 (杜邦/比率/风险)",
          "  价值投资分析       — Graham/Buffett风格 (对话模式)",
          "",
          "**新闻与数据**",
          "  `/新闻 [代码]`     — 财经新闻摘要/个股新闻",
          "  预测市场赔率       — Polymarket预测事件 (对话模式)",
          "  数据分析           — 统计/回归/异常检测 (对话模式)",
          "",
          '  💡 示例: "帮我记录今天买600519的理由"',
          '  💡 示例: "分析上周的盈亏归因"',
          '  💡 示例: "我的双均线策略最近表现怎么样"',
          '  💡 示例: "600519和000858的相关性如何"',
          "",
          "**期权工具**",
          "  `/期权链 <标的>`   — 查看期权T型报价",
          "  `/期权行情 <合约>` — 期权合约行情",
          "  `/希腊值 <参数>`   — 计算Greeks",
          "",
          "**宏观与机构**",
          "  `/宏观 [指标]`     — 宏观经济数据 (GDP/CPI/PMI/利率)",
          "  `/财报日历`        — 近期即将披露财报的公司",
          "  `/机构持仓 <代码>` — 基金/机构持仓与变动",
          "  `/大宗交易 [代码]` — 近期大宗交易明细",
          "  `/舆情 [代码]`     — 社区讨论热度与情绪",
          "  `/热股`            — 今日社区人气排名",
          "",
          "**总览**",
          "  `/状态`            — 一键看全局 (资产+持仓+预警+信号)",
          "",
          `引擎状态: ${engineStatus}`,
        ].join("\n");
      },
    },

    // ── Macro economic data ──────────────────────────────────────────────
    {
      name: "/macro",
      aliases: ["/宏观"],
      description: "宏观经济数据。用法: /宏观 [指标] 如 gdp, cpi, pmi, lpr, m2",
      async execute(args: string) {
        const indicator = args.trim().toLowerCase();
        const action = indicator ? "history" : "regime";
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/macro_economic.py",
          { action, indicator: indicator || undefined, market: "cn", limit: 12 },
          config,
        );
        if (!pyResult.ok) return `❌ 宏观数据获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        return JSON.stringify(d, null, 2);
      },
    },

    // ── Earnings calendar ────────────────────────────────────────────────
    {
      name: "/earnings",
      aliases: ["/财报日历", "/财报"],
      description: "近期财报日历。用法: /财报日历 或 /earnings",
      async execute(args: string) {
        const days = parseInt(args.trim()) || 30;
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/earnings_calendar.py",
          { action: "upcoming", market: "cn", days, limit: 30 },
          config,
        );
        if (!pyResult.ok) return `❌ 财报日历获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        const earnings = (d.earnings as Array<Record<string, unknown>>) ?? [];
        if (earnings.length === 0) return `近${String(days)}天内暂无找到即将披露的财报`;
        const lines = earnings
          .slice(0, 30)
          .map((e) => `${String(e.disclosure_date)} | ${String(e.symbol)} ${String(e.name)}`);
        return [`📅 近${String(days)}天财报日历 (共${String(d.count)}家)`, "", ...lines].join("\n");
      },
    },

    // ── Institutional holdings ────────────────────────────────────────────
    {
      name: "/institutional",
      aliases: ["/机构持仓", "/机构"],
      description: "机构持仓与变动。用法: /机构持仓 600519",
      async execute(args: string) {
        const symbol = args.trim();
        if (!symbol) return "用法: /机构持仓 <代码>\n例如: /机构持仓 600519";
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/institutional_flow.py",
          { action: "smart_money_signal", symbol },
          config,
        );
        if (!pyResult.ok) return `❌ 机构数据获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        return JSON.stringify(d, null, 2);
      },
    },

    // ── Block trades ─────────────────────────────────────────────────────
    {
      name: "/block-trades",
      aliases: ["/大宗交易", "/大宗"],
      description: "大宗交易明细。用法: /大宗交易 [代码]",
      async execute(args: string) {
        const symbol = args.trim();
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/institutional_flow.py",
          { action: "block_trades", symbol: symbol || undefined, limit: 20 },
          config,
        );
        if (!pyResult.ok) return `❌ 大宗交易获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        return JSON.stringify(d, null, 2);
      },
    },

    // ── Social sentiment ─────────────────────────────────────────────────
    {
      name: "/sentiment",
      aliases: ["/舆情"],
      description: "个股舆情分析。用法: /舆情 600519",
      async execute(args: string) {
        const symbol = args.trim();
        if (!symbol) return "用法: /舆情 <代码>\n例如: /舆情 600519";
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/social_sentiment.py",
          { action: "stock_sentiment", symbol },
          config,
        );
        if (!pyResult.ok) return `❌ 舆情数据获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        return JSON.stringify(d, null, 2);
      },
    },

    // ── Hot stocks ───────────────────────────────────────────────────────
    {
      name: "/hot-stocks",
      aliases: ["/热股", "/热门"],
      description: "今日社区讨论热度排名",
      async execute(args: string) {
        const limit = parseInt(args.trim()) || 20;
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "engines/social_sentiment.py",
          { action: "hot_stocks", market: "cn", limit },
          config,
        );
        if (!pyResult.ok) return `❌ 热股数据获取失败: ${pyResult.error ?? "unknown"}`;
        const d = pyResult.data!;
        if (d.error) return `❌ ${String(d.error)}`;
        const stocks = (d.hot_stocks as Array<Record<string, unknown>>) ?? [];
        if (stocks.length === 0) return "暂无热门股数据";
        const lines = stocks
          .slice(0, limit)
          .map(
            (s) =>
              `${String(s.rank).padStart(2, " ")}. ${String(s.symbol)} ${String(s.name)} (热度: ${String(s.hot_score ?? "-")})`,
          );
        return [`🔥 今日社区热度排名 (共${String(d.count)}只)`, "", ...lines].join("\n");
      },
    },

    // ── 大盘诊断 (Market Diagnosis) ──────────────────────────────
    {
      name: "/大盘诊断",
      aliases: ["/market-diagnosis", "/dpzd"],
      description:
        "大盘综合诊断：指数行情+板块+资金+情绪+趋势，一键了解市场状态。用法: /大盘诊断",
      async execute() {
        const lines: string[] = ["🏥 **大盘综合诊断**", ""];

        // 1. Index data — try to get major indices
        const indices = ["000001", "399001", "399006"]; // 上证/深证/创业板
        const indexNames: Record<string, string> = {
          "000001": "上证指数",
          "399001": "深证成指",
          "399006": "创业板指",
        };
        const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");
        lines.push("**指数行情**");
        for (const idx of indices) {
          try {
            const market = detectMarketFromSymbol(idx);
            const q = await provider.getQuote(idx, market);
            if (q) {
              const arrow = q.changePct >= 0 ? "📈" : "📉";
              const chg = `${q.changePct >= 0 ? "+" : ""}${q.changePct.toFixed(2)}%`;
              const vol = q.amount >= 1e8 ? `${(q.amount / 1e8).toFixed(0)}亿` : "--";
              lines.push(
                `  ${arrow} ${indexNames[idx] ?? idx}  ¥${q.price.toFixed(2)} ${chg}  成交额: ${vol}`,
              );
            }
          } catch {
            // skip
          }
        }
        lines.push("");

        // 2. Sector ranking (top 5 / bottom 5)
        try {
          const sectorResult = await runPythonScript<{
            sectors: Array<{
              name: string;
              change_pct: number;
              leader: string;
              leader_change: number;
            }>;
          }>("engines/sector_analysis.py", { action: "ranking", limit: 30 }, config);
          if (sectorResult.ok && sectorResult.data?.sectors?.length) {
            const sectors = sectorResult.data.sectors;
            lines.push("**板块涨幅前5**");
            for (const s of sectors.slice(0, 5)) {
              const chg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
              lines.push(`  📈 ${s.name} ${chg}  领涨: ${s.leader}`);
            }
            lines.push("");
            lines.push("**板块跌幅前5**");
            const bottom = [...sectors].sort((a, b) => a.change_pct - b.change_pct).slice(0, 5);
            for (const s of bottom) {
              const chg = `${s.change_pct >= 0 ? "+" : ""}${s.change_pct.toFixed(2)}%`;
              lines.push(`  📉 ${s.name} ${chg}  领涨: ${s.leader}`);
            }
            lines.push("");

            // Market breadth
            const upCount = sectors.filter((s) => s.change_pct > 0).length;
            const downCount = sectors.filter((s) => s.change_pct < 0).length;
            lines.push(
              `**板块涨跌比**: ${upCount}涨 / ${downCount}跌 / ${sectors.length - upCount - downCount}ƽ`,
            );
          }
        } catch {
          lines.push("板块数据: 获取失败");
        }
        lines.push("");

        // 3. Fund flow
        try {
          const flowResult = await runPythonScript<{
            stocks: Array<{ code: string; name: string; net_inflow: number; change_pct: number }>;
          }>("engines/fund_flow.py", { action: "rank", limit: 5 }, config);
          if (flowResult.ok && flowResult.data?.stocks?.length) {
            lines.push("**主力资金净流入 Top5**");
            for (const s of flowResult.data.stocks) {
              const inflow = `${(s.net_inflow / 10000).toFixed(0)}万`;
              lines.push(`  ${s.code} ${s.name}  净流入: ${inflow}`);
            }
            lines.push("");
          }
        } catch {
          // skip
        }

        // 4. Lianban sentiment
        try {
          const lbResult = await runPythonScript<{
            summary: {
              total_zt: number;
              highest_board: number;
              sentiment_score: number;
              sentiment_level: string;
              zt_dt_ratio: number;
              failure_rate: number;
            };
          }>("scripts/lianban_dashboard.py", { action: "scan" }, config);
          if (lbResult.ok && lbResult.data?.summary) {
            const s = lbResult.data.summary;
            lines.push("**市场情绪**");
            lines.push(
              `  涨停: ${s.total_zt}家  最高板: ${s.highest_board}板  涨跌停比: ${s.zt_dt_ratio}`,
            );
            lines.push(
              `  情绪评分: ${s.sentiment_score}/100 (${s.sentiment_level})  炸板率: ${(s.failure_rate * 100).toFixed(0)}%`,
            );
            lines.push("");
          }
        } catch {
          // skip
        }

        // 5. Diagnosis conclusion
        lines.push("**综合诊断**");
        // Simple heuristic based on collected data
        const indexQuote = await provider.getQuote("000001", "cn_stock").catch(() => null);
        if (indexQuote) {
          const pct = indexQuote.changePct;
          if (pct > 1.5) {
            lines.push("  🟢 大盘强势上攻，注意追高风险，关注量能持续性");
          } else if (pct > 0.3) {
            lines.push("  🟢 大盘小幅上涨，市场偏暖，关注板块轮动方向");
          } else if (pct > -0.3) {
            lines.push("  🟡 大盘窄幅震荡，方向不明，建议控制仓位等待确认");
          } else if (pct > -1.5) {
            lines.push("  🟠 大盘偏弱调整，注意止损纪律，减少追涨操作");
          } else {
            lines.push("  🔴 大盘大幅下跌，建议防守为主，关注系统性风险");
          }
        }
        lines.push("", "💡 深度分析可用对话模式: 「帮我全面分析今日大盘」");

        return lines.join("\n");
      },
    },

    // ── 个股诊断 (Stock Diagnosis) ──────────────────────────────
    {
      name: "/个股诊断",
      aliases: ["/stock-diagnosis", "/诊断", "/diagnosis"],
      description:
        "个股综合诊断：行情+技术面+基本面+资金面+吸筹信号，一键出结论。用法: /诊断 600519",
      async execute(args: string) {
        const symbol = args.trim();
        if (!symbol) return "用法: /诊断 <代码>  例如: /诊断 600519";

        const lines: string[] = [];
        const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");
        const market = detectMarketFromSymbol(symbol);

        // 1. Quote
        let stockName = symbol;
        try {
          const q = await provider.getQuote(symbol, market);
          if (q) {
            stockName = q.symbol.name ?? symbol;
            const arrow = q.changePct >= 0 ? "📈" : "📉";
            const chg = `${q.changePct >= 0 ? "+" : ""}${q.changePct.toFixed(2)}%`;
            const vol =
              q.volume >= 1e8
                ? `${(q.volume / 1e8).toFixed(2)}亿`
                : `${(q.volume / 1e4).toFixed(0)}万`;
            lines.push(
              `🏥 **${stockName}** (${symbol}) 综合诊断`,
              "",
              `**实时行情**`,
              `  ${arrow} ¥${q.price.toFixed(2)} ${chg}  ` +
                `开: ¥${q.open.toFixed(2)}  高: ¥${q.high.toFixed(2)}  低: ¥${q.low.toFixed(2)}`,
              `  成交量: ${vol}`,
            );
          } else {
            lines.push(`🏥 **${symbol}** 综合诊断`, "", "行情数据: 获取失败");
          }
        } catch {
          lines.push(`🏥 **${symbol}** 综合诊断`, "", "行情数据: 获取失败");
        }
        lines.push("");

        // 2. Fundamentals (via financial_calculator)
        try {
          const fundResult = await runPythonScript<Record<string, unknown>>(
            "engines/financial_calculator_engine.py",
            { action: "compare", symbol, growthRate: 0.08, discountRate: 0.1 },
            config,
          );
          if (fundResult.ok && fundResult.data) {
            const d = fundResult.data;
            const models = d.models as Record<string, Record<string, unknown>> | undefined;
            lines.push("**估值分析**");
            if (models) {
              const pe = models.pe;
              if (pe)
                lines.push(
                  `  PE: ${String(pe.currentPE ?? "—")}x  ${String(pe.pePercentile ?? "")}`,
                );
              const pb = models.pb;
              if (pb)
                lines.push(
                  `  PB: ${String(pb.currentPB ?? "—")}x  ${String(pb.interpretation ?? "")}`,
                );
              const peg = models.peg;
              if (peg)
                lines.push(
                  `  PEG: ${String(peg.peg ?? "—")}  ${String(peg.interpretation ?? "")}`,
                );
              const dcf = models.dcf;
              if (dcf && !dcf.error)
                lines.push(
                  `  DCF估值: ¥${String(dcf.perShare ?? "—")}  空间: ${String(dcf.upside ?? 0)}%`,
                );
            }
            lines.push("");
          }
        } catch {
          // skip
        }

        // 3. Fund flow
        try {
          const flowResult = await runPythonScript<{
            code: string;
            name: string;
            main_inflow: number;
            main_outflow: number;
            net_inflow: number;
            retail_net: number;
          }>("engines/fund_flow.py", { action: "stock", symbol }, config);
          if (flowResult.ok && flowResult.data) {
            const f = flowResult.data;
            const netStr =
              f.net_inflow >= 0
                ? `+${(f.net_inflow / 10000).toFixed(0)}万`
                : `${(f.net_inflow / 10000).toFixed(0)}万`;
            lines.push("**资金流向**");
            lines.push(`  主力净流入: ${netStr}  散户净流入: ${((f.retail_net ?? 0) / 10000).toFixed(0)}万`);
            const signal =
              f.net_inflow > 0 ? "🟢 主力资金流入" : f.net_inflow < -5000000 ? "🔴 主力大幅流出" : "🟡 资金面中性";
            lines.push(`  ${signal}`);
            lines.push("");
          }
        } catch {
          // skip
        }

        // 4. Chip accumulation
        try {
          const chipResult = await runPythonScript<{
            symbol: string;
            total_score: number;
            level: string;
            conclusion: string;
            advice: string;
          }>("engines/chip_accumulation.py", { action: "detect", symbol, lookback: 30 }, config);
          if (chipResult.ok && chipResult.data) {
            const c = chipResult.data;
            const icon =
              c.total_score >= 70
                ? "🟢"
                : c.total_score >= 50
                  ? "🟡"
                  : c.total_score >= 30
                    ? "⚪"
                    : "🔴";
            lines.push("**吸筹检测**");
            lines.push(`  ${icon} 评分: ${c.total_score.toFixed(1)}/100  ${c.conclusion}`);
            lines.push(`  💡 ${c.advice}`);
            lines.push("");
          }
        } catch {
          // skip
        }

        // 5. CFO quick
        try {
          const cfoResult = await runPythonScript<Record<string, unknown>>(
            "engines/cfo_engine.py",
            { action: "comprehensive", symbol, years: 3, period: "annual" },
            config,
          );
          if (cfoResult.ok && cfoResult.data) {
            const d = cfoResult.data;
            const ratios = (d.ratios as Record<string, unknown>) ?? {};
            const profit = ratios.profitability as Record<string, number> | undefined;
            if (profit) {
              lines.push("**财务健康**");
              lines.push(
                `  毛利率: ${String(profit.grossMargin ?? "—")}%  净利率: ${String(profit.netMargin ?? "—")}%  ROE: ${String(profit.roe ?? "—")}%`,
              );
              const flags = (d.redFlags as Record<string, unknown>) ?? {};
              const riskLevel = flags.riskLevel as string;
              if (riskLevel)
                lines.push(
                  `  风险等级: ${riskLevel} (${String(flags.flagCount ?? 0)}项预警)`,
                );
              lines.push("");
            }
          }
        } catch {
          // skip
        }

        // 6. Diagnosis conclusion
        lines.push(
          "💡 完整研报可用对话模式: 「帮我全面分析」 + 股票名",
        );

        return lines.join("\n");
      },
    },

    // ── 持仓诊断 (Position Diagnosis) ───────────────────────────
    {
      name: "/持仓诊断",
      aliases: ["/position-diagnosis", "/cczd"],
      description:
        "持仓逐只诊断：盈亏+资金面+估值+建议，帮助决定加仓/减仓/止损。用法: /持仓诊断",
      async execute() {
        const { detectMarketFromSymbol } = await import("../data/market-data-provider.js");

        // Load positions (prefer live engine, fallback paper)
        const engine = getEngine?.();
        let positions: Array<{
          symbol: string;
          name?: string;
          quantity: number;
          avgCost: number;
          currentPrice?: number;
          unrealizedPnlPct?: number;
        }> = [];

        if (engine && (engine.getStatus?.() === "running" || engine.getStatus?.() === "paused")) {
          positions = (engine.getPositions?.() ?? []).map((p) => ({
            symbol: p.symbol,
            name: p.name,
            quantity: p.quantity,
            avgCost: p.avgCost,
            currentPrice: p.currentPrice,
            unrealizedPnlPct: p.unrealizedPnlPct,
          }));
        } else {
          try {
            const dir = config.dataDir ?? path.join(process.cwd(), "data");
            const pfPath = path.join(dir, "portfolio.json");
            const raw = await fs.readFile(pfPath, "utf-8");
            const pf = JSON.parse(raw) as import("../types.js").Portfolio;
            positions = pf.positions.map((p) => ({
              symbol: p.symbol,
              name: p.name,
              quantity: p.quantity,
              avgCost: p.avgCost,
              currentPrice: p.currentPrice,
              unrealizedPnlPct: p.unrealizedPnlPct,
            }));
          } catch {
            return "📊 暂无持仓数据，无法进行诊断。";
          }
        }

        if (positions.length === 0) return "📊 当前空仓，无需诊断。";

        const lines: string[] = [
          `🏥 **持仓诊断** (${positions.length}只)`,
          "",
        ];

        for (const pos of positions) {
          const name = pos.name ?? pos.symbol;
          lines.push(`━━━━━ **${name}** (${pos.symbol}) ━━━━━`);

          // Update price if not available
          let price = pos.currentPrice;
          let pnlPct = pos.unrealizedPnlPct;
          if (!price) {
            try {
              const market = detectMarketFromSymbol(pos.symbol);
              const q = await provider.getQuote(pos.symbol, market);
              if (q) {
                price = q.price;
                pnlPct = ((q.price - pos.avgCost) / pos.avgCost) * 100;
              }
            } catch {
              // skip
            }
          }

          const priceStr = price ? `¥${price.toFixed(2)}` : "--";
          const pnlStr =
            pnlPct != null
              ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%`
              : "--";
          const icon = (pnlPct ?? 0) >= 5 ? "🟢" : (pnlPct ?? 0) >= 0 ? "🟡" : (pnlPct ?? 0) > -5 ? "🟠" : "🔴";

          lines.push(
            `  ${icon} ${pos.quantity}股  成本: ¥${pos.avgCost.toFixed(2)}  现价: ${priceStr}  浮盈: ${pnlStr}`,
          );

          // Fund flow quick check
          try {
            const flowResult = await runPythonScript<{
              net_inflow: number;
            }>("engines/fund_flow.py", { action: "stock", symbol: pos.symbol }, config);
            if (flowResult.ok && flowResult.data) {
              const net = flowResult.data.net_inflow;
              const flowIcon = net > 0 ? "🟢" : net < -5000000 ? "🔴" : "🟡";
              lines.push(
                `  资金: ${flowIcon} 主力净流入 ${(net / 10000).toFixed(0)}万`,
              );
            }
          } catch {
            // skip
          }

          // Generate advice based on P&L
          if (pnlPct != null) {
            if (pnlPct <= -10) {
              lines.push("  ⚠️ 建议: 亏损较大，严格执行止损纪律，考虑减仓或清仓");
            } else if (pnlPct <= -5) {
              lines.push("  ⚠️ 建议: 已达预警线，密切关注支撑位，跌破即减仓");
            } else if (pnlPct >= 20) {
              lines.push("  💰 建议: 盈利丰厚，考虑分批止盈锁定利润");
            } else if (pnlPct >= 10) {
              lines.push("  💰 建议: 盈利可观，可上移止损位保护利润");
            } else if (pnlPct >= 0) {
              lines.push("  💡 建议: 小幅盈利，关注趋势方向，设好止损");
            } else {
              lines.push("  💡 建议: 小幅浮亏，关注支撑位和资金面变化");
            }
          }
          lines.push("");
        }

        // Overall summary
        const totalPnl = positions.reduce((sum, p) => {
          const pnl = p.currentPrice
            ? (p.currentPrice - p.avgCost) * p.quantity
            : 0;
          return sum + pnl;
        }, 0);
        lines.push(
          `**组合浮盈**: ¥${totalPnl.toFixed(0)}`,
          "",
          "💡 个股深度分析: /诊断 <代码>",
        );

        return lines.join("\n");
      },
    },

    // ── 交易记录 (Trade History) ────────────────────────────────
    {
      name: "/交易记录",
      aliases: ["/trades", "/历史成交", "/trade-history"],
      description:
        "查看历史成交记录。用法:\n" +
        "  /交易记录          — 查看最近成交\n" +
        "  /交易记录 600519   — 查看个股成交记录",
      async execute(args: string) {
        const filterSymbol = args.trim().toUpperCase() || undefined;

        // Try live engine first
        const engine = getEngine?.();
        if (engine && engine.orderManager) {
          try {
            const trades = await engine.orderManager.getTrades();
            const filtered = filterSymbol
              ? trades.filter((t: Record<string, unknown>) => t.symbol === filterSymbol)
              : trades;
            if (filtered.length === 0) {
              return filterSymbol
                ? `暂无 ${filterSymbol} 的成交记录`
                : "暂无成交记录";
            }
            const lines = [`📋 **成交记录** (${filtered.length}笔)`, ""];
            for (const t of (filtered as Array<Record<string, unknown>>).slice(-30)) {
              const side = t.side === "buy" ? "🟢买" : "🔴卖";
              const time = t.filledAt
                ? new Date(t.filledAt as string).toLocaleString("zh-CN", {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "--";
              lines.push(
                `  ${side} ${time}  ${String(t.symbol)} ${String(t.quantity ?? t.filledQty)}股 @ ¥${Number(t.price ?? t.filledPrice).toFixed(2)}`,
              );
            }
            return lines.join("\n");
          } catch {
            // fall through
          }
        }

        // Fallback: read paper trade history
        try {
          const dir = config.dataDir ?? path.join(process.cwd(), "data");
          const histPath = path.join(dir, "trade-history.json");
          const raw = await fs.readFile(histPath, "utf-8");
          const history = JSON.parse(raw) as Array<{
            timestamp: string;
            symbol: string;
            name?: string;
            side: string;
            quantity: number;
            price: number;
            amount: number;
          }>;
          const filtered = filterSymbol
            ? history.filter((t) => t.symbol === filterSymbol)
            : history;
          if (filtered.length === 0) {
            return filterSymbol
              ? `暂无 ${filterSymbol} 的模拟成交记录`
              : "暂无模拟成交记录。买卖后成交记录将自动保存。";
          }
          const lines = [`📋 **模拟成交记录** (${filtered.length}笔)`, ""];
          for (const t of filtered.slice(-30)) {
            const side = t.side === "buy" ? "🟢买" : "🔴卖";
            const time = new Date(t.timestamp).toLocaleString("zh-CN", {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            });
            const pnlStr = t.side === "sell" ? `  金额: ¥${t.amount.toFixed(0)}` : "";
            lines.push(
              `  ${side} ${time}  ${t.symbol} ${t.name ?? ""} ${t.quantity}股 @ ¥${t.price.toFixed(2)}${pnlStr}`,
            );
          }
          return lines.join("\n");
        } catch {
          return "暂无成交记录。买卖后成交记录将自动保存。";
        }
      },
    },

    // ── 同类对比 (Peer Comparison) ──────────────────────────────
    {
      name: "/同类对比",
      aliases: ["/peer", "/peer-compare"],
      description: "同行业对比分析。用法: /同类对比 600519",
      async execute(args: string) {
        const symbol = args.trim();
        if (!symbol) return "用法: /同类对比 <代码>  例: /同类对比 600519";
        try {
          const pyResult = await runPythonScript<{
            target: { code: string; name: string; [k: string]: unknown };
            peers: Array<{ code: string; name: string; [k: string]: unknown }>;
            industry: string;
          }>("engines/peer_compare.py", { action: "compare", symbol, limit: 8 }, config);
          if (!pyResult.ok || !pyResult.data)
            return `❌ ${pyResult.error ?? "同类对比数据获取失败"}`;
          const d = pyResult.data;
          const lines = [
            `📊 **${d.target.name}** (${d.target.code}) 同类对比 — ${d.industry}`,
            "",
          ];
          // Format target
          const t = d.target;
          lines.push(
            `  🎯 ${t.code} ${t.name}  PE: ${String(t.pe ?? "—")}  PB: ${String(t.pb ?? "—")}  ROE: ${String(t.roe ?? "—")}%  市值: ${String(t.market_cap ?? "—")}`,
          );
          lines.push("");
          lines.push("**同行业公司**");
          for (const p of d.peers) {
            lines.push(
              `  • ${p.code} ${p.name}  PE: ${String(p.pe ?? "—")}  PB: ${String(p.pb ?? "—")}  ROE: ${String(p.roe ?? "—")}%  市值: ${String(p.market_cap ?? "—")}`,
            );
          }
          return lines.join("\n");
        } catch (err) {
          return `❌ 同类对比失败: ${err instanceof Error ? err.message : String(err)}`;
        }
      },
    },
  ];
}
