/** Read-only projections of the independent canonical research/Paper deployment. */
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { getPythonDir } from "../bridge/python-dir.js";
import type { QuantTradingRuntime } from "../runtime.js";
import type { QuantTradingConfig } from "../types.js";
import type { HttpHandler } from "./auth-middleware.js";
import { getResearchOperationsHtml } from "./pages/research-operations-page.js";

const exec = promisify(execFile);
type ObjectData = Record<string, any>;
export type ResearchOperationsQuery = { section: "overview" | "predictions"; scope: "paper" | "research"; date?: string; offset: number; limit: number };
type Query = ResearchOperationsQuery;
export type ResearchOperationsOptions = {
  read?: (query: Query) => Promise<ObjectData>;
  schedule?: () => Promise<ObjectData>;
  cacheMs?: number;
};

export function parseResearchOperationsQuery(url: string): Query {
  const params = new URL(url, "http://localhost").searchParams;
  for (const key of params.keys()) {
    if (!["section", "scope", "date", "offset", "limit"].includes(key)) throw new Error("未知查询参数");
  }
  const section = params.get("section") ?? "overview";
  const scope = params.get("scope") ?? "paper";
  const date = params.get("date") || undefined;
  const offsetText = params.get("offset") ?? "0";
  const limitText = params.get("limit") ?? "50";
  if (!["overview", "predictions"].includes(section) || !["paper", "research"].includes(scope)
      || (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date))
        || new Date(date).toISOString().slice(0, 10) !== date))
      || !/^\d+$/.test(offsetText) || !/^\d+$/.test(limitText)) throw new Error("查询参数无效");
  const offset = Number(offsetText), limit = Number(limitText);
  if (offset > 1_000_000 || limit < 1 || limit > 100) throw new Error("分页参数超出范围");
  return { section: section as Query["section"], scope: scope as Query["scope"], date, offset, limit };
}

export async function readResearchDeployment(query: Query): Promise<ObjectData> {
  const python = process.env.QT_RESEARCH_VIEW_PYTHON
    ?? (process.platform === "win32" ? "G:/qtdata/research/validation-env-py313/Scripts/python.exe" : "python3");
  const deployment = process.env.QT_RESEARCH_DEPLOYMENT ?? "G:/qtdata/research/operations/csi500/deployment.yaml";
  const args = [path.join(getPythonDir(), "scripts/research_operations_view.py"), "--deployment", deployment,
    "--section", query.section, "--scope", query.scope, "--offset", String(query.offset), "--limit", String(query.limit)];
  if (query.date) args.push("--date", query.date);
  const affinity = process.env.QT_RESEARCH_VIEW_CPU_AFFINITY ?? (process.platform === "win32" ? "16,17,18,19" : "");
  if (affinity) args.push("--cpu-affinity", affinity);
  const { stdout } = await exec(python, args, {
    windowsHide: true, timeout: 90_000, maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONIOENCODING: "utf-8" },
  });
  const result = JSON.parse(stdout);
  if (result.status !== "ok") throw new Error("research projection unavailable");
  return result.data;
}

export async function readResearchSchedule(): Promise<ObjectData> {
  const taskName = process.env.QT_RESEARCH_TASK_NAME ?? "QT-Research-Paper-CSI500";
  if (process.platform !== "win32") return { available: false, task_name: taskName, message: "当前主机不支持 Windows 任务查询" };
  const script = `$ErrorActionPreference='Stop'
$t=Get-ScheduledTask -TaskName $env:QT_RESEARCH_TASK_NAME
$i=Get-ScheduledTaskInfo -TaskName $env:QT_RESEARCH_TASK_NAME
function Stamp($v){if($v -and $v.Year -gt 2000){$v.ToUniversalTime().ToString('o')}else{$null}}
[ordered]@{available=$true;task_name=$t.TaskName;state=[string]$t.State;last_run=(Stamp $i.LastRunTime);last_result=$i.LastTaskResult;next_run=(Stamp $i.NextRunTime);observed_at=[DateTimeOffset]::UtcNow.ToString('o')}|ConvertTo-Json -Compress`;
  const { stdout } = await exec("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    windowsHide: true, timeout: 12_000, maxBuffer: 32_768,
    env: { ...process.env, QT_RESEARCH_TASK_NAME: taskName },
  });
  return JSON.parse(stdout.replace(/^\uFEFF/, ""));
}

export function createResearchOperationsHandler(options: ResearchOperationsOptions = {}): HttpHandler {
  const read = options.read ?? readResearchDeployment;
  const schedule = options.schedule ?? readResearchSchedule;
  const cache = new Map<string, { expires: number; result: ObjectData }>();
  const inflight = new Map<string, Promise<ObjectData>>();
  const ttl = options.cacheMs ?? 30_000;
  return async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    const send = (status: number, body: ObjectData) => {
      res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(body));
    };
    if (req.method !== "GET") {
      res.setHeader("Allow", "GET");
      send(405, { status: "error", message: "研究运营页面仅允许只读 GET 查询" });
      return;
    }
    let query: Query;
    try { query = parseResearchOperationsQuery(req.url ?? ""); }
    catch { send(400, { status: "error", message: "查询参数无效；仅支持固定部署的日期与分页查询" }); return; }
    const key = JSON.stringify(query);
    const entry = cache.get(key);
    if (entry && entry.expires > Date.now()) {
      send(200, { status: "ok", data: entry.result, cached: true }); return;
    }
    // Cache is bounded and concurrent browser refreshes share one read-only child.
    let pending = inflight.get(key);
    if (!pending && inflight.size >= 4) {
      send(429, { status: "error", message: "正在读取研究证据，请稍后刷新" }); return;
    }
    if (!pending) {
      pending = (async () => {
        if (query.section !== "overview") return read(query);
        const [projection, task] = await Promise.allSettled([read(query), schedule()]);
        if (projection.status === "rejected") throw projection.reason;
        return { ...projection.value, scheduler: task.status === "fulfilled" ? task.value
          : { available: false, message: "无法读取 Windows 调度任务；不能据此判断任务正常" } };
      })();
      inflight.set(key, pending);
    }
    try {
      const result = await pending;
      if (cache.size >= 32) cache.delete(cache.keys().next().value!);
      cache.set(key, { expires: Date.now() + ttl, result });
      send(200, { status: "ok", data: result, cached: false });
    } catch {
      // Never echo child stderr, environment variables or arbitrary file content.
      send(503, { status: "error", message: "研究证据暂不可用，请检查固定 Python 环境、部署文件或进程超时" });
    } finally { inflight.delete(key); }
  };
}

export function registerResearchOperationsRoutes(
  api: QuantTradingRuntime, config: QuantTradingConfig,
  secure: (handler: HttpHandler) => HttpHandler, securePublic: (handler: HttpHandler) => HttpHandler,
): void {
  api.registerHttpRoute({ auth: "plugin", path: "/quant/api/research/operations", handler: secure(createResearchOperationsHandler()) });
  api.registerHttpRoute({ auth: "plugin", path: "/quant/admin/research", handler: securePublic((req, res) => {
    if (req.method !== "GET") { res.writeHead(405, { Allow: "GET" }); res.end(); return; }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    res.end(getResearchOperationsHtml("", config.apiToken));
  }) });
}
