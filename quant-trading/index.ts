/**
 * @openclaw/quant-trading — main plugin entry point
 *
 * Architecture overview:
 * ┌─────────────────────────────────────────────────────────────────┐
 * │  OpenClaw Gateway (Node.js / TypeScript)                       │
 * │                                                                │
 * │  ┌──────────────────────────────────────────────────────────┐  │
 * │  │  quant-trading extension                                 │  │
 * │  │                                                          │  │
 * │  │  Tools (AI-callable):                                    │  │
 * │  │    • market_data      — fetch quotes, klines, fundamentals│  │
 * │  │    • stock_screen     — multi-factor stock screening      │  │
 * │  │    • backtest         — run strategy backtests             │  │
 * │  │    • portfolio        — view/manage portfolio positions    │  │
 * │  │    • trading_signal   — generate/query signals             │  │
 * │  │    • risk_analysis    — VaR, drawdown, Sharpe, etc.        │  │
 * │  │    • futures_straddle — futures spread & arbitrage         │  │
 * │  │    • live_trading     — control real-time trading engine   │  │
 * │  │    • data_cache       — data warehouse / cache management  │  │
 * │  │    • strategy_optimizer — parameter optimization           │  │
 * │  │    • sector_analysis  — sector/industry ranking & detail   │  │
 * │  │    • fund_flow        — capital flow tracking              │  │
 * │  │    • dragon_tiger     — dragon tiger board data            │  │
 * │  │    • sector_synergy   — sector synergy chase analysis     │  │
 * │  │    • scheduled_task   — delayed retry / periodic push      │  │
 * │  │    • price_alert      — condition-based price monitoring   │  │
 * │  │    • options_data     — option chain, quotes, IV surface   │  │
 * │  │    • options_strategy — strategy recommend, payoff, greeks │  │
 * │  │    • options_trading  — option order placement & positions │  │
 * │  │    • stochastic_pricing — MC, binomial tree, hedge sim   │  │
 * │  │    • value_investing   — Graham/Buffett valuation        │  │
 * │  │    • financial_calculator — DCF/PE/PB/PEG estimator      │  │
 * │  │    • news_summary      — financial news & sentiment      │  │
 * │  │    • polymarket_odds   — prediction market odds          │  │
 * │  │    • cfo_assistant     — CFO-level statement analysis    │  │
 * │  │    • data_analysis     — statistics, regression, macro   │  │
 * │  │    • macro_data        — GDP, CPI, PMI, rates, regime   │  │
 * │  │    • earnings_calendar — earnings dates, analyst ratings │  │
 * │  │    • institutional_flow— fund holdings, insider trades   │  │
 * │  │    • social_sentiment  — community hot stocks, sentiment │  │
 * │  │    • chip_accumulation — institutional accumulation scan │  │
 * │  │                                                          │  │
 * │  │  Commands (chat shortcuts):                              │  │
 * │  │    /quote /screen /backtest /portfolio /signal           │  │
 * │  │    /confirm /reject /pause /resume /orders /pnl          │  │
 * │  │    /watchlist /板块 /资金 /龙虎榜 /预警                    │  │
 * │  │    /期权链 /期权行情 /希腊值                               │  │
 * │  │                                                          │  │
 * │  │  Services (background):                                  │  │
 * │  │    • DataSyncService   — periodic market data refresh    │  │
 * │  │    • SignalMonitor     — watch strategies, emit alerts   │  │
 * │  │    • LiveEngine        — real-time broker order loop     │  │
 * │  │    • OptionsMonitor    — option Greeks/expiry monitoring │  │
 * │  │                                                          │  │
 * │  │  Python Bridge (child_process NDJSON):                   │  │
 * │  │    • backtrader engine for advanced backtesting           │  │
 * │  │    • akshare / tushare for CN market data                │  │
 * │  │    • xtquant (QMT) live trading bridge                   │  │
 * │  └──────────────────────────────────────────────────────────┘  │
 * │                                                                │
 * │  Channels: Discord / Telegram / Web / etc.                     │
 * └─────────────────────────────────────────────────────────────────┘
 */

import type {
  AnyAgentTool,
  OpenClawPluginApi,
  OpenClawPluginConfigSchema,
} from "openclaw/plugin-sdk";
import { runPythonScript } from "./src/bridge/python-bridge.js";
import { createQuantCommands } from "./src/commands/index.js";
import { validateConfig } from "./src/config-validator.js";
import { createDataProvider } from "./src/data/market-data-provider.js";
import { LiveEngine } from "./src/engine/live-engine.js";
import { TickFeatureStore } from "./src/engine/tick-feature-store.js";
import { registerAdminRoutes } from "./src/http/admin-routes.js";
import {
  withSecurity,
  withCors,
  withRateLimit,
  RateLimiter,
  type HttpHandler,
} from "./src/http/auth-middleware.js";
import { registerQuantRoutes } from "./src/http/routes.js";
import { AlertStore } from "./src/services/alert-store.js";
import { BacktestStore } from "./src/services/backtest-store.js";
import { BenchmarkService } from "./src/services/benchmark.js";
import { BreakoutMonitor } from "./src/services/breakout-monitor.js";
import { ComposedStrategyStore } from "./src/services/composed-strategy-store.js";
import { ConditionalOrderService } from "./src/services/conditional-order.js";
import { CustomStrategyStore } from "./src/services/custom-strategy-store.js";
import { DataQualityValidator } from "./src/services/data-quality.js";
import { createDataSyncService } from "./src/services/data-sync.js";
import { HistoricalDataWarehouse } from "./src/services/data-warehouse.js";
import { DcaScheduleService } from "./src/services/dca-schedule.js";
import { EquityHistory } from "./src/services/equity-history.js";
import { EventBus } from "./src/services/event-bus.js";
import { ExplorationHistoryStore } from "./src/services/exploration-history-store.js";
import { FavoritesStore } from "./src/services/favorites-store.js";
import { LogStore, wrapLoggerWithStore } from "./src/services/log-store.js";
import { tryNotify } from "./src/services/notify-helper.js";
import { OptimizationStore } from "./src/services/optimization-store.js";
import { createOptionsMonitorService } from "./src/services/options-monitor.js";
import { createPnlMonitorService } from "./src/services/pnl-monitor.js";
import { PriceAlertService } from "./src/services/price-alert.js";
import { ReceiverService } from "./src/services/receiver-service.js";
import { findReportTemplate } from "./src/services/report-templates.js";
import { ScanHistoryStore } from "./src/services/scan-history-store.js";
import { SignalHitRateTracker } from "./src/services/signal-hitrate.js";
import { createSignalMonitorService } from "./src/services/signal-monitor.js";
import { InMemoryStorage } from "./src/services/sqlite-storage.js";
import { StrategyHealthMonitor } from "./src/services/strategy-health.js";
import { UserStrategyStore } from "./src/services/strategy-template.js";
import { TaskScheduler } from "./src/services/task-scheduler.js";
import { TickHistoryStore } from "./src/services/tick-history-store.js";
import { TickIngressService } from "./src/services/tick-ingress-service.js";
import { TickStrategyService } from "./src/services/tick-strategy-service.js";
import { TradeJournal } from "./src/services/trade-journal.js";
import { createBacktestTool } from "./src/tools/backtest.js";
import { createBreakoutMonitorTool } from "./src/tools/breakout-monitor.js";
import { createCfoAssistantTool } from "./src/tools/cfo-assistant.js";
import { createChipAccumulationTool } from "./src/tools/chip-accumulation.js";
import { createConditionalOrderTool } from "./src/tools/conditional-order.js";
import { createCorrelationTool } from "./src/tools/correlation.js";
import { createCustomStrategyTool } from "./src/tools/custom-strategy.js";
import { createDataAnalysisTool } from "./src/tools/data-analysis.js";
import { createDataCacheTool } from "./src/tools/data-cache.js";
import { createDcaScheduleTool } from "./src/tools/dca-schedule.js";
import { createDragonTigerTool } from "./src/tools/dragon-tiger.js";
import { createEarningsCalendarTool } from "./src/tools/earnings-calendar.js";
import { createFavoritesTool } from "./src/tools/favorites.js";
import { createFinancialCalculatorTool } from "./src/tools/financial-calculator.js";
import { createFundFlowTool } from "./src/tools/fund-flow.js";
import { createFuturesStraddleTool } from "./src/tools/futures-straddle.js";
import { createInstitutionalFlowTool } from "./src/tools/institutional-flow.js";
import { createLianbanAnalysisTool } from "./src/tools/lianban-analysis.js";
import { createLiveTradingTool } from "./src/tools/live-trading.js";
import { createMacroDataTool } from "./src/tools/macro-data.js";
import { createMarketDataTool } from "./src/tools/market-data.js";
import { createNewsSummaryTool } from "./src/tools/news-summary.js";
import { createOptionsDataTool } from "./src/tools/options-data.js";
import { createOptionsStrategyTool } from "./src/tools/options-strategy.js";
import { createOptionsTradingTool } from "./src/tools/options-trading.js";
import { createPerformanceAttributionTool } from "./src/tools/performance-attribution.js";
import { createPolymarketOddsTool } from "./src/tools/polymarket-odds.js";
import { createPortfolioBacktestTool } from "./src/tools/portfolio-backtest.js";
import { createPortfolioRebalanceTool } from "./src/tools/portfolio-rebalance.js";
import { createPortfolioTool } from "./src/tools/portfolio.js";
import { createPositionSizingTool } from "./src/tools/position-sizing.js";
import { createPriceAlertTool } from "./src/tools/price-alert.js";
import { createRiskAnalysisTool } from "./src/tools/risk-analysis.js";
import { createScheduledTaskTool } from "./src/tools/scheduled-task.js";
import { createSectorAnalysisTool } from "./src/tools/sector-analysis.js";
import { createSectorSynergyTool } from "./src/tools/sector-synergy.js";
import { createSocialSentimentTool } from "./src/tools/social-sentiment.js";
import { createStochasticPricingTool } from "./src/tools/stochastic-pricing.js";
import { createStockRecommendTool } from "./src/tools/stock-recommend.js";
import { createStockScreenTool } from "./src/tools/stock-screen.js";
import { createStrategyComposerTool } from "./src/tools/strategy-composer.js";
import { createStrategyExplorerTool } from "./src/tools/strategy-explorer.js";
import { createStrategyHealthTool } from "./src/tools/strategy-health.js";
import { createStrategyOptimizerTool } from "./src/tools/strategy-optimizer.js";
import { createStrategyRouterTool } from "./src/tools/strategy-router.js";
import { createTechMoatTool } from "./src/tools/tech-moat.js";
import { createTradeJournalTool } from "./src/tools/trade-journal.js";
import { createTradingSignalTool } from "./src/tools/trading-signal.js";
import { createValueInvestingTool } from "./src/tools/value-investing.js";
import { createWatchlistTool } from "./src/tools/watchlist.js";
import type { QuantTradingConfig, TradingSignal } from "./src/types.js";

/** Adapt a simple tool object into the AgentTool interface expected by the plugin SDK. */
function wrapTool(
  tool: {
    name: string;
    description: string;
    parameters: unknown;
    execute: (args: Record<string, unknown>) => Promise<unknown>;
  },
  label: string,
): AnyAgentTool {
  return {
    name: tool.name,
    label,
    description: tool.description,
    parameters: tool.parameters,
    async execute(_toolCallId: string, params: unknown) {
      const result = await tool.execute(params as Record<string, unknown>);
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        details: result,
      };
    },
  } as AnyAgentTool;
}

/** Collect all valid command names (primary + aliases) that pass SDK validation.
 *  SDK validates with /^[a-z][a-z0-9_-]*$/ — only ASCII letters, digits, hyphens, underscores. */
function resolveAllValidCommandNames(cmd: { name: string; aliases?: string[] }): string[] {
  return [cmd.name, ...(cmd.aliases ?? [])]
    .map((s) => s.replace(/^\//, "").trim().toLowerCase())
    .filter(Boolean)
    .filter((s) => /^[a-z][a-z0-9_-]*$/.test(s));
}

/** Append a signal to signals.json for the trading_signal tool. */
async function persistSignal(signal: TradingSignal, config: QuantTradingConfig): Promise<void> {
  const { default: fs } = await import("node:fs/promises");
  const { default: path } = await import("node:path");
  const { default: os } = await import("node:os");
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const filePath = path.join(dir, "signals.json");
  let signals: TradingSignal[] = [];
  try {
    signals = JSON.parse(await fs.readFile(filePath, "utf-8")) as TradingSignal[];
  } catch {
    // file doesn't exist yet — start fresh
  }
  signals.push(signal);
  // Keep the last 2000 signals to avoid unbounded growth
  if (signals.length > 2000) signals = signals.slice(-2000);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(signals, null, 2));
}

/** Permissive config schema — accepts any object so all QuantTradingConfig fields are allowed. */
function quantTradingConfigSchema(): OpenClawPluginConfigSchema {
  return {
    safeParse(value: unknown) {
      if (value === undefined) return { success: true as const, data: undefined };
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return {
          success: false as const,
          error: { issues: [{ path: [], message: "expected config object" }] },
        };
      }
      return { success: true as const, data: value };
    },
    jsonSchema: { type: "object", additionalProperties: true },
  };
}

const quantTradingPlugin = {
  id: "quant-trading",
  name: "Quant Trading",
  description:
    "Quantitative trading toolkit: market data, strategy backtesting, portfolio management, trading signals, and live broker trading for stocks, futures, and funds.",
  configSchema: quantTradingConfigSchema(),

  register(api: OpenClawPluginApi) {
    const config = (api.pluginConfig ?? {}) as QuantTradingConfig;

    // ── Config validation — surface misconfigurations early ──────
    const issues = validateConfig(config);
    for (const issue of issues) {
      const prefix = issue.path ? `[${issue.path}] ` : "";
      if (issue.severity === "error") {
        api.logger.error(`[quant-trading] config: ${prefix}${issue.message}`);
      } else {
        api.logger.warn(`[quant-trading] config: ${prefix}${issue.message}`);
      }
    }

    // ── Live Engine singleton (started/stopped as a service) ─────────
    const liveEngine = new LiveEngine(config, api);
    const getEngine = () => liveEngine;

    // ── Event Bus (real-time SSE) ──────────────────────────────────
    const eventBus = new EventBus({ maxRecent: 500 });

    // ── Log Store (in-memory ring buffer for admin log viewer) ───
    const logStore = new LogStore({ maxEntries: 2000 });
    api.logger = wrapLoggerWithStore(api.logger, logStore);

    // ── Equity History (daily snapshots, separate for paper / live) ─
    const equityHistoryPaper = new EquityHistory({
      dataDir: config.dataDir ?? undefined,
      maxSnapshots: 2000,
      mode: "paper",
    });
    const equityHistoryLive = new EquityHistory({
      dataDir: config.dataDir ?? undefined,
      maxSnapshots: 2000,
      mode: "live",
    });
    // Legacy file (no suffix) — load once for migration, then read-only
    const equityHistoryLegacy = new EquityHistory({
      dataDir: config.dataDir ?? undefined,
      maxSnapshots: 2000,
    });
    void Promise.all([
      equityHistoryPaper.load(),
      equityHistoryLive.load(),
      equityHistoryLegacy.load(),
    ]).then(() => {
      equityHistoryPaper.startAutoSave(60_000);
      equityHistoryLive.startAutoSave(60_000);
      // Migrate legacy data: if legacy has data but both new files are empty,
      // copy legacy snapshots into paper history (sensible default)
      if (equityHistoryLegacy.count > 0 && equityHistoryPaper.count === 0 && equityHistoryLive.count === 0) {
        for (const snap of equityHistoryLegacy.getAll()) {
          equityHistoryPaper.addSnapshot(snap);
        }
      }
    });
    // Active equity history — resolved dynamically based on current broker mode
    const getActiveEquityHistory = () => {
      const bt = liveEngine.broker?.brokerType;
      return bt && bt !== "paper" ? equityHistoryLive : equityHistoryPaper;
    };
    // Alias for backward compat — routes and services that just need "the current one"
    const equityHistory = equityHistoryPaper;

    // ── Benchmark Service ────────────────────────────────────────
    const benchmarkService = new BenchmarkService(config);

    // ── Signal Hit Rate Tracker ──────────────────────────────────
    const signalHitRateTracker = new SignalHitRateTracker({
      horizons: [1, 3, 5, 10],
      maxRecords: 500,
      dataDir: config.dataDir ?? null,
    });
    signalHitRateTracker.setQuoteFn(async (symbol: string) => {
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const dp = createDataProvider(config);
      const market = detectMarketFromSymbol(symbol);
      const quote = await dp.getQuote(symbol, market as never);
      if (!quote) return null;
      return { price: quote.price };
    });

    // ── Trade Journal (复盘笔记) ─────────────────────────────────
    const tradeJournal = new TradeJournal({
      maxNotes: 2000,
      dataDir: config.dataDir ?? null,
    });

    // ── Favorites Store (股票收藏) ──────────────────────────────
    const favoritesStore = new FavoritesStore({
      dataDir: config.dataDir ?? null,
    });

    // ── Strategy Health Monitor (策略健康度监控) ──────────────────
    const strategyHealthMonitor = new StrategyHealthMonitor(
      config.dataDir ?? ".",
      {},
      (msg) => void tryNotify(api, msg),
    );

    // ── Conditional Order Service (止损/止盈/跟踪止损) ─────────────
    // Created early so toolInstances can reference it; wiring (quoteFn, exitFn etc.) happens later.
    const conditionalOrderService = new ConditionalOrderService({
      checkIntervalMs: 30_000,
      maxOrders: 100,
      dataDir: config.dataDir ?? null,
    });

    // ── DCA Schedule Service (定投) ────────────────────────────
    const dcaScheduleService = new DcaScheduleService(
      config,
      () => liveEngine.orderManager,
      async (symbol: string) => {
        const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
        const market = detectMarketFromSymbol(symbol);
        const quote = await dataProviderForAlerts.getQuote(symbol, market as never);
        return quote?.price ?? null;
      },
    );
    dcaScheduleService.setNotify((m: string) => tryNotify(api, m));
    void dcaScheduleService.load();

    // ── Breakout Monitor (突破监控) ────────────────────────────
    const breakoutMonitor = new BreakoutMonitor({
      checkIntervalMs: 30_000,
      maxSetups: 20,
      dataDir: config.dataDir ?? null,
    });
    breakoutMonitor.setQuoteFn(async (symbol: string) => {
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const market = detectMarketFromSymbol(symbol);
      const quote = await dataProviderForAlerts.getQuote(symbol, market as never);
      if (!quote) return null;
      return { price: quote.price, volume: quote.volume, changePct: quote.changePct };
    });
    breakoutMonitor.setNotifyFn((m: string) => tryNotify(api, m));
    breakoutMonitor.setOrderFn(async (params) => {
      const mgr = liveEngine.orderManager;
      if (!mgr) return { ok: false, reason: "引擎未启动" };
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const result = await mgr.submit({
        symbol: params.symbol,
        market: detectMarketFromSymbol(params.symbol),
        side: params.side,
        orderType: params.orderType,
        quantity: params.quantity,
        price: params.price,
        stopPrice: params.stopPrice,
        strategyId: params.strategyId ?? "breakout_monitor",
        signalId: `breakout_${Date.now()}`,
      });
      return result.ok
        ? { ok: true, orderId: result.order.orderId }
        : { ok: false, reason: result.reason };
    });
    breakoutMonitor.setMaFn(async (symbol, period, maType) => {
      try {
        const result = await runPythonScript(
          "indicator_calculator.py",
          {
            action: "ma",
            symbol,
            period,
            ma_type: maType,
          },
          config,
        );
        return (result as { value?: number }).value ?? null;
      } catch {
        return null;
      }
    });
    breakoutMonitor.setPositionFn(async (symbol) => {
      const positions = liveEngine.getPositions();
      const pos = positions.find((p: { symbol: string }) => p.symbol === symbol);
      return pos ? { quantity: pos.quantity } : null;
    });
    breakoutMonitor.start();

    // ── Data Warehouse (persistent K-line cache) ─────────────────
    const storage = new InMemoryStorage(
      config.dataDir ? `${config.dataDir}/quant-storage.json` : null,
    );
    void storage.initialize();
    const dataWarehouse = new HistoricalDataWarehouse({
      provider: createDataProvider(config),
      storage,
      refreshToday: true,
      concurrency: 3,
    });

    // ── Data Quality Validator ───────────────────────────────────
    const dataQualityValidator = new DataQualityValidator({
      maxPriceChangeRatio: 0.2,
      maxVolumeSpikeRatio: 10,
      stalenessThresholdDays: 3,
    });

    // ── Alert Store (persistent, queryable) ──────────────────────
    const alertStore = new AlertStore({
      maxAlerts: 1000,
      maxAgeMs: 7 * 24 * 60 * 60 * 1000, // 7 days
      persistPath: config.dataDir ? `${config.dataDir}/alerts.json` : null,
      autoSaveIntervalMs: 60_000,
    });
    alertStore.start();

    // Wire LiveEngine staleness alerts to the alert store + event bus
    liveEngine.setStalenessAlertCallback((severity, message, symbol) => {
      alertStore.add(severity, "data_staleness", message, { symbol });
      eventBus.emit("alert", { severity, type: "data_staleness", message, symbol });
    });

    // Wire LiveEngine connection health alerts to the alert store + event bus
    liveEngine.setConnectionAlertCallback((severity, message) => {
      const alertSeverity = severity === "resolved" ? "info" : severity;
      alertStore.add(alertSeverity, "connection_health", message);
      eventBus.emit("alert", { severity, type: "connection_health", message });
    });

    // Wire LiveEngine status changes to the event bus
    liveEngine.setStatusChangeCallback((prev, next) => {
      eventBus.emit("engine", { prev, next, timestamp: Date.now() });
    });

    // Wire LiveEngine signals to the event bus
    liveEngine.setSignalCallback((signal, result) => {
      eventBus.emit("signal", {
        symbol: signal.symbol,
        direction: signal.direction,
        price: signal.price,
        quantity: signal.quantity,
        strategyId: signal.strategyId,
        reason: signal.reason,
        confidence: signal.confidence,
        executed: result.ok,
        rejectReason: result.reason,
        orderId: result.orderId,
      });
      if (result.ok && result.orderId) {
        eventBus.emit("order", {
          orderId: result.orderId,
          symbol: signal.symbol,
          side: signal.direction,
          price: signal.price,
          quantity: signal.quantity ?? 100,
          status: "submitted",
          strategyId: signal.strategyId,
        });
      }
      // Track signal for hit rate accuracy checking
      signalHitRateTracker.trackSignal(signal);
      // Track signal for strategy health monitoring
      strategyHealthMonitor.recordSignal(
        signal.strategyId,
        new Date(signal.timestamp).toISOString(),
      );
      // Persist signal to signals.json for trading_signal tool queries
      void persistSignal(signal, config);
    });

    // Wire LiveEngine account sync to equity history + event bus
    liveEngine.setAccountSyncCallback((account, positions) => {
      const activeEH = getActiveEquityHistory();
      const snap = activeEH.record(account, positions);
      eventBus.emit("equity", {
        date: snap.date,
        equity: snap.equity,
        cash: snap.cash,
        marketValue: snap.marketValue,
        dailyPnl: snap.dailyPnl,
        positionCount: snap.positionCount,
      });
    });

    // Wire completed trades to strategy health monitor
    liveEngine.setTradeCallback((trade) => {
      if (trade.strategyId && trade.exitTime) {
        strategyHealthMonitor.recordTrade(trade.strategyId, trade.netPnl, trade.exitTime);
      }
    });

    // ── Backtest Store (persistent history) ──────────────────────
    const backtestStore = new BacktestStore({
      dataDir: config.dataDir ? `${config.dataDir}/backtests` : undefined,
      maxResults: 500,
    });
    if (config.dataDir) {
      backtestStore.start();
    }

    // ── Scan History Store (screening result history) ────────────
    const scanHistoryStore = new ScanHistoryStore({
      dataDir: config.dataDir ? `${config.dataDir}/scans` : undefined,
      maxResults: 200,
    });
    if (config.dataDir) {
      scanHistoryStore.start();
    }

    // ── Optimization Store (寻优 history) ────────────────────────
    const optimizationStore = new OptimizationStore({
      dataDir: config.dataDir ? `${config.dataDir}/optimizations` : undefined,
      maxResults: 100,
    });
    if (config.dataDir) {
      optimizationStore.start();
    }

    // ── User Strategy Store (templates & presets) ────────────────
    const userStrategyStore = new UserStrategyStore(
      config.dataDir ? `${config.dataDir}` : undefined,
    );
    void userStrategyStore.load();

    // ── Composed Strategy Store (dynamic compositions) ────────────
    const composedStrategyStore = new ComposedStrategyStore(
      config.dataDir ? `${config.dataDir}` : undefined,
    );
    void composedStrategyStore.load();

    // ── Custom (Rule-based) Strategy Store ─────────────────────────
    const customStrategyStore = new CustomStrategyStore(
      config.dataDir ? `${config.dataDir}` : undefined,
    );
    void customStrategyStore.load();

    // ── Exploration History Store ─────────────────────────────────
    const explorationHistoryStore = new ExplorationHistoryStore(
      config.dataDir ? `${config.dataDir}` : undefined,
    );
    void explorationHistoryStore.load();

    // ── Tools (AI-callable) ──────────────────────────────────
    // Create tool instances once and share between registration and scheduled-task replay
    const toolInstances = {
      market_data: createMarketDataTool(api, config),
      stock_screen: createStockScreenTool(api, config, scanHistoryStore),
      backtest: createBacktestTool(api, config, backtestStore),
      portfolio: createPortfolioTool(api, config, getEngine),
      portfolio_backtest: createPortfolioBacktestTool(api, config),
      portfolio_rebalance: createPortfolioRebalanceTool(api, config, getEngine),
      trading_signal: createTradingSignalTool(api, config, signalHitRateTracker),
      risk_analysis: createRiskAnalysisTool(api, config),
      futures_straddle: createFuturesStraddleTool(api, config),
      data_cache: createDataCacheTool(api, config),
      live_trading: createLiveTradingTool(api, config, getEngine, equityHistory),
      strategy_optimizer: createStrategyOptimizerTool(api, config, composedStrategyStore),
      strategy_composer: createStrategyComposerTool(api, config, composedStrategyStore),
      custom_strategy: createCustomStrategyTool(api, customStrategyStore),
      position_sizing: createPositionSizingTool(api, config, async () => {
        const mgr = liveEngine.orderManager;
        if (!mgr) return 100_000;
        try {
          const account = await mgr.broker.queryAccount();
          return account.totalAssets || account.cash || 100_000;
        } catch {
          return 100_000;
        }
      }),
      strategy_explorer: createStrategyExplorerTool(
        api,
        config,
        composedStrategyStore,
        backtestStore,
      ),
      strategy_router: createStrategyRouterTool(api, config),
      sector_analysis: createSectorAnalysisTool(api, config),
      fund_flow: createFundFlowTool(api, config),
      dragon_tiger: createDragonTigerTool(api, config),
      lianban_analysis: createLianbanAnalysisTool(api, config),
      sector_synergy: createSectorSynergyTool(api, config),
      options_data: createOptionsDataTool(api, config),
      options_strategy: createOptionsStrategyTool(api, config),
      options_trading: createOptionsTradingTool(api, config, getEngine),
      stochastic_pricing: createStochasticPricingTool(api, config),
      watchlist: createWatchlistTool(api, config),
      favorites: createFavoritesTool(api, config),
      conditional_order: createConditionalOrderTool(api, conditionalOrderService),
      dca_schedule: createDcaScheduleTool(api, dcaScheduleService),
      breakout_monitor: createBreakoutMonitorTool(api, config, breakoutMonitor),
      trade_journal: createTradeJournalTool(api, tradeJournal),
      performance_attribution: createPerformanceAttributionTool(api, config),
      strategy_health: createStrategyHealthTool(api, config, strategyHealthMonitor),
      value_investing: createValueInvestingTool(api, config),
      financial_calculator: createFinancialCalculatorTool(api, config),
      news_summary: createNewsSummaryTool(api, config),
      polymarket_odds: createPolymarketOddsTool(api, config),
      cfo_assistant: createCfoAssistantTool(api, config),
      data_analysis: createDataAnalysisTool(api, config),
      macro_data: createMacroDataTool(api, config),
      earnings_calendar: createEarningsCalendarTool(api, config),
      institutional_flow: createInstitutionalFlowTool(api, config),
      social_sentiment: createSocialSentimentTool(api, config),
      chip_accumulation: createChipAccumulationTool(api, config),
      stock_recommend: createStockRecommendTool(api, config, scanHistoryStore),
      correlation_analysis: createCorrelationTool(api, config, async (symbol) => {
        try {
          const end = new Date().toISOString().slice(0, 10);
          const start = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
          const bars = await dataWarehouse.getKlines(symbol, "1d", start, end);
          return { klines: bars.map((b) => ({ close: b.close })) };
        } catch {
          return null;
        }
      }),
      tech_moat: createTechMoatTool(api, config),
    };

    const toolLabels: Record<string, string> = {
      market_data: "行情数据",
      stock_screen: "选股筛选",
      backtest: "策略回测",
      portfolio: "投资组合",
      portfolio_rebalance: "组合再平衡",
      trading_signal: "交易信号",
      risk_analysis: "风险分析",
      futures_straddle: "期货套利",
      data_cache: "数据缓存",
      live_trading: "实盘交易",
      strategy_optimizer: "策略优化",
      strategy_composer: "策略组合",
      strategy_explorer: "策略探索",
      strategy_router: "策略路由",
      sector_analysis: "板块分析",
      fund_flow: "资金流向",
      dragon_tiger: "龙虎榜",
      lianban_analysis: "连板分析",
      sector_synergy: "板块协同追涨",
      options_data: "期权数据",
      options_strategy: "期权策略",
      options_trading: "期权交易",
      stochastic_pricing: "随机定价与风险",
      watchlist: "自选股管理",
      favorites: "股票收藏",
      conditional_order: "条件单",
      dca_schedule: "定投计划",
      trade_journal: "交易笔记",
      performance_attribution: "绩效归因",
      strategy_health: "策略健康",
      correlation_analysis: "相关性分析",
      tech_moat: "技术壁垒分析",
      value_investing: "价值投资",
      financial_calculator: "估值计算",
      news_summary: "新闻摘要",
      polymarket_odds: "预测市场",
      cfo_assistant: "CFO助手",
      data_analysis: "数据分析",
      macro_data: "宏观经济",
      earnings_calendar: "财报日历",
      institutional_flow: "机构持仓",
      social_sentiment: "舆情分析",
      chip_accumulation: "主力吸筹",
      stock_recommend: "智能选股推荐",
      custom_strategy: "自定义策略",
      position_sizing: "仓位管理",
      breakout_monitor: "突破监控",
    };
    for (const [name, tool] of Object.entries(toolInstances)) {
      api.registerTool(wrapTool(tool, toolLabels[name] ?? name), { names: [name] });
    }

    // ── Task Scheduler (delayed retry / periodic push) ────────────
    const taskScheduler = new TaskScheduler({
      maxConcurrent: 3,
      maxTasks: 50,
      dataDir: config.dataDir ?? null,
    });
    // Wire notify callback — delivers task results to the user's chat
    taskScheduler.setNotify((m: string) => tryNotify(api, m));

    /** Lazily-extended tool map — reuses instances from toolInstances + adds price_alert. */
    let fullToolMap: Record<
      string,
      { execute: (a: Record<string, unknown>) => Promise<unknown> }
    > | null = null;
    const getToolMap = () => {
      if (!fullToolMap) {
        fullToolMap = {
          ...toolInstances,
          price_alert: createPriceAlertTool(api, config, priceAlertService),
        };
      }
      return fullToolMap;
    };

    /** Execute a registered tool by name — used by scheduled_task to replay tool calls. */
    const executeToolByName = async (
      name: string,
      args: Record<string, unknown>,
    ): Promise<string> => {
      const tool = getToolMap()[name];
      if (!tool) return `未知工具: ${name}`;
      const result = await tool.execute(args);
      return JSON.stringify(result, null, 2);
    };

    api.registerTool(
      wrapTool(createScheduledTaskTool(api, config, taskScheduler, executeToolByName), "定时任务"),
      { names: ["scheduled_task"] },
    );

    // Wire TaskScheduler run factory for persistence: rebuild `run` closures from serialized meta
    taskScheduler.setRunFactory((meta) => {
      if (meta.reportId) {
        const template = findReportTemplate(meta.reportId);
        if (!template) return null;
        return async () => template.generate(executeToolByName, config);
      }
      if (meta.toolName) {
        const toolName = meta.toolName;
        const toolArgs = meta.toolArgs ?? {};
        return async () => executeToolByName(toolName, toolArgs);
      }
      return null;
    });
    taskScheduler.loadFromDisk();

    // ── Price Alert Service (条件预警) ──────────────────────────────
    const priceAlertService = new PriceAlertService({
      checkIntervalMs: 60_000,
      maxAlerts: 50,
      dataDir: config.dataDir ?? null,
    });
    // Wire notify callback
    priceAlertService.setNotify((m: string) => tryNotify(api, m));
    // Wire quote function
    const dataProviderForAlerts = createDataProvider(config);
    priceAlertService.setQuoteFn(async (symbol: string) => {
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const market = detectMarketFromSymbol(symbol);
      const quote = await dataProviderForAlerts.getQuote(symbol, market as never);
      if (!quote) return null;
      return { price: quote.price, changePct: quote.changePct, volume: quote.volume };
    });
    priceAlertService.start();

    // Wire sentiment function for sentiment_spike / hot_rank_above alerts
    priceAlertService.setSentimentFn(async (symbol: string) => {
      try {
        const result = await runPythonScript<{
          sentiment_score?: number;
          hot_rank?: number;
        }>(
          "engines/social_sentiment.py",
          { action: "stock_sentiment", symbol, market: "cn" },
          config,
        );
        if (!result.ok || !result.data) return null;
        return {
          sentimentScore: result.data.sentiment_score ?? 0,
          hotRank: result.data.hot_rank ?? 0,
        };
      } catch {
        return null;
      }
    });

    // ── Conditional Order Service wiring ──────────────────────────
    conditionalOrderService.setNotify((m: string) => tryNotify(api, m));
    conditionalOrderService.setQuoteFn(async (symbol: string) => {
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const market = detectMarketFromSymbol(symbol);
      const quote = await dataProviderForAlerts.getQuote(symbol, market as never);
      if (!quote) return null;
      return { price: quote.price };
    });
    // Wire exit function to OrderManager (places market sell order)
    conditionalOrderService.setExitFn(async (symbol, quantity, reason, strategyId) => {
      const mgr = liveEngine.orderManager;
      if (!mgr) return { ok: false, reason: "引擎未启动" };
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const result = await mgr.submit({
        symbol,
        market: detectMarketFromSymbol(symbol),
        side: "sell",
        orderType: "market",
        quantity,
        strategyId: strategyId ?? "conditional_exit",
        signalId: `cond_${Date.now()}`,
      });
      return result.ok ? { ok: true } : { ok: false, reason: result.reason };
    });
    // Wire indicator function for MA-based stop-loss
    conditionalOrderService.setIndicatorFn(async (symbol, maPeriod, maType) => {
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const { sma, ema } = await import("./src/engine/strategies/indicators.js");
      const market = detectMarketFromSymbol(symbol);
      const end = new Date().toISOString().slice(0, 10);
      const start = new Date(Date.now() - maPeriod * 3 * 86_400_000).toISOString().slice(0, 10);
      const bars = await dataProviderForAlerts.getKlines(symbol, "1d", start, end, market as never);
      if (!bars || bars.length < maPeriod) return null;
      const closes = bars.map((b) => b.close);
      const values = maType === "ema" ? ema(closes, maPeriod) : sma(closes, maPeriod);
      const lastValue = values[values.length - 1];
      if (lastValue == null || Number.isNaN(lastValue)) return null;
      return { maValue: lastValue };
    });
    conditionalOrderService.start();

    // Wire price alert triggers to AlertStore + EventBus for persistence and SSE
    priceAlertService.setAlertCallback((severity, message, metadata) => {
      alertStore.add(severity, "price_alert", message, metadata);
      eventBus.emit("alert", {
        severity,
        type: "price_alert",
        message,
        ...metadata,
      });
    });

    // Wire order execution for alert-triggered auto-orders
    priceAlertService.setOrderFn(async (params) => {
      const mgr = liveEngine.orderManager;
      if (!mgr) return { ok: false, reason: "引擎未启动" };
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const result = await mgr.submit({
        symbol: params.symbol,
        market: detectMarketFromSymbol(params.symbol),
        side: params.side,
        orderType: params.orderType,
        quantity: params.quantity,
        price: params.price,
        strategyId: params.strategyId ?? "alert_auto_order",
        signalId: `alert_${Date.now()}`,
      });
      return result.ok ? { ok: true } : { ok: false, reason: result.reason };
    });

    api.registerTool(wrapTool(createPriceAlertTool(api, config, priceAlertService), "条件预警"), {
      names: ["price_alert"],
    });

    // ── Chat Commands ────────────────────────────────────────
    const commands = createQuantCommands(api, config, getEngine, priceAlertService, taskScheduler);

    // Report template shortcut commands — generate and return report immediately
    const reportShortcuts: Array<{
      name: string;
      aliases: string[];
      reportId: string;
      description: string;
    }> = [
      {
        name: "/简报",
        aliases: ["/morning", "/morning_brief"],
        reportId: "morning_brief",
        description: "立即生成早盘简报",
      },
      {
        name: "/收盘",
        aliases: ["/close", "/market_close"],
        reportId: "market_close",
        description: "立即生成收盘总结",
      },
      {
        name: "/日志",
        aliases: ["/journal", "/daily_journal"],
        reportId: "daily_journal",
        description: "立即生成交易日志",
      },
      {
        name: "/周报",
        aliases: ["/weekly", "/weekly_review"],
        reportId: "weekly_review",
        description: "立即生成周度回顾",
      },
      {
        name: "/选股报告",
        aliases: ["/daily_scan"],
        reportId: "daily_scan",
        description: "立即生成每日选股报告",
      },
      {
        name: "/机构前三",
        aliases: ["/institutional_top3"],
        reportId: "institutional_top3",
        description: "立即生成每日机构行为Top3",
      },
      {
        name: "/月报",
        aliases: ["/monthly", "/monthly_report"],
        reportId: "monthly_report",
        description: "立即生成月度绩效报告",
      },
    ];
    for (const sc of reportShortcuts) {
      const template = findReportTemplate(sc.reportId);
      if (!template) continue;
      const cmd = {
        name: sc.name,
        aliases: sc.aliases,
        description: sc.description,
        async execute(_args: string) {
          const content = await template.generate(executeToolByName, config);
          return content || "⚠️ 报告生成无数据";
        },
      };
      commands.push(cmd);
    }

    for (const cmd of commands) {
      const validNames = resolveAllValidCommandNames(cmd);
      if (validNames.length === 0) {
        api.logger.warn(
          `[quant-trading] skip command registration: no valid command name in ${cmd.name}`,
        );
        continue;
      }
      // Register under all valid names (primary + aliases) so both
      // /选股 and /screen-qmt trigger the same handler.
      for (const commandName of validNames) {
        api.registerCommand({
          name: commandName,
          description: cmd.description,
          acceptsArgs: true,
          async handler(ctx) {
            try {
              const text = await cmd.execute(ctx.args ?? "");
              api.logger.info(`[cmd:${cmd.name}] 完成: ${text.slice(0, 200)}`);
              return { text };
            } catch (err) {
              const msg = err instanceof Error ? err.message : String(err);
              api.logger.info(`[cmd:${cmd.name}] 失败: ${msg}`);
              return { text: `❌ ${msg.slice(0, 300)}` };
            }
          },
        });
      }
    }

    // ── CLI sub-commands ─────────────────────────────────────
    api.registerCli(
      ({ program }) => {
        const quant = program.command("quant").description("Quantitative trading utilities");

        quant
          .command("backtest")
          .description("Run a strategy backtest from the command line")
          .argument("<strategy>", "Strategy file or built-in name")
          .option("-s, --start <date>", "Start date (YYYY-MM-DD)")
          .option("-e, --end <date>", "End date (YYYY-MM-DD)")
          .option("-c, --cash <amount>", "Initial cash", "100000")
          .option("--engine <engine>", "Backtest engine (builtin|backtrader)", "builtin")
          .option("--symbols <symbols>", "Comma-separated symbol list")
          .action(async (strategy, opts) => {
            const { runCliBacktest } = await import("./src/cli/backtest-cli.js");
            await runCliBacktest(strategy, opts, config);
          });

        quant
          .command("data")
          .description("Market data utilities")
          .argument("<action>", "fetch | list | clean")
          .option("--symbol <symbol>", "Symbol code")
          .option("--market <market>", "Market (cn_stock|us_stock|futures|fund)")
          .option(
            "--table <table>",
            "Cache table to clear: stock_list | stock_fundamentals | kline_data | stock_code_name (for clean)",
          )
          .action(async (action, opts) => {
            const { runCliData } = await import("./src/cli/data-cli.js");
            await runCliData(action, opts, config);
          });

        quant
          .command("portfolio")
          .description("Portfolio management")
          .argument("[action]", "show | add | remove | rebalance", "show")
          .option("--symbol <code>", "Symbol code (for add/remove)")
          .option("--quantity <n>", "Number of shares (for add/remove)", parseInt)
          .option("--price <p>", "Limit price (for add/remove)", parseFloat)
          .option("--port <port>", "Gateway HTTP port (default: $OPENCLAW_PORT or 18789)", parseInt)
          .action(async (action, opts) => {
            const { runCliPortfolio } = await import("./src/cli/portfolio-cli.js");
            await runCliPortfolio(action, opts, config);
          });

        quant
          .command("live")
          .description("Live trading engine control")
          .argument(
            "[action]",
            "start | stop | status | orders | pnl | pause | resume | strategies",
            "status",
          )
          .option("--strategy <id>", "Strategy ID (for start)")
          .option("--symbols <codes>", "Comma-separated symbol codes (for start)")
          .option("--broker <type>", "Broker type: paper | qmt (for start)")
          .option("--status <filter>", "Order status filter (for orders)")
          .option("--port <port>", "Gateway HTTP port (default: $OPENCLAW_PORT or 18789)", parseInt)
          .action(async (action, opts) => {
            const { runCliLive } = await import("./src/cli/live-cli.js");
            await runCliLive(action, opts, config);
          });
      },
      { commands: ["quant"] },
    );

    // ── Background Services ──────────────────────────────────
    api.registerService(createDataSyncService(api, config, eventBus));
    api.registerService(
      createSignalMonitorService(api, config, () => liveEngine.orderManager, eventBus, alertStore),
    );
    api.registerService(
      createPnlMonitorService(
        api,
        config,
        () => liveEngine.orderManager,
        async () => {
          if (liveEngine.getStatus() === "running") {
            await liveEngine.pause();
          }
        },
        // Wire PnL alerts to the persistent alert store + event bus
        (alert) => {
          alertStore.add(alert.severity, "pnl_monitor", alert.message, {
            trigger: alert.trigger,
            pnlPct: alert.pnlPct,
          });
          eventBus.emit("alert", {
            severity: alert.severity,
            type: "pnl_monitor",
            message: alert.message,
            trigger: alert.trigger,
            pnlPct: alert.pnlPct,
          });
        },
      ),
    );
    // LiveEngine is registered as a service. When tradingAccount.autoStart is
    // true the engine starts automatically with the gateway; otherwise it waits
    // for an explicit start_engine tool call. Lifecycle hooks handle cleanup.
    api.registerService(liveEngine);

    if (config.tradingAccount?.autoStart) {
      // Deferred so the rest of plugin registration completes first
      setTimeout(() => void liveEngine.start(), 0);
    }

    // Register Options Monitor as a service for option position monitoring
    api.registerService(createOptionsMonitorService(api, config, () => liveEngine.getPositions()));

    const tickEnabled = config.tickData?.enabled !== false;
    const tickSource = config.tickData?.source ?? "qmt-bridge";
    const hasTickSource =
      (tickSource === "qmt-bridge" && !!config.tradingAccount?.qmt?.windowsBridgeUrl) ||
      (tickSource === "receiver-proxy" && config.receiver?.enabled !== false);
    const tickIngressService =
      tickEnabled && hasTickSource ? new TickIngressService(config, eventBus) : null;
    // Hoist tick stores/service for admin-routes wiring below
    let tickFeatureStoreRef: InstanceType<typeof TickFeatureStore> | null = null;
    let tickStrategyServiceRef: InstanceType<typeof TickStrategyService> | null = null;
    let tickHistoryStoreRef: TickHistoryStore | null = null;
    if (tickIngressService) {
      const tickHistoryStore = new TickHistoryStore({
        persistPath: config.dataDir ? `${config.dataDir}/tick-history.json` : null,
        maxTicksPerSymbol: config.tickData?.maxRecentTicksPerSymbol ?? 256,
        maxBarsPerSeries: config.tickData?.maxRecentBarsPerSeries ?? 256,
        maxFeaturesPerSymbol: config.tickData?.maxRecentFeaturesPerSymbol ?? 256,
      });
      tickHistoryStoreRef = tickHistoryStore;
      // TickIngressService owns its internal TickFeatureStore and calls
      // publishTickFeatures after each tick. Wire history store via onTickFeatures
      // so features land in persistent storage without double-computing.
      tickFeatureStoreRef = tickIngressService.getFeatureStore();
      const tickStrategyService = new TickStrategyService(
        config,
        api,
        tickIngressService,
        eventBus,
        () => liveEngine.orderManager,
      );
      tickStrategyServiceRef = tickStrategyService;
      tickIngressService.onTick((tick) => {
        tickHistoryStore.appendTick(tick);
      });
      tickIngressService.onTickFeatures((features) => {
        tickHistoryStore.appendFeature(features);
      });
      tickIngressService.onBar((symbol, bar, interval) => {
        tickHistoryStore.appendBar(symbol, interval, bar);
      });
      liveEngine.setWatchedSymbolsCallback((symbols) => {
        void tickIngressService.syncSubscriptions(symbols).catch((err) => {
          api.logger.warn(
            `[quant-trading] tick ingress subscription sync failed: ${(err as Error).message}`,
          );
        });
        tickStrategyService.setActiveSymbols(symbols);
      });

      // Wire regime auto-switch callback (Python macro regime → TS price-action fallback)
      let priceRegimeDetect: (() => Promise<string | null>) | null = null;
      liveEngine.setRegimeCallback(async () => {
        // Try Python macro regime first
        try {
          const { runPythonScript: runPython } = await import("./src/bridge/python-bridge.js");
          const result = await runPython<{ regime?: string }>(
            "engines/strategy_router.py",
            { action: "get_regime" },
            config,
          );
          if (result.ok && result.data?.regime && result.data.regime !== "unknown") {
            return result.data.regime;
          }
        } catch {
          // Python unavailable — fall through to TS detector
        }
        // Fallback: TS-side price-action regime detection (MA + volatility based)
        if (!priceRegimeDetect) {
          const { createMarketRegimeDetector } = await import("./src/services/market-regime.js");
          priceRegimeDetect = createMarketRegimeDetector(config);
        }
        return priceRegimeDetect();
      });

      api.registerService(tickIngressService);
      api.registerService(tickStrategyService);
      api.registerService(tickHistoryStore);
    }

    // Register RECEIVER.py bridge server as a service (auto-start + auto-restart)
    const receiverService = config.receiver?.enabled !== false ? new ReceiverService(config) : null;
    if (receiverService) {
      api.registerService(receiverService);
    }

    // Register TaskScheduler and PriceAlertService as services for clean shutdown
    api.registerService({
      id: "task-scheduler",
      async start() {
        /* timers already running */
      },
      async stop() {
        taskScheduler.destroy();
      },
    });
    api.registerService({
      id: "price-alert",
      async start() {
        /* already started above */
      },
      async stop() {
        priceAlertService.stop();
      },
    });
    api.registerService({
      id: "conditional-order",
      async start() {
        /* already started above */
      },
      async stop() {
        conditionalOrderService.stop();
        dcaScheduleService.destroy();
        breakoutMonitor.destroy();
      },
    });
    // EquityHistory + InMemoryStorage lifecycle: flush dirty data on shutdown
    api.registerService({
      id: "equity-history",
      async start() {
        /* auto-save already started above */
      },
      async stop() {
        await equityHistory.close();
      },
    });
    api.registerService({
      id: "quant-storage",
      async start() {
        /* already initialized above */
      },
      async stop() {
        await storage.close();
      },
    });
    api.registerService({
      id: "alert-store",
      async start() {
        /* already started above */
      },
      async stop() {
        alertStore.stop();
      },
    });

    // ── HTTP Routes ────────────────────────────────────────
    const rateLimiter = new RateLimiter(
      config.rateLimit?.maxRequests ?? 60,
      config.rateLimit?.windowMs ?? 60_000,
    );
    const secure = (handler: HttpHandler): HttpHandler =>
      withSecurity(config, handler, { rateLimiter });
    const securePublic = (handler: HttpHandler): HttpHandler =>
      withRateLimit(config, withCors(config, handler), rateLimiter);

    registerQuantRoutes({
      api,
      config,
      alertStore,
      backtestStore,
      eventBus,
      equityHistory,
      equityHistoryPaper,
      equityHistoryLive,
      benchmarkService,
      dataWarehouse,
      dataQualityValidator,
      quantStorage: storage,
      userStrategyStore,
      getEngine,
      priceAlertService,
      taskScheduler,
      secure,
      securePublic,
    });

    registerAdminRoutes({
      api,
      config,
      dataWarehouse,
      receiverService,
      userStrategyStore,
      alertStore,
      eventBus,
      logStore,
      priceAlertService,
      conditionalOrderService,
      breakoutMonitor,
      composedStrategyStore,
      explorationHistoryStore,
      favoritesStore,
      optimizationStore,
      taskScheduler,
      getEngine,
      tickIngressService,
      tickFeatureStore: tickFeatureStoreRef,
      tickStrategyService: tickStrategyServiceRef,
      tickHistoryStore: tickHistoryStoreRef,
      secure,
      securePublic,
    });

    // ── AI Workflow Instructions (injected into system prompt) ──
    api.on("before_prompt_build", async () => ({
      appendSystemContext: QUANT_WORKFLOW_INSTRUCTIONS,
    }));

    api.logger.info("quant-trading plugin registered (live trading enabled)");
  },
};

// ── Static AI workflow guidance ──────────────────────────────────
// Appended to the system prompt so the AI follows deterministic multi-step
// workflows instead of guessing tool sequences.
const QUANT_WORKFLOW_INSTRUCTIONS = `
## Quant Trading — 工具调用工作流指南

### 🚫 绝对禁止
- **禁止自己编造或默认使用股票代码**（如600519、000858、300750等蓝筹股）。
- 所有股票代码必须来自工具的实际返回结果。
- 如果工具失败且重试后仍然失败，必须如实告知用户，不要编造结果。

### 推荐选股：stock_recommend 工具
stock_recommend 是一个便捷的一键工具，自动完成 全市场选股→策略路由→回测验证→排名推荐 全流程。
也可以分步调用 stock_screen + strategy_router + backtest 手动组合，根据需要灵活选择。
**universe 参数：用户没有明确指定股票范围时，推荐使用 universe="all"（全市场扫描约5000只）。超时会自动降级到较小范围，无需担心性能。**
例: stock_recommend(count=3, universe="all") 即可全市场筛选并推荐3只经过回测的股票。

### screenerId 选择指南（classic模式下非常重要）
系统共有12种选股器，不同选股器产生完全不同的候选股。**同一选股器在同一天会返回相同结果**，所以：
- **用户说"再推荐/换一批/还有吗"** → 必须换一个不同的 screenerId 调用
- **用户没指定 screenerId** → 不传 screenerId 参数，系统会自动轮换选股器
- **用户有明确偏好** → 根据意图选择对应选股器：

| 用户意图 | 推荐 screenerId | 说明 |
|---------|----------------|------|
| 价值股/低估值/基本面好 | fscore, magic_formula | 财报驱动，适合中长线 |
| 成长股/高增长 | canslim, peg | 盈利+营收增速，适合中线 |
| 短线突破/技术买点 | technical | 均线/MACD/RSI金叉 |
| 量价异动/主力建仓 | volume_price | Wyckoff方法，适合波段 |
| 隔夜短线/尾盘买入 | overnight, eod_catalyst | 尾盘介入次日卖出 |
| 热点题材/龙头股 | sector_hotspot, dragon_master | 板块轮动+涨停池筛选 |
| 连板接力/打板 | lianban_relay | N进N+1候选 |
| 事件驱动/催化剂 | event_catalyst | 美股映射A股 |

提示：如果多次推荐结果相同，说明用的是同一个选股器。请换用不同的 screenerId 获取不同视角的推荐。

### ⚠️ strategy_router 调用规则（必须遵守）
- **strategy_router 的 symbols 参数是必填的**（除 routing_table/build_table/get_regime 三个 action 外）。
- **禁止不传 symbols 调用** strategy_router 的 profile/recommend/recommend_with_regime/route_portfolio/profile_route_backtest action。
- **禁止与 stock_screen 并行调用**——strategy_router 依赖 stock_screen 的返回结果，必须串行执行：先调用 stock_screen，等待其返回后，提取股票代码列表，再传入 strategy_router 的 symbols 参数。
- 正确示例：先调用 stock_screen → 等待返回 codes=[600519,000858] → 再调用 strategy_router(action=recommend, symbols="600519,000858")
- 错误示例1：stock_screen 和 strategy_router 同时并行调用 ← **strategy_router 拿不到 symbols，会报错**
- 错误示例2：stock_screen 返回结果后调用 strategy_router(action=recommend) 而不传 symbols ← **会报错**
- 也可以使用 stock_recommend 工具，它内部自动完成 选股→路由→回测 全流程，无需手动拼接。

### 单只股票回测分析（用户指定了具体股票时）
1. 调用 strategy_router(action=recommend, symbols=目标股票) 获取推荐策略。
2. 使用推荐策略ID调用 backtest(action=run, strategyId=推荐策略, symbols=目标股票, endDate=今天)。
3. 检查 warnings 和 forwardSignals，给出结论。

### 个股深度分析流程（用户问"帮我分析XX股票"时）
按以下顺序调用工具，构建完整的研报级分析：
1. **行情概览**: market_data(action=quote) — 当前价格、涨跌幅、成交量
2. **技术形态**: market_data(action=indicators) — MA/MACD/RSI/布林带，判断趋势方向和支撑压力位
3. **基本面**: market_data(action=fundamentals) + value_investing — PE/PB/ROE/毛利率，DCF估值
4. **资金面**: fund_flow(action=stock) + institutional_flow(action=fund_holdings) — 主力资金流向、机构持仓变化
5. **筹码分布**: chip_accumulation — 主力吸筹/出货信号
6. **事件催化**: earnings_calendar(action=upcoming) + news_summary(action=stock_news) — 近期财报和新闻
7. **策略验证**: strategy_router(action=recommend) → backtest — 量化策略回测验证
8. **风险评估**: risk_analysis — VaR、最大回撤、波动率
最后综合给出：趋势判断 + 估值水平 + 资金动向 + 操作建议 + **风险提示**

### 板块分析流程（用户问"哪个板块好/热点板块"时）
1. sector_analysis — 板块排名、涨跌幅排行
2. fund_flow(action=rank) — 板块级资金净流入排名
3. sector_synergy — 板块协同效应、联动分析
4. lianban_analysis — 连板梯队，辨识市场主线
5. dragon_tiger — 龙虎榜异动，游资动向

### 每日简报模板（用户说"简报/早报/收盘"时）
用以下结构组织回答：
1. 📊 **大盘概况** — 上证/深证/创业板涨跌 + 成交量对比
2. 🔥 **板块热点** — TOP5 板块 + 连板龙头
3. 💰 **资金动向** — 北向资金 + 主力净流入板块
4. ⚡ **个股异动** — 龙虎榜 + 涨停分析
5. 📋 **操作建议** — 基于数据的短线/中线观点 + 风险提示

### 重要规则
- backtest action=list 可查看所有可用策略（50+内置 + 用户组合策略）。
- 回测结果有 warnings 字段时必须向用户展示。
- 如果回测夏普比率>3但回撤>30%，或交易次数<5，说明统计不可靠。
- 所有投资建议必须附带风险提示，明确说明"以上分析仅供参考，不构成投资建议"。
- 回答中尽量使用表格呈现关键数据，便于对比。
- 区分短线（1-5日）、中线（1-3月）、长线（半年以上）不同维度给出建议。
`.trim();

export default quantTradingPlugin;
