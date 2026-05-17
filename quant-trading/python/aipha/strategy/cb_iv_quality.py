"""CB IV 质量增强策略 v5

在 v4b (正股质量 5+1 因子) 基础上，融合 BSM 隐含波动率和 Greeks 因子。
新增因子维度（IC 验证通过，新规前后均显著）：
  - **theta**: ICIR=+0.38(全量)/+0.30(新规后) — 时间价值衰减率
  - **gamma**: ICIR=+0.20(全量)/+0.38(新规后) — 新规后增强
  - **iv_reverse**: ICIR=-0.41(全量)/-0.28(新规后) — 低波动率异常

因子矩阵 (8+1 因子)：
  Layer 1 — CB 本体: price + premium (0.20)
  Layer 2 — 正股质量: ROE + EP + Vol (0.35)
  Layer 3 — IV/Greeks: theta + gamma + iv_reverse (0.35)
  Structural: issue_size (0.10)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from aipha.factors.cb_iv_greeks import compute_cb_iv_factors
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_stock_quality import (
    CBStockQualityStrategy,
    StockQualityCBConfig,
    _rank_scores,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class IVQualityConfig(StockQualityCBConfig):
    """CB IV 质量增强配置（继承 v4b 全部参数）。"""

    # --- IV/Greeks 因子权重 ---
    weight_theta: float = 0.15
    weight_gamma: float = 0.10
    weight_iv_reverse: float = 0.10

    # --- v5 权重覆盖 (降低传统因子，腾出 IV 空间) ---
    weight_price: float = 0.12
    weight_premium: float = 0.08
    weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.12
    weight_stock_vol: float = 0.08
    weight_issue_size: float = 0.10

    # --- IV 计算参数 ---
    iv_risk_free_rate: float = 0.02
    iv_credit_spread: float = 0.015


@StrategyRegistry().register("cb_iv_quality")
class CBIVQualityStrategy(CBStockQualityStrategy):
    """CB IV 质量增强策略 v5。"""

    name = "cb_iv_quality"

    def __init__(self, config: IVQualityConfig | None = None, **kwargs: Any) -> None:
        if config is None:
            config = IVQualityConfig(**kwargs)
        super().__init__(config=config, **kwargs)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """v4b 因子 + theta/gamma/iv_reverse 扩展评分。"""
        cfg: IVQualityConfig = self._config  # type: ignore[assignment]
        if not candidates:
            return []
        n = len(candidates)
        if n == 1:
            return [candidates[0].symbol]

        # ---- 传统因子 (复用 v4b 逻辑) ----
        prices = [c.close for c in candidates]
        premiums = [
            c.cb_over_rate if c.cb_over_rate is not None else 50.0
            for c in candidates
        ]
        sizes = [c.issue_size for c in candidates]
        roes = self._get_factor_roe(candidates, cfg)
        eps_vals = self._get_factor_ep(candidates, cfg)
        vols = self._get_factor_vol(candidates)

        # ---- IV/Greeks 因子 ----
        iv_factors = self._compute_iv_for_candidates(candidates, context)
        thetas = [iv_factors[i][0] for i in range(n)]
        gammas = [iv_factors[i][1] for i in range(n)]
        ivs = [iv_factors[i][2] for i in range(n)]

        # ---- 排名归一化 ----
        r_price = _rank_scores(prices, ascending=True)
        r_prem = _rank_scores(premiums, ascending=True)
        r_roe = _rank_scores(roes, ascending=False)
        r_ep = _rank_scores(eps_vals, ascending=False)
        r_vol = _rank_scores(vols, ascending=False)
        r_size = _rank_scores(sizes, ascending=True)
        r_theta = _rank_scores(thetas, ascending=False)   # 高 theta 好
        r_gamma = _rank_scores(gammas, ascending=False)    # 高 gamma 好
        r_iv = _rank_scores(ivs, ascending=True)           # 低 IV 好(反向)

        # ---- 权重归一化 ----
        total_w = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_stock_roe + cfg.weight_stock_ep
            + cfg.weight_stock_vol + cfg.weight_issue_size
            + cfg.weight_theta + cfg.weight_gamma + cfg.weight_iv_reverse
            + cfg.weight_ytm + cfg.weight_bond_floor
        )
        if total_w <= 0:
            total_w = 1.0

        # ---- 综合打分 ----
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            score = (
                cfg.weight_price / total_w * r_price[i]
                + cfg.weight_premium / total_w * r_prem[i]
                + cfg.weight_stock_roe / total_w * r_roe[i]
                + cfg.weight_stock_ep / total_w * r_ep[i]
                + cfg.weight_stock_vol / total_w * r_vol[i]
                + cfg.weight_issue_size / total_w * r_size[i]
                + cfg.weight_theta / total_w * r_theta[i]
                + cfg.weight_gamma / total_w * r_gamma[i]
                + cfg.weight_iv_reverse / total_w * r_iv[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1])
        return [s for s, _ in scored[: cfg.top_k]]

    # ---- 传统因子提取辅助 ----

    def _get_factor_roe(
        self, candidates: list[CandidateInfo], cfg: IVQualityConfig,
    ) -> list[float]:
        roes: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and fund.roe is not None:
                roes.append(fund.roe)
            else:
                roes.append(cfg.default_roe)
        return roes

    def _get_factor_ep(
        self, candidates: list[CandidateInfo], cfg: IVQualityConfig,
    ) -> list[float]:
        eps_vals: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk_code)
            if fund is not None and fund.ep is not None:
                eps_vals.append(fund.ep)
            else:
                eps_vals.append(cfg.default_ep)
        return eps_vals

    def _get_factor_vol(self, candidates: list[CandidateInfo]) -> list[float]:
        vols: list[float] = []
        for c in candidates:
            stk_code = (c.metadata or {}).get("stk_code", "")
            vol = self._compute_stk_vol(stk_code)
            vols.append(vol if vol is not None else 0.0)
        return vols

    def _compute_iv_for_candidates(
        self,
        candidates: list[CandidateInfo],
        context: StrategyContext,
    ) -> list[tuple[float, float, float]]:
        """为每个候选计算 (theta, gamma, iv) 三因子。

        Returns:
            List of (theta, gamma, iv) tuples; 缺失值用中位数填充。
        """
        cfg: IVQualityConfig = self._config  # type: ignore[assignment]
        trade_date = context.current_time
        if hasattr(trade_date, "strftime"):
            td_str = trade_date.strftime("%Y%m%d")
        else:
            td_str = str(trade_date).replace("-", "")[:8]

        raw: list[tuple[float | None, float | None, float | None]] = []
        for c in candidates:
            iv_res = self._single_iv(c, td_str, cfg)
            raw.append(iv_res)

        # 中位数填充缺失值
        return self._fill_median(raw)

    def _single_iv(
        self,
        c: CandidateInfo,
        td_str: str,
        cfg: IVQualityConfig,
    ) -> tuple[float | None, float | None, float | None]:
        """单只转债 IV/Greeks 计算，返回 (theta, gamma, iv)。"""
        conv_price = c.conv_price
        if conv_price is None or conv_price <= 0:
            return (None, None, None)

        # 正股价格: cb_value * conv_price / 100
        cb_val = c.cb_value if c.cb_value and c.cb_value > 0 else None
        if cb_val is None:
            return (None, None, None)
        S = float(cb_val) * float(conv_price) / 100.0
        if S <= 0:
            return (None, None, None)

        # 剩余期限(年)
        mat = c.maturity_date
        if not mat:
            return (None, None, None)
        try:
            mat_dt = datetime.strptime(str(mat)[:8], "%Y%m%d")
            td_dt = datetime.strptime(td_str[:8], "%Y%m%d")
            T = max(0.0, (mat_dt - td_dt).days / 365.25)
        except (ValueError, TypeError):
            return (None, None, None)
        if T <= 0.01:
            return (None, None, None)

        # 正股收益率序列 (from cb_value history)
        stk_code = (c.metadata or {}).get("stk_code", "")
        stock_rets = self._get_stock_returns(stk_code)

        result = compute_cb_iv_factors(
            symbol=c.symbol,
            cb_price=float(c.close),
            stock_price=S,
            conversion_price=float(conv_price),
            T=T,
            r=cfg.iv_risk_free_rate,
            spread=cfg.iv_credit_spread,
            stock_returns=stock_rets,
        )
        if result is None:
            return (None, None, None)
        theta = result.theta if math.isfinite(result.theta) else None
        gamma = result.gamma if math.isfinite(result.gamma) else None
        iv = result.iv if math.isfinite(result.iv) else None
        return (theta, gamma, iv)

    def _get_stock_returns(self, stk_code: str) -> np.ndarray | None:
        """从 cb_value 历史获取正股日收益率序列。"""
        if not stk_code:
            return None
        hist = self._stk_price_history.get(stk_code)
        if hist is None or len(hist) < 20:
            return None
        vals = np.array(list(hist), dtype=float)
        vals = vals[vals > 0]
        if len(vals) < 20:
            return None
        return np.diff(np.log(vals))

    @staticmethod
    def _fill_median(
        raw: list[tuple[float | None, float | None, float | None]],
    ) -> list[tuple[float, float, float]]:
        """将 None 值填充为该因子列的中位数。"""
        cols: list[list[float | None]] = [[], [], []]
        for r in raw:
            for j in range(3):
                cols[j].append(r[j])

        medians: list[float] = []
        for col in cols:
            valid = [v for v in col if v is not None]
            medians.append(float(np.median(valid)) if valid else 0.0)

        result: list[tuple[float, float, float]] = []
        for r in raw:
            result.append(tuple(  # type: ignore[arg-type]
                v if v is not None else medians[j]
                for j, v in enumerate(r)
            ))
        return result
