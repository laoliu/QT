/**
 * Portfolio Combo & Risk Simulator routes.
 *
 * Provides:
 *  - GET  /quant/admin/portfolio-combo      → Portfolio Combiner UI page
 *  - POST /quant/admin/api/portfolio-combo   → Run combine / validate / perturbation / slippage
 *  - GET  /quant/admin/risk-simulator        → Risk Simulator UI page
 *  - POST /quant/admin/api/risk-simulate     → Run MDD gradient + vol scaling simulation
 */

import { runPythonScript } from "../../bridge/python-bridge.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { json, parseObjectBody } from "./_helpers.js";

export function registerPortfolioComboRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure, securePublic } = deps;

  // ── Portfolio Combo UI Page ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/portfolio-combo",
    handler: securePublic(async (_req, res) => {
      const { getPortfolioComboPageHtml } = await import("../pages/portfolio-combo-page.js");
      const html = getPortfolioComboPageHtml("", config.apiToken);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Portfolio Combo API ────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/portfolio-combo",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;

        const result = await runPythonScript<Record<string, unknown>>(
          "engines/portfolio_combo_api.py",
          body,
          config,
        );

        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `组合构建失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Risk Simulator UI Page ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/risk-simulator",
    handler: securePublic(async (_req, res) => {
      const { getRiskSimulatorPageHtml } = await import("../pages/risk-simulator-page.js");
      const html = getRiskSimulatorPageHtml("", config.apiToken);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Risk Simulator API ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/risk-simulate",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;

        const result = await runPythonScript<Record<string, unknown>>(
          "engines/risk_simulate_api.py",
          body,
          config,
        );

        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `风控模拟失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });
}
