/**
 * Investment Planner Page — self-contained SPA for investment planning,
 * stock screening, portfolio monitoring, and trade signal alerts.
 *
 * Assembled from split modules:
 *   styles.ts      — page-specific CSS (~100 lines)
 *   body-html.ts   — HTML skeleton with tabs & forms (~1290 lines)
 *   tab-nav.ts     — independent tab-switching script (~45 lines)
 *   main-script.ts — all application logic (~5880 lines)
 */

import { getPlannerBodyHtml } from "./body-html.js";
import { getPlannerMainScript } from "./main-script.js";
import { PLANNER_CSS } from "./styles.js";
import { TAB_NAV_SCRIPT } from "./tab-nav.js";

/** Return the full HTML for the investment planner page. */
export function getInvestmentPlannerPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>投资规划 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
${PLANNER_CSS}
</style>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
${getPlannerBodyHtml(b)}

<div class="toast" id="toast"></div>

<!--
  Tab navigation lives in its own <script> so a syntax/runtime error
  in the main script block can never break tab switching.
-->
<script>
${TAB_NAV_SCRIPT}
</script>

<script>
${getPlannerMainScript(JSON.stringify(b))}
</script>
</body>
</html>`;
}
