"""
滚动 IC 分析与分段因子评估

提供因子有效性的时间维度分析：
- Rolling IC: 滚动窗口内的因子IC变化趋势
- Segment IC: 分段(如新规前/后)的因子IC对比
- IC-Weighted Factor Selection: 基于滚动IC的动态因子权重

使用示例:
    analyzer = RollingICAnalyzer(factor_data, return_data)
    rolling = analyzer.compute_rolling_ic(window=60)
    segments = analyzer.compare_segments(cutoff_date)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np
from scipy import stats


@dataclass
class RollingICResult:
    """滚动IC分析结果"""

    factor_name: str
    window: int
    ic_series: dict[date, float]  # {date: rolling_ic}
    ic_mean: float
    ic_std: float
    ir: float
    trend_slope: float  # IC趋势线斜率(正=改善, 负=衰减)
    stability: float  # 1 - CV, 稳定性指标 [0, 1]

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "window": self.window,
            "ic_mean": round(self.ic_mean, 4),
            "ic_std": round(self.ic_std, 4),
            "ir": round(self.ir, 4),
            "trend_slope": round(self.trend_slope, 6),
            "stability": round(self.stability, 4),
            "n_points": len(self.ic_series),
        }


@dataclass
class SegmentICResult:
    """分段IC对比结果"""

    factor_name: str
    segment_name: str
    ic_mean: float
    ic_std: float
    ir: float
    n_days: int
    t_stat: float
    p_value: float

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "segment_name": self.segment_name,
            "ic_mean": round(self.ic_mean, 4),
            "ic_std": round(self.ic_std, 4),
            "ir": round(self.ir, 4),
            "n_days": self.n_days,
            "t_stat": round(self.t_stat, 4),
            "p_value": round(self.p_value, 4),
        }


@dataclass
class SegmentComparison:
    """分段对比汇总"""

    factor_name: str
    segments: list[SegmentICResult]
    ic_change: float  # 后段IC - 前段IC
    ic_ratio: float  # 后段IC / 前段IC (>1=改善)
    regime_robust: bool  # 后段IR > 0.3 且 IC同号

    def to_dict(self) -> dict:
        return {
            "factor_name": self.factor_name,
            "segments": [s.to_dict() for s in self.segments],
            "ic_change": round(self.ic_change, 4),
            "ic_ratio": round(self.ic_ratio, 4),
            "regime_robust": self.regime_robust,
        }


@dataclass
class AdaptiveWeights:
    """IC驱动的自适应因子权重"""

    weights: dict[str, float]  # {factor_name: weight}
    ic_values: dict[str, float]  # {factor_name: recent_ic}
    as_of_date: date | None = None

    def to_dict(self) -> dict:
        return {
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "ic_values": {k: round(v, 4) for k, v in self.ic_values.items()},
            "as_of_date": self.as_of_date.isoformat() if self.as_of_date else None,
        }


class RollingICAnalyzer:
    """滚动IC分析器

    支持:
    - compute_rolling_ic(): 滚动窗口IC序列
    - compare_segments(): 分段IC对比（新规前/后）
    - compute_adaptive_weights(): IC驱动的动态因子权重
    """

    def __init__(
        self,
        factor_data: dict[str, dict[date, dict[str, float]]],
        return_data: dict[date, dict[str, float]],
    ) -> None:
        """初始化

        Args:
            factor_data: 多因子数据 {factor_name: {date: {symbol: value}}}
            return_data: 收益数据 {date: {symbol: return}}
        """
        self.factor_data = factor_data
        self.return_data = return_data
        self._return_dates = sorted(return_data.keys())

    def _compute_daily_ic(
        self,
        factor_name: str,
        d: date,
        method: str = "spearman",
    ) -> float | None:
        """计算单日单因子IC"""
        factors = self.factor_data.get(factor_name, {}).get(d, {})
        returns = self.return_data.get(d, {})
        common = set(factors.keys()) & set(returns.keys())
        if len(common) < 5:
            return None
        fv = [factors[s] for s in common]
        rv = [returns[s] for s in common]
        if method == "spearman":
            corr, _ = stats.spearmanr(fv, rv)
        else:
            corr, _ = stats.pearsonr(fv, rv)
        return float(corr) if not np.isnan(corr) else None

    def compute_rolling_ic(
        self,
        factor_name: str,
        window: int = 60,
        method: str = "spearman",
    ) -> RollingICResult:
        """计算滚动窗口IC序列

        Args:
            factor_name: 因子名
            window: 滚动窗口大小（交易日）
            method: spearman 或 pearson

        Returns:
            RollingICResult
        """
        f_dates = sorted(self.factor_data.get(factor_name, {}).keys())
        all_dates = sorted(set(f_dates) & set(self._return_dates))

        # 先计算每日IC
        daily_ic: dict[date, float] = {}
        for d in all_dates:
            ic = self._compute_daily_ic(factor_name, d, method)
            if ic is not None:
                daily_ic[d] = ic

        sorted_dates = sorted(daily_ic.keys())
        rolling_ic: dict[date, float] = {}

        for i in range(window - 1, len(sorted_dates)):
            win_values = [daily_ic[sorted_dates[j]]
                          for j in range(i - window + 1, i + 1)]
            rolling_ic[sorted_dates[i]] = float(np.mean(win_values))

        if not rolling_ic:
            return RollingICResult(
                factor_name=factor_name, window=window,
                ic_series={}, ic_mean=0.0, ic_std=0.0,
                ir=0.0, trend_slope=0.0, stability=0.0,
            )

        vals = np.array(list(rolling_ic.values()))
        ic_mean = float(np.mean(vals))
        ic_std = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        ir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

        # 趋势线斜率
        x = np.arange(len(vals))
        slope, _, _, _, _ = stats.linregress(x, vals) if len(vals) > 2 else (0.0, 0, 0, 0, 0)

        # 稳定性 = 1 - CV (clipped to [0,1])
        cv = abs(ic_std / ic_mean) if abs(ic_mean) > 1e-10 else 999.0
        stability = max(0.0, min(1.0, 1.0 - cv))

        return RollingICResult(
            factor_name=factor_name, window=window,
            ic_series=rolling_ic, ic_mean=ic_mean, ic_std=ic_std,
            ir=ir, trend_slope=float(slope), stability=stability,
        )

    def _segment_ic(
        self,
        factor_name: str,
        start: date,
        end: date,
        segment_name: str,
        method: str = "spearman",
    ) -> SegmentICResult:
        """计算指定区间的IC统计"""
        f_data = self.factor_data.get(factor_name, {})
        dates_in = [d for d in sorted(f_data.keys())
                    if start <= d <= end and d in self.return_data]
        ic_vals: list[float] = []
        for d in dates_in:
            ic = self._compute_daily_ic(factor_name, d, method)
            if ic is not None:
                ic_vals.append(ic)
        n = len(ic_vals)
        if n == 0:
            return SegmentICResult(
                factor_name=factor_name, segment_name=segment_name,
                ic_mean=0.0, ic_std=0.0, ir=0.0, n_days=0,
                t_stat=0.0, p_value=1.0,
            )
        arr = np.array(ic_vals)
        ic_mean = float(np.mean(arr))
        ic_std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        ir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        t_stat = ic_mean / (ic_std / np.sqrt(n)) if ic_std > 0 and n > 1 else 0.0
        p_value = float(2 * (1 - stats.t.cdf(abs(t_stat), n - 1))) if n > 1 else 1.0
        return SegmentICResult(
            factor_name=factor_name, segment_name=segment_name,
            ic_mean=ic_mean, ic_std=ic_std, ir=ir,
            n_days=n, t_stat=float(t_stat), p_value=p_value,
        )

    def compare_segments(
        self,
        factor_name: str,
        cutoff_date: date,
        method: str = "spearman",
        pre_label: str = "pre",
        post_label: str = "post",
    ) -> SegmentComparison:
        """分段比较因子IC（如新规前 vs 新规后）

        Args:
            factor_name: 因子名称
            cutoff_date: 分段截断日 (该日归入post段)
            method: IC计算方法
            pre_label: 前段名称
            post_label: 后段名称

        Returns:
            SegmentComparison
        """
        f_data = self.factor_data.get(factor_name, {})
        all_dates = sorted(d for d in f_data if d in self.return_data)
        if not all_dates:
            empty_seg = SegmentICResult(
                factor_name=factor_name, segment_name=pre_label,
                ic_mean=0.0, ic_std=0.0, ir=0.0,
                n_days=0, t_stat=0.0, p_value=1.0,
            )
            return SegmentComparison(
                factor_name=factor_name,
                segments=[empty_seg, empty_seg],
                ic_change=0.0, ic_ratio=0.0, regime_robust=False,
            )

        pre_start = all_dates[0]
        from datetime import timedelta
        pre_end = cutoff_date - timedelta(days=1)
        post_end = all_dates[-1]

        seg_pre = self._segment_ic(factor_name, pre_start, pre_end,
                                   pre_label, method)
        seg_post = self._segment_ic(factor_name, cutoff_date, post_end,
                                    post_label, method)

        ic_change = seg_post.ic_mean - seg_pre.ic_mean
        ic_ratio = (seg_post.ic_mean / seg_pre.ic_mean
                    if abs(seg_pre.ic_mean) > 1e-10 else 0.0)

        # Regime robust: 后段IR > 0.3 且IC同号
        same_sign = (seg_pre.ic_mean * seg_post.ic_mean > 0
                     if seg_pre.n_days > 0 and seg_post.n_days > 0 else False)
        regime_robust = abs(seg_post.ir) > 0.3 and same_sign

        return SegmentComparison(
            factor_name=factor_name,
            segments=[seg_pre, seg_post],
            ic_change=ic_change,
            ic_ratio=ic_ratio,
            regime_robust=regime_robust,
        )

    def compute_adaptive_weights(
        self,
        factor_names: Sequence[str],
        base_weights: dict[str, float] | None = None,
        lookback: int = 60,
        ic_floor: float = 0.01,
        blend_ratio: float = 0.5,
        method: str = "spearman",
        as_of: date | None = None,
    ) -> AdaptiveWeights:
        """基于滚动IC计算自适应因子权重

        权重 = blend_ratio * ic_weight + (1 - blend_ratio) * base_weight
        ic_weight 由近 lookback 日的 abs(IC) 归一化得到。
        IC < ic_floor 的因子权重被抑制。

        Args:
            factor_names: 参与的因子名列表
            base_weights: 基线权重（None=等权）
            lookback: IC回看窗口
            ic_floor: IC低于此值的因子权重=0
            blend_ratio: IC权重与基线权重的混合比
            method: IC计算方法
            as_of: 计算截止日

        Returns:
            AdaptiveWeights
        """
        if base_weights is None:
            base_weights = {f: 1.0 / len(factor_names) for f in factor_names}

        target_date = as_of or (self._return_dates[-1] if self._return_dates
                                else None)
        if target_date is None:
            return AdaptiveWeights(
                weights=dict(base_weights), ic_values={}, as_of_date=None,
            )

        # 获取最近 lookback 天的每日IC
        ic_values: dict[str, float] = {}
        for fn in factor_names:
            f_dates = sorted(d for d in self.factor_data.get(fn, {})
                             if d <= target_date and d in self.return_data)
            recent = f_dates[-lookback:] if len(f_dates) >= lookback else f_dates
            ics: list[float] = []
            for d in recent:
                ic = self._compute_daily_ic(fn, d, method)
                if ic is not None:
                    ics.append(ic)
            ic_values[fn] = float(np.mean(ics)) if ics else 0.0

        # abs(IC)归一化为IC权重, IC < floor 的被抑制
        abs_ics = {fn: max(0.0, abs(ic) - ic_floor)
                   for fn, ic in ic_values.items()}
        total_abs = sum(abs_ics.values())
        if total_abs > 1e-10:
            ic_weights = {fn: v / total_abs for fn, v in abs_ics.items()}
        else:
            ic_weights = {fn: 1.0 / len(factor_names) for fn in factor_names}

        # 混合: blend * ic_weight + (1-blend) * base_weight
        final: dict[str, float] = {}
        for fn in factor_names:
            w = blend_ratio * ic_weights.get(fn, 0.0) + \
                (1.0 - blend_ratio) * base_weights.get(fn, 0.0)
            final[fn] = w

        # 归一化
        total = sum(final.values())
        if total > 1e-10:
            final = {fn: v / total for fn, v in final.items()}

        return AdaptiveWeights(
            weights=final, ic_values=ic_values, as_of_date=target_date,
        )
