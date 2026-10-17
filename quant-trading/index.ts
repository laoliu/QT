/**
 * @quant-trading/quant-trading — main application entry point
 *
 * Architecture overview:
 * ┌─────────────────────────────────────────────────────────────────┐
 * │  Quant Trading Application (Node.js / TypeScript)                   │
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
  QuantTradingRuntime,
} from "./src/runtime.js";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runPythonScript, setPythonBridgeLogger } from "./src/bridge/python-bridge.js";
import { enforceSafeTradingConfig, validateConfig } from "./src/config-validator.js";
import { createDataProvider } from "./src/data/market-data-provider.js";
import { LiveEngine } from "./src/engine/live-engine.js";
import { TickFeatureStore } from "./src/engine/tick-feature-store.js";
import { registerAdminRoutes } from "./src/http/admin-routes.js";
import { registerAiChatRoutes } from "./src/http/ai-chat-routes.js";
import { registerRiskOverlayRoutes } from "./src/http/routes/risk-overlay.js";
import {
  withSecurity,
  withCors,
  withRateLimit,
  RateLimiter,
  type HttpHandler,
} from "./src/http/auth-middleware.js";
import { registerQuantRoutes } from "./src/http/routes.js";
import { createRouteCollector, startStandaloneServer, stopStandaloneServer } from "./src/http/standalone-server.js";
import { AlertStore } from "./src/services/alert-store.js";
import { BacktestStore } from "./src/services/backtest-store.js";
import { BenchmarkService } from "./src/services/benchmark.js";
import { TradePlanStoreImpl } from "./src/services/trade-plan-store.js";
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
import { RecommendationTracker } from "./src/services/recommendation-tracker.js";
import { SignalHitRateTracker } from "./src/services/signal-hitrate.js";
import { AiChatService, defaultChatDbPath, type ChatTool } from "./src/services/ai-chat.js";
import { ChatStore } from "./src/services/chat-store.js";
import { resolveLlmConfig } from "./src/services/standalone-llm.js";
import { createSignalMonitorService } from "./src/services/signal-monitor.js";
import { InMemoryStorage } from "./src/services/sqlite-storage.js";
import { StrategyHealthMonitor } from "./src/services/strategy-health.js";
import { UserStrategyStore } from "./src/services/strategy-template.js";
import { TaskScheduler } from "./src/services/task-scheduler.js";
import { AutoTradeScheduler } from "./src/services/auto-trade-scheduler.js";
import { PortfolioRiskOverlay } from "./src/services/portfolio-risk-overlay.js";
import { TickHistoryStore } from "./src/services/tick-history-store.js";
import { TickIngressService } from "./src/services/tick-ingress-service.js";
import { TickStrategyService } from "./src/services/tick-strategy-service.js";
import { TradeJournal } from "./src/services/trade-journal.js";
import { createAiphaBacktestTool } from "./src/tools/aipha-backtest.js";
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
import { createFactorAnalysisTool } from "./src/tools/factor-analysis.js";
import { createFavoritesTool } from "./src/tools/favorites.js";
import { createFinancialCalculatorTool } from "./src/tools/financial-calculator.js";
import { createFundFlowTool } from "./src/tools/fund-flow.js";
import { createFuturesStraddleTool } from "./src/tools/futures-straddle.js";
import { createInstitutionalFlowTool } from "./src/tools/institutional-flow.js";
import { createLianbanAnalysisTool } from "./src/tools/lianban-analysis.js";
import { createLiveTradingTool } from "./src/tools/live-trading.js";
import { createMacroDataTool } from "./src/tools/macro-data.js";
import { createMarketDataTool } from "./src/tools/market-data.js";
import { createMLRankingTool } from "./src/tools/ml-ranking.js";
import { createNewsSummaryTool } from "./src/tools/news-summary.js";
import { createOptionsDataTool } from "./src/tools/options-data.js";
import { createOptionsStrategyTool } from "./src/tools/options-strategy.js";
import { createOptionsTradingTool } from "./src/tools/options-trading.js";
import { createPerformanceAttributionTool } from "./src/tools/performance-attribution.js";
import { createPolymarketOddsTool } from "./src/tools/polymarket-odds.js";
import { createPortfolioBacktestTool } from "./src/tools/portfolio-backtest.js";
import { createPortfolioOptimizerTool } from "./src/tools/portfolio-optimizer.js";
import { createCBAnalysisTool } from "./src/tools/cb-analysis.js";
import { createStrategyValidationTool } from "./src/tools/strategy-validation.js";
import { createEquityScreeningTool } from "./src/tools/equity-screening.js";
import { createAiphaMonitorTool } from "./src/tools/aipha-monitor.js";
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
import { createRecommendTrackerTool } from "./src/tools/recommend-tracker.js";
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

/** Append a signal to signals.json for the trading_signal tool. */
async function persistSignal(signal: TradingSignal, config: QuantTradingConfig): Promise<void> {
  const { default: fs } = await import("node:fs/promises");
  const { default: path } = await import("node:path");
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
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

/** Decision log entry for post-trade analysis. */
interface DecisionLogEntry {
  timestamp: string;
  symbol: string;
  action: "buy" | "sell" | "reject" | "size_adjust" | "skip";
  source: "signal" | "recommend" | "conditional" | "scheduler";
  reason: string;
  details: Record<string, unknown>;
}

/** Append a decision log entry for post-trade analysis. */
async function persistDecisionLog(entry: DecisionLogEntry, config: QuantTradingConfig): Promise<void> {
  const { default: fs } = await import("node:fs/promises");
  const { default: path } = await import("node:path");
  const dir = config.dataDir ?? path.join(process.cwd(), "data");
  const filePath = path.join(dir, "decision-log.json");
  let log: DecisionLogEntry[] = [];
  try {
    log = JSON.parse(await fs.readFile(filePath, "utf-8")) as DecisionLogEntry[];
  } catch {
    // file doesn't exist yet
  }
  log.push(entry);
  if (log.length > 5000) log = log.slice(-5000);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(log, null, 2));
}

const quantTradingApp = {
  id: "quant-trading",
  name: "Quant Trading",
  description:
    "Quantitative trading toolkit: market data, strategy backtesting, portfolio management, trading signals, and live broker trading for stocks, futures, and funds.",
  start(api: QuantTradingRuntime) {
    const rawConfig = api.pluginConfig as QuantTradingConfig;
    const safety = enforceSafeTradingConfig(rawConfig);
    const config = safety.config;
    if (safety.forcedPaper) {
      api.logger.warn(
        `[quant-trading] ${safety.reason}; runtime forced to paper mode`,
      );
    }

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

    // Forward Python subprocess stderr to LogStore
    setPythonBridgeLogger(api.logger);

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

    // ── Portfolio Risk Overlay (live MDD gradient + vol scaling) ──
    const portfolioRiskOverlay = new PortfolioRiskOverlay({
      dataDir: config.dataDir ?? undefined,
      log: (msg, level) => api.logger[level === "error" ? "error" : level === "warn" ? "warn" : "info"](msg),
    });
    void portfolioRiskOverlay.load().then(() => {
      portfolioRiskOverlay.start(60_000);
    });

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

        // Auto-create market context conditional orders for buy signals
        if (signal.direction === "buy") {
          try {
            const ctx = liveEngine.marketContext.getContext(signal.symbol);
            const qty = signal.quantity ?? 100;

            if (ctx.sentiment && ctx.sentiment.score > 0) {
              conditionalOrderService.addSentimentCrash({
                symbol: signal.symbol,
                entryPrice: signal.price,
                quantity: qty,
                baselineSentiment: ctx.sentiment.score,
                strategyId: signal.strategyId ?? "live-signal",
              });
            }

            if (ctx.lianban?.inPool && ctx.lianban.level != null) {
              conditionalOrderService.addLianbanExit({
                symbol: signal.symbol,
                entryPrice: signal.price,
                quantity: qty,
                baselineLianbanLevel: ctx.lianban.level,
                strategyId: signal.strategyId ?? "live-signal",
              });
            }

            if (ctx.macroRegime && ctx.macroRegime !== "UNKNOWN") {
              conditionalOrderService.addRegimeShift({
                symbol: signal.symbol,
                entryPrice: signal.price,
                quantity: qty,
                baselineRegime: ctx.macroRegime,
                strategyId: signal.strategyId ?? "live-signal",
              });
            }
          } catch {
            // Context not ready — skip context conditional orders
          }
        }
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

      // Persist structured decision log for post-trade analysis
      try {
        const ctx = liveEngine.marketContext.getContext(signal.symbol);
        void persistDecisionLog({
          timestamp: new Date(signal.timestamp).toISOString(),
          symbol: signal.symbol,
          action: result.ok ? (signal.direction as "buy" | "sell") : "reject",
          source: "signal",
          reason: result.ok
            ? (signal.reason ?? signal.direction)
            : (result.reason ?? "风控拒绝"),
          details: {
            strategyId: signal.strategyId,
            price: signal.price,
            quantity: signal.quantity,
            confidence: signal.confidence,
            orderId: result.orderId,
            sentimentScore: ctx.sentiment?.score ?? null,
            sentimentTone: ctx.sentiment?.tone ?? null,
            macroRegime: ctx.macroRegime ?? null,
            volatilityRegime: ctx.volatilityRegime ?? null,
            lianbanInPool: ctx.lianban?.inPool ?? false,
            lianbanLevel: ctx.lianban?.level ?? null,
            stStage: ctx.stStatus?.stage ?? null,
            stRiskLevel: ctx.stStatus?.riskLevel ?? null,
            catalystType: ctx.catalyst?.eventType ?? null,
            catalystDaysUntil: ctx.catalyst?.daysUntil ?? null,
          },
        }, config);
      } catch {
        // Context not available — log without enrichment
        void persistDecisionLog({
          timestamp: new Date(signal.timestamp).toISOString(),
          symbol: signal.symbol,
          action: result.ok ? (signal.direction as "buy" | "sell") : "reject",
          source: "signal",
          reason: result.ok ? (signal.reason ?? signal.direction) : (result.reason ?? "风控拒绝"),
          details: {
            strategyId: signal.strategyId,
            price: signal.price,
            quantity: signal.quantity,
            confidence: signal.confidence,
            orderId: result.orderId,
          },
        }, config);
      }
    });

    // Wire LiveEngine account sync to equity history + event bus + risk overlay
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

      // Feed NAV to portfolio risk overlay (MDD gradient + vol scaling)
      const overlayScale = portfolioRiskOverlay.updateNav(account.totalAssets, snap.date);
      if (overlayScale < 1.0 && portfolioRiskOverlay.getConfig().enabled) {
        eventBus.emit("risk_overlay", {
          scale: overlayScale,
          state: portfolioRiskOverlay.getStatus().mddState,
          drawdown: portfolioRiskOverlay.getStatus().currentDrawdown,
        });
      }

      // Auto-register price alerts for held positions
      const posSymbols = positions
        .filter((p) => p.quantity > 0)
        .map((p) => p.symbol);
      if (posSymbols.length > 0) {
        const created = priceAlertService.syncPositionAlerts(posSymbols);
        if (created > 0) {
          api.logger.info(`Auto-created ${created} price alert(s) for held positions`);
        }
      }
    });

    // Wire completed trades to strategy health monitor
    liveEngine.setTradeCallback((trade) => {
      if (trade.strategyId && trade.exitTime) {
        strategyHealthMonitor.recordTrade(trade.strategyId, trade.netPnl, trade.exitTime);
      }
    });

    // Wire portfolio risk overlay into LiveEngine → OrderManager
    liveEngine.setRiskOverlayFn(() => portfolioRiskOverlay.getScale());

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

    // ── Recommendation Tracker (RE-8: 推荐追踪与复盘) ─────────────
    const recommendationTracker = new RecommendationTracker({
      dataDir: config.dataDir ?? undefined,
      maxRecords: 500,
    });
    recommendationTracker.setNotify((m: string) => tryNotify(api, m));
    recommendationTracker.start();

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
      stock_recommend: createStockRecommendTool(api, config, scanHistoryStore, () => createDataProvider(config), recommendationTracker),
      recommend_tracker: createRecommendTrackerTool(api, recommendationTracker, () => createDataProvider(config)),
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
      // ── Aipha 高级分析工具 ──
      factor_analysis: createFactorAnalysisTool(api, config),
      ml_ranking: createMLRankingTool(api, config),
      aipha_backtest: createAiphaBacktestTool(api, config),
      portfolio_optimizer: createPortfolioOptimizerTool(api, config),
      cb_analysis: createCBAnalysisTool(api, config),
      strategy_validation: createStrategyValidationTool(api, config),
      equity_screening: createEquityScreeningTool(api, config),
      aipha_monitor: createAiphaMonitorTool(api, config),
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
      recommend_tracker: "推荐追踪复盘",
      custom_strategy: "自定义策略",
      position_sizing: "仓位管理",
      breakout_monitor: "突破监控",
      factor_analysis: "因子分析",
      ml_ranking: "ML选股排序",
      aipha_backtest: "高级回测",
      portfolio_optimizer: "组合优化",
      cb_analysis: "可转债分析",
      strategy_validation: "策略验证",
      equity_screening: "AI选股筛选",
      aipha_monitor: "因子策略监控",
    };
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
    // Wire exit function to OrderManager (places market buy/sell order)
    conditionalOrderService.setExitFn(async (symbol, quantity, reason, strategyId, exitSide) => {
      const mgr = liveEngine.orderManager;
      if (!mgr) return { ok: false, reason: "引擎未启动" };
      const { detectMarketFromSymbol } = await import("./src/data/market-data-provider.js");
      const side = exitSide === "buy" ? "buy" : "sell";
      const result = await mgr.submit(
        {
          symbol,
          market: detectMarketFromSymbol(symbol),
          side,
          orderType: "market",
          quantity,
          strategyId: strategyId ?? "conditional_exit",
          signalId: `cond_${Date.now()}`,
        },
        { skipMarketHours: true },
      );
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

    // Wire market context to conditional order service for enrichment triggers
    conditionalOrderService.setMarketContextFn((symbol: string) =>
      liveEngine.marketContext.getContext(symbol),
    );

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
    // true the engine starts automatically with the application; otherwise it waits
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

      // Wire real-time quotes to conditional order checks (low-latency grid triggers)
      tickIngressService.onQuote((quote) => {
        if (quote.price > 0) {
          void conditionalOrderService.checkSymbol(quote.symbol.code, quote.price).catch(() => {});
        }
      });
      liveEngine.setWatchedSymbolsCallback((symbols) => {
        // Merge engine symbols + conditional order symbols for tick subscription
        const coSymbols = conditionalOrderService.getActiveSymbols();
        const allSymbols = [...new Set([...symbols, ...coSymbols])];
        void tickIngressService.syncSubscriptions(allSymbols).catch((err) => {
          api.logger.warn(
            `[quant-trading] tick ingress subscription sync failed: ${(err as Error).message}`,
          );
        });
        tickStrategyService.setActiveSymbols(symbols);
      });

      // Initial tick subscription for conditional order symbols (engine may have 0 strategies)
      {
        const coSymbols = conditionalOrderService.getActiveSymbols();
        if (coSymbols.length > 0) {
          void tickIngressService.syncSubscriptions(coSymbols).catch((err) => {
            api.logger.warn(
              `[quant-trading] tick ingress initial CO subscription failed: ${(err as Error).message}`,
            );
          });
        }
      }

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
          try {
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const modPath = "./src/services/market-regime.js";
            const mod = await (Function("p", "return import(p)")(modPath) as Promise<{ createMarketRegimeDetector: (c: typeof config) => () => Promise<string | null> }>);
            priceRegimeDetect = mod.createMarketRegimeDetector(config);
          } catch {
            // Market regime module not available
            return "unknown";
          }
        }
        return priceRegimeDetect!();
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

    // ── Standalone server: intercept route registrations ───
    const standalonePort = config.standalonePort
      ? Number(config.standalonePort)
      : 0;
    const routeCollector = standalonePort ? createRouteCollector() : null;
    if (routeCollector) {
      const origRegister = api.registerHttpRoute.bind(api);
      api.registerHttpRoute = (params) => {
        origRegister(params);
        routeCollector.collect({
          path: params.path,
          handler: params.handler,
          match: params.match,
        });
      };
    }

    const tradePlanStore = new TradePlanStoreImpl({
      persistPath: config.dataDir ? `${config.dataDir}/trade-plans.json` : null,
    });

    // ── Auto-Trade Scheduler ─────────────────────────────────────
    const autoTradeScheduler = new AutoTradeScheduler({
      dataDir: config.dataDir ?? null,
      baseUrl: `http://127.0.0.1:${standalonePort || 18891}`,
      authToken: config.apiToken ?? "",
    });
    autoTradeScheduler.setNotify((m: string) => tryNotify(api, m));

    // Wire market context to auto-trade scheduler for pre-exec validation
    autoTradeScheduler.setMarketContextFn((symbol: string) =>
      liveEngine.marketContext.getContext(symbol),
    );

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
      tradePlanStore,
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
      signalHitRateTracker,
      autoTradeScheduler,
      secure,
      securePublic,
    });

    const chatStore = new ChatStore(defaultChatDbPath(config.dataDir));
    const chatService = new AiChatService(
      chatStore,
      resolveLlmConfig(api.pluginConfig),
      path.join(path.dirname(fileURLToPath(import.meta.url)), "skills"),
      Object.values(getToolMap()) as ChatTool[],
    );
    registerAiChatRoutes({ api, chat: chatService, store: chatStore, secure });
    api.registerService({
      id: "ai-chat-store",
      async start() { await chatService.initialize(); },
      async stop() { chatStore.close(); },
    });

    // ── Risk Overlay API Routes ──────────────────────────────
    registerRiskOverlayRoutes(
      { api, config, dataWarehouse, receiverService, userStrategyStore, alertStore, eventBus, logStore, priceAlertService, conditionalOrderService, favoritesStore, taskScheduler, getEngine, secure, securePublic },
      portfolioRiskOverlay,
    );

    // ── Standalone HTTP Server ─────────────────────────────
    if (routeCollector && standalonePort) {
      startStandaloneServer(routeCollector.routes, standalonePort, api.logger);
      api.registerService({
        id: "standalone-http",
        async start() { /* already started above */ },
        async stop() { stopStandaloneServer(); },
      });

      // ── Auto-Trade Scheduler (requires standalone server) ───
      autoTradeScheduler.start();
      api.registerService({
        id: "auto-trade-scheduler",
        async start() { /* already started above */ },
        async stop() { autoTradeScheduler.destroy(); },
      });
    }

    // ── AI Workflow Instructions (injected into system prompt) ──

    api.logger.info("quant-trading application started");
  },
};

export default quantTradingApp;
