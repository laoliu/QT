"""滑点压力测试 — 不同滑点水平下策略表现"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_STRESS_BPS_LEVELS = (3.0, 5.0, 10.0, 15.0, 25.0, 40.0)


@dataclass(frozen=True)
class SlippagePoint:
    bps: float
    ann_ret: float
    sharpe: float


@dataclass(frozen=True)
class SlippageStressResult:
    points: tuple[SlippagePoint, ...]
    survival_bps: float | None
    realistic_bps: float
    return_at_realistic: float
    sharpe_at_realistic: float
    decay_rate: float
    score: float
    verdict: str

    def to_dict(self) -> dict:
        return {
            "survival_bps": self.survival_bps,
            "realistic_bps": self.realistic_bps,
            "return_at_realistic": round(self.return_at_realistic, 6),
            "sharpe_at_realistic": round(self.sharpe_at_realistic, 4),
            "decay_rate": round(self.decay_rate, 6),
            "score": round(self.score, 4),
            "verdict": self.verdict,
            "points": [{"bps": p.bps, "ret": round(p.ann_ret, 6), "sharpe": round(p.sharpe, 4)}
                       for p in self.points],
        }


def _find_survival_bps(pts: tuple[SlippagePoint, ...]) -> float | None:
    for p in pts:
        if p.ann_ret <= 0:
            return p.bps
    return None


def _interpolate(pts: tuple[SlippagePoint, ...], target_bps: float, attr: str = "ann_ret") -> float:
    if not pts:
        return 0.0
    if target_bps <= pts[0].bps:
        return getattr(pts[0], attr)
    if target_bps >= pts[-1].bps:
        return getattr(pts[-1], attr)
    for i in range(len(pts) - 1):
        if pts[i].bps <= target_bps <= pts[i + 1].bps:
            frac = (target_bps - pts[i].bps) / (pts[i + 1].bps - pts[i].bps)
            v0 = getattr(pts[i], attr)
            v1 = getattr(pts[i + 1], attr)
            return v0 + frac * (v1 - v0)
    return getattr(pts[-1], attr)


def _linear_decay_rate(pts: tuple[SlippagePoint, ...]) -> float:
    if len(pts) < 2:
        return 0.0
    x = [p.bps for p in pts]
    y = [p.ann_ret for p in pts]
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = sum((xi - mx) ** 2 for xi in x)
    return num / den if den > 0 else 0.0


def _compute_score(*, survival_bps: float | None, realistic_bps: float,
                   ret_at_real: float, sharpe_at_real: float, decay_rate: float) -> float:
    score = 0.0
    if survival_bps is not None:
        score += min(1, survival_bps / 40) * 0.3
    else:
        score += 0.3  # 从未跌到零
    if ret_at_real > 0:
        score += min(1, ret_at_real / 0.15) * 0.3
    if sharpe_at_real > 0:
        score += min(1, sharpe_at_real / 1.5) * 0.2
    # Decay rate penalty
    score += max(0, 1 + decay_rate * 10) * 0.2  # decay_rate usually negative
    return min(1.0, max(0.0, score))


def _make_verdict(score: float, survival_bps: float | None,
                  realistic_bps: float, ret_at_real: float) -> str:
    if score >= 0.8:
        return "优秀: 策略对滑点极不敏感"
    elif score >= 0.6:
        return "良好: 策略在合理滑点下仍可盈利"
    elif score >= 0.4:
        return f"一般: 在 {realistic_bps}bps 时年化={ret_at_real:.2%}"
    else:
        return "较差: 策略利润高度依赖低滑点假设"


def stress_test_slippage(
    points: Sequence[SlippagePoint],
    *,
    realistic_bps: float = 10.0,
) -> SlippageStressResult:
    """执行滑点压力测试

    Parameters
    ----------
    points : 不同滑点水平下的测试结果
    realistic_bps : 合理滑点预估 (bps)
    """
    pts = tuple(sorted(points, key=lambda p: p.bps))
    survival = _find_survival_bps(pts)
    ret_real = _interpolate(pts, realistic_bps, "ann_ret")
    sharpe_real = _interpolate(pts, realistic_bps, "sharpe")
    decay = _linear_decay_rate(pts)
    score = _compute_score(survival_bps=survival, realistic_bps=realistic_bps,
                           ret_at_real=ret_real, sharpe_at_real=sharpe_real, decay_rate=decay)
    verdict = _make_verdict(score, survival, realistic_bps, ret_real)

    return SlippageStressResult(
        points=pts, survival_bps=survival, realistic_bps=realistic_bps,
        return_at_realistic=ret_real, sharpe_at_realistic=sharpe_real,
        decay_rate=decay, score=score, verdict=verdict,
    )
