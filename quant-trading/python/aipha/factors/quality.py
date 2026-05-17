"""质量因子模块

基于 PIT 财务数据的盈利质量因子，A 股多因子体系核心组件。

包含：
- ROEFactor: 净资产收益率（高盈利能力溢价）
- ROAFactor: 总资产收益率
- GrossMarginFactor: 毛利率（业务竞争力）
- NetMarginFactor: 净利率
- OperatingCashFlowQualityFactor: 经营现金流/净利润（盈利真实性）

所有因子继承 Factor ABC + PITAwareFactorMixin，确保 PIT 语义。

数据依赖：
- PITFinancialProvider（通过 set_pit_provider() 注入）
- fina_indicator: roe, roa, grossprofit_margin, netprofit_margin
- income + cashflow: 经营现金流/净利润比

设计决策：
- 因子方向统一：值越大 = 质量越高 = 越优（无需 invert）
- 缺失值返回 NaN + invalid 标记，由上游过滤
- 支持从 FactorData.metadata 直接读取（无 PIT provider 时的降级路径）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from aipha.data.pit_financial import (
    FinancialFields,
    PITAwareFactorMixin,
    PITFinancialProvider,
    PITValidator,
)
from aipha.factors.base import Factor, FactorData, FactorResult


def _decimal_to_float(val: Decimal | None) -> float | None:
    """Decimal → float，None 保持 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ROE 因子
# ---------------------------------------------------------------------------

@dataclass
class ROEFactor(Factor, PITAwareFactorMixin):
    """净资产收益率因子 (ROE)

    高 ROE 公司通常具备持续盈利能力，A 股中 ROE 因子长期有效。

    数据来源优先级：
    1. PIT Provider → fina_indicator.roe
    2. FactorData.metadata["roe"]（降级）

    Attributes:
        use_weighted: 使用加权 ROE (roe_waa) 还是摊薄 ROE (roe)
        min_roe: ROE 下限过滤（低于此值返回 NaN，用于剔除亏损公司）
    """

    use_weighted: bool = False
    min_roe: float | None = None

    @property
    def name(self) -> str:
        """因子名称: ``ROE_WAA`` 或 ``ROE``。"""
        return "ROE_WAA" if self.use_weighted else "ROE"

    def compute(self, data: FactorData) -> FactorResult:
        """计算净资产收益率。

        Args:
            data: 因子输入数据（PIT provider 或 metadata["roe"]）

        Returns:
            FactorResult，value 为 ROE 百分比；数据缺失或低于下限时 value=NaN。
        """
        roe_val = self._get_roe(data)

        if roe_val is None:
            return self._invalid(data, "无 ROE 数据")

        if self.min_roe is not None and roe_val < self.min_roe:
            return self._invalid(data, f"ROE {roe_val:.2f} < 下限 {self.min_roe}")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=roe_val,
            metadata={"roe": roe_val},
        )

    def _get_roe(self, data: FactorData) -> float | None:
        """获取 ROE 值（PIT 优先，metadata 降级）"""
        # PIT 路径
        if self._pit_provider is not None:
            field_name = "roe_waa" if self.use_weighted else FinancialFields.ROE
            val = self.get_financial_value(
                data.symbol, data.trade_date, field_name, "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        # 降级路径
        roe = data.metadata.get("roe") or data.metadata.get("roe_waa")
        if roe is not None:
            try:
                return float(roe)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# ROA 因子
# ---------------------------------------------------------------------------

@dataclass
class ROAFactor(Factor, PITAwareFactorMixin):
    """总资产收益率因子 (ROA)

    衡量公司利用全部资产创造利润的能力。
    """

    @property
    def name(self) -> str:
        """因子名称: ``ROA``。"""
        return "ROA"

    def compute(self, data: FactorData) -> FactorResult:
        """计算总资产收益率。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为 ROA 百分比；数据缺失时 value=NaN。
        """
        roa_val = self._get_roa(data)

        if roa_val is None:
            return self._invalid(data, "无 ROA 数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=roa_val,
            metadata={"roa": roa_val},
        )

    def _get_roa(self, data: FactorData) -> float | None:
        if self._pit_provider is not None:
            val = self.get_financial_value(
                data.symbol, data.trade_date, FinancialFields.ROA, "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        roa = data.metadata.get("roa")
        if roa is not None:
            try:
                return float(roa)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 毛利率因子
# ---------------------------------------------------------------------------

@dataclass
class GrossMarginFactor(Factor, PITAwareFactorMixin):
    """毛利率因子

    高毛利率反映业务竞争力和定价权。

    数据来源：fina_indicator.grossprofit_margin（百分比）
    """

    @property
    def name(self) -> str:
        """因子名称: ``GROSS_MARGIN``。"""
        return "GROSS_MARGIN"

    def compute(self, data: FactorData) -> FactorResult:
        """计算毛利率因子。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为毛利率百分比；数据缺失时 value=NaN。
        """
        gm = self._get_gross_margin(data)

        if gm is None:
            return self._invalid(data, "无毛利率数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=gm,
            metadata={"gross_margin": gm},
        )

    def _get_gross_margin(self, data: FactorData) -> float | None:
        if self._pit_provider is not None:
            val = self.get_financial_value(
                data.symbol, data.trade_date, FinancialFields.GROSS_MARGIN, "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        gm = data.metadata.get("gross_margin") or data.metadata.get("grossprofit_margin")
        if gm is not None:
            try:
                return float(gm)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 净利率因子
# ---------------------------------------------------------------------------

@dataclass
class NetMarginFactor(Factor, PITAwareFactorMixin):
    """净利率因子

    反映公司最终盈利能力占营收的比例。
    """

    @property
    def name(self) -> str:
        """因子名称: ``NET_MARGIN``。"""
        return "NET_MARGIN"

    def compute(self, data: FactorData) -> FactorResult:
        """计算净利率因子。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为净利率百分比；数据缺失时 value=NaN。
        """
        nm = self._get_net_margin(data)

        if nm is None:
            return self._invalid(data, "无净利率数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=nm,
            metadata={"net_margin": nm},
        )

    def _get_net_margin(self, data: FactorData) -> float | None:
        if self._pit_provider is not None:
            val = self.get_financial_value(
                data.symbol, data.trade_date, FinancialFields.NET_MARGIN, "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        nm = data.metadata.get("net_margin") or data.metadata.get("netprofit_margin")
        if nm is not None:
            try:
                return float(nm)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 经营现金流质量因子
# ---------------------------------------------------------------------------

@dataclass
class OperatingCashFlowQualityFactor(Factor, PITAwareFactorMixin):
    """经营现金流质量因子

    CFO / NetProfit，衡量盈利的"含金量"。
    - 值 > 1: 经营现金流超过净利润，盈利质量高
    - 值 < 0.5: 大量利润未转化为现金流，可能存在应收账款膨胀

    数据来源：
    - cashflow.n_cashflow_act（经营活动现金流净额）
    - income.n_income_attr_p（归母净利润）
    如果 PIT provider 不可用，从 metadata 读取 cfps / eps 近似。
    """

    @property
    def name(self) -> str:
        """因子名称: ``CFO_QUALITY``。"""
        return "CFO_QUALITY"

    def compute(self, data: FactorData) -> FactorResult:
        """计算经营现金流质量比 (CFO / NetProfit)。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为 CFO/NP 比率；数据缺失时 value=NaN。
        """
        ratio = self._get_cfo_ratio(data)

        if ratio is None:
            return self._invalid(data, "无现金流/净利润数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=ratio,
            metadata={"cfo_to_net_profit": ratio},
        )

    def _get_cfo_ratio(self, data: FactorData) -> float | None:
        # PIT 路径：使用报表数据
        if self._pit_provider is not None:
            cfo_val = self.get_financial_value(
                data.symbol, data.trade_date,
                FinancialFields.CASH_FROM_OPERATIONS, "cashflow"
            )
            np_val = self.get_financial_value(
                data.symbol, data.trade_date,
                FinancialFields.NET_PROFIT, "income"
            )
            cfo_f = _decimal_to_float(cfo_val)
            np_f = _decimal_to_float(np_val)
            if cfo_f is not None and np_f is not None and np_f != 0:
                return cfo_f / np_f

        # 降级路径：用 cfps / eps 近似
        cfps = data.metadata.get("cfps")
        eps = data.metadata.get("eps")
        if cfps is not None and eps is not None:
            try:
                cfps_f = float(cfps)
                eps_f = float(eps)
                if eps_f != 0:
                    return cfps_f / eps_f
            except (TypeError, ValueError):
                pass

        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
