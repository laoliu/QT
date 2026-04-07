/**
 * Tests for TaskScheduler service.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskScheduler } from "./task-scheduler.js";

describe("TaskScheduler", () => {
  let scheduler: TaskScheduler;

  beforeEach(() => {
    vi.useFakeTimers();
    scheduler = new TaskScheduler({ maxConcurrent: 2, maxTasks: 10 });
  });

  afterEach(() => {
    scheduler.destroy();
    vi.useRealTimers();
  });

  it("should schedule and execute a delayed task", async () => {
    const run = vi.fn().mockResolvedValue("done");
    scheduler.scheduleDelay("test task", 5000, run);

    expect(scheduler.pendingCount).toBe(1);
    expect(run).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(5000);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("should deliver result via notify callback", async () => {
    const notify = vi.fn();
    scheduler.setNotify(notify);

    scheduler.scheduleDelay("report", 1000, async () => "晨报内容");

    await vi.advanceTimersByTimeAsync(1000);

    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0]![0]).toContain("report");
    expect(notify.mock.calls[0]![0]).toContain("晨报内容");
  });

  it("should cancel a pending task", () => {
    const id = scheduler.scheduleDelay("to cancel", 5000, async () => "x");
    expect(scheduler.cancel(id)).toBe(true);
    expect(scheduler.get(id)?.status).toBe("cancelled");
    expect(scheduler.pendingCount).toBe(0);
  });

  it("should not cancel completed tasks", async () => {
    const id = scheduler.scheduleDelay("quick", 100, async () => "ok");
    await vi.advanceTimersByTimeAsync(100);
    expect(scheduler.cancel(id)).toBe(false);
  });

  it("should list tasks by status", () => {
    scheduler.scheduleDelay("a", 1000, async () => "a");
    scheduler.scheduleDelay("b", 2000, async () => "b");
    const id = scheduler.scheduleDelay("c", 3000, async () => "c");
    scheduler.cancel(id);

    expect(scheduler.list("pending").length).toBe(2);
    expect(scheduler.list("cancelled").length).toBe(1);
    expect(scheduler.list().length).toBe(3);
  });

  it("should handle recurring tasks", async () => {
    const run = vi.fn().mockResolvedValue("tick");
    scheduler.scheduleDelay("recurring", 1000, run, { intervalMs: 1000 });

    await vi.advanceTimersByTimeAsync(1000);
    expect(run).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(run).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1000);
    expect(run).toHaveBeenCalledTimes(3);
  });

  it("should handle task failure", async () => {
    const notify = vi.fn();
    scheduler.setNotify(notify);

    scheduler.scheduleDelay("fail", 500, async () => {
      throw new Error("boom");
    });

    await vi.advanceTimersByTimeAsync(500);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0]![0]).toContain("失败");
    expect(notify.mock.calls[0]![0]).toContain("boom");
  });

  it("should respect maxTasks by evicting completed tasks", async () => {
    const scheduler2 = new TaskScheduler({ maxTasks: 3 });
    vi.useFakeTimers();

    scheduler2.scheduleDelay("1", 100, async () => "a");
    scheduler2.scheduleDelay("2", 100, async () => "b");
    await vi.advanceTimersByTimeAsync(100);

    // 2 completed + this new one should fit
    scheduler2.scheduleDelay("3", 100, async () => "c");
    scheduler2.scheduleDelay("4", 100, async () => "d");
    // Should not throw, old completed tasks evicted
    expect(scheduler2.list().length).toBeLessThanOrEqual(3);

    scheduler2.destroy();
  });

  it("should respect concurrency limit", async () => {
    let concurrentCount = 0;
    let maxConcurrent = 0;

    const run = async () => {
      concurrentCount++;
      maxConcurrent = Math.max(maxConcurrent, concurrentCount);
      await new Promise((r) => setTimeout(r, 200));
      concurrentCount--;
      return "ok";
    };

    // Schedule 3 tasks with maxConcurrent=2
    scheduler.scheduleDelay("a", 100, run);
    scheduler.scheduleDelay("b", 100, run);
    scheduler.scheduleDelay("c", 100, run);

    await vi.advanceTimersByTimeAsync(100);
    // First 2 should start, 3rd should be deferred
    // Note: with fake timers the inner setTimeout in run() also needs advancing
    await vi.advanceTimersByTimeAsync(200);
    // After the first 2 complete, the 3rd should run
    await vi.advanceTimersByTimeAsync(10_000);
    // 3rd task re-scheduled after 10s delay
    await vi.advanceTimersByTimeAsync(200);

    // All should eventually complete
    expect(maxConcurrent).toBeLessThanOrEqual(2);
  });
});

describe("TaskScheduler — Cron", () => {
  let scheduler: TaskScheduler;

  beforeEach(() => {
    vi.useFakeTimers();
    scheduler = new TaskScheduler({ maxConcurrent: 2, maxTasks: 10 });
  });

  afterEach(() => {
    scheduler.destroy();
    vi.useRealTimers();
  });

  it("should schedule a cron task", () => {
    const run = vi.fn().mockResolvedValue("report");
    const id = scheduler.scheduleCron("test cron", "0 7 * * 1-5", run, {
      cronExpr: "0 7 * * 1-5",
      reportId: "morning_brief",
    });

    expect(id).toMatch(/^cron-/);
    expect(scheduler.listCron()).toHaveLength(1);
    expect(scheduler.getCron(id)?.description).toBe("test cron");
    expect(scheduler.getCron(id)?.enabled).toBe(true);
    expect(scheduler.getCron(id)?.nextFireAt).toBeDefined();
  });

  it("should cancel a cron task", () => {
    const id = scheduler.scheduleCron("to delete", "0 7 * * 1-5", async () => "x", {
      cronExpr: "0 7 * * 1-5",
    });
    expect(scheduler.cancelCron(id)).toBe(true);
    expect(scheduler.listCron()).toHaveLength(0);
    expect(scheduler.cancelCron("nonexistent")).toBe(false);
  });

  it("should fire cron task when time matches", async () => {
    const notify = vi.fn();
    scheduler.setNotify(notify);

    // Set current time to Monday 2026-04-06 06:59:30
    vi.setSystemTime(new Date(2026, 3, 6, 6, 59, 30));

    const run = vi.fn().mockResolvedValue("晨报内容");
    scheduler.scheduleCron("晨间简报", "0 7 * * 1-5", run, {
      cronExpr: "0 7 * * 1-5",
      reportId: "morning_brief",
    });

    // Advance 30 seconds to 07:00:00 — the cron checker fires every 30s
    await vi.advanceTimersByTimeAsync(30_000);

    expect(run).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0]![0]).toContain("晨间简报");
    expect(notify.mock.calls[0]![0]).toContain("晨报内容");
  });

  it("should not fire cron task on non-matching time", async () => {
    // Set current time to Saturday 2026-04-04 06:59:30
    vi.setSystemTime(new Date(2026, 3, 4, 6, 59, 30));

    const run = vi.fn().mockResolvedValue("report");
    scheduler.scheduleCron("weekday only", "0 7 * * 1-5", run, {
      cronExpr: "0 7 * * 1-5",
    });

    // Advance past 07:00
    await vi.advanceTimersByTimeAsync(60_000);

    expect(run).not.toHaveBeenCalled();
  });

  it("should not double-fire within the same minute", async () => {
    vi.setSystemTime(new Date(2026, 3, 6, 6, 59, 30));

    const run = vi.fn().mockResolvedValue("report");
    scheduler.scheduleCron("test", "0 7 * * 1-5", run, {
      cronExpr: "0 7 * * 1-5",
    });

    // Fire at 07:00
    await vi.advanceTimersByTimeAsync(30_000);
    expect(run).toHaveBeenCalledTimes(1);

    // Another 30s tick — still minute 0, should NOT fire again
    await vi.advanceTimersByTimeAsync(30_000);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("should handle cron task failure gracefully", async () => {
    const notify = vi.fn();
    scheduler.setNotify(notify);

    vi.setSystemTime(new Date(2026, 3, 6, 6, 59, 30));

    const run = vi.fn().mockRejectedValue(new Error("network error"));
    scheduler.scheduleCron("failing task", "0 7 * * 1-5", run, {
      cronExpr: "0 7 * * 1-5",
    });

    await vi.advanceTimersByTimeAsync(30_000);

    expect(run).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0]![0]).toContain("失败");
    expect(notify.mock.calls[0]![0]).toContain("network error");

    const task = scheduler.listCron()[0]!;
    expect(task.lastError).toBe("network error");
  });

  it("should throw on invalid cron expression", () => {
    expect(() =>
      scheduler.scheduleCron("bad", "invalid", async () => "x", { cronExpr: "invalid" }),
    ).toThrow();
  });
});
