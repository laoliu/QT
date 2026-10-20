/** Isolated read-only preview; does not bootstrap trading services or brokers.
 * Run: node --import tsx scripts/serve-research-operations.ts
 */
import { createServer } from "node:http";
import { createResearchOperationsHandler } from "../quant-trading/src/http/research-operations-api.js";
import { getResearchOperationsHtml } from "../quant-trading/src/http/pages/research-operations-page.js";

const handler = createResearchOperationsHandler();
const port = Number(process.env.QT_RESEARCH_VIEW_PORT ?? "18993");
const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const pathname = url.pathname;
  if (["/quant/", "/quant/admin/ui", "/quant/admin/planner", "/quant/admin/stock"].includes(pathname) && req.method === "GET") {
    res.writeHead(302, { Location: "http://127.0.0.1:18891" + pathname + url.search }); res.end(); return;
  }
  if (pathname === "/quant/api/research/operations") { await handler(req, res); return; }
  if (["/", "/quant/admin/research"].includes(pathname) && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    res.end(getResearchOperationsHtml("")); return;
  }
  res.writeHead(404); res.end("Not found");
});
server.listen(port, "127.0.0.1", () => console.log(`Research view: http://127.0.0.1:${port}/quant/admin/research`));
process.on("SIGINT", () => server.close());
process.on("SIGTERM", () => server.close());
