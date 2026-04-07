/**
 * MultiChannelNotifier — multi-channel push notification service.
 *
 * Supports:
 *   - Default: OpenClaw api.notify (chat session)
 *   - Bark (iOS push)
 *   - WeCom / 企业微信 webhook
 *   - DingTalk / 钉钉 webhook
 *   - Feishu / 飞书 webhook
 *   - Custom webhook (generic HTTP POST)
 *
 * Falls back to api.notify if no external channels are configured.
 * All channels are best-effort — failures are logged but not thrown.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

export type NotifyChannel =
  | "chat"
  | "bark"
  | "wecom"
  | "dingtalk"
  | "feishu"
  | "webhook";

export interface NotifyChannelConfig {
  /** Bark server URL (e.g. https://api.day.app/YOUR_KEY). */
  barkUrl?: string;
  /** WeCom (企业微信) webhook URL. */
  wecomWebhookUrl?: string;
  /** DingTalk (钉钉) webhook URL. */
  dingtalkWebhookUrl?: string;
  /** Feishu (飞书) webhook URL. */
  feishuWebhookUrl?: string;
  /** Custom webhook URL (receives JSON POST with { text, title, timestamp }). */
  customWebhookUrl?: string;
  /** Enabled channels (default: ["chat"]). */
  enabledChannels?: NotifyChannel[];
}

export class MultiChannelNotifier {
  private api: OpenClawPluginApi;
  private config: NotifyChannelConfig;
  private enabledChannels: Set<NotifyChannel>;

  constructor(api: OpenClawPluginApi, config: NotifyChannelConfig = {}) {
    this.api = api;
    this.config = config;
    this.enabledChannels = new Set(config.enabledChannels ?? ["chat"]);
  }

  /** Update channel configuration. */
  updateConfig(config: Partial<NotifyChannelConfig>): void {
    Object.assign(this.config, config);
    if (config.enabledChannels) {
      this.enabledChannels = new Set(config.enabledChannels);
    }
  }

  /** Get current configuration status. */
  getStatus(): {
    enabledChannels: NotifyChannel[];
    configuredChannels: NotifyChannel[];
  } {
    const configured: NotifyChannel[] = ["chat"];
    if (this.config.barkUrl) configured.push("bark");
    if (this.config.wecomWebhookUrl) configured.push("wecom");
    if (this.config.dingtalkWebhookUrl) configured.push("dingtalk");
    if (this.config.feishuWebhookUrl) configured.push("feishu");
    if (this.config.customWebhookUrl) configured.push("webhook");

    return {
      enabledChannels: [...this.enabledChannels],
      configuredChannels: configured,
    };
  }

  /** Send a notification to all enabled channels. */
  async notify(message: string, title?: string): Promise<{
    results: Array<{ channel: NotifyChannel; success: boolean; error?: string }>;
  }> {
    const results: Array<{ channel: NotifyChannel; success: boolean; error?: string }> = [];
    const promises: Promise<void>[] = [];

    for (const channel of this.enabledChannels) {
      promises.push(
        this.sendToChannel(channel, message, title).then(
          () => {
            results.push({ channel, success: true });
          },
          (err: Error) => {
            results.push({ channel, success: false, error: err.message });
          },
        ),
      );
    }

    await Promise.allSettled(promises);
    return { results };
  }

  private async sendToChannel(
    channel: NotifyChannel,
    message: string,
    title?: string,
  ): Promise<void> {
    switch (channel) {
      case "chat":
        return this.sendChat(message);
      case "bark":
        return this.sendBark(message, title);
      case "wecom":
        return this.sendWeCom(message);
      case "dingtalk":
        return this.sendDingTalk(message, title);
      case "feishu":
        return this.sendFeishu(message, title);
      case "webhook":
        return this.sendWebhook(message, title);
    }
  }

  private async sendChat(message: string): Promise<void> {
    try {
      const notifyFn = (this.api as Record<string, unknown>).notify;
      if (typeof notifyFn === "function") {
        await (notifyFn as (msg: string) => Promise<void>)(message);
      }
    } catch {
      // best-effort
    }
  }

  private async sendBark(message: string, title?: string): Promise<void> {
    const url = this.config.barkUrl;
    if (!url) throw new Error("Bark URL not configured");
    const body = {
      title: title ?? "量化交易提醒",
      body: message,
      group: "quant-trading",
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`Bark: HTTP ${resp.status}`);
  }

  private async sendWeCom(message: string): Promise<void> {
    const url = this.config.wecomWebhookUrl;
    if (!url) throw new Error("WeCom webhook URL not configured");
    const body = {
      msgtype: "text",
      text: { content: message },
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`WeCom: HTTP ${resp.status}`);
  }

  private async sendDingTalk(message: string, title?: string): Promise<void> {
    const url = this.config.dingtalkWebhookUrl;
    if (!url) throw new Error("DingTalk webhook URL not configured");
    const body = {
      msgtype: "markdown",
      markdown: {
        title: title ?? "量化交易提醒",
        text: message,
      },
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`DingTalk: HTTP ${resp.status}`);
  }

  private async sendFeishu(message: string, title?: string): Promise<void> {
    const url = this.config.feishuWebhookUrl;
    if (!url) throw new Error("Feishu webhook URL not configured");
    const body = {
      msg_type: "interactive",
      card: {
        header: {
          title: { tag: "plain_text", content: title ?? "量化交易提醒" },
        },
        elements: [
          {
            tag: "div",
            text: { tag: "lark_md", content: message },
          },
        ],
      },
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`Feishu: HTTP ${resp.status}`);
  }

  private async sendWebhook(message: string, title?: string): Promise<void> {
    const url = this.config.customWebhookUrl;
    if (!url) throw new Error("Custom webhook URL not configured");
    const body = {
      text: message,
      title: title ?? "量化交易提醒",
      timestamp: Date.now(),
      source: "quant-trading",
    };
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`Webhook: HTTP ${resp.status}`);
  }
}
