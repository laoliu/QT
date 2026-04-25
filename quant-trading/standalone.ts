#!/usr/bin/env node
/**
 * Standalone HTTP server for quant-trading — runs WITHOUT the OpenClaw gateway.
 *
 * Reads the plugin config from ~/.openclaw/openclaw.json (or $OPENCLAW_STATE_DIR),
 * bootstraps core services, registers all HTTP routes into a standalone Node.js
 * HTTP server, and starts listening.
 *
 * Usage:
 *   npx tsx quant-trading/standalone.ts [--port 18891]
 *   node --import tsx/esm quant-trading/standalone.ts [--port 18891]
 *
 * The server defaults to the `standalonePort` from plugin config, or 18891.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { createRouteCollector, startStandaloneServer } from "./src/http/standalone-server.js";

// ── Config Loading ────────────────────────────────────────────────

function loadOpenClawConfig(): Record<string, unknown> {
  const candidates = [
    process.env["OPENCLAW_STATE_DIR"]
      ? path.join(process.env["OPENCLAW_STATE_DIR"], "openclaw.json")
      : null,
    path.join(os.homedir(), ".openclaw-dev", "openclaw.json"),
    path.join(os.homedir(), ".openclaw", "openclaw.json"),
  ].filter(Boolean) as string[];

  for (const p of candidates) {
    try {
      const raw = fs.readFileSync(p, "utf-8");
      console.log(`[standalone] loaded config from ${p}`);
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      // try next
    }
  }
  console.warn("[standalone] no openclaw.json found, using defaults");
  return {};
}

function extractPluginConfig(root: Record<string, unknown>): Record<string, unknown> {
  const plugins = root["plugins"] as Record<string, unknown> | undefined;
  const entries = plugins?.["entries"] as Record<string, unknown> | undefined;
  const qt = entries?.["quant-trading"] as Record<string, unknown> | undefined;
  return (qt?.["config"] as Record<string, unknown>) ?? {};
}

// ── CLI Args ──────────────────────────────────────────────────────

function parsePort(args: string[], fallback: number): number {
  const idx = args.indexOf("--port");
  if (idx !== -1 && args[idx + 1]) {
    const n = Number(args[idx + 1]);
    if (Number.isFinite(n) && n > 0 && n < 65536) return n;
  }
  return fallback;
}

// ── Mock OpenClaw Plugin API ─────────────────────────────────────

type ServiceEntry = { id: string; start?: () => Promise<void>; stop?: () => Promise<void> };

function createMockApi(
  pluginConfig: Record<string, unknown>,
  routeCollector: ReturnType<typeof createRouteCollector>,
): { api: OpenClawPluginApi; services: ServiceEntry[] } {
  const services: ServiceEntry[] = [];

  const logger = {
    info: (msg: string) => console.log(`[info] ${msg}`),
    warn: (msg: string) => console.warn(`[warn] ${msg}`),
    error: (msg: string) => console.error(`[error] ${msg}`),
    debug: (msg: string) => console.debug(`[debug] ${msg}`),
  };

  const noop = () => {};

  // Minimal mock that satisfies the plugin's register() requirements
  const api = {
    id: "quant-trading",
    name: "Quant Trading",
    source: "standalone",
    registrationMode: "eager" as const,
    config: { gateway: {} } as unknown,
    pluginConfig,
    runtime: new Proxy({} as object, { get: () => () => Promise.reject(new Error("not available in standalone mode")) }),
    logger,
    registerHttpRoute: (params: { path: string; handler: unknown; match?: "exact" | "prefix" }) => {
      routeCollector.collect(params as Parameters<typeof routeCollector.collect>[0]);
    },
    registerTool: noop,
    registerHook: noop,
    registerChannel: noop,
    registerGatewayMethod: noop,
    registerCli: noop,
    registerReload: noop,
    registerNodeHostCommand: noop,
    registerSecurityAuditCollector: noop,
    registerService: (svc: { id: string; start?: () => Promise<void>; stop?: () => Promise<void> }) => {
      services.push(svc);
    },
    registerCliBackend: noop,
    registerConfigMigration: noop,
    registerAutoEnableProbe: noop,
    registerProvider: noop,
    registerSpeechProvider: noop,
    registerRealtimeTranscriptionProvider: noop,
    registerRealtimeVoiceProvider: noop,
    registerMediaUnderstandingProvider: noop,
    registerImageGenerationProvider: noop,
    registerVideoGenerationProvider: noop,
    registerMusicGenerationProvider: noop,
    registerWebFetchProvider: noop,
    registerWebSearchProvider: noop,
    registerInteractiveHandler: noop,
    onConversationBindingResolved: noop,
    registerCommand: noop,
    registerContextEngine: noop,
    registerCompactionProvider: noop,
    registerMemoryCapability: noop,
    registerMemoryPromptSection: noop,
    registerMemoryPromptSupplement: noop,
    registerMemoryCorpusSupplement: noop,
    registerMemoryFlushPlan: noop,
    registerMemoryRuntime: noop,
    registerMemoryEmbeddingProvider: noop,
    resolvePath: (input: string) => path.resolve(input),
    on: noop,
  } as unknown as OpenClawPluginApi;

  return { api, services };
}

// ── Crash Guard ───────────────────────────────────────────────────
// Prevent transient socket errors (write EOF, ECONNRESET, EPIPE, etc.)
// from crashing the whole process.
process.on("uncaughtException", (err: NodeJS.ErrnoException) => {
  const transient = ["EOF", "ECONNRESET", "EPIPE", "ECONNABORTED", "ETIMEDOUT"];
  if (err?.code && transient.includes(err.code)) {
    console.warn(`[standalone] transient error ignored: ${err.code} (${err.syscall ?? "unknown syscall"})`);
    return;
  }
  console.error("[standalone] uncaughtException:", err);
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  const err = reason as NodeJS.ErrnoException | undefined;
  const transient = ["EOF", "ECONNRESET", "EPIPE", "ECONNABORTED", "ETIMEDOUT"];
  if (err?.code && transient.includes(err.code)) {
    console.warn(`[standalone] transient rejection ignored: ${err.code}`);
    return;
  }
  console.error("[standalone] unhandledRejection:", reason);
});

// ── Main ──────────────────────────────────────────────────────────

async function main() {
  const rootConfig = loadOpenClawConfig();
  const pluginConfig = extractPluginConfig(rootConfig);
  const port = parsePort(process.argv.slice(2), Number(pluginConfig["standalonePort"]) || 18891);

  console.log(`[standalone] bootstrapping quant-trading plugin (port ${port})...`);

  const routeCollector = createRouteCollector();
  const { api, services } = createMockApi(pluginConfig, routeCollector);

  // Dynamically import the plugin and call register()
  const { default: plugin } = await import("./index.js");
  plugin.register(api);

  console.log(`[standalone] ${routeCollector.routes.length} routes collected`);

  // Start all registered services (TickIngress, Receiver, etc.)
  for (const svc of services) {
    if (svc.start) {
      try {
        await svc.start();
        console.log(`[standalone] service '${svc.id}' started`);
      } catch (err) {
        console.warn(`[standalone] service '${svc.id}' failed to start:`, err);
      }
    }
  }

  // Start the HTTP server
  startStandaloneServer(routeCollector.routes, port, {
    info: (msg) => console.log(msg),
    warn: (msg) => console.warn(msg),
    error: (msg) => console.error(msg),
  });

  // Graceful shutdown
  const shutdown = async () => {
    console.log("\n[standalone] shutting down...");
    for (const svc of services) {
      if (svc.stop) {
        try { await svc.stop(); } catch { /* ignore */ }
      }
    }
    const { stopStandaloneServer } = await import("./src/http/standalone-server.js");
    stopStandaloneServer();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((err) => {
  console.error("[standalone] fatal:", err);
  process.exit(1);
});
