import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { expect, it, vi } from "vitest";
import { runPythonScript, setPythonBridgeLogger } from "./python-bridge.js";

vi.mock("node:child_process", () => ({ spawn: vi.fn() }));

it("forwards explicit Python warnings at warning level including a final partial line", async () => {
  const logger = { info: vi.fn(), warn: vi.fn() };
  setPythonBridgeLogger(logger);
  vi.mocked(spawn).mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), {
      stdout: new PassThrough(), stderr: new PassThrough(), stdin: new PassThrough(), kill: vi.fn(),
    });
    queueMicrotask(() => {
      child.stderr.write("Loaded 5497 stocks\n[warn");
      child.stderr.write("ing] Yahoo unavailable\n[warning] Cooling down");
      child.stdout.write('{"auction_signals":{}}');
      child.emit("close", 0);
    });
    return child as never;
  });
  const result = await runPythonScript("scripts/market_context_provider.py", {}, {} as never, { maxRetries: 0 });
  expect(result).toEqual({ ok: true, data: { auction_signals: {} } });
  expect(logger.info).toHaveBeenCalledTimes(1);
  expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("Loaded 5497 stocks"));
  expect(logger.warn).toHaveBeenCalledTimes(2);
  expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("Yahoo unavailable"));
  expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("Cooling down"));
});

it("keeps cache statistics responsive while research slots are full, with one cache job at a time", async () => {
  const children: Array<EventEmitter & { stdout: PassThrough }> = [];
  vi.mocked(spawn).mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), {
      stdout: new PassThrough(), stderr: new PassThrough(), stdin: new PassThrough(), kill: vi.fn(),
    });
    children.push(child);
    return child as never;
  });
  const finish = (index: number) => {
    children[index].stdout.write('{}');
    children[index].emit("close", 0);
  };
  const research = Array.from({ length: 8 }, () => runPythonScript(
    "scripts/investment_planner.py", {}, {} as never, { maxRetries: 0 },
  ));
  await vi.waitFor(() => expect(children).toHaveLength(8));
  const stats = runPythonScript("cache/stock_cache.py", { action: "refresh" }, {} as never);
  const details = runPythonScript("cache/stock_cache.py", { action: "symbols_detail" }, {} as never);
  await vi.waitFor(() => expect(children).toHaveLength(9));
  finish(8);
  expect((await stats).ok).toBe(true);
  await vi.waitFor(() => expect(children).toHaveLength(10));
  finish(9);
  expect((await details).ok).toBe(true);
  for (let i = 0; i < 8; i++) finish(i);
  expect((await Promise.all(research)).every(result => result.ok)).toBe(true);
});
