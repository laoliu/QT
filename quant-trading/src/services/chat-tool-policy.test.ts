import { describe, expect, it, vi } from "vitest";
import { createChatTools } from "./chat-tool-policy.js";

const parameters = { type: "object", properties: { action: { type: "string", enum: ["plan", "execute"] } }, required: ["action"] };

describe("chat action permissions", () => {
  it.each([
    ["portfolio", "buy"], ["portfolio_rebalance", "execute"], ["data_cache", "clear"], ["data_cache", "cleanup"],
    ["ml_ranking", "train"], ["ml_ranking", "train_tushare"], ["ml_ranking", "export"], ["ml_ranking", "execute_paper"],
    ["watchlist", "add"], ["favorites", "clear"], ["trade_journal", "add_note"], ["recommend_tracker", "refresh"],
    ["strategy_health", "remove"], ["trading_signal", "clear"], ["strategy_router", "build_table"], ["aipha_backtest", "run_from_yaml"],
  ])("blocks %s/%s before invoking the real tool", async (name, action) => {
    const execute = vi.fn();
    const tool = createChatTools([{ name, parameters: { properties: { action: { type: "string" } } }, execute }]).get(name)!;
    await expect(tool.execute({ action })).rejects.toThrow("未授权");
    expect(execute).not.toHaveBeenCalled();
  });

  it("restricts advertised actions without mutating the shared tool schema", async () => {
    const execute = vi.fn().mockResolvedValue({ planned: true });
    const tool = createChatTools([{ name: "portfolio_rebalance", parameters, execute }]).get("portfolio_rebalance")!;
    expect((tool.parameters!.properties as any).action.enum).toEqual(["plan"]);
    expect(parameters.properties.action.enum).toEqual(["plan", "execute"]);
    await tool.execute({ action: "plan" });
    expect(execute).toHaveBeenCalledWith({ action: "plan" });
    await expect(tool.execute({})).rejects.toThrow();
    await expect(tool.execute(null as any)).rejects.toThrow();
  });

  it.each([["stock_screen", "persist_results", false], ["stock_recommend", "persist_results", false], ["backtest", "persist_results", false], ["strategy_explorer", "saveBest", false], ["position_sizing", "require_equity", true]])("enforces %s internal analysis settings", async (name, key, value) => {
    const execute = vi.fn();
    const action = name === "stock_screen" ? "screen" : name === "backtest" ? "run" : name === "position_sizing" ? "compute" : "explore";
    const rawParameters = { properties: { action: { type: "string" }, [key]: { type: "boolean" } } };
    const tool = createChatTools([{ name, parameters: rawParameters, execute }]).get(name)!;
    const args = name === "stock_recommend" ? { [key]: !value } : { action, [key]: !value };
    await tool.execute(args);
    expect(execute).toHaveBeenCalledWith({ ...args, [key]: value });
    expect((tool.parameters!.properties as any)[key]).toBeUndefined();
  });

  it("excludes unreviewed tools and rejects omitted mutation parameters", async () => {
    const execute = vi.fn();
    const tools = createChatTools([{ name: "live_trading", execute }, { name: "constructor", execute }, { name: "equity_screening", execute }, { name: "ml_ranking", parameters: { properties: { action: {}, outputPath: {} } }, execute }]);
    expect(tools.has("live_trading")).toBe(false);
    expect(tools.has("equity_screening")).toBe(false);
    expect(tools.has("constructor")).toBe(false);
    await expect(tools.get("ml_ranking")!.execute({ action: "predict", outputPath: "should-not-write" })).rejects.toThrow("不支持参数");
    expect(execute).not.toHaveBeenCalled();
  });
});
