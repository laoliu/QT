/**
 * TaskScheduler — simple in-memory scheduler for delayed/periodic tasks.
 *
 * Supports:
 * - One-shot delayed tasks (e.g., "retry in 15 minutes")
 * - Periodic recurring tasks (e.g., "every 30 minutes")
 * - Cron-based tasks (e.g., "weekdays at 07:00")
 * - Persistence: serializable task metadata saved/loaded from disk
 *
 * Results are delivered via api.notify() to the user's active chat channel.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import {
  type CronExpression,
  cronMatches,
  describeCron,
  nextCronOccurrence,
  parseCron,
} from "./cron-parser.js";

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

/** Serializable metadata for a cron task. */
export interface CronTaskMeta {
  /** Cron expression (5-field). */
  cronExpr: string;
  /** Report template ID (for replay). */
  reportId?: string;
  /** Tool name to execute (for replay). */
  toolName?: string;
  /** Tool arguments (for replay). */
  toolArgs?: Record<string, unknown>;
}

export interface CronTask {
  id: string;
  /** Human-readable description. */
  description: string;
  /** Cron expression (5-field). */
  cronExpr: string;
  /** Parsed cron expression for matching. */
  cron: CronExpression;
  /** Function to execute — returns a message string to send to the user. */
  run: () => Promise<string>;
  /** Whether the task is active. */
  enabled: boolean;
  /** Last execution time (ISO). */
  lastRunAt?: string;
  /** Last error message, if any. */
  lastError?: string;
  /** Created timestamp. */
  createdAt: string;
  /** Next scheduled fire time (ISO). */
  nextFireAt?: string;
  /** Serializable metadata for persistence. */
  meta: CronTaskMeta;
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

  // ── Cron state ────────────────────────────────────────────────
  private cronTasks = new Map<string, CronTask>();
  private cronIdCounter = 0;
  private cronCheckTimer: ReturnType<typeof setInterval> | null = null;
  /** Factory to rebuild cron `run` functions from persisted CronTaskMeta. Set via `setCronRunFactory`. */
  private cronRunFactory?: (meta: CronTaskMeta) => (() => Promise<string>) | null;

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
    // Clean up cron
    if (this.cronCheckTimer) {
      clearInterval(this.cronCheckTimer);
      this.cronCheckTimer = null;
    }
    this.saveCronToDisk();
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

  // ── Cron scheduling ─────────────────────────────────────────────

  /** Set a factory that can rebuild cron `run` functions from persisted CronTaskMeta.
   *  Must be called before `loadCronFromDisk()`. */
  setCronRunFactory(fn: (meta: CronTaskMeta) => (() => Promise<string>) | null): void {
    this.cronRunFactory = fn;
  }

  /**
   * Schedule a cron-based task.
   * @param description Human-readable description.
   * @param cronExpr 5-field cron expression (minute hour dom month dow).
   * @param run Function to execute at each cron match.
   * @param meta Serializable metadata for persistence.
   * @returns The cron task ID.
   */
  scheduleCron(
    description: string,
    cronExpr: string,
    run: () => Promise<string>,
    meta: CronTaskMeta,
  ): string {
    const cron = parseCron(cronExpr);
    const id = `cron-${String(++this.cronIdCounter).padStart(4, "0")}`;
    const now = new Date();
    const nextFire = nextCronOccurrence(cron, now);

    const task: CronTask = {
      id,
      description,
      cronExpr,
      cron,
      run,
      enabled: true,
      createdAt: now.toISOString(),
      nextFireAt: nextFire?.toISOString(),
      meta,
    };

    this.cronTasks.set(id, task);
    this.ensureCronChecker();
    this.saveCronToDisk();

    return id;
  }

  /** Cancel/delete a cron task by ID. */
  cancelCron(id: string): boolean {
    const task = this.cronTasks.get(id);
    if (!task) return false;
    this.cronTasks.delete(id);
    this.saveCronToDisk();
    return true;
  }

  /** List all cron tasks. */
  listCron(): CronTask[] {
    return [...this.cronTasks.values()];
  }

  /** Get a specific cron task by ID. */
  getCron(id: string): CronTask | undefined {
    return this.cronTasks.get(id);
  }

  /** Load persisted cron tasks from disk. Call after setCronRunFactory + setNotify. */
  loadCronFromDisk(): void {
    const path = this.cronPersistPath;
    if (!path) return;
    try {
      if (!existsSync(path)) return;
      const raw = readFileSync(path, "utf-8");
      const data = JSON.parse(raw) as {
        idCounter: number;
        tasks: Array<{
          id: string;
          description: string;
          cronExpr: string;
          enabled: boolean;
          createdAt: string;
          lastRunAt?: string;
          meta: CronTaskMeta;
        }>;
      };
      this.cronIdCounter = data.idCounter ?? 0;
      for (const t of data.tasks) {
        if (!t.enabled || !t.meta) continue;
        const run = this.cronRunFactory?.(t.meta);
        if (!run) {
          console.warn(
            `[TaskScheduler] skipping persisted cron task "${t.description}" (id=${t.id}): run factory returned null`,
          );
          continue;
        }

        let cron: CronExpression;
        try {
          cron = parseCron(t.cronExpr);
        } catch {
          console.warn(
            `[TaskScheduler] skipping cron task "${t.description}" (id=${t.id}): invalid cron expression "${t.cronExpr}"`,
          );
          continue;
        }

        const nextFire = nextCronOccurrence(cron, new Date());

        const task: CronTask = {
          id: t.id,
          description: t.description,
          cronExpr: t.cronExpr,
          cron,
          run,
          enabled: true,
          createdAt: t.createdAt,
          lastRunAt: t.lastRunAt,
          nextFireAt: nextFire?.toISOString(),
          meta: t.meta,
        };
        this.cronTasks.set(t.id, task);
      }
      if (this.cronTasks.size > 0) {
        this.ensureCronChecker();
      }
    } catch {
      // Corrupt file — start fresh
    }
  }

  // ── Cron persistence ────────────────────────────────────────────

  private get cronPersistPath(): string | null {
    if (!this.dataDir) return null;
    return `${this.dataDir}/cron-tasks.json`;
  }

  private saveCronToDisk(): void {
    const path = this.cronPersistPath;
    if (!path) return;
    try {
      const dir = dirname(path);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const tasks = [...this.cronTasks.values()].map((t) => ({
        id: t.id,
        description: t.description,
        cronExpr: t.cronExpr,
        enabled: t.enabled,
        createdAt: t.createdAt,
        lastRunAt: t.lastRunAt,
        meta: t.meta,
      }));
      writeFileSync(
        path,
        JSON.stringify({ idCounter: this.cronIdCounter, tasks }, null, 2),
        "utf-8",
      );
    } catch {
      // Best-effort persistence
    }
  }

  // ── Cron internal ───────────────────────────────────────────────

  /** Start the cron checker interval if not already running. Checks every 30s. */
  private ensureCronChecker(): void {
    if (this.cronCheckTimer) return;
    this.cronCheckTimer = setInterval(() => {
      void this.checkCronTasks();
    }, 30_000); // Check every 30 seconds
  }

  /** Check all cron tasks and fire any that match the current minute. */
  private async checkCronTasks(): Promise<void> {
    const now = new Date();
    // Truncate to current minute for matching
    const matchDate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes());

    for (const task of this.cronTasks.values()) {
      if (!task.enabled) continue;

      // Skip if already ran this minute
      if (task.lastRunAt) {
        const lastRun = new Date(task.lastRunAt);
        if (
          lastRun.getFullYear() === matchDate.getFullYear() &&
          lastRun.getMonth() === matchDate.getMonth() &&
          lastRun.getDate() === matchDate.getDate() &&
          lastRun.getHours() === matchDate.getHours() &&
          lastRun.getMinutes() === matchDate.getMinutes()
        ) {
          continue;
        }
      }

      if (!cronMatches(task.cron, matchDate)) continue;

      // Fire the task
      task.lastRunAt = now.toISOString();
      const nextFire = nextCronOccurrence(task.cron, now);
      task.nextFireAt = nextFire?.toISOString();

      try {
        const result = await task.run();
        task.lastError = undefined;
        if (this.notifyFn && result) {
          const header = `🕐 定时报告: ${task.description}`;
          await this.notifyFn(`${header}\n\n${result}`);
        }
      } catch (err) {
        task.lastError = err instanceof Error ? err.message : String(err);
        if (this.notifyFn) {
          await this.notifyFn(`❌ 定时报告失败: ${task.description}\n错误: ${task.lastError}`);
        }
      }

      this.saveCronToDisk();
    }
  }
}
