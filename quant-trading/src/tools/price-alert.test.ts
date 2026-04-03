import { describe, expect, it, vi } from "vitest";
import { PriceAlertService } from "../services/price-alert.js";
import { createPriceAlertTool } from "./price-alert.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  const service = new PriceAlertService({ maxAlerts: 10 });
  const tool = createPriceAlertTool(api, {} as never, service);
  return { tool, service };
}

describe("price_alert tool", () => {
  it("has correct name and required fields", () => {
    const { tool } = makeTool();
    expect(tool.name).toBe("price_alert");
    expect(tool.description).toContain("条件预警");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports add, list, and remove actions", () => {
    const { tool } = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("add");
    expect(actionProp.enum).toContain("list");
    expect(actionProp.enum).toContain("remove");
  });

  it("adds a price_above alert", async () => {
    const { tool, service } = makeTool();
    const result = (await tool.execute({
      action: "add",
      symbol: "600519",
      conditionType: "price_above",
      threshold: 2000,
    })) as Record<string, unknown>;

    expect(result.action).toBe("add");
    expect(result.alertId).toBeDefined();
    expect(result.activeCount).toBe(1);
    expect(service.listAlerts()).toHaveLength(1);
  });

  it("lists alerts", async () => {
    const { tool } = makeTool();
    await tool.execute({
      action: "add",
      symbol: "600519",
      conditionType: "price_above",
      threshold: 2000,
    });
    await tool.execute({
      action: "add",
      symbol: "000858",
      conditionType: "price_below",
      threshold: 100,
    });

    const result = (await tool.execute({ action: "list" })) as Record<string, unknown>;
    expect(result.count).toBe(2);
    expect(result.activeCount).toBe(2);
  });

  it("removes an alert by ID", async () => {
    const { tool } = makeTool();
    const addResult = (await tool.execute({
      action: "add",
      symbol: "600519",
      conditionType: "price_above",
      threshold: 2000,
    })) as Record<string, unknown>;

    const removeResult = (await tool.execute({
      action: "remove",
      alertId: addResult.alertId,
    })) as Record<string, unknown>;

    expect(removeResult.action).toBe("remove");
    expect(removeResult.message).toContain("已删除");

    const listResult = (await tool.execute({ action: "list" })) as Record<string, unknown>;
    expect(listResult.count).toBe(0);
  });

  it("returns error for missing required params", async () => {
    const { tool } = makeTool();

    const r1 = (await tool.execute({ action: "add" })) as Record<string, unknown>;
    expect(r1.error).toContain("symbol");

    const r2 = (await tool.execute({
      action: "add",
      symbol: "600519",
    })) as Record<string, unknown>;
    expect(r2.error).toContain("conditionType");

    const r3 = (await tool.execute({
      action: "add",
      symbol: "600519",
      conditionType: "price_above",
    })) as Record<string, unknown>;
    expect(r3.error).toContain("threshold");
  });

  it("returns error for unknown alert ID", async () => {
    const { tool } = makeTool();
    const result = (await tool.execute({
      action: "remove",
      alertId: "fake-id",
    })) as Record<string, unknown>;
    expect(result.error).toContain("未找到");
  });
});
