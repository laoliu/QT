import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { createTickDrivenStrategy, type TickDrivenStrategy } from "../engine/tick-strategies.js";
import type { TickFeatures, TickStrategyId, TradingSignal, QuantTradingConfig } from "../types.js";
import type { EventBus } from "./event-bus.js";
import type { OrderManager } from "./order-manager.js";
import type { TickIngressService } from "./tick-ingress-service.js";

type TickStrategyConfig = NonNullable<
  NonNullable<QuantTradingConfig["tickData"]>["strategies"]
>[number];

type StrategyRuntime = {
  key: string;
  strategyId: TickStrategyId;
  symbol: string;
  submitOrders: boolean;
  strategy: TickDrivenStrategy;
};

export class TickStrategyService implements OpenClawPluginService {
  readonly id = "qmt-tick-strategies";

  private readonly runtimes = new Map<string, StrategyRuntime>();
  private activeSymbols = new Set<string>();
  private unsubscribeFeatures: (() => void) | null = null;
  private unsubscribeBars: (() => void) | null = null;
  private readonly recentSignalTimestamps = new Map<string, number>();
  private readonly signalDedupeWindowMs = 100;

  constructor(
    private readonly config: QuantTradingConfig,
    private readonly api: OpenClawPluginApi,
    private readonly tickIngress: TickIngressService,
    private readonly eventBus: EventBus,
    private readonly getOrderManager: () => OrderManager | null,
  ) {}

  async start(): Promise<void> {
    if (this.unsubscribeFeatures) return;
    this.refreshRuntimes();
    this.unsubscribeFeatures = this.tickIngress.onTickFeatures((features) => {
      void this.handleFeatures(features);
    });
    this.unsubscribeBars = this.tickIngress.onBar((symbol, bar, interval) => {
      for (const runtime of this.runtimes.values()) {
        if (runtime.symbol !== symbol) continue;
        runtime.strategy.onBar?.(bar, interval);
      }
    });
  }

  async stop(): Promise<void> {
    this.unsubscribeFeatures?.();
    this.unsubscribeFeatures = null;
    this.unsubscribeBars?.();
    this.unsubscribeBars = null;
    this.runtimes.clear();
  }

  setActiveSymbols(symbols: string[]): void {
    this.activeSymbols = new Set(symbols);
    this.refreshRuntimes();
  }

  getConfiguredStrategies(): TickStrategyConfig[] {
    return [...(this.config.tickData?.strategies ?? [])];
  }

  setConfiguredStrategies(strategies: TickStrategyConfig[]): void {
    if (!this.config.tickData) this.config.tickData = {};
    this.config.tickData.strategies = [...strategies];
    this.refreshRuntimes();
  }

  refreshFromConfig(): void {
    this.refreshRuntimes();
  }

  getStatus(): {
    running: boolean;
    activeSymbols: string[];
    runtimes: Array<{
      key: string;
      strategyId: string;
      symbol: string;
      submitOrders: boolean;
      strategyName: string;
    }>;
  } {
    return {
      running: this.unsubscribeFeatures !== null,
      activeSymbols: [...this.activeSymbols].sort(),
      runtimes: [...this.runtimes.values()].map((r) => ({
        key: r.key,
        strategyId: r.strategyId,
        symbol: r.symbol,
        submitOrders: r.submitOrders,
        strategyName: r.strategy.name,
      })),
    };
  }

  private refreshRuntimes(): void {
    const next = new Map<string, StrategyRuntime>();
    const configs = (this.config.tickData?.strategies ?? []).filter(
      (strategy) => strategy.enabled !== false,
    );

    for (const strategyCfg of configs) {
      const symbols =
        strategyCfg.symbols && strategyCfg.symbols.length > 0
          ? strategyCfg.symbols
          : [...this.activeSymbols];
      for (const symbol of symbols) {
        const key = `${strategyCfg.id}:${symbol}`;
        const existing = this.runtimes.get(key);
        next.set(
          key,
          existing ?? {
            key,
            strategyId: strategyCfg.id,
            symbol,
            submitOrders: strategyCfg.submitOrders === true,
            strategy: createTickDrivenStrategy(strategyCfg.id, {
              symbol,
              quantity: strategyCfg.quantity,
              parameters: strategyCfg.parameters,
            }),
          },
        );
      }
    }

    this.runtimes.clear();
    for (const [key, runtime] of next) {
      this.runtimes.set(key, runtime);
    }
  }

  private async handleFeatures(features: TickFeatures): Promise<void> {
    const relevant = [...this.runtimes.values()].filter(
      (runtime) => runtime.symbol === features.symbol,
    );
    if (relevant.length === 0) return;

    const tick = this.tickIngress.getLatestTick(features.symbol);
    if (!tick) return;

    for (const runtime of relevant) {
      const signal = runtime.strategy.onTick({ tick, features });
      if (!signal) continue;
      await this.dispatchSignal(signal, runtime.submitOrders);
    }
  }

  private async dispatchSignal(signal: TradingSignal, submitOrders: boolean): Promise<void> {
    const dedupKey = `${signal.symbol}:${signal.strategyId}`;
    const lastTimestamp = this.recentSignalTimestamps.get(dedupKey);
    const now = signal.timestamp;

    if (lastTimestamp != null && now - lastTimestamp < this.signalDedupeWindowMs) {
      this.api.logger.info(
        `[tick-strategy] duplicate signal suppressed: ${dedupKey} (${now - lastTimestamp}ms window)`,
      );
      return;
    }

    this.recentSignalTimestamps.set(dedupKey, now);

    this.eventBus.emit("signal", {
      symbol: signal.symbol,
      direction: signal.direction,
      price: signal.price,
      quantity: signal.quantity,
      strategyId: signal.strategyId,
      reason: signal.reason,
      confidence: signal.confidence,
      executed: false,
    });

    if (!submitOrders) return;

    const mgr = this.getOrderManager();
    if (!mgr) {
      this.api.logger.warn(
        `[tick-strategy] signal generated but order manager is unavailable: ${signal.strategyId} ${signal.symbol}`,
      );
      return;
    }

    const result = await mgr.submitFromSignal(signal);
    if (result.ok) {
      this.eventBus.emit("order", {
        orderId: result.order.orderId,
        symbol: signal.symbol,
        side: signal.direction,
        price: signal.price,
        quantity: signal.quantity ?? 100,
        status: result.order.status,
        strategyId: signal.strategyId,
      });
      return;
    }

    this.api.logger.warn(
      `[tick-strategy] order blocked: ${signal.strategyId} ${signal.symbol} ${result.reason}`,
    );
  }
}
