"""价值质量策略 — Magic Formula A股版

两层复合打分:
  - 价值复合 (PE 60% + PB 40%) × 0.30
  - 质量复合 (ROE 50% + 毛利率 30% + 低杠杆 20%) × 0.30
  - 动量 × 0.20
  - 低波 × 0.10
  - 小市值 × 0.10
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class ValueQualityConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 20
    stop_loss_pct: float = 0.18
    min_roe: float = 8.0
    max_pe: float = 80.0
    max_debt_to_assets: float = 70.0
    weight_value: float = 0.30
    weight_quality: float = 0.30
    weight_momentum: float = 0.20
    weight_volatility: float = 0.10
    weight_size: float = 0.10
    # Sub-weights for value composite
    value_pe_weight: float = 0.60
    value_pb_weight: float = 0.40
    # Sub-weights for quality composite
    quality_roe_weight: float = 0.50
    quality_margin_weight: float = 0.30
    quality_debt_weight: float = 0.20
    # Enhanced factor slots
    weight_piotroski: float = 0.0
    weight_valuation_pct: float = 0.0
    weight_accruals: float = 0.0
    piotroski_min_dims: int = 5
    momentum_lookback: int = 60
    volatility_lookback: int = 60


class ValueQualityStrategy(EquityRotationStrategy):
    name = "value_quality"

    def __init__(self, config: ValueQualityConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or ValueQualityConfig(), **kwargs)

    @property
    def config(self) -> ValueQualityConfig:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        cfg = self._config
        m = candidate.metadata
        roe = m.get("roe", None)
        pe = m.get("pe_ttm", None)
        debt = m.get("debt_to_assets", None)
        if roe is not None and roe < cfg.min_roe:
            return False
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        if debt is not None and debt > cfg.max_debt_to_assets:
            return False
        return True

    def _calc_piotroski(self, c: CandidateInfo) -> float | None:
        m = c.metadata
        dims = 0
        score = 0
        roa = m.get("roa", None)
        if roa is not None:
            dims += 1
            if roa > 0:
                score += 1
        roa_prev = m.get("roa_prev", None)
        if roa is not None and roa_prev is not None:
            dims += 1
            if roa > roa_prev:
                score += 1
        cfps = m.get("cfps", None)
        if cfps is not None:
            dims += 1
            if cfps > 0:
                score += 1
        eps = m.get("eps", None)
        if cfps is not None and eps is not None:
            dims += 1
            if cfps > eps:
                score += 1
        debt = m.get("debt_to_assets", None)
        debt_prev = m.get("debt_to_assets_prev", None)
        if debt is not None and debt_prev is not None:
            dims += 1
            if debt < debt_prev:
                score += 1
        cr = m.get("current_ratio", None)
        cr_prev = m.get("current_ratio_prev", None)
        if cr is not None and cr_prev is not None:
            dims += 1
            if cr > cr_prev:
                score += 1
        if dims < self._config.piotroski_min_dims:
            return None
        return score / dims if dims > 0 else None

    def _calc_valuation_pct(self, c: CandidateInfo) -> float | None:
        m = c.metadata
        pe_history = m.get("pe_history", None)
        pe = m.get("pe_ttm", None)
        if pe_history and pe is not None and len(pe_history) > 0:
            below = sum(1 for h in pe_history if h < pe)
            return below / len(pe_history)
        return None

    def _calc_accruals(self, c: CandidateInfo) -> float | None:
        m = c.metadata
        net_profit = m.get("net_profit", None)
        ocf = m.get("ocf", None)
        total_assets = m.get("total_assets", None)
        if net_profit is not None and ocf is not None and total_assets and total_assets > 0:
            return (net_profit - ocf) / total_assets
        return None

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        if not candidates:
            return []

        # 价值复合
        ep_vals = []
        bp_vals = []
        for c in candidates:
            pe = c.metadata.get("pe_ttm", None)
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
            pb = c.metadata.get("pb", None)
            bp_vals.append(1.0 / pb if pb and pb > 0 else None)

        ep_r = rank_normalize(ep_vals)
        bp_r = rank_normalize(bp_vals)
        value_composite = [cfg.value_pe_weight * ep_r[i] + cfg.value_pb_weight * bp_r[i]
                           for i in range(len(candidates))]

        # 质量复合
        roe_vals = [c.metadata.get("roe", None) for c in candidates]
        gm_vals = [c.metadata.get("grossprofit_margin", None) for c in candidates]
        low_debt = []
        for c in candidates:
            d = c.metadata.get("debt_to_assets", None)
            low_debt.append(1.0 - d if d is not None else None)
        roe_r = rank_normalize(roe_vals)
        gm_r = rank_normalize(gm_vals)
        ld_r = rank_normalize(low_debt)
        quality_composite = [cfg.quality_roe_weight * roe_r[i]
                             + cfg.quality_margin_weight * gm_r[i]
                             + cfg.quality_debt_weight * ld_r[i]
                             for i in range(len(candidates))]

        mom_vals = [self._calc_momentum(c.symbol, cfg.momentum_lookback, 5) for c in candidates]
        vol_vals = [self._calc_volatility(c.symbol, cfg.volatility_lookback) for c in candidates]
        size_vals = [-(c.metadata.get("total_mv", c.metadata.get("circ_mv", 1e18)))
                     for c in candidates]

        mom_r = rank_normalize(mom_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])
        size_r = rank_normalize(size_vals)

        # Enhanced factor slots
        pio_vals = None
        vp_vals = None
        acc_vals = None
        if cfg.weight_piotroski > 0:
            pio_vals = [self._calc_piotroski(c) for c in candidates]
        if cfg.weight_valuation_pct > 0:
            vp_vals = [self._calc_valuation_pct(c) for c in candidates]
        if cfg.weight_accruals > 0:
            acc_vals = [self._calc_accruals(c) for c in candidates]

        pio_r = rank_normalize(pio_vals) if pio_vals else [0.5] * len(candidates)
        vp_r = rank_normalize(vp_vals) if vp_vals else [0.5] * len(candidates)
        acc_r = rank_normalize([-v if v is not None else 0 for v in acc_vals]) if acc_vals else [0.5] * len(candidates)

        scored = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_value * value_composite[i]
                 + cfg.weight_quality * quality_composite[i]
                 + cfg.weight_momentum * mom_r[i]
                 + cfg.weight_volatility * vol_r[i]
                 + cfg.weight_size * size_r[i]
                 + cfg.weight_piotroski * pio_r[i]
                 + cfg.weight_valuation_pct * (1.0 - vp_r[i])
                 + cfg.weight_accruals * acc_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
