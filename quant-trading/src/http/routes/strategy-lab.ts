import type {
  CompositionConfig,
  CompositionMethod,
  SubStrategyConfig,
} from "../../engine/dynamic-composer.js";
import {
  hasStrategyFactory,
  listComposedStrategies,
  listStrategies,
} from "../../engine/strategy-registry.js";
import { runBatchBacktest, type BatchBacktestItem } from "../../services/batch-backtest.js";
/**
 * Strategy Lab API routes (composed strategies, exploration, batch backtest)
 *
 * Extracted from admin-routes.ts for maintainability.
 */
import type { AdminRouteDeps } from "../admin-routes.js";
import {
  json,
  clampInteger,
  toFiniteNumber,
  parseObjectBody,
  buildFallbackCandidates,
  sanitizeAiCandidates,
  extractLastAssistantText,
  parseJsonFromText,
  type StrategyCatalogItem,
  type AiCompositionCandidate,
} from "./_helpers.js";
import { todayCST } from "../../utils/cst-time.js";

export function registerStrategyLabRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure, securePublic, composedStrategyStore, explorationHistoryStore } =
    deps;

  // ── Strategy Lab: Composed Strategies CRUD ───────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-lab/ai-explore",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      if (!composedStrategyStore) {
        json(res, { error: "组合策略存储未初始化" }, 503);
        return;
      }

      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = ((body.symbols as string) ?? "000300")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        const startDate =
          (body.startDate as string) ??
          (() => {
            const d = new Date();
            d.setFullYear(d.getFullYear() - 1);
            return d.toISOString().slice(0, 10);
          })();
        const endDate = (body.endDate as string) ?? todayCST();
        const initialCash = toFiniteNumber(body.initialCash, 100000);
        const candidateCount = clampInteger(body.candidateCount, 3, 30, 10);
        const minStrategies = clampInteger(body.minStrategies, 2, 6, 2);
        const maxStrategies = clampInteger(
          body.maxStrategies,
          minStrategies,
          8,
          Math.max(minStrategies, 4),
        );

        const composedIds = new Set(listComposedStrategies().map((s) => s.id));
        const strategyCatalog: StrategyCatalogItem[] = listStrategies()
          .filter((s) => !composedIds.has(s.id) && hasStrategyFactory(s.id))
          .map((s) => ({
            id: s.id,
            name: s.name,
            description: s.description,
            parameters: (s.parameters ?? []).map((p) => ({
              name: p.name,
              type: p.type,
              default: p.default,
              description: p.description,
              min: p.min,
              max: p.max,
            })),
          }));

        if (strategyCatalog.length < 2) {
          json(res, { error: "可用策略不足，至少需要 2 个策略" }, 400);
          return;
        }

        let source: "ai" | "fallback" = "ai";
        let aiCandidates: AiCompositionCandidate[] = [];

        try {
          const sessionKey = `agent:quant-strategy-lab:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 8)}`;
          const prompt = [
            "请你作为量化策略组合专家，根据给定策略库生成合理的候选组合。",
            `约束: 生成 ${candidateCount} 个候选，每个候选策略数在 ${minStrategies}-${maxStrategies}。`,
            "方法 method 仅能是 voting / weighted-score / gated / cascade。",
            "每个候选必须提供 strategies 数组，每项包含 strategyId，且 strategyId 必须来自策略库。",
            "仅当 method=voting 时可设置 minVotes。",
            "gated/cascade 里建议包含 primary + filter 角色。",
            "请只返回 JSON，不要输出任何额外说明。",
            "JSON Schema:",
            '{"candidates":[{"name":"string","method":"voting|weighted-score|gated|cascade","minVotes":2,"rationale":"string","strategies":[{"strategyId":"id","weight":1,"role":"primary|filter"}]}]}',
            "策略库:",
            JSON.stringify(strategyCatalog),
          ].join("\n");

          const run = await api.runtime.subagent.run({
            sessionKey,
            message: prompt,
            deliver: false,
            lane: "local",
            idempotencyKey: `quant-strategy-lab:${Date.now()}`,
          });
          const wait = await api.runtime.subagent.waitForRun({
            runId: run.runId,
            timeoutMs: 120_000,
          });
          if (wait.status !== "ok") {
            throw new Error(wait.error || `AI 生成超时/失败: ${wait.status}`);
          }
          const messages = await api.runtime.subagent.getSessionMessages({ sessionKey, limit: 40 });
          const lastAssistantText = extractLastAssistantText(messages.messages ?? []);
          const parsed = parseJsonFromText(lastAssistantText);
          aiCandidates = sanitizeAiCandidates({
            raw: parsed,
            strategyCatalog,
            candidateCount,
            minStrategies,
            maxStrategies,
          });
          if (aiCandidates.length === 0) {
            throw new Error("AI 未返回有效候选组合");
          }
        } catch (err) {
          source = "fallback";
          const reason = err instanceof Error ? err.message : String(err);
          api.logger.warn(`[quant] AI 组合生成失败，使用回退规则: ${reason}`);
          aiCandidates = buildFallbackCandidates({
            strategyCatalog,
            candidateCount,
            minStrategies,
            maxStrategies,
          });
        }

        if (aiCandidates.length === 0) {
          json(res, { error: "未生成到有效候选组合" }, 500);
          return;
        }

        const createdIds: string[] = [];
        try {
          for (let i = 0; i < aiCandidates.length; i++) {
            const c = aiCandidates[i]!;
            const id = `ai-composed-${Date.now().toString(36)}-${i.toString(36)}`;
            const comp: CompositionConfig = {
              id,
              name: c.name,
              description: c.rationale,
              method: c.method,
              strategies: c.strategies.map(
                (s: { strategyId: string; weight?: number; role?: "primary" | "filter" }) => ({
                  strategyId: s.strategyId,
                  weight: s.weight ?? 1,
                  role: s.role ?? "primary",
                }),
              ),
              minVotes: c.minVotes,
            };
            await composedStrategyStore.upsert(comp);
            createdIds.push(id);
          }

          const items: BatchBacktestItem[] = createdIds.map((id, idx) => ({
            label: aiCandidates[idx]?.name || id,
            request: { strategyId: id, symbols, startDate, endDate, initialCash },
          }));

          const summary = await runBatchBacktest(items, config, { maxConcurrency: 4 });

          // Persist exploration result for history viewing
          if (explorationHistoryStore) {
            const explorationId = `explore-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
            void explorationHistoryStore.add({
              id: explorationId,
              createdAt: new Date().toISOString(),
              params: {
                symbols,
                startDate,
                endDate,
                initialCash,
                candidateCount,
                minStrategies,
                maxStrategies,
              },
              source,
              candidates: aiCandidates,
              ...summary,
            });
          }

          json(res, {
            ok: true,
            source,
            candidates: aiCandidates,
            strategyCatalogSize: strategyCatalog.length,
            ...summary,
          });
        } finally {
          await Promise.all(createdIds.map(async (id) => composedStrategyStore.delete(id)));
        }
      } catch (err) {
        json(
          res,
          { error: `AI 探索失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/compositions",
    handler: secure(async (req, res) => {
      if (!composedStrategyStore) {
        json(res, { error: "组合策略存储未初始化" }, 503);
        return;
      }

      if (req.method === "GET") {
        const compositions = composedStrategyStore.list();
        json(res, { ok: true, compositions });
        return;
      }

      if (req.method === "POST") {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const id = (body.id as string) ?? `composed-${Date.now().toString(36)}`;
        const name = body.name as string;
        const method = (body.method as CompositionMethod) ?? "voting";
        const strategies = body.strategies as SubStrategyConfig[];

        if (!name || !strategies || !Array.isArray(strategies) || strategies.length < 2) {
          json(res, { error: "需要 name 和至少 2 个 strategies" }, 400);
          return;
        }

        const comp: CompositionConfig = {
          id,
          name,
          description: body.description as string | undefined,
          method,
          strategies,
          minVotes: body.minVotes as number | undefined,
          scoreThreshold: body.scoreThreshold as number | undefined,
        };
        await composedStrategyStore.upsert(comp);
        json(res, { ok: true, composition: comp });
        return;
      }

      if (req.method === "DELETE") {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const id = body.id as string;
        if (!id) {
          json(res, { error: "请提供组合策略 id" }, 400);
          return;
        }
        const deleted = await composedStrategyStore.delete(id);
        json(res, deleted ? { ok: true } : { error: "未找到" }, deleted ? 200 : 404);
        return;
      }

      json(res, { error: "仅支持 GET/POST/DELETE" }, 405);
    }),
  });

  // ── Strategy Lab: Exploration History ────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-lab/explore-history",
    handler: secure(async (req, res) => {
      if (!explorationHistoryStore) {
        json(res, { error: "探索历史存储未初始化" }, 503);
        return;
      }

      if (req.method === "GET") {
        // ?id=xxx returns single record; otherwise list all
        const url = new URL(req.url ?? "/", "http://localhost");
        const id = url.searchParams.get("id");
        if (id) {
          const record = explorationHistoryStore.get(id);
          if (!record) {
            json(res, { error: "未找到" }, 404);
            return;
          }
          json(res, { ok: true, record });
          return;
        }
        const records = explorationHistoryStore.list();
        json(res, { ok: true, records });
        return;
      }

      if (req.method === "DELETE") {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const id = body.id as string;
        if (!id) {
          json(res, { error: "请提供探索记录 id" }, 400);
          return;
        }
        const deleted = await explorationHistoryStore.delete(id);
        json(res, deleted ? { ok: true } : { error: "未找到" }, deleted ? 200 : 404);
        return;
      }

      json(res, { error: "仅支持 GET/DELETE" }, 405);
    }),
  });

  // ── Strategy Lab: Favorite (save candidate as permanent composition) ──
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-lab/favorite",
    handler: secure(async (req, res) => {
      if (!composedStrategyStore) {
        json(res, { error: "组合策略存储未初始化" }, 503);
        return;
      }

      if (req.method === "POST") {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const name = body.name as string | undefined;
        const method = body.method as string | undefined;
        const strategies = body.strategies as
          | Array<{ strategyId: string; weight?: number; role?: string }>
          | undefined;

        if (!name || !method || !Array.isArray(strategies) || strategies.length < 2) {
          json(res, { error: "需要 name, method 和至少 2 个 strategies" }, 400);
          return;
        }
        const id = `fav-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 5)}`;
        const comp: CompositionConfig = {
          id,
          name,
          description: (body.rationale as string) ?? `收藏自策略探索`,
          method: method as CompositionMethod,
          strategies: strategies.map((s) => ({
            strategyId: s.strategyId,
            weight: s.weight ?? 1,
            role: (s.role as "primary" | "filter") ?? "primary",
          })),
          minVotes: body.minVotes as number | undefined,
          scoreThreshold: body.scoreThreshold as number | undefined,
        };
        await composedStrategyStore.upsert(comp);
        json(res, { ok: true, composition: comp });
        return;
      }

      json(res, { error: "仅支持 POST" }, 405);
    }),
  });

  // ── Strategy Lab: Batch Backtest ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/batch-backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const strategyIds = body.strategyIds as string[];
        const symbols = ((body.symbols as string) ?? "000300")
          .split(",")
          .map((s: string) => s.trim());
        const startDate =
          (body.startDate as string) ??
          (() => {
            const d = new Date();
            d.setFullYear(d.getFullYear() - 1);
            return d.toISOString().slice(0, 10);
          })();
        const endDate = (body.endDate as string) ?? todayCST();
        const initialCash = toFiniteNumber(body.initialCash, 100000);

        if (!Array.isArray(strategyIds) || strategyIds.length === 0) {
          json(res, { error: "请提供 strategyIds 数组" }, 400);
          return;
        }

        const items: BatchBacktestItem[] = strategyIds.map((sid: string) => ({
          label: sid,
          request: { strategyId: sid, symbols, startDate, endDate, initialCash },
        }));

        const summary = await runBatchBacktest(items, config, { maxConcurrency: 4 });
        json(res, { ok: true, ...summary });
      } catch (err) {
        json(
          res,
          { error: `批量回测失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });
}
