import { getAdminBodyHtml } from "./body-html.js";
import { getAdminMainScript } from "./main-script.js";
import { getChatMarkdownScript } from "./chat-markdown.js";
/**
 * Admin Management Page — assembles CSS, HTML body, and JS into a full HTML page.
 */
import { ADMIN_CSS } from "./styles.js";

export function getAdminPageHtml(
  baseUrl: string,
  apiToken?: string,
): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易管理面板</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>${ADMIN_CSS}</style>
</head>
<body>
${getAdminBodyHtml(b)}
<script>${getChatMarkdownScript()}</script>
<script>
window.onerror=function(msg,src,line){console.error('[admin] JS error:',msg,'at line',line,src)};
var B = ${JSON.stringify(b)};
var _T = ${JSON.stringify(apiToken ?? "")};
${getAdminMainScript()}
</script>
</body>
</html>`;
}
