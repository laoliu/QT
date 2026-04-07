/**
 * Typed notification helper — safely calls `api.notify()` when available,
 * and pushes to external channels via NotificationService (Feishu, WeCom,
 * Telegram, Discord) when configured.
 *
 * All callers should use `tryNotify(api, msg)` instead of raw `api.notify`.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

import type { NotificationSeverity } from "../types.js";
import type { NotificationService } from "./notification-service.js";

/** Module-level singleton — set once at startup via `setNotificationService()`. */
let notificationService: NotificationService | null = null;

/**
 * Register the global NotificationService instance.
 * Called once from the extension entry point after reading config.
 */
export function setNotificationService(service: NotificationService): void {
  notificationService = service;
}

/** Get the current NotificationService (for tests / advanced usage). */
export function getNotificationService(): NotificationService | null {
  return notificationService;
}

/**
 * Infer severity from message content using emoji / keyword heuristics.
 * - 🔴 / ⚠️ / 严重 / 熔断 → critical
 * - 🟡 / ⚠ / 警告 / 预警 → warning
 * - everything else → info
 */
function inferSeverity(message: string): NotificationSeverity {
  if (/🔴|严重|熔断|critical|circuit.?breaker/i.test(message)) return "critical";
  if (/⚠️|🟡|⚠|警告|预警|warning/i.test(message)) return "warning";
  return "info";
}

/**
 * Best-effort notification delivery.
 * 1. Delivers via `api.notify` (in-app chat notification) if available.
 * 2. Pushes to external channels via NotificationService if configured.
 *
 * Does nothing if `api.notify` is unavailable; swallows errors silently.
 *
 * @param severity  Optional explicit severity; auto-inferred from message if omitted.
 */
export async function tryNotify(
  api: OpenClawPluginApi,
  message: string,
  severity?: NotificationSeverity,
): Promise<void> {
  // 1. In-app notification (original behaviour)
  try {
    const notifyFn = (api as Record<string, unknown>).notify;
    if (typeof notifyFn === "function") {
      await (notifyFn as (msg: string) => Promise<void>)(message);
    }
  } catch {
    // best-effort — notification channel may be unavailable
  }

  // 2. External push notification channels
  if (notificationService && notificationService.enabledChannelCount > 0) {
    try {
      const level = severity ?? inferSeverity(message);
      await notificationService.send(message, level);
    } catch {
      // best-effort — never let push failure break the caller
    }
  }
}
