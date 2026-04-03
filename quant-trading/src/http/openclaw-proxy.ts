/**
 * OpenClaw iframe proxy — starts a dedicated reverse-proxy port
 * that strips X-Frame-Options / CSP headers and optionally injects
 * the gateway token into `sessionStorage` so the embedded chat
 * auto-authenticates.
 *
 * Uses only Node.js built-in `http` module — no extra dependencies.
 */
import * as http from "node:http";

export interface OpenClawProxyOptions {
  /** OpenClaw Gateway origin, e.g. "http://localhost:18789" */
  target: string;
  /** Port for the proxy to listen on */
  port: number;
  /** Gateway auth token — if set, injected into HTML for auto-login */
  gatewayToken?: string;
}

/**
 * Start the OpenClaw iframe proxy.
 * Returns the `http.Server` so callers can close it on shutdown.
 */
export function startOpenClawProxy(opts: OpenClawProxyOptions): http.Server {
  const { target, port, gatewayToken } = opts;
  const targetUrl = new URL(target);
  const targetHost = targetUrl.hostname;
  const targetPort = targetUrl.port || "80";

  // Build token-injection script (runs before OpenClaw's own JS)
  const tokenScript = gatewayToken
    ? `<script>(function(){try{` +
      `var p=location.protocol==="https:"?"wss":"ws";` +
      `var u=p+"://"+location.host;` +
      `var k="openclaw.control.token.v1:"+u;` +
      `if(!sessionStorage.getItem(k))` +
      `sessionStorage.setItem(k,${JSON.stringify(gatewayToken)});` +
      `}catch(e){}})()</script>`
    : "";

  const server = http.createServer((clientReq, clientRes) => {
    const reqOpts: http.RequestOptions = {
      hostname: targetHost,
      port: targetPort,
      path: clientReq.url,
      method: clientReq.method,
      headers: { ...clientReq.headers, host: `${targetHost}:${targetPort}` },
    };

    const proxyReq = http.request(reqOpts, (proxyRes) => {
      // ── Strip iframe-blocking headers ──
      delete proxyRes.headers["x-frame-options"];
      delete proxyRes.headers["content-security-policy"];

      const isHtml = proxyRes.headers["content-type"]?.includes("text/html");

      if (tokenScript && isHtml) {
        // Buffer the HTML response to inject token script
        const chunks: Buffer[] = [];
        proxyRes.on("data", (chunk: Buffer) => chunks.push(chunk));
        proxyRes.on("end", () => {
          let html = Buffer.concat(chunks).toString("utf8");
          html = html.replace("</head>", `${tokenScript}</head>`);
          const buf = Buffer.from(html, "utf8");
          // Fix content-length after injection
          delete proxyRes.headers["content-length"];
          proxyRes.headers["content-length"] = String(buf.length);
          clientRes.writeHead(proxyRes.statusCode ?? 200, proxyRes.headers);
          clientRes.end(buf);
        });
      } else {
        clientRes.writeHead(proxyRes.statusCode ?? 200, proxyRes.headers);
        proxyRes.pipe(clientRes, { end: true });
      }
    });

    proxyReq.on("error", (err) => {
      clientRes.writeHead(502, { "Content-Type": "text/plain" });
      clientRes.end(`OpenClaw proxy error: ${err.message}`);
    });

    clientReq.pipe(proxyReq, { end: true });
  });

  // ── WebSocket upgrade forwarding ──
  server.on("upgrade", (clientReq, clientSocket, head) => {
    const reqOpts: http.RequestOptions = {
      hostname: targetHost,
      port: targetPort,
      path: clientReq.url,
      method: clientReq.method,
      headers: { ...clientReq.headers, host: `${targetHost}:${targetPort}` },
    };

    const proxyReq = http.request(reqOpts);

    proxyReq.on("upgrade", (_proxyRes, proxySocket, proxyHead) => {
      // Forward the raw 101 Switching Protocols response
      clientSocket.write(
        `HTTP/1.1 101 Switching Protocols\r\n` +
          `Upgrade: websocket\r\n` +
          `Connection: Upgrade\r\n` +
          // Forward Sec-WebSocket-Accept and other headers
          Object.entries(_proxyRes.headers)
            .filter(([k]) => !["upgrade", "connection"].includes(k.toLowerCase()))
            .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
            .join("\r\n") +
          "\r\n\r\n",
      );

      if (proxyHead.length) clientSocket.write(proxyHead);

      proxySocket.pipe(clientSocket);
      clientSocket.pipe(proxySocket);

      proxySocket.on("error", () => clientSocket.destroy());
      clientSocket.on("error", () => proxySocket.destroy());
    });

    proxyReq.on("error", () => {
      clientSocket.destroy();
    });

    proxyReq.end();
  });

  server.listen(port, () => {
    console.log(`[openclaw-proxy] iframe proxy on http://localhost:${port} → ${target}`);
  });

  return server;
}
