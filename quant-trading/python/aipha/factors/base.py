"""
因子基础框架

提供因子计算的抽象接口和数据结构。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class FactorData:
    """因子输入数据

    Attributes:
        symbol: 标的代码
        trade_date: 交易日期
        close: 收盘价（可选）
        volume: 成交量（可选）
        returns: 收益率序列（可选）
        historical_close: 历史收盘价 {date: price}（可选）
        fundamentals: 财务数据（可选）
        metadata: 其他元数据
    """

    symbol: str
    trade_date: date
    close: Optional[float] = None
    volume: Optional[float] = None
    returns: Optional[list[float]] = None
    historical_close: Optional[dict[date, float]] = None
    fundamentals: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorResult:
    """因子计算结果

    Attributes:
        symbol: 标的代码
        trade_date: 交易日期
        factor_name: 因子名称
        value: 因子值
        rank: 因子排名（可选，0-1 归一化）
        z_score: 因子 Z-Score（可选）
        metadata: 其他元数据
    """

    symbol: str
    trade_date: date
    factor_name: str
    value: float
    rank: Optional[float] = None
    z_score: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Factor(ABC):
    """因子抽象基类

    所有因子必须实现：
    - name: 因子名称
    - compute(): 计算单个标的的因子值
    - compute_batch(): 批量计算因子值（可选覆盖以优化性能）
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """因子名称"""
        raise NotImplementedError

    @abstractmethod
    def compute(self, data: FactorData) -> FactorResult:
        """
        计算单个标的的因子值

        Args:
            data: 因子输入数据

        Returns:
            FactorResult: 因子计算结果
        """
        raise NotImplementedError

    def compute_batch(
        self, data_list: list[FactorData]
    ) -> list[FactorResult]:
        """
        批量计算因子值

        默认实现：逐个调用 compute()
        子类可覆盖以优化性能

        Args:
            data_list: 因子输入数据列表

        Returns:
            因子结果列表
        """
        return [self.compute(data) for data in data_list]

    def normalize(
        self, results: list[FactorResult], method: str = "rank"
    ) -> list[FactorResult]:
        """
        归一化因子值

        Args:
            results: 因子结果列表
            method: 归一化方法（rank/zscore）

        Returns:
            归一化后的因子结果列表
        """
        if not results:
            return []

        values = [r.value for r in results]

        if method == "rank":
            # 排名归一化（0-1）— 端点法: rank/(n-1)，范围 [0,1]
            # 注: composite.py RankNormalizer 使用中点法 (rank+0.5)/n，范围 ~(0,1)
            # 两者设计意图不同: base.py 用于简单因子排序，composite.py 用于组合因子流水线
            sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
            ranks = [0.0] * len(values)
            for rank, idx in enumerate(sorted_indices):
                ranks[idx] = rank / (len(values) - 1) if len(values) > 1 else 0.5

            return [
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    rank=ranks[i],
                    z_score=r.z_score,
                    metadata=r.metadata,
                )
                for i, r in enumerate(results)
            ]

        elif method == "zscore":
            # Z-Score 归一化
            import statistics

            if len(values) < 2:
                return [
                    FactorResult(
                        symbol=r.symbol,
                        trade_date=r.trade_date,
                        factor_name=r.factor_name,
                        value=r.value,
                        rank=r.rank,
                        z_score=0.0,
                        metadata=r.metadata,
                    )
                    for r in results
                ]

            mean = statistics.mean(values)
            stdev = statistics.stdev(values)
            if stdev == 0:
                z_scores = [0.0] * len(values)
            else:
                z_scores = [(v - mean) / stdev for v in values]

            return [
                FactorResult(
                    symbol=r.symbol,
                    trade_date=r.trade_date,
                    factor_name=r.factor_name,
                    value=r.value,
                    rank=r.rank,
                    z_score=z_scores[i],
                    metadata=r.metadata,
                )
                for i, r in enumerate(results)
            ]

        else:
            raise ValueError(f"未知的归一化方法: {method}")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
