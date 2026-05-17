"""A 股 Quality + LowVol 组合因子策略。

面向 CSI300 大盘蓝筹的低衰减稳定 alpha 策略，作为 v6d 组合
的 CSI300 腿增强方案或独立组件。

**因子组成**：
- Quality (60%): ROE 稳定性 + ROIC + 低杠杆 + 高经营现金流质量
- LowVol (40%): 60 日波动率倒数 + 最大跌幅回避

**理论基础**：
- Fama-French 2015 五因子模型：RMW (盈利质量) 长期溢价
- Low Volatility Anomaly (Ang et al. 2006): 低波动股票异常高收益
- 零衰减属性: CSI300 ≥200B 成分内该信号无显著衰减 (D-014)

**与 ML CSI2000 正交性**：
- CSI2000 以中小盘量价因子为主 → 大盘财务质量因子天然正交
- 目标相关性 ρ < 0.40

**设计约束**：
- Universe: CSI300 成分股 (市值 ≥ 200 亿)
- top_k: 30 (与 T1A 同)
- 调仓周期: 月频 (财务数据月度更新)
- fill_model: next_bar_open
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
    rank_normalize,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


@dataclass
class QualityLowVolConfig(EquityRotationConfig):
    """Quality + LowVol 策略配置。"""

    # 因子权重
    quality_weight: float = 0.60
    lowvol_weight: float = 0.40

    # Quality 子因子权重
    roe_stability_weight: float = 0.30
    roic_weight: float = 0.25
    leverage_weight: float = 0.20  # 低杠杆 (inverted)
    cashflow_quality_weight: float = 0.25

    # LowVol 子因子权重
    vol60_weight: float = 0.60  # 60日波动率倒数
    mdd60_weight: float = 0.40  # 60日最大跌幅回避

    # ROE 稳定性参数
    roe_lookback_quarters: int = 8  # 2年稳定性
    roe_min: float = 5.0  # ROE > 5% 才参与

    # 波动率参数
    vol_lookback_days: int = 60
    mdd_lookback_days: int = 60

    # 覆盖基类默认值，确保 _update_history 保留足够的价格历史
    volatility_lookback: int = 60


@StrategyRegistry().register("quality_lowvol")
class QualityLowVolStrategy(EquityRotationStrategy):
    """Quality + LowVol A 股大盘选股策略。"""

    config_class = QualityLowVolConfig

    def __init__(self, config: Optional[QualityLowVolConfig] = None, **kwargs: Any) -> None:
        if config:
            self._cfg = config
        else:
            # 过滤掉 data_loader 专用参数，只传 config 支持的字段
            import dataclasses
            valid_fields = {f.name for f in dataclasses.fields(QualityLowVolConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            self._cfg = QualityLowVolConfig(**filtered)
        super().__init__()

    @property
    def config(self) -> QualityLowVolConfig:
        return self._cfg

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext
    ) -> list[str]:
        """对候选股票打分: Quality × 0.6 + LowVol × 0.4，返回排序 symbol 列表。"""
        if not candidates:
            return []

        scores: dict[str, float] = {}

        for cand in candidates:
            q_score = self._compute_quality_score(cand)
            lv_score = self._compute_lowvol_score(cand)

            if q_score is None or lv_score is None:
                continue

            scores[cand.symbol] = q_score  # 先存原始质量分

        if not scores:
            return []

        # 收集合格候选的分数
        valid_symbols = list(scores.keys())
        q_raw = [scores[s] for s in valid_symbols]
        lv_raw = []
        for cand in candidates:
            if cand.symbol in scores:
                lv = self._compute_lowvol_score(cand)
                lv_raw.append(lv if lv is not None else 0.0)

        # 排名归一化
        q_ranks = rank_normalize(q_raw)
        lv_ranks = rank_normalize(lv_raw)

        # 复合得分
        final_scores: dict[str, float] = {}
        w_q = self._cfg.quality_weight
        w_lv = self._cfg.lowvol_weight
        for i, sym in enumerate(valid_symbols):
            final_scores[sym] = w_q * q_ranks[i] + w_lv * lv_ranks[i]

        # 按分数降序排列
        sorted_symbols = sorted(final_scores, key=lambda s: final_scores[s], reverse=True)
        return sorted_symbols

    def _compute_quality_score(
        self, cand: Any
    ) -> Optional[float]:
        """计算 Quality 子分数。

        子因子:
        - ROE 稳定性: mean(ROE) / std(ROE)，值越高越好
        - ROIC: 投入资本回报率
        - 低杠杆: 1/资产负债率
        - 现金流质量: 经营现金流/净利润
        """
        # 从 metadata/PIT 获取财务数据
        meta = getattr(cand, "metadata", {}) or {}

        # ROE 稳定性
        roe_hist = meta.get("roe_history", [])
        if len(roe_hist) >= 4:
            roe_arr = np.array(roe_hist, dtype=float)
            roe_arr = roe_arr[~np.isnan(roe_arr)]
            if len(roe_arr) >= 4 and np.std(roe_arr) > 0:
                roe_stability = float(np.mean(roe_arr) / np.std(roe_arr))
            else:
                roe_stability = 0.0
            if np.mean(roe_arr) < self._cfg.roe_min:
                return None  # 剔除低 ROE
        else:
            # 降级: 使用单期 ROE
            roe = meta.get("roe", None)
            if roe is None or roe < self._cfg.roe_min:
                return None
            roe_stability = roe / 10.0  # 归一化

        # ROIC
        roic = meta.get("roic", meta.get("roe", 10.0))

        # 杠杆 (低=好, 取倒数)
        debt_ratio = meta.get("debt_to_assets", 0.5)
        if debt_ratio <= 0.01:
            debt_ratio = 0.01
        leverage_inv = 1.0 / debt_ratio

        # 现金流质量 (经营现金流/营业收入)
        ocf_quality = meta.get("ocf_to_or", meta.get("ocf_to_profit", 1.0))
        if ocf_quality < 0:
            ocf_quality = 0.0

        # 加权
        cfg = self._cfg
        score = (
            cfg.roe_stability_weight * roe_stability
            + cfg.roic_weight * roic
            + cfg.leverage_weight * leverage_inv
            + cfg.cashflow_quality_weight * ocf_quality
        )
        return score

    def _compute_lowvol_score(
        self, cand: Any
    ) -> Optional[float]:
        """计算 LowVol 子分数。

        子因子:
        - 60 日波动率倒数 (低波=好)
        - 60 日最大跌幅回避 (MDD 小=好, 取倒数)

        使用基类维护的 _price_history 获取历史收盘价。
        """
        symbol = cand.symbol
        hist = self._price_history.get(symbol, [])

        lookback = self._cfg.vol_lookback_days
        if len(hist) < lookback + 1:
            return None

        prices = np.array(hist[-(lookback + 1):], dtype=float)
        prices = prices[prices > 0]
        if len(prices) < lookback // 2:
            return None

        # 60日波动率
        rets = np.diff(np.log(prices))
        if len(rets) < 5:
            return None
        vol60 = float(np.std(rets) * np.sqrt(244))
        if vol60 <= 0:
            return None
        vol_inv = 1.0 / vol60

        # 60日最大跌幅
        cummax = np.maximum.accumulate(prices)
        drawdowns = (prices - cummax) / cummax
        mdd60 = float(-np.min(drawdowns))
        if mdd60 <= 0.001:
            mdd60 = 0.001
        mdd_inv = 1.0 / mdd60

        # 加权
        cfg = self._cfg
        score = cfg.vol60_weight * vol_inv + cfg.mdd60_weight * mdd_inv
        return score
