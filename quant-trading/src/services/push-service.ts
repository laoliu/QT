/**
 * PushService — multi-channel message push service.
 *
 * Supports:
 *   - 企业微信 Webhook (WeChat Work)
 *   - 钉钉机器人 Webhook (DingTalk with HMAC signature)
 *   - Bark (iOS push via GET)
 *   - Server酱 / PushPlus (generic webhook)
 *
 * Messages are routed to channels based on event severity (info/warning/critical).
 * All delivery is best-effort — failures are logged but never thrown.
 */

export type PushSeverity = "info" | "warning" | "critical";

// ── Channel config types ────────────────────────────────────────

export type WeWorkChannel = {
  type: "wework";
  /** 企业微信 Webhook URL (e.g. https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx) */
  webhookUrl: string;
  /** Event levels to send to this channel (default: all) */
  levels?: PushSeverity[];
};

export type DingTalkChannel = {
  type: "dingtalk";
  /** 钉钉机器人 Webhook URL */
  webhookUrl: string;
  /** 签名密钥 (SEC...) for HMAC-SHA256 signature verification */
  secret?: string;
  /** Event levels to send to this channel (default: all) */
  levels?: PushSeverity[];
};

export type BarkChannel = {
  type: "bark";
  /** Bark server URL (e.g. https://api.day.app/your-key) */
  serverUrl: string;
  /** Event levels to send to this channel (default: all) */
  levels?: PushSeverity[];
};

export type ServerChanChannel = {
  type: "serverchan";
  /** Server酱/PushPlus Webhook URL (e.g. https://sctapi.ftqq.com/xxx.send or https://www.pushplus.plus/send) */
  webhookUrl: string;
  /** Event levels to send to this channel (default: all) */
  levels?: PushSeverity[];
};

export type PushChannel = WeWorkChannel | DingTalkChannel | BarkChannel | ServerChanChannel;

export type PushChannelsConfig = {
  /** Configured push channels */
  channels?: PushChannel[];
  /** Whether push service is enabled (default: true when channels are configured) */
  enabled?: boolean;
};

// ── Logger interface (avoid importing OpenClawPluginApi) ─────────

interface PushLogger {
  info(msg: string): void;
  warn(msg: string): void;
  error(msg: string): void;
}

// ── HMAC helper for DingTalk ────────────────────────────────────

async function dingtalkSign(secret: string, timestamp: number): Promise<string> {
  const { createHmac } = await import("node:crypto");
  const stringToSign = `${timestamp}\n${secret}`;
  const hmac = createHmac("sha256", secret).update(stringToSign).digest("base64");
  return encodeURIComponent(hmac);
}

// ── Individual channel senders ──────────────────────────────────

async function sendWeWork(channel: WeWorkChannel, title: string, body: string): Promise<void> {
  const content = title ? `${title}\n${body}` : body;
  await fetch(channel.webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      msgtype: "text",
      text: { content },
    }),
  });
}

async function sendDingTalk(channel: DingTalkChannel, title: string, body: string): Promise<void> {
  let url = channel.webhookUrl;
  if (channel.secret) {
    const timestamp = Date.now();
    const sign = await dingtalkSign(channel.secret, timestamp);
    const sep = url.includes("?") ? "&" : "?";
    url = `${url}${sep}timestamp=${timestamp}&sign=${sign}`;
  }
  const content = title ? `${title}\n${body}` : body;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      msgtype: "text",
      text: { content },
    }),
  });
}

async function sendBark(channel: BarkChannel, title: string, body: string): Promise<void> {
  const baseUrl = channel.serverUrl.replace(/\/+$/, "");
  const encodedTitle = encodeURIComponent(title || "Quant Trading");
  const encodedBody = encodeURIComponent(body);
  await fetch(`${baseUrl}/${encodedTitle}/${encodedBody}`);
}

async function sendServerChan(channel: ServerChanChannel, title: string, body: string): Promise<void> {
  await fetch(channel.webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: title || "Quant Trading",
      desp: body,
    }),
  });
}

// ── PushService class ───────────────────────────────────────────

const SEVERITY_PREFIX: Record<PushSeverity, string> = {
  info: "ℹ️",
  warning: "⚠️",
  critical: "🚨",
};

export class PushService {
  private channels: PushChannel[] = [];
  private enabled = false;
  private logger: PushLogger;

  constructor(config: PushChannelsConfig | undefined, logger: PushLogger) {
    this.logger = logger;
    if (!config) return;
    this.channels = config.channels ?? [];
    this.enabled = config.enabled !== false && this.channels.length > 0;
    if (this.enabled) {
      logger.info(`PushService: initialized with ${this.channels.length} channel(s)`);
    }
  }

  /** Push a message to all channels matching the given severity level. */
  async push(severity: PushSeverity, message: string, title?: string): Promise<void> {
    if (!this.enabled) return;

    const effectiveTitle = title
      ? `${SEVERITY_PREFIX[severity]} ${title}`
      : `${SEVERITY_PREFIX[severity]} Quant Trading`;

    const matching = this.channels.filter((ch) => {
      const levels = ch.levels;
      if (!levels || levels.length === 0) return true;
      return levels.includes(severity);
    });

    if (matching.length === 0) return;

    const results = await Promise.allSettled(
      matching.map((ch) => this.sendToChannel(ch, effectiveTitle, message)),
    );

    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      if (result.status === "rejected") {
        const ch = matching[i];
        this.logger.warn(
          `PushService: failed to send to ${ch.type}: ${(result.reason as Error)?.message ?? result.reason}`,
        );
      }
    }
  }

  /** Whether push service has any active channels. */
  get isEnabled(): boolean {
    return this.enabled;
  }

  private async sendToChannel(channel: PushChannel, title: string, body: string): Promise<void> {
    switch (channel.type) {
      case "wework":
        return sendWeWork(channel, title, body);
      case "dingtalk":
        return sendDingTalk(channel, title, body);
      case "bark":
        return sendBark(channel, title, body);
      case "serverchan":
        return sendServerChan(channel, title, body);
    }
  }
}
