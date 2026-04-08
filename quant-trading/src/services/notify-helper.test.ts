/**
 * Unit tests for the tryNotify helper (including webhook channels).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { tryNotify, registerNotificationChannels } from "./notify-helper.js";

describe("tryNotify", () => {
  beforeEach(() => {
    // Reset channels before each test
    registerNotificationChannels(undefined);
  });

  it("calls api.notify when available", async () => {
    const notifyFn = vi.fn();
    const api = { notify: notifyFn } as never;
    await tryNotify(api, "hello");
    expect(notifyFn).toHaveBeenCalledWith("hello");
  });

  it("does nothing when api.notify is missing", async () => {
    const api = {} as never;
    // Should not throw
    await tryNotify(api, "hello");
  });

  it("swallows errors from api.notify", async () => {
    const api = {
      notify: vi.fn().mockRejectedValue(new Error("fail")),
    } as never;
    // Should not throw
    await tryNotify(api, "hello");
  });

  it("does nothing when api.notify is not a function", async () => {
    const api = { notify: 42 } as never;
    // Should not throw
    await tryNotify(api, "hello");
  });
});

describe("registerNotificationChannels", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve("") });
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    registerNotificationChannels(undefined);
    vi.unstubAllGlobals();
  });

  it("sends to WeCom webhook with markdown format", async () => {
    registerNotificationChannels([
      { type: "wecom", webhookUrl: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test" },
    ]);

    const api = {} as never;
    await tryNotify(api, "测试信号: 买入600519");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchSpy.mock.calls[0];
    expect(url).toBe("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test");
    const body = JSON.parse(opts.body);
    expect(body.msgtype).toBe("markdown");
    expect(body.markdown.content).toBe("测试信号: 买入600519");
  });

  it("sends to DingTalk webhook with markdown format", async () => {
    registerNotificationChannels([
      { type: "dingtalk", webhookUrl: "https://oapi.dingtalk.com/robot/send?access_token=test" },
    ]);

    const api = {} as never;
    await tryNotify(api, "价格预警: 600519 突破3000");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchSpy.mock.calls[0];
    expect(url).toBe("https://oapi.dingtalk.com/robot/send?access_token=test");
    const body = JSON.parse(opts.body);
    expect(body.msgtype).toBe("markdown");
    expect(body.markdown.title).toBe("量化交易通知");
    expect(body.markdown.text).toBe("价格预警: 600519 突破3000");
  });

  it("sends to multiple channels in parallel", async () => {
    registerNotificationChannels([
      { type: "wecom", webhookUrl: "https://wecom.example.com/hook1", name: "主群" },
      { type: "dingtalk", webhookUrl: "https://dingtalk.example.com/hook2", name: "备用" },
    ]);

    const api = {} as never;
    await tryNotify(api, "熔断事件");

    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("skips disabled channels", async () => {
    registerNotificationChannels([
      { type: "wecom", webhookUrl: "https://wecom.example.com/hook1", enabled: false },
      { type: "dingtalk", webhookUrl: "https://dingtalk.example.com/hook2", enabled: true },
    ]);

    const api = {} as never;
    await tryNotify(api, "test");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("https://dingtalk.example.com/hook2");
  });

  it("does not throw when webhook fails", async () => {
    fetchSpy.mockResolvedValue({ ok: false, status: 500, text: () => Promise.resolve("error") });
    registerNotificationChannels([
      { type: "wecom", webhookUrl: "https://wecom.example.com/hook1" },
    ]);

    const api = {} as never;
    // Should not throw
    await tryNotify(api, "test");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("does not throw when fetch throws network error", async () => {
    fetchSpy.mockRejectedValue(new Error("network error"));
    registerNotificationChannels([
      { type: "dingtalk", webhookUrl: "https://dingtalk.example.com/hook2" },
    ]);

    const api = {} as never;
    // Should not throw
    await tryNotify(api, "test");
  });

  it("does nothing when no channels registered", async () => {
    registerNotificationChannels([]);

    const api = {} as never;
    await tryNotify(api, "test");

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
