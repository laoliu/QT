import { describe, expect, it, vi } from "vitest";
import { createResearchOperationsTool } from "./research-operations.js";
import { createPositionSizingTool } from "./position-sizing.js";
import { createChatTools } from "../services/chat-tool-policy.js";

describe("chat research operations", () => {
  it("reads the fixed deployment and reports scheduler unavailability independently", async () => {
    const read = vi.fn().mockResolvedValue({ scope: "research", data_as_of: "2026-10-19" });
    const schedule = vi.fn().mockRejectedValue(new Error("task unavailable"));
    const tool = createResearchOperationsTool({ read, schedule });
    const result = await tool.execute({ action: "overview", scope: "research" });
    expect(read).toHaveBeenCalledWith({ section: "overview", scope: "research", date: undefined, offset: 0, limit: 20 });
    expect(result).toMatchObject({ status: "ok", data: { data_as_of: "2026-10-19", scheduler: { available: false } } });
    await tool.execute({ action: "predictions", date: "2026-10-19", offset: 20, limit: 10 });
    expect(schedule).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid dates and pagination before querying or reporting success", async () => {
    const read = vi.fn(); const tool = createResearchOperationsTool({ read });
    await expect(tool.execute({ action: "predictions", date: "2026-02-30" })).rejects.toThrow();
    await expect(tool.execute({ action: "predictions", limit: 1000 })).rejects.toThrow();
    await expect(tool.execute({ action: "start" })).rejects.toThrow();
    expect(read).not.toHaveBeenCalled();
    const unavailable = createResearchOperationsTool({ read: vi.fn().mockRejectedValue(new Error("sensitive stderr")), schedule: vi.fn().mockResolvedValue({}) });
    expect(await unavailable.execute({ action: "overview" })).toMatchObject({ status: "unavailable" });
  });

  it("does not turn unavailable account equity into a made-up position size", async () => {
    const getEquity = vi.fn().mockRejectedValue(new Error("not connected"));
    const raw = createPositionSizingTool({} as any, {} as any, getEquity);
    const chat = createChatTools([raw]).get("position_sizing")!;
    const result = await chat.execute({ action: "compute", price: 10, method: "risk_pct", risk_pct: 0.01, stop_pct: 0.05 });
    expect(getEquity).toHaveBeenCalledWith(true);
    expect(result).toContain("权益不可用");
    expect(result).not.toContain("建议数量");
  });
});
