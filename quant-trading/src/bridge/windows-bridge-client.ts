import type { TradingEvent } from "../http/trading-bridge-api.js";

export type BridgeEventResponse = {
  events?: Array<{ seq?: number; event: string; data: unknown; receivedAt?: number }>;
  count?: number;
  latestSeq?: number;
};

/** POST JSON to the Windows bridge and return parsed response. */
export async function directPost<T = unknown>(
  baseUrl: string,
  path: string,
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<T> {
  const url = `${baseUrl.replace(/\/$/, "")}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** GET JSON from the Windows bridge. */
export async function directGet<T = unknown>(
  baseUrl: string,
  path: string,
  timeoutMs: number,
  query?: Record<string, string | number | undefined>,
): Promise<T> {
  const url = new URL(`${baseUrl.replace(/\/$/, "")}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value == null) continue;
    url.searchParams.set(key, String(value));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export class WindowsBridgeEventPoller {
  private timer: ReturnType<typeof setInterval> | null = null;
  private afterSeq = 0;

  constructor(
    private readonly bridgeUrl: string,
    private readonly onEvent: (event: TradingEvent) => void,
    private readonly pollIntervalMs: number,
    private readonly timeoutMs = 5_000,
  ) {}

  start(): void {
    if (this.timer) return;
    const poll = async () => {
      try {
        const resp = await directGet<BridgeEventResponse>(
          this.bridgeUrl,
          "/trading/events",
          this.timeoutMs,
          this.afterSeq > 0 ? { afterSeq: this.afterSeq } : undefined,
        );
        for (const raw of resp.events ?? []) {
          this.onEvent({
            seq: raw.seq ?? 0,
            event: raw.event,
            data: raw.data,
            receivedAt: raw.receivedAt ?? Date.now(),
          });
        }
        this.afterSeq = Math.max(this.afterSeq, resp.latestSeq ?? this.afterSeq);
      } catch {
        // Windows bridge offline or temporarily unavailable.
      }
    };

    this.timer = setInterval(() => void poll(), this.pollIntervalMs);
    void poll();
  }

  stop(): void {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  getAfterSeq(): number {
    return this.afterSeq;
  }
}
