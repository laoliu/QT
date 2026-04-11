/**
 * Python bridge — spawns Python child processes for backtrader / akshare / tushare.
 *
 * Communication via JSON over stdin/stdout or a local HTTP server.
 * Includes concurrency limiting to prevent overwhelming the system.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import type { QuantTradingConfig } from "../types.js";
import { getPythonDir } from "./python-dir.js";

export type PythonBridgeResult<T = unknown> = {
  ok: boolean;
  data?: T;
  error?: string;
};

// ── Module-level Logger ──────────────────────────────────────────

let _bridgeLogger: { info: (msg: string) => void; warn: (msg: string) => void } | undefined;

/** Set a logger so that Python stderr output is forwarded to LogStore in real time. */
export function setPythonBridgeLogger(logger: { info: (msg: string) => void; warn: (msg: string) => void }): void {
  _bridgeLogger = logger;
}

// ── Concurrency Limiter ──────────────────────────────────────────

/**
 * Limits concurrent Python process spawns to avoid resource exhaustion.
 * Default max: 4 concurrent processes.
 */
class ConcurrencyLimiter {
  private running = 0;
  private queue: Array<() => void> = [];

  constructor(private maxConcurrent: number = 4) {}

  async acquire(): Promise<void> {
    if (this.running < this.maxConcurrent) {
      this.running++;
      return;
    }
    return new Promise<void>((resolve) => {
      this.queue.push(() => {
        this.running++;
        resolve();
      });
    });
  }

  release(): void {
    this.running--;
    const next = this.queue.shift();
    if (next) next();
  }

  /** Current number of running processes. */
  get active(): number {
    return this.running;
  }

  /** Number of waiting requests. */
  get pending(): number {
    return this.queue.length;
  }
}

const limiter = new ConcurrencyLimiter(8);

/**
 * Run a Python script and return its JSON stdout output.
 * Retries once on transient failures (non-zero exit, spawn errors).
 */
export async function runPythonScript<T = unknown>(
  scriptRelPath: string,
  args: Record<string, unknown>,
  config: QuantTradingConfig,
  {
    maxRetries = 1,
    retryDelayMs = 2000,
    timeoutMs,
  }: { maxRetries?: number; retryDelayMs?: number; timeoutMs?: number } = {},
): Promise<PythonBridgeResult<T>> {
  let lastResult: PythonBridgeResult<T> = { ok: false, error: "no attempt" };
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, retryDelayMs));
    }
    lastResult = await runPythonScriptOnce<T>(scriptRelPath, args, config, timeoutMs);
    if (lastResult.ok) return lastResult;
  }
  return lastResult;
}

/** Single-attempt Python script execution (internal). Uses concurrency limiter. */
async function runPythonScriptOnce<T = unknown>(
  scriptRelPath: string,
  args: Record<string, unknown>,
  config: QuantTradingConfig,
  timeoutMs?: number,
): Promise<PythonBridgeResult<T>> {
  await limiter.acquire();
  try {
    return await _spawnPython<T>(scriptRelPath, args, config, timeoutMs);
  } finally {
    limiter.release();
  }
}

/** Raw Python process spawn (no concurrency control). */
async function _spawnPython<T = unknown>(
  scriptRelPath: string,
  args: Record<string, unknown>,
  config: QuantTradingConfig,
  overrideTimeoutMs?: number,
): Promise<PythonBridgeResult<T>> {
  const pythonPath = config.pythonPath ?? "python3";
  const scriptDir = getPythonDir();

  // Run as a Python module (python -m engines.factor_filter) so that
  // relative imports within the engines/ package work correctly.
  // Convert path like "engines/factor_filter.py" → "engines.factor_filter"
  const modulePath = scriptRelPath.replace(/\.py$/, "").replace(/[\\/]/g, ".");

  const TIMEOUT_MS = overrideTimeoutMs ?? 300_000; // default 5 minutes

  return new Promise((resolve) => {
    let settled = false;
    const child = spawn(pythonPath, ["-m", modulePath, "--json"], {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: scriptDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        QUANT_CONFIG: JSON.stringify(args),
        ...(config.tradingAccount?.qmt?.windowsBridgeUrl
          ? { WINDOWS_XTDATA_URL: config.tradingAccount.qmt.windowsBridgeUrl }
          : {}),
        ...(config.dataProviders?.tushare?.token
          ? { TUSHARE_TOKEN: config.dataProviders.tushare.token }
          : {}),
        ...(config.dataProviders?.jqdata?.username
          ? { JQDATA_USERNAME: config.dataProviders.jqdata.username }
          : {}),
        ...(config.dataProviders?.jqdata?.password
          ? { JQDATA_PASSWORD: config.dataProviders.jqdata.password }
          : {}),
        ...(config.dataProviders?.fred?.apiKey
          ? { FRED_API_KEY: config.dataProviders.fred.apiKey }
          : {}),
      },
    });

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGKILL");
        resolve({
          ok: false,
          error: `Python script timed out after ${TIMEOUT_MS / 1000}s: ${scriptRelPath}`,
        });
      }
    }, TIMEOUT_MS);

    let stdout = "";
    let stderr = "";
    let stderrRemainder = "";

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf-8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      stderr += text;
      if (_bridgeLogger) {
        const lines = (stderrRemainder + text).split("\n");
        stderrRemainder = lines.pop() ?? "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed) _bridgeLogger.info(`[python:${modulePath}] ${trimmed}`);
        }
      }
    });

    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // Flush remaining stderr
      if (_bridgeLogger && stderrRemainder.trim()) {
        _bridgeLogger.info(`[python:${modulePath}] ${stderrRemainder.trim()}`);
      }
      if (code !== 0) {
        resolve({ ok: false, error: stderr || `Python exited with code ${code}` });
        return;
      }
      try {
        const data = JSON.parse(stdout) as T;
        resolve({ ok: true, data });
      } catch {
        resolve({ ok: false, error: `Failed to parse Python output: ${stdout.slice(0, 500)}` });
      }
    });

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ok: false, error: `Failed to spawn Python: ${err.message}` });
    });

    // Send input via stdin
    child.stdin.write(JSON.stringify(args));
    child.stdin.end();
  });
}

/**
 * Run inline Python code and parse its JSON stdout output.
 * Used by services that generate Python scripts dynamically (e.g. auction-monitor).
 * The code should `print(json.dumps(...))` its result to stdout.
 */
export async function runInlinePython<T = unknown>(
  code: string,
  config: QuantTradingConfig,
): Promise<PythonBridgeResult<T>> {
  await limiter.acquire();
  try {
    return await _spawnInlinePython<T>(code, config);
  } finally {
    limiter.release();
  }
}

/** Spawn python with inline code via stdin. */
async function _spawnInlinePython<T = unknown>(
  code: string,
  config: QuantTradingConfig,
  overrideTimeoutMs?: number,
): Promise<PythonBridgeResult<T>> {
  const pythonPath = config.pythonPath ?? "python3";
  const TIMEOUT_MS = overrideTimeoutMs ?? 300_000;

  return new Promise((resolve) => {
    let settled = false;
    const child = spawn(pythonPath, ["-"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
    });

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGKILL");
        resolve({ ok: false, error: `Inline Python timed out after ${TIMEOUT_MS / 1000}s` });
      }
    }, TIMEOUT_MS);

    let stdout = "";
    let stderr = "";
    let stderrRemainder = "";
    child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf-8"); });
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      stderr += text;
      if (_bridgeLogger) {
        const lines = (stderrRemainder + text).split("\n");
        stderrRemainder = lines.pop() ?? "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed) _bridgeLogger.info(`[python:inline] ${trimmed}`);
        }
      }
    });

    child.on("close", (exitCode) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (_bridgeLogger && stderrRemainder.trim()) {
        _bridgeLogger.info(`[python:inline] ${stderrRemainder.trim()}`);
      }
      if (exitCode !== 0) {
        resolve({ ok: false, error: stderr || `Python exited with code ${exitCode}` });
        return;
      }
      try {
        const data = JSON.parse(stdout) as T;
        resolve({ ok: true, data });
      } catch {
        resolve({ ok: false, error: `Failed to parse Python output: ${stdout.slice(0, 500)}` });
      }
    });

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ok: false, error: `Failed to spawn Python: ${err.message}` });
    });

    child.stdin.write(code);
    child.stdin.end();
  });
}

/**
 * Check if a Python module is available.
 */
export async function checkPythonModule(
  moduleName: string,
  config: QuantTradingConfig,
): Promise<boolean> {
  const pythonPath = config.pythonPath ?? "python3";
  return new Promise((resolve) => {
    const child = spawn(pythonPath, ["-c", `import ${moduleName}; print("ok")`], {
      stdio: ["ignore", "pipe", "ignore"],
    });
    let out = "";
    child.stdout.on("data", (d: Buffer) => {
      out += d.toString();
    });
    child.on("close", () => resolve(out.trim() === "ok"));
    child.on("error", () => resolve(false));
  });
}
