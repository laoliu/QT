"""因子基础框架 — 因子计算的抽象接口和数据结构。"""
from __future__ import annotations

import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class FactorData:
    """因子输入数据"""

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
    """因子计算结果"""

    symbol: str
    trade_date: date
    factor_name: str
    value: float
    rank: Optional[float] = None
    z_score: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Factor(ABC):
    """因子抽象基类

    所有因子必须实现:
    - name: 因子名称
    - compute(): 计算单个标的的因子值
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def compute(self, data: FactorData) -> FactorResult:
        raise NotImplementedError

    def compute_batch(self, data_list: list[FactorData]) -> list[FactorResult]:
        """批量计算因子值。默认逐个调用 compute()。"""
        return [self.compute(data) for data in data_list]

    def normalize(self, results: list[FactorResult], method: str = "rank") -> list[FactorResult]:
        """归一化因子值 (rank 或 zscore)。"""
        if not results:
            return []

        values = [r.value for r in results]

        if method == "rank":
            sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
            ranks = [0.0] * len(values)
            for rank_pos, idx in enumerate(sorted_indices):
                ranks[idx] = rank_pos / (len(values) - 1) if len(values) > 1 else 0.5
            return [
                FactorResult(
                    symbol=r.symbol, trade_date=r.trade_date,
                    factor_name=r.factor_name, value=r.value,
                    rank=ranks[i], z_score=r.z_score, metadata=r.metadata,
                )
                for i, r in enumerate(results)
            ]

        elif method == "zscore":
            if len(values) < 2:
                return [
                    FactorResult(
                        symbol=r.symbol, trade_date=r.trade_date,
                        factor_name=r.factor_name, value=r.value,
                        rank=r.rank, z_score=0.0, metadata=r.metadata,
                    )
                    for r in results
                ]
            mean = statistics.mean(values)
            stdev = statistics.stdev(values)
            z_scores = [0.0] * len(values) if stdev == 0 else [(v - mean) / stdev for v in values]
            return [
                FactorResult(
                    symbol=r.symbol, trade_date=r.trade_date,
                    factor_name=r.factor_name, value=r.value,
                    rank=r.rank, z_score=z_scores[i], metadata=r.metadata,
                )
                for i, r in enumerate(results)
            ]
        else:
            raise ValueError(f"未知的归一化方法: {method}")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
