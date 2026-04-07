/**
 * FutuEventBridge — wires FutuBroker push events to the in-process EventBus.
 *
 * When the LiveEngine starts with a Futu broker, this bridge subscribes to
 * fills, order status changes, position changes, and optionally order book /
 * ticker events, then re-emits them on the EventBus so SSE clients and other
 * services can consume them.
 *
 * Usage:
 *   const bridge = new FutuEventBridge(futuBroker, eventBus);
 *   bridge.start();   // wires callbacks
 *   bridge.stop();    // removes callbacks
 */
import type { FutuBroker } from "../broker/futu-broker.js";
import type { IBrokerAdapter } from "../broker/adapter.js";
import type { EventBus } from "./event-bus.js";

export class FutuEventBridge {
  private broker: FutuBroker;
  private eventBus: EventBus;
  private unsubs: Array<() => void> = [];

  constructor(broker: IBrokerAdapter, eventBus: EventBus) {
    this.broker = broker as FutuBroker;
    this.eventBus = eventBus;
  }

  /**
   * Wire all FutuBroker push events to the EventBus.
   * Call once after the broker is connected.
   */
  start(): void {
    // Fill events → EventBus "order" category
    this.unsubs.push(
      this.broker.subscribeFills((fill) => {
        this.eventBus.emit("order", {
          type: "fill",
          orderId: fill.orderId,
          symbol: fill.symbol,
          side: fill.side,
          price: fill.price,
          quantity: fill.quantity,
          commission: fill.commission,
          timestamp: fill.timestamp,
        });
      }),
    );

    // Order status changes → EventBus "order" category
    this.unsubs.push(
      this.broker.subscribeOrderStatus((orderId, status, order) => {
        this.eventBus.emit("order", {
          type: "order_status",
          orderId,
          symbol: order.symbol,
          side: order.side,
          status,
          price: order.price,
          quantity: order.quantity,
          filledQty: order.filledQty,
          avgFillPrice: order.avgFillPrice,
          brokerType: "futu",
        });
      }),
    );

    // Position changes → EventBus "position" category
    if (typeof this.broker.onPositionChange === "function") {
      this.unsubs.push(
        this.broker.onPositionChange((code, pos) => {
          this.eventBus.emit("position", {
            type: "position_change",
            symbol: code,
            position: pos
              ? {
                  quantity: pos.quantity,
                  avgCost: pos.avgCost,
                  currentPrice: pos.currentPrice,
                  marketValue: pos.marketValue,
                  unrealizedPnl: pos.unrealizedPnl,
                  unrealizedPnlPct: pos.unrealizedPnlPct,
                }
              : null,
            brokerType: "futu",
          });
        }),
      );
    }

    // Order book depth → EventBus "tick" category
    if (typeof this.broker.subscribeOrderBook === "function") {
      this.unsubs.push(
        this.broker.subscribeOrderBook((code, bids, asks, time) => {
          this.eventBus.emit("tick", {
            type: "order_book",
            symbol: code,
            bids,
            asks,
            timestamp: time,
            brokerType: "futu",
          });
        }),
      );
    }

    // Ticker (tick-by-tick trades) → EventBus "tick" category
    if (typeof this.broker.subscribeTicker === "function") {
      this.unsubs.push(
        this.broker.subscribeTicker((data) => {
          this.eventBus.emit("tick", {
            type: "ticker",
            symbol: data.code,
            price: data.price,
            volume: data.volume,
            turnover: data.turnover,
            direction: data.direction,
            sequence: data.sequence,
            time: data.time,
            brokerType: "futu",
          });
        }),
      );
    }
  }

  /** Remove all event subscriptions. */
  stop(): void {
    for (const unsub of this.unsubs) unsub();
    this.unsubs = [];
  }
}
