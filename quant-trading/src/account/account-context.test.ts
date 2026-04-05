/**
 * Tests for AccountContext — active account switching with safety checks.
 *
 * Covers:
 *  - Default account binding
 *  - Switching to valid accounts
 *  - Blocking switch when pending orders exist
 *  - Force switch with confirm=true
 *  - Rejecting switch to non-existent accounts
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AccountStore } from "./account-store.js";
import { AccountManager } from "./account-manager.js";
import { AccountContext } from "./account-context.js";

let tmpDir: string;
let store: AccountStore;
let manager: AccountManager;
let ctx: AccountContext;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-account-ctx-test-"));
  store = new AccountStore({ dataDir: tmpDir });
  manager = new AccountManager(store);

  manager.register({
    accountId: "paper-default",
    label: "模拟账户",
    broker: "paper",
    market: ["cn_stock"],
    mode: "paper",
    brokerConfig: {},
  });
  manager.register({
    accountId: "qmt-001",
    label: "QMT实盘",
    broker: "qmt",
    market: ["cn_stock"],
    mode: "live",
    brokerConfig: {},
  });

  ctx = new AccountContext(manager, "paper-default");
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe("AccountContext", () => {
  it("starts with the default account", () => {
    expect(ctx.current).toBe("paper-default");
  });

  it("switches to another registered account", async () => {
    await ctx.switch("qmt-001");
    expect(ctx.current).toBe("qmt-001");
  });

  it("rejects switch to non-existent account", async () => {
    await expect(ctx.switch("nonexistent")).rejects.toThrow("not registered");
  });

  it("blocks switch when current account has pending orders", async () => {
    manager.updateAccountState("paper-default", { pendingOrderCount: 3 });
    await expect(ctx.switch("qmt-001")).rejects.toThrow("pending orders");
    expect(ctx.current).toBe("paper-default"); // unchanged
  });

  it("allows forced switch even with pending orders", async () => {
    manager.updateAccountState("paper-default", { pendingOrderCount: 3 });
    await ctx.switch("qmt-001", true); // confirm=true
    expect(ctx.current).toBe("qmt-001");
  });

  it("exposes the account manager", () => {
    expect(ctx.accountManager).toBe(manager);
  });
});
