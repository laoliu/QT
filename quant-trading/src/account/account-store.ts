/**
 * AccountStore — encrypted credential storage and account config persistence.
 *
 * Stores account configurations in `{dataDir}/accounts.json`.
 * Broker-specific credentials (passwords, tokens) are encrypted with AES-256-GCM.
 */
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// ── Types ────────────────────────────────────────────────────

export interface AccountConfig {
  accountId: string;
  label: string;
  broker: "paper" | "futu" | "qmt" | "remote-qmt" | "ib";
  market: string[];
  mode: "paper" | "live";
  riskLimits?: Record<string, unknown>;
  brokerConfig: Record<string, unknown>;
}

interface EncryptedAccountRecord {
  accountId: string;
  label: string;
  broker: string;
  market: string[];
  mode: string;
  riskLimits?: Record<string, unknown>;
  brokerConfig: string; // encrypted JSON
  encryptionVersion: 1;
  iv: string;
  authTag: string;
}

// ── Encryption helpers ──────────────────────────────────────

const ALGORITHM = "aes-256-gcm";
const KEY_LENGTH = 32;

function deriveKey(seed?: string): Buffer {
  const machineId = os.hostname() + os.userInfo().username;
  const material = seed ? `${machineId}:${seed}` : machineId;
  return crypto.createHash("sha256").update(material).digest();
}

export function encrypt(plaintext: string, key: Buffer): { ciphertext: string; iv: string; authTag: string } {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
  let encrypted = cipher.update(plaintext, "utf-8", "base64");
  encrypted += cipher.final("base64");
  return {
    ciphertext: encrypted,
    iv: iv.toString("base64"),
    authTag: (cipher as crypto.CipherGCM).getAuthTag().toString("base64"),
  };
}

export function decrypt(ciphertext: string, iv: string, authTag: string, key: Buffer): string {
  const decipher = crypto.createDecipheriv(ALGORITHM, key, Buffer.from(iv, "base64"));
  (decipher as crypto.DecipherGCM).setAuthTag(Buffer.from(authTag, "base64"));
  let decrypted = decipher.update(ciphertext, "base64", "utf-8");
  decrypted += decipher.final("utf-8");
  return decrypted;
}

// ── Store ───────────────────────────────────────────────────

export class AccountStore {
  private filePath: string;
  private key: Buffer;
  private accounts: Map<string, AccountConfig> = new Map();
  private loaded = false;

  constructor(opts: { dataDir?: string; encryptionSeed?: string } = {}) {
    const dir = opts.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
    this.filePath = path.join(dir, "accounts.json");
    this.key = deriveKey(opts.encryptionSeed);
  }

  async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, "utf-8");
      const records: EncryptedAccountRecord[] = JSON.parse(raw);
      this.accounts.clear();
      for (const rec of records) {
        const brokerConfig = JSON.parse(decrypt(rec.brokerConfig, rec.iv, rec.authTag, this.key));
        this.accounts.set(rec.accountId, {
          accountId: rec.accountId,
          label: rec.label,
          broker: rec.broker as AccountConfig["broker"],
          market: rec.market,
          mode: rec.mode as "paper" | "live",
          riskLimits: rec.riskLimits,
          brokerConfig,
        });
      }
    } catch (err) {
      // Distinguish "file not found" from "decrypt failure"
      if (err && typeof err === "object" && "code" in err && (err as NodeJS.ErrnoException).code === "ENOENT") {
        this.accounts.clear();
      } else {
        throw err;
      }
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    const records: EncryptedAccountRecord[] = [];
    for (const config of this.accounts.values()) {
      const { ciphertext, iv, authTag } = encrypt(JSON.stringify(config.brokerConfig), this.key);
      records.push({
        accountId: config.accountId,
        label: config.label,
        broker: config.broker,
        market: config.market,
        mode: config.mode,
        riskLimits: config.riskLimits,
        brokerConfig: ciphertext,
        encryptionVersion: 1,
        iv,
        authTag,
      });
    }
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, JSON.stringify(records, null, 2));
  }

  register(config: AccountConfig): void {
    if (this.accounts.has(config.accountId)) {
      throw new Error(`Account '${config.accountId}' already exists`);
    }
    this.accounts.set(config.accountId, config);
  }

  unregister(accountId: string): void {
    if (!this.accounts.has(accountId)) {
      throw new Error(`Account '${accountId}' not found`);
    }
    this.accounts.delete(accountId);
  }

  get(accountId: string): AccountConfig | undefined {
    return this.accounts.get(accountId);
  }

  list(): AccountConfig[] {
    return [...this.accounts.values()];
  }

  has(accountId: string): boolean {
    return this.accounts.has(accountId);
  }

  get size(): number {
    return this.accounts.size;
  }
}
