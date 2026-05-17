"""滑点压力测试工具。

在多个滑点水平下评估策略回测结果的衰减情况，
判断策略收益是否对滑点假设过度敏感。

主要功能：
- ``stress_test_slippage()``: 在指定 bps 序列下计算收益/Sharpe 衰减
- ``SlippageStressResult``: 结构化结果（含衰减曲线、存活线、结论）

设计：
- 纯分析工具，不直接调用回测引擎（避免循环依赖）
- 输入为 ``(slippage_bps, annualized_return, sharpe)`` 三元组序列
- 与 alpha_authenticity.py 配合：其 ``SlippageRobustnessCheck`` 可用本模块结果填充

典型用法::

    results = [
        SlippagePoint(bps=3,  ann_ret=0.25, sharpe=1.2),
        SlippagePoint(bps=10, ann_ret=0.18, sharpe=0.9),
        SlippagePoint(bps=15, ann_ret=0.10, sharpe=0.5),
        SlippagePoint(bps=25, ann_ret=-0.05, sharpe=-0.2),
    ]
    report = stress_test_slippage(results, realistic_bps=15.0)
    print(f"存活线: {report.survival_bps}bps  评分: {report.score}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================


@dataclass(frozen=True, slots=True)
class SlippagePoint:
    """单个滑点水平下的回测结果。

    Attributes:
        bps: 滑点设置（基点）
        ann_ret: 年化收益率（decimal，如 0.25 = 25%）
        sharpe: Sharpe ratio
    """

    bps: float
    ann_ret: float
    sharpe: float = 0.0


@dataclass(frozen=True, slots=True)
class SlippageStressResult:
    """滑点压力测试结果。

    Attributes:
        points: 按 bps 排序的评估点
        survival_bps: 收益仍为正的最大滑点（None = 任何滑点下都为负）
        realistic_bps: 用户指定的"合理"滑点水平
        return_at_realistic: 在合理滑点下的年化收益（插值或最近值）
        sharpe_at_realistic: 在合理滑点下的 Sharpe（插值或最近值）
        decay_rate: 每增加 1bps，年化收益下降多少（线性近似）
        score: 0-10 评分（越高越鲁棒）
        verdict: 结论字符串
    """

    points: tuple[SlippagePoint, ...]
    survival_bps: float | None
    realistic_bps: float
    return_at_realistic: float
    sharpe_at_realistic: float
    decay_rate: float
    score: float
    verdict: str

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "points": [
                {"bps": p.bps, "ann_ret": p.ann_ret, "sharpe": p.sharpe}
                for p in self.points
            ],
            "survival_bps": self.survival_bps,
            "realistic_bps": self.realistic_bps,
            "return_at_realistic": round(self.return_at_realistic, 4),
            "sharpe_at_realistic": round(self.sharpe_at_realistic, 4),
            "decay_rate": round(self.decay_rate, 6),
            "score": round(self.score, 2),
            "verdict": self.verdict,
        }


# ============================================================================
# A 股滑点测试序列
# ============================================================================

# 标准测试序列（bps）：覆盖从理想到极端
DEFAULT_STRESS_BPS_LEVELS = (3.0, 5.0, 10.0, 15.0, 25.0, 40.0)


# ============================================================================
# 核心函数
# ============================================================================


def stress_test_slippage(
    points: Sequence[SlippagePoint],
    *,
    realistic_bps: float = 15.0,
) -> SlippageStressResult:
    """在多个滑点水平下评估策略鲁棒性。

    Args:
        points: 各滑点水平下的回测结果（至少 2 个点）
        realistic_bps: "合理"滑点水平，用于计算存活评分

    Returns:
        SlippageStressResult 包含衰减曲线、存活线、评分
    """
    if len(points) < 2:
        return SlippageStressResult(
            points=tuple(sorted(points, key=lambda p: p.bps)),
            survival_bps=None,
            realistic_bps=realistic_bps,
            return_at_realistic=0.0,
            sharpe_at_realistic=0.0,
            decay_rate=0.0,
            score=0.0,
            verdict="数据不足（需 ≥ 2 个滑点水平）",
        )

    sorted_pts = tuple(sorted(points, key=lambda p: p.bps))

    # 1) 存活线：收益仍为正的最大 bps
    survival_bps = _find_survival_bps(sorted_pts)

    # 2) 在 realistic_bps 处插值
    ret_at_real = _interpolate(sorted_pts, realistic_bps, attr="ann_ret")
    sharpe_at_real = _interpolate(sorted_pts, realistic_bps, attr="sharpe")

    # 3) 衰减率（线性回归斜率：ann_ret vs bps）
    decay_rate = _linear_decay_rate(sorted_pts)

    # 4) 评分
    score = _compute_score(
        survival_bps=survival_bps,
        realistic_bps=realistic_bps,
        ret_at_real=ret_at_real,
        sharpe_at_real=sharpe_at_real,
        decay_rate=decay_rate,
    )

    # 5) 结论
    verdict = _make_verdict(score, survival_bps, realistic_bps, ret_at_real)

    return SlippageStressResult(
        points=sorted_pts,
        survival_bps=survival_bps,
        realistic_bps=realistic_bps,
        return_at_realistic=ret_at_real,
        sharpe_at_realistic=sharpe_at_real,
        decay_rate=decay_rate,
        score=score,
        verdict=verdict,
    )


# ============================================================================
# 辅助函数
# ============================================================================


def _find_survival_bps(pts: tuple[SlippagePoint, ...]) -> float | None:
    """找到收益仍为正的最大滑点水平。

    如果最低 bps 处收益就为负，返回 None。
    如果存在相邻正→负的转折，用线性插值逼近零点。
    """
    last_positive: float | None = None

    for p in pts:
        if p.ann_ret > 0:
            last_positive = p.bps
        else:
            # 在此点收益已为负，尝试零点插值
            if last_positive is not None:
                # 找上一个正收益点
                prev = next(pp for pp in pts if pp.bps == last_positive)
                # 线性插值零点
                denom = prev.ann_ret - p.ann_ret
                if denom > 0:
                    zero_bps = prev.bps + prev.ann_ret * (p.bps - prev.bps) / denom
                    return round(zero_bps, 2)
            return last_positive

    return last_positive


def _interpolate(
    pts: tuple[SlippagePoint, ...],
    target_bps: float,
    *,
    attr: str = "ann_ret",
) -> float:
    """在排序后的点序列中线性插值。"""
    if not pts:
        return 0.0

    # 边界情况
    if target_bps <= pts[0].bps:
        return getattr(pts[0], attr)
    if target_bps >= pts[-1].bps:
        return getattr(pts[-1], attr)

    # 找到包含 target_bps 的区间
    for i in range(len(pts) - 1):
        if pts[i].bps <= target_bps <= pts[i + 1].bps:
            frac = (target_bps - pts[i].bps) / (pts[i + 1].bps - pts[i].bps)
            v0 = getattr(pts[i], attr)
            v1 = getattr(pts[i + 1], attr)
            return v0 + frac * (v1 - v0)

    return getattr(pts[-1], attr)


def _linear_decay_rate(pts: tuple[SlippagePoint, ...]) -> float:
    """线性回归斜率：每增加 1 bps，年化收益下降多少。

    返回负值表示收益随滑点增加而下降（正常情况）。
    """
    n = len(pts)
    if n < 2:
        return 0.0

    x = [p.bps for p in pts]
    y = [p.ann_ret for p in pts]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    ss_xx = sum((xi - mean_x) ** 2 for xi in x)

    if ss_xx == 0:
        return 0.0

    return ss_xy / ss_xx


def _compute_score(
    *,
    survival_bps: float | None,
    realistic_bps: float,
    ret_at_real: float,
    sharpe_at_real: float,
    decay_rate: float,
) -> float:
    """滑点压力评分 0~10。

    评分维度：
    - 存活线 vs 合理线 (0~4)
    - 合理滑点下的收益 (0~3)
    - 合理滑点下的 Sharpe (0~3)
    """
    score = 0.0

    # 1) 存活线评分 (0~4)
    if survival_bps is None:
        # 最低 bps 就为负
        score += 0.0
    elif survival_bps >= realistic_bps * 2.0:
        score += 4.0
    elif survival_bps >= realistic_bps:
        score += 2.0 + 2.0 * (survival_bps - realistic_bps) / realistic_bps
    else:
        score += 2.0 * survival_bps / realistic_bps if realistic_bps > 0 else 0.0

    # 2) 合理滑点下收益 (0~3)
    if ret_at_real >= 0.15:
        score += 3.0
    elif ret_at_real >= 0.05:
        score += 1.5 + 1.5 * (ret_at_real - 0.05) / 0.10
    elif ret_at_real > 0:
        score += 1.5 * (ret_at_real / 0.05)
    # else: 0

    # 3) Sharpe (0~3)
    if sharpe_at_real >= 1.0:
        score += 3.0
    elif sharpe_at_real >= 0.5:
        score += 1.5 + 1.5 * (sharpe_at_real - 0.5) / 0.5
    elif sharpe_at_real > 0:
        score += 1.5 * (sharpe_at_real / 0.5)

    return min(10.0, max(0.0, score))


def _make_verdict(
    score: float,
    survival_bps: float | None,
    realistic_bps: float,
    ret_at_real: float,
) -> str:
    """生成人类可读的结论。"""
    if score >= 7.0:
        return "滑点鲁棒: 策略在合理滑点下仍有可观收益"
    elif score >= 4.0:
        if survival_bps is not None and survival_bps >= realistic_bps:
            return (
                f"基本鲁棒: 在 {realistic_bps:.0f}bps 下年化 "
                f"{ret_at_real:.1%}，但存活线 {survival_bps:.0f}bps 偏低"
            )
        return f"部分鲁棒: 在 {realistic_bps:.0f}bps 下年化 {ret_at_real:.1%}，需关注"
    else:
        return (
            f"滑点脆弱: 在 {realistic_bps:.0f}bps 下"
            f"{'为负' if ret_at_real <= 0 else f'仅 {ret_at_real:.1%}'}，"
            f"策略收益可能不真实"
        )
