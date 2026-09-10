"""推荐实施差距度量 — 量化「推荐打分价」到「实际成交价」之间漏掉的收益。

回答两个问题:

1. **钱漏在哪** — 按 Perold (1988) implementation shortfall 分解:
   延迟成本（推荐→可下单）+ 执行成本（可下单→实际成交）+ 费用。
   回测收益里凡是落在延迟成本里的部分，实盘拿不到。

2. **「一买就跌」是策略失效还是正常波动** — 用二项检验对比
   「实际首日浮亏比例」与「策略胜率隐含的基线浮亏比例」。
   样本不足时明确给出「还需多少笔才能判断」，而不是给一个假结论。

符号约定: 所有 *_bps 指标 **正 = 对你不利**（买入付得更多 / 卖出收得更少），
买卖方向已归一，可直接跨方向汇总。

用法::

    records = [ExecutionRecord.from_recommendation(
        rec, arrival_price=10.15, fill_price=10.18, qty=1000,
        fees=5.2, forward_prices=[10.05, 9.98, 10.30]) for rec in recs]
    report = analyze_shortfall(records, baseline_win_rate=0.45)
    print(format_shortfall_report(report))
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from aipha.advisory.models import Recommendation

logger = logging.getLogger(__name__)

_BPS = 10_000.0

# 二项检验显著性水平
DEFAULT_ALPHA = 0.05

# 判定样本量下限 — 低于此值不给失效结论，只报「样本不足」
MIN_SAMPLE_FOR_VERDICT = 20


def _is_valid_price(value: Any) -> bool:
    """价格是否为正的有限数。"""
    if value is None:
        return False
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 0


@dataclass
class ExecutionRecord:
    """一条推荐的实际执行结果。

    Attributes:
        signal_price: 推荐打分时点的参考价（Recommendation.signal_price）
        arrival_price: 实际可下单的第一个价格（通常是次日开盘）。
            None 表示未记录，此时只能算总差距，无法拆分延迟/执行成本。
        fill_price: 实际成交价。None 表示未成交（计入未成交率）。
        forward_prices: 成交后逐日收盘价，用于买入后表现分析。
    """

    ts_code: str
    signal_price: float
    arrival_price: float | None = None
    fill_price: float | None = None
    qty: int = 0
    fees: float = 0.0
    side: str = "buy"
    stop_loss: float | None = None
    forward_prices: list[float] = field(default_factory=list)
    horizon: str = ""

    @classmethod
    def from_recommendation(
        cls,
        rec: Recommendation,
        arrival_price: float | None = None,
        fill_price: float | None = None,
        qty: int = 0,
        fees: float = 0.0,
        forward_prices: list[float] | None = None,
    ) -> ExecutionRecord:
        """从 Recommendation 构建 —— 复用推荐自带的 signal_price / stop_loss。"""
        if not _is_valid_price(rec.signal_price):
            raise ValueError(
                f"{rec.ts_code}: 推荐缺少有效 signal_price，无法度量实施差距。"
                "请确认 RecommendationEngine.generate() 收到了 current_prices。",
            )
        return cls(
            ts_code=rec.ts_code,
            signal_price=float(rec.signal_price),  # type: ignore[arg-type]
            arrival_price=arrival_price,
            fill_price=fill_price,
            qty=qty,
            fees=fees,
            side="buy" if rec.is_buy else "sell",
            stop_loss=rec.stop_loss,
            forward_prices=list(forward_prices or []),
            horizon=rec.horizon.value,
        )

    # ── 方向归一 ──────────────────────────────────────────────

    @property
    def _direction(self) -> float:
        """买入 +1 / 卖出 -1 —— 让「正 = 不利」对两个方向都成立。"""
        return 1.0 if self.side == "buy" else -1.0

    @property
    def is_filled(self) -> bool:
        return _is_valid_price(self.fill_price)

    # ── 差距分解 ──────────────────────────────────────────────

    @property
    def delay_bps(self) -> float | None:
        """延迟成本 — 推荐打分到可下单之间价格跑掉的部分。

        这是「一买就跌」最常见的隐藏来源: 回测按信号价成交，
        你按次日开盘价成交，差额是结构性漏损。
        """
        if not _is_valid_price(self.arrival_price):
            return None
        arrival = float(self.arrival_price)  # type: ignore[arg-type]
        return self._direction * (arrival - self.signal_price) / self.signal_price * _BPS

    @property
    def execution_bps(self) -> float | None:
        """执行成本 — 可下单价到实际成交价，由下单方式造成。"""
        if not self.is_filled or not _is_valid_price(self.arrival_price):
            return None
        arrival = float(self.arrival_price)  # type: ignore[arg-type]
        fill = float(self.fill_price)  # type: ignore[arg-type]
        return self._direction * (fill - arrival) / arrival * _BPS

    @property
    def fee_bps(self) -> float | None:
        """费用占成交金额的比例。"""
        if not self.is_filled or self.qty <= 0:
            return None
        notional = float(self.fill_price) * self.qty  # type: ignore[arg-type]
        if notional <= 0:
            return None
        return abs(self.fees) / notional * _BPS

    @property
    def total_shortfall_bps(self) -> float | None:
        """总实施差距 = 信号价到成交价的滑移 + 费用。"""
        if not self.is_filled:
            return None
        fill = float(self.fill_price)  # type: ignore[arg-type]
        slip = self._direction * (fill - self.signal_price) / self.signal_price * _BPS
        return slip + (self.fee_bps or 0.0)

    # ── 买入后表现 ────────────────────────────────────────────

    @property
    def first_forward_bps(self) -> float | None:
        """成交后第一个收盘的浮动盈亏（负 = 浮亏）。"""
        if not self.is_filled or not self.forward_prices:
            return None
        fill = float(self.fill_price)  # type: ignore[arg-type]
        return self._direction * (self.forward_prices[0] - fill) / fill * _BPS

    @property
    def mae_bps(self) -> float | None:
        """最大不利偏移 (maximum adverse excursion) — 持有期内最大浮亏。

        负值。衡量「买了就跌」跌得有多深，而不只是有没有跌。
        """
        if not self.is_filled or not self.forward_prices:
            return None
        fill = float(self.fill_price)  # type: ignore[arg-type]
        excursions = [
            self._direction * (p - fill) / fill * _BPS
            for p in self.forward_prices
            if _is_valid_price(p)
        ]
        return min(excursions) if excursions else None

    @property
    def stop_hit(self) -> bool:
        """持有期内是否触及推荐给出的止损位。"""
        if not self.is_filled or self.stop_loss is None or not self.forward_prices:
            return False
        stop = float(self.stop_loss)
        if stop <= 0:
            return False
        if self.side == "buy":
            return any(p <= stop for p in self.forward_prices if _is_valid_price(p))
        return any(p >= stop for p in self.forward_prices if _is_valid_price(p))


@dataclass
class NoiseVerdict:
    """「一买就跌」是否显著偏离基线的判定。"""

    n: int
    observed_loss_rate: float
    baseline_loss_rate: float
    p_value: float | None = None
    is_significant: bool = False
    sample_sufficient: bool = False
    min_n_for_detection: int | None = None
    verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "observed_loss_rate": round(self.observed_loss_rate, 4),
            "baseline_loss_rate": round(self.baseline_loss_rate, 4),
            "p_value": round(self.p_value, 6) if self.p_value is not None else None,
            "is_significant": self.is_significant,
            "sample_sufficient": self.sample_sufficient,
            "min_n_for_detection": self.min_n_for_detection,
            "verdict": self.verdict,
        }


@dataclass
class ShortfallReport:
    """实施差距汇总报告。"""

    total: int = 0
    filled: int = 0
    mean_delay_bps: float | None = None
    mean_execution_bps: float | None = None
    mean_fee_bps: float | None = None
    mean_total_bps: float | None = None
    median_total_bps: float | None = None
    worst_total_bps: float | None = None
    immediate_loss_rate: float | None = None
    mean_mae_bps: float | None = None
    stop_hit_rate: float | None = None
    noise_verdict: NoiseVerdict | None = None
    per_record: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        return self.filled / self.total if self.total else 0.0

    @property
    def annualized_drag_pct(self) -> float | None:
        """按单边差距粗估的年化拖累 —— 需要调用方提供换手率才有意义。

        这里只返回单边差距的百分比形式，年化换算留给调用方
        （乘以年换手次数）。
        """
        if self.mean_total_bps is None:
            return None
        return self.mean_total_bps / 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "filled": self.filled,
            "fill_rate": round(self.fill_rate, 4),
            "mean_delay_bps": _r(self.mean_delay_bps),
            "mean_execution_bps": _r(self.mean_execution_bps),
            "mean_fee_bps": _r(self.mean_fee_bps),
            "mean_total_bps": _r(self.mean_total_bps),
            "median_total_bps": _r(self.median_total_bps),
            "worst_total_bps": _r(self.worst_total_bps),
            "immediate_loss_rate": _r(self.immediate_loss_rate),
            "mean_mae_bps": _r(self.mean_mae_bps),
            "stop_hit_rate": _r(self.stop_hit_rate),
            "noise_verdict": (
                self.noise_verdict.to_dict() if self.noise_verdict else None
            ),
        }


def _r(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _collect(records: list[ExecutionRecord], attr: str) -> list[float]:
    """收集某个指标的非 None 取值。"""
    out: list[float] = []
    for rec in records:
        val = getattr(rec, attr)
        if val is not None and math.isfinite(val):
            out.append(float(val))
    return out


def assess_noise(
    n_losses: int,
    n_total: int,
    baseline_loss_rate: float,
    alpha: float = DEFAULT_ALPHA,
) -> NoiseVerdict:
    """判定观测到的浮亏比例是否显著高于基线。

    单尾二项检验（H1: 实际浮亏率 > 基线）。样本量低于
    ``MIN_SAMPLE_FOR_VERDICT`` 时不给失效结论，只报样本不足并给出
    达到可判定所需的最小笔数 —— 避免用 n=1 的经历去改策略。

    Args:
        n_losses: 买入后首日浮亏的笔数
        n_total: 有效样本笔数
        baseline_loss_rate: 基线浮亏率，通常取 1 - 策略回测胜率
    """
    baseline = min(max(float(baseline_loss_rate), 1e-6), 1 - 1e-6)
    if n_total <= 0:
        return NoiseVerdict(
            n=0, observed_loss_rate=0.0, baseline_loss_rate=baseline,
            verdict="无有效样本 — 无法判定",
        )

    observed = n_losses / n_total
    p_value = _binom_sf(n_losses, n_total, baseline)
    sufficient = n_total >= MIN_SAMPLE_FOR_VERDICT
    significant = bool(sufficient and p_value is not None and p_value < alpha)

    min_n = _min_n_for_detection(observed, baseline, alpha) if observed > baseline else None

    if not sufficient:
        need = max(MIN_SAMPLE_FOR_VERDICT, min_n or 0)
        verdict = (
            f"样本不足 (n={n_total}) — 观测浮亏率 {observed:.0%} vs "
            f"基线 {baseline:.0%}，但至少需要 {need} 笔才能区分策略失效与正常波动。"
            "此时不应修改策略参数。"
        )
    elif significant:
        verdict = (
            f"显著异常 (n={n_total}, p={p_value:.4f}) — 浮亏率 {observed:.0%} "
            f"显著高于基线 {baseline:.0%}，策略行为已偏离回测预期，值得排查。"
        )
    else:
        verdict = (
            f"属正常波动 (n={n_total}, p={p_value:.4f}) — 浮亏率 {observed:.0%} "
            f"与基线 {baseline:.0%} 无统计差异。「买了就跌」是该策略的设计内表现。"
        )

    return NoiseVerdict(
        n=n_total,
        observed_loss_rate=observed,
        baseline_loss_rate=baseline,
        p_value=p_value,
        is_significant=significant,
        sample_sufficient=sufficient,
        min_n_for_detection=min_n,
        verdict=verdict,
    )


def _binom_sf(k: int, n: int, p: float) -> float | None:
    """P(X >= k) — 单尾二项检验 p 值。scipy 不可用时退化为正态近似。"""
    try:
        from scipy.stats import binomtest

        return float(binomtest(k=k, n=n, p=p, alternative="greater").pvalue)
    except Exception:  # pragma: no cover - scipy 缺失时的兜底
        logger.debug("scipy 不可用，二项检验退化为正态近似")
        sigma = math.sqrt(n * p * (1 - p))
        if sigma <= 0:
            return None
        z = (k - 0.5 - n * p) / sigma
        return 0.5 * math.erfc(z / math.sqrt(2))


def _min_n_for_detection(
    observed_rate: float,
    baseline: float,
    alpha: float = DEFAULT_ALPHA,
    max_n: int = 2000,
) -> int | None:
    """在当前效应量下，达到显著所需的最小样本量。

    用于把「样本不够」讲成一个具体数字，而不是一句托辞。
    """
    if observed_rate <= baseline:
        return None
    n = MIN_SAMPLE_FOR_VERDICT
    while n <= max_n:
        k = math.ceil(observed_rate * n)
        p_val = _binom_sf(k, n, baseline)
        if p_val is not None and p_val < alpha:
            return n
        n += 1
    return None


def analyze_shortfall(
    records: list[ExecutionRecord],
    baseline_win_rate: float | None = None,
    alpha: float = DEFAULT_ALPHA,
) -> ShortfallReport:
    """汇总实施差距并判定「买了就跌」是否异常。

    Args:
        records: 执行记录
        baseline_win_rate: 策略回测胜率。提供时用 1-胜率 作为基线浮亏率
            做二项检验；不提供则跳过判定（不猜基线）。
    """
    report = ShortfallReport(total=len(records))
    if not records:
        return report

    filled = [r for r in records if r.is_filled]
    report.filled = len(filled)

    report.mean_delay_bps = _mean(_collect(records, "delay_bps"))
    report.mean_execution_bps = _mean(_collect(filled, "execution_bps"))
    report.mean_fee_bps = _mean(_collect(filled, "fee_bps"))

    totals = _collect(filled, "total_shortfall_bps")
    report.mean_total_bps = _mean(totals)
    report.median_total_bps = median(totals) if totals else None
    report.worst_total_bps = max(totals) if totals else None

    forwards = _collect(filled, "first_forward_bps")
    n_losses = sum(1 for v in forwards if v < 0)
    if forwards:
        report.immediate_loss_rate = n_losses / len(forwards)

    report.mean_mae_bps = _mean(_collect(filled, "mae_bps"))

    with_stop = [r for r in filled if r.stop_loss is not None and r.forward_prices]
    if with_stop:
        report.stop_hit_rate = sum(1 for r in with_stop if r.stop_hit) / len(with_stop)

    if baseline_win_rate is not None and forwards:
        report.noise_verdict = assess_noise(
            n_losses=n_losses,
            n_total=len(forwards),
            baseline_loss_rate=1.0 - float(baseline_win_rate),
            alpha=alpha,
        )

    report.per_record = [
        {
            "ts_code": r.ts_code,
            "side": r.side,
            "horizon": r.horizon,
            "signal_price": r.signal_price,
            "fill_price": r.fill_price,
            "delay_bps": _r(r.delay_bps),
            "execution_bps": _r(r.execution_bps),
            "fee_bps": _r(r.fee_bps),
            "total_shortfall_bps": _r(r.total_shortfall_bps),
            "first_forward_bps": _r(r.first_forward_bps),
            "mae_bps": _r(r.mae_bps),
            "stop_hit": r.stop_hit,
        }
        for r in records
    ]
    return report


def format_shortfall_report(report: ShortfallReport) -> str:
    """生成人类可读的实施差距报告。"""
    def fmt(value: float | None, unit: str = "bps", digits: int = 1) -> str:
        return f"{value:+.{digits}f} {unit}" if value is not None else "n/a"

    def pct(value: float | None) -> str:
        return f"{value:.1%}" if value is not None else "n/a"

    lines = [
        "=== 推荐实施差距报告 ===",
        f"推荐笔数: {report.total}   成交: {report.filled}   "
        f"成交率: {pct(report.fill_rate)}",
        "",
        "-- 差距分解 (正 = 对你不利) --",
        f"  延迟成本 (推荐→可下单): {fmt(report.mean_delay_bps)}",
        f"  执行成本 (可下单→成交): {fmt(report.mean_execution_bps)}",
        f"  费用:                   {fmt(report.mean_fee_bps)}",
        f"  合计 (均值):            {fmt(report.mean_total_bps)}",
        f"  合计 (中位数):          {fmt(report.median_total_bps)}",
        f"  最差单笔:               {fmt(report.worst_total_bps)}",
        "",
        "-- 买入后表现 --",
        f"  首日浮亏比例: {pct(report.immediate_loss_rate)}",
        f"  平均最大浮亏: {fmt(report.mean_mae_bps)}",
        f"  触及止损比例: {pct(report.stop_hit_rate)}",
    ]
    if report.noise_verdict:
        lines += ["", "-- 判定 --", f"  {report.noise_verdict.verdict}"]
    return "\n".join(lines)
