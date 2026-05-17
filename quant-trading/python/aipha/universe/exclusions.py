"""
剔除规则实现

包含：
- STExclusionRule: ST/*ST 股票剔除
- DelistedExclusionRule: 退市/暂停上市剔除
- SuspendedExclusionRule: 长期停牌剔除
- ManualExclusionRule: 手动黑名单剔除
- load_manual_exclusion_from_config: 从配置文件加载手动剔除规则
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from aipha.universe.policy import ExclusionReason, ExclusionRecord, ExclusionRule


@dataclass
class STExclusionRule(ExclusionRule):
    """ST/*ST 股票剔除规则

    剔除条件：is_st=True 或名称包含 ST/*ST
    """

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """筛选并剔除 ST/*ST 股票。

        通过 is_st 标志或股票简称中包含 'ST' 进行识别。

        Args:
            stocks: 待筛选的股票信息列表，每项包含 ts_code、name、is_st 等字段
            trade_date: 当前交易日期

        Returns:
            被剔除记录列表
        """
        excluded = []
        for stock in stocks:
            is_st = stock.get("is_st", False)
            name = stock.get("name", "")

            # 检查 is_st 标志或名称包含 ST
            if is_st or "ST" in name.upper():
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=name,
                        reason=ExclusionReason.ST,
                        effective_date=trade_date,
                        detail="ST/*ST股票",
                    )
                )
        return excluded


@dataclass
class DelistedExclusionRule(ExclusionRule):
    """退市/暂停上市剔除规则

    剔除条件：list_status != 'L'
    - D: 退市
    - P: 暂停上市
    """

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """筛选并剔除退市/暂停上市股票。

        通过 list_status 字段判断：D=退市，P=暂停上市。

        Args:
            stocks: 待筛选的股票信息列表
            trade_date: 当前交易日期

        Returns:
            被剔除记录列表
        """
        excluded = []
        for stock in stocks:
            list_status = stock.get("list_status", "L")

            if list_status != "L":
                status_desc = {"D": "退市", "P": "暂停上市"}.get(list_status, "非上市")
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=stock.get("name", ""),
                        reason=ExclusionReason.DELISTED,
                        effective_date=trade_date,
                        detail=status_desc,
                    )
                )
        return excluded


@dataclass
class SuspendedExclusionRule(ExclusionRule):
    """长期停牌剔除规则

    剔除条件：is_suspended=True 且 suspend_days >= min_suspend_days

    Attributes:
        min_suspend_days: 最小停牌天数阈值（默认10天）
    """

    min_suspend_days: int = 10

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """筛选并剔除长期停牌股票。

        同时满足 is_suspended=True 且停牌天数 >= min_suspend_days 才剔除。

        Args:
            stocks: 待筛选的股票信息列表
            trade_date: 当前交易日期

        Returns:
            被剔除记录列表
        """
        excluded = []
        for stock in stocks:
            is_suspended = stock.get("is_suspended", False)
            suspend_days = stock.get("suspend_days", 0)

            if is_suspended and suspend_days >= self.min_suspend_days:
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=stock.get("name", ""),
                        reason=ExclusionReason.SUSPENDED,
                        effective_date=trade_date,
                        detail=f"停牌{suspend_days}天",
                    )
                )
        return excluded


@dataclass
class ManualExclusionRule(ExclusionRule):
    """手动黑名单剔除规则

    支持：
    - 按股票代码剔除
    - 按名称模式剔除（包含匹配）

    Attributes:
        exclude_codes: 要剔除的股票代码列表
        exclude_name_patterns: 名称匹配模式列表
    """

    exclude_codes: list[str] = field(default_factory=list)
    exclude_name_patterns: list[str] = field(default_factory=list)

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """根据手动黑名单剔除股票。

        支持按股票代码精确匹配和按名称关键字包含匹配两种方式。

        Args:
            stocks: 待筛选的股票信息列表
            trade_date: 当前交易日期

        Returns:
            被剔除记录列表
        """
        excluded = []
        exclude_codes_set = set(self.exclude_codes)

        for stock in stocks:
            ts_code = stock["ts_code"]
            name = stock.get("name", "")

            # 按代码剔除
            if ts_code in exclude_codes_set:
                excluded.append(
                    ExclusionRecord(
                        ts_code=ts_code,
                        name=name,
                        reason=ExclusionReason.MANUAL,
                        effective_date=trade_date,
                        detail="手动黑名单(代码)",
                    )
                )
                continue

            # 按名称模式剔除
            for pattern in self.exclude_name_patterns:
                if pattern in name:
                    excluded.append(
                        ExclusionRecord(
                            ts_code=ts_code,
                            name=name,
                            reason=ExclusionReason.MANUAL,
                            effective_date=trade_date,
                            detail=f"手动黑名单(名称匹配: {pattern})",
                        )
                    )
                    break

        return excluded


def load_manual_exclusion_from_config(
    config_path: Path | str,
    trade_date: date | None = None,
) -> ManualExclusionRule:
    """
    从配置文件加载手动剔除规则

    配置文件格式（YAML）：
    ```yaml
    items:
      - ts_code: "000001.SZ"
        reason: "手动剔除原因"
        effective_from: "2020-01-01"
        effective_to: null
      - name_keyword: "*ST"
        reason: "剔除ST股票"
    ```

    Args:
        config_path: 配置文件路径
        trade_date: 交易日期（用于判断生效区间，None表示不检查）

    Returns:
        ManualExclusionRule 实例
    """
    config_path = Path(config_path)
    if not config_path.exists():
        return ManualExclusionRule()

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    exclude_codes: list[str] = []
    exclude_name_patterns: list[str] = []

    items = config.get("items", [])
    for item in items:
        # 检查生效区间
        if trade_date is not None:
            effective_from = item.get("effective_from")
            effective_to = item.get("effective_to")

            if effective_from:
                from_date = date.fromisoformat(effective_from)
                if trade_date < from_date:
                    continue

            if effective_to:
                to_date = date.fromisoformat(effective_to)
                if trade_date > to_date:
                    continue

        # 按代码剔除
        if "ts_code" in item:
            exclude_codes.append(item["ts_code"])

        # 按名称关键字剔除
        if "name_keyword" in item:
            exclude_name_patterns.append(item["name_keyword"])

    return ManualExclusionRule(
        exclude_codes=exclude_codes,
        exclude_name_patterns=exclude_name_patterns,
    )


@dataclass
class ForceRedeemExclusionRule(ExclusionRule):
    """CB 强赎剔除规则

    已公告强赎的可转债从 Universe 中剔除，避免买入后被强制赎回。
    通过 Bar.metadata 中的 ``is_called`` 和 ``force_redeem_status`` 判断。

    数据来源优先级：
    1. ``is_called=True`` — PIT 公告数据确认
    2. ``force_redeem_status`` 包含"公告"关键字 — 上游注入

    Attributes:
        check_heuristic: 是否把启发式判断（cb_value≥130）也列为剔除。
            默认 False，仅剔除公告确认的强赎。
    """

    check_heuristic: bool = False

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        excluded = []
        for stock in stocks:
            meta = stock.get("metadata") or {}
            is_called = meta.get("is_called")
            fr_status = str(meta.get("force_redeem_status", ""))

            # 公告确认的强赎
            if is_called is True or any(
                kw in fr_status for kw in ("公告实施强赎", "已公告强赎", "公告强赎")
            ):
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=stock.get("name", ""),
                        reason=ExclusionReason.FORCE_REDEEM,
                        effective_date=trade_date,
                        detail=f"已公告强赎: {fr_status}" if fr_status else "已公告强赎",
                    )
                )
                continue

            # 可选：启发式高风险也剔除
            if self.check_heuristic and "启发式" in fr_status:
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=stock.get("name", ""),
                        reason=ExclusionReason.FORCE_REDEEM,
                        effective_date=trade_date,
                        detail=f"启发式强赎高风险: {fr_status}",
                    )
                )

        return excluded


@dataclass
class NearMaturityExclusionRule(ExclusionRule):
    """CB 近到期剔除规则

    距到期日 ≤ ``min_remaining_days`` 天的可转债从 Universe 中剔除，
    避免流动性枯竭和赎回不确定性。

    通过 Bar.metadata 中的 ``maturity_date``（YYYYMMDD）计算剩余天数。

    Attributes:
        min_remaining_days: 最小剩余天数（默认 30 天）
    """

    min_remaining_days: int = 30

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        excluded = []
        for stock in stocks:
            meta = stock.get("metadata") or {}
            maturity_str = meta.get("maturity_date", "")
            if not maturity_str:
                continue

            try:
                mat_date = date(
                    int(maturity_str[:4]),
                    int(maturity_str[4:6]),
                    int(maturity_str[6:8]),
                )
            except (ValueError, IndexError):
                continue

            remaining = (mat_date - trade_date).days
            if remaining <= self.min_remaining_days:
                excluded.append(
                    ExclusionRecord(
                        ts_code=stock["ts_code"],
                        name=stock.get("name", ""),
                        reason=ExclusionReason.NEAR_MATURITY,
                        effective_date=trade_date,
                        detail=f"距到期{remaining}天 (≤{self.min_remaining_days}天)",
                    )
                )

        return excluded


@dataclass
class IndexMemberExclusionRule(ExclusionRule):
    """指数成分股过滤规则

    剔除**非**指定指数成分股的标的。支持时点归属判断（半年度快照），
    避免前视偏差。

    典型用法::

        rule = IndexMemberExclusionRule(
            index_code="000905.SH",   # CSI500
            snapshots={"20230101": ["000001.SZ", "000002.SZ", ...]},
        )

    Attributes:
        index_code: 指数代码（如 ``000905.SH`` 中证500）
        snapshots: 按日期排序的成分股快照 {date_str → [ts_code, ...]}
        index_name: 指数名称（仅用于日志/报告）
    """

    index_code: str = ""
    snapshots: dict[str, list[str]] = field(default_factory=dict)
    index_name: str = ""

    def _get_members_at(self, trade_date: date) -> set[str]:
        """获取 trade_date 时点的成分股集合（最近快照回溯）。"""
        td_str = trade_date.strftime("%Y%m%d")
        best_key: str | None = None
        for snap_date in sorted(self.snapshots.keys()):
            if snap_date <= td_str:
                best_key = snap_date
            else:
                break

        if best_key is None:
            return set()
        return set(self.snapshots[best_key])

    def filter(
        self, stocks: list[dict[str, Any]], trade_date: date
    ) -> list[ExclusionRecord]:
        """剔除非指数成分股的标的。

        Args:
            stocks: 待筛选的股票信息列表
            trade_date: 当前交易日期

        Returns:
            被剔除记录列表
        """
        members = self._get_members_at(trade_date)
        if not members:
            return []  # 无快照数据时不剔除（保守策略）

        excluded = []
        label = self.index_name or self.index_code
        for stock in stocks:
            ts_code = stock["ts_code"]
            if ts_code not in members:
                excluded.append(
                    ExclusionRecord(
                        ts_code=ts_code,
                        name=stock.get("name", ""),
                        reason=ExclusionReason.NOT_INDEX_MEMBER,
                        effective_date=trade_date,
                        detail=f"非{label}成分股",
                    )
                )
        return excluded
