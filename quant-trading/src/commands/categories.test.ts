/**
 * Tests for categories.ts — data quality detection and fallback display.
 */
import { describe, expect, it } from "vitest";
import {
  findCategory,
  formatCategoryDetail,
  formatCategoryOverview,
  showCategoryHelp,
  type StockResult,
} from "./categories.js";

/** A stock with full fundamentals */
function fullStock(code: string, overrides: Record<string, unknown> = {}): StockResult {
  return {
    code,
    name: `Stock${code}`,
    score: 70,
    signals: [],
    factors: {
      pe: 15,
      pb: 1.5,
      roe: 18,
      f_score: 7,
      revenue_growth_yoy: 25,
      net_profit_growth_yoy: 20,
      debt_ratio: 40,
      volatility_20d: 25,
      total_market_val_yi: 500,
      inv_details: { valuation: 18, growth: 20, profitability: 22, quality_momentum: 15 },
      ...overrides,
    },
  };
}

/** A stock missing fundamentals — only technicals available (simulates rate-limited data) */
function techOnlyStock(code: string): StockResult {
  return {
    code,
    name: `Tech${code}`,
    score: 50,
    signals: ["放量2.5倍"],
    factors: {
      chg_3m: 12,
      rps_120: 85,
      volatility_20d: 30,
      inv_details: { valuation: 0, growth: 0, profitability: 0, quality_momentum: 20 },
    },
  };
}

describe("categories", () => {
  describe("findCategory", () => {
    it("should find category by Chinese name", () => {
      expect(findCategory("高成长")?.key).toBe("growth");
      expect(findCategory("稳健白马")?.key).toBe("bluechip");
      expect(findCategory("价值洼地")?.key).toBe("value");
      expect(findCategory("强势动能")?.key).toBe("momentum");
    });

    it("should find category by English key", () => {
      expect(findCategory("growth")?.key).toBe("growth");
      expect(findCategory("overall")?.key).toBe("overall");
    });

    it("should return undefined for unknown input", () => {
      expect(findCategory("不存在")).toBeUndefined();
    });
  });

  describe("formatCategoryOverview — full data", () => {
    it("should show all categories with results when data is complete", () => {
      const results = [
        fullStock("600519"),
        fullStock("000858", { revenue_growth_yoy: 30 }),
        fullStock("601318", { pe: 8, pb: 0.9 }),
      ];
      const output = formatCategoryOverview(results, 3);
      expect(output).toContain("选股完成");
      expect(output).not.toContain("基本面数据不完整");
      // Growth category should show results (revG >= 10)
      expect(output).toContain("高成长");
    });
  });

  describe("formatCategoryOverview — degraded data", () => {
    it("should show warning when most stocks lack fundamentals", () => {
      // 1 stock with full data, 5 with technical-only
      const results = [
        fullStock("600519"),
        techOnlyStock("000001"),
        techOnlyStock("000002"),
        techOnlyStock("000003"),
        techOnlyStock("000004"),
        techOnlyStock("000005"),
      ];
      const output = formatCategoryOverview(results, 6);
      expect(output).toContain("基本面数据不完整");
      // Momentum should still have results
      expect(output).toContain("强势动能");
      // The 1 fullStock qualifies for growth (revG=25), bluechip (roe=18, f=7, debt_ratio=40),
      // value (pe=15, pb=1.5, roe=18), and lowrisk (vol=25, debt=40).
      // So strict filters may not be empty. But with degraded flag, fallback banner is shown.
      expect(output).not.toContain("无符合");
    });
  });

  describe("formatCategoryDetail — fallback", () => {
    it("should show fallback results when strict filter fails and data is degraded", () => {
      const results = [techOnlyStock("000001"), techOnlyStock("000002"), techOnlyStock("000003")];
      const cat = findCategory("高成长")!;
      const output = formatCategoryDetail(cat, results, 3);
      // Should show degraded warning, not "无符合条件"
      expect(output).toContain("宽松");
    });

    it("should show normal results when data is complete", () => {
      const results = [
        fullStock("600519", { revenue_growth_yoy: 30 }),
        fullStock("000858", { revenue_growth_yoy: 25 }),
      ];
      const cat = findCategory("高成长")!;
      const output = formatCategoryDetail(cat, results, 2);
      expect(output).toContain("只符合");
      expect(output).not.toContain("宽松");
    });
  });

  describe("showCategoryHelp", () => {
    it("should list available categories", () => {
      const output = showCategoryHelp("xyz");
      expect(output).toContain("未知类别「xyz」");
      expect(output).toContain("高成长");
      expect(output).toContain("综合优选");
    });
  });
});
