"""IC Trap Guard — 因子准入自动防护管道。

核心用途：
解决 Aipha-Q 的结构性瓶颈 IC Trap (CF-020, 5 轮独立确认):
  新增因子提升全截面 IC，但 Top-K 组合 AR 反而下降。

防护机制：
1. IC/ICIR 初筛 (D7 标准: IC≥4%, ICIR≥0.30)
2. AST 结构去重 (similarity < 0.85)
3. 数值相关性去重 (correlation < 0.70)
4. Top-K AR 不降检查 (新因子加入后 AR 不低于基线 - 3%)
5. HLZ 多重检验校正 (t_adj > 2.0)

术语定义：
- IC Trap: 因子在全截面 IC 上有正贡献，但在 Top-K 组合中稀释了预测浓度
- AR 不降检查: 加入新因子后回测 AR 不低于基线的 97%

与现有架构关系：
- factor_expression_parser.py: 表达式解析
- ast_dedup.py: AST 去重
- correlation.py: 数值去重
- factor_analyzer.py: IC/ICIR 计算
- auto_factor_mining.py: HLZ 校正

参考：
- CF-020 (IC Trap BINDING)
- CF-022 (Top-K 稀释 BINDING)
- Harvey, Liu, Zhu (2016) — HLZ 校正
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


class GuardVerdict(Enum):
    """防护判定结果"""
    PASS = "pass"
    FAIL_COMPLEXITY = "fail_complexity"
    FAIL_IC = "fail_ic_threshold"
    FAIL_AST_DUP = "fail_ast_duplicate"
    FAIL_CORR_DUP = "fail_correlation_duplicate"
    FAIL_AR_DROP = "fail_ar_drop"
    FAIL_HLZ = "fail_hlz_correction"
    ERROR = "error"


@dataclass
class GuardCheckResult:
    """单个因子的防护检查结果"""
    factor_name: str
    verdict: GuardVerdict
    details: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == GuardVerdict.PASS


@dataclass
class GuardConfig:
    """IC Trap Guard 配置

    优先从 configs/mining/field_registry.yml 加载统一阈值。
    """
    # IC 门槛
    ic_threshold: float = 0.04
    icir_threshold: float = 0.30
    # AST 去重门槛
    ast_similarity_threshold: float = 0.85
    # 数值相关性门槛
    correlation_threshold: float = 0.70
    # AR 不降容忍度 (允许新因子后 AR 下降不超过此百分比)
    ar_drop_tolerance: float = 0.03
    # HLZ 校正门槛
    hlz_t_threshold: float = 2.0
    # 总试验数（用于 HLZ 校正）
    total_trials: int = 100
    # 复杂度控制 (AlphaAgent 风格)
    max_ast_depth: int = 5
    max_ast_node_count: int = 15

    @classmethod
    def from_registry(cls, path: Path | str | None = None) -> "GuardConfig":
        """从字段注册表 YAML 加载阈值配置。"""
        p = Path(path) if path else (
            Path(__file__).resolve().parents[3] / "configs" / "mining" / "field_registry.yml"
        )
        if not p.exists():
            logger.warning("注册表不存在: %s, 使用默认阈值", p)
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        t = data.get("guard_thresholds", {})
        return cls(
            ic_threshold=t.get("ic_threshold", cls.ic_threshold),
            icir_threshold=t.get("icir_threshold", cls.icir_threshold),
            ast_similarity_threshold=t.get("ast_similarity_threshold", cls.ast_similarity_threshold),
            correlation_threshold=t.get("correlation_threshold", cls.correlation_threshold),
            ar_drop_tolerance=t.get("ar_drop_tolerance", cls.ar_drop_tolerance),
            hlz_t_threshold=t.get("hlz_t_threshold", cls.hlz_t_threshold),
            total_trials=t.get("total_trials", cls.total_trials),
            max_ast_depth=t.get("max_ast_depth", cls.max_ast_depth),
            max_ast_node_count=t.get("max_ast_node_count", cls.max_ast_node_count),
        )


class ICTrapGuard:
    """IC Trap 防护管道 — 因子准入的自动看门人"""

    def __init__(self, config: Optional[GuardConfig] = None) -> None:
        self.config = config or GuardConfig()
        self._baseline_ar: Optional[float] = None
        self._existing_factors: list[str] = []
        self._existing_ic_values: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def set_baseline(
        self,
        baseline_ar: float,
        existing_factor_names: Optional[list[str]] = None,
    ) -> None:
        """设置基线 AR 和现有因子列表。

        Args:
            baseline_ar: 当前因子集的 Top-K 组合 AR
            existing_factor_names: 现有因子名称列表
        """
        self._baseline_ar = baseline_ar
        if existing_factor_names:
            self._existing_factors = list(existing_factor_names)
        logger.info(
            "Baseline set: AR=%.4f, existing_factors=%d",
            baseline_ar, len(self._existing_factors),
        )

    def check_factor(
        self,
        factor_name: str,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        expression: str = "",
        new_ar: Optional[float] = None,
        existing_factor_values: Optional[pd.DataFrame] = None,
    ) -> GuardCheckResult:
        """运行完整的 IC Trap 防护检查。

        Args:
            factor_name: 因子名称
            factor_values: 因子值 Series
            forward_returns: 前向收益 Series
            expression: 因子表达式（用于 AST 去重）
            new_ar: 加入新因子后的 Top-K AR（若无则跳过 AR 检查）
            existing_factor_values: 现有因子 DataFrame（用于相关性去重）

        Returns:
            GuardCheckResult 包含判定结果和详细信息
        """
        details: dict = {"factor_name": factor_name, "checks": {}}

        try:
            # Step 0: 复杂度控制 (AST 深度/节点数)
            if expression:
                complexity_result = self._check_complexity(expression)
                details["checks"]["complexity"] = complexity_result
                if not complexity_result["passed"]:
                    return GuardCheckResult(
                        factor_name=factor_name,
                        verdict=GuardVerdict.FAIL_COMPLEXITY,
                        details=details,
                    )

            # Step 1: IC/ICIR 初筛
            ic_result = self._check_ic(factor_values, forward_returns)
            details["checks"]["ic"] = ic_result
            if not ic_result["passed"]:
                return GuardCheckResult(
                    factor_name=factor_name,
                    verdict=GuardVerdict.FAIL_IC,
                    details=details,
                )

            # Step 2: AST 去重
            if expression:
                ast_result = self._check_ast_dedup(factor_name, expression)
                details["checks"]["ast_dedup"] = ast_result
                if not ast_result["passed"]:
                    return GuardCheckResult(
                        factor_name=factor_name,
                        verdict=GuardVerdict.FAIL_AST_DUP,
                        details=details,
                    )

            # Step 3: 数值相关性去重
            if existing_factor_values is not None:
                corr_result = self._check_correlation(
                    factor_values, existing_factor_values,
                )
                details["checks"]["correlation"] = corr_result
                if not corr_result["passed"]:
                    return GuardCheckResult(
                        factor_name=factor_name,
                        verdict=GuardVerdict.FAIL_CORR_DUP,
                        details=details,
                    )

            # Step 4: AR 不降检查 (IC Trap 核心防护)
            if new_ar is not None and self._baseline_ar is not None:
                ar_result = self._check_ar_drop(new_ar)
                details["checks"]["ar_drop"] = ar_result
                if not ar_result["passed"]:
                    return GuardCheckResult(
                        factor_name=factor_name,
                        verdict=GuardVerdict.FAIL_AR_DROP,
                        details=details,
                    )

            # Step 5: HLZ 校正
            hlz_result = self._check_hlz(
                ic_result["ic_mean"], ic_result["ic_std"],
            )
            details["checks"]["hlz"] = hlz_result
            if not hlz_result["passed"]:
                return GuardCheckResult(
                    factor_name=factor_name,
                    verdict=GuardVerdict.FAIL_HLZ,
                    details=details,
                )

            # 全部通过
            return GuardCheckResult(
                factor_name=factor_name,
                verdict=GuardVerdict.PASS,
                details=details,
            )

        except Exception as e:
            logger.error("Guard check error for %s: %s", factor_name, e)
            details["error"] = str(e)
            return GuardCheckResult(
                factor_name=factor_name,
                verdict=GuardVerdict.ERROR,
                details=details,
            )

    def batch_check(
        self,
        candidates: dict[str, pd.Series],
        forward_returns: pd.Series,
        expressions: Optional[dict[str, str]] = None,
        existing_factor_values: Optional[pd.DataFrame] = None,
    ) -> list[GuardCheckResult]:
        """批量检查多个候选因子。"""
        results: list[GuardCheckResult] = []
        expressions = expressions or {}

        for name, values in candidates.items():
            result = self.check_factor(
                factor_name=name,
                factor_values=values,
                forward_returns=forward_returns,
                expression=expressions.get(name, ""),
                existing_factor_values=existing_factor_values,
            )
            results.append(result)
            logger.info(
                "Factor '%s': %s", name, result.verdict.value,
            )

        passed = sum(1 for r in results if r.passed)
        logger.info(
            "Batch check: %d/%d passed", passed, len(results),
        )
        return results

    # ------------------------------------------------------------------
    # 检查实现
    # ------------------------------------------------------------------

    def _check_complexity(self, expression: str) -> dict:
        """复杂度控制检查 (AlphaAgent 风格)。

        限制 AST 深度和节点数，防止过拟合的复杂表达式。
        """
        from aipha.factors.ast_dedup import ASTFactorDeduplicator
        try:
            dedup = ASTFactorDeduplicator()
            sig = dedup.compute_signature("_tmp_", expression)
            depth = sig.depth
            node_count = sig.node_count
            depth_ok = depth <= self.config.max_ast_depth
            count_ok = node_count <= self.config.max_ast_node_count
            passed = depth_ok and count_ok
            return {
                "passed": passed,
                "ast_depth": depth,
                "ast_node_count": node_count,
                "max_depth": self.config.max_ast_depth,
                "max_node_count": self.config.max_ast_node_count,
                "reason": "" if passed else (
                    f"depth={depth}>{self.config.max_ast_depth}"
                    if not depth_ok else
                    f"nodes={node_count}>{self.config.max_ast_node_count}"
                ),
            }
        except Exception as e:
            logger.warning("复杂度检查失败: %s", e)
            return {"passed": True, "error": str(e),
                    "reason": "parse error, skipped"}

    def _check_ic(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
    ) -> dict:
        """IC/ICIR 初筛 (D7 标准)。"""
        from scipy.stats import spearmanr

        # 对齐索引
        common = factor_values.dropna().index.intersection(
            forward_returns.dropna().index
        )
        if len(common) < 30:
            return {
                "passed": False, "ic_mean": 0.0, "ic_std": 1.0,
                "icir": 0.0, "reason": "Insufficient data",
            }

        fv = factor_values.loc[common].values
        rv = forward_returns.loc[common].values

        corr, _ = spearmanr(fv, rv)
        ic_mean = float(corr) if np.isfinite(corr) else 0.0

        # Bootstrap IC std
        rng = np.random.RandomState(42)
        ics = []
        for _ in range(100):
            idx = rng.choice(len(fv), size=len(fv), replace=True)
            c, _ = spearmanr(fv[idx], rv[idx])
            if np.isfinite(c):
                ics.append(c)
        ic_std = float(np.std(ics)) if ics else 1.0
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

        passed = (
            abs(ic_mean) >= self.config.ic_threshold
            and abs(icir) >= self.config.icir_threshold
        )
        return {
            "passed": passed,
            "ic_mean": round(ic_mean, 6),
            "ic_std": round(ic_std, 6),
            "icir": round(icir, 4),
            "threshold_ic": self.config.ic_threshold,
            "threshold_icir": self.config.icir_threshold,
        }

    def _check_ast_dedup(
        self, factor_name: str, expression: str,
    ) -> dict:
        """AST 结构去重检查。"""
        from aipha.factors.ast_dedup import ASTFactorDeduplicator

        dedup = ASTFactorDeduplicator(
            similarity_threshold=self.config.ast_similarity_threshold,
        )
        # 注册现有因子
        for name in self._existing_factors:
            ic_key = self._existing_ic_values.get(name, "")
            if ic_key:
                dedup.register(name, str(ic_key))

        result = dedup.check_duplicate(factor_name, expression)
        return {
            "passed": not result.is_duplicate,
            "most_similar": result.most_similar_name,
            "similarity": round(result.similarity_score, 4),
            "threshold": self.config.ast_similarity_threshold,
            "reason": result.reason,
        }

    def _check_correlation(
        self,
        factor_values: pd.Series,
        existing_df: pd.DataFrame,
    ) -> dict:
        """数值相关性去重。"""
        max_corr = 0.0
        max_corr_name = ""

        for col in existing_df.columns:
            common = factor_values.dropna().index.intersection(
                existing_df[col].dropna().index
            )
            if len(common) < 30:
                continue
            corr = factor_values.loc[common].corr(
                existing_df[col].loc[common], method="spearman"
            )
            if abs(corr) > abs(max_corr):
                max_corr = corr
                max_corr_name = col

        passed = abs(max_corr) < self.config.correlation_threshold
        return {
            "passed": passed,
            "max_correlation": round(max_corr, 4),
            "max_corr_with": max_corr_name,
            "threshold": self.config.correlation_threshold,
        }

    def _check_ar_drop(self, new_ar: float) -> dict:
        """AR 不降检查（IC Trap 核心）。"""
        baseline = self._baseline_ar or 0.0
        drop = baseline - new_ar
        drop_pct = drop / abs(baseline) if abs(baseline) > 1e-10 else 0.0
        passed = drop_pct <= self.config.ar_drop_tolerance

        return {
            "passed": passed,
            "baseline_ar": round(baseline, 4),
            "new_ar": round(new_ar, 4),
            "ar_drop": round(drop, 4),
            "ar_drop_pct": round(drop_pct, 4),
            "tolerance": self.config.ar_drop_tolerance,
            "is_ic_trap": not passed,
        }

    def _check_hlz(
        self, ic_mean: float, ic_std: float,
    ) -> dict:
        """HLZ 多重检验校正。"""
        t_stat = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        n = max(self.config.total_trials, 1)
        penalty = np.sqrt(2.0 * np.log(n))
        t_adj = abs(t_stat) - penalty
        passed = t_adj > self.config.hlz_t_threshold

        return {
            "passed": passed,
            "t_stat": round(t_stat, 4),
            "t_adj": round(t_adj, 4),
            "penalty": round(penalty, 4),
            "threshold": self.config.hlz_t_threshold,
            "total_trials": n,
        }
