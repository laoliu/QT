"""可转债动态标的池管理

解决 Paper Trading 中静态 symbols 列表的问题：
- 可转债频繁发行/退市/强赎，静态列表几天就过期
- 策略候选池 = bars dict keys，无法自主发现新标的

CBUniverseProvider 提供：
1. 从 AKShare 获取全市场存续 CB 列表（1000+）
2. 按策略需求过滤（价格/规模/评级/排除已强赎）
3. 定时刷新（盘前 + 午间，可配置）
4. 与 PaperEngine 集成，自动更新订阅列表

设计原则（对齐 copilot-instructions.md）：
- 可插拔：通过 UniversePolicy 接口抽象，未来可替换数据源
- 配置驱动：所有过滤参数来自 configs/，不硬编码
- 避免前视偏差：仅使用当前可获取的公开信息
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 接口定义
# ---------------------------------------------------------------------------


class UniversePolicy(ABC):
    """标的池策略接口（可插拔）

    不同策略/资产类型可实现不同的 UniversePolicy。
    """

    @abstractmethod
    def fetch_symbols(self) -> list[str]:
        """获取当前符合条件的标的代码列表

        Returns:
            标的代码列表（如 ['127045', '128054', ...]）
        """
        ...

    @abstractmethod
    def get_metadata(self) -> dict[str, Any]:
        """获取本次刷新的元数据（用于审计/日志）

        Returns:
            元数据字典，至少包含 total_fetched, total_filtered, timestamp
        """
        ...


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class CBUniverseConfig:
    """可转债标的池配置

    所有参数应写入 configs/ YAML，不硬编码。
    """

    # 是否启用动态标的池（False 时使用静态 symbols）
    enabled: bool = True

    # 过滤参数
    max_price: float = 200.0       # 最高价格
    min_amount: float = 0.5        # 最小剩余规模（亿元）
    min_rating: str = ""           # 最低评级（空=不过滤）
    exclude_unrated: bool = False  # 是否剔除无评级转债
    exclude_called: bool = True    # 排除已触发强赎

    # 额外排除列表（手动黑名单）
    exclude_codes: list[str] = field(default_factory=list)

    # 结果限制（0=不限）
    max_symbols: int = 0

    # 刷新间隔（秒），0 表示不自动刷新
    refresh_interval_sec: int = 3600  # 默认 1 小时

    # 数据源超时（秒）
    fetch_timeout_sec: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CBUniverseConfig:
        """从字典创建配置"""
        return cls(
            enabled=data.get("enabled", True),
            max_price=data.get("max_price", 200.0),
            min_amount=data.get("min_amount", 0.5),
            min_rating=data.get("min_rating", ""),
            exclude_unrated=data.get("exclude_unrated", False),
            exclude_called=data.get("exclude_called", True),
            exclude_codes=data.get("exclude_codes", []),
            max_symbols=data.get("max_symbols", 0),
            refresh_interval_sec=data.get("refresh_interval_sec", 3600),
            fetch_timeout_sec=data.get("fetch_timeout_sec", 30),
        )


# ---------------------------------------------------------------------------
# CBUniverseProvider 实现
# ---------------------------------------------------------------------------


@dataclass
class UniverseRefreshResult:
    """标的池刷新结果"""

    symbols: list[str]
    total_fetched: int      # 数据源返回总数
    total_after_filter: int # 过滤后数量
    added: list[str]        # 新增标的
    removed: list[str]      # 移除标的
    timestamp: datetime
    duration_sec: float
    error: str | None = None


class CBUniverseProvider(UniversePolicy):
    """可转债动态标的池

    基于 AKShareCBProvider.load_bond_list_full() 获取全市场 CB，
    按配置进行过滤，提供给 PaperEngine 使用。

    使用示例::

        config = CBUniverseConfig(max_price=150, min_amount=1.0)
        provider = CBUniverseProvider(config)
        symbols = provider.fetch_symbols()
        print(f"当前可交易 CB: {len(symbols)} 只")

    与 PaperEngine 集成::

        result = provider.refresh()
        if result.added or result.removed:
            engine.update_subscriptions(result.symbols)
    """

    def __init__(self, config: CBUniverseConfig | None = None):
        self._config = config or CBUniverseConfig()
        self._current_symbols: list[str] = []
        self._last_refresh: datetime | None = None
        self._last_metadata: dict[str, Any] = {}
        self._refresh_count: int = 0

    @property
    def config(self) -> CBUniverseConfig:
        """返回可转债股票池配置。"""
        return self._config

    @property
    def current_symbols(self) -> list[str]:
        """当前标的列表（上次刷新结果）"""
        return list(self._current_symbols)

    @property
    def last_refresh_time(self) -> datetime | None:
        """返回上次刷新时间。"""
        return self._last_refresh

    @property
    def symbol_count(self) -> int:
        """返回当前股票池中标的数量。"""
        return len(self._current_symbols)

    def needs_refresh(self) -> bool:
        """是否需要刷新"""
        if not self._config.enabled:
            return False
        if self._last_refresh is None:
            return True
        if self._config.refresh_interval_sec <= 0:
            return False
        elapsed = (datetime.now() - self._last_refresh).total_seconds()
        return elapsed >= self._config.refresh_interval_sec

    def fetch_symbols(self) -> list[str]:
        """获取当前符合条件的 CB 代码列表

        从 AKShare 拉取全量 CB，按配置过滤。

        Returns:
            代码列表（纯数字，如 ['127045', '128054']）
        """
        from aipha.data.akshare_cb_provider import AKShareCBProvider

        t0 = time.monotonic()
        provider = AKShareCBProvider()

        try:
            bonds = provider.load_bond_list_full(
                max_price=self._config.max_price,
                min_amount=self._config.min_amount,
                min_rating=self._config.min_rating,
            )
        except Exception as e:
            logger.error(f"获取 CB 列表失败: {e}")
            self._last_metadata = {
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
            return self._current_symbols  # 返回上次结果（降级）

        total_fetched = len(bonds)

        # 显式信用评级过滤（双保险：避免上游数据源口径/实现差异）
        if self._config.min_rating or self._config.exclude_unrated:
            from aipha.strategy.cb_rotation_base import rating_score

            min_score = rating_score(self._config.min_rating) if self._config.min_rating else None

            filtered_bonds = []
            for b in bonds:
                rating = getattr(b, "rating", "") or ""
                if self._config.exclude_unrated and not str(rating).strip():
                    continue
                if min_score is not None and str(rating).strip():
                    if rating_score(str(rating)) < min_score:
                        continue
                filtered_bonds.append(b)
            bonds = filtered_bonds

        # 额外过滤：排除黑名单
        exclude_set = set(self._config.exclude_codes)
        if exclude_set:
            bonds = [b for b in bonds if b.code not in exclude_set]

        # 排除已强赎（通过 name 关键字判断）
        if self._config.exclude_called:
            bonds = [
                b for b in bonds
                if not any(kw in b.name for kw in ("强赎", "已赎回", "摘牌"))
            ]

        # 提取代码
        symbols = [b.code for b in bonds]

        # 限制数量
        if self._config.max_symbols > 0:
            symbols = symbols[:self._config.max_symbols]

        duration = time.monotonic() - t0

        self._last_metadata = {
            "total_fetched": total_fetched,
            "total_filtered": len(symbols),
            "excluded_by_blacklist": len(exclude_set & {b.code for b in bonds}),
            "timestamp": datetime.now().isoformat(),
            "duration_sec": round(duration, 2),
        }

        logger.info(
            f"CB Universe: 获取 {total_fetched} 只 → 过滤后 {len(symbols)} 只 "
            f"(耗时 {duration:.1f}s)"
        )

        return symbols

    def get_metadata(self) -> dict[str, Any]:
        """获取本次刷新的元数据"""
        return dict(self._last_metadata)

    def refresh(self) -> UniverseRefreshResult:
        """执行一次刷新并返回变更详情

        Returns:
            刷新结果，包含新增/移除标的
        """
        t0 = time.monotonic()
        old_set = set(self._current_symbols)

        try:
            new_symbols = self.fetch_symbols()
        except Exception as e:
            return UniverseRefreshResult(
                symbols=self._current_symbols,
                total_fetched=0,
                total_after_filter=0,
                added=[],
                removed=[],
                timestamp=datetime.now(),
                duration_sec=time.monotonic() - t0,
                error=str(e),
            )

        new_set = set(new_symbols)
        added = sorted(new_set - old_set)
        removed = sorted(old_set - new_set)

        self._current_symbols = new_symbols
        self._last_refresh = datetime.now()
        self._refresh_count += 1
        duration = time.monotonic() - t0

        meta = self._last_metadata
        result = UniverseRefreshResult(
            symbols=new_symbols,
            total_fetched=meta.get("total_fetched", 0),
            total_after_filter=len(new_symbols),
            added=added,
            removed=removed,
            timestamp=self._last_refresh,
            duration_sec=round(duration, 2),
        )

        if added or removed:
            logger.info(
                f"CB Universe 变更: +{len(added)} -{len(removed)} "
                f"(当前 {len(new_symbols)} 只)"
            )
            if added:
                logger.debug(f"  新增: {added[:10]}{'...' if len(added) > 10 else ''}")
            if removed:
                logger.debug(f"  移除: {removed[:10]}{'...' if len(removed) > 10 else ''}")
        else:
            logger.debug(f"CB Universe 无变更 (维持 {len(new_symbols)} 只)")

        return result

    def set_symbols_override(self, symbols: list[str]) -> None:
        """手动覆盖标的列表（用于测试或固定池场景）

        Args:
            symbols: 覆盖的标的列表
        """
        self._current_symbols = list(symbols)
        self._last_refresh = datetime.now()
        logger.info(f"CB Universe 手动覆盖: {len(symbols)} 只")

    def get_status(self) -> dict[str, Any]:
        """获取当前状态（用于监控/日志）"""
        return {
            "enabled": self._config.enabled,
            "symbol_count": len(self._current_symbols),
            "last_refresh": (
                self._last_refresh.isoformat() if self._last_refresh else None
            ),
            "refresh_count": self._refresh_count,
            "needs_refresh": self.needs_refresh(),
            "config": {
                "max_price": self._config.max_price,
                "min_amount": self._config.min_amount,
                "exclude_called": self._config.exclude_called,
                "refresh_interval_sec": self._config.refresh_interval_sec,
            },
        }
