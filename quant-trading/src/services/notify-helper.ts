/**
 * Typed notification helper — safely calls `api.notify()` when available,
 * and optionally forwards messages to external push channels via PushService.
 *
 * Works around SDK versions that may not have `notify` on the API interface.
 * All callers should use `tryNotify(api, msg)` instead of raw `api.notify`.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { PushService, PushSeverity } from "./push-service.js";

/** Singleton push service instance — set once at startup via `setPushService`. */
let pushServiceInstance: PushService | null = null;

/**
 * Register the global PushService instance.
 * Called once during plugin initialization in index.ts.
 */
export function setPushService(service: PushService): void {
  pushServiceInstance = service;
}

/**
 * Best-effort notification delivery (in-chat only).
 * Does nothing if `api.notify` is unavailable; swallows errors silently.
 */
export async function tryNotify(api: OpenClawPluginApi, message: string): Promise<void> {
  try {
    const notifyFn = (api as Record<string, unknown>).notify;
    if (typeof notifyFn === "function") {
      await (notifyFn as (msg: string) => Promise<void>)(message);
    }
  } catch {
    // best-effort — notification channel may be unavailable
  }
}

/**
 * Best-effort notification delivery — sends to BOTH in-chat (api.notify)
 * AND external push channels based on severity.
 *
 * @param api       OpenClaw plugin API
 * @param message   Notification text
 * @param severity  Event severity for push channel routing (default: "info")
 * @param title     Optional title for push notifications
 */
export async function tryNotifyWithPush(
  api: OpenClawPluginApi,
  message: string,
  severity: PushSeverity = "info",
  title?: string,
): Promise<void> {
  // In-chat delivery
  await tryNotify(api, message);

  // External push channels
  if (pushServiceInstance) {
    try {
      await pushServiceInstance.push(severity, message, title);
    } catch {
      // best-effort — push channel may be unavailable
    }
  }
}
