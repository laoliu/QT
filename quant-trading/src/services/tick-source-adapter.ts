import { WindowsBridgeEventPoller, directPost } from "../bridge/windows-bridge-client.js";
import type { QuantTradingConfig } from "../types.js";

export type TickSourceEvent = {
  event: string;
  data: unknown;
};

export type TickSourceStatus = {
  kind: string;
  running: boolean;
  endpoint?: string;
};

export interface TickSourceAdapter {
  readonly kind: string;
  readonly endpoint?: string;
  start(onEvent: (event: TickSourceEvent) => void): void;
  stop(): void;
  subscribe(symbols: string[]): Promise<void>;
  unsubscribe(symbols: string[]): Promise<void>;
  toInternalSymbol(symbol: string): string;
  toSourceSymbol(symbol: string): string;
  getStatus(): TickSourceStatus;
}

function toQmtSymbol(symbol: string): string {
  if (symbol.includes(".")) return symbol;
  const code = symbol.replace(/[^0-9A-Za-z]/g, "");
  const n = parseInt(code, 10);
  if (!Number.isNaN(n)) {
    if (code.startsWith("8") || code.startsWith("43")) return `${code}.BJ`;
    if (n >= 500_000) return `${code}.SH`;
    return `${code}.SZ`;
  }
  return symbol;
}

function fromQmtSymbol(symbol: string): string {
  return symbol.replace(/\.(SH|SZ|BJ)$/i, "");
}

class QmtBridgeTickSource implements TickSourceAdapter {
  readonly kind = "qmt-bridge";
  readonly endpoint: string;
  private readonly pollIntervalMs: number;
  private poller: WindowsBridgeEventPoller | null = null;
  private running = false;

  constructor(bridgeUrl: string, pollIntervalMs: number) {
    this.endpoint = bridgeUrl;
    this.pollIntervalMs = pollIntervalMs;
  }

  start(onEvent: (event: TickSourceEvent) => void): void {
    if (this.running) return;
    this.poller = new WindowsBridgeEventPoller(
      this.endpoint,
      (event) => onEvent({ event: event.event, data: event.data }),
      this.pollIntervalMs,
    );
    this.poller.start();
    this.running = true;
  }

  stop(): void {
    this.poller?.stop();
    this.poller = null;
    this.running = false;
  }

  async subscribe(symbols: string[]): Promise<void> {
    if (symbols.length === 0) return;
    await directPost<{ ok: boolean; error?: string }>(
      this.endpoint,
      "/trading",
      {
        method: "subscribe_quote",
        params: { stock_codes: symbols.map((s) => this.toSourceSymbol(s)) },
      },
      10_000,
    );
  }

  async unsubscribe(symbols: string[]): Promise<void> {
    if (symbols.length === 0) return;
    await directPost<{ ok: boolean; error?: string }>(
      this.endpoint,
      "/trading",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: symbols.map((s) => this.toSourceSymbol(s)) },
      },
      10_000,
    );
  }

  toInternalSymbol(symbol: string): string {
    return fromQmtSymbol(symbol);
  }

  toSourceSymbol(symbol: string): string {
    return toQmtSymbol(symbol);
  }

  getStatus(): TickSourceStatus {
    return {
      kind: this.kind,
      running: this.running,
      endpoint: this.endpoint,
    };
  }
}

class ReceiverProxyTickSource implements TickSourceAdapter {
  readonly kind = "receiver-proxy";
  readonly endpoint: string;
  private readonly pollIntervalMs: number;
  private readonly fetchTimeoutMs = 5000;
  private timer: ReturnType<typeof setInterval> | null = null;
  private afterSeq = 0;
  private running = false;
  private isPolling = false;

  constructor(endpoint: string, pollIntervalMs: number, initialSeq = 0) {
    this.endpoint = endpoint.replace(/\/$/, "");
    this.pollIntervalMs = pollIntervalMs;
    this.afterSeq = initialSeq;
  }

  start(onEvent: (event: TickSourceEvent) => void): void {
    if (this.running) return;

    const poll = async () => {
      if (this.isPolling) return;
      this.isPolling = true;

      try {
        const controller = new AbortController();
        const timeoutHandle = setTimeout(() => controller.abort(), this.fetchTimeoutMs);

        const url = new URL(`${this.endpoint}/quant/api/trading-events`);
        if (this.afterSeq > 0) url.searchParams.set("afterSeq", String(this.afterSeq));
        const resp = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutHandle);

        if (!resp.ok) return;
        const data = (await resp.json()) as {
          events?: Array<{ seq?: number; event: string; data: unknown }>;
          latestSeq?: number;
        };
        for (const evt of data.events ?? []) {
          onEvent({ event: evt.event, data: evt.data });
          if (typeof evt.seq === "number" && Number.isFinite(evt.seq)) {
            this.afterSeq = Math.max(this.afterSeq, evt.seq);
          }
        }
        if (typeof data.latestSeq === "number" && Number.isFinite(data.latestSeq)) {
          this.afterSeq = Math.max(this.afterSeq, data.latestSeq);
        }
      } catch {
        // receiver/gateway temporarily unavailable or timeout
      } finally {
        this.isPolling = false;
      }
    };

    this.timer = setInterval(() => void poll(), this.pollIntervalMs);
    void poll();
    this.running = true;
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.running = false;
  }

  async subscribe(symbols: string[]): Promise<void> {
    if (symbols.length === 0) return;
    await directPost<{ status?: string; requestId?: string }>(
      this.endpoint,
      "/quant/api/trading-request",
      {
        method: "subscribe_quote",
        params: { stock_codes: symbols.map((s) => this.toSourceSymbol(s)) },
      },
      10_000,
    );
  }

  async unsubscribe(symbols: string[]): Promise<void> {
    if (symbols.length === 0) return;
    await directPost<{ status?: string; requestId?: string }>(
      this.endpoint,
      "/quant/api/trading-request",
      {
        method: "unsubscribe_quote",
        params: { stock_codes: symbols.map((s) => this.toSourceSymbol(s)) },
      },
      10_000,
    );
  }

  toInternalSymbol(symbol: string): string {
    return fromQmtSymbol(symbol);
  }

  toSourceSymbol(symbol: string): string {
    return toQmtSymbol(symbol);
  }

  getStatus(): TickSourceStatus {
    return {
      kind: this.kind,
      running: this.running,
      endpoint: this.endpoint,
    };
  }

  getSeq(): number {
    return this.afterSeq;
  }
}

export function createDefaultTickSourceAdapter(
  config: QuantTradingConfig,
  pollIntervalMs: number,
): TickSourceAdapter | null {
  if (config.tickData?.enabled === false) return null;

  const configuredBridgeUrl = config.tradingAccount?.qmt?.windowsBridgeUrl ?? "";
  // A receiver-only QMT setup has no direct Windows URL. Prefer the local
  // RECEIVER proxy when it is configured, while preserving the direct bridge
  // default for existing configurations.
  const source =
    config.tickData?.source ??
    (configuredBridgeUrl || !config.receiver ? "qmt-bridge" : "receiver-proxy");
  if (source === "receiver-proxy") {
    if (config.receiver?.enabled === false) return null;
    const receiverPort = config.receiver?.port ?? 18889;
    const endpoint = `http://127.0.0.1:${receiverPort}`;
    let initialSeq = 0;
    // Attempt to recover afterSeq from persistent state
    if (config.dataDir) {
      try {
        const { readFileSync } = require("fs");
        const { join } = require("path");
        const seqFile = join(config.dataDir, "tick-seq.json");
        const content = readFileSync(seqFile, "utf8");
        const data = JSON.parse(content) as { afterSeq?: number };
        if (
          typeof data.afterSeq === "number" &&
          Number.isFinite(data.afterSeq) &&
          data.afterSeq > 0
        ) {
          initialSeq = data.afterSeq;
        }
      } catch {
        // No persistent seq; start from 0
      }
    }
    return new ReceiverProxyTickSource(endpoint, pollIntervalMs, initialSeq);
  }

  const bridgeUrl = configuredBridgeUrl;
  if (!bridgeUrl) return null;
  return new QmtBridgeTickSource(bridgeUrl, pollIntervalMs);
}
