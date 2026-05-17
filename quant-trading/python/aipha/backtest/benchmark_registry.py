"""
基准注册表

统一管理回测/评估所用的基准（沪深300、中证500、中证1000、等权 CB 等）。
所有策略比较必须通过此注册表选取基准，禁止在代码中硬编码基准代码。

使用示例：
    registry = get_default_registry()
    info = registry.get("hs300")        # {'name': 'hs300', 'ts_code': '399300.SZ', ...}
    returns = registry.get_returns("hs300", "2022-01-01", "2024-12-31")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# 预置基准配置
# ---------------------------------------------------------------------------

_DEFAULT_BENCHMARKS: list[dict] = [
    {
        "name": "hs300",
        "ts_code": "399300.SZ",
        "description": "沪深300指数",
        "asset_type": "index",
    },
    {
        "name": "csi500",
        "ts_code": "000905.SH",
        "description": "中证500指数",
        "asset_type": "index",
    },
    {
        "name": "csi1000",
        "ts_code": "000852.SH",
        "description": "中证1000指数",
        "asset_type": "index",
    },
    {
        "name": "equal_weight_cb",
        "ts_code": None,
        "description": "可转债等权指数（内部计算）",
        "asset_type": "cb_custom",
    },
]


@dataclass
class BenchmarkInfo:
    """单个基准的元信息"""

    name: str
    ts_code: Optional[str]
    description: str
    asset_type: str   # "index" / "etf" / "cb_custom"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ts_code": self.ts_code,
            "description": self.description,
            "asset_type": self.asset_type,
        }


class BenchmarkRegistry:
    """基准注册表：注册、查询、获取收益率序列"""

    def __init__(self) -> None:
        self._store: dict[str, BenchmarkInfo] = {}

    def register(
        self,
        name: str,
        ts_code: Optional[str],
        description: str = "",
        asset_type: str = "index",
        extra: Optional[dict] = None,
    ) -> None:
        """注册一个基准"""
        if not name:
            raise ValueError("name must not be empty")
        self._store[name] = BenchmarkInfo(
            name=name,
            ts_code=ts_code,
            description=description,
            asset_type=asset_type,
            extra=extra or {},
        )

    def get(self, name: str) -> BenchmarkInfo:
        """查询基准元信息，不存在则抛出 KeyError"""
        if name not in self._store:
            available = list(self._store.keys())
            raise KeyError(f"Benchmark '{name}' not found. Available: {available}")
        return self._store[name]

    def list_all(self) -> list[BenchmarkInfo]:
        """列出所有已注册基准"""
        return list(self._store.values())

    def get_returns(
        self,
        name: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.Series:
        """获取基准的日收益率序列（通过 TuShareGateway 拉取）

        Returns:
            pd.Series，index 为交易日期（str），values 为日对数收益率
        """
        info = self.get(name)

        if info.asset_type == "cb_custom":
            # CB 等权指数由调用方注入，此处返回空 Series 占位
            return pd.Series(dtype=float, name=name)

        if info.ts_code is None:
            raise ValueError(f"Benchmark '{name}' has no ts_code, cannot fetch returns")

        from aipha.data.tushare_gateway import get_tushare_gateway

        gw = get_tushare_gateway()
        df = gw.pro.index_daily(
            ts_code=info.ts_code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            fields="trade_date,close",
        )
        if df is None or df.empty:
            return pd.Series(dtype=float, name=name)

        df = df.sort_values("trade_date").reset_index(drop=True)
        df["trade_date"] = df["trade_date"].str[:4] + "-" + df["trade_date"].str[4:6] + "-" + df["trade_date"].str[6:]
        closes = df.set_index("trade_date")["close"]
        log_returns = closes.pct_change().dropna()
        log_returns.name = name
        return log_returns

    def __contains__(self, name: str) -> bool:
        return name in self._store

    def __len__(self) -> int:
        return len(self._store)


def get_default_registry() -> BenchmarkRegistry:
    """返回预注册了四个标准基准的默认注册表"""
    registry = BenchmarkRegistry()
    for b in _DEFAULT_BENCHMARKS:
        registry.register(
            name=b["name"],
            ts_code=b["ts_code"],
            description=b["description"],
            asset_type=b["asset_type"],
        )
    return registry
