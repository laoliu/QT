/**
 * Tests for Dashboard HTML generation and endpoint index.
 */
import { describe, expect, it } from "vitest";
import { getDashboardHtml, handleDashboardRequest } from "./dashboard.js";

describe("Dashboard", () => {
  describe("getDashboardHtml", () => {
    const html = getDashboardHtml("http://localhost:18789");

    it("returns valid HTML document", () => {
      expect(html).toMatch(/^<!DOCTYPE html>/);
      expect(html).toContain("</html>");
    });

    it("includes the base URL in script", () => {
      expect(html).toContain('"http://localhost:18789"');
    });

    it("embeds the page title", () => {
      expect(html).toContain("<title>OpenClaw 量化交易仪表盘</title>");
    });

    // ── Layout sections ──────────────────────────────────────

    it("contains portfolio card", () => {
      expect(html).toContain('id="portfolioStats"');
      expect(html).toContain("投资组合");
    });

    it("contains positions table", () => {
      expect(html).toContain('id="positionsTable"');
    });

    it("contains orders table", () => {
      expect(html).toContain('id="ordersTable"');
    });

    it("contains signals table", () => {
      expect(html).toContain('id="signalsTable"');
    });

    it("contains daily PnL card", () => {
      expect(html).toContain('id="pnlCard"');
    });

    // ── New G1-G4 features ──────────────────────────────────

    it("contains equity chart container", () => {
      expect(html).toContain('id="equityChart"');
      expect(html).toContain("收益曲线");
    });

    it("contains SVG chart renderer function", () => {
      expect(html).toContain("renderInteractiveSvgChart");
      expect(html).toContain("viewBox");
    });

    it("contains engine control buttons", () => {
      expect(html).toContain('id="btnStart"');
      expect(html).toContain('id="btnPause"');
      expect(html).toContain('id="btnResume"');
      expect(html).toContain('id="btnStop"');
      expect(html).toContain("engineAction");
    });

    it("contains alerts panel", () => {
      expect(html).toContain('id="alertsList"');
      expect(html).toContain("最近告警");
      expect(html).toContain("ackAll");
    });

    it("contains health strip", () => {
      expect(html).toContain('id="brokerHealth"');
      expect(html).toContain('id="dataHealth"');
      expect(html).toContain('id="alertSummary"');
    });

    it("contains staleness table", () => {
      expect(html).toContain('id="stalenessTable"');
      expect(html).toContain("数据源状态");
    });

    // ── API calls ────────────────────────────────────────────

    it("calls health endpoint", () => {
      expect(html).toContain("/quant/api/health");
    });

    it("calls staleness endpoint", () => {
      expect(html).toContain("/quant/api/staleness");
    });

    it("calls alerts endpoint", () => {
      expect(html).toContain("/quant/api/alerts");
    });

    it("calls alerts summary endpoint", () => {
      expect(html).toContain("/quant/api/alerts/summary");
    });

    it("calls engine action endpoints", () => {
      expect(html).toContain("/quant/api/engine/");
    });

    // ── SSE + Auto-refresh ────────────────────────────────────

    it("sets up SSE connection for real-time updates", () => {
      expect(html).toContain("SSE.connect()");
      expect(html).toContain("EventSource");
      expect(html).toContain("/quant/api/events");
    });

    it("falls back to polling when SSE disconnected", () => {
      expect(html).toContain("SSE.connected");
      expect(html).toContain("refreshAll()");
    });

    it("shows SSE connection status indicator", () => {
      expect(html).toContain('id="sseStatus"');
      expect(html).toContain("实时");
      expect(html).toContain("轮询");
    });

    it("refreshAll calls all loaders", () => {
      expect(html).toContain("loadHealth");
      expect(html).toContain("loadStaleness");
      expect(html).toContain("loadAlerts");
      expect(html).toContain("loadEquityChart");
    });

    // ── CSS classes ─────────────────────────────────────────

    it("includes chart CSS classes", () => {
      expect(html).toContain(".chart-line");
      expect(html).toContain(".chart-area");
      expect(html).toContain(".chart-grid");
    });

    it("includes alert CSS classes", () => {
      expect(html).toContain(".alert-item");
      expect(html).toContain(".sev-critical");
      expect(html).toContain(".sev-warning");
    });

    it("includes badge variants for status", () => {
      expect(html).toContain(".badge-connected");
      expect(html).toContain(".badge-degraded");
      expect(html).toContain(".badge-disconnected");
    });

    // ── Responsive layout ───────────────────────────────────

    it("uses 3-column grid with responsive breakpoints", () => {
      expect(html).toContain("grid-template-columns: repeat(3, 1fr)");
      expect(html).toContain("@media (max-width: 1200px)");
      expect(html).toContain("@media (max-width: 700px)");
    });
  });

  describe("handleDashboardRequest", () => {
    const result = handleDashboardRequest();

    it("returns ok status", () => {
      expect(result.status).toBe("ok");
    });

    it("includes plugin name", () => {
      expect(result.name).toBe("Quant Trading Plugin");
    });

    it("lists all endpoints", () => {
      const endpoints = result.endpoints as string[];
      expect(endpoints.length).toBeGreaterThanOrEqual(17);
    });

    it("includes new monitoring endpoints", () => {
      const endpoints = result.endpoints as string[];
      expect(endpoints.some((e) => e.includes("/quant/api/health"))).toBe(true);
      expect(endpoints.some((e) => e.includes("/quant/api/staleness"))).toBe(true);
      expect(endpoints.some((e) => e.includes("/quant/api/alerts"))).toBe(true);
      expect(endpoints.some((e) => e.includes("/quant/api/alerts/summary"))).toBe(true);
      expect(endpoints.some((e) => e.includes("/quant/api/alerts/acknowledge"))).toBe(true);
    });
  });
});
