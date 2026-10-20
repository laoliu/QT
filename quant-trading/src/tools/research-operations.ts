import { parseResearchOperationsQuery, readResearchDeployment, readResearchSchedule, type ResearchOperationsOptions } from "../http/research-operations-api.js";

/** Fixed deployment only: no arbitrary executable, path or scheduler mutation. */
export function createResearchOperationsTool(options: ResearchOperationsOptions = {}) {
  return {
    name: "research_operations",
    description: "查询固定研究/Paper 部署的运行概览、证据日期、预测列表和调度状态。只读查询，不启动研究、训练、交易或 Windows 任务。",
    parameters: {
      type: "object", properties: {
        action: { type: "string", enum: ["overview", "predictions"] },
        scope: { type: "string", enum: ["research", "paper"] },
        date: { type: "string", description: "证据日期 YYYY-MM-DD，省略则查询当前证据" },
        offset: { type: "integer", minimum: 0, maximum: 1_000_000 },
        limit: { type: "integer", minimum: 1, maximum: 100 },
      }, required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      if (!["overview", "predictions"].includes(String(args.action))) throw new Error("未知研究查询操作");
      const params = new URLSearchParams({ section: String(args.action), scope: String(args.scope ?? "paper"),
        offset: String(args.offset ?? 0), limit: String(args.limit ?? 20) });
      if (args.date != null) params.set("date", String(args.date));
      const query = parseResearchOperationsQuery(`/?${params}`);
      try {
        const read = options.read ?? readResearchDeployment;
        if (query.section !== "overview") return { status: "ok", data: await read(query) };
        const [projection, schedule] = await Promise.allSettled([read(query), (options.schedule ?? readResearchSchedule)()]);
        if (projection.status === "rejected") throw projection.reason;
        return { status: "ok", data: { ...projection.value, scheduler: schedule.status === "fulfilled" ? schedule.value : { available: false, message: "调度状态不可用，不能据此判断任务正常" } } };
      } catch {
        return { status: "unavailable", message: "研究证据暂不可用，请检查部署环境或查询日期；不能据此认定运行正常" };
      }
    },
  };
}
