import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { DcaScheduleService } from "./dca-schedule.js";

const mockConfig: QuantTradingConfig = { dataDir: "/tmp/test-dca" };

describe("DcaScheduleService", () => {
  let svc: DcaScheduleService;
  const mockSubmit = vi.fn().mockResolvedValue({ ok: true, order: { orderId: "o1" } });
  const mockQueryAccount = vi
    .fn()
    .mockResolvedValue({ totalAssets: 500_000, availableCash: 100_000 });
  const mockGetPrice = vi.fn().mockResolvedValue(25.0);

  beforeEach(() => {
    vi.useFakeTimers();
    const mockOrderMgr = {
      submit: mockSubmit,
      broker: { queryAccount: mockQueryAccount },
    };
    svc = new DcaScheduleService(mockConfig, () => mockOrderMgr as never, mockGetPrice);
    mockSubmit.mockClear();
    mockGetPrice.mockClear();
  });

  afterEach(() => {
    svc.destroy();
    vi.useRealTimers();
  });

  it("creates a plan with correct defaults", () => {
    const plan = svc.createPlan({ symbol: "600519", amount: 5000, frequency: "weekly" });
    expect(plan.id).toMatch(/^dca-/);
    expect(plan.symbol).toBe("600519");
    expect(plan.amount).toBe(5000);
    expect(plan.frequency).toBe("weekly");
    expect(plan.status).toBe("active");
    expect(plan.executionCount).toBe(0);
    expect(plan.nextExecutionAt).toBeTruthy();
  });

  it("lists plans filtered by status", () => {
    svc.createPlan({ symbol: "600519", amount: 5000, frequency: "weekly" });
    svc.createPlan({ symbol: "000001", amount: 3000, frequency: "daily" });

    expect(svc.listPlans()).toHaveLength(2);
    expect(svc.listPlans("active")).toHaveLength(2);
    expect(svc.listPlans("paused")).toHaveLength(0);
  });

  it("pauses and resumes a plan", () => {
    const plan = svc.createPlan({ symbol: "600519", amount: 5000, frequency: "weekly" });
    expect(svc.pausePlan(plan.id)).toBe(true);
    expect(svc.getPlan(plan.id)?.status).toBe("paused");

    expect(svc.resumePlan(plan.id)).toBe(true);
    expect(svc.getPlan(plan.id)?.status).toBe("active");
  });

  it("cancels a plan", () => {
    const plan = svc.createPlan({ symbol: "600519", amount: 5000, frequency: "weekly" });
    expect(svc.cancelPlan(plan.id)).toBe(true);
    expect(svc.getPlan(plan.id)?.status).toBe("cancelled");
    // Cannot resume a cancelled plan
    expect(svc.resumePlan(plan.id)).toBe(false);
  });

  it("pause returns false for non-existent plan", () => {
    expect(svc.pausePlan("dca-9999")).toBe(false);
  });

  it("creates with max executions", () => {
    const plan = svc.createPlan({
      symbol: "600519",
      amount: 5000,
      frequency: "monthly",
      maxExecutions: 12,
    });
    expect(plan.maxExecutions).toBe(12);
  });

  it("creates with custom description", () => {
    const plan = svc.createPlan({
      symbol: "600519",
      amount: 5000,
      frequency: "weekly",
      description: "茅台每周定投",
    });
    expect(plan.description).toBe("茅台每周定投");
  });
});
