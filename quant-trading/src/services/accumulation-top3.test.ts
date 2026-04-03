import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

vi.mock("./tech-moat-analysis.js", () => ({
  loadTechMoatInputsForSymbol: vi.fn(),
  analyzeTechMoat: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { buildAccumulationTop3 } from "./accumulation-top3.js";
import { analyzeTechMoat, loadTechMoatInputsForSymbol } from "./tech-moat-analysis.js";

const mockRunPython = vi.mocked(runPythonScript);
const mockAnalyzeTechMoat = vi.mocked(analyzeTechMoat);
const mockLoadTechMoatInputs = vi.mocked(loadTechMoatInputsForSymbol);

describe("accumulation-top3", () => {
  beforeEach(() => {
    mockRunPython.mockReset();
    mockAnalyzeTechMoat.mockReset();
    mockLoadTechMoatInputs.mockReset();
  });

  it("ranks candidates by condition hits first and total score second", async () => {
    mockRunPython.mockImplementation(async (script, args) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              { symbol: "600001", name: "甲", price: 10, total_score: 82 },
              { symbol: "600002", name: "乙", price: 11, total_score: 76 },
              { symbol: "600003", name: "丙", price: 12, total_score: 88 },
              { symbol: "600004", name: "丁", price: 9, total_score: 70 },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        const symbol = String(args.symbol ?? "");
        const rawEventsBySymbol: Record<string, Array<Record<string, unknown>>> = {
          "600001": [
            { eventType: "pre_accumulation_threshold_hit", detail: "近10天放量2次且MA20上行" },
            { eventType: "institutional_rush_threshold_hit", detail: "今日涨停放量攻击" },
          ],
          "600002": [
            { eventType: "pre_accumulation_threshold_hit", detail: "近10天放量3次且MA20上行" },
          ],
          "600003": [{ eventType: "institutional_rush_threshold_hit", detail: "高换手放量长阳" }],
          "600004": [],
        };
        const summaryBySymbol: Record<string, Record<string, unknown>> = {
          "600001": {
            price: 10,
            buildScore: 35,
            confidence: 72,
            netConviction: 28,
            positiveEvidence: [],
          },
          "600002": {
            price: 11,
            buildScore: 25,
            confidence: 63,
            netConviction: 12,
            positiveEvidence: [],
          },
          "600003": {
            price: 12,
            buildScore: 31,
            confidence: 66,
            netConviction: 18,
            positiveEvidence: [],
          },
          "600004": {
            price: 9,
            buildScore: 10,
            confidence: 35,
            netConviction: 0,
            positiveEvidence: [],
          },
        };
        return {
          ok: true,
          data: {
            summary: summaryBySymbol[symbol],
            rawEvents: rawEventsBySymbol[symbol],
            positiveEvidence: [{ title: "公开信号增强" }],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockImplementation(async (symbol) => {
      if (symbol === "600001") {
        return {
          companyName: "甲",
          newsTitles: ["公司技术突破并通过量产认证"],
          detailPayload: null,
        };
      }
      if (symbol === "600002") {
        return { companyName: "乙", newsTitles: ["公司拟推进资产重组事项"], detailPayload: null };
      }
      if (symbol === "600003") {
        return { companyName: "丙", newsTitles: ["普通经营更新"], detailPayload: null };
      }
      return { companyName: "丁", newsTitles: [], detailPayload: null };
    });

    mockAnalyzeTechMoat.mockImplementation(async (symbol) => {
      if (symbol === "600001") return { moatScore: 7.2, moatGrade: "较强（供应链验证）" };
      if (symbol === "600003") return { moatScore: 3.0, moatGrade: "一般" };
      return null;
    });

    const result = await buildAccumulationTop3({
      config: {} as never,
      lookback: 30,
      min_confidence: 40,
      evaluate_count: 8,
    });

    expect(result.top3).toHaveLength(3);
    expect(result.top3.map((item) => item.symbol)).toEqual(["600001", "600002", "600003"]);
    expect(result.top3[0]?.conditionHitCount).toBe(3);
    expect(result.top3[0]?.selectionReason).toBe("吸筹与抢筹共振");
    expect(result.top3[0]?.reasonTrail).toContain("前期吸筹命中");
    expect(result.top3[1]?.conditionHits.techMoatOrRestructuring).toBe(true);
    expect(result.top3[1]?.selectionReason).toBe("前期吸筹主导");
    expect(result.top3[1]?.restructureKeywords).toContain("重组");
    expect(result.top3[2]?.conditionHitCount).toBe(1);
    expect(result.top3[2]?.selectionReason).toBe("机构抢筹主导");
  });

  it("throws when chip accumulation scan fails", async () => {
    mockRunPython.mockResolvedValue({ ok: false, error: "scan failed" } as never);

    await expect(
      buildAccumulationTop3({
        config: {} as never,
      }),
    ).rejects.toThrow("scan failed");
  });

  it("forwards custom thresholds into institution detail analysis", async () => {
    mockRunPython.mockImplementation(async (script, args) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: { stocks: [{ symbol: "600010", name: "测试股", price: 8, total_score: 61 }] },
        };
      }
      if (script === "scripts/institution_activity.py") {
        return {
          ok: true,
          data: {
            summary: { price: 8, buildScore: 18, confidence: 45, netConviction: 6 },
            rawEvents: [{ eventType: "pre_accumulation_threshold_hit", detail: "命中自定义阈值" }],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });
    mockLoadTechMoatInputs.mockResolvedValue({
      companyName: "测试股",
      newsTitles: [],
      detailPayload: null,
    });
    mockAnalyzeTechMoat.mockResolvedValue(null);

    await buildAccumulationTop3({
      config: {} as never,
      as_of_date: "2026-03-20",
      thresholds: {
        pre_accum_days: 12,
        pre_accum_count_min: 3,
        pre_accum_volume_ratio: 1.8,
        rush_change_pct: 6.5,
        rush_volume_ratio: 2.6,
        rush_turnover_pct: 4.5,
        pre_surge_chip_min: 9,
        pre_surge_volume_price_min: 7,
        pre_surge_turnover_min: 7,
      },
    });

    expect(mockRunPython).toHaveBeenCalledWith(
      "scripts/institution_activity.py",
      expect.objectContaining({
        as_of_date: "2026-03-20",
        pre_accum_days: 12,
        pre_accum_count_min: 3,
        pre_accum_volume_ratio: 1.8,
        rush_change_pct: 6.5,
        rush_volume_ratio: 2.6,
        rush_turnover_pct: 4.5,
        pre_surge_chip_min: 9,
        pre_surge_volume_price_min: 7,
        pre_surge_turnover_min: 7,
      }),
      expect.anything(),
    );
  });

  it("excludes moat-only candidates when the pure accumulation score is too weak", async () => {
    mockRunPython.mockImplementation(async (script, args) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              { symbol: "600101", name: "弱吸筹", price: 10, total_score: 8.9 },
              { symbol: "600102", name: "有效候选", price: 11, total_score: 36 },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        const symbol = String(args.symbol ?? "");
        if (symbol === "600101") {
          return {
            ok: true,
            data: {
              summary: { price: 10, buildScore: 12, confidence: 40, netConviction: 5 },
              rawEvents: [],
              positiveEvidence: [],
            },
          };
        }
        return {
          ok: true,
          data: {
            summary: { price: 11, buildScore: 20, confidence: 52, netConviction: 11 },
            rawEvents: [
              { eventType: "pre_accumulation_threshold_hit", detail: "近10天放量2次且MA20上行" },
            ],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockImplementation(async (symbol) => {
      if (symbol === "600101") {
        return {
          companyName: "弱吸筹",
          newsTitles: ["公司存在技术壁垒验证"],
          detailPayload: null,
        };
      }
      return {
        companyName: "有效候选",
        newsTitles: [],
        detailPayload: null,
      };
    });

    mockAnalyzeTechMoat.mockImplementation(async (symbol) => {
      if (symbol === "600101") return { moatScore: 8, moatGrade: "较强" };
      return null;
    });

    const result = await buildAccumulationTop3({
      config: {} as never,
      lookback: 30,
      min_confidence: 0,
      evaluate_count: 6,
    });

    expect(result.top3.map((item) => item.symbol)).toEqual(["600102"]);
    expect(result.top3[0]?.selectionReason).toBe("前期吸筹主导");
    expect(result.top3[0]?.reasonTrail).not.toContain("壁垒/重组增强");
  });

  it("admits pre-surge anomaly candidates even when pure chip total is below backbone threshold", async () => {
    mockRunPython.mockImplementation(async (script) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              {
                symbol: "300335",
                name: "异动样本",
                price: 6.2,
                total_score: 16.4,
                dimension_scores: {
                  chip: 10.5,
                  volume_price: 8.4,
                  turnover: 12.1,
                },
              },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        return {
          ok: true,
          data: {
            summary: { price: 6.2, buildScore: 18, confidence: 53, netConviction: 9 },
            rawEvents: [],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockResolvedValue({
      companyName: "异动样本",
      newsTitles: [],
      detailPayload: null,
    });
    mockAnalyzeTechMoat.mockResolvedValue(null);

    const result = await buildAccumulationTop3({
      config: {} as never,
      min_confidence: 0,
      evaluate_count: 6,
    });

    expect(result.top3).toHaveLength(1);
    expect(result.top3[0]?.symbol).toBe("300335");
    expect(result.top3[0]?.conditionHits.preSurgeAnomaly).toBe(true);
    expect(result.top3[0]?.selectionReason).toBe("启动前异动主导");
  });

  it("propagates ambush explainability fields from chip scan rows", async () => {
    mockRunPython.mockImplementation(async (script) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              {
                symbol: "600888",
                name: "解释样本",
                price: 9.8,
                total_score: 24.2,
                reasons: ["前期放量命中 2 次", "主力线维持改善"],
                risk_flags: ["主升段确认不足，当前仅观察"],
                score_components: {
                  base: 40,
                  pre_accum_hits: 8,
                  main_force_zljc: 6,
                  main_force_var6: 4,
                  ma_deviation: 5,
                  day_change: 3,
                },
              },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        return {
          ok: true,
          data: {
            summary: { price: 9.8, buildScore: 10, confidence: 35, netConviction: 3 },
            rawEvents: [],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockResolvedValue({
      companyName: "解释样本",
      newsTitles: [],
      detailPayload: null,
    });
    mockAnalyzeTechMoat.mockResolvedValue(null);

    const result = await buildAccumulationTop3({
      config: {} as never,
      min_confidence: 0,
      evaluate_count: 5,
    });

    expect(result.top3).toHaveLength(1);
    expect(result.top3[0]?.symbol).toBe("600888");
    expect(result.top3[0]?.ambushReasons).toEqual(["前期放量命中 2 次", "主力线维持改善"]);
    expect(result.top3[0]?.riskFlags).toEqual(["主升段确认不足，当前仅观察"]);
    expect(result.top3[0]?.scoreComponents).toEqual({
      base: 40,
      pre_accum_hits: 8,
      main_force_zljc: 6,
      main_force_var6: 4,
      ma_deviation: 5,
      day_change: 3,
    });
  });

  it("falls back safely when ambush explainability fields are malformed", async () => {
    mockRunPython.mockImplementation(async (script) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              {
                symbol: "600889",
                name: "异常解释样本",
                price: 8.6,
                total_score: 23.1,
                reasons: "not-array",
                risk_flags: 123,
                score_components: "bad-object",
              },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        return {
          ok: true,
          data: {
            summary: { price: 8.6, buildScore: 10, confidence: 34, netConviction: 2 },
            rawEvents: [],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockResolvedValue({
      companyName: "异常解释样本",
      newsTitles: [],
      detailPayload: null,
    });
    mockAnalyzeTechMoat.mockResolvedValue(null);

    const result = await buildAccumulationTop3({
      config: {} as never,
      min_confidence: 0,
      evaluate_count: 5,
    });

    expect(result.top3).toHaveLength(1);
    expect(result.top3[0]?.symbol).toBe("600889");
    expect(result.top3[0]?.ambushReasons).toBeUndefined();
    expect(result.top3[0]?.riskFlags).toBeUndefined();
    expect(result.top3[0]?.scoreComponents).toBeUndefined();
  });

  it("keeps valid ambush fields while dropping malformed parts", async () => {
    mockRunPython.mockImplementation(async (script) => {
      if (script === "engines/chip_accumulation.py") {
        return {
          ok: true,
          data: {
            stocks: [
              {
                symbol: "600890",
                name: "混合解释样本",
                price: 8.9,
                total_score: 22.4,
                reasons: ["前期放量命中 1 次", "主力线维持改善"],
                risk_flags: "not-array",
                score_components: {
                  base: 40,
                  pre_accum_hits: "x",
                  main_force_zljc: 6,
                  main_force_var6: null,
                  ma_deviation: 4,
                  day_change: 3,
                },
              },
            ],
          },
        };
      }
      if (script === "scripts/institution_activity.py") {
        return {
          ok: true,
          data: {
            summary: { price: 8.9, buildScore: 10, confidence: 33, netConviction: 2 },
            rawEvents: [],
            positiveEvidence: [],
          },
        };
      }
      return { ok: false, error: `unexpected script ${script}` };
    });

    mockLoadTechMoatInputs.mockResolvedValue({
      companyName: "混合解释样本",
      newsTitles: [],
      detailPayload: null,
    });
    mockAnalyzeTechMoat.mockResolvedValue(null);

    const result = await buildAccumulationTop3({
      config: {} as never,
      min_confidence: 0,
      evaluate_count: 5,
    });

    expect(result.top3).toHaveLength(1);
    expect(result.top3[0]?.symbol).toBe("600890");
    expect(result.top3[0]?.ambushReasons).toEqual(["前期放量命中 1 次", "主力线维持改善"]);
    expect(result.top3[0]?.riskFlags).toBeUndefined();
    expect(result.top3[0]?.scoreComponents).toEqual({
      base: 40,
      pre_accum_hits: 0,
      main_force_zljc: 6,
      main_force_var6: 0,
      ma_deviation: 4,
      day_change: 3,
    });
  });
});
