"""
UniversePolicy 模块

股票池策略：定义可交易标的集合的过滤规则。

设计原则（遵循 copilot-instructions.md）：
1. 任一策略/回测必须显式指定 UniversePolicy
2. 支持"自动剔除 + 手动剔除"两套入口
3. 报告输出剔除统计
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class ExclusionReason(str, Enum):
    """剔除原因枚举"""

    ST = "ST"  # ST/*ST
    DELISTED = "DELISTED"  # 退市整理/终止上市/暂停上市
    SUSPENDED = "SUSPENDED"  # 长期停牌
    MANUAL = "MANUAL"  # 手动黑名单
    FORCE_REDEEM = "FORCE_REDEEM"  # 已公告强赎（CB）
    NEAR_MATURITY = "NEAR_MATURITY"  # 近到期（CB）
    NOT_INDEX_MEMBER = "NOT_INDEX_MEMBER"  # 非指数成分股


@dataclass(frozen=True, slots=True)
class ExclusionRecord:
    """剔除记录

    Attributes:
        ts_code: 股票代码
        name: 股票名称
        reason: 剔除原因
        effective_date: 生效日期
        detail: 详细说明
    """

    ts_code: str
    name: str
    reason: ExclusionReason
    effective_date: date
    detail: str = ""


@dataclass
class FilterResult:
    """过滤结果

    Attributes:
        tradable: 可交易股票列表
        excluded: 被剔除的记录列表
        total_count: 输入总数
        trade_date: 交易日期
    """

    tradable: list[dict[str, Any]]
    excluded: list[ExclusionRecord]
    total_count: int
    trade_date: date

    @property
    def tradable_count(self) -> int:
        """可交易数量"""
        return len(self.tradable)

    @property
    def excluded_count(self) -> int:
        """剔除数量"""
        return len(self.excluded)

    def get_exclusion_stats(self) -> dict[ExclusionReason, int]:
        """按原因统计剔除数量"""
        stats: dict[ExclusionReason, int] = {}
        for record in self.excluded:
            stats[record.reason] = stats.get(record.reason, 0) + 1
        return stats


class ExclusionRule(ABC):
    """剔除规则抽象基类"""

    @abstractmethod
    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """
        对股票列表应用剔除规则

        Args:
            stocks: 股票信息列表
            trade_date: 交易日期

        Returns:
            被剔除的记录列表
        """
        raise NotImplementedError


class UniversePolicy(ABC):
    """股票池策略抽象基类"""

    @abstractmethod
    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> FilterResult:
        """
        过滤股票池

        Args:
            stocks: 全量股票信息列表
            trade_date: 交易日期

        Returns:
            过滤结果
        """
        raise NotImplementedError


@dataclass
class CompositeUniversePolicy(UniversePolicy):
    """组合策略：依次应用多个剔除规则

    Attributes:
        rules: 剔除规则列表（按顺序应用）
    """

    rules: list[ExclusionRule] = field(default_factory=list)

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> FilterResult:
        """应用所有规则过滤股票池"""
        excluded_codes: set[str] = set()
        excluded_records: list[ExclusionRecord] = []

        # 依次应用每个规则
        for rule in self.rules:
            records = rule.filter(stocks, trade_date)
            for record in records:
                # 避免重复剔除
                if record.ts_code not in excluded_codes:
                    excluded_codes.add(record.ts_code)
                    excluded_records.append(record)

        # 筛选可交易股票
        tradable = [s for s in stocks if s["ts_code"] not in excluded_codes]

        return FilterResult(
            tradable=tradable,
            excluded=excluded_records,
            total_count=len(stocks),
            trade_date=trade_date,
        )
