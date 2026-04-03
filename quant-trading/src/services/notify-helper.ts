/**
 * Typed notification helper — safely calls `api.notify()` when available.
 *
 * Works around SDK versions that may not have `notify` on the API interface.
 * All callers should use `tryNotify(api, msg)` instead of raw `api.notify`.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

/**
 * Best-effort notification delivery.
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
