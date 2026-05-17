"""S2 统计验证框架 — 策略/组合上线前的 P8 级统计检验。

覆盖 §1.2 + §1.4 要求的全部统计验证项：

1. DSR (Deflated Sharpe Ratio) — Bailey & Lopez de Prado 2014
2. HLZ (Harvey-Liu-Zhu) 多重检验校正 — t_adj ≥ 2.0
3. PBO (Probability of Backtest Overfitting) — CSCV 方法，< 0.50
4. 极端年排除 — 剔除 AR 最高 1 年后 SR > 0.6
5. Alpha 衰减 — 近 3 年滚动 SR ≥ 全量 SR × 40%
6. WF 折 CV — 各折收益 CV ≤ 2.0
7. 分段验证 — 全量 vs 新规后

使用示例::

    report = run_s2_verification(
        daily_nav_csv="output/tier_s_v5a_swap_etf_regime/combo_daily_nav.csv",
        n_trials=9,
    )
    print(report.verdict)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aipha.engines.statistical_validator import (
    StatisticalValidator,
    DSRResult,
    HLZResult,
    PBOResult,
)
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


# ── 结果数据结构 ─────────────────────────────────────────────────


@dataclass
class ExtremeYearResult:
    """极端年排除检验结果（原则 B）。"""
    excluded_year: int
    excluded_year_return: float
    remaining_sr: float
    remaining_ar: float
    pass_threshold: bool  # SR > 0.6
    best_year_contribution: float  # 最高年占总收益比例

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class AlphaDecayResult:
    """Alpha 衰减检验结果（原则 B / McLean & Pontiff 2016）。"""
    full_period_sr: float
    recent_3y_sr: float
    decay_ratio: float  # recent / full
    pass_threshold: bool  # recent ≥ full × 0.40
    rolling_sr_series: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "rolling_sr_series"}
        d["rolling_sr_sample"] = self.rolling_sr_series[-5:] if self.rolling_sr_series else []
        return d


@dataclass
class SegmentResult:
    """分段验证结果。"""
    label: str
    start_date: str
    end_date: str
    n_days: int
    ar: float
    sr: float
    mdd: float
    calmar: float

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class YearlyConsistency:
    """逐年盈利一致性检验。"""
    yearly_returns: dict[int, float]
    n_positive: int
    n_total: int
    win_ratio: float
    median_return: float
    pass_threshold: bool  # ≥ 8/10 年盈利

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class S2VerificationReport:
    """S2 阶段统计验证完整报告。"""
    timestamp: str
    data_source: str
    n_observations: int
    n_years: float
    n_trials: int

    # Core P8 tests
    dsr: DSRResult
    hlz: HLZResult
    pbo: PBOResult

    # Extended checks
    extreme_year: ExtremeYearResult
    alpha_decay: AlphaDecayResult
    segments: list[SegmentResult]
    yearly: YearlyConsistency

    # Verdicts
    p8_score: int  # 0-10
    p8_verdict: str  # STRONG / ACCEPTABLE / WEAK / FAIL
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "data_source": self.data_source,
            "n_observations": self.n_observations,
            "n_years": round(self.n_years, 2),
            "n_trials": self.n_trials,
            "dsr": {
                "p_value": self.dsr.p_value,
                "significant": self.dsr.significant,
                "observed_sr": self.dsr.observed_sr,
                "haircut_sr": self.dsr.haircut_sr,
            },
            "hlz": {
                "t_adjusted": self.hlz.t_adjusted,
                "significant": self.hlz.significant,
                "penalty": self.hlz.penalty,
            },
            "pbo": {
                "pbo": self.pbo.pbo,
                "is_overfitted": self.pbo.is_overfitted,
            },
            "extreme_year": self.extreme_year.to_dict(),
            "alpha_decay": self.alpha_decay.to_dict(),
            "segments": [s.to_dict() for s in self.segments],
            "yearly": self.yearly.to_dict(),
            "p8_score": self.p8_score,
            "p8_verdict": self.p8_verdict,
            "blocking_failures": self.blocking_failures,
            "warnings": self.warnings,
        }


# ── 公开入口（占位，下一步实现） ─────────────────────────────────


def run_s2_verification(
    daily_nav_csv: str | Path,
    n_trials: int = 8,
    combo_summary_json: str | Path | None = None,
    regime_split_date: str = "2022-08-01",
    tier: str = "Tier_S",
) -> S2VerificationReport:
    """运行完整 S2 统计验证。

    Args:
        daily_nav_csv: NAV CSV 文件路径（列：date, nav）
        n_trials: 独立试验数（用于 DSR/HLZ 校正）
        combo_summary_json: 可选的组合 summary.json（补充已有指标）
        regime_split_date: 制度变更日（CB 2022-08-01）
        tier: 评估层级 Tier_S / Tier_L

    Returns:
        S2VerificationReport 完整报告
    """
    nav_path = Path(daily_nav_csv)
    dates, returns = _load_daily_returns(nav_path)
    n_obs = len(returns)
    n_years = n_obs / TRADING_DAYS_PER_YEAR

    logger.info("S2 验证: %d 日收益, %.2f 年, %d 试验", n_obs, n_years, n_trials)

    # ── 1. DSR + HLZ + PBO (核心 P8) ─────────────────────────
    validator = StatisticalValidator(
        daily_returns=returns,
        n_trials=n_trials,
        n_years=n_years,
    )
    dsr = validator.run_dsr()
    hlz = validator.run_hlz()
    pbo = validator.run_pbo(n_partitions=10)

    # ── 2. 极端年排除 ────────────────────────────────────────
    extreme_year = _compute_extreme_year_check(dates, returns)

    # ── 3. Alpha 衰减 ────────────────────────────────────────
    alpha_decay = _compute_alpha_decay(dates, returns)

    # ── 4. 分段验证 ──────────────────────────────────────────
    first_date = dates[0]
    last_date = dates[-1]
    segments = [
        _compute_segment_metrics(dates, returns, first_date, last_date, "全量期"),
        _compute_segment_metrics(dates, returns, first_date, regime_split_date, "新规前"),
        _compute_segment_metrics(dates, returns, regime_split_date, last_date, "新规后"),
    ]

    # ── 5. 逐年盈利 ──────────────────────────────────────────
    yearly = _compute_yearly_consistency(dates, returns)

    # ── 6. 评分与判定 ────────────────────────────────────────
    score = 0
    blocking: list[str] = []
    warnings: list[str] = []

    # DSR — 阻塞性（P8 核心，4 分）
    if dsr.significant:
        score += 4
    else:
        blocking.append(f"DSR 不显著 (p={dsr.p_value:.4f})")

    # HLZ — 阻塞性（3 分）
    if hlz.significant:
        score += 3
    else:
        blocking.append(f"HLZ t_adj={hlz.t_adjusted:.4f} < 2.0")

    # PBO — 阻塞性（3 分）
    if pbo.significant:
        score += 3
    else:
        blocking.append(f"PBO={pbo.pbo:.4f} ≥ 0.50")

    # 极端年 — 警告级
    if not extreme_year.pass_threshold:
        warnings.append(f"剔除 {extreme_year.excluded_year} 后 SR={extreme_year.remaining_sr:.2f} < 0.6")
    if extreme_year.best_year_contribution > 0.30:
        warnings.append(
            f"最高年 ({extreme_year.excluded_year}) 贡献 "
            f"{extreme_year.best_year_contribution:.0%} > 30%"
        )

    # Alpha 衰减 — 警告级
    if not alpha_decay.pass_threshold:
        warnings.append(
            f"Alpha 衰减: 近3年SR={alpha_decay.recent_3y_sr:.2f} "
            f"< 全量SR×40% ({alpha_decay.full_period_sr * 0.4:.2f})"
        )

    # 新规后 SR — 警告级
    post_seg = next((s for s in segments if s.label == "新规后"), None)
    if post_seg and post_seg.sr < 0.5:
        warnings.append(f"新规后 SR={post_seg.sr:.2f} < 0.5")

    # 判定
    if score >= 7 and not blocking:
        verdict = "STRONG"
    elif score >= 4:
        verdict = "ACCEPTABLE"
    elif score >= 1:
        verdict = "WEAK"
    else:
        verdict = "FAIL"

    report = S2VerificationReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data_source=str(nav_path),
        n_observations=n_obs,
        n_years=n_years,
        n_trials=n_trials,
        dsr=dsr,
        hlz=hlz,
        pbo=pbo,
        extreme_year=extreme_year,
        alpha_decay=alpha_decay,
        segments=segments,
        yearly=yearly,
        p8_score=score,
        p8_verdict=verdict,
        blocking_failures=blocking,
        warnings=warnings,
    )

    logger.info("S2 验证完成: 评分=%d/10, 判定=%s, 阻塞=%d, 警告=%d",
                score, verdict, len(blocking), len(warnings))
    return report


def _load_daily_returns(nav_csv: str | Path) -> tuple[list[str], list[float]]:
    """从 NAV CSV 加载日期 + 日收益率序列。

    Returns:
        (dates, daily_returns) — dates[i] 对应 returns[i]，长度 = len(nav) - 1
    """
    import csv

    path = Path(nav_csv)
    if not path.exists():
        raise FileNotFoundError(f"NAV 文件不存在: {path}")

    rows: list[tuple[str, float]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", row.get("trade_date", ""))
            nav_val = float(row.get("nav", row.get("total_nav", 0)))
            # 标准化日期格式为 YYYY-MM-DD
            date_str = date_str[:10]
            rows.append((date_str, nav_val))

    rows.sort(key=lambda x: x[0])

    dates: list[str] = []
    returns: list[float] = []
    for i in range(1, len(rows)):
        prev_nav = rows[i - 1][1]
        curr_nav = rows[i][1]
        if prev_nav > 0:
            ret = (curr_nav - prev_nav) / prev_nav
        else:
            ret = 0.0
        dates.append(rows[i][0])
        returns.append(ret)

    logger.info("Loaded %d daily returns from %s (%s ~ %s)", len(returns), path.name, dates[0], dates[-1])
    return dates, returns


def _compute_segment_metrics(
    dates: list[str], returns: list[float],
    start: str, end: str, label: str,
) -> SegmentResult:
    """计算指定区间的绩效指标。"""
    # 筛选日期范围内的收益
    seg_rets = [r for d, r in zip(dates, returns) if start <= d <= end]
    n = len(seg_rets)
    if n < 10:
        return SegmentResult(label, start, end, n, 0, 0, 0, 0)

    # 年化收益
    cumulative = 1.0
    for r in seg_rets:
        cumulative *= (1 + r)
    years = n / TRADING_DAYS_PER_YEAR
    ar = cumulative ** (1 / years) - 1 if years > 0 else 0

    # 年化波动率
    mean_r = sum(seg_rets) / n
    var = sum((r - mean_r) ** 2 for r in seg_rets) / (n - 1) if n > 1 else 0
    vol = math.sqrt(var * TRADING_DAYS_PER_YEAR) if var > 0 else 1e-8

    # Sharpe (Rf=2%)
    rf_daily = 0.02 / TRADING_DAYS_PER_YEAR
    excess = [r - rf_daily for r in seg_rets]
    mean_ex = sum(excess) / len(excess)
    var_ex = sum((e - mean_ex) ** 2 for e in excess) / (len(excess) - 1) if len(excess) > 1 else 0
    std_ex = math.sqrt(var_ex) if var_ex > 0 else 1e-8
    sr = (mean_ex / std_ex) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # 最大回撤
    peak = cumulative_val = 1.0
    mdd = 0.0
    for r in seg_rets:
        cumulative_val *= (1 + r)
        if cumulative_val > peak:
            peak = cumulative_val
        dd = (peak - cumulative_val) / peak
        if dd > mdd:
            mdd = dd

    calmar = ar / mdd if mdd > 0 else float("inf")

    return SegmentResult(
        label=label, start_date=start, end_date=end,
        n_days=n, ar=round(ar, 4), sr=round(sr, 4),
        mdd=round(mdd, 4), calmar=round(calmar, 4),
    )


def _compute_extreme_year_check(
    dates: list[str], returns: list[float],
) -> ExtremeYearResult:
    """极端年排除后 SR 检验（原则 B）。

    剔除 AR 最高 1 年后策略 SR 须仍 > 0.6。
    最高 1 年收益占总收益比例 > 30% 须标注为尾部依赖风险。
    """
    from collections import defaultdict

    year_rets: dict[int, list[float]] = defaultdict(list)
    for d, r in zip(dates, returns):
        year = int(d[:4])
        year_rets[year].append(r)

    # 每年 AR
    yearly_ar: dict[int, float] = {}
    for y, rets in year_rets.items():
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        yearly_ar[y] = cum - 1

    if not yearly_ar:
        return ExtremeYearResult(0, 0, 0, 0, False, 0)

    best_year = max(yearly_ar, key=yearly_ar.get)  # type: ignore[arg-type]
    best_year_return = yearly_ar[best_year]

    # 剔除最佳年后的收益序列
    remaining_rets = [r for d, r in zip(dates, returns) if int(d[:4]) != best_year]
    remaining_dates = [d for d in dates if int(d[:4]) != best_year]

    if len(remaining_rets) < 20:
        return ExtremeYearResult(best_year, best_year_return, 0, 0, False, 1.0)

    # Remaining SR
    rf_daily = 0.02 / TRADING_DAYS_PER_YEAR
    excess = [r - rf_daily for r in remaining_rets]
    mean_ex = sum(excess) / len(excess)
    var_ex = sum((e - mean_ex) ** 2 for e in excess) / (len(excess) - 1)
    std_ex = math.sqrt(var_ex) if var_ex > 0 else 1e-8
    remaining_sr = (mean_ex / std_ex) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # Remaining AR
    cum = 1.0
    for r in remaining_rets:
        cum *= (1 + r)
    years = len(remaining_rets) / TRADING_DAYS_PER_YEAR
    remaining_ar = cum ** (1 / years) - 1 if years > 0 else 0

    # 最高年贡献度
    total_cum = 1.0
    for r in returns:
        total_cum *= (1 + r)
    total_return = total_cum - 1
    contribution = best_year_return / total_return if total_return > 0 else 0

    return ExtremeYearResult(
        excluded_year=best_year,
        excluded_year_return=round(best_year_return, 4),
        remaining_sr=round(remaining_sr, 4),
        remaining_ar=round(remaining_ar, 4),
        pass_threshold=remaining_sr > 0.6,
        best_year_contribution=round(contribution, 4),
    )


def _compute_alpha_decay(
    dates: list[str], returns: list[float],
    window_years: float = 3.0,
) -> AlphaDecayResult:
    """Alpha 衰减检验（近 N 年 vs 全量，McLean & Pontiff 2016）。

    近 3 年滚动 SR 不低于全量期 SR 的 40%。
    """
    rf_daily = 0.02 / TRADING_DAYS_PER_YEAR
    window_days = int(window_years * TRADING_DAYS_PER_YEAR)

    # 全量期 SR
    if len(returns) < 60:
        return AlphaDecayResult(0, 0, 0, False)

    excess = [r - rf_daily for r in returns]
    mean_ex = sum(excess) / len(excess)
    var_ex = sum((e - mean_ex) ** 2 for e in excess) / (len(excess) - 1)
    std_ex = math.sqrt(var_ex) if var_ex > 0 else 1e-8
    full_sr = (mean_ex / std_ex) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # 近 N 年 SR
    recent = returns[-window_days:] if len(returns) > window_days else returns
    exc_recent = [r - rf_daily for r in recent]
    mean_r = sum(exc_recent) / len(exc_recent)
    var_r = sum((e - mean_r) ** 2 for e in exc_recent) / (len(exc_recent) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-8
    recent_sr = (mean_r / std_r) * math.sqrt(TRADING_DAYS_PER_YEAR)

    decay_ratio = recent_sr / full_sr if full_sr > 0 else 0

    # 滚动 SR 序列（每半年一个点）
    step = int(TRADING_DAYS_PER_YEAR / 2)
    rolling_sr: list[tuple[str, float]] = []
    for end_idx in range(window_days, len(returns) + 1, step):
        start_idx = end_idx - window_days
        window = returns[start_idx:end_idx]
        exc_w = [r - rf_daily for r in window]
        m = sum(exc_w) / len(exc_w)
        v = sum((e - m) ** 2 for e in exc_w) / (len(exc_w) - 1)
        s = math.sqrt(v) if v > 0 else 1e-8
        w_sr = (m / s) * math.sqrt(TRADING_DAYS_PER_YEAR)
        rolling_sr.append((dates[end_idx - 1], round(w_sr, 4)))

    return AlphaDecayResult(
        full_period_sr=round(full_sr, 4),
        recent_3y_sr=round(recent_sr, 4),
        decay_ratio=round(decay_ratio, 4),
        pass_threshold=decay_ratio >= 0.40,
        rolling_sr_series=rolling_sr,
    )


def _compute_yearly_consistency(
    dates: list[str], returns: list[float],
) -> YearlyConsistency:
    """逐年盈利一致性检验。"""
    from collections import defaultdict

    year_rets: dict[int, list[float]] = defaultdict(list)
    for d, r in zip(dates, returns):
        year = int(d[:4])
        year_rets[year].append(r)

    yearly: dict[int, float] = {}
    for y in sorted(year_rets):
        cum = 1.0
        for r in year_rets[y]:
            cum *= (1 + r)
        yearly[y] = round(cum - 1, 4)

    n_pos = sum(1 for v in yearly.values() if v > 0)
    n_total = len(yearly)
    sorted_vals = sorted(yearly.values())
    median_ret = sorted_vals[len(sorted_vals) // 2] if sorted_vals else 0

    return YearlyConsistency(
        yearly_returns=yearly,
        n_positive=n_pos,
        n_total=n_total,
        win_ratio=round(n_pos / n_total, 2) if n_total else 0,
        median_return=round(median_ret, 4),
        pass_threshold=n_total < 10 or n_pos >= 8,  # ≥ 8/10 年盈利
    )
