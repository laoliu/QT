/**
 * Resolve the absolute path to the python/ directory inside this extension.
 *
 * When running from source (`extensions/quant-trading/src/bridge/`), two `..`
 * hops reach `extensions/quant-trading/python`.  After bundling (`dist/extensions/
 * quant-trading/index.js`), the same relative path resolves to `dist/python`
 * which does not exist.  This helper detects the dist build and corrects the
 * path, falling back to the relative approach when running unbundled.
 */
import path from "node:path";

/** Root of `extensions/quant-trading/python`. */
export function getPythonDir(): string {
  const base = import.meta.dirname ?? __dirname;

  // When bundled, `base` could be `<repo>/dist` (for shared chunks) or
  // `<repo>/dist/extensions/quant-trading` (for the plugin entry).
  // Detect either case and resolve to `<repo>/extensions/quant-trading/python`.
  const distSep = `${path.sep}dist${path.sep}`;
  const distEnd = `${path.sep}dist`;
  if (base.includes(distSep)) {
    const repoRoot = base.split(distSep)[0];
    return path.join(repoRoot, "extensions", "quant-trading", "python");
  }
  if (base.endsWith(distEnd)) {
    const repoRoot = base.slice(0, -distEnd.length);
    return path.join(repoRoot, "extensions", "quant-trading", "python");
  }

  // Unbundled (running via tsx/Jiti from source): relative resolution works.
  return path.resolve(base, "../../python");
}

/** Root of `extensions/quant-trading/python/strategies`. */
export function getStrategiesDir(): string {
  return path.join(getPythonDir(), "strategies");
}
