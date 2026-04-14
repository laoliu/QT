"""高阶质量因子 — F-Score / 应计质量 / 负债质量 / 盈利稳定性"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


def _safe_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


@dataclass
class PiotroskiFScoreFactor(Factor):
    """Piotroski F-Score (0-9) 综合财务质量因子"""
    min_score: Optional[int] = None

    @property
    def name(self) -> str:
        return "PIOTROSKI_F"

    def compute(self, data: FactorData) -> FactorResult:
        fund = data.fundamentals or {}
        score = 0

        # 盈利 (4 items)
        roa = _safe_float(fund.get("roa"))
        if roa is not None and roa > 0:
            score += 1
        cfo = _safe_float(fund.get("ocfps"))
        if cfo is not None and cfo > 0:
            score += 1
        roa_prev = _safe_float(fund.get("roa_prev"))
        if roa is not None and roa_prev is not None and roa > roa_prev:
            score += 1
        if cfo is not None and roa is not None and cfo > roa:
            score += 1

        # 杠杆 (3 items)
        lever = _safe_float(fund.get("debt_to_assets"))
        lever_prev = _safe_float(fund.get("debt_to_assets_prev"))
        if lever is not None and lever_prev is not None and lever < lever_prev:
            score += 1
        cr = _safe_float(fund.get("current_ratio"))
        cr_prev = _safe_float(fund.get("current_ratio_prev"))
        if cr is not None and cr_prev is not None and cr > cr_prev:
            score += 1
        shares = _safe_float(fund.get("total_shares"))
        shares_prev = _safe_float(fund.get("total_shares_prev"))
        if shares is not None and shares_prev is not None and shares <= shares_prev:
            score += 1

        # 经营效率 (2 items)
        gm = _safe_float(fund.get("gross_margin"))
        gm_prev = _safe_float(fund.get("gross_margin_prev"))
        if gm is not None and gm_prev is not None and gm > gm_prev:
            score += 1
        at = _safe_float(fund.get("asset_turnover"))
        at_prev = _safe_float(fund.get("asset_turnover_prev"))
        if at is not None and at_prev is not None and at > at_prev:
            score += 1

        return FactorResult(
            symbol=data.symbol, factor_name=self.name, value=float(score),
            timestamp=data.timestamp, metadata={"score": score},
        )

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=float("nan"), timestamp=data.timestamp,
                            metadata={"error": reason})


@dataclass
class AccrualsQualityFactor(Factor):
    """应计质量因子: (净利润 - 经营现金流) / 总资产，越低越好"""
    use_per_share: bool = True

    @property
    def name(self) -> str:
        return "ACCRUALS_QUALITY"

    def compute(self, data: FactorData) -> FactorResult:
        fund = data.fundamentals or {}
        if self.use_per_share:
            ni = _safe_float(fund.get("eps"))
            cfo = _safe_float(fund.get("ocfps"))
        else:
            ni = _safe_float(fund.get("net_income"))
            cfo = _safe_float(fund.get("operating_cf"))
        ta = _safe_float(fund.get("total_assets"))

        if ni is None or cfo is None or ta is None or abs(ta) < 1e-10:
            return self._invalid(data, "missing_data")

        accrual = (ni - cfo) / ta
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=-accrual, timestamp=data.timestamp)

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=float("nan"), timestamp=data.timestamp,
                            metadata={"error": reason})


@dataclass
class DebtQualityFactor(Factor):
    """负债质量综合得分"""
    w_current: float = 0.35
    w_quick: float = 0.35
    w_debt: float = 0.30

    @property
    def name(self) -> str:
        return "DEBT_QUALITY"

    def compute(self, data: FactorData) -> FactorResult:
        fund = data.fundamentals or {}
        cr = _safe_float(fund.get("current_ratio"))
        qr = _safe_float(fund.get("quick_ratio"))
        da = _safe_float(fund.get("debt_to_assets"))

        parts = []
        if cr is not None:
            parts.append(self.w_current * min(cr / 2.0, 1.0))
        if qr is not None:
            parts.append(self.w_quick * min(qr / 1.5, 1.0))
        if da is not None:
            parts.append(self.w_debt * max(0, 1 - da))

        if not parts:
            return self._invalid(data, "no_debt_metrics")
        value = sum(parts) / (self.w_current + self.w_quick + self.w_debt)
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=value, timestamp=data.timestamp)

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=float("nan"), timestamp=data.timestamp,
                            metadata={"error": reason})


@dataclass
class EarningsStabilityFactor(Factor):
    """盈利稳定性因子: 历史 ROE/EPS 的变异系数 (CV)，越低表示越稳定"""
    min_periods: int = 3
    metric: str = "roe"
    history_field: str = "roe_history"

    @property
    def name(self) -> str:
        return f"EARNINGS_STABILITY_{self.metric.upper()}"

    def compute(self, data: FactorData) -> FactorResult:
        fund = data.fundamentals or {}
        hist = fund.get(self.history_field)
        if hist is None:
            return self._invalid(data, "no_history")
        if isinstance(hist, dict):
            series = [float(v) for v in hist.values() if v is not None]
        elif isinstance(hist, (list, tuple)):
            series = [float(v) for v in hist if v is not None]
        else:
            return self._invalid(data, "bad_history_type")
        return self._compute_from_series(data, series)

    def _compute_from_series(self, data: FactorData, series: list[float]) -> FactorResult:
        if len(series) < self.min_periods:
            return self._invalid(data, f"need {self.min_periods} periods")
        mu = statistics.mean(series)
        if abs(mu) < 1e-10:
            return self._invalid(data, "mean_near_zero")
        sigma = statistics.stdev(series)
        cv = sigma / abs(mu)
        # 稳定性分数: 1 / (1 + cv)
        score = 1.0 / (1.0 + cv)
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=score, timestamp=data.timestamp,
                            metadata={"cv": cv, "n": len(series)})

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(symbol=data.symbol, factor_name=self.name,
                            value=float("nan"), timestamp=data.timestamp,
                            metadata={"error": reason})
