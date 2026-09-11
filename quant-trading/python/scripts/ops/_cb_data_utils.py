#!/usr/bin/env python3
"""可转债日线增强 —— 独立 paper 脚本用的薄封装

真正的实现在 ``aipha/data/cb_data_enricher.py``（``fetch_cb_daily_enriched``
按 TuShare 返回的完整度决定是直接用 ``cb_over_rate``、还是从
``cb_basic.conv_price`` + 正股收盘价现算转股溢价率）。这里**不复制那段逻辑**
—— 溢价率算错会直接改变双低排序，也就是改变买哪些债，两份实现迟早算出两
个答案。

这个文件存在的理由只有一个：``scripts/ops`` 下的独立脚本历史上是把
``scripts/ops`` 挂到 ``sys.path`` 后 ``from _cb_data_utils import ...``
（扁平导入），换成包路径会一起改一批脚本。所以保留这个名字做转发。

## 关于 ``_CB_BASIC_CACHE``

``cb_basic`` 一天内不变，enricher 里按 600s TTL 缓存。老脚本/测试用
``_cb_data_utils._CB_BASIC_CACHE = None`` 表达「把缓存清掉」。本模块的同名
属性是那份缓存的**镜像**：调用前如果发现本模块的值被置成了 None 而
enricher 里还有东西，就按「显式清缓存」处理；调用后把 enricher 的当前值
同步回来。这样只有一份缓存，清缓存的老写法也照旧有效。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from aipha.data import cb_data_enricher as _enricher
from aipha.data.cb_data_enricher import score_cb_double_low  # noqa: F401 - 转发

__all__ = ["fetch_cb_daily_enriched", "score_cb_double_low"]

# enricher 缓存的镜像，见模块 docstring
_CB_BASIC_CACHE: pd.DataFrame | None = None


def _push_cache() -> None:
    """本模块的镜像被置 None（= 请求清缓存）时，清掉 enricher 的真缓存。"""
    if _CB_BASIC_CACHE is None and _enricher._CB_BASIC_CACHE is not None:
        _enricher._CB_BASIC_CACHE = None
        _enricher._CB_BASIC_TS = 0.0


def _pull_cache() -> None:
    """把 enricher 的当前缓存同步回镜像。"""
    global _CB_BASIC_CACHE
    _CB_BASIC_CACHE = _enricher._CB_BASIC_CACHE


def fetch_cb_daily_enriched(
    gw: Any,
    trade_date: str,
    *,
    max_retries: int = 1,
) -> pd.DataFrame:
    """取指定交易日的可转债日线，并保证 ``cb_over_rate`` 列存在。

    Args:
        gw: TuShare 网关（需要 ``call(api_name, **kwargs)``）
        trade_date: ``YYYYMMDD``
        max_retries: 保留参数，由 enricher 处理

    Returns:
        含 ``cb_over_rate`` 列的 DataFrame；上游返回空时返回空 DataFrame。
    """
    _push_cache()
    try:
        return _enricher.fetch_cb_daily_enriched(
            gw, trade_date, max_retries=max_retries,
        )
    finally:
        _pull_cache()
