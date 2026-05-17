"""
价值因子

包含：
- PEFactor: 市盈率因子
- PBFactor: 市净率因子
- PSFactor: 市销率因子
- ValuationPercentileFactor: 估值历史分位因子
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class PEFactor(Factor):
    """市盈率因子 (P/E Ratio)

    PE = 股价 / 每股收益

    注意：使用倒数（EP = E/P）作为因子值，
    因为更低的 PE 通常被认为是更好的价值。

    Attributes:
        use_ttm: 是否使用 TTM（滚动12个月）数据
        invert: 是否取倒数（默认 True，即使用 EP）
    """

    use_ttm: bool = True
    invert: bool = True

    @property
    def name(self) -> str:
        """返回因子名称：invert=True 时为 'EP'，否则为 'PE'。"""
        return "EP" if self.invert else "PE"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 PE/EP 因子"""
        if data.fundamentals is None:
            return self._invalid_result(data, "缺少财务数据")

        # 获取每股收益
        eps_key = "eps_ttm" if self.use_ttm else "eps"
        eps = data.fundamentals.get(eps_key)

        if eps is None or eps == 0:
            return self._invalid_result(data, f"缺少或无效的 {eps_key}")

        if data.close is None or data.close <= 0:
            return self._invalid_result(data, "缺少或无效的收盘价")

        pe = data.close / eps

        # 取倒数
        value = 1 / pe if self.invert and pe != 0 else pe

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={"pe": pe, "eps": eps},
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        """返回无效结果"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


@dataclass
class PBFactor(Factor):
    """市净率因子 (P/B Ratio)

    PB = 股价 / 每股净资产

    注意：使用倒数（BP = B/P）作为因子值。

    Attributes:
        invert: 是否取倒数（默认 True，即使用 BP）
    """

    invert: bool = True

    @property
    def name(self) -> str:
        """返回因子名称：invert=True 时为 'BP'，否则为 'PB'。"""
        return "BP" if self.invert else "PB"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 PB/BP 因子"""
        if data.fundamentals is None:
            return self._invalid_result(data, "缺少财务数据")

        bps = data.fundamentals.get("bps")  # 每股净资产

        if bps is None or bps <= 0:
            return self._invalid_result(data, "缺少或无效的每股净资产")

        if data.close is None or data.close <= 0:
            return self._invalid_result(data, "缺少或无效的收盘价")

        pb = data.close / bps
        value = 1 / pb if self.invert and pb != 0 else pb

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={"pb": pb, "bps": bps},
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


@dataclass
class PSFactor(Factor):
    """市销率因子 (P/S Ratio)

    PS = 总市值 / 营业收入

    注意：使用倒数（SP = S/P）作为因子值。

    Attributes:
        use_ttm: 是否使用 TTM 数据
        invert: 是否取倒数（默认 True）
    """

    use_ttm: bool = True
    invert: bool = True

    @property
    def name(self) -> str:
        """返回因子名称：invert=True 时为 'SP'，否则为 'PS'。"""
        return "SP" if self.invert else "PS"

    def compute(self, data: FactorData) -> FactorResult:
        """计算 PS/SP 因子"""
        if data.fundamentals is None:
            return self._invalid_result(data, "缺少财务数据")

        # 获取每股营收
        sps_key = "revenue_per_share_ttm" if self.use_ttm else "revenue_per_share"
        sps = data.fundamentals.get(sps_key)

        if sps is None or sps <= 0:
            return self._invalid_result(data, f"缺少或无效的 {sps_key}")

        if data.close is None or data.close <= 0:
            return self._invalid_result(data, "缺少或无效的收盘价")

        ps = data.close / sps
        value = 1 / ps if self.invert and ps != 0 else ps

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={"ps": ps, "sps": sps},
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


@dataclass
class ValuationPercentileFactor(Factor):
    """估值历史分位因子

    计算当前估值指标在历史分布中的百分位排名，并转换为因子值。
    分位越低（越便宜）→ 因子值越高（越有投资价值）。

    Attributes:
        metric: 估值指标名称，如 "pe", "pb", "ps"
        current_field: metadata 中当前值的字段名（默认根据 metric 自动推断）
        lookback_field: metadata 中历史序列的字段名（默认 "{metric}_history"）
        min_history: 最少历史数据点数（不足时返回 NaN）
        filter_positive: 是否过滤掉非正值（默认 True）
    """

    metric: str = "pe"
    current_field: Optional[str] = None
    lookback_field: Optional[str] = None
    min_history: int = 20
    filter_positive: bool = True

    @property
    def name(self) -> str:
        return f"{self.metric.upper()}_PERCENTILE"

    def _resolve_current_field(self) -> str:
        """解析当前值字段名"""
        if self.current_field:
            return self.current_field
        return f"{self.metric}_ttm"

    def _resolve_lookback_field(self) -> str:
        """解析历史序列字段名"""
        if self.lookback_field:
            return self.lookback_field
        return f"{self.metric}_history"

    def compute(self, data: FactorData) -> FactorResult:
        """计算估值分位因子"""
        meta = data.metadata or {}

        cur_field = self._resolve_current_field()
        current = meta.get(cur_field)
        if current is None:
            current = meta.get(self.metric)
        if current is None:
            return self._nan_result(data, f"缺少当前值: {cur_field}")

        lb_field = self._resolve_lookback_field()
        history = meta.get(lb_field)
        if history is None or not isinstance(history, (list, tuple)):
            return self._nan_result(data, f"缺少历史数据: {lb_field}")

        if self.filter_positive:
            history = [v for v in history if isinstance(v, (int, float)) and v > 0]
        else:
            history = [
                v for v in history
                if isinstance(v, (int, float)) and not math.isnan(v)
            ]

        if len(history) < self.min_history:
            return self._nan_result(
                data,
                f"历史数据不足: {len(history)} < {self.min_history}",
            )

        below = sum(1 for v in history if v < current)
        equal = sum(1 for v in history if v == current)
        n = len(history)
        percentile = (below + 0.5 * equal) / n
        factor_value = 1.0 - percentile

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=factor_value,
            metadata={
                "percentile": percentile,
                "current_value": current,
                "history_length": n,
            },
        )

    def _nan_result(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
