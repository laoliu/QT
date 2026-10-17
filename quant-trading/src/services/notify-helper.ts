/**
 * Typed notification helper — safely calls `api.notify()` when available,
 * and pushes messages to external webhook channels (企业微信 / 钉钉).
 *
 * Works around SDK versions that may not have `notify` on the API interface.
 * All callers should use `tryNotify(api, msg)` instead of raw `api.notify`.
 */
import type { QuantTradingRuntime } from "../runtime.js";
import type { NotificationChannel } from "../types.js";

// ── Channel registry (set once at startup) ───────────────────────

let registeredChannels: NotificationChannel[] = [];

/**
 * Register external notification channels.
 * Called once during plugin startup with the config's `notificationChannels`.
 */
export function registerNotificationChannels(channels: NotificationChannel[] | undefined): void {
  registeredChannels = (channels ?? []).filter((ch) => ch.enabled !== false);
}

/**
 * Best-effort notification delivery with severity level and title.
 * Sends via `api.notify` (if available) AND all registered webhook channels.
 * Adds a severity prefix for webhook channels.
 * Swallows errors silently — notification should never break business logic.
 */
export async function tryNotifyWithPush(
  api: QuantTradingRuntime,
  message: string,
  level: "info" | "warning" | "critical" = "info",
  title?: string,
): Promise<void> {
  const prefix = title ? `【${title}】` : "";
  const fullMessage = `${prefix}${message}`;
  await tryNotify(api, fullMessage);
}

/**
 * Best-effort notification delivery.
 * Sends via `api.notify` (if available) AND all registered webhook channels.
 * Swallows errors silently — notification should never break business logic.
 */
export async function tryNotify(api: QuantTradingRuntime, message: string): Promise<void> {
  // 1. SDK notify (in-app)
  try {
    const notifyFn = (api as Record<string, unknown>).notify;
    if (typeof notifyFn === "function") {
      await (notifyFn as (msg: string) => Promise<void>)(message);
    }
  } catch {
    // best-effort — notification channel may be unavailable
  }

  // 2. External webhook channels
  if (registeredChannels.length > 0) {
    await pushToChannels(message);
  }
}

// ── Webhook push ─────────────────────────────────────────────────

/**
 * Push a message to all registered external channels in parallel.
 * Each channel failure is independent — one failing does not block others.
 */
async function pushToChannels(message: string): Promise<void> {
  const tasks = registeredChannels.map((ch) => pushToChannel(ch, message));
  await Promise.allSettled(tasks);
}

async function pushToChannel(channel: NotificationChannel, message: string): Promise<void> {
  try {
    const body = buildWebhookBody(channel.type, message);
    const resp = await fetch(channel.webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      // Log but don't throw — best effort
      const text = await resp.text().catch(() => "");
      console.warn(
        `[notify] ${channel.type}${channel.name ? ` (${channel.name})` : ""} webhook failed: ${resp.status} ${text.slice(0, 200)}`,
      );
    }
  } catch (err) {
    console.warn(
      `[notify] ${channel.type}${channel.name ? ` (${channel.name})` : ""} webhook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

/**
 * Build the JSON body for a webhook request based on channel type.
 *
 * - WeCom (企业微信): uses markdown msgtype
 *   https://developer.work.weixin.qq.com/document/path/91770
 *
 * - DingTalk (钉钉): uses markdown msgtype
 *   https://open.dingtalk.com/document/orgapp/custom-robot-access
 */
function buildWebhookBody(
  type: NotificationChannel["type"],
  message: string,
): Record<string, unknown> {
  switch (type) {
    case "wecom":
      return {
        msgtype: "markdown",
        markdown: {
          content: message,
        },
      };
    case "dingtalk":
      return {
        msgtype: "markdown",
        markdown: {
          title: "量化交易通知",
          text: message,
        },
      };
    default:
      // Fallback — treat unknown types as simple text POST
      return { text: message };
  }
}
