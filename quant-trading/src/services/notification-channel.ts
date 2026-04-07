/**
 * Multi-channel notification system.
 *
 * Abstracts notification delivery into pluggable channels so alerts can be
 * fan-out to WeChat Work (企业微信), Email (SMTP), and the original
 * chat-channel simultaneously.
 */

/** Alert types that can trigger notifications. */
export type AlertType =
  | "signal"
  | "risk"
  | "pnl"
  | "price_alert"
  | "connection"
  | "data_staleness"
  | "order"
  | "engine"
  | "system";

/** Metadata attached to a notification message. */
export interface NotificationMetadata {
  alertType?: AlertType;
  severity?: "info" | "warning" | "critical";
  symbol?: string;
  [key: string]: unknown;
}

/** A single notification delivery channel. */
export interface NotificationChannel {
  /** Unique channel type identifier. */
  readonly type: string;
  /** Human-readable name for logging. */
  readonly name: string;
  /** Send a message through this channel. Returns true on success. */
  send(message: string, metadata?: NotificationMetadata): Promise<boolean>;
}

// ── WeChat Work Channel (企业微信机器人 Webhook) ────────────────────

export interface WeChatWorkConfig {
  /** Webhook URL, e.g. https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx */
  webhookUrl: string;
  /** Optional mentioned user list (企业微信 userId) */
  mentionedList?: string[];
  /** Optional mentioned mobile list */
  mentionedMobileList?: string[];
}

export class WeChatWorkChannel implements NotificationChannel {
  readonly type = "wechat_work";
  readonly name = "企业微信";

  constructor(private readonly config: WeChatWorkConfig) {}

  async send(message: string, metadata?: NotificationMetadata): Promise<boolean> {
    try {
      const severityTag = metadata?.severity ? `[${metadata.severity.toUpperCase()}] ` : "";
      const content = `${severityTag}${message}`;
      const body: Record<string, unknown> = {
        msgtype: "text",
        text: {
          content,
          mentioned_list: this.config.mentionedList,
          mentioned_mobile_list: this.config.mentionedMobileList,
        },
      };

      const resp = await fetch(this.config.webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(10_000),
      });

      if (!resp.ok) return false;
      const result = (await resp.json()) as { errcode?: number };
      return result.errcode === 0;
    } catch {
      return false;
    }
  }
}

// ── Email Channel (SMTP) ───────────────────────────────────────────

export interface EmailChannelConfig {
  /** SMTP host, e.g. smtp.qq.com */
  smtpHost: string;
  /** SMTP port, default 465 */
  smtpPort?: number;
  /** Use TLS/SSL, default true */
  secure?: boolean;
  /** SMTP auth user (usually the email address) */
  user: string;
  /** SMTP auth password or app-specific password */
  pass: string;
  /** Sender address (defaults to user) */
  from?: string;
  /** Recipient address(es) */
  to: string | string[];
  /** Email subject prefix, default "[量化交易预警]" */
  subjectPrefix?: string;
}

export class EmailChannel implements NotificationChannel {
  readonly type = "email";
  readonly name = "邮件";

  constructor(private readonly config: EmailChannelConfig) {}

  async send(message: string, metadata?: NotificationMetadata): Promise<boolean> {
    try {
      // Dynamic import nodemailer — optional dependency.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const nodemailer = (await import("nodemailer" as string)) as {
        createTransport: (opts: Record<string, unknown>) => {
          sendMail: (mail: Record<string, unknown>) => Promise<unknown>;
        };
      };

      const transporter = nodemailer.createTransport({
        host: this.config.smtpHost,
        port: this.config.smtpPort ?? 465,
        secure: this.config.secure !== false,
        auth: {
          user: this.config.user,
          pass: this.config.pass,
        },
      });

      const prefix = this.config.subjectPrefix ?? "[量化交易预警]";
      const severityTag = metadata?.severity ? `[${metadata.severity.toUpperCase()}]` : "";
      const subject = `${prefix} ${severityTag} ${message.slice(0, 80)}`;
      const to = Array.isArray(this.config.to) ? this.config.to.join(",") : this.config.to;

      await transporter.sendMail({
        from: this.config.from ?? this.config.user,
        to,
        subject,
        text: message,
      });

      return true;
    } catch {
      return false;
    }
  }
}

// ── Multi-Channel Dispatcher ───────────────────────────────────────

export interface ChannelRegistration {
  channel: NotificationChannel;
  /** Alert types this channel should receive. Empty/undefined = all types. */
  enabledAlertTypes?: AlertType[];
}

export class NotificationDispatcher {
  private channels: ChannelRegistration[] = [];

  /** Register a notification channel. */
  addChannel(channel: NotificationChannel, enabledAlertTypes?: AlertType[]): void {
    this.channels.push({ channel, enabledAlertTypes });
  }

  /** Get count of registered channels. */
  get channelCount(): number {
    return this.channels.length;
  }

  /**
   * Fan-out a message to all channels that accept the given alert type.
   * Best-effort: failures are logged but do not throw.
   */
  async dispatch(
    message: string,
    metadata?: NotificationMetadata,
    logger?: { warn: (msg: string) => void },
  ): Promise<void> {
    const alertType = metadata?.alertType;
    const targets = this.channels.filter((reg) => {
      if (!reg.enabledAlertTypes || reg.enabledAlertTypes.length === 0) return true;
      return alertType ? reg.enabledAlertTypes.includes(alertType) : true;
    });

    const results = await Promise.allSettled(
      targets.map((reg) => reg.channel.send(message, metadata)),
    );

    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      const ch = targets[i].channel;
      if (result.status === "rejected") {
        logger?.warn(`[notification] ${ch.name} (${ch.type}) delivery failed: ${result.reason}`);
      } else if (!result.value) {
        logger?.warn(`[notification] ${ch.name} (${ch.type}) delivery returned false`);
      }
    }
  }
}

// ── Factory: build channels from config ────────────────────────────

export interface NotificationChannelConfigItem {
  type: "wechat_work" | "email";
  enabled?: boolean;
  enabledAlertTypes?: AlertType[];
  config: WeChatWorkConfig | EmailChannelConfig;
}

/**
 * Build a NotificationDispatcher from an array of channel configurations.
 */
export function createDispatcherFromConfig(
  channelConfigs: NotificationChannelConfigItem[],
): NotificationDispatcher {
  const dispatcher = new NotificationDispatcher();

  for (const item of channelConfigs) {
    if (item.enabled === false) continue;

    let channel: NotificationChannel | null = null;
    switch (item.type) {
      case "wechat_work":
        channel = new WeChatWorkChannel(item.config as WeChatWorkConfig);
        break;
      case "email":
        channel = new EmailChannel(item.config as EmailChannelConfig);
        break;
    }

    if (channel) {
      dispatcher.addChannel(channel, item.enabledAlertTypes);
    }
  }

  return dispatcher;
}
