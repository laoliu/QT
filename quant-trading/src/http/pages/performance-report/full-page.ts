/**
 * Performance Report Page — full page assembly.
 */

import { getPerformanceReportBodyHtml } from "./body-html.js";
import { getPerformanceReportMainScript } from "./main-script.js";
import { PERFORMANCE_REPORT_CSS } from "./styles.js";

/** Return the full HTML for the performance report page. */
export function getPerformanceReportPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>交易绩效报告 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
${PERFORMANCE_REPORT_CSS}
</style>
</head>
<body>
${getPerformanceReportBodyHtml(b)}

<script>
${getPerformanceReportMainScript(JSON.stringify(b))}
</script>
</body>
</html>`;
}
