"""高级四指标拥挤度监控 — AdvancedCrowdingMonitor。

扩展现有 CrowdingLimit（Jaccard），增加知识库 Q2 规定的四个拥挤度指标：

| 指标 | 英文名 | 阈值 | 说明 |
|------|--------|------|------|
| 因子估值价差 | Factor Value Spread (FVS) | >90% 百分位 | 因子多空组合估值溢价与历史比较 |
| 换手集中度 | Turnover Concentration | >2x | 当前策略换手是否集中在少数标的 |
| Pairwise 相关 | Strategy Correlation | >0.6 | 各策略日收益的两两相关均值 |
| 日内冲击比 | Intraday Impact Ratio | Level-2 | 需L2数据，当前仅占位实现 |

背景：2024 DMA 踩踏事件（知识库 Q3）— 多策略同向持仓 + 高相关导致流动性危机。

理论基础：
- Khandani & Lo (2007) — 量化基金拥挤度
- 知识库 Q2 四个拥挤度指标定义
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

FVS_CROWD_QUANTILE = 0.90      # FVS >90分位 → 拥挤
TURNOVER_CROWD_RATIO = 2.0     # 换手集中度 >2x → 警告
PW_CORR_CROWD = 0.60           # Pairwise相关 >0.60 → 拥挤


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CrowdingMonitorConfig:
    """拥挤度监控配置。

    Attributes:
        fvs_crowd_quantile: FVS 拥挤阈值（历史百分位）
        fvs_history_window: FVS 历史窗口（期数）
        turnover_crowd_ratio: 换手集中度警告倍数
        pairwise_corr_crowd: Pairwise 相关拥挤阈值
        pairwise_min_obs: 计算相关所需的最少共同观测数
    """
    fvs_crowd_quantile: float = FVS_CROWD_QUANTILE
    fvs_history_window: int = 252
    turnover_crowd_ratio: float = TURNOVER_CROWD_RATIO
    pairwise_corr_crowd: float = PW_CORR_CROWD
    pairwise_min_obs: int = 20


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------

@dataclass
class CrowdingMetrics:
    """四指标拥挤度评估结果。

    Attributes:
        fvs_value: 当前因子估值价差
        fvs_percentile: FVS 历史百分位（0~1）
        fvs_crowded: FVS 是否拥挤
        turnover_ratio: 换手集中度（当前/历史均值）
        turnover_crowded: 换手集中度是否超阈值
        pairwise_corr: 策略两两相关均值
        pairwise_crowded: 是否超相关阈值
        intraday_impact: 日内冲击比（需L2，None=未接入）
        overall_crowded: 任一指标拥挤即为 True
        crowding_score: 加权综合拥挤分（0~1）
        warnings: 告警信息列表
    """
    fvs_value: Optional[float] = None
    fvs_percentile: Optional[float] = None
    fvs_crowded: bool = False
    turnover_ratio: Optional[float] = None
    turnover_crowded: bool = False
    pairwise_corr: Optional[float] = None
    pairwise_crowded: bool = False
    intraday_impact: Optional[float] = None   # Level-2 接入后填充
    overall_crowded: bool = False
    crowding_score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fvs_value": self.fvs_value,
            "fvs_percentile": round(self.fvs_percentile, 4) if self.fvs_percentile is not None else None,
            "fvs_crowded": self.fvs_crowded,
            "turnover_ratio": round(self.turnover_ratio, 4) if self.turnover_ratio is not None else None,
            "turnover_crowded": self.turnover_crowded,
            "pairwise_corr": round(self.pairwise_corr, 4) if self.pairwise_corr is not None else None,
            "pairwise_crowded": self.pairwise_crowded,
            "intraday_impact": self.intraday_impact,
            "overall_crowded": self.overall_crowded,
            "crowding_score": round(self.crowding_score, 4),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# 主监控类
# ---------------------------------------------------------------------------

class AdvancedCrowdingMonitor:
    """四指标拥挤度监控器。

    Usage::

        monitor = AdvancedCrowdingMonitor()

        # 指标1: 因子估值价差
        monitor.add_fvs_observation(current_fvs=0.15)

        # 指标2: 换手集中度
        turnover_ratio = monitor.compute_turnover_concentration(
            current_turnovers={"A001": 5000, "A002": 200},
            historical_avg_turnover=1500.0,
        )

        # 指标3: Pairwise 相关
        import numpy as np
        returns_matrix = np.random.randn(60, 5)  # 60天 × 5策略
        corr = monitor.compute_pairwise_correlation(returns_matrix)

        # 综合评估
        metrics = monitor.evaluate()
    """

    def __init__(self, config: CrowdingMonitorConfig | None = None) -> None:
        self._config = config or CrowdingMonitorConfig()
        self._fvs_history: list[float] = []    # FVS 历史观测
        self._latest_turnover_ratio: Optional[float] = None
        self._latest_pairwise_corr: Optional[float] = None

    @property
    def config(self) -> CrowdingMonitorConfig:
        return self._config

    # ------------------------------------------------------------------
    # 指标1: Factor Value Spread (FVS) — 因子估值价差
    # ------------------------------------------------------------------

    def add_fvs_observation(self, current_fvs: float) -> None:
        """添加当期因子估值价差观测（纳入历史窗口）。

        FVS 定义：多头分位组平均因子值 − 空头分位组平均因子值
        历史百分位 >90% 时标记为拥挤。

        Args:
            current_fvs: 当期 FVS 值（可正可负）
        """
        self._fvs_history.append(current_fvs)
        # 保留最近 fvs_history_window 个观测
        max_w = self._config.fvs_history_window
        if len(self._fvs_history) > max_w:
            self._fvs_history = self._fvs_history[-max_w:]

    def get_fvs_percentile(self) -> Optional[float]:
        """当期 FVS 在历史窗口内的百分位（0~1）。

        Returns:
            百分位，历史不足 2 点时返回 None
        """
        if len(self._fvs_history) < 2:
            return None
        latest = self._fvs_history[-1]
        arr = np.array(self._fvs_history)
        return float(np.mean(arr <= latest))

    # ------------------------------------------------------------------
    # 指标2: Turnover Concentration — 换手集中度
    # ------------------------------------------------------------------

    def compute_turnover_concentration(
        self,
        current_turnovers: dict[str, float],
        historical_avg_turnover: float,
    ) -> float:
        """计算换手集中度（当前最大换手 / 历史均值换手）。

        超过 turnover_crowd_ratio 倍时发出警告。

        Args:
            current_turnovers: 各标的当日成交额 {symbol: amount}
            historical_avg_turnover: 历史日均换手额（参考基准）

        Returns:
            最大集中符号换手 / 历史均值换手
        """
        if not current_turnovers or historical_avg_turnover <= 0:
            return 0.0
        max_turnover = max(current_turnovers.values())
        ratio = max_turnover / historical_avg_turnover
        self._latest_turnover_ratio = ratio
        return ratio

    # ------------------------------------------------------------------
    # 指标3: Pairwise Correlation — 策略两两相关
    # ------------------------------------------------------------------

    def compute_pairwise_correlation(
        self,
        returns_matrix: np.ndarray,
    ) -> Optional[float]:
        """计算多策略日收益的两两相关均值。

        Args:
            returns_matrix: shape=(T, N) 的收益矩阵
              - T = 日数（需 >= pairwise_min_obs）
              - N = 策略数（需 >= 2）

        Returns:
            两两 Pearson 相关均值，N<2 或 T 不足时返回 None
        """
        cfg = self._config
        if returns_matrix.ndim != 2:
            return None
        T, N = returns_matrix.shape
        if N < 2 or T < cfg.pairwise_min_obs:
            return None

        corr_matrix = np.corrcoef(returns_matrix.T)  # (N, N)
        # 取上三角（不含对角线）
        triu_idx = np.triu_indices(N, k=1)
        if len(triu_idx[0]) == 0:
            return None
        corr_values = corr_matrix[triu_idx]
        # 过滤 NaN
        valid = corr_values[np.isfinite(corr_values)]
        if len(valid) == 0:
            return None
        mean_corr = float(np.mean(valid))
        self._latest_pairwise_corr = mean_corr
        return mean_corr

    # ------------------------------------------------------------------
    # 综合评估
    # ------------------------------------------------------------------

    def evaluate(self) -> CrowdingMetrics:
        """综合评估当前拥挤状态，返回 CrowdingMetrics。

        仅对已收到数据的指标打分，未接入的指标不影响综合结果。
        """
        cfg = self._config
        warnings: list[str] = []
        scores: list[float] = []

        # --- FVS ---
        fvs_pct = self.get_fvs_percentile()
        fvs_val = self._fvs_history[-1] if self._fvs_history else None
        fvs_crowded = False
        if fvs_pct is not None:
            fvs_crowded = fvs_pct > cfg.fvs_crowd_quantile
            if fvs_crowded:
                warnings.append(
                    f"FVS percentile={fvs_pct:.2f} > {cfg.fvs_crowd_quantile} (crowded)"
                )
            scores.append(min(fvs_pct, 1.0))

        # --- Turnover Concentration ---
        tr = self._latest_turnover_ratio
        turnover_crowded = False
        if tr is not None:
            turnover_crowded = tr > cfg.turnover_crowd_ratio
            if turnover_crowded:
                warnings.append(
                    f"Turnover concentration={tr:.2f}x > {cfg.turnover_crowd_ratio}x"
                )
            scores.append(min(tr / (cfg.turnover_crowd_ratio * 2), 1.0))

        # --- Pairwise Correlation ---
        pc = self._latest_pairwise_corr
        pairwise_crowded = False
        if pc is not None:
            pairwise_crowded = pc > cfg.pairwise_corr_crowd
            if pairwise_crowded:
                warnings.append(
                    f"Mean pairwise corr={pc:.3f} > {cfg.pairwise_corr_crowd}"
                )
            scores.append(max(0.0, min(abs(pc), 1.0)))

        overall = fvs_crowded or turnover_crowded or pairwise_crowded
        crowding_score = float(np.mean(scores)) if scores else 0.0

        return CrowdingMetrics(
            fvs_value=fvs_val,
            fvs_percentile=fvs_pct,
            fvs_crowded=fvs_crowded,
            turnover_ratio=tr,
            turnover_crowded=turnover_crowded,
            pairwise_corr=pc,
            pairwise_crowded=pairwise_crowded,
            intraday_impact=None,  # Level-2 接入后填充
            overall_crowded=overall,
            crowding_score=crowding_score,
            warnings=warnings,
        )

    def reset(self) -> None:
        """重置所有历史数据。"""
        self._fvs_history.clear()
        self._latest_turnover_ratio = None
        self._latest_pairwise_corr = None
