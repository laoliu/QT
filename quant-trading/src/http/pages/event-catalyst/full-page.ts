/**
 * Event Catalyst Page — assembles CSS, body HTML, and main script.
 *
 * Tabs: 全局扫描, 美股异动, 对标映射, 催化选股, 利好利空, 科技前沿.
 * Data fetched from POST `/quant/admin/api/event-catalyst`.
 */
import { getEventCatalystBodyHtml } from "./body-html.js";
import { getEventCatalystMainScript } from "./main-script.js";
import { getEventCatalystStyles } from "./styles.js";

export function getEventCatalystPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  const baseUrlJson = JSON.stringify(b);
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>事件催化分析 — Quant Trading 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>${getEventCatalystStyles()}</style>
</head>
<body>
${getEventCatalystBodyHtml(b)}
<script>
var B = ${baseUrlJson};
var API = B + "/quant/admin/api/event-catalyst";
var SENT_API = B + "/quant/admin/api/sentiment-signal";
var SCAN_HISTORY_API = B + "/quant/admin/api/scan-history";
var CAL_API = B + "/quant/admin/api/stock-calendar";
${getEventCatalystMainScript()}
</script>
</body>
</html>`;
}
