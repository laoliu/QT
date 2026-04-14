"""基准收益注册表 — 快速获取常用宽基指数收益序列"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkInfo:
    name: str
    code: str
    description: str = ""
    provider: Optional[Callable[[str, date, date], dict[date, float]]] = field(default=None, repr=False)


class BenchmarkRegistry:
    _benchmarks: dict[str, BenchmarkInfo] = {}

    @classmethod
    def register(cls, key: str, info: BenchmarkInfo):
        cls._benchmarks[key] = info

    @classmethod
    def list(cls) -> list[str]:
        return list(cls._benchmarks.keys())

    @classmethod
    def get_info(cls, key: str) -> BenchmarkInfo:
        if key not in cls._benchmarks:
            raise KeyError(f"未注册的基准: {key}, 可用: {cls.list()}")
        return cls._benchmarks[key]

    @classmethod
    def get_returns(cls, key: str, start: date, end: date) -> dict[date, float]:
        info = cls.get_info(key)
        if info.provider is None:
            raise RuntimeError(f"基准 {key} 未设置数据源")
        return info.provider(info.code, start, end)


def _make_equal_weight_benchmark(code: str, start: date, end: date) -> dict[date, float]:
    """等权基准: 每日收益 = 0, NAV 恒为 1"""
    from datetime import timedelta
    nav = {}
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            nav[cur] = 1.0
        cur += timedelta(days=1)
    return nav


def _make_tushare_provider(src: str = "tushare"):
    """工厂: 返回基于数据源的基准提供器"""
    def provider(code: str, start: date, end: date) -> dict[date, float]:
        try:
            import tushare as ts
            pro = ts.pro_api()
            df = pro.index_daily(
                ts_code=code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="trade_date,close",
            )
            if df is None or df.empty:
                logger.warning("TuShare 未获取到 %s 数据, 回退至等权", code)
                return _make_equal_weight_benchmark(code, start, end)
            df = df.sort_values("trade_date")
            nav = {}
            for _, row in df.iterrows():
                d = date(int(row["trade_date"][:4]), int(row["trade_date"][4:6]), int(row["trade_date"][6:8]))
                nav[d] = float(row["close"])
            # 归一化
            first = nav[min(nav)]
            return {d: v / first for d, v in nav.items()}
        except Exception as e:
            logger.warning("获取基准 %s 失败: %s, 回退至等权", code, e)
            return _make_equal_weight_benchmark(code, start, end)
    return provider


# 预注册常用基准
_default_benchmarks = [
    ("hs300", BenchmarkInfo("沪深300", "399300.SZ", "沪深300指数")),
    ("csi500", BenchmarkInfo("中证500", "000905.SH", "中证500指数")),
    ("csi1000", BenchmarkInfo("中证1000", "000852.SH", "中证1000指数")),
    ("equal_weight_cb", BenchmarkInfo("等权基准", "", "等权基准(NAV=1)")),
]


def _register_defaults():
    ts_provider = _make_tushare_provider()
    for key, info in _default_benchmarks:
        if key == "equal_weight_cb":
            info.provider = _make_equal_weight_benchmark
        else:
            info.provider = ts_provider
        BenchmarkRegistry.register(key, info)


_register_defaults()
