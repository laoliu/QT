import { spawn } from "child_process";
import * as path from "path";

/**
 * AiphaRiskGateAdapter — extends the existing RiskGate with aipha-level
 * risk constraints: factor crowding, decay monitoring, portfolio
 * concentration (HHI), sector deviation, and liquidity impact checks.
 *
 * Uses the Python bridge engine `aipha_risk_gate_engine.py` for computation.
 */

export interface AiphaRiskConfig {
  maxPositionWeight?: number;
  maxDrawdown?: number;
  maxDailyTurnover?: number;
  maxSectorWeight?: number;
  maxHhi?: number;
  crowdingThreshold?: number;
  decayThreshold?: number;
  maxParticipationRate?: number;
}

export interface AiphaRiskWarning {
  rule: string;
  symbol?: string;
  sector?: string;
  message: string;
  [key: string]: unknown;
}

export interface AiphaRiskResult {
  passed: boolean;
  warnings: AiphaRiskWarning[];
  [key: string]: unknown;
}

export interface PortfolioPosition {
  symbol: string;
  market_value: number;
  sector?: string;
  crowding_score?: number;
  factor_ic?: number;
  avg_daily_turnover?: number;
}

export class AiphaRiskGateAdapter {
  private pythonDir: string;
  private enginePath: string;
  private config: AiphaRiskConfig;

  constructor(workingDirectory: string, config?: AiphaRiskConfig) {
    this.pythonDir = path.join(workingDirectory, "python");
    this.enginePath = path.join(this.pythonDir, "engines", "aipha_risk_gate_engine.py");
    this.config = config ?? {};
  }

  /** Check portfolio-level constraints (concentration, sector, HHI). */
  async checkPortfolio(positions: PortfolioPosition[]): Promise<AiphaRiskResult> {
    return this.callBridge({
      action: "check_portfolio",
      positions,
      config: this.toBridgeConfig(),
    });
  }

  /** Check order-level constraints (crowding, decay, liquidity). */
  async checkOrder(
    order: { symbol: string; side: string; quantity: number; price: number; crowding_score?: number; factor_ic?: number; avg_daily_turnover?: number },
    positions: PortfolioPosition[],
  ): Promise<AiphaRiskResult> {
    return this.callBridge({
      action: "check_order",
      order,
      positions,
      config: this.toBridgeConfig(),
    });
  }

  /** Fetch current constraint configuration. */
  async getConstraints(): Promise<Record<string, unknown>> {
    return this.callBridge({
      action: "get_constraints",
      config: this.toBridgeConfig(),
    });
  }

  updateConfig(config: Partial<AiphaRiskConfig>): void {
    this.config = { ...this.config, ...config };
  }

  // ── Internal ────────────────────────────────────────────────

  private toBridgeConfig(): Record<string, unknown> {
    return {
      max_position_weight: this.config.maxPositionWeight,
      max_drawdown: this.config.maxDrawdown,
      max_daily_turnover: this.config.maxDailyTurnover,
      max_sector_weight: this.config.maxSectorWeight,
      max_hhi: this.config.maxHhi,
      crowding_threshold: this.config.crowdingThreshold,
      decay_threshold: this.config.decayThreshold,
      max_participation_rate: this.config.maxParticipationRate,
    };
  }

  private callBridge(input: Record<string, unknown>): Promise<AiphaRiskResult> {
    return new Promise((resolve) => {
      const proc = spawn("python", [this.enginePath], {
        cwd: this.pythonDir,
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      });

      let stdout = "";
      let stderr = "";
      proc.stdin.write(JSON.stringify(input));
      proc.stdin.end();
      proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
      proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
      proc.on("close", (code: number) => {
        if (code !== 0) {
          resolve({ passed: false, warnings: [{ rule: "BRIDGE_ERROR", message: stderr || `exit ${code}` }] });
        } else {
          try {
            resolve(JSON.parse(stdout));
          } catch {
            resolve({ passed: false, warnings: [{ rule: "PARSE_ERROR", message: stdout }] });
          }
        }
      });
      proc.on("error", (err: Error) => {
        resolve({ passed: false, warnings: [{ rule: "SPAWN_ERROR", message: err.message }] });
      });
    });
  }
}
