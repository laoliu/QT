import { randomBytes } from "node:crypto";
/**
 * ConfirmationManager — manages human-in-the-loop order confirmations.
 *
 * When an order requires human confirmation (from RiskGate), this service:
 *  1. Generates a short confirmation ID (e.g. "a3f9")
 *  2. Formats a structured message and sends it to the configured channel
 *  3. Registers /confirm and /reject chat command handlers
 *  4. Returns a Promise<boolean> that resolves when the user responds or times out
 *
 * Example confirmation message in chat:
 *   ⚠️ 实盘下单确认 #a3f9
 *   策略: dual-ma-crossover   标的: 600519 贵州茅台
 *   操作: 买入 100 股          预估金额: ¥185,000
 *   风险提示: 订单金额超过确认阈值
 *
 *   回复 /confirm a3f9 确认 | /reject a3f9 取消
 *   (120 秒后自动取消)
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OrderRequest } from "../broker/adapter.js";
import type { ConfirmationStatus, PendingConfirmation, QuantTradingConfig } from "../types.js";
import { AsyncMutex } from "../utils/async-mutex.js";
import { nowCSTISO } from "../utils/cst-time.js";

const confirmsMutex = new AsyncMutex();

// ── File persistence ─────────────────────────────────────────────

function confirmsPath(cfg: QuantTradingConfig): string {
  const dir = cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(dir, "confirmations.json");
}

async function loadConfirms(cfg: QuantTradingConfig): Promise<PendingConfirmation[]> {
  try {
    return JSON.parse(await fs.readFile(confirmsPath(cfg), "utf-8")) as PendingConfirmation[];
  } catch {
    return [];
  }
}

async function saveConfirms(items: PendingConfirmation[], cfg: QuantTradingConfig): Promise<void> {
  const dir = cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  await fs.mkdir(dir, { recursive: true });
  // Prune old terminal records (resolved/expired/rejected) to prevent unbounded growth
  const MAX_TERMINAL_CONFIRMS = 500;
  const terminal: PendingConfirmation[] = [];
  const active: PendingConfirmation[] = [];
  for (const item of items) {
    if (item.status === "waiting") active.push(item);
    else terminal.push(item);
  }
  const pruned =
    terminal.length > MAX_TERMINAL_CONFIRMS
      ? [...active, ...terminal.slice(terminal.length - MAX_TERMINAL_CONFIRMS)]
      : items;
  await fs.writeFile(confirmsPath(cfg), JSON.stringify(pruned, null, 2));
}

// ── In-memory pending map ─────────────────────────────────────────

type PendingEntry = {
  resolve: (confirmed: boolean) => void;
  timer: ReturnType<typeof setTimeout>;
};

// ── ID generation ────────────────────────────────────────────────

function shortId(): string {
  return randomBytes(2).toString("hex"); // e.g. "a3f9"
}

// ── Message formatter ─────────────────────────────────────────────

function buildMessage(
  confirmId: string,
  req: OrderRequest,
  valueDisplay: string,
  reason: string,
  timeoutSecs: number,
): string {
  const sideLabel =
    {
      buy: "买入",
      sell: "卖出",
      short: "做空",
      cover: "平空",
      buy_open: "买入开仓",
      sell_open: "卖出开仓",
      buy_close: "买入平仓",
      sell_close: "卖出平仓",
    }[req.side] ?? req.side;
  const isOptionSide = ["buy_open", "sell_open", "buy_close", "sell_close"].includes(req.side);
  const unit = isOptionSide ? "张" : "股";
  const lines = [
    `⚠️ 实盘下单确认 #${confirmId}`,
    `策略: ${req.strategyId ?? "手动"}   标的: ${req.symbol}`,
    `操作: ${sideLabel} ${req.quantity} ${unit}   预估金额: ${valueDisplay}`,
    req.price ? `限价: ¥${req.price}` : `类型: 市价单`,
    `风险提示: ${reason}`,
    ``,
    `回复 /confirm ${confirmId} 确认 | /reject ${confirmId} 取消`,
    `(${timeoutSecs} 秒后自动取消)`,
  ];
  return lines.join("\n");
}

// ── ConfirmationManager ──────────────────────────────────────────

export class ConfirmationManager {
  private pending: Map<string, PendingEntry> = new Map();
  private cfg: QuantTradingConfig;
  // api may be undefined in unit tests
  private api: OpenClawPluginApi | null;

  constructor(cfg: QuantTradingConfig, api: OpenClawPluginApi | null = null) {
    this.cfg = cfg;
    this.api = api;
  }

  /**
   * Request human confirmation for an order.
   * Returns true if confirmed, false if rejected or timed out.
   */
  request(req: OrderRequest, reason: string, orderId: string): Promise<boolean> {
    const timeoutSecs = this.cfg.tradingAccount?.riskLimits?.confirmationTimeoutSecs ?? 120;
    const confirmId = shortId();
    const estimatedValue = (req.price ?? 0) * req.quantity;
    const valueDisplay =
      estimatedValue > 0
        ? `¥${estimatedValue.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : "市价（待成交确定）";
    const message = buildMessage(confirmId, req, valueDisplay, reason, timeoutSecs);

    const expiresAt = new Date(Date.now() + timeoutSecs * 1000).toISOString();
    const record: PendingConfirmation = {
      confirmId,
      orderId,
      channel: this.cfg.tradingAccount?.confirmationChannel,
      messageText: message,
      expiresAt,
      status: "waiting",
    };

    // Persist to disk (serialized via mutex to prevent concurrent corruption)
    void (async () => {
      const release = await confirmsMutex.acquire();
      try {
        const items = await loadConfirms(this.cfg);
        items.push(record);
        await saveConfirms(items, this.cfg);
      } catch (e) {
        const msg = `[ConfirmationManager] Failed to persist confirmation: ${e}`;
        this.api ? this.api.logger.warn(msg) : console.warn(msg);
      } finally {
        release();
      }
    })();

    // Send message via plugin API (best-effort) or configured webhook
    if (this.api) {
      const webhookOrChannel = this.cfg.tradingAccount?.confirmationChannel;
      if (webhookOrChannel?.startsWith("http")) {
        // POST to a configured webhook (e.g. a Telegram bot API URL or custom endpoint)
        void fetch(webhookOrChannel, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: message, confirmId, type: "quant_confirm" }),
        }).catch((e: Error) => {
          this.api?.logger.warn(`[Confirm] Webhook delivery failed: ${e.message}`);
        });
      }
      // Always surface in the server log so it appears in gateway output
      this.api.logger.info(`[CONFIRM REQUEST]\n${message}`);
    } else {
      // Fallback: print to stdout for CLI usage
      console.log(`\n${message}\n`);
    }

    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        void this.expire(confirmId);
        resolve(false);
      }, timeoutSecs * 1000);

      this.pending.set(confirmId, { resolve, timer });
    });
  }

  /** Called when the user sends /confirm <id> */
  async confirm(confirmId: string, respondedBy?: string): Promise<string> {
    return this.respond(confirmId, "confirmed", respondedBy);
  }

  /** Called when the user sends /reject <id> */
  async reject(confirmId: string, respondedBy?: string): Promise<string> {
    return this.respond(confirmId, "rejected", respondedBy);
  }

  /**
   * Send a stop-loss prompt to the user after a buy order fills when no
   * autoStopLoss rule is configured. Returns true if the user confirms
   * they want default stop-loss/take-profit orders created.
   */
  requestStopLossPrompt(order: {
    symbol: string;
    side: string;
    quantity: number;
    avgFillPrice: number;
    orderId: string;
  }): Promise<boolean> {
    const timeoutSecs = this.cfg.tradingAccount?.riskLimits?.confirmationTimeoutSecs ?? 120;
    const confirmId = shortId();
    const fillPrice = order.avgFillPrice;
    const defaultStopPct = 5;
    const defaultTpPct = 15;
    const stopPrice = fillPrice * (1 - defaultStopPct / 100);
    const tpPrice = fillPrice * (1 + defaultTpPct / 100);

    const message = [
      `💡 止损止盈设置确认 #${confirmId}`,
      `订单已成交: ${order.side === "buy" ? "买入" : order.side === "cover" ? "平空" : "买入开仓"} ${order.symbol} ${order.quantity}股 @ ¥${fillPrice.toFixed(2)}`,
      `当前未配置自动止损止盈规则。`,
      ``,
      `是否按默认规则创建止损止盈？`,
      `  止损: -${defaultStopPct}% @ ¥${stopPrice.toFixed(2)}`,
      `  止盈: +${defaultTpPct}% @ ¥${tpPrice.toFixed(2)}`,
      ``,
      `回复 /confirm ${confirmId} 确认创建 | /reject ${confirmId} 跳过`,
      `(${timeoutSecs} 秒后自动跳过)`,
    ].join("\n");

    const expiresAt = new Date(Date.now() + timeoutSecs * 1000).toISOString();
    const record: PendingConfirmation = {
      confirmId,
      orderId: order.orderId,
      channel: this.cfg.tradingAccount?.confirmationChannel,
      messageText: message,
      expiresAt,
      status: "waiting",
    };

    // Persist to disk
    void (async () => {
      const release = await confirmsMutex.acquire();
      try {
        const items = await loadConfirms(this.cfg);
        items.push(record);
        await saveConfirms(items, this.cfg);
      } catch (e) {
        const msg = `[ConfirmationManager] Failed to persist stop-loss prompt: ${e}`;
        this.api ? this.api.logger.warn(msg) : console.warn(msg);
      } finally {
        release();
      }
    })();

    // Send message
    if (this.api) {
      const webhookOrChannel = this.cfg.tradingAccount?.confirmationChannel;
      if (webhookOrChannel?.startsWith("http")) {
        void fetch(webhookOrChannel, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: message, confirmId, type: "quant_stoploss_prompt" }),
        }).catch((e: Error) => {
          this.api?.logger.warn(`[Confirm] Webhook delivery failed: ${e.message}`);
        });
      }
      this.api.logger.info(`[STOPLOSS PROMPT]\n${message}`);
    } else {
      console.log(`\n${message}\n`);
    }

    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        void this.expire(confirmId);
        resolve(false);
      }, timeoutSecs * 1000);

      this.pending.set(confirmId, { resolve, timer });
    });
  }

  /** Returns all waiting confirmations (for status queries). */
  async listPending(): Promise<PendingConfirmation[]> {
    const items = await loadConfirms(this.cfg);
    const now = nowCSTISO();
    return items.filter((c) => c.status === "waiting" && c.expiresAt > now);
  }

  // ── Private ──────────────────────────────────────────────────

  private async respond(
    confirmId: string,
    status: "confirmed" | "rejected",
    respondedBy?: string,
  ): Promise<string> {
    const entry = this.pending.get(confirmId);
    if (!entry) {
      // Check if it was already resolved (e.g. expired)
      const items = await loadConfirms(this.cfg);
      const existing = items.find((c) => c.confirmId === confirmId);
      if (!existing) return `未找到确认请求 #${confirmId}`;
      if (existing.status !== "waiting")
        return `请求 #${confirmId} 已${this.statusLabel(existing.status)}`;
      return `请求 #${confirmId} 已过期`;
    }

    clearTimeout(entry.timer);
    this.pending.delete(confirmId);
    entry.resolve(status === "confirmed");

    await this.updateStatus(confirmId, status, respondedBy);

    const label = status === "confirmed" ? "✅ 已确认" : "❌ 已拒绝";
    return `${label}：下单请求 #${confirmId}`;
  }

  private async expire(confirmId: string): Promise<void> {
    this.pending.delete(confirmId);
    await this.updateStatus(confirmId, "expired");
  }

  private async updateStatus(
    confirmId: string,
    status: ConfirmationStatus,
    respondedBy?: string,
  ): Promise<void> {
    const release = await confirmsMutex.acquire();
    try {
      const items = await loadConfirms(this.cfg);
      const idx = items.findIndex((c) => c.confirmId === confirmId);
      if (idx >= 0) {
        items[idx].status = status;
        items[idx].respondedAt = nowCSTISO();
        if (respondedBy) items[idx].respondedBy = respondedBy;
        await saveConfirms(items, this.cfg);
      }
    } finally {
      release();
    }
  }

  private statusLabel(s: ConfirmationStatus): string {
    switch (s) {
      case "confirmed":
        return "确认";
      case "rejected":
        return "拒绝";
      case "expired":
        return "过期";
      default:
        return s;
    }
  }

  /** Clear all pending timers — call on shutdown to prevent post-teardown writes. */
  dispose(): void {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.resolve(false);
      this.pending.delete(id);
    }
  }
}
