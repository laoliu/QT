"""自动因子挖掘 — 遗传编程 + IC 筛选 + HLZ 校正。

使用 gplearn 遗传编程搜索因子表达式，经 IC/ICIR 筛选后
通过 HLZ 多重检验校正（原则 A）确保因子统计显著。

理论基础：
- 知识库 G3: 遗传编程因子挖掘 (DEAP/gplearn) → IC 筛选 → 去相关
- 知识库 J2: HLZ 校正 t_adj = t_obs - sqrt(2*ln(N))
- Harvey, Liu, Zhu (2016) — Cross-Section of Expected Returns

使用示例::

    miner = AutoFactorMiner(
        base_features=["close", "volume", "high", "low"],
        ic_threshold=0.02,
    )
    results = miner.mine(returns_df, features_df, n_generations=20)
    significant = miner.filter_hlz(results, n_total_trials=100)

与现有架构关系：
- 产出因子可注册到 FactorRegistry
- IC 评估使用 factor_analyzer.py 基础设施
- 去相关使用 factors/composite.py 正交化
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 可选依赖
try:
    from gplearn.genetic import SymbolicTransformer
    HAS_GPLEARN = True
except ImportError:
    HAS_GPLEARN = False


@dataclass
class MinedFactor:
    """挖掘出的因子"""
    expression: str
    ic_mean: float
    ic_std: float
    icir: float
    t_stat: float
    hlz_t_adj: float
    is_significant: bool
    rank_ic: float = 0.0
    turnover: float = 0.0

    def to_dict(self) -> dict:
        return {
            "expression": self.expression,
            "ic_mean": round(self.ic_mean, 6),
            "icir": round(self.icir, 4),
            "t_stat": round(self.t_stat, 4),
            "hlz_t_adj": round(self.hlz_t_adj, 4),
            "is_significant": self.is_significant,
        }


@dataclass
class MiningResult:
    """挖掘结果汇总"""
    total_explored: int = 0
    total_significant: int = 0
    factors: list[MinedFactor] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_explored": self.total_explored,
            "total_significant": self.total_significant,
            "factors": [f.to_dict() for f in self.factors],
        }


class AutoFactorMiner:
    """遗传编程自动因子挖掘器"""

    def __init__(
        self,
        base_features: Optional[list[str]] = None,
        ic_threshold: float = 0.02,
        icir_threshold: float = 0.5,
        hlz_t_threshold: float = 2.0,
        population_size: int = 500,
        generations: int = 20,
        tournament_size: int = 20,
        n_components: int = 30,
        random_state: int = 42,
    ) -> None:
        if not HAS_GPLEARN:
            raise ImportError("gplearn required. pip install gplearn")
        self.base_features = base_features or []
        self.ic_threshold = ic_threshold
        self.icir_threshold = icir_threshold
        self.hlz_t_threshold = hlz_t_threshold
        self.population_size = population_size
        self.generations = generations
        self.tournament_size = tournament_size
        self.n_components = n_components
        self.random_state = random_state

    def mine(
        self,
        returns: pd.Series | pd.DataFrame,
        features: pd.DataFrame,
        n_generations: Optional[int] = None,
    ) -> MiningResult:
        """执行遗传编程因子挖掘"""
        gens = n_generations or self.generations
        if isinstance(returns, pd.DataFrame):
            y = returns.iloc[:, 0].values
        else:
            y = returns.values
        X = features.values.astype(np.float64)
        # 处理 NaN
        mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        X_clean, y_clean = X[mask], y[mask]
        if len(X_clean) < 50:
            logger.warning("Too few valid rows (%d), skip mining", len(X_clean))
            return MiningResult()

        gp = SymbolicTransformer(
            population_size=self.population_size,
            hall_of_fame=min(100, self.population_size),
            generations=gens,
            tournament_size=min(self.tournament_size, self.population_size),
            n_components=min(self.n_components, self.population_size),
            function_set=["add", "sub", "mul", "div", "sqrt", "log", "abs"],
            metric="spearman",
            parsimony_coefficient=0.01,
            random_state=self.random_state,
            n_jobs=1,
        )
        gp.fit(X_clean, y_clean)
        transformed = gp.transform(X_clean)

        result = MiningResult(total_explored=len(gp._programs[-1]))
        for i in range(transformed.shape[1]):
            fv = transformed[:, i]
            if np.std(fv) < 1e-10:
                continue
            ic_mean, ic_std, icir = self._compute_ic(fv, y_clean)
            t_stat = ic_mean / ic_std if ic_std > 1e-10 else 0.0
            n_trials = result.total_explored
            hlz_adj = self._hlz_adjust(t_stat, max(n_trials, 1))
            expr = str(gp._best_programs[i]) if i < len(gp._best_programs) else f"gp_factor_{i}"
            mf = MinedFactor(
                expression=expr,
                ic_mean=ic_mean,
                ic_std=ic_std,
                icir=icir,
                t_stat=t_stat,
                hlz_t_adj=hlz_adj,
                is_significant=(
                    abs(ic_mean) >= self.ic_threshold
                    and hlz_adj > self.hlz_t_threshold
                ),
            )
            result.factors.append(mf)

        result.total_significant = sum(
            1 for f in result.factors if f.is_significant
        )
        logger.info(
            "Mining complete: %d explored, %d significant",
            result.total_explored, result.total_significant,
        )
        return result

    def filter_hlz(
        self,
        result: MiningResult,
        n_total_trials: Optional[int] = None,
    ) -> list[MinedFactor]:
        """HLZ 多重检验校正筛选"""
        n = n_total_trials or result.total_explored
        out = []
        for f in result.factors:
            adj = self._hlz_adjust(f.t_stat, max(n, 1))
            f.hlz_t_adj = adj
            f.is_significant = adj > self.hlz_t_threshold
            if f.is_significant:
                out.append(f)
        return self._decorrelate(out)

    def _compute_ic(
        self, factor_values: np.ndarray, returns: np.ndarray,
    ) -> tuple[float, float, float]:
        """计算 Spearman IC / IC_std / ICIR"""
        from scipy.stats import spearmanr
        # 简化：全截面一次性计算
        valid = ~(np.isnan(factor_values) | np.isnan(returns))
        fv, rv = factor_values[valid], returns[valid]
        if len(fv) < 10:
            return 0.0, 1.0, 0.0
        corr, _ = spearmanr(fv, rv)
        ic_mean = float(corr) if np.isfinite(corr) else 0.0
        # 用 bootstrap 估 IC std
        rng = np.random.RandomState(self.random_state)
        ics = []
        for _ in range(50):
            idx = rng.choice(len(fv), size=len(fv), replace=True)
            c, _ = spearmanr(fv[idx], rv[idx])
            if np.isfinite(c):
                ics.append(c)
        ic_std = float(np.std(ics)) if ics else 1.0
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        return ic_mean, ic_std, icir

    def _hlz_adjust(
        self, t_stat: float, n_trials: int,
    ) -> float:
        """HLZ 校正: t_adj = t_obs - sqrt(2 * ln(N))"""
        if n_trials <= 1:
            return t_stat
        penalty = np.sqrt(2.0 * np.log(n_trials))
        return t_stat - penalty

    def _decorrelate(
        self, factors: list[MinedFactor], corr_threshold: float = 0.7,
    ) -> list[MinedFactor]:
        """去相关：保留 ICIR 最高的非冗余因子"""
        if len(factors) <= 1:
            return factors
        # 按 |ICIR| 降序
        sorted_f = sorted(factors, key=lambda f: abs(f.icir), reverse=True)
        selected: list[MinedFactor] = [sorted_f[0]]
        for f in sorted_f[1:]:
            # 简单去相关：IC 值接近的视为冗余
            is_redundant = False
            for s in selected:
                if abs(f.ic_mean - s.ic_mean) < 0.005:
                    is_redundant = True
                    break
            if not is_redundant:
                selected.append(f)
        return selected
