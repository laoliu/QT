/**
 * NotificationService — 多渠道消息推送服务
 *
 * 0100:
 *  - 0901 (wechat_work) — Webhook 机器人
 *  - 钉钉 (dingtalk) — Webhook 机器人
 *  - Bark (bark) — iOS 推送
 *  - PushPlus (pushplus) — 010116
 *  - 00 Webhook (webhook) — 08 HTTP POST
 */

// ── Types ────────────────────────────────────────────────────

export type NotificationChannelType = "wechat_work" | "dingtalk" | "bark" | "pushplus" | "webhook";

export interface NotificationMessage {
  title: string;
  content: string;
  severity: "info" | "warning" | "critical";
  category: "trade" | "alert" | "report" | "system";
  /** Optional markdown content (for channels that support it) */
  markdown?: string;
}

export interface NotificationChannelConfig {
  type: NotificationChannelType;
  enabled: boolean;
  /** Webhook URL or endpoint */
  url: string;
  /** Optional display name */
  name?: string;
  /** Optional extra config (e.g. secret for DingTalk sign) */
  secret?: string;
  /** Optional token (for pushplus, bark) */
  token?: string;
  /** Filter: only send messages matching these categories */
  categories?: NotificationMessage["category"][];
  /** Filter: minimum severity to trigger */
  minSeverity?: NotificationMessage["severity"];
}

export interface NotificationResult {
  channel: string;
  success: boolean;
  error?: string;
}

// ── Severity Ordering ───────────────────────────────────────

const SEVERITY_ORDER: Record<string, number> = { info: 0, warning: 1, critical: 2 };

// ── Channel Implementations ──────────────────────────────────

async function sendWechatWork(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
  const body = {
    msgtype: "markdown",
    markdown: {
      content: `### ${severityEmoji(msg.severity)} ${msg.title}\n\n${msg.markdown || msg.content}`,
    },
  };
  const res = await fetch(cfg.url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const data = (await res.json()) as { errcode?: number };
  return data.errcode === 0;
}

async function sendDingtalk(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
  let url = cfg.url;

  // Sign if secret is configured
  if (cfg.secret) {
    const timestamp = Date.now();
    const stringToSign = `${timestamp}\n${cfg.secret}`;
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(cfg.secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(stringToSign));
    const base64Sig = btoa(String.fromCharCode(...new Uint8Array(sig)));
    const sign = encodeURIComponent(base64Sig);
    url += `&timestamp=${timestamp}&sign=${sign}`;
  }

  const body = {
    msgtype: "markdown",
    markdown: {
      title: msg.title,
      text: `### ${severityEmoji(msg.severity)} ${msg.title}\n\n${msg.markdown || msg.content}`,
    },
  };
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const data = (await res.json()) as { errcode?: number };
  return data.errcode === 0;
}

async function sendBark(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
  const token = cfg.token ?? "";
  const baseUrl = cfg.url.endsWith("/") ? cfg.url : `${cfg.url}/`;
  const url = `${baseUrl}${encodeURIComponent(token)}/${encodeURIComponent(msg.title)}/${encodeURIComponent(msg.content)}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  return res.ok;
}

async function sendPushPlus(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
  const body = {
    token: cfg.token ?? "",
    title: `${severityEmoji(msg.severity)} ${msg.title}`,
    content: msg.markdown || msg.content,
    template: "markdown",
  };
  const res = await fetch("https://www.pushplus.plus/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  const data = (await res.json()) as { code?: number };
  return data.code === 200;
}

async function sendWebhook(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
  const body = {
    title: msg.title,
    content: msg.content,
    severity: msg.severity,
    category: msg.category,
    timestamp: new Date().toISOString(),
  };
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (cfg.secret) {
    headers["X-Webhook-Secret"] = cfg.secret;
  }
  const res = await fetch(cfg.url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  return res.ok;
}

function severityEmoji(severity: string): string {
  switch (severity) {
    case "critical": return "92";
    case "warning": return "7215";
    default: return "6715";
  }
}

// ── Main Service ─────────────────────────────────────────────

export class NotificationService {
  private channels: NotificationChannelConfig[] = [];
  private sendHistory: Array<{ time: string; channel: string; title: string; success: boolean }> = [];
  private readonly maxHistory = 200;

  constructor(channels?: NotificationChannelConfig[]) {
    if (channels) this.channels = channels;
  }

  /** Add or update a notification channel */
  addChannel(channel: NotificationChannelConfig): void {
    const idx = this.channels.findIndex(
      (c) => c.type === channel.type && c.url === channel.url,
    );
    if (idx >= 0) {
      this.channels[idx] = channel;
    } else {
      this.channels.push(channel);
    }
  }

  /** Remove a notification channel by type and URL */
  removeChannel(type: NotificationChannelType, url?: string): boolean {
    const idx = this.channels.findIndex(
      (c) => c.type === type && (!url || c.url === url),
    );
    if (idx < 0) return false;
    this.channels.splice(idx, 1);
    return true;
  }

  /** Get all configured channels */
  getChannels(): NotificationChannelConfig[] {
    return [...this.channels];
  }

  /** Get recent send history */
  getHistory(): typeof this.sendHistory {
    return [...this.sendHistory];
  }

  /** Send a notification to all matching channels */
  async send(msg: NotificationMessage): Promise<NotificationResult[]> {
    const results: NotificationResult[] = [];

    for (const channel of this.channels) {
      if (!channel.enabled) continue;

      // Category filter
      if (channel.categories?.length && !channel.categories.includes(msg.category)) {
        continue;
      }

      // Severity filter
      if (channel.minSeverity) {
        if (SEVERITY_ORDER[msg.severity] < SEVERITY_ORDER[channel.minSeverity]) {
          continue;
        }
      }

      const channelName = channel.name ?? channel.type;
      try {
        const success = await this.sendToChannel(channel, msg);
        results.push({ channel: channelName, success });
        this.recordHistory(channelName, msg.title, success);
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : String(err);
        results.push({ channel: channelName, success: false, error: errorMsg });
        this.recordHistory(channelName, msg.title, false);
      }
    }

    return results;
  }

  /** Send a notification to a specific channel type */
  private async sendToChannel(cfg: NotificationChannelConfig, msg: NotificationMessage): Promise<boolean> {
    switch (cfg.type) {
      case "wechat_work": return sendWechatWork(cfg, msg);
      case "dingtalk": return sendDingtalk(cfg, msg);
      case "bark": return sendBark(cfg, msg);
      case "pushplus": return sendPushPlus(cfg, msg);
      case "webhook": return sendWebhook(cfg, msg);
      default: return false;
    }
  }

  private recordHistory(channel: string, title: string, success: boolean): void {
    this.sendHistory.push({
      time: new Date().toISOString(),
      channel,
      title,
      success,
    });
    if (this.sendHistory.length > this.maxHistory) {
      this.sendHistory = this.sendHistory.slice(-this.maxHistory);
    }
  }
}
