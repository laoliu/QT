/**
 * In-process EventBus with Server-Sent Events (SSE) streaming.
 *
 * Provides a typed publish/subscribe bus that can push events to
 * connected SSE clients in real-time.
 *
 * Event categories:
 *   signal    — trading signal from a strategy
 *   order     — order lifecycle (submitted/filled/cancelled/rejected)
 *   position  — position change (opened/closed/updated)
 *   pnl       — PnL update (per-position or portfolio-level)
 *   engine    — engine status change (started/stopped/paused/resumed/error)
 *   alert     — risk alert, staleness alert, circuit breaker
 *   equity    — equity curve point (periodic snapshot)
 */
import type { ServerResponse } from "node:http";

// ── Event Types ──────────────────────────────────────────────────

export type EventCategory =
  | "signal"
  | "order"
  | "position"
  | "pnl"
  | "engine"
  | "alert"
  | "equity"
  | "tick"
  | "data_sync"
  | "sector_flow_anomaly";

export interface QuantEvent {
  /** Unique event ID (monotonic counter) */
  id: number;
  /** Event category for filtering */
  category: EventCategory;
  /** ISO-8601 timestamp */
  timestamp: string;
  /** Arbitrary payload */
  data: unknown;
}

export type EventHandler = (event: QuantEvent) => void;

// ── EventBus ─────────────────────────────────────────────────────

export class EventBus {
  private nextId = 1;
  private handlers = new Map<string, Set<EventHandler>>();
  private sseClients = new Set<ServerResponse>();
  /** Sliding window of recent events for re-delivery / catch-up */
  private recentEvents: QuantEvent[] = [];
  private readonly maxRecent: number;

  constructor(opts?: { maxRecent?: number }) {
    this.maxRecent = opts?.maxRecent ?? 200;
  }

  // ── Pub/Sub ──────────────────────────────────────────────────

  /** Subscribe to a specific category (or "*" for all). */
  on(category: EventCategory | "*", handler: EventHandler): () => void {
    const key = category;
    let set = this.handlers.get(key);
    if (!set) {
      set = new Set();
      this.handlers.set(key, set);
    }
    set.add(handler);
    return () => {
      set!.delete(handler);
      if (set!.size === 0) this.handlers.delete(key);
    };
  }

  /** Publish an event to all subscribers + SSE clients. */
  emit(category: EventCategory, data: unknown): QuantEvent {
    const event: QuantEvent = {
      id: this.nextId++,
      category,
      timestamp: new Date().toISOString(),
      data,
    };

    // In-memory history
    this.recentEvents.push(event);
    if (this.recentEvents.length > this.maxRecent) {
      this.recentEvents.splice(0, this.recentEvents.length - this.maxRecent);
    }

    // Notify in-process subscribers
    const categoryHandlers = this.handlers.get(category);
    if (categoryHandlers) {
      for (const h of categoryHandlers) {
        try {
          h(event);
        } catch {
          /* swallow subscriber errors */
        }
      }
    }
    const wildcardHandlers = this.handlers.get("*");
    if (wildcardHandlers) {
      for (const h of wildcardHandlers) {
        try {
          h(event);
        } catch {
          /* swallow subscriber errors */
        }
      }
    }

    // Push to SSE clients
    this.broadcastSSE(event);

    return event;
  }

  // ── SSE ──────────────────────────────────────────────────────

  /**
   * Register an SSE client connection.
   *
   * Sends appropriate headers, optional catch-up from `lastEventId`,
   * and keeps the connection open until the client disconnects.
   *
   * @param res     The HTTP response to stream events to
   * @param filter  Optional category filter (comma-separated)
   * @param lastEventId  Last-Event-ID for catch-up (from header or query)
   */
  addSSEClient(res: ServerResponse, filter?: string, lastEventId?: string): void {
    // SSE headers
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no", // nginx passthrough
    });

    // Send initial comment to keep connection alive
    res.write(":ok\n\n");

    // Catch-up: replay events after lastEventId
    if (lastEventId) {
      const afterId = Number(lastEventId);
      if (!Number.isNaN(afterId)) {
        const categories = filter ? new Set(filter.split(",")) : null;
        for (const event of this.recentEvents) {
          if (event.id > afterId) {
            if (!categories || categories.has(event.category)) {
              this.writeSSE(res, event);
            }
          }
        }
      }
    }

    // Store client with metadata
    const client = res as ServerResponse & { __sseFilter?: Set<string> | null };
    client.__sseFilter = filter ? new Set(filter.split(",")) : null;
    this.sseClients.add(client);

    // Clean up on disconnect
    res.on("close", () => {
      this.sseClients.delete(client);
    });
  }

  /** Number of currently connected SSE clients. */
  get clientCount(): number {
    return this.sseClients.size;
  }

  /** Recent events (for debugging / APIs). */
  getRecentEvents(category?: EventCategory, limit?: number): QuantEvent[] {
    let events = this.recentEvents;
    if (category) {
      events = events.filter((e) => e.category === category);
    }
    if (limit && limit > 0) {
      events = events.slice(-limit);
    }
    return events;
  }

  /** Disconnect all SSE clients and clear state. */
  close(): void {
    for (const client of this.sseClients) {
      try {
        client.end();
      } catch {
        /* ignore */
      }
    }
    this.sseClients.clear();
    this.handlers.clear();
    this.recentEvents = [];
    this.nextId = 1;
  }

  // ── Internal ─────────────────────────────────────────────────

  private broadcastSSE(event: QuantEvent): void {
    for (const client of this.sseClients) {
      const filtered = (client as ServerResponse & { __sseFilter?: Set<string> | null })
        .__sseFilter;
      if (filtered && !filtered.has(event.category)) continue;
      try {
        this.writeSSE(client, event);
      } catch {
        // Client disconnected without close event — remove it
        this.sseClients.delete(client);
      }
    }
  }

  private writeSSE(res: ServerResponse, event: QuantEvent): void {
    res.write(`id: ${event.id}\n`);
    res.write(`event: ${event.category}\n`);
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  }
}
