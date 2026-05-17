"""CB ML Ranking 策略 — 基于 LightGBM 排名模型的可转债轮动策略。

策略流程：
1. 每个调仓日，计算所有候选 CB 的特征向量
2. 用预训练的 CBLGBRanker 打分
3. 按分数降序选取 top_k 持仓

ML 模型预训练（offline），策略运行时仅做推理。
训练使用 Purged Walk-Forward，确保无前视偏差。

与现有架构关系：
- 继承 CBRotationStrategy
- 注册为 "cb_ml_ranking"
- 复用 cb_factors + 传统因子
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBMLRankingConfig(CBRotationConfig):
    """CB ML 排名策略配置。

    Attributes:
        model_path: 预训练模型路径（空则用因子 fallback）
        max_premium: 最大溢价率过滤
        use_fallback: 模型不可用时是否回退到因子打分
    """
    model_path: str = ""
    max_premium: float = 40.0
    use_fallback: bool = True


# ---------------------------------------------------------------------------
# 策略类
# ---------------------------------------------------------------------------

@StrategyRegistry().register("cb_ml_ranking")
class CBMLRankingStrategy(CBRotationStrategy):
    """基于 LightGBM 排名模型的 CB 轮动策略。"""

    config_class = CBMLRankingConfig

    def __init__(self, config: CBMLRankingConfig | None = None) -> None:
        super().__init__()
        self._config = config or CBMLRankingConfig()
        self._ranker: Any = None
        self._fallback_mode = False

    @property
    def config(self) -> CBMLRankingConfig:
        return self._config

    def on_start(self) -> None:
        """初始化：尝试加载预训练模型。"""
        super().on_start()
        self._load_model()

    def _load_model(self) -> None:
        """加载预训练 LightGBM 模型。"""
        cfg: CBMLRankingConfig = self.config  # type: ignore[assignment]
        if not cfg.model_path:
            logger.info("No model_path specified, using factor fallback")
            self._fallback_mode = True
            return
        model_path = Path(cfg.model_path)
        if not model_path.exists():
            logger.warning("Model file not found: %s, using fallback", model_path)
            self._fallback_mode = True
            return
        try:
            from aipha.models.cb_lgb_ranker import CBLGBRanker
            self._ranker = CBLGBRanker()
            self._ranker.load(model_path)
            self._fallback_mode = False
            logger.info("ML ranker loaded from %s", model_path)
        except Exception as e:
            logger.warning("Failed to load ML ranker: %s, using fallback", e)
            self._fallback_mode = True

    def _extra_filter(
        self, candidate: CandidateInfo, context: StrategyContext
    ) -> bool:
        """溢价率过滤。cb_over_rate=None 时拒绝（§R140: 防止 ML 收到 0% 虚假特征）。"""
        cfg: CBMLRankingConfig = self.config  # type: ignore[assignment]
        if candidate.cb_over_rate is None:
            return False
        if candidate.cb_over_rate > cfg.max_premium:
            return False
        return True

    def _score_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """ML 排名 → top_k 选股。"""
        if not candidates:
            return []
        cfg: CBMLRankingConfig = self.config  # type: ignore[assignment]
        top_k = cfg.top_k

        if self._fallback_mode or self._ranker is None:
            return self._fallback_score(candidates, context)

        # 构建特征并用 ML 排名
        import pandas as pd
        features = self._build_features(candidates, context)
        df = pd.DataFrame(features)
        try:
            scores = self._ranker.predict(df)
        except Exception as e:
            logger.warning("ML predict failed: %s, using fallback", e)
            return self._fallback_score(candidates, context)

        # 按分数降序排名
        ranked_idx = np.argsort(-scores)
        selected = [candidates[i].symbol for i in ranked_idx[:top_k]]
        return selected

    def _fallback_score(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """因子 fallback 打分（无模型时使用）。

        简单多因子打分：price + premium (升序), cb_value + liquidity (降序)
        """
        from aipha.factors.cb_factors import compute_all_factors
        cfg: CBMLRankingConfig = self.config  # type: ignore[assignment]
        now = context.current_time
        scored = []
        for c in candidates:
            fv = compute_all_factors(c, now)
            # 简化打分：低双低 + 高流动性 + 高到期凸性
            # 溢价率缺失的标的跳过（避免 0.0 虚假信号）
            if c.cb_over_rate is None:
                continue
            double_low = c.close + c.cb_over_rate
            score = double_low - 10 * fv.liquidity_activity - 5 * fv.maturity_convexity
            scored.append((score, c.symbol))
        scored.sort()  # 升序：低分更好
        return [s for _, s in scored[:cfg.top_k]]

    def _build_features(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[dict[str, float]]:
        """构建特征向量。"""
        from aipha.factors.cb_factors import compute_all_factors
        now = context.current_time
        features_list = []
        for c in candidates:
            # 基础因子 — _extra_filter 已保证 cb_over_rate 非 None
            feat: dict[str, float] = {
                "close": c.close,
                "cb_over_rate": float(c.cb_over_rate),  # guaranteed non-None by filter
                "cb_value": c.cb_value if c.cb_value is not None else 0.0,
                "issue_size": c.issue_size,
                "amount": c.amount,
                "rating_score": self._rating_to_score(c.rating),
            }
            # 新因子
            fv = compute_all_factors(c, now)
            feat["iv_bias"] = fv.iv_bias
            feat["gamma_convexity"] = fv.gamma_convexity
            feat["credit_spread"] = fv.credit_spread
            feat["liquidity_activity"] = fv.liquidity_activity
            feat["maturity_convexity"] = fv.maturity_convexity
            features_list.append(feat)
        return features_list

    @staticmethod
    def _rating_to_score(rating: str) -> float:
        """信用评级 → 数值分数。"""
        score_map = {
            "AAA": 6.0, "AA+": 5.0, "AA": 4.0, "AA-": 3.0,
            "A+": 2.0, "A": 1.0,
        }
        return score_map.get(rating, 3.0)
