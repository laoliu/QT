"""EQ-S02 行业中性配对交易策略 — A 股多头版.

理论基础:
  - 行业中性约束消除系统性行业 beta 暴露 (Khandani & Lo 2007)
  - 行业内相对价值回归: 同行业股票受共同因子驱动, 偏离均值后回归
  - A 股做空限制下降级为行业内选股: 每个行业选择相对低估标的做多

策略逻辑:
  1. 按申万一级行业分组候选股
  2. 行业内计算相对价值得分 (PE/PB 行业内百分位)
  3. 行业内计算均值回归得分 (近期相对收益 z-score)
  4. 加权合成得分, 每个行业选出 top N 标的
  5. 行业均衡组合: 等权分配, 天然行业中性
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)

__all__ = ["PairTradingIndustryNeutralStrategy", "PairTradingConfig"]


@dataclass
class PairTradingConfig(EquityRotationConfig):
    """行业中性配对交易策略配置."""

    # --- 行业中性参数 ---
    n_per_industry: int = 2
    """每个行业选取的标的数."""
    min_industry_stocks: int = 5
    """行业内候选不足此数则跳过该行业."""
    top_industries: int = 0
    """参与的行业数上限 (0=不限制)."""

    # --- 因子权重 ---
    value_weight: float = 0.5
    """相对价值因子权重 (1 - value_weight = 均值回归权重)."""
    lookback_days: int = 60
    """均值回归回看窗口 (交易日)."""

    # --- 基类默认覆盖 ---
    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 5.0
    max_price: float = 200.0
    min_mv: float = 100_000.0
    max_mv: float = 30_000_000.0
    stop_loss_pct: float = 0.08
    momentum_lookback: int = 60


@StrategyRegistry().register("pair_trading_industry_neutral")
class PairTradingIndustryNeutralStrategy(EquityRotationStrategy):
    """行业中性配对交易策略."""

    name = "pair_trading_industry_neutral"

    def __init__(
        self,
        config: PairTradingConfig | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None and kwargs:
            valid = {
                k: v
                for k, v in kwargs.items()
                if k in PairTradingConfig.__dataclass_fields__
            }
            config = PairTradingConfig(**valid)
        super().__init__()
        self._cfg = config or PairTradingConfig()

    @property
    def config(self) -> PairTradingConfig:
        return self._cfg

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: Any,
    ) -> list[str]:
        """行业内相对价值 + 均值回归得分, 按行业均衡选股.

        1. 按申万一级行业分组
        2. 行业内计算相对价值排名 (低 PE/PB 优先)
        3. 行业内计算均值回归排名 (近期跑输行业均值的股票优先)
        4. 加权合成得分, 每行业选 top N
        5. 汇总并按得分排序返回
        """
        cfg = self._cfg

        # --- 按行业分组 ---
        industry_map: dict[str, list[EquityCandidateInfo]] = {}
        for c in candidates:
            ind = c.metadata.get("industry", "未知") if c.metadata else "未知"
            industry_map.setdefault(ind, []).append(c)

        # 过滤候选不足的行业
        valid_industries = {
            ind: stocks
            for ind, stocks in industry_map.items()
            if len(stocks) >= cfg.min_industry_stocks and ind != "未知"
        }

        if not valid_industries:
            logger.warning("EQ-S02: 无满足最低标的数(%d)的行业", cfg.min_industry_stocks)
            return [c.symbol for c in candidates[: cfg.top_k]]

        # --- 行业内打分 ---
        scored: list[tuple[str, float]] = []  # (symbol, composite_score)

        for ind, stocks in valid_industries.items():
            ind_scores = self._score_within_industry(stocks)
            # 选出行业内 top N
            ind_scores.sort(key=lambda x: x[1], reverse=True)
            for sym, score in ind_scores[: cfg.n_per_industry]:
                scored.append((sym, score))

        # 若启用 top_industries 限制, 按行业平均得分选 top N 行业
        if cfg.top_industries > 0 and len(valid_industries) > cfg.top_industries:
            ind_avg: dict[str, float] = {}
            for ind, stocks in valid_industries.items():
                ind_scores_all = self._score_within_industry(stocks)
                if ind_scores_all:
                    ind_avg[ind] = np.mean([s for _, s in ind_scores_all])
            top_inds = sorted(ind_avg, key=ind_avg.get, reverse=True)[  # type: ignore[arg-type]
                : cfg.top_industries
            ]
            scored = [(s, sc) for s, sc in scored
                      if any(s in [c.symbol for c in valid_industries.get(ind, [])]
                             for ind in top_inds)]

        # 按得分排序
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [sym for sym, _ in scored]

        logger.info(
            "EQ-S02: %d 行业 × %d/行业 → %d 候选",
            len(valid_industries),
            cfg.n_per_industry,
            len(result),
        )
        return result

    def _score_within_industry(
        self,
        stocks: list[EquityCandidateInfo],
    ) -> list[tuple[str, float]]:
        """计算行业内相对价值 + 均值回归合成得分.

        Returns:
            [(symbol, score), ...] — 分值越高越优先.
        """
        cfg = self._cfg
        n = len(stocks)
        if n == 0:
            return []

        # --- 相对价值得分 (低 PE/PB → 高分) ---
        pe_vals = []
        pb_vals = []
        for c in stocks:
            pe = c.metadata.get("pe_ttm") if c.metadata else None
            pb = c.metadata.get("pb") if c.metadata else None
            pe_vals.append(pe if pe and pe > 0 else None)
            pb_vals.append(pb if pb and pb > 0 else None)

        value_ranks = self._rank_ascending(pe_vals, pb_vals, n)

        # --- 均值回归得分 (近期跑输行业均值 → 高分) ---
        mr_ranks = self._rank_mean_reversion(stocks, n)

        # --- 合成 ---
        vw = cfg.value_weight
        mw = 1.0 - vw
        result = []
        for i, c in enumerate(stocks):
            composite = vw * value_ranks[i] + mw * mr_ranks[i]
            result.append((c.symbol, composite))

        return result

    @staticmethod
    def _rank_ascending(
        pe_vals: list[float | None],
        pb_vals: list[float | None],
        n: int,
    ) -> list[float]:
        """PE/PB 升序排名转百分位 (低估值→高分)."""
        pe_rank = [0.5] * n
        pb_rank = [0.5] * n

        valid_pe = [(i, v) for i, v in enumerate(pe_vals) if v is not None]
        if valid_pe:
            sorted_pe = sorted(valid_pe, key=lambda x: x[1])
            for rank_idx, (orig_idx, _) in enumerate(sorted_pe):
                pe_rank[orig_idx] = 1.0 - rank_idx / max(len(sorted_pe) - 1, 1)

        valid_pb = [(i, v) for i, v in enumerate(pb_vals) if v is not None]
        if valid_pb:
            sorted_pb = sorted(valid_pb, key=lambda x: x[1])
            for rank_idx, (orig_idx, _) in enumerate(sorted_pb):
                pb_rank[orig_idx] = 1.0 - rank_idx / max(len(sorted_pb) - 1, 1)

        return [0.5 * pe + 0.5 * pb for pe, pb in zip(pe_rank, pb_rank)]

    def _rank_mean_reversion(
        self,
        stocks: list[EquityCandidateInfo],
        n: int,
    ) -> list[float]:
        """行业内均值回归排名: 近期跑输行业均值 → 高分."""
        returns = []
        for c in stocks:
            hist = self._price_history.get(c.symbol, [])
            lb = self._cfg.lookback_days
            if len(hist) >= lb + 1:
                ret = (hist[-1] / hist[-lb - 1]) - 1.0
            elif len(hist) >= 2:
                ret = (hist[-1] / hist[0]) - 1.0
            else:
                ret = 0.0
            returns.append(ret)

        if not returns:
            return [0.5] * n

        # 行业均值回报
        mean_ret = np.mean(returns)
        std_ret = np.std(returns) if len(returns) > 1 else 1.0
        std_ret = max(std_ret, 1e-8)

        # Z-score: 负值 = 跑输均值 → 均值回归机会
        # 反转: 低 z-score → 高分
        z_scores = [(r - mean_ret) / std_ret for r in returns]
        sorted_z = sorted(range(n), key=lambda i: z_scores[i])
        ranks = [0.0] * n
        for rank_idx, orig_idx in enumerate(sorted_z):
            ranks[orig_idx] = 1.0 - rank_idx / max(n - 1, 1)

        return ranks
