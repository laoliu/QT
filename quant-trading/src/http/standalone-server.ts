/**
 * Standalone HTTP server for quant-trading routes.
 *
 * This lightweight Node.js HTTP server serves the admin UI and API routes
 * independently on a configurable port (e.g. 18891).
 *
 * Routes are collected via {@link createRouteCollector} which wraps
 * `api.registerHttpRoute` to intercept registrations.
 */
import * as http from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";

export type RouteHandler = (req: IncomingMessage, res: ServerResponse) => Promise<boolean | void> | boolean | void;

export interface CollectedRoute {
  path: string;
  handler: RouteHandler;
  match: "exact" | "prefix";
}

let _server: http.Server | undefined;

/**
 * Create a route collector that stores application routes for the standalone server.
 *
 * Usage:
 * ```ts
 * const collector = createRouteCollector();
 * // Register routes through the collector while bootstrapping the application.
 * startStandaloneServer(collector.routes, port, logger);
 * ```
 */
export function createRouteCollector() {
  const routes: CollectedRoute[] = [];

  return {
    routes,
    collect(params: { path: string; handler: RouteHandler; match?: "exact" | "prefix" }) {
      routes.push({
        path: params.path,
        handler: params.handler,
        match: params.match ?? "exact",
      });
    },
  };
}

/**
 * Start the standalone HTTP server on the given port.
 * If a server is already running, it is closed first.
 */
export function startStandaloneServer(
  routes: CollectedRoute[],
  port: number,
  logger: { info: (msg: string) => void; warn: (msg: string) => void; error: (msg: string) => void },
): http.Server {
  if (_server) {
    try { _server.close(); } catch { /* ignore */ }
    _server = undefined;
  }

  // Build lookup maps for fast routing
  const exactRoutes = new Map<string, RouteHandler>();
  const prefixRoutes: { prefix: string; handler: RouteHandler }[] = [];

  for (const route of routes) {
    if (route.match === "prefix") {
      prefixRoutes.push({ prefix: route.path, handler: route.handler });
    } else {
      exactRoutes.set(route.path, route.handler);
    }
  }
  // Sort prefix routes longest-first for greedy matching
  prefixRoutes.sort((a, b) => b.prefix.length - a.prefix.length);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://localhost:${port}`);
    const pathname = url.pathname;

    // Try exact match first
    let handler = exactRoutes.get(pathname);

    // Then try prefix match
    if (!handler) {
      for (const pr of prefixRoutes) {
        if (pathname.startsWith(pr.prefix)) {
          handler = pr.handler;
          break;
        }
      }
    }

    if (handler) {
      try {
        await handler(req, res);
      } catch (err) {
        logger.error(`[standalone] ${req.method} ${pathname} error: ${err}`);
        if (!res.headersSent) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ status: "error", message: "Internal server error" }));
        }
      }
    } else {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error", message: "Not found" }));
    }
  });

  server.listen(port, "127.0.0.1", () => {
    logger.info(`[standalone] HTTP server listening on http://127.0.0.1:${port}`);
  });

  server.on("error", (err) => {
    logger.error(`[standalone] server error: ${err}`);
  });

  _server = server;
  return server;
}

/** Stop the standalone server if running. */
export function stopStandaloneServer(): void {
  if (_server) {
    _server.close();
    _server = undefined;
  }
}
