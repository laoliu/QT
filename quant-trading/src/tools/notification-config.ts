/**
 * notification_config tool — AI-callable for managing multi-channel notifications.
 *
 * Actions:
 *   status          — View current notification channel configuration
 *   configure       — Configure notification channels
 *   test            — Send a test notification
 *   enable          — Enable specific channels
 *   disable         — Disable specific channels
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { MultiChannelNotifier, NotifyChannel } from "../services/multi-channel-notifier.js";

export function createNotificationConfigTool(
  _api: OpenClawPluginApi,
  notifier: MultiChannelNotifier,
) {
  return {
    name: "notification_config",
    description:
      "通知渠道配置工具。管理多渠道推送（聊天/Bark/企业微信/钉钉/飞书/Webhook）。\n" +
      "• action=status — 查看通知渠道状态\n" +
      "• action=configure — 配置通知渠道URL\n" +
      "• action=test — 发送测试通知\n" +
      "• action=enable — 启用指定渠道\n" +
      "• action=disable — 禁用指定渠道",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["status", "configure", "test", "enable", "disable"],
          description: "操作类型",
        },
        channel: {
          type: "string",
          enum: ["chat", "bark", "wecom", "dingtalk", "feishu", "webhook"],
          description: "通知渠道",
        },
        channels: {
          type: "array",
          items: {
            type: "string",
            enum: ["chat", "bark", "wecom", "dingtalk", "feishu", "webhook"],
          },
          description: "通知渠道列表 (enable/disable)",
        },
        bark_url: { type: "string", description: "Bark推送URL" },
        wecom_webhook_url: { type: "string", description: "企业微信Webhook URL" },
        dingtalk_webhook_url: { type: "string", description: "钉钉Webhook URL" },
        feishu_webhook_url: { type: "string", description: "飞书Webhook URL" },
        custom_webhook_url: { type: "string", description: "自定义Webhook URL" },
        message: { type: "string", description: "测试消息内容 (test)" },
        title: { type: "string", description: "消息标题 (test)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "status": {
          return notifier.getStatus();
        }

        case "configure": {
          const updates: Record<string, string | undefined> = {};
          if (args.bark_url) updates.barkUrl = args.bark_url as string;
          if (args.wecom_webhook_url) updates.wecomWebhookUrl = args.wecom_webhook_url as string;
          if (args.dingtalk_webhook_url)
            updates.dingtalkWebhookUrl = args.dingtalk_webhook_url as string;
          if (args.feishu_webhook_url) updates.feishuWebhookUrl = args.feishu_webhook_url as string;
          if (args.custom_webhook_url) updates.customWebhookUrl = args.custom_webhook_url as string;

          if (Object.keys(updates).length === 0) {
            return { error: "请提供至少一个渠道URL配置" };
          }
          notifier.updateConfig(updates);
          return { message: "通知渠道配置已更新", ...notifier.getStatus() };
        }

        case "test": {
          const message = (args.message as string) ?? "这是一条测试通知 — 量化交易系统";
          const title = args.title as string | undefined;
          const result = await notifier.notify(message, title);
          return { message: "测试通知已发送", ...result };
        }

        case "enable": {
          const channels =
            (args.channels as NotifyChannel[]) ??
            (args.channel ? [args.channel as NotifyChannel] : []);
          if (channels.length === 0) return { error: "请提供 channel 或 channels" };
          const current = notifier.getStatus().enabledChannels;
          const newEnabled = [...new Set([...current, ...channels])];
          notifier.updateConfig({ enabledChannels: newEnabled as NotifyChannel[] });
          return { message: `已启用: ${channels.join(", ")}`, ...notifier.getStatus() };
        }

        case "disable": {
          const channels =
            (args.channels as NotifyChannel[]) ??
            (args.channel ? [args.channel as NotifyChannel] : []);
          if (channels.length === 0) return { error: "请提供 channel 或 channels" };
          const current = notifier.getStatus().enabledChannels;
          const newEnabled = current.filter(
            (c) => !channels.includes(c as NotifyChannel),
          );
          notifier.updateConfig({ enabledChannels: newEnabled as NotifyChannel[] });
          return { message: `已禁用: ${channels.join(", ")}`, ...notifier.getStatus() };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
