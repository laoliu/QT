/**
 * Utility for parsing JSON request bodies from raw IncomingMessage streams.
 * Replaces the repetitive chunks.push / on('end') / JSON.parse pattern.
 */
import type { IncomingMessage } from "node:http";

/**
 * Read and parse a JSON body from an incoming HTTP request.
 *
 * @param req - The raw Node.js IncomingMessage
 * @param maxBytes - Maximum body size in bytes (default: 1 MB). Returns an error if exceeded.
 * @returns Parsed body, or null if empty
 */
export function parseJsonBody<T = unknown>(req: IncomingMessage, maxBytes = 1_048_576): Promise<T> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let totalLength = 0;

    req.on("data", (chunk: Buffer) => {
      totalLength += chunk.length;
      if (totalLength > maxBytes) {
        req.destroy();
        reject(new Error(`Request body exceeds ${maxBytes} bytes`));
        return;
      }
      chunks.push(chunk);
    });

    req.on("error", reject);

    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf-8") || "{}";
        resolve(JSON.parse(raw) as T);
      } catch (e) {
        reject(new Error(`Invalid JSON: ${(e as Error).message}`));
      }
    });
  });
}
