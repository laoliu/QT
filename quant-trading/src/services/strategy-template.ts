/**
 * Strategy Template System — create custom strategy configurations from
 * predefined templates with parameter overrides.
 *
 * Features:
 *   - Template catalog with common strategy patterns (trend, mean-rev, breakout …)
 *   - User strategy configs: save/load/list/delete named strategy presets
 *   - Parameter validation against strategy definition schema
 *   - Persist to `~/.openclaw/quant-trading/user-strategies.json`
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { listStrategies, getStrategy } from "../engine/strategy-registry.js";
import type { StrategyDefinition, StrategyParameter } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface StrategyTemplate {
  id: string;
  name: string;
  description: string;
  /** Base strategy ID from the registry */
  baseStrategyId: string;
  /** Category for organization */
  category: "trend" | "oscillator" | "breakout" | "range" | "composite" | "custom";
  /** Suggested parameter overrides (still uses base strategy defaults if omitted) */
  suggestedParams: Record<string, unknown>;
  /** Symbol suggestions (optional) */
  suggestedSymbols?: string[];
  /** Markets this template is tuned for */
  markets?: string[];
  /** Usage notes for the user */
  notes?: string;
}

export interface UserStrategy {
  id: string;
  name: string;
  description?: string;
  /** The underlying strategy ID from the registry */
  strategyId: string;
  /** Parameter overrides */
  parameters: Record<string, unknown>;
  /** Target symbols */
  symbols: string[];
  /** User-defined tags */
  tags: string[];
  /** Whether this is favorited */
  starred: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ParamValidationError {
  param: string;
  message: string;
}

// ── Built-in Templates ───────────────────────────────────────

export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  {
    id: "conservative-trend",
    name: "稳健趋势跟踪",
    description: "长周期双均线策略，减少虚假信号，适合中长线投资者",
    baseStrategyId: "dual-ma-crossover",
    category: "trend",
    suggestedParams: { fastPeriod: 20, slowPeriod: 60 },
    suggestedSymbols: ["000300", "510300"],
    notes: "适合跟踪宽基指数或蓝筹股，减少频繁交易",
  },
  {
    id: "aggressive-trend",
    name: "激进趋势跟踪",
    description: "短周期双均线策略，反应快速，适合活跃交易者",
    baseStrategyId: "dual-ma-crossover",
    category: "trend",
    suggestedParams: { fastPeriod: 5, slowPeriod: 15 },
    notes: "适合短线波段操作，回撤较大但反应灵敏",
  },
  {
    id: "bollinger-squeeze",
    name: "布林带压缩突破",
    description: "窄带宽突破策略，使用较紧的标准差捕捉区间突破",
    baseStrategyId: "mean-reversion",
    category: "range",
    suggestedParams: { period: 20, stdDev: 1.5 },
    notes: "适合震荡行情阶段",
  },
  {
    id: "wide-bollinger",
    name: "宽幅均值回归",
    description: "宽标准差布林带，仅在极端偏离时交易",
    baseStrategyId: "mean-reversion",
    category: "range",
    suggestedParams: { period: 30, stdDev: 2.5 },
    notes: "交易频率低但胜率高",
  },
  {
    id: "turtle-20d",
    name: "海龟交易-20日突破",
    description: "经典海龟交易系统的20日突破变体",
    baseStrategyId: "momentum-breakout",
    category: "breakout",
    suggestedParams: { lookback: 20 },
    notes: "经典趋势跟踪方法，适合趋势明显的品种",
  },
  {
    id: "turtle-55d",
    name: "海龟交易-55日突破",
    description: "海龟交易系统的长周期55日突破",
    baseStrategyId: "momentum-breakout",
    category: "breakout",
    suggestedParams: { lookback: 55 },
    notes: "适合大级别趋势判断，信号少但可靠",
  },
  {
    id: "multi-strategy-vote",
    name: "多策略投票",
    description: "综合多个策略投票决策，仅当多数一致时执行",
    baseStrategyId: "composite",
    category: "composite",
    suggestedParams: {
      fastPeriod: 10,
      slowPeriod: 30,
      breakoutLookback: 20,
      bollingerPeriod: 20,
      bollingerStdDev: 2,
    },
    notes: "降低单一策略风险，适合稳健型配置",
  },
  {
    id: "rsi-oversold-bounce",
    name: "RSI超卖反弹",
    description: "RSI跌入超卖区域后反弹买入的短线策略",
    baseStrategyId: "rsi",
    category: "oscillator",
    suggestedParams: { period: 14, oversold: 25, overbought: 75 },
    notes: "适合大盘下跌后的抢反弹操作",
  },
];

// ── Parameter Validation ─────────────────────────────────────

/**
 * Validate parameter overrides against a strategy definition's schema.
 */
export function validateParameters(
  strategyDef: StrategyDefinition,
  params: Record<string, unknown>,
): ParamValidationError[] {
  const errors: ParamValidationError[] = [];

  for (const [key, value] of Object.entries(params)) {
    const paramDef = strategyDef.parameters.find((p) => p.name === key);
    if (!paramDef) {
      errors.push({
        param: key,
        message: `Unknown parameter "${key}" for strategy "${strategyDef.id}"`,
      });
      continue;
    }
    if (paramDef.type === "number") {
      if (typeof value !== "number" || Number.isNaN(value)) {
        errors.push({ param: key, message: `"${key}" must be a number, got ${typeof value}` });
        continue;
      }
      if (paramDef.min !== undefined && value < paramDef.min) {
        errors.push({ param: key, message: `"${key}" must be >= ${paramDef.min}, got ${value}` });
      }
      if (paramDef.max !== undefined && value > paramDef.max) {
        errors.push({ param: key, message: `"${key}" must be <= ${paramDef.max}, got ${value}` });
      }
    } else if (paramDef.type === "boolean" && typeof value !== "boolean") {
      errors.push({ param: key, message: `"${key}" must be a boolean` });
    } else if (paramDef.type === "string" && typeof value !== "string") {
      errors.push({ param: key, message: `"${key}" must be a string` });
    }
  }

  return errors;
}

/**
 * Merge defaults from strategy definition with user overrides.
 */
export function mergeWithDefaults(
  strategyDef: StrategyDefinition,
  overrides: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const p of strategyDef.parameters) {
    merged[p.name] = p.default;
  }
  for (const [k, v] of Object.entries(overrides)) {
    merged[k] = v;
  }
  return merged;
}

// ── User Strategy Store ──────────────────────────────────────

export class UserStrategyStore {
  private strategies: UserStrategy[] = [];
  private persistPath: string;

  constructor(dataDir?: string) {
    const dir = dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.persistPath = path.join(dir, "user-strategies.json");
  }

  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.persistPath, "utf-8");
      this.strategies = JSON.parse(raw);
    } catch {
      this.strategies = [];
    }
  }

  async save(): Promise<void> {
    await fs.mkdir(path.dirname(this.persistPath), { recursive: true });
    await fs.writeFile(this.persistPath, JSON.stringify(this.strategies, null, 2));
  }

  /** List all user strategies, optionally filtered. */
  list(opts?: { tag?: string; strategyId?: string; starred?: boolean }): UserStrategy[] {
    let result = [...this.strategies];
    if (opts?.tag) result = result.filter((s) => s.tags.includes(opts.tag!));
    if (opts?.strategyId) result = result.filter((s) => s.strategyId === opts.strategyId);
    if (opts?.starred !== undefined) result = result.filter((s) => s.starred === opts.starred);
    return result;
  }

  /** Get a specific user strategy by ID. */
  get(id: string): UserStrategy | undefined {
    return this.strategies.find((s) => s.id === id);
  }

  /**
   * Create a user strategy from a template or from scratch.
   * Validates parameters against the base strategy definition.
   */
  async create(opts: {
    name: string;
    strategyId: string;
    parameters?: Record<string, unknown>;
    symbols?: string[];
    description?: string;
    tags?: string[];
    templateId?: string;
  }): Promise<{ strategy?: UserStrategy; errors?: ParamValidationError[] }> {
    const stratDef = getStrategy(opts.strategyId);
    if (!stratDef) {
      return {
        errors: [{ param: "strategyId", message: `Unknown strategy "${opts.strategyId}"` }],
      };
    }

    // If creating from template, merge template params with user overrides
    let params = opts.parameters ?? {};
    if (opts.templateId) {
      const template = STRATEGY_TEMPLATES.find((t) => t.id === opts.templateId);
      if (template) {
        params = { ...template.suggestedParams, ...params };
      }
    }

    // Merge with defaults
    params = mergeWithDefaults(stratDef, params);

    // Validate
    const errors = validateParameters(stratDef, params);
    if (errors.length > 0) return { errors };

    const now = new Date().toISOString();
    const strategy: UserStrategy = {
      id: `us-${Date.now().toString(36)}`,
      name: opts.name,
      description: opts.description,
      strategyId: opts.strategyId,
      parameters: params,
      symbols: opts.symbols ?? [],
      tags: opts.tags ?? [],
      starred: false,
      createdAt: now,
      updatedAt: now,
    };

    this.strategies.push(strategy);
    await this.save();
    return { strategy };
  }

  /** Update an existing user strategy. */
  async update(
    id: string,
    updates: {
      name?: string;
      description?: string;
      parameters?: Record<string, unknown>;
      symbols?: string[];
      tags?: string[];
      starred?: boolean;
    },
  ): Promise<{ strategy?: UserStrategy; errors?: ParamValidationError[] }> {
    const idx = this.strategies.findIndex((s) => s.id === id);
    if (idx === -1) return { errors: [{ param: "id", message: `Strategy "${id}" not found` }] };

    const existing = this.strategies[idx]!;

    if (updates.parameters) {
      const stratDef = getStrategy(existing.strategyId);
      if (stratDef) {
        const merged = mergeWithDefaults(stratDef, updates.parameters);
        const errors = validateParameters(stratDef, merged);
        if (errors.length > 0) return { errors };
        existing.parameters = merged;
      }
    }

    if (updates.name !== undefined) existing.name = updates.name;
    if (updates.description !== undefined) existing.description = updates.description;
    if (updates.symbols !== undefined) existing.symbols = updates.symbols;
    if (updates.tags !== undefined) existing.tags = updates.tags;
    if (updates.starred !== undefined) existing.starred = updates.starred;
    existing.updatedAt = new Date().toISOString();

    await this.save();
    return { strategy: existing };
  }

  /** Delete a user strategy by ID. */
  async delete(id: string): Promise<boolean> {
    const before = this.strategies.length;
    this.strategies = this.strategies.filter((s) => s.id !== id);
    if (this.strategies.length < before) {
      await this.save();
      return true;
    }
    return false;
  }

  /**
   * Create a user strategy directly from a template.
   */
  async createFromTemplate(
    templateId: string,
    opts?: { name?: string; symbols?: string[]; paramOverrides?: Record<string, unknown> },
  ): Promise<{ strategy?: UserStrategy; errors?: ParamValidationError[] }> {
    const template = STRATEGY_TEMPLATES.find((t) => t.id === templateId);
    if (!template) {
      return { errors: [{ param: "templateId", message: `Unknown template "${templateId}"` }] };
    }

    return this.create({
      name: opts?.name ?? template.name,
      strategyId: template.baseStrategyId,
      parameters: { ...template.suggestedParams, ...(opts?.paramOverrides ?? {}) },
      symbols: opts?.symbols ?? template.suggestedSymbols ?? [],
      description: template.description,
      tags: [template.category],
    });
  }
}
