"""CSI2000 微盘 ML 策略 (TS-5, R156)

基于 LightGBM 预测未来 5 日超额收益，Universe 为 CSI2000 成分股。
继承 EquityRotationStrategy，使用 ml_factor 注册名复用 OOS predictions 机制。

设计要点：
- 周频调仓（每 5 交易日）
- Top 20 等权
- 特征 ≈ 40 个（价量 14 + fina_indicator 20 + cashflow 6）
- 单票参与率 0.05（微盘保守）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
    StrategyContext,
)

logger = logging.getLogger(__name__)


@dataclass
class MicrocapMLCSI2000Config(EquityRotationConfig):
    """CSI2000 微盘 ML 策略配置。"""

    top_k: int = 20
    rebalance_days: int = 5
    buffer_k: int = 5
    min_price: float = 2.0
    max_price: float = 50.0
    min_turnover: float = 0.005
    min_listed_days: int = 120
    check_index_member: bool = True
    lot_size: int = 100
    # ML 参数
    oos_predictions_path: str = ""
    prediction_col: str = "pred_ret5d"


class MicrocapMLCSI2000Strategy(EquityRotationStrategy):
    """CSI2000 微盘 ML 策略。

    注册名: microcap_ml_csi2000
    """

    STRATEGY_NAME = "microcap_ml_csi2000"

    def __init__(self, **kwargs) -> None:
        self._params = kwargs
        super().__init__()
        self._predictions: dict[str, float] = {}

    @property
    def config(self) -> MicrocapMLCSI2000Config:
        if not hasattr(self, "_config"):
            self._config = MicrocapMLCSI2000Config(**self._params)
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """使用 ML 预测值排名，返回 top_k 代码列表。"""
        scored = []
        for c in candidates:
            pred = self._predictions.get(c.symbol, 0.0)
            scored.append((c.symbol, pred))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_k = self.config.top_k
        return [code for code, _ in scored[:top_k]]

    def _on_bar_update_state(
        self, context: StrategyContext
    ) -> None:
        """每个 bar 更新 ML 预测值。"""
        trade_date = context.trade_date
        if not trade_date:
            return
        # 从 bar metadata 或外部 predictions 加载
        if hasattr(context, "predictions"):
            self._predictions = context.predictions
