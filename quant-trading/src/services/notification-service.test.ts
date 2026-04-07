/**
 * Unit tests for NotificationService.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationService } from "./notification-service.js";

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({ ok: true, text: async () => "" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("NotificationService", () => {
  it("sends to feishu webhook", async () => {
    const svc = new NotificationService({
      channels: [
        { type: "feishu", webhookUrl: "https://open.feishu.cn/open-apis/bot/v2/hook/test-token" },
      ],
    });

    const results = await svc.send("测试消息", "info");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, opts] = mockFetch.mock.calls[0]!;
    expect(url).toBe("https://open.feishu.cn/open-apis/bot/v2/hook/test-token");
    const body = JSON.parse(opts.body as string);
    expect(body.msg_type).toBe("interactive");
    expect(body.card.elements[0].content).toBe("测试消息");
  });

  it("sends to wecom webhook", async () => {
    const svc = new NotificationService({
      channels: [
        {
          type: "wecom",
          webhookUrl: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        },
      ],
    });

    const results = await svc.send("企微测试", "warning");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(true);
    const body = JSON.parse(mockFetch.mock.calls[0]![1].body as string);
    expect(body.msgtype).toBe("markdown");
    expect(body.markdown.content).toContain("企微测试");
  });

  it("sends to telegram bot", async () => {
    const svc = new NotificationService({
      channels: [
        {
          type: "telegram",
          webhookUrl:
            "https://api.telegram.org/bot123:ABC/sendMessage?chat_id=-1001234567890",
        },
      ],
    });

    const results = await svc.send("TG测试", "critical");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(true);
    const body = JSON.parse(mockFetch.mock.calls[0]![1].body as string);
    expect(body.chat_id).toBe("-1001234567890");
    expect(body.text).toContain("TG测试");
    expect(body.parse_mode).toBe("Markdown");
  });

  it("sends to discord webhook", async () => {
    const svc = new NotificationService({
      channels: [
        {
          type: "discord",
          webhookUrl: "https://discord.com/api/webhooks/123/abc",
        },
      ],
    });

    const results = await svc.send("Discord测试", "info");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(true);
    const body = JSON.parse(mockFetch.mock.calls[0]![1].body as string);
    expect(body.embeds).toHaveLength(1);
    expect(body.embeds[0].description).toBe("Discord测试");
    expect(body.embeds[0].color).toBe(0x3498db);
  });

  it("sends to multiple channels in parallel", async () => {
    const svc = new NotificationService({
      channels: [
        { type: "feishu", webhookUrl: "https://feishu.test/hook" },
        { type: "wecom", webhookUrl: "https://wecom.test/hook" },
        { type: "discord", webhookUrl: "https://discord.test/hook" },
      ],
    });

    const results = await svc.send("多通道测试", "warning");

    expect(results).toHaveLength(3);
    expect(results.every((r) => r.ok)).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it("skips disabled channels", async () => {
    const svc = new NotificationService({
      channels: [
        { type: "feishu", webhookUrl: "https://feishu.test/hook", enabled: false },
        { type: "wecom", webhookUrl: "https://wecom.test/hook", enabled: true },
      ],
    });

    const results = await svc.send("测试", "info");

    expect(results).toHaveLength(1);
    expect(results[0]!.channel).toBe("wecom");
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("returns empty array when no channels configured", async () => {
    const svc = new NotificationService({});
    const results = await svc.send("测试", "info");
    expect(results).toHaveLength(0);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("handles HTTP errors gracefully", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      text: async () => "rate limited",
    });

    const svc = new NotificationService({
      channels: [{ type: "feishu", webhookUrl: "https://feishu.test/hook" }],
    });

    const results = await svc.send("测试", "info");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(false);
    expect(results[0]!.error).toContain("429");
  });

  it("handles fetch exceptions gracefully", async () => {
    mockFetch.mockRejectedValueOnce(new Error("network timeout"));

    const svc = new NotificationService({
      channels: [{ type: "feishu", webhookUrl: "https://feishu.test/hook" }],
    });

    const results = await svc.send("测试", "info");

    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(false);
    expect(results[0]!.error).toContain("network timeout");
  });

  // ── Quiet hours tests ─────────────────────────────────────────

  it("suppresses non-critical messages during quiet hours", async () => {
    // Set quiet hours to cover current time
    const now = new Date();
    const startHour = now.getHours();
    const endHour = (startHour + 2) % 24;
    const start = `${String(startHour).padStart(2, "0")}:00`;
    const end = `${String(endHour).padStart(2, "0")}:00`;

    const svc = new NotificationService({
      channels: [{ type: "feishu", webhookUrl: "https://feishu.test/hook" }],
      quietHours: { enabled: true, start, end },
    });

    const results = await svc.send("应该被静默", "info");
    expect(results).toHaveLength(0);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("allows critical messages through quiet hours", async () => {
    const now = new Date();
    const startHour = now.getHours();
    const endHour = (startHour + 2) % 24;
    const start = `${String(startHour).padStart(2, "0")}:00`;
    const end = `${String(endHour).padStart(2, "0")}:00`;

    const svc = new NotificationService({
      channels: [{ type: "feishu", webhookUrl: "https://feishu.test/hook" }],
      quietHours: { enabled: true, start, end, bypassLevels: ["critical"] },
    });

    const results = await svc.send("紧急消息", "critical");
    expect(results).toHaveLength(1);
    expect(results[0]!.ok).toBe(true);
  });

  it("does not suppress when quiet hours disabled", async () => {
    const svc = new NotificationService({
      channels: [{ type: "feishu", webhookUrl: "https://feishu.test/hook" }],
      quietHours: { enabled: false, start: "00:00", end: "23:59" },
    });

    const results = await svc.send("不应被静默", "info");
    expect(results).toHaveLength(1);
  });

  // ── Config update ─────────────────────────────────────────────

  it("supports runtime config update", async () => {
    const svc = new NotificationService({});
    expect(svc.enabledChannelCount).toBe(0);

    svc.updateConfig({
      channels: [
        { type: "feishu", webhookUrl: "https://feishu.test/hook" },
        { type: "discord", webhookUrl: "https://discord.test/hook" },
      ],
    });
    expect(svc.enabledChannelCount).toBe(2);

    const results = await svc.send("更新后测试", "info");
    expect(results).toHaveLength(2);
  });

  // ── Severity colors ───────────────────────────────────────────

  it("uses correct discord embed color for severity levels", async () => {
    const svc = new NotificationService({
      channels: [{ type: "discord", webhookUrl: "https://discord.test/hook" }],
    });

    await svc.send("critical", "critical");
    const body1 = JSON.parse(mockFetch.mock.calls[0]![1].body as string);
    expect(body1.embeds[0].color).toBe(0xff0000);

    await svc.send("warning", "warning");
    const body2 = JSON.parse(mockFetch.mock.calls[1]![1].body as string);
    expect(body2.embeds[0].color).toBe(0xffa500);

    await svc.send("info", "info");
    const body3 = JSON.parse(mockFetch.mock.calls[2]![1].body as string);
    expect(body3.embeds[0].color).toBe(0x3498db);
  });
});
