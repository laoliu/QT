/**
 * PersistentPythonProcess — long-running Python child process with NDJSON
 * request/response multiplexing over stdin / stdout.
 *
 * Protocol:
 *   → stdin:  one JSON line per request  { "requestId": "...", "method": "...", "params": {...} }
 *   ← stdout: one JSON line per response { "requestId": "...", "ok": true|false, "data": ..., "error": "..." }
 *   ← stdout: push events (no requestId) { "event": "fill"|"quote"|"order_status", "data": {...} }
 *
 * The process is auto-restarted on crash with exponential back-off.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { createInterface } from "node:readline";
import type { QuantTradingConfig } from "../types.js";
import { getPythonDir } from "./python-dir.js";

export type PythonMessage = {
  requestId?: string;
  event?: string;
  ok?: boolean;
  data?: unknown;
  error?: string;
};

export type EventHandler = (event: string, data: unknown) => void;

type PendingCall = {
  resolve: (data: unknown) => void;
  reject: (err: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
};

/** Default call timeout: 30 s */
const DEFAULT_TIMEOUT_MS = 30_000;
/** Startup timeout: 30 s (time for Python to emit "ready" event) */
const STARTUP_TIMEOUT_MS = 30_000;
/** Max restart attempts before giving up */
const MAX_RESTARTS = 10;
/** Back-off delays in ms */
const BACKOFF = [1_000, 2_000, 5_000, 10_000, 30_000];

export class PersistentPythonProcess {
  private child: ChildProcess | null = null;
  private pending: Map<string, PendingCall> = new Map();
  private eventHandlers: EventHandler[] = [];
  private restartCount = 0;
  private stopping = false;
  private readyPromise: Promise<void> | null = null;
  private readyResolve: (() => void) | null = null;
  private readyReject: ((err: Error) => void) | null = null;
  private startupTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly module: string, // Python module path, e.g. "brokers.qmt_broker"
    private readonly config: QuantTradingConfig,
    private readonly initArgs?: Record<string, unknown>,
  ) {}

  /** Start the process. Idempotent. */
  async start(): Promise<void> {
    if (this.child && !this.child.killed) return;
    this.stopping = false;
    this.readyPromise = new Promise((res, rej) => {
      this.readyResolve = res;
      this.readyReject = rej;
    });
    this.spawn();
    // Start a timer so start() doesn't hang forever if Python never emits "ready"
    this.startupTimer = setTimeout(() => {
      this.startupTimer = null;
      if (this.readyReject) {
        this.readyReject(
          new Error(
            `Python process '${this.module}' failed to start within ${STARTUP_TIMEOUT_MS}ms`,
          ),
        );
        this.readyReject = null;
        this.readyResolve = null;
      }
    }, STARTUP_TIMEOUT_MS);
    return this.readyPromise;
  }

  /** Stop the process and do not restart. */
  stop(): void {
    this.stopping = true;
    if (this.startupTimer) {
      clearTimeout(this.startupTimer);
      this.startupTimer = null;
    }
    if (this.readyReject) {
      this.readyReject(new Error("Python process stopped"));
      this.readyReject = null;
      this.readyResolve = null;
    }
    if (this.child) {
      this.child.stdin?.end();
      this.child.kill("SIGTERM");
      this.child = null;
    }
    // Reject all pending calls
    for (const [id, p] of this.pending) {
      clearTimeout(p.timeout);
      p.reject(new Error("Python process stopped"));
      this.pending.delete(id);
    }
  }

  /**
   * Send a request and wait for the matching response.
   * Rejects if the process is not started, or if the call times out.
   */
  async call<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  ): Promise<T> {
    if (!this.child || this.child.killed) {
      await this.start();
    }
    const requestId = randomUUID();
    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(requestId);
        reject(new Error(`Python call '${method}' timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      this.pending.set(requestId, {
        resolve: (d) => resolve(d as T),
        reject,
        timeout,
      });

      const line = JSON.stringify({ requestId, method, params: params ?? {} }) + "\n";
      this.child!.stdin!.write(line);
    });
  }

  /** Register a handler for push events (fills, quotes, order status). */
  onEvent(handler: EventHandler): () => void {
    this.eventHandlers.push(handler);
    return () => {
      this.eventHandlers = this.eventHandlers.filter((h) => h !== handler);
    };
  }

  // ── Private ──────────────────────────────────────────────────

  private spawn(): void {
    const pythonPath = this.config.pythonPath ?? "python3";
    const scriptDir = getPythonDir();

    const child = spawn(pythonPath, ["-m", this.module, "--persistent", "--json"], {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: scriptDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        QUANT_CONFIG: JSON.stringify(this.config),
        QUANT_INIT_ARGS: JSON.stringify(this.initArgs ?? {}),
      },
    });
    this.child = child;

    const rl = createInterface({ input: child.stdout! });
    rl.on("line", (line) => this.handleLine(line));

    child.stderr!.on("data", (chunk: Buffer) => {
      // Log Python stderr as warnings; don't crash
      const msg = chunk.toString("utf-8").trim();
      if (msg) console.warn(`[PersistentPython:${this.module}] ${msg}`);
    });

    child.on("close", (code) => {
      this.child = null;
      // If the process exited before emitting "ready", reject the start() promise
      if (this.readyReject) {
        this.readyReject(
          new Error(
            `Python process '${this.module}' exited with code ${code} before becoming ready`,
          ),
        );
        this.readyReject = null;
        this.readyResolve = null;
      }
      if (this.stopping) return;
      console.warn(`[PersistentPython:${this.module}] process exited (code=${code}); restarting…`);
      this.scheduleRestart();
    });

    child.on("error", (err) => {
      console.error(`[PersistentPython:${this.module}] spawn error: ${err.message}`);
      this.scheduleRestart();
    });

    // Send init message once process is up; it will reply with { "event": "ready" }
    const initLine =
      JSON.stringify({ requestId: "__init__", method: "__init__", params: this.initArgs ?? {} }) +
      "\n";
    child.stdin!.write(initLine);
  }

  private handleLine(line: string): void {
    if (!line.trim()) return;
    let msg: PythonMessage;
    try {
      msg = JSON.parse(line) as PythonMessage;
    } catch {
      return; // ignore non-JSON output
    }

    if (msg.event === "ready") {
      // Process has initialised; resolve the start() promise
      this.restartCount = 0;
      if (this.startupTimer) {
        clearTimeout(this.startupTimer);
        this.startupTimer = null;
      }
      this.readyResolve?.();
      this.readyResolve = null;
      this.readyReject = null;
      return;
    }

    if (msg.event) {
      // Push event (fill, quote, order_status, …)
      for (const h of this.eventHandlers) {
        try {
          h(msg.event, msg.data);
        } catch {
          /* ignore handler errors */
        }
      }
      return;
    }

    if (msg.requestId) {
      const pending = this.pending.get(msg.requestId);
      if (!pending) return;
      clearTimeout(pending.timeout);
      this.pending.delete(msg.requestId);
      if (msg.ok) {
        pending.resolve(msg.data);
      } else {
        pending.reject(new Error(msg.error ?? "Python call failed"));
      }
    }
  }

  private scheduleRestart(): void {
    if (this.stopping || this.restartCount >= MAX_RESTARTS) return;
    const delay = BACKOFF[Math.min(this.restartCount, BACKOFF.length - 1)];
    this.restartCount++;
    setTimeout(() => {
      if (!this.stopping) {
        this.readyPromise = new Promise((res, rej) => {
          this.readyResolve = res;
          this.readyReject = rej;
        });
        this.spawn();
      }
    }, delay);
  }
}
