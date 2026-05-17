"""统一组合分析模块（D1-2）

为 Tier S / Tier L 提供标准化的组合层分析 API，覆盖：
- 策略间相关性矩阵（Pearson 日收益相关）
- Euler 风险贡献分解
- 持仓 Jaccard 相似度
- 滑点灵敏度测试
- 容量估计汇总

输出结构化 ComboAnalysisResult，可直接用于 P11 报告填充。

使用示例::

    from aipha.reporting.combo_analyzer import ComboAnalyzer

    analyzer = ComboAnalyzer()
    result = analyzer.analyze(
        daily_returns={"cb_leg": cb_series, "ml_leg": ml_series},
        weights={"cb_leg": 0.4, "ml_leg": 0.6},
    )
    print(result.max_pair_corr)
    print(result.risk_contributions)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ComboAnalysisResult:
    """组合分析结果。"""

    # 相关性
    correlation_matrix: pd.DataFrame
    max_pair_corr: float
    avg_pair_corr: float

    # 风险贡献（Euler 分解）
    risk_contributions: dict[str, float]  # name → RC 比例 (sum=1.0)
    max_risk_contribution: float
    max_rc_name: str

    # 组合风险指标
    portfolio_vol: float
    cvar_95: float

    # 分散化检验通过/失败
    diversification_pass: bool
    diversification_failures: list[str] = field(default_factory=list)

    # 持仓 Jaccard（可选，需 positions 数据）
    jaccard_matrix: pd.DataFrame | None = None
    max_jaccard: float = 0.0

    # 容量（可选）
    capacity_aum: float | None = None
    bottleneck_symbol: str | None = None

    # 滑点灵敏度（可选）
    slippage_results: dict[str, dict[str, float]] | None = None


class ComboAnalyzer:
    """统一组合分析器。"""

    # P11 / 原则 E 的硬约束阈值
    MAX_PAIR_CORR = 0.75
    AVG_PAIR_CORR = 0.60
    MAX_RISK_CONTRIBUTION = 0.50
    MAX_JACCARD = 0.30
    MAX_CVAR_95 = 0.03

    def analyze(
        self,
        daily_returns: dict[str, pd.Series],
        weights: dict[str, float] | None = None,
        positions_history: dict[str, list[set[str]]] | None = None,
    ) -> ComboAnalysisResult:
        """执行完整组合分析。

        Args:
            daily_returns: 策略名 → 日收益率 Series（index 为日期）
            weights: 策略名 → 组合权重（默认等权）
            positions_history: 策略名 → 每期持仓代码集合列表（用于 Jaccard）

        Returns:
            ComboAnalysisResult 包含所有分析指标
        """
        names = list(daily_returns.keys())
        n = len(names)

        if n < 2:
            raise ValueError("组合分析至少需要 2 个策略")

        # 默认等权
        if weights is None:
            weights = {name: 1.0 / n for name in names}

        # 对齐日期
        df = pd.DataFrame(daily_returns)
        df = df.dropna(how="all").fillna(0.0)

        # 1. 相关性矩阵
        corr_matrix = df.corr()
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append(corr_matrix.iloc[i, j])
        max_pair_corr = max(pairs) if pairs else 0.0
        avg_pair_corr = float(np.mean(pairs)) if pairs else 0.0

        # 2. Euler 风险贡献
        w = np.array([weights[name] for name in names])
        w = w / w.sum()  # 归一化
        cov = df.cov().values * 252  # 年化协方差

        sigma_w = cov @ w
        port_var = float(w @ sigma_w)
        port_vol = float(np.sqrt(max(port_var, 1e-12)))

        # RC_i = w_i * (Sigma @ w)_i / portfolio_variance
        rc_raw = w * sigma_w
        rc_sum = rc_raw.sum()
        if abs(rc_sum) > 1e-12:
            rc_pct = rc_raw / rc_sum
        else:
            rc_pct = np.ones(n) / n

        risk_contributions = {names[i]: float(rc_pct[i]) for i in range(n)}
        max_rc = float(rc_pct.max())
        max_rc_name = names[int(np.argmax(rc_pct))]

        # 3. CVaR(95%) - 组合层日收益
        port_returns = df.values @ w
        sorted_returns = np.sort(port_returns)
        cutoff = int(len(sorted_returns) * 0.05)
        if cutoff > 0:
            cvar_95 = float(-sorted_returns[:cutoff].mean())
        else:
            cvar_95 = float(-sorted_returns[0]) if len(sorted_returns) > 0 else 0.0

        # 4. Jaccard（若提供持仓数据）
        jaccard_matrix = None
        max_jaccard = 0.0
        if positions_history and len(positions_history) >= 2:
            jaccard_matrix, max_jaccard = self._compute_jaccard(
                names, positions_history
            )

        # 5. 分散化检验
        failures: list[str] = []
        if max_pair_corr > self.MAX_PAIR_CORR:
            failures.append(
                f"max_pair_corr={max_pair_corr:.3f} > {self.MAX_PAIR_CORR}"
            )
        if avg_pair_corr > self.AVG_PAIR_CORR:
            failures.append(
                f"avg_pair_corr={avg_pair_corr:.3f} > {self.AVG_PAIR_CORR}"
            )
        if max_rc > self.MAX_RISK_CONTRIBUTION:
            failures.append(
                f"max_rc({max_rc_name})={max_rc:.3f} > {self.MAX_RISK_CONTRIBUTION}"
            )
        if cvar_95 > self.MAX_CVAR_95:
            failures.append(
                f"cvar_95={cvar_95:.4f} > {self.MAX_CVAR_95}"
            )
        if max_jaccard > self.MAX_JACCARD:
            failures.append(
                f"max_jaccard={max_jaccard:.3f} > {self.MAX_JACCARD}"
            )

        return ComboAnalysisResult(
            correlation_matrix=corr_matrix,
            max_pair_corr=max_pair_corr,
            avg_pair_corr=avg_pair_corr,
            risk_contributions=risk_contributions,
            max_risk_contribution=max_rc,
            max_rc_name=max_rc_name,
            portfolio_vol=port_vol,
            cvar_95=cvar_95,
            diversification_pass=len(failures) == 0,
            diversification_failures=failures,
            jaccard_matrix=jaccard_matrix,
            max_jaccard=max_jaccard,
        )

    def _compute_jaccard(
        self,
        names: list[str],
        positions_history: dict[str, list[set[str]]],
    ) -> tuple[pd.DataFrame, float]:
        """计算策略间平均 Jaccard 相似度。"""
        n = len(names)
        jac = np.zeros((n, n))

        for i in range(n):
            for j in range(i + 1, n):
                pos_i = positions_history.get(names[i], [])
                pos_j = positions_history.get(names[j], [])
                min_len = min(len(pos_i), len(pos_j))
                if min_len == 0:
                    continue
                jaccard_vals = []
                for k in range(min_len):
                    si, sj = pos_i[k], pos_j[k]
                    union = si | sj
                    if union:
                        jaccard_vals.append(len(si & sj) / len(union))
                if jaccard_vals:
                    avg_j = float(np.mean(jaccard_vals))
                    jac[i, j] = avg_j
                    jac[j, i] = avg_j

        np.fill_diagonal(jac, 1.0)
        jac_df = pd.DataFrame(jac, index=names, columns=names)

        # max off-diagonal
        mask = np.ones_like(jac, dtype=bool)
        np.fill_diagonal(mask, False)
        max_jac = float(jac[mask].max()) if n > 1 else 0.0

        return jac_df, max_jac
