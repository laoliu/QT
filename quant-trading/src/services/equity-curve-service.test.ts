import { describe, expect, it } from "vitest";
import type { EquitySnapshot } from "./equity-history.js";
import { EquityHistory } from "./equity-history.js";
import {
  EquityCurveService,
  type CurveSource,
  type EquityCurveResult,
} from "./equity-curve-service.js";

// ── Helpers ─────────────────────────────────────────────────────

function snap(date: string, equity: number, dailyPnl = 0): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.6,
    marketValue: equity * 0.4,
    dailyPnl,
    positionCount: 2,
  };
}

function snapsWithBenchmark(
  date: string,
  equity: number,
  benchmark: number,
): EquitySnapshot {
  return { ...snap(date, equity), benchmark };
}

/**
 * 生成连续工作日快照序列。
 * startDate 应为周一（YYYY-MM-DD），生成 count 个工作日。
 */
function generateWeekdaySnaps(
  startDate: string,
  count: number,
  baseEquity = 100000,
): EquitySnapshot[] {
  const snaps: EquitySnapshot[] = [];
  const current = new Date(startDate + "T00:00:00");
  let i = 0;
  while (snaps.length < count) {
    const day = current.getDay();
    if (day !== 0 && day !== 6) {
      const eq = baseEquity + i * 100;
      snaps.push(snap(current.toISOString().slice(0, 10), eq, i > 0 ? 100 : 0));
      i++;
    }
    current.setDate(current.getDate() + 1);
  }
  return snaps;
}

// ── Tests ───────────────────────────────────────────────────────

describe("EquityCurveService", () => {
  const service = new EquityCurveService();

  // ── queryCurve ─────────────────────────────────────────────

  describe("queryCurve", () => {
    it("returns snapshots from direct snapshot source", () => {
      const snaps = [
        snap("2024-03-11", 100000),
        snap("2024-03-12", 100500, 500),
        snap("2024-03-13", 101000, 500),
      ];
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "paper", snapshots: snaps },
      });

      expect(result.snapshots).toHaveLength(3);
      expect(result.metadata.sourceId).toBe("test");
      expect(result.metadata.sourceType).toBe("paper");
      expect(result.metadata.snapshotCount).toBe(3);
      expect(result.metadata.startDate).toBe("2024-03-11");
      expect(result.metadata.endDate).toBe("2024-03-13");
    });

    it("filters by date range", () => {
      const snaps = generateWeekdaySnaps("2024-03-11", 10);
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "live", snapshots: snaps },
        startDate: "2024-03-13",
        endDate: "2024-03-15",
      });

      expect(result.snapshots.length).toBe(3);
      expect(result.snapshots[0]!.date).toBe("2024-03-13");
      expect(result.snapshots[result.snapshots.length - 1]!.date).toBe("2024-03-15");
    });

    it("returns empty result for no data", () => {
      const result = service.queryCurve({
        source: { sourceId: "empty", sourceType: "paper", snapshots: [] },
      });
      expect(result.snapshots).toHaveLength(0);
      expect(result.metadata.snapshotCount).toBe(0);
    });

    it("fills missing days with previous value", () => {
      // 周一和周三有数据，周二缺失
      const snaps = [
        snap("2024-03-11", 100000), // Monday
        snap("2024-03-13", 101000, 1000), // Wednesday
      ];
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "paper", snapshots: snaps },
        fillStrategy: "previous",
      });

      // 周二应该被填充
      expect(result.snapshots.length).toBe(3);
      expect(result.snapshots[1]!.date).toBe("2024-03-12");
      expect(result.snapshots[1]!.equity).toBe(100000); // 前值填充
      expect(result.snapshots[1]!.dailyPnl).toBe(0);
      expect(result.metadata.filledDays).toBe(1);
    });

    it("fills missing days with zero", () => {
      const snaps = [
        snap("2024-03-11", 100000),
        snap("2024-03-13", 101000, 1000),
      ];
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "paper", snapshots: snaps },
        fillStrategy: "zero",
      });

      expect(result.snapshots[1]!.date).toBe("2024-03-12");
      expect(result.snapshots[1]!.equity).toBe(0);
    });

    it("fills missing days with linear interpolation", () => {
      const snaps = [
        snap("2024-03-11", 100000),
        snap("2024-03-13", 102000, 1000),
      ];
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "paper", snapshots: snaps },
        fillStrategy: "linear",
      });

      const tuesday = result.snapshots.find((s) => s.date === "2024-03-12");
      expect(tuesday).toBeDefined();
      expect(tuesday!.equity).toBeCloseTo(101000, 0); // 线性插值
    });

    it("fills with none strategy skips missing days", () => {
      const snaps = [
        snap("2024-03-11", 100000),
        snap("2024-03-13", 101000),
      ];
      const result = service.queryCurve({
        source: { sourceId: "test", sourceType: "paper", snapshots: snaps },
        fillStrategy: "none",
      });

      expect(result.snapshots).toHaveLength(2);
      expect(result.metadata.filledDays).toBe(0);
    });

    it("reads from EquityHistory instance", () => {
      const hist = new EquityHistory({});
      hist.addSnapshot(snap("2024-03-11", 100000));
      hist.addSnapshot(snap("2024-03-12", 100500, 500));

      const result = service.queryCurve({
        source: { sourceId: "paper", sourceType: "paper", equityHistory: hist },
      });

      expect(result.snapshots).toHaveLength(2);
      expect(result.snapshots[0]!.equity).toBe(100000);
    });
  });

  // ── queryMultiCurve ────────────────────────────────────────

  describe("queryMultiCurve", () => {
    it("returns aligned curves for multiple sources", () => {
      const paperSnaps = [
        snap("2024-03-11", 100000),
        snap("2024-03-12", 100500),
        snap("2024-03-13", 101000),
      ];
      const liveSnaps = [
        snap("2024-03-11", 200000),
        snap("2024-03-13", 202000), // 周二缺失
      ];

      const results = service.queryMultiCurve({
        sources: [
          { sourceId: "paper", sourceType: "paper", snapshots: paperSnaps },
          { sourceId: "live", sourceType: "live", snapshots: liveSnaps },
        ],
        fillStrategy: "previous",
      });

      expect(results).toHaveLength(2);
      // 两条曲线都应该有3天
      expect(results[0]!.snapshots).toHaveLength(3);
      expect(results[1]!.snapshots).toHaveLength(3);
      // live 曲线周二被前值填充
      expect(results[1]!.snapshots[1]!.date).toBe("2024-03-12");
      expect(results[1]!.snapshots[1]!.equity).toBe(200000);
    });

    it("handles empty source gracefully", () => {
      const results = service.queryMultiCurve({
        sources: [
          { sourceId: "a", sourceType: "paper", snapshots: [snap("2024-03-11", 100000)] },
          { sourceId: "b", sourceType: "live", snapshots: [] },
        ],
      });

      expect(results).toHaveLength(2);
      expect(results[0]!.snapshots).toHaveLength(1);
      expect(results[1]!.snapshots).toHaveLength(0);
    });

    it("applies date range filter across all sources", () => {
      const snapsA = generateWeekdaySnaps("2024-03-11", 5);
      const snapsB = generateWeekdaySnaps("2024-03-11", 5, 200000);

      const results = service.queryMultiCurve({
        sources: [
          { sourceId: "a", sourceType: "paper", snapshots: snapsA },
          { sourceId: "b", sourceType: "live", snapshots: snapsB },
        ],
        startDate: "2024-03-12",
        endDate: "2024-03-14",
      });

      for (const r of results) {
        expect(r.snapshots.length).toBe(3);
        expect(r.snapshots[0]!.date).toBe("2024-03-12");
      }
    });
  });

  // ── mergeCurves ────────────────────────────────────────────

  describe("mergeCurves", () => {
    function makeCurveResult(
      sourceId: string,
      snaps: EquitySnapshot[],
    ): EquityCurveResult {
      return {
        metadata: {
          sourceId,
          sourceType: "paper",
          startDate: snaps[0]?.date ?? "",
          endDate: snaps[snaps.length - 1]?.date ?? "",
          snapshotCount: snaps.length,
          fillStrategy: "previous",
          filledDays: 0,
        },
        snapshots: snaps,
      };
    }

    it("sums equity across curves (default mode)", () => {
      const curveA = makeCurveResult("account-A", [
        snap("2024-03-11", 100000),
        snap("2024-03-12", 101000, 1000),
      ]);
      const curveB = makeCurveResult("account-B", [
        snap("2024-03-11", 200000),
        snap("2024-03-12", 199000, -1000),
      ]);

      const merged = service.mergeCurves({ curves: [curveA, curveB] });

      expect(merged.metadata.sourceType).toBe("merged");
      expect(merged.metadata.mergeMode).toBe("sum");
      expect(merged.metadata.mergedSources).toEqual(["account-A", "account-B"]);
      expect(merged.snapshots).toHaveLength(2);
      expect(merged.snapshots[0]!.equity).toBe(300000);
      expect(merged.snapshots[1]!.equity).toBe(300000);
      expect(merged.snapshots[1]!.dailyPnl).toBe(0); // 1000 + (-1000)
    });

    it("averages equity in average mode", () => {
      const curveA = makeCurveResult("a", [snap("2024-03-11", 100000)]);
      const curveB = makeCurveResult("b", [snap("2024-03-11", 200000)]);

      const merged = service.mergeCurves({
        curves: [curveA, curveB],
        mode: "average",
      });

      expect(merged.snapshots[0]!.equity).toBe(150000);
    });

    it("applies weighted merge", () => {
      const curveA = makeCurveResult("a", [snap("2024-03-11", 100000)]);
      const curveB = makeCurveResult("b", [snap("2024-03-11", 200000)]);

      const merged = service.mergeCurves({
        curves: [curveA, curveB],
        mode: "weighted",
        weights: [3, 1], // A gets 75%, B gets 25%
      });

      // 100000 * 0.75 + 200000 * 0.25 = 75000 + 50000 = 125000
      expect(merged.snapshots[0]!.equity).toBeCloseTo(125000);
    });

    it("handles empty curves array", () => {
      const merged = service.mergeCurves({ curves: [] });
      expect(merged.snapshots).toHaveLength(0);
      expect(merged.metadata.mergedSources).toEqual([]);
    });

    it("handles single curve", () => {
      const curve = makeCurveResult("only", [snap("2024-03-11", 100000)]);
      const merged = service.mergeCurves({ curves: [curve] });
      expect(merged.snapshots).toHaveLength(1);
      expect(merged.snapshots[0]!.equity).toBe(100000);
      expect(merged.metadata.mergedSources).toEqual(["only"]);
    });

    it("aligns curves with different date ranges during merge", () => {
      const curveA = makeCurveResult("a", [
        snap("2024-03-11", 100000),
        snap("2024-03-12", 101000),
        snap("2024-03-13", 102000),
      ]);
      const curveB = makeCurveResult("b", [
        snap("2024-03-12", 200000), // starts one day later
        snap("2024-03-13", 201000),
      ]);

      const merged = service.mergeCurves({
        curves: [curveA, curveB],
        fillStrategy: "previous",
      });

      // 3月11日只有 curveA 贡献
      expect(merged.snapshots).toHaveLength(3);
      expect(merged.snapshots[0]!.date).toBe("2024-03-11");
      expect(merged.snapshots[0]!.equity).toBe(100000); // only A
      // 3月12日两条曲线都有
      expect(merged.snapshots[1]!.equity).toBe(301000); // 101000 + 200000
    });

    it("preserves benchmark in merged results", () => {
      const curveA = makeCurveResult("a", [
        snapsWithBenchmark("2024-03-11", 100000, 3500),
      ]);
      const curveB = makeCurveResult("b", [
        snapsWithBenchmark("2024-03-11", 200000, 3500),
      ]);

      const merged = service.mergeCurves({ curves: [curveA, curveB] });
      expect(merged.snapshots[0]!.benchmark).toBe(7000); // sum mode
    });
  });

  // ── 端到端：多账户合并场景 ─────────────────────────────────

  describe("end-to-end: multi-account merge", () => {
    it("merges paper and live accounts into combined equity curve", () => {
      const paperHist = new EquityHistory({});
      paperHist.addSnapshot(snap("2024-03-11", 100000));
      paperHist.addSnapshot(snap("2024-03-12", 101000, 1000));
      paperHist.addSnapshot(snap("2024-03-13", 102000, 1000));

      const liveHist = new EquityHistory({});
      liveHist.addSnapshot(snap("2024-03-11", 500000));
      liveHist.addSnapshot(snap("2024-03-12", 498000, -2000));
      liveHist.addSnapshot(snap("2024-03-13", 505000, 7000));

      // Step 1: query multi-curve
      const curves = service.queryMultiCurve({
        sources: [
          { sourceId: "paper", sourceType: "paper", equityHistory: paperHist },
          { sourceId: "live", sourceType: "live", equityHistory: liveHist },
        ],
      });

      expect(curves).toHaveLength(2);

      // Step 2: merge
      const merged = service.mergeCurves({ curves });

      expect(merged.snapshots).toHaveLength(3);
      expect(merged.snapshots[0]!.equity).toBe(600000);
      expect(merged.snapshots[1]!.equity).toBe(599000);
      expect(merged.snapshots[2]!.equity).toBe(607000);
      expect(merged.metadata.mergedSources).toEqual(["paper", "live"]);
    });
  });
});
