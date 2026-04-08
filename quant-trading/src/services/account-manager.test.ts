/**
 * Tests for AccountManager 66multi-account registry and CRUD.
 */
import fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AccountManager } from "./account-manager.js";
import type { QuantTradingConfig, NamedTradingAccount } from "../types.js";

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: join(tmpdir(), `acct-mgr-test-${Date.now()}-${Math.random().toString(36).slice(2)}`),
    ...overrides,
  };
}

describe("AccountManager", () => {
  let cleanupDirs: string[] = [];

  afterEach(async () => {
    for (const d of cleanupDirs) {
      await fs.rm(d, { recursive: true, force: true }).catch(() => {});
    }
    cleanupDirs = [];
  });

  // ── Constructor / backward compatibility ──────────────────────

  describe("constructor", () => {
    it("wraps single tradingAccount as default", () => {
      const mgr = new AccountManager(makeConfig({ tradingAccount: { mode: "paper" } }));
      const accounts = mgr.listAccounts();
      expect(accounts).toHaveLength(1);
      expect(accounts[0].name).toBe("default");
      expect(accounts[0].mode).toBe("paper");
      expect(mgr.isMultiAccount).toBe(false);
    });

    it("uses tradingAccounts array when provided", () => {
      const mgr = new AccountManager(
        makeConfig({
          tradingAccounts: [
            { name: "paper-1", mode: "paper", label: "00" },
            { name: "qmt-live", mode: "live", broker: "qmt", label: "06QMT" },
          ],
        }),
      );
      expect(mgr.listAccounts()).toHaveLength(2);
      expect(mgr.isMultiAccount).toBe(true);
      expect(mgr.getActiveAccountName()).toBe("paper-1");
    });

    it("respects activeAccount from config", () => {
      const mgr = new AccountManager(
        makeConfig({
          tradingAccounts: [
            { name: "a", mode: "paper" },
            { name: "b", mode: "live" },
          ],
          activeAccount: "b",
        }),
      );
      expect(mgr.getActiveAccountName()).toBe("b");
    });

    it("falls back to first account if activeAccount is invalid", () => {
      const mgr = new AccountManager(
        makeConfig({
          tradingAccounts: [
            { name: "a", mode: "paper" },
            { name: "b", mode: "live" },
          ],
          activeAccount: "nonexistent",
        }),
      );
      expect(mgr.getActiveAccountName()).toBe("a");
    });
  });

  // ── Active account switching ─────────────────────────────────

  describe("setActiveAccount", () => {
    it("switches to a valid account", async () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live" },
        ],
      });
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);

      const ok = await mgr.setActiveAccount("b");
      expect(ok).toBe(true);
      expect(mgr.getActiveAccountName()).toBe("b");
    });

    it("returns false for nonexistent account", async () => {
      const mgr = new AccountManager(makeConfig());
      const ok = await mgr.setActiveAccount("nonexistent");
      expect(ok).toBe(false);
    });

    it("persists and reloads", async () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live" },
        ],
      });
      cleanupDirs.push(cfg.dataDir!);
      const mgr1 = new AccountManager(cfg);
      await mgr1.setActiveAccount("b");

      // Create a new instance and load 66should restore "b?
      const mgr2 = new AccountManager(cfg);
      await mgr2.load();
      expect(mgr2.getActiveAccountName()).toBe("b");
    });
  });

  // ── Config resolution ────────────────────────────────────────

  describe("resolveConfig", () => {
    it("injects active account as tradingAccount", () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "test", mode: "live", broker: "qmt" },
        ],
      });
      const mgr = new AccountManager(cfg);
      const resolved = mgr.resolveConfig(cfg);
      expect(resolved.tradingAccount?.mode).toBe("live");
      expect(resolved.tradingAccount?.broker).toBe("qmt");
    });

    it("resolveConfigForAccount returns null for unknown", () => {
      const cfg = makeConfig();
      const mgr = new AccountManager(cfg);
      expect(mgr.resolveConfigForAccount(cfg, "nope")).toBeNull();
    });

    it("resolveConfigForAccount returns config for specific account", () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live", broker: "qmt" },
        ],
      });
      const mgr = new AccountManager(cfg);
      const resolved = mgr.resolveConfigForAccount(cfg, "b");
      expect(resolved).not.toBeNull();
      expect(resolved!.tradingAccount?.broker).toBe("qmt");
    });
  });

  // ── CRUD: addAccount ─────────────────────────────────────────

  describe("addAccount", () => {
    it("adds a new account", async () => {
      const cfg = makeConfig();
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);

      const result = await mgr.addAccount({ name: "new-acct", mode: "paper", label: "新账" });
      expect(result.ok).toBe(true);
      expect(mgr.listAccounts()).toHaveLength(2);
      expect(mgr.getAccountByName("new-acct")?.label).toBe("新账");
      expect(mgr.isMultiAccount).toBe(true);
    });

    it("rejects duplicate name", async () => {
      const cfg = makeConfig();
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);
      await mgr.addAccount({ name: "x", mode: "paper" });
      const result = await mgr.addAccount({ name: "x", mode: "live" });
      expect(result.ok).toBe(false);
    });

    it("rejects invalid name", async () => {
      const mgr = new AccountManager(makeConfig());
      const result = await mgr.addAccount({ name: "has spaces", mode: "paper" });
      expect(result.ok).toBe(false);
    });

    it("rejects empty name", async () => {
      const mgr = new AccountManager(makeConfig());
      const result = await mgr.addAccount({ name: "", mode: "paper" });
      expect(result.ok).toBe(false);
    });

    it("rejects missing mode", async () => {
      const mgr = new AccountManager(makeConfig());
      const result = await mgr.addAccount({ name: "test" } as NamedTradingAccount);
      expect(result.ok).toBe(false);
    });

    it("persists runtime accounts across loads", async () => {
      const cfg = makeConfig();
      cleanupDirs.push(cfg.dataDir!);
      const mgr1 = new AccountManager(cfg);
      await mgr1.addAccount({ name: "runtime-1", mode: "paper" });

      const mgr2 = new AccountManager(cfg);
      await mgr2.load();
      expect(mgr2.getAccountByName("runtime-1")).toBeDefined();
      expect(mgr2.listAccounts()).toHaveLength(2); // default + runtime-1
    });
  });

  // ── CRUD: updateAccount ──────────────────────────────────────

  describe("updateAccount", () => {
    it("updates an existing account", async () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
        ],
      });
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);

      const result = await mgr.updateAccount("a", { label: "更新", mode: "live", broker: "qmt" });
      expect(result.ok).toBe(true);
      const updated = mgr.getAccountByName("a");
      expect(updated?.label).toBe("更新");
      expect(updated?.mode).toBe("live");
      expect(updated?.broker).toBe("qmt");
      // Name should not change
      expect(updated?.name).toBe("a");
    });

    it("returns error for nonexistent account", async () => {
      const mgr = new AccountManager(makeConfig());
      const result = await mgr.updateAccount("nope", { label: "x" });
      expect(result.ok).toBe(false);
    });
  });

  // ── CRUD: removeAccount ──────────────────────────────────────

  describe("removeAccount", () => {
    it("removes a non-active account", async () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live" },
        ],
      });
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);

      const result = await mgr.removeAccount("b");
      expect(result.ok).toBe(true);
      expect(mgr.listAccounts()).toHaveLength(1);
      expect(mgr.getAccountByName("b")).toBeUndefined();
    });

    it("rejects removal of active account", async () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live" },
        ],
      });
      const mgr = new AccountManager(cfg);
      const result = await mgr.removeAccount("a"); // "a" is active
      expect(result.ok).toBe(false);
    });

    it("rejects removal of last account", async () => {
      const mgr = new AccountManager(makeConfig());
      // Only "default" account exists, and it's active
      const result = await mgr.removeAccount("default");
      expect(result.ok).toBe(false);
    });

    it("rejects removal of nonexistent account", async () => {
      const mgr = new AccountManager(makeConfig());
      const result = await mgr.removeAccount("nope");
      expect(result.ok).toBe(false);
    });
  });

  // ── Cross-account queries ────────────────────────────────────

  describe("getAccountsSummary", () => {
    it("returns summary with correct active flag", () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "a", mode: "paper" },
          { name: "b", mode: "live", broker: "qmt", riskLimits: { maxDailyLoss: 10000 } },
        ],
      });
      const mgr = new AccountManager(cfg);
      const summaries = mgr.getAccountsSummary();
      expect(summaries).toHaveLength(2);
      expect(summaries[0].isActive).toBe(true);
      expect(summaries[1].isActive).toBe(false);
      expect(summaries[1].riskLimits?.maxDailyLoss).toBe(10000);
    });

    it("marks config vs runtime accounts", async () => {
      const cfg = makeConfig({
        tradingAccounts: [{ name: "from-config", mode: "paper" }],
      });
      cleanupDirs.push(cfg.dataDir!);
      const mgr = new AccountManager(cfg);
      await mgr.addAccount({ name: "runtime", mode: "paper" });

      const summaries = mgr.getAccountsSummary();
      expect(summaries.find((s) => s.name === "from-config")?.isFromConfig).toBe(true);
      expect(summaries.find((s) => s.name === "runtime")?.isFromConfig).toBe(false);
    });
  });

  describe("getAllAccountNames", () => {
    it("returns all names", () => {
      const cfg = makeConfig({
        tradingAccounts: [
          { name: "x", mode: "paper" },
          { name: "y", mode: "live" },
        ],
      });
      const mgr = new AccountManager(cfg);
      expect(mgr.getAllAccountNames()).toEqual(["x", "y"]);
    });
  });
});
