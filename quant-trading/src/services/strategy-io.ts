/**
 * Strategy Import/Export — portable JSON packages for sharing
 * strategy configurations between users and machines.
 *
 * Features:
 *   - Export user strategies as self-contained JSON packages
 *   - Import strategies from JSON with validation
 *   - Version-stamped packages for compatibility
 *   - Batch export/import
 *   - Include optional backtest result snapshots
 */

import { getStrategy } from "../engine/strategy-registry.js";
import type { BacktestResult } from "../types.js";
import type { UserStrategy, UserStrategyStore, ParamValidationError } from "./strategy-template.js";
import { validateParameters, mergeWithDefaults } from "./strategy-template.js";

// ── Types ────────────────────────────────────────────────────

export const EXPORT_FORMAT_VERSION = "1.0";

export interface StrategyPackage {
  /** Format version for forward compatibility */
  formatVersion: string;
  /** Export metadata */
  exportedAt: string;
  exportedBy?: string;
  /** Strategies in this package */
  strategies: ExportedStrategy[];
}

export interface ExportedStrategy {
  /** Original user strategy ID (for dedup on import) */
  originalId: string;
  name: string;
  description?: string;
  /** The base strategy definition ID */
  strategyId: string;
  /** Parameter values */
  parameters: Record<string, unknown>;
  /** Target symbols */
  symbols: string[];
  /** User tags */
  tags: string[];
  /** Optional backtest snapshot */
  backtestSnapshot?: BacktestResult;
}

export interface ImportResult {
  imported: UserStrategy[];
  skipped: Array<{ name: string; reason: string }>;
  errors: Array<{ name: string; errors: ParamValidationError[] }>;
}

export interface ImportOptions {
  /** Skip strategies with duplicate originalId (default: true) */
  skipDuplicates?: boolean;
  /** Override symbols for all imported strategies */
  symbolOverride?: string[];
  /** Tag to add to all imported strategies */
  addTag?: string;
  /** Whether to validate parameters strictly (default: true) */
  validate?: boolean;
}

// ── Export ────────────────────────────────────────────────────

/**
 * Export user strategies into a portable JSON package.
 */
export function exportStrategies(
  strategies: UserStrategy[],
  opts?: {
    exportedBy?: string;
    includeBacktestResults?: Map<string, BacktestResult>;
  },
): StrategyPackage {
  return {
    formatVersion: EXPORT_FORMAT_VERSION,
    exportedAt: new Date().toISOString(),
    exportedBy: opts?.exportedBy,
    strategies: strategies.map((s) => ({
      originalId: s.id,
      name: s.name,
      description: s.description,
      strategyId: s.strategyId,
      parameters: s.parameters,
      symbols: s.symbols,
      tags: s.tags,
      backtestSnapshot: opts?.includeBacktestResults?.get(s.id),
    })),
  };
}

// ── Validation ───────────────────────────────────────────────

export interface PackageValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * Validate a strategy package before import.
 */
export function validatePackage(pkg: unknown): PackageValidation {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (!pkg || typeof pkg !== "object") {
    return { valid: false, errors: ["Package must be a JSON object"], warnings: [] };
  }

  const p = pkg as Record<string, unknown>;

  if (typeof p.formatVersion !== "string") {
    errors.push("Missing or invalid formatVersion");
  } else if (p.formatVersion !== EXPORT_FORMAT_VERSION) {
    warnings.push(
      `Package version ${p.formatVersion} differs from current ${EXPORT_FORMAT_VERSION}`,
    );
  }

  if (!Array.isArray(p.strategies)) {
    errors.push("Missing or invalid strategies array");
    return { valid: false, errors, warnings };
  }

  for (let i = 0; i < p.strategies.length; i++) {
    const s = p.strategies[i] as Record<string, unknown>;
    if (!s || typeof s !== "object") {
      errors.push(`strategies[${i}] must be an object`);
      continue;
    }
    if (typeof s.name !== "string" || !s.name) {
      errors.push(`strategies[${i}].name is required`);
    }
    if (typeof s.strategyId !== "string" || !s.strategyId) {
      errors.push(`strategies[${i}].strategyId is required`);
    }
    if (s.parameters !== undefined && typeof s.parameters !== "object") {
      errors.push(`strategies[${i}].parameters must be an object`);
    }
    if (s.symbols !== undefined && !Array.isArray(s.symbols)) {
      errors.push(`strategies[${i}].symbols must be an array`);
    }

    // Check if base strategy exists
    if (typeof s.strategyId === "string") {
      const stratDef = getStrategy(s.strategyId);
      if (!stratDef) {
        warnings.push(`strategies[${i}].strategyId "${s.strategyId}" not found in registry`);
      }
    }
  }

  return { valid: errors.length === 0, errors, warnings };
}

// ── Import ───────────────────────────────────────────────────

/**
 * Import strategies from a package into the user strategy store.
 */
export async function importStrategies(
  store: UserStrategyStore,
  pkg: StrategyPackage,
  opts: ImportOptions = {},
): Promise<ImportResult> {
  const { skipDuplicates = true, symbolOverride, addTag, validate = true } = opts;

  const imported: UserStrategy[] = [];
  const skipped: ImportResult["skipped"] = [];
  const errors: ImportResult["errors"] = [];

  const existingIds = new Set(store.list().map((s) => s.id));

  for (const exported of pkg.strategies) {
    // Check for duplicates by originalId match
    if (skipDuplicates) {
      const existing = store
        .list()
        .find(
          (s) =>
            s.id === exported.originalId ||
            (s.name === exported.name && s.strategyId === exported.strategyId),
        );
      if (existing) {
        skipped.push({ name: exported.name, reason: "Duplicate exists" });
        continue;
      }
    }

    // Validate parameters against strategy definition if requested
    if (validate) {
      const stratDef = getStrategy(exported.strategyId);
      if (!stratDef) {
        skipped.push({
          name: exported.name,
          reason: `Unknown base strategy: ${exported.strategyId}`,
        });
        continue;
      }
      const merged = mergeWithDefaults(stratDef, exported.parameters ?? {});
      const paramErrors = validateParameters(stratDef, merged);
      if (paramErrors.length > 0) {
        errors.push({ name: exported.name, errors: paramErrors });
        continue;
      }
    }

    // Build symbols list
    const symbols = symbolOverride ?? exported.symbols ?? [];

    // Build tags
    const tags = [...(exported.tags ?? [])];
    if (addTag && !tags.includes(addTag)) {
      tags.push(addTag);
    }

    const result = await store.create({
      name: exported.name,
      strategyId: exported.strategyId,
      parameters: exported.parameters,
      symbols,
      description: exported.description,
      tags,
    });

    if (result.errors) {
      errors.push({ name: exported.name, errors: result.errors });
    } else if (result.strategy) {
      imported.push(result.strategy);
    }
  }

  return { imported, skipped, errors };
}

/**
 * Serialize a package to JSON string.
 */
export function serializePackage(pkg: StrategyPackage): string {
  return JSON.stringify(pkg, null, 2);
}

/**
 * Deserialize a JSON string to a package.
 * Returns null if parsing fails.
 */
export function deserializePackage(json: string): StrategyPackage | null {
  try {
    return JSON.parse(json) as StrategyPackage;
  } catch {
    return null;
  }
}
