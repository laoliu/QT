"""核心因子实现 — 价值/质量/动量/波动率/规模。

每个因子类实现 Factor ABC，注册到 FactorRegistry。
"""
from __future__ import annotations

from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.factors.registry import _registry


# ============================================================================
# 价值因子
# ============================================================================

class PEFactor(Factor):
    """市盈率因子 (P/E)"""

    @property
    def name(self) -> str:
        return "pe"

    def compute(self, data: FactorData) -> FactorResult:
        pe = data.fundamentals.get("pe_ttm", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=pe,
        )


class PBFactor(Factor):
    """市净率因子 (P/B)"""

    @property
    def name(self) -> str:
        return "pb"

    def compute(self, data: FactorData) -> FactorResult:
        pb = data.fundamentals.get("pb", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=pb,
        )


class PSFactor(Factor):
    """市销率因子 (P/S)"""

    @property
    def name(self) -> str:
        return "ps"

    def compute(self, data: FactorData) -> FactorResult:
        ps = data.fundamentals.get("ps_ttm", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=ps,
        )


class DividendYieldFactor(Factor):
    """股息率因子"""

    @property
    def name(self) -> str:
        return "dividend_yield"

    def compute(self, data: FactorData) -> FactorResult:
        dy = data.fundamentals.get("dv_ttm", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=dy,
        )


# ============================================================================
# 质量因子
# ============================================================================

class ROEFactor(Factor):
    """净资产收益率"""

    @property
    def name(self) -> str:
        return "roe"

    def compute(self, data: FactorData) -> FactorResult:
        roe = data.fundamentals.get("roe", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=roe,
        )


class ROAFactor(Factor):
    """总资产收益率"""

    @property
    def name(self) -> str:
        return "roa"

    def compute(self, data: FactorData) -> FactorResult:
        roa = data.fundamentals.get("roa", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=roa,
        )


class GrossMarginFactor(Factor):
    """毛利率"""

    @property
    def name(self) -> str:
        return "gross_margin"

    def compute(self, data: FactorData) -> FactorResult:
        gm = data.fundamentals.get("grossprofit_margin", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=gm,
        )


# ============================================================================
# 动量因子
# ============================================================================

class ReturnMomentumFactor(Factor):
    """收益率动量因子"""

    def __init__(self, lookback: int = 20):
        self._lookback = lookback

    @property
    def name(self) -> str:
        return "momentum"

    def compute(self, data: FactorData) -> FactorResult:
        if data.returns and len(data.returns) >= self._lookback:
            window = data.returns[-self._lookback:]
            cum_ret = 1.0
            for r in window:
                cum_ret *= (1 + r)
            momentum = cum_ret - 1.0
        else:
            momentum = 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=momentum,
        )


# ============================================================================
# 波动率因子
# ============================================================================

class VolatilityFactor(Factor):
    """已实现波动率"""

    def __init__(self, lookback: int = 20):
        self._lookback = lookback

    @property
    def name(self) -> str:
        return "volatility"

    def compute(self, data: FactorData) -> FactorResult:
        if data.returns and len(data.returns) >= self._lookback:
            import statistics
            vol = statistics.stdev(data.returns[-self._lookback:])
        else:
            vol = 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=vol,
        )


# ============================================================================
# 规模因子
# ============================================================================

class MarketCapFactor(Factor):
    """流通市值因子"""

    @property
    def name(self) -> str:
        return "market_cap"

    def compute(self, data: FactorData) -> FactorResult:
        mc = data.fundamentals.get("circ_mv", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=mc,
        )


class TurnoverRateFactor(Factor):
    """换手率因子"""

    @property
    def name(self) -> str:
        return "turnover_rate"

    def compute(self, data: FactorData) -> FactorResult:
        tr = data.fundamentals.get("turnover_rate", 0.0) if data.fundamentals else 0.0
        return FactorResult(
            symbol=data.symbol, trade_date=data.trade_date,
            factor_name=self.name, value=tr,
        )


# ============================================================================
# 注册内置因子
# ============================================================================

def _register_builtin_factors() -> None:
    _registry.register_class("pe", PEFactor, category="value", description="市盈率因子 (P/E)")
    _registry.register_class("pb", PBFactor, category="value", description="市净率因子 (P/B)")
    _registry.register_class("ps", PSFactor, category="value", description="市销率因子 (P/S)")
    _registry.register_class("dividend_yield", DividendYieldFactor, category="value", description="股息率因子")
    _registry.register_class("roe", ROEFactor, category="quality", description="净资产收益率")
    _registry.register_class("roa", ROAFactor, category="quality", description="总资产收益率")
    _registry.register_class("gross_margin", GrossMarginFactor, category="quality", description="毛利率")
    _registry.register_class("momentum", ReturnMomentumFactor, category="momentum", description="收益率动量")
    _registry.register_class("volatility", VolatilityFactor, category="volatility", description="波动率")
    _registry.register_class("market_cap", MarketCapFactor, category="size", description="流通市值")
    _registry.register_class("turnover_rate", TurnoverRateFactor, category="size", description="换手率")


_register_builtin_factors()
