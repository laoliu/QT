/**
 * Tests for the scheduled_task AI tool.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskScheduler } from "../services/task-scheduler.js";
import { createScheduledTaskTool } from "./scheduled-task.js";

function fakeApi(): OpenClawPluginApi {
  return {
    id: "quant-trading",
    name: "quant-trading",
    source: "test",
    config: {},
    pluginConfig: {},
    runtime: { version: "test" } as never,
    logger: { info() {}, warn() {}, error() {}, debug() {} },
    registerTool() {},
    registerChannel() {},
    registerGatewayMethod() {},
    registerCli() {},
    registerService() {},
    registerProvider() {},
    registerHook() {},
    registerHttpRoute() {},
    registerCommand() {},
    on() {},
    resolvePath: (p: string) => p,
  } as unknown as OpenClawPluginApi;
}

describe("scheduled_task tool", () => {
  let scheduler: TaskScheduler;
  let tool: ReturnType<typeof createScheduledTaskTool>;
  const executeTool = vi.fn().mockResolvedValue('{"status": "ok"}');

  beforeEach(() => {
    vi.useFakeTimers();
    scheduler = new TaskScheduler();
    tool = createScheduledTaskTool(fakeApi(), {}, scheduler, executeTool);
  });

  afterEach(() => {
    scheduler.destroy();
    vi.useRealTimers();
  });

  it("should have correct name", () => {
    expect(tool.name).toBe("scheduled_task");
  });

  it("action=schedule should create a delayed task", async () => {
    const result = (await tool.execute({
      action: "schedule",
      description: "重试获取K线",
      delayMinutes: 15,
      toolName: "market_data",
      toolArgs: '{"action":"klines","symbol":"600519"}',
    })) as Record<string, unknown>;

    expect(result.success).toBe(true);
    expect(result.taskId).toBeDefined();
    expect(result.delayMinutes).toBe(15);
    expect(scheduler.pendingCount).toBe(1);
  });

  it("action=schedule should error without toolName", async () => {
    const result = (await tool.execute({
      action: "schedule",
      description: "test",
    })) as Record<string, unknown>;

    expect(result.error).toBeDefined();
  });

  it("action=list should return tasks", async () => {
    await tool.execute({
      action: "schedule",
      description: "task1",
      delayMinutes: 10,
      toolName: "market_data",
      toolArgs: '{"action":"quote","symbol":"600519"}',
    });

    const result = (await tool.execute({ action: "list" })) as {
      tasks: Array<{ id: string; description: string }>;
    };
    expect(result.tasks.length).toBe(1);
    expect(result.tasks[0].description).toBe("task1");
  });

  it("action=cancel should cancel pending task", async () => {
    const schedResult = (await tool.execute({
      action: "schedule",
      description: "to cancel",
      delayMinutes: 5,
      toolName: "market_data",
      toolArgs: "{}",
    })) as { taskId: string };

    const cancelResult = (await tool.execute({
      action: "cancel",
      taskId: schedResult.taskId,
    })) as Record<string, unknown>;

    expect(cancelResult.success).toBe(true);
    expect(scheduler.pendingCount).toBe(0);
  });

  it("scheduled task should execute the tool when timer fires", async () => {
    await tool.execute({
      action: "schedule",
      description: "delayed query",
      delayMinutes: 1,
      toolName: "market_data",
      toolArgs: '{"action":"quote","symbol":"000001"}',
    });

    await vi.advanceTimersByTimeAsync(60_000);

    expect(executeTool).toHaveBeenCalledWith("market_data", {
      action: "quote",
      symbol: "000001",
    });
  });

  it("action=schedule with intervalMinutes should set recurrence", async () => {
    const result = (await tool.execute({
      action: "schedule",
      description: "periodic check",
      delayMinutes: 5,
      toolName: "market_data",
      toolArgs: '{"action":"quote","symbol":"600519"}',
      intervalMinutes: 30,
    })) as Record<string, unknown>;

    expect(result.repeatEveryMinutes).toBe(30);
  });
});
