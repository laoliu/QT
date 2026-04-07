/**
 * NotificationService — unified push notification gateway.
 *
 * Delivers alert messages to external messaging channels:
 *   - Feishu (飞书) Webhook
 *   - WeCom (企业微信) Webhook
 *   - Telegram Bot API
 *   - Discord Webhook
 *
 * Supports quiet hours (静默时段) to suppress non-critical notifications
 * during off-market hours or sleep time.
 *
 * Usage:
 *   const ns = new NotificationService(config.notifications);
 *   await ns.send("⚠️ 价格预警: 600519 跌破 ¥1800", "warning");
 */

import type {
  NotificationChannelConfig,
  NotificationConfig,
  NotificationSeverity,
  QuietHoursConfig,
} from "../types.js";

// ── Channel sender interface ────────────────────────────────────

interface ChannelSendResult {
  channel: string;
  ok: boolean;
  error?: string;
}

// ── NotificationService ─────────────────────────────────────────

export class NotificationService {
  private channels: NotificationChannelConfig[];
  private quietHours: QuietHoursConfig;

  constructor(config?: NotificationConfig) {
    this.channels = config?.channels ?? [];
    this.quietHours = config?.quietHours ?? {};
  }

  /** Update configuration at runtime (e.g. after config reload). */
  updateConfig(config: NotificationConfig): void {
    this.channels = config.channels ?? [];
    this.quietHours = config.quietHours ?? {};
  }

  /** Number of enabled channels. */
  get enabledChannelCount(): number {
    return this.channels.filter((ch) => ch.enabled !== false).length;
  }

  /**
   * Send a notification message to all enabled channels.
   * Respects quiet hours — non-bypass severity messages are silently dropped.
   *
   * @returns Array of per-channel results (empty if suppressed by quiet hours).
   */
  async send(
    message: string,
    severity: NotificationSeverity = "info",
    metadata?: Record<string, unknown>,
  ): Promise<ChannelSendResult[]> {
    if (this.isQuietHour(severity)) {
      return [];
    }

    const enabledChannels = this.channels.filter((ch) => ch.enabled !== false);
    if (enabledChannels.length === 0) return [];

    const results = await Promise.allSettled(
      enabledChannels.map((ch) => this.sendToChannel(ch, message, severity, metadata)),
    );

    return results.map((r, i) => {
      const ch = enabledChannels[i]!;
      const name = ch.name ?? ch.type;
      if (r.status === "fulfilled") return r.value;
      return { channel: name, ok: false, error: (r.reason as Error).message };
    });
  }

  // ── Quiet hours logic ───────────────────────────────────────

  private isQuietHour(severity: NotificationSeverity): boolean {
    if (!this.quietHours.enabled) return false;

    const bypassLevels = this.quietHours.bypassLevels ?? ["critical"];
    if (bypassLevels.includes(severity)) return false;

    const start = this.quietHours.start;
    const end = this.quietHours.end;
    if (!start || !end) return false;

    const now = new Date();
    const currentMinutes = now.getHours() * 60 + now.getMinutes();
    const startMinutes = this.parseHHMM(start);
    const endMinutes = this.parseHHMM(end);

    if (startMinutes === -1 || endMinutes === -1) return false;

    // Handle overnight range (e.g. 21:00 → 09:00)
    if (startMinutes > endMinutes) {
      return currentMinutes >= startMinutes || currentMinutes < endMinutes;
    }
    // Same-day range (e.g. 12:00 → 13:00)
    return currentMinutes >= startMinutes && currentMinutes < endMinutes;
  }

  private parseHHMM(time: string): number {
    const parts = time.split(":");
    if (parts.length !== 2) return -1;
    const h = Number(parts[0]);
    const m = Number(parts[1]);
    if (Number.isNaN(h) || Number.isNaN(m) || h < 0 || h > 23 || m < 0 || m > 59) return -1;
    return h * 60 + m;
  }

  // ── Per-channel dispatch ────────────────────────────────────

  private async sendToChannel(
    ch: NotificationChannelConfig,
    message: string,
    severity: NotificationSeverity,
    metadata?: Record<string, unknown>,
  ): Promise<ChannelSendResult> {
    const name = ch.name ?? ch.type;
    try {
      switch (ch.type) {
        case "feishu":
          await this.sendFeishu(ch, message, severity);
          break;
        case "wecom":
          await this.sendWeCom(ch, message, severity);
          break;
        case "telegram":
          await this.sendTelegram(ch, message, severity, metadata);
          break;
        case "discord":
          await this.sendDiscord(ch, message, severity);
          break;
        default:
          return { channel: name, ok: false, error: `unknown channel type: ${ch.type as string}` };
      }
      return { channel: name, ok: true };
    } catch (err) {
      return { channel: name, ok: false, error: (err as Error).message };
    }
  }

  // ── Feishu (飞书) ───────────────────────────────────────────

  private async sendFeishu(
    ch: NotificationChannelConfig,
    message: string,
    severity: NotificationSeverity,
  ): Promise<void> {
    const severityTag = severity === "critical" ? "🔴" : severity === "warning" ? "🟡" : "🟢";
    const body: Record<string, unknown> = {
      msg_type: "interactive",
      card: {
        header: {
          title: { tag: "plain_text", content: `${severityTag} 量化交易通知` },
          template: severity === "critical" ? "red" : severity === "warning" ? "orange" : "blue",
        },
        elements: [
          {
            tag: "markdown",
            content: message,
          },
        ],
      },
    };

    // Feishu v2 signature if secret is set
    if (ch.secret) {
      const timestamp = Math.floor(Date.now() / 1000);
      const sign = await this.feishuSign(timestamp, ch.secret);
      body.timestamp = String(timestamp);
      body.sign = sign;
    }

    await this.httpPost(ch.webhookUrl, body);
  }

  private async feishuSign(timestamp: number, secret: string): Promise<string> {
    const stringToSign = `${String(timestamp)}\n${secret}`;
    // Use Web Crypto API (available in Node 18+)
    const { createHmac } = await import("node:crypto");
    const hmac = createHmac("sha256", stringToSign);
    hmac.update("");
    return hmac.digest("base64");
  }

  // ── WeCom (企业微信) ────────────────────────────────────────

  private async sendWeCom(
    ch: NotificationChannelConfig,
    message: string,
    severity: NotificationSeverity,
  ): Promise<void> {
    const severityTag = severity === "critical" ? "🔴" : severity === "warning" ? "🟡" : "🟢";
    const body = {
      msgtype: "markdown",
      markdown: {
        content: `${severityTag} **量化交易通知**\n\n${message}`,
      },
    };

    await this.httpPost(ch.webhookUrl, body);
  }

  // ── Telegram ────────────────────────────────────────────────

  private async sendTelegram(
    ch: NotificationChannelConfig,
    message: string,
    severity: NotificationSeverity,
    _metadata?: Record<string, unknown>,
  ): Promise<void> {
    const severityTag = severity === "critical" ? "🔴" : severity === "warning" ? "🟡" : "🟢";
    // webhookUrl format: https://api.telegram.org/bot<token>/sendMessage?chat_id=<chat_id>
    const url = new URL(ch.webhookUrl);
    const chatId = url.searchParams.get("chat_id");
    // Strip chat_id from URL to build the endpoint
    const baseUrl = `${url.origin}${url.pathname}`;

    const body: Record<string, unknown> = {
      chat_id: chatId,
      text: `${severityTag} *量化交易通知*\n\n${message}`,
      parse_mode: "Markdown",
    };

    await this.httpPost(baseUrl, body);
  }

  // ── Discord ─────────────────────────────────────────────────

  private async sendDiscord(
    ch: NotificationChannelConfig,
    message: string,
    severity: NotificationSeverity,
  ): Promise<void> {
    const color =
      severity === "critical" ? 0xff0000 : severity === "warning" ? 0xffa500 : 0x3498db;

    const body = {
      embeds: [
        {
          title: "量化交易通知",
          description: message,
          color,
          timestamp: new Date().toISOString(),
        },
      ],
    };

    await this.httpPost(ch.webhookUrl, body);
  }

  // ── HTTP helper ─────────────────────────────────────────────

  private async httpPost(url: string, body: unknown): Promise<void> {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`HTTP ${String(response.status)}: ${text.slice(0, 200)}`);
    }
  }
}
