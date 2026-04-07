/**
 * IEquityCurveService — 资金曲线时间序列查询服务接口与实现。
 *
 * 支持：
 *  - 单曲线查询（queryCurve）
 *  - 多账户/多策略曲线叠加查询（queryMultiCurve）
 *  - 多条曲线合并（mergeCurves）
 *  - 日期对齐与缺失值填充策略
 */
import type { EquitySnapshot, EquityHistory } from "./equity-history.js";

// ── 缺失值填充策略 ────────────────────────────────────────────

/**
 * 缺失值填充策略：
 *  - "previous"  — 使用前一个有效快照填充（前值填充）
 *  - "zero"      — 填充为零（适用于 PnL 序列）
 *  - "linear"    — 线性插值（在两个有效点之间做线性内插）
 *  - "none"      — 不填充，保留原始空洞
 */
export type FillStrategy = "previous" | "zero" | "linear" | "none";

// ── 查询参数 ──────────────────────────────────────────────────

/** 单条曲线数据源标识 */
export interface CurveSource {
  /** 数据源标签（如 "paper", "live", 账户ID, 策略ID） */
  sourceId: string;
  /** 数据源类型 */
  sourceType: "paper" | "live" | "account" | "strategy" | "backtest";
  /** 关联的 EquityHistory 实例（用于运行时查询） */
  equityHistory?: EquityHistory;
  /** 或直接提供快照序列（回测、外部数据等） */
  snapshots?: EquitySnapshot[];
}

/** 单曲线查询请求 */
export interface CurveQuery {
  /** 数据源 */
  source: CurveSource;
  /** 起始日期（YYYY-MM-DD，含），不传则取全部 */
  startDate?: string;
  /** 结束日期（YYYY-MM-DD，含），不传则取到最新 */
  endDate?: string;
  /** 缺失值填充策略，默认 "previous" */
  fillStrategy?: FillStrategy;
}

/** 多曲线查询请求 */
export interface MultiCurveQuery {
  /** 多个数据源 */
  sources: CurveSource[];
  /** 起始日期 */
  startDate?: string;
  /** 结束日期 */
  endDate?: string;
  /** 缺失值填充策略，默认 "previous" */
  fillStrategy?: FillStrategy;
}

/** 曲线合并模式 */
export type MergeMode =
  | "sum"       // 权益简单求和（多账户合并总资产）
  | "average"   // 取平均
  | "weighted"; // 按初始资产加权

/** 曲线合并请求 */
export interface MergeCurvesQuery {
  /** 待合并的曲线结果 */
  curves: EquityCurveResult[];
  /** 合并模式，默认 "sum" */
  mode?: MergeMode;
  /** 加权合并时各曲线权重（按顺序，总和不要求为1，会自动归一化） */
  weights?: number[];
  /** 缺失值填充策略，默认 "previous" */
  fillStrategy?: FillStrategy;
}

// ── 查询结果 ──────────────────────────────────────────────────

/** 曲线元数据 */
export interface CurveMetadata {
  /** 数据源标识 */
  sourceId: string;
  /** 数据源类型 */
  sourceType: CurveSource["sourceType"] | "merged";
  /** 起始日期 */
  startDate: string;
  /** 结束日期 */
  endDate: string;
  /** 快照总数 */
  snapshotCount: number;
  /** 使用的填充策略 */
  fillStrategy: FillStrategy;
  /** 填充的缺失天数 */
  filledDays: number;
  /** 合并模式（仅合并结果） */
  mergeMode?: MergeMode;
  /** 合并的数据源列表（仅合并结果） */
  mergedSources?: string[];
}

/** 资金曲线查询结果 */
export interface EquityCurveResult {
  /** 曲线元数据 */
  metadata: CurveMetadata;
  /** 时间序列快照（按日期升序） */
  snapshots: EquitySnapshot[];
}

// ── 服务接口 ──────────────────────────────────────────────────

export interface IEquityCurveService {
  /**
   * 查询单条资金曲线。
   * 从指定数据源提取快照序列，按日期范围过滤并填充缺失值。
   */
  queryCurve(query: CurveQuery): EquityCurveResult;

  /**
   * 查询多条资金曲线（多账户/多策略叠加）。
   * 返回日期对齐后的多条独立曲线，共享同一时间轴。
   */
  queryMultiCurve(query: MultiCurveQuery): EquityCurveResult[];

  /**
   * 合并多条曲线为一条。
   * 支持求和（多账户合并）、平均、加权平均等模式。
   */
  mergeCurves(query: MergeCurvesQuery): EquityCurveResult;
}

// ── 工具函数 ──────────────────────────────────────────────────

/**
 * 生成两个日期之间的所有日期（YYYY-MM-DD 格式，含首尾）。
 */
function generateDateRange(startDate: string, endDate: string): string[] {
  const dates: string[] = [];
  const current = new Date(startDate + "T12:00:00");
  const end = new Date(endDate + "T12:00:00");
  while (current <= end) {
    dates.push(formatLocalDate(current));
    current.setDate(current.getDate() + 1);
  }
  return dates;
}

/** Format a Date as YYYY-MM-DD in local timezone. */
function formatLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * 仅保留工作日（周一到周五）——粗略过滤，不含节假日。
 * 对于交易日序列来说已经足够，节假日缺失会被填充策略处理。
 */
function filterWeekdays(dates: string[]): string[] {
  return dates.filter((d) => {
    const day = new Date(d + "T12:00:00").getDay();
    return day !== 0 && day !== 6;
  });
}

/**
 * 对快照序列按指定日期网格进行对齐和缺失值填充。
 */
function alignAndFill(
  snapshots: EquitySnapshot[],
  dateGrid: string[],
  fillStrategy: FillStrategy,
): { aligned: EquitySnapshot[]; filledDays: number } {
  const byDate = new Map(snapshots.map((s) => [s.date, s]));
  const aligned: EquitySnapshot[] = [];
  let filledDays = 0;

  if (fillStrategy === "none") {
    // 只返回日期网格内存在数据的快照
    for (const date of dateGrid) {
      const snap = byDate.get(date);
      if (snap) aligned.push(snap);
    }
    return { aligned, filledDays: 0 };
  }

  // 将快照按日期排序，用于线性插值时快速查找前后值
  const sortedSnaps = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));

  let lastKnown: EquitySnapshot | null = null;

  for (const date of dateGrid) {
    const snap = byDate.get(date);
    if (snap) {
      aligned.push(snap);
      lastKnown = snap;
      continue;
    }

    // 缺失日期
    filledDays++;

    switch (fillStrategy) {
      case "previous": {
        if (lastKnown) {
          aligned.push({ ...lastKnown, date, dailyPnl: 0 });
        }
        // 如果没有前值，跳过该日期（序列开头的缺失）
        break;
      }

      case "zero": {
        aligned.push({
          date,
          equity: 0,
          cash: 0,
          marketValue: 0,
          dailyPnl: 0,
          positionCount: 0,
        });
        break;
      }

      case "linear": {
        // 找到前后最近的有效快照做线性插值
        const prev = findPrevSnapshot(sortedSnaps, date);
        const next = findNextSnapshot(sortedSnaps, date);
        if (prev && next) {
          const totalDays = daysBetween(prev.date, next.date);
          const elapsedDays = daysBetween(prev.date, date);
          const ratio = totalDays > 0 ? elapsedDays / totalDays : 0;
          aligned.push({
            date,
            equity: lerp(prev.equity, next.equity, ratio),
            cash: lerp(prev.cash, next.cash, ratio),
            marketValue: lerp(prev.marketValue, next.marketValue, ratio),
            dailyPnl: 0, // 插值日无实际盈亏
            positionCount: prev.positionCount, // 保持前值
            benchmark:
              prev.benchmark != null && next.benchmark != null
                ? lerp(prev.benchmark, next.benchmark, ratio)
                : prev.benchmark,
          });
        } else if (prev) {
          // 没有后续数据，退回前值填充
          aligned.push({ ...prev, date, dailyPnl: 0 });
        }
        break;
      }
    }
  }

  return { aligned, filledDays };
}

function findPrevSnapshot(sorted: EquitySnapshot[], date: string): EquitySnapshot | null {
  for (let i = sorted.length - 1; i >= 0; i--) {
    if (sorted[i]!.date < date) return sorted[i]!;
  }
  return null;
}

function findNextSnapshot(sorted: EquitySnapshot[], date: string): EquitySnapshot | null {
  for (const snap of sorted) {
    if (snap.date > date) return snap;
  }
  return null;
}

function daysBetween(a: string, b: string): number {
  const msPerDay = 86_400_000;
  return Math.round(
    (new Date(b + "T12:00:00").getTime() - new Date(a + "T12:00:00").getTime()) / msPerDay,
  );
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * 从数据源提取原始快照序列。
 */
function resolveSnapshots(source: CurveSource): EquitySnapshot[] {
  if (source.snapshots) {
    return [...source.snapshots];
  }
  if (source.equityHistory) {
    return source.equityHistory.getAll();
  }
  return [];
}

/**
 * 计算多条曲线的公共日期网格（所有曲线中出现过的日期的并集）。
 */
function buildUnionDateGrid(multiSnapshots: EquitySnapshot[][]): string[] {
  const dateSet = new Set<string>();
  for (const snaps of multiSnapshots) {
    for (const s of snaps) {
      dateSet.add(s.date);
    }
  }
  return Array.from(dateSet).sort();
}

// ── 服务实现 ──────────────────────────────────────────────────

export class EquityCurveService implements IEquityCurveService {
  queryCurve(query: CurveQuery): EquityCurveResult {
    const fillStrategy = query.fillStrategy ?? "previous";
    let rawSnapshots = resolveSnapshots(query.source);

    // 排序
    rawSnapshots.sort((a, b) => a.date.localeCompare(b.date));

    // 日期范围过滤
    if (query.startDate) {
      rawSnapshots = rawSnapshots.filter((s) => s.date >= query.startDate!);
    }
    if (query.endDate) {
      rawSnapshots = rawSnapshots.filter((s) => s.date <= query.endDate!);
    }

    if (rawSnapshots.length === 0) {
      return {
        metadata: {
          sourceId: query.source.sourceId,
          sourceType: query.source.sourceType,
          startDate: query.startDate ?? "",
          endDate: query.endDate ?? "",
          snapshotCount: 0,
          fillStrategy,
          filledDays: 0,
        },
        snapshots: [],
      };
    }

    // 构建日期网格（数据范围内的工作日）
    const gridStart = query.startDate ?? rawSnapshots[0]!.date;
    const gridEnd = query.endDate ?? rawSnapshots[rawSnapshots.length - 1]!.date;
    const dateGrid = filterWeekdays(generateDateRange(gridStart, gridEnd));

    const { aligned, filledDays } = alignAndFill(rawSnapshots, dateGrid, fillStrategy);

    return {
      metadata: {
        sourceId: query.source.sourceId,
        sourceType: query.source.sourceType,
        startDate: aligned.length > 0 ? aligned[0]!.date : gridStart,
        endDate: aligned.length > 0 ? aligned[aligned.length - 1]!.date : gridEnd,
        snapshotCount: aligned.length,
        fillStrategy,
        filledDays,
      },
      snapshots: aligned,
    };
  }

  queryMultiCurve(query: MultiCurveQuery): EquityCurveResult[] {
    const fillStrategy = query.fillStrategy ?? "previous";

    // 先提取所有数据源的原始快照
    const allRawSnapshots = query.sources.map((source) => {
      let snaps = resolveSnapshots(source);
      snaps.sort((a, b) => a.date.localeCompare(b.date));
      if (query.startDate) snaps = snaps.filter((s) => s.date >= query.startDate!);
      if (query.endDate) snaps = snaps.filter((s) => s.date <= query.endDate!);
      return snaps;
    });

    // 构建统一日期网格（所有曲线日期的并集，过滤为工作日）
    const unionDates = buildUnionDateGrid(allRawSnapshots);
    const filteredUnion =
      unionDates.length > 0
        ? filterWeekdays(
            generateDateRange(
              unionDates[0]!,
              unionDates[unionDates.length - 1]!,
            ),
          )
        : [];

    // 用统一日期网格对齐每条曲线
    return query.sources.map((source, idx) => {
      const rawSnaps = allRawSnapshots[idx]!;

      if (rawSnaps.length === 0) {
        return {
          metadata: {
            sourceId: source.sourceId,
            sourceType: source.sourceType,
            startDate: query.startDate ?? "",
            endDate: query.endDate ?? "",
            snapshotCount: 0,
            fillStrategy,
            filledDays: 0,
          },
          snapshots: [],
        };
      }

      const { aligned, filledDays } = alignAndFill(rawSnaps, filteredUnion, fillStrategy);

      return {
        metadata: {
          sourceId: source.sourceId,
          sourceType: source.sourceType,
          startDate: aligned.length > 0 ? aligned[0]!.date : "",
          endDate: aligned.length > 0 ? aligned[aligned.length - 1]!.date : "",
          snapshotCount: aligned.length,
          fillStrategy,
          filledDays,
        },
        snapshots: aligned,
      };
    });
  }

  mergeCurves(query: MergeCurvesQuery): EquityCurveResult {
    const mode = query.mode ?? "sum";
    const fillStrategy = query.fillStrategy ?? "previous";
    const curves = query.curves;

    if (curves.length === 0) {
      return {
        metadata: {
          sourceId: "merged",
          sourceType: "merged",
          startDate: "",
          endDate: "",
          snapshotCount: 0,
          fillStrategy,
          filledDays: 0,
          mergeMode: mode,
          mergedSources: [],
        },
        snapshots: [],
      };
    }

    if (curves.length === 1) {
      return {
        metadata: {
          ...curves[0]!.metadata,
          sourceId: "merged",
          sourceType: "merged",
          mergeMode: mode,
          mergedSources: [curves[0]!.metadata.sourceId],
        },
        snapshots: [...curves[0]!.snapshots],
      };
    }

    // 构建统一日期网格
    const unionDateGrid = buildUnionDateGrid(curves.map((c) => c.snapshots));

    // 对齐所有曲线到统一日期网格
    const alignedCurves = curves.map((curve) =>
      alignAndFill(curve.snapshots, unionDateGrid, fillStrategy),
    );

    // 归一化权重
    let weights: number[];
    if (mode === "weighted" && query.weights && query.weights.length === curves.length) {
      const totalWeight = query.weights.reduce((a, b) => a + b, 0);
      weights = totalWeight > 0 ? query.weights.map((w) => w / totalWeight) : curves.map(() => 1 / curves.length);
    } else if (mode === "average") {
      weights = curves.map(() => 1 / curves.length);
    } else {
      // sum 模式：权重全为 1
      weights = curves.map(() => 1);
    }

    // 按日期合并
    const mergedSnapshots: EquitySnapshot[] = [];
    let totalFilledDays = 0;

    for (const date of unionDateGrid) {
      let equity = 0;
      let cash = 0;
      let marketValue = 0;
      let dailyPnl = 0;
      let positionCount = 0;
      let benchmarkSum = 0;
      let benchmarkCount = 0;
      let contributorCount = 0;

      for (let i = 0; i < alignedCurves.length; i++) {
        const snap = alignedCurves[i]!.aligned.find((s) => s.date === date);
        if (!snap) continue;

        const w = weights[i]!;
        equity += snap.equity * w;
        cash += snap.cash * w;
        marketValue += snap.marketValue * w;
        dailyPnl += snap.dailyPnl * w;
        positionCount += snap.positionCount;
        if (snap.benchmark != null) {
          benchmarkSum += snap.benchmark * w;
          benchmarkCount++;
        }
        contributorCount++;
      }

      if (contributorCount > 0) {
        mergedSnapshots.push({
          date,
          equity,
          cash,
          marketValue,
          dailyPnl,
          positionCount,
          benchmark: benchmarkCount > 0 ? benchmarkSum : undefined,
        });
      }
    }

    for (const ac of alignedCurves) {
      totalFilledDays += ac.filledDays;
    }

    return {
      metadata: {
        sourceId: "merged",
        sourceType: "merged",
        startDate: mergedSnapshots.length > 0 ? mergedSnapshots[0]!.date : "",
        endDate:
          mergedSnapshots.length > 0 ? mergedSnapshots[mergedSnapshots.length - 1]!.date : "",
        snapshotCount: mergedSnapshots.length,
        fillStrategy,
        filledDays: totalFilledDays,
        mergeMode: mode,
        mergedSources: curves.map((c) => c.metadata.sourceId),
      },
      snapshots: mergedSnapshots,
    };
  }
}
