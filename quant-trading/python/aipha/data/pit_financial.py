"""
点时(Point-in-Time)财务数据框架

确保因子计算使用历史上"当时可得"的数据，避免前视偏差。

核心概念：
- report_date: 财报报告期（如2023Q3）
- announce_date: 实际公告日期（财报发布日）
- PIT查询: 给定查询日期，返回当时可用的最新数据
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class ReportPeriod(str, Enum):
    """财报周期"""

    Q1 = "Q1"  # 一季报
    Q2 = "Q2"  # 半年报
    Q3 = "Q3"  # 三季报
    Q4 = "Q4"  # 年报


@dataclass(frozen=True)
class ReportDate:
    """报告期标识"""

    year: int
    period: ReportPeriod

    def __str__(self) -> str:
        return f"{self.year}{self.period.value}"

    @classmethod
    def from_string(cls, s: str) -> "ReportDate":
        """从字符串解析"""
        year = int(s[:4])
        period = ReportPeriod(s[4:])
        return cls(year=year, period=period)

    def to_date(self) -> date:
        """转换为报告期末日期"""
        month_map = {
            ReportPeriod.Q1: 3,
            ReportPeriod.Q2: 6,
            ReportPeriod.Q3: 9,
            ReportPeriod.Q4: 12,
        }
        day_map = {
            ReportPeriod.Q1: 31,
            ReportPeriod.Q2: 30,
            ReportPeriod.Q3: 30,
            ReportPeriod.Q4: 31,
        }
        return date(self.year, month_map[self.period], day_map[self.period])

    def previous(self) -> "ReportDate":
        """上一个报告期"""
        if self.period == ReportPeriod.Q1:
            return ReportDate(self.year - 1, ReportPeriod.Q4)
        period_order = [ReportPeriod.Q1, ReportPeriod.Q2, ReportPeriod.Q3, ReportPeriod.Q4]
        idx = period_order.index(self.period)
        return ReportDate(self.year, period_order[idx - 1])


@dataclass
class FinancialStatement:
    """财务报表数据

    Attributes:
        symbol: 股票代码
        report_date: 报告期
        announce_date: 公告日期（PIT关键）
        statement_type: 报表类型
        data: 财务数据字典
    """

    symbol: str
    report_date: ReportDate
    announce_date: date  # 关键：实际公告日期
    statement_type: str  # income / balance / cashflow
    data: dict[str, Decimal | None] = field(default_factory=dict)

    def get(self, field_name: str, default: Decimal | None = None) -> Decimal | None:
        """获取字段值"""
        return self.data.get(field_name, default)


@dataclass
class FinancialIndicator:
    """财务指标（派生指标）"""

    symbol: str
    report_date: ReportDate
    announce_date: date
    indicators: dict[str, Decimal | None] = field(default_factory=dict)


def latest_available_financial(records, query_date: date):
    """Select the newest visible report period, then its latest visible revision."""
    visible = [record for record in records if record.announce_date <= query_date]
    return max(visible, key=lambda record: (record.report_date.to_date(), record.announce_date), default=None)


class PITFinancialProvider(ABC):
    """点时财务数据提供者接口

    所有财务数据源必须实现此接口，确保PIT语义。
    """

    @abstractmethod
    def get_statement_pit(
        self,
        symbol: str,
        query_date: date,
        statement_type: str,
    ) -> FinancialStatement | None:
        """获取给定日期时可用的最新财务报表

        Args:
            symbol: 股票代码
            query_date: 查询日期（模拟回测时的"当前日期"）
            statement_type: 报表类型

        Returns:
            当时可用的最新报表，若无则返回None
        """
        ...

    @abstractmethod
    def get_indicator_pit(
        self,
        symbol: str,
        query_date: date,
        indicator_names: list[str],
    ) -> FinancialIndicator | None:
        """获取给定日期时可用的财务指标"""
        ...

    @abstractmethod
    def get_statement_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        statement_type: str,
    ) -> list[FinancialStatement]:
        """获取历史报表（按公告日期过滤）"""
        ...


class PITValidator:
    """PIT校验器

    用于检测因子计算是否存在前视偏差。
    """

    def __init__(self):
        self._access_log: list[dict[str, Any]] = []

    def log_access(
        self,
        query_date: date,
        data_date: date,
        field_name: str,
        symbol: str,
    ) -> None:
        """记录数据访问"""
        self._access_log.append({
            "query_date": query_date,
            "data_date": data_date,
            "field": field_name,
            "symbol": symbol,
            "is_lookahead": data_date > query_date,
        })

    def check_lookahead(self) -> list[dict[str, Any]]:
        """检查前视偏差"""
        violations = [
            log for log in self._access_log if log["is_lookahead"]
        ]
        if violations:
            logger.warning(f"Found {len(violations)} lookahead bias violations")
        return violations

    def clear(self) -> None:
        """清除访问日志"""
        self._access_log.clear()

    def get_report(self) -> dict[str, Any]:
        """生成校验报告"""
        violations = self.check_lookahead()
        return {
            "total_accesses": len(self._access_log),
            "violation_count": len(violations),
            "violations": violations[:10],  # 只返回前10条
            "is_clean": len(violations) == 0,
        }


class InMemoryPITProvider(PITFinancialProvider):
    """内存PIT数据提供者

    用于测试和简单场景。
    """

    def __init__(self):
        self._statements: dict[str, list[FinancialStatement]] = {}
        self._indicators: dict[str, list[FinancialIndicator]] = {}

    def add_statement(self, statement: FinancialStatement) -> None:
        """添加报表数据"""
        key = f"{statement.symbol}_{statement.statement_type}"
        if key not in self._statements:
            self._statements[key] = []
        self._statements[key].append(statement)
        # 按公告日期排序
        self._statements[key].sort(key=lambda s: s.announce_date)

    def add_indicator(self, indicator: FinancialIndicator) -> None:
        """添加指标数据"""
        if indicator.symbol not in self._indicators:
            self._indicators[indicator.symbol] = []
        self._indicators[indicator.symbol].append(indicator)
        self._indicators[indicator.symbol].sort(key=lambda i: i.announce_date)

    def get_statement_pit(
        self,
        symbol: str,
        query_date: date,
        statement_type: str,
    ) -> FinancialStatement | None:
        """获取指定查询日期前已公告的最新财务报表（PIT视角）。"""
        key = f"{symbol}_{statement_type}"
        statements = self._statements.get(key, [])

        # 找到query_date之前公告的最新报表
        available = [s for s in statements if s.announce_date <= query_date]
        if not available:
            return None

        return latest_available_financial(available, query_date)

    def get_indicator_pit(
        self,
        symbol: str,
        query_date: date,
        indicator_names: list[str],
    ) -> FinancialIndicator | None:
        """获取指定查询日期前已公告的最新财务指标（PIT视角）。"""
        indicators = self._indicators.get(symbol, [])

        available = [i for i in indicators if i.announce_date <= query_date]
        if not available:
            return None

        latest = latest_available_financial(available, query_date)

        # 过滤请求的指标
        filtered_indicators = {
            k: v for k, v in latest.indicators.items()
            if k in indicator_names
        }

        return FinancialIndicator(
            symbol=symbol,
            report_date=latest.report_date,
            announce_date=latest.announce_date,
            indicators=filtered_indicators,
        )

    def get_statement_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        statement_type: str,
    ) -> list[FinancialStatement]:
        """获取指定时间范围内的历史财务报表列表。"""
        key = f"{symbol}_{statement_type}"
        statements = self._statements.get(key, [])

        return [
            s for s in statements
            if start_date <= s.announce_date <= end_date
        ]


class PITAwareFactorMixin:
    """PIT感知因子混入类

    为因子提供PIT数据访问能力。
    """

    _pit_provider: PITFinancialProvider | None = None
    _pit_validator: PITValidator | None = None

    def set_pit_provider(self, provider: PITFinancialProvider) -> None:
        """设置PIT数据提供者"""
        self._pit_provider = provider

    def set_pit_validator(self, validator: PITValidator) -> None:
        """设置PIT校验器"""
        self._pit_validator = validator

    def get_financial_value(
        self,
        symbol: str,
        query_date: date,
        field_name: str,
        statement_type: str = "income",
    ) -> Decimal | None:
        """获取财务字段值（PIT语义）"""
        if self._pit_provider is None:
            raise RuntimeError("PIT provider not set")

        statement = self._pit_provider.get_statement_pit(
            symbol, query_date, statement_type
        )

        if statement is None:
            return None

        value = statement.get(field_name)

        # 记录访问（用于校验）
        if self._pit_validator is not None:
            self._pit_validator.log_access(
                query_date=query_date,
                data_date=statement.announce_date,
                field_name=field_name,
                symbol=symbol,
            )

        return value


# 常用财务字段常量
class FinancialFields:
    """财务字段名称常量"""

    # 利润表
    REVENUE = "revenue"  # 营业收入
    OPERATING_PROFIT = "operating_profit"  # 营业利润
    NET_PROFIT = "net_profit"  # 净利润
    NET_PROFIT_DEDUCTED = "net_profit_deducted"  # 扣非净利润

    # 资产负债表
    TOTAL_ASSETS = "total_assets"  # 总资产
    TOTAL_LIABILITIES = "total_liabilities"  # 总负债
    TOTAL_EQUITY = "total_equity"  # 股东权益
    CASH = "cash"  # 货币资金

    # 现金流量表
    CASH_FROM_OPERATIONS = "cash_from_operations"  # 经营活动现金流
    CASH_FROM_INVESTING = "cash_from_investing"  # 投资活动现金流
    CASH_FROM_FINANCING = "cash_from_financing"  # 筹资活动现金流

    # 派生指标
    PE = "pe"  # 市盈率
    PB = "pb"  # 市净率
    PS = "ps"  # 市销率
    ROE = "roe"  # 净资产收益率
    ROA = "roa"  # 总资产收益率
    GROSS_MARGIN = "gross_margin"  # 毛利率
    NET_MARGIN = "net_margin"  # 净利率
