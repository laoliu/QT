import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import {
  appendInstitutionActivityHistory,
  handleDeleteInstitutionActivityHistoryApi,
  handleGetInstitutionActivityHistoryApi,
  handleGetInstitutionActivityHistoryByIdApi,
  handlePatchInstitutionActivityHistoryApi,
} from "./institution-history.js";

let tmpDir: string;
let config: QuantTradingConfig;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "oc-inst-history-test-"));
  config = { dataDir: tmpDir, pythonPath: "python3" } as QuantTradingConfig;
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

function makeScan(asOfDate: string, leaders: string[], candidates?: string[]) {
  const leaderRows = leaders.map((symbol, index) => ({
    symbol,
    name: `股票${symbol}`,
    actionTag: index === 0 ? "可观察" : "中性观察",
    confidence: 55 - index,
    netConviction: 8 - index,
  }));
  const candidateRows = (candidates ?? leaders).map((symbol, index) => ({
    symbol,
    name: `股票${symbol}`,
    actionTag: index === 0 ? "可观察" : "中性观察",
    confidence: 55 - index,
    netConviction: 8 - index,
  }));
  return {
    asOfDate,
    generatedAt: `${asOfDate}T15:00:00`,
    marketSummary: { marketTone: "中性", northboundNetIn: 120000000 },
    candidates: candidateRows,
    leaders: leaderRows,
    highRiskBaits: [{ symbol: "300750", name: "宁德时代" }],
  };
}

describe("Institution activity history", () => {
  it("saves scan snapshots and strips fullScan from list payload", async () => {
    await appendInstitutionActivityHistory(config, makeScan("2026-03-12", ["600519", "000858"]), {
      as_of_date: "2026-03-12",
      lookback_days: 10,
      min_confidence: 35,
      symbols: ["600519", "000858"],
    });

    const result = await handleGetInstitutionActivityHistoryApi(config);
    expect(result.status).toBe("ok");
    expect(result.count).toBe(1);
    const entries = result.entries as Array<Record<string, unknown>>;
    expect(entries[0].version).toMatch(/^\d{4}-\d{2}-\d{2}-v1$/);
    expect(entries[0].fullScan).toBeUndefined();
  });

  it("returns full scan data by id", async () => {
    await appendInstitutionActivityHistory(config, makeScan("2026-03-12", ["600519"]), {
      as_of_date: "2026-03-12",
    });
    const list = await handleGetInstitutionActivityHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    const detail = await handleGetInstitutionActivityHistoryByIdApi(
      config,
      entries[0].id as string,
    );
    expect(detail.status).toBe("ok");
    const entry = detail.entry as Record<string, unknown>;
    expect(entry.fullScan).toBeTruthy();
  });

  it("computes top leaders and latest-vs-previous comparison", async () => {
    await appendInstitutionActivityHistory(
      config,
      {
        ...makeScan("2026-03-11", ["600519", "000001"], ["600519", "000001", "300750"]),
        topLeaders: [
          {
            symbol: "600519",
            name: "股票600519",
            actionTag: "可观察",
            confidence: 55,
            netConviction: 8,
            buildScore: 8,
            techMoatScore: 7.2,
            techMoatGrade: "强",
            decisionScore: 28.6,
          },
          {
            symbol: "000001",
            name: "股票000001",
            actionTag: "中性观察",
            confidence: 54,
            netConviction: 7,
            buildScore: 7,
            techMoatScore: 5.1,
            techMoatGrade: "中",
            decisionScore: 24.3,
          },
        ],
      },
      {
        as_of_date: "2026-03-11",
      },
    );
    await appendInstitutionActivityHistory(
      config,
      {
        ...makeScan("2026-03-12", ["600519", "000858"], ["600519", "000858", "300750"]),
        topLeaders: [
          {
            symbol: "600519",
            name: "股票600519",
            actionTag: "可观察",
            confidence: 56,
            netConviction: 9,
            buildScore: 8.5,
            techMoatScore: 8.4,
            techMoatGrade: "很强",
            manualConfirmation: {
              moatConfirmed: true,
              restructureConfirmed: true,
              restructureLevel: "announcement",
              supplyChainVerified: true,
              orderVerified: true,
              customerVerified: true,
              patentVerified: false,
              notes: "龙头公告确认",
              score: 7,
              updatedAt: "2026-03-12T10:30:00",
            },
            manualScore: 7,
            decisionScore: 31.4,
          },
          {
            symbol: "000858",
            name: "股票000858",
            actionTag: "中性观察",
            confidence: 53,
            netConviction: 7.5,
            buildScore: 7.2,
            techMoatScore: 6.6,
            techMoatGrade: "较强",
            manualConfirmation: {
              moatConfirmed: false,
              restructureConfirmed: false,
              restructureLevel: "none",
              supplyChainVerified: false,
              orderVerified: true,
              customerVerified: false,
              patentVerified: true,
              notes: "订单侧验证",
              score: 3,
              updatedAt: "2026-03-12T10:45:00",
            },
            manualScore: 3,
            decisionScore: 26.8,
          },
        ],
      },
      {
        as_of_date: "2026-03-12",
      },
    );

    const result = await handleGetInstitutionActivityHistoryApi(config);
    const comparison = result.comparison as Record<string, unknown>;
    const topLeaderStocks = result.topLeaderStocks as Array<Record<string, unknown>>;
    const metrics = result.metrics as Record<string, unknown>;

    expect(topLeaderStocks[0].code).toBe("600519");
    expect(topLeaderStocks[0].count).toBe(2);
    expect(comparison.keptLeaders).toContain("600519");
    expect(comparison.newLeaders).toContain("000858");
    expect(comparison.droppedLeaders).toContain("000001");
    expect((comparison.leaderStreaks as Record<string, number>)["600519"]).toBe(2);
    expect(metrics.latestAvgMoatScore).toBeCloseTo(7.5, 5);
    expect(metrics.latestAvgManualScore).toBeCloseTo(5, 5);
    expect(metrics.latestAvgDecisionScore).toBeCloseTo(29.1, 5);
    expect(metrics.latestOrderVerifiedCount).toBe(2);
    expect(metrics.latestCustomerVerifiedCount).toBe(1);
    expect(metrics.latestPatentVerifiedCount).toBe(1);
    expect(metrics.latestRestructureCount).toBe(1);
    expect((metrics.bestMoatLeader as Record<string, unknown>).symbol).toBe("600519");
    expect((metrics.bestDecisionLeader as Record<string, unknown>).score).toBe(31.4);
    expect((metrics.topMoatLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("600519");
    expect((metrics.topMoatLeaders as Array<Record<string, unknown>>)[0].grade).toBe("很强");
    expect((metrics.topManualLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("600519");
    expect((metrics.topManualLeaders as Array<Record<string, unknown>>)[0].score).toBe(7);
    expect((metrics.topDecisionLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("600519");
    expect((metrics.topOrderLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("600519");
    expect((metrics.topOrderLeaders as Array<Record<string, unknown>>)[0].count).toBe(1);
    expect((metrics.topCustomerLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("600519");
    expect((metrics.topPatentLeaders as Array<Record<string, unknown>>)[0].symbol).toBe("000858");
    expect((metrics.topRestructureLeaders as Array<Record<string, unknown>>)[0].symbol).toBe(
      "600519",
    );
    expect((metrics.topRestructureLeaders as Array<Record<string, unknown>>)[0].label).toBe(
      "重组公告",
    );

    // Verify trendData is present and contains recent snapshot data
    const trendData = metrics.trendData as Array<Record<string, unknown>>;
    expect(trendData).toBeDefined();
    expect(Array.isArray(trendData)).toBe(true);
    expect(trendData.length).toBeLessThanOrEqual(12);
    // Latest snapshot should have 2 order-verified, 1 customer-verified, 1 patent-verified, 1 restructure
    // trendData is reversed from history, so last element is the latest
    const latestTrendEntry = trendData[trendData.length - 1] as Record<string, unknown>;
    expect(latestTrendEntry.orderVerified).toBe(2);
    expect(latestTrendEntry.customerVerified).toBe(1);
    expect(latestTrendEntry.patentVerified).toBe(1);
    expect(latestTrendEntry.restructure).toBe(1);
  });

  it("deletes saved snapshots", async () => {
    await appendInstitutionActivityHistory(config, makeScan("2026-03-12", ["600519"]), {
      as_of_date: "2026-03-12",
    });
    const list = await handleGetInstitutionActivityHistoryApi(config);
    const entries = list.entries as Array<Record<string, unknown>>;
    const result = await handleDeleteInstitutionActivityHistoryApi(config, entries[0].id as string);
    expect(result.status).toBe("ok");
    const afterDelete = await handleGetInstitutionActivityHistoryApi(config);
    expect(afterDelete.count).toBe(0);
  });

  it("updates manual confirmations for a saved top leader", async () => {
    await appendInstitutionActivityHistory(
      config,
      {
        ...makeScan("2026-03-12", ["600519"]),
        topLeaders: [
          {
            symbol: "600519",
            name: "股票600519",
            actionTag: "可观察",
            confidence: 56,
            netConviction: 9,
            buildScore: 8.5,
            techMoatScore: 8.4,
            techMoatGrade: "很强",
            decisionScore: 31.4,
          },
        ],
      },
      {
        as_of_date: "2026-03-12",
      },
    );

    const list = await handleGetInstitutionActivityHistoryApi(config);
    const historyId = (list.entries as Array<Record<string, unknown>>)[0].id as string;
    const result = await handlePatchInstitutionActivityHistoryApi(config, historyId, {
      symbol: "600519",
      manualConfirmation: {
        moatConfirmed: true,
        restructureConfirmed: true,
        restructureLevel: "announcement",
        supplyChainVerified: false,
        orderVerified: true,
        customerVerified: true,
        patentVerified: false,
        notes: "已核对产业链和公告",
        score: 7,
        updatedAt: "2026-03-12T16:00:00",
      },
      manualScore: 7,
      decisionScore: 38.4,
    });

    expect(result.status).toBe("ok");

    const detail = await handleGetInstitutionActivityHistoryByIdApi(config, historyId);
    const entry = detail.entry as Record<string, unknown>;
    const leader = ((entry.topLeaders as Array<Record<string, unknown>>) ?? [])[0] ?? {};
    expect(leader.manualScore).toBe(7);
    expect((leader.manualConfirmation as Record<string, unknown>).moatConfirmed).toBe(true);
    expect((leader.manualConfirmation as Record<string, unknown>).restructureLevel).toBe(
      "announcement",
    );
    expect((leader.manualConfirmation as Record<string, unknown>).orderVerified).toBe(true);
    expect((leader.manualConfirmation as Record<string, unknown>).notes).toBe("已核对产业链和公告");
    expect(leader.decisionScore).toBe(38.4);
  });
});
