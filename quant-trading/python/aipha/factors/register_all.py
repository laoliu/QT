"""批量注册所有 aipha 因子到 FactorRegistry

导入本模块即可完成注册。
"""
from aipha.factors.registry import FactorRegistry

_reg = FactorRegistry()

# ── 9 大类因子 ──

# momentum
from aipha.factors.momentum import (
    ReturnMomentumFactor, VolAdjMomentumFactor, ReversalFactor, IndustryRelMomentumFactor,
)
_reg.register_class("return_momentum", ReturnMomentumFactor, category="momentum", description="N日收益率动量")
_reg.register_class("vol_adj_momentum", VolAdjMomentumFactor, category="momentum", description="波动调整动量")
_reg.register_class("short_reversal", ReversalFactor, category="momentum", description="短期反转")
_reg.register_class("industry_rel_momentum", IndustryRelMomentumFactor, category="momentum", description="行业相对动量")

# value
from aipha.factors.value import PEFactor, PBFactor, EPFactor, CFPFactor, EVEBITDAFactor
_reg.register_class("ep", PEFactor, category="value", description="市盈率倒数(EP)")
_reg.register_class("bp", PBFactor, category="value", description="市净率倒数(BP)")
_reg.register_class("earnings_price", EPFactor, category="value", description="Earnings-to-Price")
_reg.register_class("cashflow_price", CFPFactor, category="value", description="现金流收益率")
_reg.register_class("ebitda_ev", EVEBITDAFactor, category="value", description="EV/EBITDA倒数")

# quality
from aipha.factors.quality import ROEFactor, ROAFactor, GrossMarginFactor, AccrualFactor, AssetTurnoverFactor
_reg.register_class("roe_ttm", ROEFactor, category="quality", description="ROE TTM")
_reg.register_class("roa_ttm", ROAFactor, category="quality", description="ROA TTM")
_reg.register_class("gross_margin", GrossMarginFactor, category="quality", description="毛利率")
_reg.register_class("accrual", AccrualFactor, category="quality", description="应计项目")
_reg.register_class("asset_turnover", AssetTurnoverFactor, category="quality", description="总资产周转率")

# volatility
from aipha.factors.volatility import VolatilityFactor, DownsideVolFactor, IdioVolFactor, RangeVolFactor
_reg.register_class("realized_vol", VolatilityFactor, category="volatility", description="N日波动率")
_reg.register_class("downside_vol", DownsideVolFactor, category="volatility", description="下行波动率")
_reg.register_class("idio_vol", IdioVolFactor, category="volatility", description="特质波动率")
_reg.register_class("range_vol", RangeVolFactor, category="volatility", description="Parkinson波动率")

# size
from aipha.factors.size import MarketCapFactor, FloatCapFactor, NonLinearSizeFactor
_reg.register_class("ln_market_cap", MarketCapFactor, category="size", description="对数总市值")
_reg.register_class("ln_float_cap", FloatCapFactor, category="size", description="对数流通市值")
_reg.register_class("nlsize", NonLinearSizeFactor, category="size", description="非线性市值")

# liquidity
from aipha.factors.liquidity import AmihudIlliquidityFactor, TurnoverMomentumFactor, SpreadFactor, ZeroTradingDaysFactor
_reg.register_class("amihud_illiq", AmihudIlliquidityFactor, category="liquidity", description="Amihud非流动性")
_reg.register_class("turnover_momentum", TurnoverMomentumFactor, category="liquidity", description="换手率动量")
_reg.register_class("bid_ask_spread", SpreadFactor, category="liquidity", description="买卖价差估计")
_reg.register_class("zero_trading_days", ZeroTradingDaysFactor, category="liquidity", description="零成交天数占比")

# dividend
from aipha.factors.dividend import DividendYieldFactor, DividendGrowthFactor, PayoutRatioFactor, DividendStabilityFactor
_reg.register_class("dividend_yield", DividendYieldFactor, category="dividend", description="股息率")
_reg.register_class("dividend_growth", DividendGrowthFactor, category="dividend", description="股息增长率")
_reg.register_class("payout_ratio", PayoutRatioFactor, category="dividend", description="分红率")
_reg.register_class("dividend_stability", DividendStabilityFactor, category="dividend", description="股息稳定性")

# growth
from aipha.factors.growth import RevenueGrowthFactor, EarningsGrowthFactor, SUEFactor, ROEChangeFactor, SalesAccelerationFactor
_reg.register_class("revenue_growth_yoy", RevenueGrowthFactor, category="growth", description="营收同比增长")
_reg.register_class("earnings_growth_yoy", EarningsGrowthFactor, category="growth", description="净利润同比增长")
_reg.register_class("sue", SUEFactor, category="growth", description="标准化意外盈利")
_reg.register_class("roe_change", ROEChangeFactor, category="growth", description="ROE环比变化")
_reg.register_class("sales_acceleration", SalesAccelerationFactor, category="growth", description="营收加速度")

# technical
from aipha.factors.technical import (
    RSIFactor, MACDFactor, BollingerPctFactor, KDJFactor, WilliamsRFactor, ATRFactor, OBVFactor,
)
_reg.register_class("rsi", RSIFactor, category="technical", description="RSI指标")
_reg.register_class("macd_hist", MACDFactor, category="technical", description="MACD柱状图")
_reg.register_class("bollinger_pct", BollingerPctFactor, category="technical", description="%B Bollinger")
_reg.register_class("kdj_j", KDJFactor, category="technical", description="KDJ J值")
_reg.register_class("williams_r", WilliamsRFactor, category="technical", description="Williams %R")
_reg.register_class("atr_ratio", ATRFactor, category="technical", description="相对ATR")
_reg.register_class("obv_momentum", OBVFactor, category="technical", description="OBV动量")
