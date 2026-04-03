/**
 * Unit tests for the tryNotify helper.
 */
import { describe, expect, it, vi } from "vitest";
import { tryNotify } from "./notify-helper.js";

describe("tryNotify", () => {
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
