"""追高上限校准 — 用自己的历史推荐数据定 ``max_chase_pct``，而不是拍一个。

``RecommendationConfig.max_chase_pct`` 默认 0.03、``RegimeGateConfig``
的 BEAR 乘数默认 0.5，这两个数是凭直觉给的。本模块把它们变成可校准量:

1. **追高幅度的实测分布** — ``ExecutionRecord.delay_bps`` 就是
   「信号价 → 你真正能下单的价」之间跑掉的部分，即实际追高幅度。
2. **追高是否真的预测后续亏损** — 先做一次先验的秩相关检验。
   若追高幅度与买入后表现无统计关系，收紧 cap 只会白掉成交，
   问题不在这里；此时本模块拒绝给新数字。
3. **只有相关性成立时才定 cap** — 取「平均结果转负」的那一档分箱下沿，
   而不是在候选网格上取最优。网格取最优正是过拟合，
   与本项目 DSR/PBO 门禁要防的是同一件事。

方法学约束（有意为之，不要绕过）:

- 样本不足 (< ``MIN_SAMPLE_FOR_CALIBRATION``) 时返回 ``None``、保持原值，
  并给出还差多少笔 —— 与 :func:`aipha.advisory.shortfall.assess_noise`
  同一条纪律: 不拿 n=1 的经历改参数。
- 候选 cap 的显著性做 Bonferroni 校正，试了几个就报几个。
- cap 由「结果转负」的分箱边界给出，不做网格 argmax。
- 只用买入侧记录 —— ``max_chase_pct`` 卡的是买入价上限。

用法::

    records = [ExecutionRecord.from_recommendation(rec, arrival_price=...,
                   fill_price=..., forward_prices=[...]) for rec in history]
    cal = calibrate_chase_cap(records, current_max_chase_pct=0.03)
    print(format_chase_calibration(cal))
    if cal.recommended_max_chase_pct is not None:
        config = apply_calibration(config, cal)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from statistics import fmean, median
from typing import Any, Sequence

from aipha.advisory.shortfall import ExecutionRecord, _is_valid_price

logger = logging.getLogger(__name__)

_BPS = 10_000.0

DEFAULT_ALPHA = 0.05

# 校准样本量下限 —— 比单次二项检验的 20 更高: 这里要分箱对比，
# 每箱都得有可比的笔数，否则「结果转负」只是某一笔的噪声。
MIN_SAMPLE_FOR_CALIBRATION = 30

# 单个分箱要被当作 cap 依据所需的最小笔数
MIN_SAMPLE_PER_BIN = 10

# cap 的合理区间。上限 10% 对齐 A 股涨跌停 —— 超过涨跌停的 cap
# 永不触发，等于没有约束；下限 0.5% 以下会把正常盘中波动全部挡掉。
CAP_LOWER_BOUND = 0.005
CAP_UPPER_BOUND = 0.10

# 分箱边界（追高比例）。首箱为 (-inf, 0]: 信号价之下买到，不算追高。
DEFAULT_BIN_EDGES: tuple[float, ...] = (0.0, 0.01, 0.02, 0.03, 0.05)

# 可用于定 cap 的结果指标 —— 必须是带符号的收益率。
# MAE 只作为分箱描述信息: 它几乎恒为负，「转负」规则对它退化。
OUTCOME_METRICS = ("first_forward_bps", "forward_total_bps")


# ── 记录侧派生量 ──────────────────────────────────────────────


def observed_chase_pct(record: ExecutionRecord) -> float | None:
    """该笔实际追高比例 —— 信号价到可下单价的涨幅。

    正 = 价格在你下单前跑高了（对买入不利），与 ``max_chase_pct``
    的语义完全一致: cap 卡的就是这个数。
    """
    delay = record.delay_bps
    return delay / _BPS if delay is not None else None


def forward_total_bps(record: ExecutionRecord) -> float | None:
    """整段持有期收益 —— 最后一个有效前瞻价对成交价。

    ``first_forward_bps`` 只看成交后第一个收盘，衡量入场时点；
    这个看完整持有，衡量追高是否最终也没赚到。
    """
    if not record.is_filled or not record.forward_prices:
        return None
    valid = [float(p) for p in record.forward_prices if _is_valid_price(p)]
    if not valid:
        return None
    fill = float(record.fill_price)  # type: ignore[arg-type]
    direction = 1.0 if record.side == "buy" else -1.0
    return direction * (valid[-1] - fill) / fill * _BPS


def _outcome(record: ExecutionRecord, metric: str) -> float | None:
    if metric == "forward_total_bps":
        return forward_total_bps(record)
    value = getattr(record, metric, None)
    return value


# ── 结果结构 ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChaseBin:
    """一个追高区间内的历史表现。"""

    lower_pct: float
    upper_pct: float
    n: int
    mean_outcome_bps: float | None
    median_outcome_bps: float | None
    loss_rate: float | None
    mean_mae_bps: float | None
    stop_hit_rate: float | None

    @property
    def label(self) -> str:
        lo = "-∞" if self.lower_pct == float("-inf") else f"{self.lower_pct:+.1%}"
        hi = "+∞" if self.upper_pct == float("inf") else f"{self.upper_pct:+.1%}"
        return f"({lo}, {hi}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "range": self.label,
            "lower_pct": _finite(self.lower_pct),
            "upper_pct": _finite(self.upper_pct),
            "n": self.n,
            "mean_outcome_bps": _r(self.mean_outcome_bps, 1),
            "median_outcome_bps": _r(self.median_outcome_bps, 1),
            "loss_rate": _r(self.loss_rate),
            "mean_mae_bps": _r(self.mean_mae_bps, 1),
            "stop_hit_rate": _r(self.stop_hit_rate),
        }


@dataclass(frozen=True, slots=True)
class ChaseSplit:
    """在某个候选 cap 上把样本一分为二的对比。"""

    cap_pct: float
    accept_n: int
    reject_n: int
    accept_mean_bps: float | None
    reject_mean_bps: float | None
    p_value: float | None
    testable: bool

    @property
    def edge_bps(self) -> float | None:
        """cap 带来的收益差 —— 正 = 被挡掉的那批确实更差。"""
        if self.accept_mean_bps is None or self.reject_mean_bps is None:
            return None
        return self.accept_mean_bps - self.reject_mean_bps

    @property
    def retention(self) -> float:
        """保留下来的成交比例 —— cap 收紧的代价。"""
        total = self.accept_n + self.reject_n
        return self.accept_n / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cap_pct": round(self.cap_pct, 4),
            "accept_n": self.accept_n,
            "reject_n": self.reject_n,
            "accept_mean_bps": _r(self.accept_mean_bps, 1),
            "reject_mean_bps": _r(self.reject_mean_bps, 1),
            "edge_bps": _r(self.edge_bps, 1),
            "retention": round(self.retention, 4),
            "p_value": _r(self.p_value, 6),
            "testable": self.testable,
        }


@dataclass
class ChaseCalibration:
    """追高上限校准结果。

    Attributes:
        recommended_max_chase_pct: 建议值。``None`` = 数据不支持给新值，
            保持 ``current_max_chase_pct``。**这是常见且正确的结果**，
            不是失败。
        predictive: 追高幅度是否显著预测买入后表现。为假时收紧 cap
            没有依据 —— 只会减少成交，不会改善结果。
        alpha_adjusted: Bonferroni 校正后的显著性水平（按候选 cap 个数）。
    """

    outcome_metric: str = "first_forward_bps"
    n_records: int = 0
    n_usable: int = 0
    current_max_chase_pct: float = 0.03
    recommended_max_chase_pct: float | None = None

    chase_p50: float | None = None
    chase_p75: float | None = None
    chase_p90: float | None = None
    chase_max: float | None = None

    spearman_rho: float | None = None
    p_value: float | None = None
    predictive: bool = False
    alpha: float = DEFAULT_ALPHA
    alpha_adjusted: float = DEFAULT_ALPHA
    n_candidates: int = 0

    sample_sufficient: bool = False
    min_n_needed: int = MIN_SAMPLE_FOR_CALIBRATION
    bins: list[ChaseBin] = field(default_factory=list)
    splits: list[ChaseSplit] = field(default_factory=list)
    verdict: str = ""

    @property
    def effective_cap(self) -> float:
        """实际该用的值 —— 有建议用建议，没建议保持现状。"""
        return (
            self.recommended_max_chase_pct
            if self.recommended_max_chase_pct is not None
            else self.current_max_chase_pct
        )

    @property
    def recommended_split(self) -> ChaseSplit | None:
        if self.recommended_max_chase_pct is None:
            return None
        return next(
            (s for s in self.splits
             if math.isclose(s.cap_pct, self.recommended_max_chase_pct)),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_metric": self.outcome_metric,
            "n_records": self.n_records,
            "n_usable": self.n_usable,
            "current_max_chase_pct": round(self.current_max_chase_pct, 4),
            "recommended_max_chase_pct": _r(self.recommended_max_chase_pct),
            "effective_cap": round(self.effective_cap, 4),
            "chase_p50": _r(self.chase_p50),
            "chase_p75": _r(self.chase_p75),
            "chase_p90": _r(self.chase_p90),
            "chase_max": _r(self.chase_max),
            "spearman_rho": _r(self.spearman_rho),
            "p_value": _r(self.p_value, 6),
            "predictive": self.predictive,
            "alpha": self.alpha,
            "alpha_adjusted": _r(self.alpha_adjusted, 6),
            "n_candidates": self.n_candidates,
            "sample_sufficient": self.sample_sufficient,
            "min_n_needed": self.min_n_needed,
            "bins": [b.to_dict() for b in self.bins],
            "splits": [s.to_dict() for s in self.splits],
            "verdict": self.verdict,
        }


@dataclass
class RegimeChaseCalibration:
    """分市场状态的追高乘数校准结果。"""

    base_regime: str = "BULL"
    base_cap_pct: float | None = None
    per_regime: dict[str, ChaseCalibration] = field(default_factory=dict)
    recommended_multipliers: dict[str, float] = field(default_factory=dict)
    current_multipliers: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def any_calibrated(self) -> bool:
        return bool(self.recommended_multipliers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_regime": self.base_regime,
            "base_cap_pct": _r(self.base_cap_pct),
            "recommended_multipliers": {
                k: round(v, 3) for k, v in self.recommended_multipliers.items()
            },
            "current_multipliers": dict(self.current_multipliers),
            "notes": dict(self.notes),
            "per_regime": {k: v.to_dict() for k, v in self.per_regime.items()},
        }


def _r(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


# ── 统计检验 ──────────────────────────────────────────────────


def _ranks(values: Sequence[float]) -> list[float]:
    """平均秩 —— 并列取名次均值。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = fmean(xs), fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2))


def spearman_one_tailed(
    xs: Sequence[float],
    ys: Sequence[float],
) -> tuple[float | None, float | None]:
    """秩相关及单尾 p 值，H1: rho < 0（追得越高，结果越差）。

    单尾方向是先验的 —— 我们只关心「追高伤业绩」这一个方向。
    反向显著（追高反而更好）不构成收紧 cap 的理由。
    """
    if len(xs) < 3 or len(xs) != len(ys):
        return None, None
    try:
        from scipy.stats import spearmanr

        res = spearmanr(xs, ys, alternative="less")
        return float(res.statistic), float(res.pvalue)
    except Exception:  # pragma: no cover - scipy 缺失/旧版本兜底
        logger.debug("scipy.spearmanr 不可用，退化为秩 Pearson + 正态近似")

    rho = _pearson(_ranks(xs), _ranks(ys))
    if rho is None:
        return None, None
    n = len(xs)
    if abs(rho) >= 1.0:
        return rho, 0.0 if rho < 0 else 1.0
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    return rho, _normal_cdf(t)


def welch_one_tailed(
    treatment: Sequence[float],
    control: Sequence[float],
) -> float | None:
    """Welch t 检验单尾 p 值，H1: mean(treatment) > mean(control)。"""
    if len(treatment) < 2 or len(control) < 2:
        return None
    try:
        from scipy.stats import ttest_ind

        res = ttest_ind(
            treatment, control, equal_var=False, alternative="greater",
        )
        p = float(res.pvalue)
        return p if math.isfinite(p) else None
    except Exception:  # pragma: no cover - scipy 缺失兜底
        logger.debug("scipy.ttest_ind 不可用，退化为正态近似")

    n1, n2 = len(treatment), len(control)
    m1, m2 = fmean(treatment), fmean(control)
    v1 = sum((x - m1) ** 2 for x in treatment) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in control) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    return 1.0 - _normal_cdf((m1 - m2) / se)


# ── 校准主流程 ────────────────────────────────────────────────


def _usable(
    records: Sequence[ExecutionRecord],
    metric: str,
) -> list[tuple[ExecutionRecord, float, float]]:
    """筛出能用于校准的记录，返回 (记录, 追高比例, 结果)。

    要求: 买入侧、已成交、有到达价（否则算不出追高）、有前瞻价。
    """
    out: list[tuple[ExecutionRecord, float, float]] = []
    for rec in records:
        if rec.side != "buy":
            continue
        chase = observed_chase_pct(rec)
        outcome = _outcome(rec, metric)
        if chase is None or outcome is None:
            continue
        if not (math.isfinite(chase) and math.isfinite(outcome)):
            continue
        out.append((rec, chase, float(outcome)))
    return out


def _quantile(sorted_values: Sequence[float], q: float) -> float | None:
    """线性插值分位数。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _build_bins(
    samples: list[tuple[ExecutionRecord, float, float]],
    edges: Sequence[float],
) -> list[ChaseBin]:
    bounds = [float("-inf"), *edges, float("inf")]
    bins: list[ChaseBin] = []
    for lower, upper in zip(bounds[:-1], bounds[1:]):
        members = [s for s in samples if lower < s[1] <= upper]
        outcomes = [s[2] for s in members]
        maes = [
            s[0].mae_bps for s in members
            if s[0].mae_bps is not None and math.isfinite(s[0].mae_bps)
        ]
        with_stop = [
            s[0] for s in members
            if s[0].stop_loss is not None and s[0].forward_prices
        ]
        bins.append(ChaseBin(
            lower_pct=lower,
            upper_pct=upper,
            n=len(members),
            mean_outcome_bps=fmean(outcomes) if outcomes else None,
            median_outcome_bps=median(outcomes) if outcomes else None,
            loss_rate=(
                sum(1 for v in outcomes if v < 0) / len(outcomes)
                if outcomes else None
            ),
            mean_mae_bps=fmean(maes) if maes else None,  # type: ignore[arg-type]
            stop_hit_rate=(
                sum(1 for r in with_stop if r.stop_hit) / len(with_stop)
                if with_stop else None
            ),
        ))
    return bins


def _build_split(
    samples: list[tuple[ExecutionRecord, float, float]],
    cap: float,
) -> ChaseSplit:
    accept = [s[2] for s in samples if s[1] <= cap]
    reject = [s[2] for s in samples if s[1] > cap]
    testable = len(accept) >= MIN_SAMPLE_PER_BIN and len(reject) >= MIN_SAMPLE_PER_BIN
    return ChaseSplit(
        cap_pct=cap,
        accept_n=len(accept),
        reject_n=len(reject),
        accept_mean_bps=fmean(accept) if accept else None,
        reject_mean_bps=fmean(reject) if reject else None,
        p_value=welch_one_tailed(accept, reject) if testable else None,
        testable=testable,
    )


def _pick_cap(bins: list[ChaseBin], overall_mean: float) -> tuple[float | None, str]:
    """取「平均结果转负」的那一档分箱下沿作为 cap。

    条件: 该箱平均结果为负、且差于全样本均值、且笔数够。
    刻意不在候选网格上取最优 —— 那是在同一批数据上挑最好看的切点，
    正是本项目 DSR/PBO 门禁要防的过拟合。
    """
    for b in bins:
        if b.lower_pct <= 0 or not math.isfinite(b.lower_pct):
            continue  # cap 必须为正: 「买得比信号价便宜」不该被挡
        if b.n < MIN_SAMPLE_PER_BIN:
            continue
        if b.mean_outcome_bps is None:
            continue
        if b.mean_outcome_bps < 0 and b.mean_outcome_bps < overall_mean:
            return b.lower_pct, (
                f"追高进入 {b.label} 后平均结果转负 "
                f"({b.mean_outcome_bps:+.1f} bps, n={b.n})，"
                f"cap 取该区间下沿 {b.lower_pct:.1%}"
            )
    return None, "各追高区间的平均结果都不为负 —— 数据里找不到该收紧的位置"


def calibrate_chase_cap(
    records: Sequence[ExecutionRecord],
    current_max_chase_pct: float = 0.03,
    outcome_metric: str = "first_forward_bps",
    bin_edges: Sequence[float] = DEFAULT_BIN_EDGES,
    alpha: float = DEFAULT_ALPHA,
) -> ChaseCalibration:
    """从历史执行记录校准 ``max_chase_pct``。

    Args:
        records: 历史执行记录。需带 ``arrival_price``（算追高幅度）
            与 ``forward_prices``（算买入后表现），否则该笔被跳过。
        current_max_chase_pct: 当前配置值，用于对比，也是无法校准时的回退值。
        outcome_metric: ``first_forward_bps``（默认，衡量入场时点）或
            ``forward_total_bps``（衡量整段持有）。
        bin_edges: 追高比例分箱边界。
        alpha: 显著性水平。

    Returns:
        ``recommended_max_chase_pct is None`` 表示数据不支持改值 ——
        这是常见结果，应保持原值而不是硬凑一个数。
    """
    if outcome_metric not in OUTCOME_METRICS:
        raise ValueError(
            f"outcome_metric 须为 {OUTCOME_METRICS} 之一（必须是带符号收益率，"
            f"MAE 之类恒负指标无法支撑「结果转负」判据），收到 {outcome_metric!r}",
        )

    cal = ChaseCalibration(
        outcome_metric=outcome_metric,
        n_records=len(records),
        current_max_chase_pct=float(current_max_chase_pct),
        alpha=alpha,
        alpha_adjusted=alpha,
    )

    samples = _usable(records, outcome_metric)
    cal.n_usable = len(samples)
    if not samples:
        cal.verdict = (
            "无可用样本 —— 校准需要每笔记录同时带 arrival_price（可下单价）"
            "与 forward_prices（成交后价格）。缺 arrival_price 就算不出追高幅度。"
        )
        return cal

    chases = sorted(s[1] for s in samples)
    cal.chase_p50 = _quantile(chases, 0.50)
    cal.chase_p75 = _quantile(chases, 0.75)
    cal.chase_p90 = _quantile(chases, 0.90)
    cal.chase_max = chases[-1]

    cal.bins = _build_bins(samples, bin_edges)

    if len(samples) < MIN_SAMPLE_FOR_CALIBRATION:
        cal.verdict = (
            f"样本不足 (n={len(samples)}) — 至少需要 "
            f"{MIN_SAMPLE_FOR_CALIBRATION} 笔带到达价与前瞻价的买入记录"
            f"才能校准追高上限。保持当前值 {current_max_chase_pct:.1%}。"
            "此时改 cap 与凭直觉改没有区别。"
        )
        return cal
    cal.sample_sufficient = True

    # 1) 先验方向检验: 追得越高，结果是否越差
    cal.spearman_rho, cal.p_value = spearman_one_tailed(
        [s[1] for s in samples], [s[2] for s in samples],
    )
    cal.predictive = bool(cal.p_value is not None and cal.p_value < alpha)

    # 2) 候选 cap 的两侧对比（含当前值，便于看现状是否有依据）
    candidates = sorted({
        float(e) for e in bin_edges if e > 0
    } | {float(current_max_chase_pct)})
    cal.splits = [_build_split(samples, c) for c in candidates]
    n_testable = sum(1 for s in cal.splits if s.testable)
    cal.n_candidates = n_testable
    cal.alpha_adjusted = alpha / n_testable if n_testable else alpha

    if not cal.predictive:
        rho_txt = f"{cal.spearman_rho:+.3f}" if cal.spearman_rho is not None else "n/a"
        p_txt = f"{cal.p_value:.4f}" if cal.p_value is not None else "n/a"
        cal.verdict = (
            f"无证据支持收紧 (n={len(samples)}, rho={rho_txt}, p={p_txt}) — "
            f"追高幅度与买入后表现没有统计关系。收紧 cap 只会减少成交、"
            f"不会改善结果。保持当前值 {current_max_chase_pct:.1%}，"
            "「一买就跌」的原因应到别处找（选股信号本身、持有期、止损位）。"
        )
        return cal

    # 3) 只在方向成立后定 cap，且用分箱边界而非网格最优
    overall_mean = fmean(s[2] for s in samples)
    picked, reason = _pick_cap(cal.bins, overall_mean)
    if picked is None:
        cal.verdict = (
            f"追高与结果负相关 (rho={cal.spearman_rho:+.3f}, p={cal.p_value:.4f})，"
            f"但{reason}。保持当前值 {current_max_chase_pct:.1%}。"
        )
        return cal

    clamped = min(max(picked, CAP_LOWER_BOUND), CAP_UPPER_BOUND)
    cal.recommended_max_chase_pct = round(clamped, 4)

    split = cal.recommended_split
    sig_txt = ""
    if split is not None and split.p_value is not None:
        verified = split.p_value < cal.alpha_adjusted
        sig_txt = (
            f" 该切点两侧差 {split.edge_bps:+.1f} bps"
            f"（p={split.p_value:.4f} vs Bonferroni 校正后 "
            f"{cal.alpha_adjusted:.4f}，{'通过' if verified else '未通过'}），"
            f"保留 {split.retention:.0%} 的成交。"
        )
    elif split is not None:
        sig_txt = (
            f" 该切点两侧笔数不足（{split.accept_n}/{split.reject_n}），"
            "差异未做检验。"
        )

    clamp_txt = ""
    if not math.isclose(clamped, picked):
        clamp_txt = f" 原始值 {picked:.1%} 已收敛到合理区间。"

    cal.verdict = (
        f"建议 {cal.recommended_max_chase_pct:.1%}（当前 "
        f"{current_max_chase_pct:.1%}）— {reason}。{sig_txt}{clamp_txt}"
        " 该值在同一批数据上选出，上线前应在新样本上复核。"
    )
    return cal


def calibrate_regime_multipliers(
    records_by_regime: dict[str, Sequence[ExecutionRecord]],
    current_max_chase_pct: float = 0.03,
    current_multipliers: dict[str, float] | None = None,
    base_regime: str = "BULL",
    outcome_metric: str = "first_forward_bps",
    bin_edges: Sequence[float] = DEFAULT_BIN_EDGES,
    alpha: float = DEFAULT_ALPHA,
) -> RegimeChaseCalibration:
    """按市场状态校准 ``chase_pct_multiplier``。

    对每个状态独立跑一遍 :func:`calibrate_chase_cap`，乘数取
    「该状态 cap / 基准状态 cap」。**只有基准状态与该状态都校准成功时
    才给乘数** —— 分状态后每档样本会骤减，多数情况下会返回
    「样本不足，保持默认」，这正是该说的话: BEAR=0.5 目前无法验证。

    Args:
        records_by_regime: 状态 → 该状态下的执行记录。
            键名与 ``normalize_regime()`` 的输出一致（BULL/NEUTRAL/BEAR）。
        current_multipliers: 当前配置，用于对比与回退。
    """
    current = dict(current_multipliers or {"BULL": 1.0, "NEUTRAL": 1.0, "BEAR": 0.5})
    result = RegimeChaseCalibration(
        base_regime=base_regime,
        current_multipliers=current,
    )

    for regime, recs in records_by_regime.items():
        result.per_regime[regime] = calibrate_chase_cap(
            recs,
            current_max_chase_pct=current_max_chase_pct,
            outcome_metric=outcome_metric,
            bin_edges=bin_edges,
            alpha=alpha,
        )

    base = result.per_regime.get(base_regime)
    if base is None or base.recommended_max_chase_pct is None:
        need = (
            f"基准状态 {base_regime} 未能校准"
            f"（{'无该状态样本' if base is None else base.verdict}）"
        )
        for regime in current:
            result.notes[regime] = (
                f"{need} —— 乘数是相对量，基准定不下来就无从换算。"
                f"保持默认 {current.get(regime, 1.0)}。"
            )
        return result

    result.base_cap_pct = base.recommended_max_chase_pct
    for regime, cal in result.per_regime.items():
        if regime == base_regime:
            result.recommended_multipliers[regime] = 1.0
            result.notes[regime] = f"基准状态，cap={base.recommended_max_chase_pct:.1%}"
            continue
        if cal.recommended_max_chase_pct is None:
            result.notes[regime] = (
                f"未能校准，保持默认 {current.get(regime, 1.0)} —— {cal.verdict}"
            )
            continue
        mult = cal.recommended_max_chase_pct / base.recommended_max_chase_pct
        result.recommended_multipliers[regime] = round(mult, 3)
        result.notes[regime] = (
            f"cap={cal.recommended_max_chase_pct:.1%} / 基准 "
            f"{base.recommended_max_chase_pct:.1%} = {mult:.2f}"
            f"（当前 {current.get(regime, 1.0)}，n={cal.n_usable}）"
        )

    for regime in current:
        result.notes.setdefault(
            regime,
            f"无 {regime} 状态样本，保持默认 {current.get(regime, 1.0)}",
        )
    return result


def apply_calibration(
    config: Any,
    calibration: ChaseCalibration,
    regime_result: RegimeChaseCalibration | None = None,
) -> Any:
    """把校准结果写进 ``RecommendationConfig``，返回新对象。

    只覆盖数据支持的项: ``recommended_*`` 为 ``None`` 的一律保持原值。
    不改传入的 config。
    """
    from dataclasses import replace as _replace  # 局部导入: 保持本模块为纯分析

    updates: dict[str, Any] = {}
    if calibration.recommended_max_chase_pct is not None:
        updates["max_chase_pct"] = calibration.recommended_max_chase_pct

    if regime_result is not None and regime_result.any_calibrated:
        gate = config.regime_gate
        merged = dict(gate.chase_pct_multiplier)
        merged.update(regime_result.recommended_multipliers)
        updates["regime_gate"] = _replace(gate, chase_pct_multiplier=merged)

    return _replace(config, **updates) if updates else config


def format_chase_calibration(cal: ChaseCalibration) -> str:
    """生成人类可读的校准报告。"""
    def bps(value: float | None) -> str:
        return f"{value:+.1f}" if value is not None else "n/a"

    def pct(value: float | None) -> str:
        return f"{value:.1%}" if value is not None else "n/a"

    lines = [
        "=== 追高上限校准 ===",
        f"结果指标: {cal.outcome_metric}   "
        f"记录 {cal.n_records} 笔，可用 {cal.n_usable} 笔",
        f"当前值: {pct(cal.current_max_chase_pct)}   "
        f"建议值: {pct(cal.recommended_max_chase_pct) if cal.recommended_max_chase_pct is not None else '不改（数据不支持）'}",
        "",
        "-- 实际追高幅度分布 --",
        f"  中位数 {pct(cal.chase_p50)}   P75 {pct(cal.chase_p75)}   "
        f"P90 {pct(cal.chase_p90)}   最大 {pct(cal.chase_max)}",
    ]

    if cal.bins:
        lines += [
            "",
            "-- 分箱表现（正 = 买入后上涨）--",
            f"  {'追高区间':<20} {'笔数':>5} {'均值bps':>9} "
            f"{'中位bps':>9} {'亏损率':>7} {'均MAE':>9} {'触损率':>7}",
        ]
        for b in cal.bins:
            if b.n == 0:
                continue
            lines.append(
                f"  {b.label:<20} {b.n:>5} {bps(b.mean_outcome_bps):>9} "
                f"{bps(b.median_outcome_bps):>9} "
                f"{(pct(b.loss_rate) if b.loss_rate is not None else 'n/a'):>7} "
                f"{bps(b.mean_mae_bps):>9} "
                f"{(pct(b.stop_hit_rate) if b.stop_hit_rate is not None else 'n/a'):>7}"
            )

    if cal.sample_sufficient:
        rho = f"{cal.spearman_rho:+.3f}" if cal.spearman_rho is not None else "n/a"
        p = f"{cal.p_value:.4f}" if cal.p_value is not None else "n/a"
        lines += [
            "",
            "-- 方向检验（H1: 追得越高结果越差）--",
            f"  秩相关 rho={rho}   单尾 p={p}   "
            f"结论: {'追高确实伤业绩' if cal.predictive else '无统计关系'}",
        ]

    if cal.splits:
        lines += [
            "",
            f"-- 候选切点（Bonferroni 校正后阈值 {cal.alpha_adjusted:.4f}，"
            f"共检验 {cal.n_candidates} 个）--",
            f"  {'cap':>7} {'接受':>5} {'拒绝':>5} {'接受均值':>10} "
            f"{'拒绝均值':>10} {'差额':>9} {'保留成交':>9} {'p值':>9}",
        ]
        for s in cal.splits:
            mark = " *" if (
                cal.recommended_max_chase_pct is not None
                and math.isclose(s.cap_pct, cal.recommended_max_chase_pct)
            ) else "  "
            p_txt = f"{s.p_value:.4f}" if s.p_value is not None else "样本不足"
            lines.append(
                f"{mark}{pct(s.cap_pct):>7} {s.accept_n:>5} {s.reject_n:>5} "
                f"{bps(s.accept_mean_bps):>10} {bps(s.reject_mean_bps):>10} "
                f"{bps(s.edge_bps):>9} {pct(s.retention):>9} {p_txt:>9}"
            )

    lines += ["", "-- 结论 --", f"  {cal.verdict}"]
    return "\n".join(lines)


def format_regime_calibration(result: RegimeChaseCalibration) -> str:
    """生成分状态乘数校准报告。"""
    lines = [
        "=== 分市场状态追高乘数校准 ===",
        f"基准状态: {result.base_regime}   基准 cap: "
        f"{f'{result.base_cap_pct:.1%}' if result.base_cap_pct is not None else 'n/a'}",
        "",
    ]
    keys = sorted(set(result.current_multipliers) | set(result.per_regime))
    for regime in keys:
        cur = result.current_multipliers.get(regime)
        rec = result.recommended_multipliers.get(regime)
        cal = result.per_regime.get(regime)
        lines.append(
            f"  {regime:<8} 当前 {cur if cur is not None else 'n/a'}   "
            f"建议 {rec if rec is not None else '不改'}   "
            f"样本 {cal.n_usable if cal else 0} 笔"
        )
        note = result.notes.get(regime)
        if note:
            lines.append(f"           {note}")
    if not result.any_calibrated:
        lines += [
            "",
            "  注: 没有任何状态达到校准条件。分状态后样本会骤减，"
            "这是预期结果 —— 说明当前的 BEAR 乘数仍是未经验证的先验值。",
        ]
    return "\n".join(lines)
