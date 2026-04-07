/**
 * Typed notification helper — safely calls `api.notify()` when available,
 * and fans out to multi-channel dispatchers (WeChat Work, Email, etc.).
 *
 * Works around SDK versions that may not have `notify` on the API interface.
 * All callers should use `tryNotify(api, msg)` or `tryNotifyWithMeta(api, msg, meta)`
 * instead of raw `api.notify`.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { NotificationDispatcher, NotificationMetadata } from "./notification-channel.js";

/** Module-level dispatcher reference, set once during plugin registration. */
let globalDispatcher: NotificationDispatcher | null = null;
/** Module-level logger reference for dispatcher error logging. */
let globalLogger: { warn: (msg: string) => void } | null = null;

/**
 * Set the global multi-channel dispatcher. Called once from plugin index.ts.
 */
export function setNotificationDispatcher(
  dispatcher: NotificationDispatcher,
  logger?: { warn: (msg: string) => void },
): void {
  globalDispatcher = dispatcher;
  globalLogger = logger ?? null;
}

/**
 * Best-effort notification delivery with metadata.
 * Sends to the chat channel (api.notify) AND all configured external channels.
 * Does nothing if `api.notify` is unavailable; swallows errors silently.
 */
export async function tryNotifyWithMeta(
  api: OpenClawPluginApi,
  message: string,
  metadata?: NotificationMetadata,
): Promise<void> {
  // Chat channel delivery (original behavior)
  try {
    const notifyFn = (api as Record<string, unknown>).notify;
    if (typeof notifyFn === "function") {
      await (notifyFn as (msg: string) => Promise<void>)(message);
    }
  } catch {
    // best-effort — notification channel may be unavailable
  }

  // Multi-channel fan-out
  if (globalDispatcher && globalDispatcher.channelCount > 0) {
    try {
      await globalDispatcher.dispatch(message, metadata, globalLogger ?? undefined);
    } catch {
      // best-effort — external channels may be unavailable
    }
  }
}

/**
 * Best-effort notification delivery (backward-compatible signature).
 * Does nothing if `api.notify` is unavailable; swallows errors silently.
 */
export async function tryNotify(api: OpenClawPluginApi, message: string): Promise<void> {
  return tryNotifyWithMeta(api, message);
}
