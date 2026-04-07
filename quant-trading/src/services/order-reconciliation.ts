/**
 * OrderReconciliationService — background service that periodically compares
 * local order/position state against broker-reported state and alerts on
 * discrepancies.
 *
 * Detects:
 * 1. Stuck orders: orders in submitted/partial status for > N minutes without updates
 * 2. Position mismatches: local tracked positions differ from broker positions
 * 3. Unexpected fills: broker positions that have no matching local order
 * 4. Stale orders: pending orders that were never submitted
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { isMarketOpen } from "../broker/paper-broker.js";
import type { LivePosition, Order, OrderStatus, QuantTradingConfig } from "../types.js";
import { tryNotify } from "./notify-helper.js";

// ── Config ───────────────────────────────────────────────────

export type OrderReconciliationConfig = {
  /** Polling interval in seconds (default 120) */
  pollIntervalSecs?: number;
  /** Minutes after which a submitted order is considered stuck (default 10) */
  stuckOrderMinutes?: number;
  /** Minutes after which a pending order is considered stale (default 30) */
  stalePendingMinutes?: number;
  /** Enable/disable the reconciliation service (default true) */
  enabled?: boolean;
};

const DEFAULTS: Required<OrderReconciliationConfig> = {
  pollIntervalSecs: 120,
  stuckOrderMinutes: 10,
  stalePendingMinutes: 30,
  enabled: true,
};

// ── Anomaly types ────────────────────────────────────────────

export type ReconciliationIssue = {
  type: "stuck_order" | "position_mismatch" | "unexpected_position" | "stale_pending";
  severity: "warning" | "error";
  symbol: string;
  message: string;
  details: Record<string, unknown>;
  detectedAt: string;
};

// ── Service factory ──────────────────────────────────────────

export function createOrderReconciliationService(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getOrders: () => Promise<Order[]>,
  getLocalPositions: () => LivePosition[],
  getBrokerPositions: () => Promise<LivePosition[]>,
): OpenClawPluginService {
  const cfg: Required<OrderReconciliationConfig> = {
    ...DEFAULTS,
    ...((config as Record<string, unknown>).orderReconciliation as Partial<OrderReconciliationConfig> ?? {}),
  };

  let interval: ReturnType<typeof setInterval> | null = null;

  // Dedup: track which issues have been reported today
  let reportedToday = new Set<string>();
  let lastTradingDay = "";
  let lastIssues: ReconciliationIssue[] = [];

  const resetDailyState = () => {
    const today = new Date().toISOString().slice(0, 10);
    if (today !== lastTradingDay) {
      reportedToday = new Set();
      lastTradingDay = today;
    }
  };

  const alert = async (issue: ReconciliationIssue) => {
    const key = `${issue.type}:${issue.symbol}`;
    if (reportedToday.has(key)) return;
    reportedToday.add(key);

    const emoji =
      issue.type === "stuck_order" ? "⏳" :
      issue.type === "position_mismatch" ? "⚠️" :
      issue.type === "unexpected_position" ? "❓" : "📋";
    const msg = `${emoji} [订单对账] ${issue.message}`;
    api.logger.info(`OrderReconciliation: ${issue.message}`);
    await tryNotify(api, msg);
  };

  const reconcile = async (): Promise<ReconciliationIssue[]> => {
    const issues: ReconciliationIssue[] = [];
    const now = Date.now();

    try {
      // ── 1. Check for stuck orders ──
      const orders = await getOrders();
      const activeStatuses: OrderStatus[] = ["submitted", "partial"];
      const activeOrders = orders.filter((o) => activeStatuses.includes(o.status));

      for (const order of activeOrders) {
        const updatedAt = new Date(order.updatedAt).getTime();
        const minutesElapsed = (now - updatedAt) / 60_000;

        if (minutesElapsed > cfg.stuckOrderMinutes) {
          const issue: ReconciliationIssue = {
            type: "stuck_order",
            severity: "warning",
            symbol: order.symbol,
            message: `订单 ${order.orderId.slice(0, 8)} (${order.side} ${order.symbol} ${order.quantity}股) ` +
              `已在 ${order.status} 状态停留 ${Math.round(minutesElapsed)} 分钟未更新`,
            details: {
              orderId: order.orderId,
              side: order.side,
              quantity: order.quantity,
              status: order.status,
              minutesElapsed: Math.round(minutesElapsed),
            },
            detectedAt: new Date().toISOString(),
          };
          issues.push(issue);
          await alert(issue);
        }
      }

      // ── 2. Check for stale pending orders ──
      const pendingOrders = orders.filter((o) => o.status === "pending");
      for (const order of pendingOrders) {
        const createdAt = new Date(order.createdAt).getTime();
        const minutesElapsed = (now - createdAt) / 60_000;

        if (minutesElapsed > cfg.stalePendingMinutes) {
          const issue: ReconciliationIssue = {
            type: "stale_pending",
            severity: "warning",
            symbol: order.symbol,
            message: `订单 ${order.orderId.slice(0, 8)} (${order.side} ${order.symbol}) ` +
              `已在 pending 状态 ${Math.round(minutesElapsed)} 分钟，可能未成功提交到券商`,
            details: {
              orderId: order.orderId,
              side: order.side,
              quantity: order.quantity,
              minutesElapsed: Math.round(minutesElapsed),
            },
            detectedAt: new Date().toISOString(),
          };
          issues.push(issue);
          await alert(issue);
        }
      }

      // ── 3. Check position mismatches ──
      const localPositions = getLocalPositions();
      let brokerPositions: LivePosition[];
      try {
        brokerPositions = await getBrokerPositions();
      } catch {
        // Can't reach broker — skip position checks
        return issues;
      }

      // Build maps for comparison
      const localMap = new Map<string, LivePosition>();
      for (const pos of localPositions) {
        if (pos.quantity > 0) localMap.set(pos.symbol, pos);
      }
      const brokerMap = new Map<string, LivePosition>();
      for (const pos of brokerPositions) {
        if (pos.quantity > 0) brokerMap.set(pos.symbol, pos);
      }

      // Check broker positions against local
      for (const [symbol, brokerPos] of brokerMap) {
        const localPos = localMap.get(symbol);

        if (!localPos) {
          // Broker has position that local doesn't track
          const issue: ReconciliationIssue = {
            type: "unexpected_position",
            severity: "error",
            symbol,
            message: `券商报告持有 ${brokerPos.name ?? symbol} ${brokerPos.quantity}股，但本地未跟踪此持仓`,
            details: {
              brokerQuantity: brokerPos.quantity,
              brokerAvgCost: brokerPos.avgCost,
            },
            detectedAt: new Date().toISOString(),
          };
          issues.push(issue);
          await alert(issue);
        } else if (Math.abs(localPos.quantity - brokerPos.quantity) > 0) {
          // Quantity mismatch
          const issue: ReconciliationIssue = {
            type: "position_mismatch",
            severity: "error",
            symbol,
            message: `${localPos.name ?? symbol} 持仓数量不一致: 本地=${localPos.quantity}股, 券商=${brokerPos.quantity}股`,
            details: {
              localQuantity: localPos.quantity,
              brokerQuantity: brokerPos.quantity,
              diff: brokerPos.quantity - localPos.quantity,
            },
            detectedAt: new Date().toISOString(),
          };
          issues.push(issue);
          await alert(issue);
        }
      }

      // Check local positions that broker doesn't have
      for (const [symbol, localPos] of localMap) {
        if (!brokerMap.has(symbol) && localPos.source === "broker") {
          const issue: ReconciliationIssue = {
            type: "position_mismatch",
            severity: "error",
            symbol,
            message: `本地记录持有 ${localPos.name ?? symbol} ${localPos.quantity}股，但券商无此持仓`,
            details: {
              localQuantity: localPos.quantity,
              localAvgCost: localPos.avgCost,
            },
            detectedAt: new Date().toISOString(),
          };
          issues.push(issue);
          await alert(issue);
        }
      }
    } catch (err) {
      api.logger.warn(
        `OrderReconciliation: 检测失败: ${(err as Error).message}`,
      );
    }

    lastIssues = issues;
    return issues;
  };

  const tick = async () => {
    if (!isMarketOpen()) return;
    resetDailyState();
    await reconcile();
  };

  return {
    id: "quant-order-reconciliation",
    async start() {
      if (!cfg.enabled) {
        api.logger.info("OrderReconciliation: disabled by config");
        return;
      }
      api.logger.info(
        `OrderReconciliation: started (poll=${cfg.pollIntervalSecs}s, ` +
          `stuckThreshold=${cfg.stuckOrderMinutes}min, ` +
          `staleThreshold=${cfg.stalePendingMinutes}min)`,
      );
      interval = setInterval(() => void tick(), cfg.pollIntervalSecs * 1000);
      void tick();
    },
    async stop() {
      if (interval) {
        clearInterval(interval);
        interval = null;
      }
      api.logger.info("OrderReconciliation: stopped");
    },
    /** Expose for tool to call on-demand */
    reconcile,
    getLastIssues: () => lastIssues,
  } as OpenClawPluginService & {
    reconcile: () => Promise<ReconciliationIssue[]>;
    getLastIssues: () => ReconciliationIssue[];
  };
}

export type OrderReconciliationServiceInstance = OpenClawPluginService & {
  reconcile: () => Promise<ReconciliationIssue[]>;
  getLastIssues: () => ReconciliationIssue[];
};
