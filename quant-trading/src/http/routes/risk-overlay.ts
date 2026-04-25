/**
 * Risk Overlay API routes — live MDD gradient + vol scaling risk control.
 *
 * Provides:
 *  - GET  /quant/admin/api/risk-overlay/status  → Current overlay status & diagnostics
 *  - GET  /quant/admin/api/risk-overlay/config  → Current configuration
 *  - POST /quant/admin/api/risk-overlay/config  → Update configuration
 *  - POST /quant/admin/api/risk-overlay/reset   → Reset controller state
 */

import type { AdminRouteDeps } from "../admin-routes.js";
import type { PortfolioRiskOverlay } from "../../services/portfolio-risk-overlay.js";
import { json, parseObjectBody } from "./_helpers.js";

export function registerRiskOverlayRoutes(
  deps: AdminRouteDeps,
  riskOverlay: PortfolioRiskOverlay,
): void {
  const { api, secure } = deps;

  // ── Status & Diagnostics ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/risk-overlay/status",
    handler: secure(async (_req, res) => {
      json(res, { ok: true, data: riskOverlay.getStatus() });
    }),
  });

  // ── Get Config ─────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/risk-overlay/config",
    handler: secure(async (req, res) => {
      if (req.method === "POST") {
        try {
          const body = (await parseObjectBody(req)) as Record<string, unknown>;
          riskOverlay.updateConfig(body);
          void riskOverlay.save();
          json(res, { ok: true, config: riskOverlay.getConfig() });
        } catch (err) {
          json(res, { error: `配置更新失败: ${err instanceof Error ? err.message : String(err)}` }, 400);
        }
        return;
      }
      json(res, { ok: true, config: riskOverlay.getConfig() });
    }),
  });

  // ── Reset State ────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/risk-overlay/reset",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      riskOverlay.resetState();
      void riskOverlay.save();
      json(res, { ok: true, message: "控制器状态已重置" });
    }),
  });
}
