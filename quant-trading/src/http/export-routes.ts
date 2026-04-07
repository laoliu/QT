/**
 * HTTP routes for data export: trigger export and download files.
 *
 * POST /quant/export         — trigger a new export
 * GET  /quant/export/:filename — download an exported file
 */
import fs from "node:fs/promises";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type {
  DataExportService,
  ExportFormat,
  ExportTarget,
} from "../services/data-export.js";
import type { HttpHandler } from "./auth-middleware.js";
import { parseJsonBody } from "./parse-body.js";

export interface ExportRouteDeps {
  api: OpenClawPluginApi;
  exportService: DataExportService;
  secure: (handler: HttpHandler) => HttpHandler;
}

/** Write a JSON response on a raw ServerResponse. */
function json(res: ServerResponse, data: unknown, status = 200): void {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(body);
}

const CONTENT_TYPES: Record<string, string> = {
  ".csv": "text/csv; charset=utf-8",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

export function registerExportRoutes(deps: ExportRouteDeps): void {
  const { api, exportService, secure } = deps;

  // POST /quant/export — trigger a new export
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/export",
    handler: secure(async (req: IncomingMessage, res: ServerResponse) => {
      if (req.method !== "POST") {
        res.writeHead(405, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "error", message: "Method not allowed" }));
        return;
      }

      const body = await parseJsonBody<Record<string, unknown>>(req);
      const target = body.target as ExportTarget | undefined;
      const format = (body.format as ExportFormat) ?? "csv";

      if (!target) {
        json(res, { status: "error", message: "缺少 target 参数" }, 400);
        return;
      }

      const validTargets: ExportTarget[] = [
        "positions",
        "orders",
        "trades",
        "backtest",
        "screen",
        "equity",
      ];
      if (!validTargets.includes(target)) {
        json(
          res,
          { status: "error", message: `无效 target，可选: ${validTargets.join(", ")}` },
          400,
        );
        return;
      }

      const options: Record<string, unknown> = {};
      if (body.id) options.id = body.id;
      if (body.mode) options.mode = body.mode;
      if (body.content) options.content = body.content;

      const result = await exportService.export(target, format, options);
      if (!result.ok) {
        json(res, { status: "error", message: result.error }, 500);
        return;
      }

      json(res, {
        status: "ok",
        filename: result.filename,
        rowCount: result.rowCount,
        downloadUrl: `/quant/export/${result.filename}`,
      });
    }),
  });

  // GET /quant/export/:filename — download an exported file
  // Since the framework uses exact path matching, we register a wildcard handler
  // that checks for the /quant/export/ prefix and extracts the filename.
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/export/download",
    handler: secure(async (req: IncomingMessage, res: ServerResponse) => {
      if (req.method !== "GET") {
        res.writeHead(405);
        res.end();
        return;
      }

      const url = new URL(req.url ?? "/", "http://localhost");
      const filename = url.searchParams.get("file");

      if (!filename) {
        json(res, { status: "error", message: "缺少 ?file= 参数" }, 400);
        return;
      }

      // Security: prevent path traversal
      const sanitized = path.basename(filename);
      if (sanitized !== filename || filename.includes("..")) {
        json(res, { status: "error", message: "无效文件名" }, 400);
        return;
      }

      const filePath = path.join(exportService.getExportDir(), sanitized);
      try {
        const stat = await fs.stat(filePath);
        const ext = path.extname(filePath).toLowerCase();
        const contentType = CONTENT_TYPES[ext] ?? "application/octet-stream";

        res.writeHead(200, {
          "Content-Type": contentType,
          "Content-Length": String(stat.size),
          "Content-Disposition": `attachment; filename="${encodeURIComponent(sanitized)}"`,
        });

        // Stream the file
        const { createReadStream } = await import("node:fs");
        const stream = createReadStream(filePath);
        stream.pipe(res);
        stream.on("error", () => {
          if (!res.headersSent) {
            json(res, { status: "error", message: "文件读取失败" }, 500);
          }
        });
      } catch {
        json(res, { status: "error", message: "文件不存在或已过期" }, 404);
      }
    }),
  });
}
