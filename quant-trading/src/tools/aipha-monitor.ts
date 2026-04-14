import { QuantTradingConfig } from "../config.js";
import { OpenClawPluginApi } from "@anthropic-ai/openclaw-plugin";
import { spawn } from "child_process";
import * as path from "path";

/**
 * 因子/策略健康监控工具 — 调用 monitoring 子系统
 */
export function createAiphaMonitorTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "aipha_monitor",
    description:
      "Monitor factor decay, strategy crowding, and model retrain triggers. " +
      "Actions: factor_health (check IC decay), crowding (assess holding concentration), " +
      "retrain_check (check if model retraining is needed).",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description: "Action: factor_health | crowding | retrain_check",
          enum: ["factor_health", "crowding", "retrain_check"],
        },
        factor_names: {
          type: "array",
          items: { type: "string" },
          description: "Factor names to check (for factor_health)",
        },
        strategy_name: {
          type: "string",
          description: "Strategy name (for crowding/retrain_check)",
        },
        params: {
          type: "object",
          description: "Additional parameters",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const pythonDir = path.join(config.workingDirectory || ".", "python");
      const enginePath = path.join(pythonDir, "engines", "aipha_monitor_engine.py");

      const input = JSON.stringify(args);

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
