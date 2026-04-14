import { QuantTradingConfig } from "../config.js";
import { OpenClawPluginApi } from "@anthropic-ai/openclaw-plugin";
import { spawn } from "child_process";
import * as path from "path";

/**
 * A股多因子选股工具 — 调用 aipha 策略引擎选股
 */
export function createEquityScreeningTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "equity_screening",
    description:
      "Run AI-powered A-share equity screening using aipha multi-factor strategies. " +
      "Supports strategies: dividend_quality, earnings_momentum, garp_select, value_quality, " +
      "small_cap_quality, pead_event, northbound_enhanced, sector_rotation, vol_managed_value, " +
      "momentum_acceleration, turnaround_play, stable_compounder, buyback_signal, insider_buying, " +
      "high_delivery_expect, index_enhance. Returns ranked stock list with factor scores.",
    parameters: {
      type: "object" as const,
      properties: {
        strategy: {
          type: "string",
          description: "Strategy name to use for screening",
        },
        top_k: {
          type: "number",
          description: "Number of top stocks to return (default: 30)",
        },
        universe: {
          type: "string",
          description: "Universe type: ALL_A, CSI300, CSI500, CSI1000 (default: ALL_A)",
        },
        params: {
          type: "object",
          description: "Additional strategy parameters (optional)",
        },
      },
      required: ["strategy"],
    },
    async execute(args: Record<string, unknown>) {
      const strategy = args.strategy as string;
      const top_k = (args.top_k as number) || 30;
      const universe = (args.universe as string) || "ALL_A";
      const params = args.params || {};

      const pythonDir = path.join(config.workingDirectory || ".", "python");
      const enginePath = path.join(pythonDir, "engines", "aipha_screening_engine.py");

      const input = JSON.stringify({ strategy, top_k, universe, params });

      return new Promise((resolve, reject) => {
        const proc = spawn("python", [enginePath], {
          cwd: pythonDir,
          env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        });

        let stdout = "";
        let stderr = "";
        proc.stdin.write(input);
        proc.stdin.end();
        proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
        proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
        proc.on("close", (code: number) => {
          if (code !== 0) {
            resolve({ error: true, message: stderr || `exit code ${code}` });
          } else {
            try { resolve(JSON.parse(stdout)); } catch { resolve({ raw: stdout }); }
          }
        });
        proc.on("error", (err: Error) => resolve({ error: true, message: err.message }));
      });
    },
  };
}
