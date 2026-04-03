/**
 * TaskScheduler — simple in-memory scheduler for delayed/periodic tasks.
 *
 * Supports:
 * - One-shot delayed tasks (e.g., "retry in 15 minutes")
 * - Periodic recurring tasks (e.g., "every 30 minutes")
 * - Persistence: serializable task metadata saved/loaded from disk
 *
 * Results are delivered via api.notify() to the user's active chat channel.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

/** Serializable metadata for a task. Stored to disk for persistence across restarts. */
export interface TaskMeta {
  /** Tool name to execute (for replay). */
  toolName?: string;
  /** Tool arguments (for replay). */
  toolArgs?: Record<string, unknown>;
  /** Report template ID (for replay). */
  reportId?: string;
}

export interface ScheduledTask {
  id: string;
  /** Human-readable description. */
  description: string;
  /** ISO timestamp when the task should fire. */
  scheduledAt: string;
  /** If set, repeat every N ms after the initial run. */
  intervalMs?: number;
  /** Function to execute — returns a message string to send to the user. */
  run: () => Promise<string>;
  /** Internal state. */
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  /** Last error message, if any. */
  lastError?: string;
  /** Created timestamp. */
  createdAt: string;
  /** Serializable metadata for persistence. */
  meta?: TaskMeta;
}

export interface TaskSchedulerOptions {
  /** Max concurrent running tasks. */
  maxConcurrent?: number;
  /** Max total scheduled tasks. */
  maxTasks?: number;
  /** Data directory for persistence (null = no persistence). */
  dataDir?: string | null;
}

export class TaskScheduler {
  private tasks = new Map<string, ScheduledTask>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private running = 0;
  private idCounter = 0;
  private readonly maxConcurrent: number;
  private readonly maxTasks: number;
  private readonly dataDir: string | null;
  private notifyFn?: (message: string) => void | Promise<void>;
  /** Factory to rebuild `run` functions from persisted TaskMeta. Set via `setRunFactory`. */
  private runFactory?: (meta: TaskMeta) => (() => Promise<string>) | null;

  constructor(opts: TaskSchedulerOptions = {}) {
    this.maxConcurrent = opts.maxConcurrent ?? 3;
    this.maxTasks = opts.maxTasks ?? 50;
    this.dataDir = opts.dataDir ?? null;
  }

  /** Set the notification callback (typically api.notify). */
  setNotify(fn: (message: string) => void | Promise<void>): void {
    this.notifyFn = fn;
  }

  /** Set a factory that can rebuild `run` functions from persisted TaskMeta.
   *  Must be called before `loadFromDisk()`. */
  setRunFactory(fn: (meta: TaskMeta) => (() => Promise<string>) | null): void {
    this.runFactory = fn;
  }

  /** Load persisted tasks from disk. Call after setRunFactory + setNotify. */
  loadFromDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as {
        idCounter: number;
        tasks: Array<{
          id: string;
          description: string;
          scheduledAt: string;
          intervalMs?: number;
          status: string;
          createdAt: string;
          meta?: TaskMeta;
        }>;
      };
      this.idCounter = data.idCounter ?? 0;
      const now = Date.now();
      for (const t of data.tasks) {
        // Only restore pending tasks that can be rebuilt
        if (t.status !== "pending" || !t.meta) continue;
        const run = this.runFactory?.(t.meta);
        if (!run) {
          console.warn(
            `[TaskScheduler] skipping persisted task "${t.description}" (id=${t.id}): run factory returned null`,
          );
          continue;
        }

        const fireAt = new Date(t.scheduledAt).getTime();
        const delayMs = Math.max(0, fireAt - now);

        const task: ScheduledTask = {
          id: t.id,
          description: t.description,
          scheduledAt: t.scheduledAt,
          intervalMs: t.intervalMs,
          run,
          status: "pending",
          createdAt: t.createdAt,
          meta: t.meta,
        };
        this.tasks.set(t.id, task);
        this.scheduleTimer(t.id, delayMs);
      }
    } catch {
      // Corrupt file — start fresh
    }
  }

  /**
   * Schedule a task to run after `delayMs` milliseconds.
   * Returns the task ID.
   */
  scheduleDelay(
    description: string,
    delayMs: number,
    run: () => Promise<string>,
    options?: { intervalMs?: number; meta?: TaskMeta },
  ): string {
    if (this.tasks.size >= this.maxTasks) {
      // Evict oldest completed/cancelled tasks
      for (const [id, t] of this.tasks) {
        if (t.status === "completed" || t.status === "cancelled" || t.status === "failed") {
          this.tasks.delete(id);
          if (this.tasks.size < this.maxTasks) break;
        }
      }
    }

    const id = `task-${String(++this.idCounter).padStart(4, "0")}`;
    const now = new Date();
    const task: ScheduledTask = {
      id,
      description,
      scheduledAt: new Date(now.getTime() + delayMs).toISOString(),
      intervalMs: options?.intervalMs,
      run,
      status: "pending",
      createdAt: now.toISOString(),
      meta: options?.meta,
    };

    this.tasks.set(id, task);
    this.scheduleTimer(id, delayMs);
    this.saveToDisk();

    return id;
  }

  /**
   * Schedule a task at specific time.
   */
  scheduleAt(
    description: string,
    at: Date,
    run: () => Promise<string>,
    options?: { intervalMs?: number; meta?: TaskMeta },
  ): string {
    const delayMs = Math.max(0, at.getTime() - Date.now());
    return this.scheduleDelay(description, delayMs, run, options);
  }

  /** Cancel a pending task. */
  cancel(id: string): boolean {
    const task = this.tasks.get(id);
    if (!task || task.status !== "pending") return false;

    const timer = this.timers.get(id);
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(id);
    }
    task.status = "cancelled";
    this.saveToDisk();
    return true;
  }

  /** List all tasks (optionally filtered by status). */
  list(status?: ScheduledTask["status"]): ScheduledTask[] {
    const all = [...this.tasks.values()];
    if (status) return all.filter((t) => t.status === status);
    return all;
  }

  /** Get a specific task by ID. */
  get(id: string): ScheduledTask | undefined {
    return this.tasks.get(id);
  }

  /** Number of pending tasks. */
  get pendingCount(): number {
    return [...this.tasks.values()].filter((t) => t.status === "pending").length;
  }

  /** Clean up all timers. Call on shutdown. */
  destroy(): void {
    for (const timer of this.timers.values()) {
      clearTimeout(timer);
    }
    this.timers.clear();
    this.saveToDisk();
  }

  // ── Persistence ─────────────────────────────────────────────

  private get persistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/scheduled-tasks.json`;
  }

  /** Persist serializable task metadata to disk. */
  private saveToDisk(): void {
    const path = this.persistPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      // Only persist pending tasks that have meta (can be rebuilt)
      const tasks = [...this.tasks.values()]
        .filter((t) => t.status === "pending" && t.meta)
        .map((t) => ({
          id: t.id,
          description: t.description,
          scheduledAt: t.scheduledAt,
          intervalMs: t.intervalMs,
          status: t.status,
          createdAt: t.createdAt,
          meta: t.meta,
        }));
      writeFileSync(path, JSON.stringify({ idCounter: this.idCounter, tasks }, null, 2), "utf-8");
    } catch {
      // Best-effort persistence
    }
  }

  // ── Internal ────────────────────────────────────────────────

  private scheduleTimer(id: string, delayMs: number): void {
    const timer = setTimeout(() => {
      this.timers.delete(id);
      void this.executeTask(id);
    }, delayMs);
    this.timers.set(id, timer);
  }

  private async executeTask(id: string): Promise<void> {
    const task = this.tasks.get(id);
    if (!task || task.status !== "pending") return;

    if (this.running >= this.maxConcurrent) {
      // Re-schedule after 10s if too many concurrent
      this.scheduleTimer(id, 10_000);
      return;
    }

    task.status = "running";
    this.running++;

    try {
      const result = await task.run();
      task.status = "completed";

      // Deliver result to user
      if (this.notifyFn && result) {
        const header = `⏰ 定时任务完成: ${task.description}`;
        await this.notifyFn(`${header}\n\n${result}`);
      }

      // If recurring, reschedule
      if (task.intervalMs && task.intervalMs > 0) {
        task.status = "pending";
        task.scheduledAt = new Date(Date.now() + task.intervalMs).toISOString();
        this.scheduleTimer(id, task.intervalMs);
      }
    } catch (err) {
      task.status = "failed";
      task.lastError = err instanceof Error ? err.message : String(err);

      if (this.notifyFn) {
        await this.notifyFn(`❌ 定时任务失败: ${task.description}\n错误: ${task.lastError}`);
      }
    } finally {
      this.running--;
    }
  }
}
