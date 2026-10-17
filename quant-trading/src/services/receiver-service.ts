/**
 * ReceiverService — manages the RECEIVER.py child process lifecycle.
 *
 * RECEIVER.py is the Mac-side HTTP server that bridges data requests between
 * the Mac (running OpenClaw) and the Windows QMT machine. It listens on a
 * configurable port (default 18889) and provides endpoints for the Windows
 * BRIDGE.py to poll pending requests and push results.
 *
 * This service:
 *  - Spawns RECEIVER.py on start (skips if the port is already occupied)
 *  - Auto-restarts on unexpected exit with exponential back-off
 *  - Cleanly kills the process on stop
 */
import { spawn, execSync, type ChildProcess } from "node:child_process";
import { createConnection } from "node:net";
import path from "node:path";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { getStrategiesDir } from "../bridge/python-dir.js";
import type { QuantTradingConfig } from "../types.js";

/** Max restart attempts before giving up */
const MAX_RESTARTS = 10;
/** Back-off delays in ms */
const BACKOFF = [1_000, 2_000, 5_000, 10_000, 30_000];

export interface ReceiverStatus {
  running: boolean;
  pid: number | null;
  port: number;
  host: string;
  restartCount: number;
  startedAt: string | null;
}

export class ReceiverService implements OpenClawPluginService {
  readonly id = "qmt-receiver";

  private child: ChildProcess | null = null;
  private stopping = false;
  private restartCount = 0;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private startedAt: Date | null = null;

  private readonly host: string;
  private readonly port: number;
  private readonly pythonPath: string;
  private readonly scriptDir: string;

  constructor(private readonly config: QuantTradingConfig) {
    this.host = config.receiver?.host ?? "0.0.0.0";
    this.port = config.receiver?.port ?? 18889;
    this.pythonPath = config.pythonPath ?? "python3";
    this.scriptDir = getStrategiesDir();
  }

  async start(): Promise<void> {
    this.stopping = false;
    this.restartCount = 0;

    // Kill any orphaned RECEIVER process occupying the port
    const inUse = await this.isPortInUse();
    if (inUse) {
      console.log(`[ReceiverService] Port ${this.port} occupied — killing orphaned process…`);
      await this.killOrphanOnPort();
      // Wait briefly for port to free up
      await new Promise((r) => setTimeout(r, 1_000));
      const stillInUse = await this.isPortInUse();
      if (stillInUse) {
        console.error(`[ReceiverService] Port ${this.port} still in use after kill — cannot start`);
        return;
      }
    }

    this.spawn();
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    if (this.child) {
      this.child.kill("SIGTERM");
      // Give it a moment to exit gracefully
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          if (this.child && !this.child.killed) {
            this.child.kill("SIGKILL");
          }
          resolve();
        }, 3_000);
        this.child!.on("close", () => {
          clearTimeout(timer);
          resolve();
        });
      });
      this.child = null;
    }
    // Also kill any orphaned processes on our port
    await this.killOrphanOnPort();
  }

  /** Get current service status. */
  getStatus(): ReceiverStatus {
    return {
      running: this.child !== null && !this.child.killed,
      pid: this.child?.pid ?? null,
      port: this.port,
      host: this.host,
      restartCount: this.restartCount,
      startedAt: this.startedAt?.toISOString() ?? null,
    };
  }

  /** Stop then start — useful for admin restart. */
  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  /** Kill any orphaned RECEIVER.py process listening on our port. */
  private async killOrphanOnPort(): Promise<void> {
    try {
      // Use lsof to find PIDs listening on the port
      const out = execSync(`lsof -ti :${this.port} 2>/dev/null`, {
        encoding: "utf-8",
        timeout: 5_000,
      }).trim();
      if (!out) return;
      const selfPid = String(process.pid);
      const pids = out
        .split("\n")
        .map((p) => p.trim())
        .filter(Boolean);
      for (const pid of pids) {
        // Don't kill our own managed child or the gateway process itself
        if (pid === selfPid) continue;
        if (this.child?.pid && String(this.child.pid) === pid) continue;
        console.log(`[ReceiverService] Killing orphaned process PID=${pid} on port ${this.port}`);
        try {
          process.kill(Number(pid), "SIGTERM");
        } catch {
          // Process may have already exited
        }
      }
      // Give processes a moment to die, then force-kill survivors
      await new Promise((r) => setTimeout(r, 500));
      for (const pid of pids) {
        if (pid === selfPid) continue;
        if (this.child?.pid && String(this.child.pid) === pid) continue;
        try {
          process.kill(Number(pid), 0); // check if still alive
          process.kill(Number(pid), "SIGKILL");
          console.log(`[ReceiverService] Force-killed PID=${pid}`);
        } catch {
          // Already gone
        }
      }
    } catch {
      // lsof not available or no matching processes
    }
  }

  /** Check if target port is already occupied. */
  private isPortInUse(): Promise<boolean> {
    return new Promise((resolve) => {
      const sock = createConnection({ host: "127.0.0.1", port: this.port });
      sock.setTimeout(1_000);
      sock.on("connect", () => {
        sock.destroy();
        resolve(true);
      });
      sock.on("error", () => {
        sock.destroy();
        resolve(false);
      });
      sock.on("timeout", () => {
        sock.destroy();
        resolve(false);
      });
    });
  }

  private spawn(): void {
    const args = [
      path.join(this.scriptDir, "RECEIVER.py"),
      "--host",
      this.host,
      "--port",
      String(this.port),
    ];

    console.log(`[ReceiverService] Starting RECEIVER.py on ${this.host}:${this.port}`);
    this.startedAt = new Date();

    const child = spawn(this.pythonPath, args, {
      stdio: ["ignore", "pipe", "pipe"],
      cwd: this.scriptDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
    });
    this.child = child;

    child.stdout!.on("data", (chunk: Buffer) => {
      const msg = chunk.toString("utf-8").trim();
      if (msg) console.log(`[RECEIVER.py] ${msg}`);
    });

    child.stderr!.on("data", (chunk: Buffer) => {
      const msg = chunk.toString("utf-8").trim();
      if (msg) console.warn(`[RECEIVER.py] ${msg}`);
    });

    child.on("close", (code) => {
      this.child = null;
      if (this.stopping) {
        console.log("[ReceiverService] RECEIVER.py stopped");
        return;
      }
      console.warn(`[ReceiverService] RECEIVER.py exited (code=${code}); scheduling restart…`);
      this.scheduleRestart();
    });

    child.on("error", (err) => {
      console.error(`[ReceiverService] Failed to spawn RECEIVER.py: ${err.message}`);
      this.scheduleRestart();
    });
  }

  private scheduleRestart(): void {
    if (this.stopping) return;
    if (this.restartCount >= MAX_RESTARTS) {
      console.error(`[ReceiverService] RECEIVER.py exceeded ${MAX_RESTARTS} restarts; giving up`);
      return;
    }
    const delay = BACKOFF[Math.min(this.restartCount, BACKOFF.length - 1)]!;
    this.restartCount++;
    console.log(
      `[ReceiverService] Restarting RECEIVER.py in ${delay}ms (attempt ${this.restartCount}/${MAX_RESTARTS})`,
    );
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      this.spawn();
    }, delay);
  }
}
