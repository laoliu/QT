/**
 * LogStore — in-memory ring buffer for plugin log entries.
 *
 * Wraps the plugin logger to capture log messages for the admin
 * log-viewer UI while still forwarding to the original logger.
 */

export type LogLevel = "trace" | "debug" | "info" | "warn" | "error" | "fatal";

export interface LogEntry {
  id: number;
  level: LogLevel;
  message: string;
  timestamp: string;
  /** Optional source/module tag extracted from `[tag]` prefix */
  source?: string;
}

export class LogStore {
  private nextId = 1;
  private entries: LogEntry[] = [];
  private readonly maxEntries: number;

  constructor(opts?: { maxEntries?: number }) {
    this.maxEntries = opts?.maxEntries ?? 2000;
  }

  /** Append a log entry. */
  push(level: LogLevel, message: string): void {
    // Extract optional [source] prefix
    let source: string | undefined;
    const match = message.match(/^\[([^\]]+)\]\s*/);
    if (match) {
      source = match[1];
    }

    this.entries.push({
      id: this.nextId++,
      level,
      message,
      timestamp: new Date().toISOString(),
      source,
    });

    if (this.entries.length > this.maxEntries) {
      this.entries.splice(0, this.entries.length - this.maxEntries);
    }
  }

  /** Query recent log entries with optional filters. */
  query(opts?: {
    level?: LogLevel;
    source?: string;
    search?: string;
    limit?: number;
    since?: string;
  }): LogEntry[] {
    let results = this.entries;

    if (opts?.level) {
      const minLevel = LEVEL_ORDER[opts.level] ?? 0;
      results = results.filter((e) => (LEVEL_ORDER[e.level] ?? 0) >= minLevel);
    }

    if (opts?.source) {
      const src = opts.source.toLowerCase();
      results = results.filter((e) => e.source?.toLowerCase().includes(src));
    }

    if (opts?.search) {
      const q = opts.search.toLowerCase();
      results = results.filter((e) => e.message.toLowerCase().includes(q));
    }

    if (opts?.since) {
      results = results.filter((e) => e.timestamp >= opts.since!);
    }

    const limit = opts?.limit ?? 200;
    return results.slice(-limit);
  }

  /** Get summary counts by level. */
  getSummary(): Record<LogLevel, number> {
    const counts: Record<LogLevel, number> = {
      trace: 0,
      debug: 0,
      info: 0,
      warn: 0,
      error: 0,
      fatal: 0,
    };
    for (const e of this.entries) {
      counts[e.level] = (counts[e.level] ?? 0) + 1;
    }
    return counts;
  }

  /** Clear all entries. */
  clear(): void {
    this.entries = [];
  }

  get size(): number {
    return this.entries.length;
  }
}

const LEVEL_ORDER: Record<string, number> = {
  trace: 0,
  debug: 1,
  info: 2,
  warn: 3,
  error: 4,
  fatal: 5,
};

/**
 * Wrap a plugin logger to intercept all log calls and push them to a LogStore,
 * while still forwarding to the original logger.
 */
export function wrapLoggerWithStore<
  T extends {
    info: (msg: string) => void;
    warn: (msg: string) => void;
    error: (msg: string) => void;
    debug?: (msg: string) => void;
  },
>(logger: T, store: LogStore): T {
  return new Proxy(logger, {
    get(target, prop, receiver) {
      const orig = Reflect.get(target, prop, receiver);
      if (typeof orig !== "function") return orig;
      if (prop === "info" || prop === "warn" || prop === "error" || prop === "debug") {
        return (msg: string, ...args: unknown[]) => {
          store.push(prop as LogLevel, msg);
          return (orig as Function).call(target, msg, ...args);
        };
      }
      return orig;
    },
  });
}
