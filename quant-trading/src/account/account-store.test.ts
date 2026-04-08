/**
 * Tests for AccountStore  credential encryption, account CRUD, persistence.
 *
 * Covers:
 *  - AES-256-GCM encryption/decryption of broker credentials
 *  - Account registration and unregistration
 *  - Duplicate registration prevention
 *  - Persistence to disk (save/load round-trip)
 *  - Encrypted credentials are not stored in plaintext
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AccountStore, encrypt, decrypt } from "./account-store.js";

let tmpDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-account-store-test-"));
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

function makeStore(seed?: string) {
  return new AccountStore({ dataDir: tmpDir, encryptionSeed: seed });
}

const PAPER_ACCOUNT = {
  accountId: "paper-default",
  label: "模拟账户",
  broker: "paper" as const,
  market: ["cn_stock"],
  mode: "paper" as const,
  brokerConfig: {},
};

const FUTU_ACCOUNT = {
  accountId: "futu-main",
  label: "瀵岄€旀腐鑲",
  broker: "futu" as const,
  market: ["hk_stock", "cn_stock"],
  mode: "live" as const,
  brokerConfig: {
    openDHost: "127.0.0.1",
    openDPort: 11111,
    pwdUnlock: "my-secret-trading-password",
  },
};

// ── Encryption unit tests ───────────────────────────────────

describe("encrypt/decrypt", () => {
  it("round-trips plaintext correctly", () => {
    const key = Buffer.alloc(32, "test-key-material");
    const plaintext = '{"password":"super-secret"}';
    const { ciphertext, iv, authTag } = encrypt(plaintext, key);
    const result = decrypt(ciphertext, iv, authTag, key);
    expect(result).toBe(plaintext);
  });

  it("ciphertext differs from plaintext", () => {
    const key = Buffer.alloc(32, "test-key-material");
    const plaintext = "hello world";
    const { ciphertext } = encrypt(plaintext, key);
    expect(ciphertext).not.toBe(plaintext);
    expect(ciphertext).not.toContain("hello");
  });

  it("different keys produce different ciphertexts", () => {
    const key1 = Buffer.alloc(32, "key1");
    const key2 = Buffer.alloc(32, "key2");
    const plaintext = "same data";
    const enc1 = encrypt(plaintext, key1);
    const enc2 = encrypt(plaintext, key2);
    expect(enc1.ciphertext).not.toBe(enc2.ciphertext);
  });

  it("wrong key fails to decrypt", () => {
    const key1 = Buffer.alloc(32, "correct-key");
    const key2 = Buffer.alloc(32, "wrong-key");
    const { ciphertext, iv, authTag } = encrypt("secret", key1);
    expect(() => decrypt(ciphertext, iv, authTag, key2)).toThrow();
  });
});

// ── AccountStore CRUD tests ─────────────────────────────────

describe("AccountStore", () => {
  describe("register/unregister", () => {
    it("registers a new account", () => {
      const store = makeStore();
      store.register(PAPER_ACCOUNT);
      expect(store.has("paper-default")).toBe(true);
      expect(store.size).toBe(1);
    });

    it("rejects duplicate accountId", () => {
      const store = makeStore();
      store.register(PAPER_ACCOUNT);
      expect(() => store.register(PAPER_ACCOUNT)).toThrow("already exists");
    });

    it("unregisters an existing account", () => {
      const store = makeStore();
      store.register(PAPER_ACCOUNT);
      store.unregister("paper-default");
      expect(store.has("paper-default")).toBe(false);
      expect(store.size).toBe(0);
    });

    it("throws on unregister of nonexistent account", () => {
      const store = makeStore();
      expect(() => store.unregister("nonexistent")).toThrow("not found");
    });
  });

  describe("get/list", () => {
    it("retrieves account by id", () => {
      const store = makeStore();
      store.register(PAPER_ACCOUNT);
      const account = store.get("paper-default");
      expect(account?.label).toBe("模拟账户");
      expect(account?.broker).toBe("paper");
    });

    it("returns undefined for nonexistent id", () => {
      const store = makeStore();
      expect(store.get("nonexistent")).toBeUndefined();
    });

    it("lists all registered accounts", () => {
      const store = makeStore();
      store.register(PAPER_ACCOUNT);
      store.register(FUTU_ACCOUNT);
      const list = store.list();
      expect(list).toHaveLength(2);
      expect(list.map((a) => a.accountId).sort()).toEqual(["futu-main", "paper-default"]);
    });
  });

  describe("save/load (persistence)", () => {
    it("round-trips accounts through save and load", async () => {
      const store1 = makeStore("test-seed");
      store1.register(PAPER_ACCOUNT);
      store1.register(FUTU_ACCOUNT);
      await store1.save();

      const store2 = makeStore("test-seed");
      await store2.load();
      expect(store2.size).toBe(2);
      expect(store2.get("paper-default")?.label).toBe("模拟账户");
      expect(store2.get("futu-main")?.brokerConfig).toEqual(FUTU_ACCOUNT.brokerConfig);
    });

    it("encrypted file does not contain plaintext password", async () => {
      const store = makeStore("seed");
      store.register(FUTU_ACCOUNT);
      await store.save();

      const raw = await fs.readFile(path.join(tmpDir, "accounts.json"), "utf-8");
      expect(raw).not.toContain("my-secret-trading-password");
      expect(raw).toContain("encryptionVersion");
    });

    it("load from empty dir returns empty store", async () => {
      const store = makeStore();
      await store.load();
      expect(store.size).toBe(0);
    });

    it("different encryption seeds cannot read each other's data", async () => {
      const store1 = makeStore("seed-A");
      store1.register(FUTU_ACCOUNT);
      await store1.save();

      const store2 = makeStore("seed-B");
      // Should fail gracefully (can't decrypt with wrong key)
      await expect(store2.load()).rejects.toThrow();
    });
  });
});
