"""
Alpha158 因子库

"Alpha158" 是 Microsoft Qlib 定义的标准量价因子集名称，包含恰好 158 个
基于 OHLCV 的技术因子（K线形态 + 价格相对 + 时序特征）。"158" 是该因子集
的固定身份标识（如同 "Alpha101" 指 WorldQuant 101 Alphas），不是版本号，
不会递增为 159/160。扩展因子应新建独立因子集（如 Qlib Alpha360）。

统一接口支持全市场截面批量计算，适配 ML 模型训练。

参考：
- Microsoft Qlib Alpha158 Dataset (github.com/microsoft/qlib)
- vnpy AlphaLab Alpha158 实现
- Kakushadze, Z. (2016). "101 Formulaic Alphas" → 对应本项目 alpha101.py

所有因子基于 OHLCV 数据，无需基本面字段。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.factors.operators import (
    ts_delay,
    ts_mean,
    ts_std,
    ts_min,
    ts_max,
    ts_sum,
    ts_slope,
    ts_rsquare,
    ts_resi,
    ts_quantile,
    ts_rank,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_log,
    ts_abs,
    ts_greater,
    safe_div,
)


# Alpha158 标准窗口
WINDOWS: list[int] = [5, 10, 20, 30, 60]

# 因子元数据
ALPHA158_CATEGORIES = {
    "candlestick": "K线形态特征",
    "price_relative": "价格相对特征",
    "roc": "价格变化率",
    "ma": "均线偏离",
    "std": "波动率",
    "beta": "趋势斜率",
    "rsqr": "拟合优度",
    "resi": "回归残差",
    "max_min": "极值偏离",
    "quantile": "分位数",
    "rank": "时序排名",
    "rsv": "随机指标",
    "argmax_argmin": "极值位置",
    "corr": "量价相关",
    "trend_freq": "涨跌频率",
    "trend_strength": "涨跌幅度",
    "volume": "成交量结构",
}


@dataclass
class Alpha158Config:
    """Alpha158 计算配置。"""
    windows: list[int] = field(default_factory=lambda: [5, 10, 20, 30, 60])
    include_candlestick: bool = True
    include_price_relative: bool = True
    include_time_series: bool = True
    eps: float = 1e-12


class Alpha158:
    """Alpha158 因子计算器。

    计算 Qlib 标准的 158 个量价因子，输入标准 OHLCV 面板数据。

    用法::

        calculator = Alpha158()
        result_df = calculator.compute(df)  # df 含 datetime, symbol, open, high, low, close, volume

    返回的 DataFrame 包含原始列 + 158 个因子列。
    """

    def __init__(self, config: Alpha158Config | None = None) -> None:
        self.config = config or Alpha158Config()
        self._factor_names: list[str] = []

    @property
    def factor_names(self) -> list[str]:
        """返回所有因子名称列表。"""
        if not self._factor_names:
            self._build_factor_names()
        return self._factor_names

    @property
    def factor_count(self) -> int:
        """因子总数。"""
        return len(self.factor_names)

    def _build_factor_names(self) -> None:
        """构建因子名称列表。"""
        names: list[str] = []
        cfg = self.config

        if cfg.include_candlestick:
            names.extend([
                "kmid", "klen", "kmid_2", "kup", "kup_2",
                "klow", "klow_2", "ksft", "ksft_2",
            ])

        if cfg.include_price_relative:
            names.extend(["open_0", "high_0", "low_0", "vwap_0"])

        if cfg.include_time_series:
            for w in cfg.windows:
                names.append(f"roc_{w}")
            for w in cfg.windows:
                names.append(f"ma_{w}")
            for w in cfg.windows:
                names.append(f"std_{w}")
            for w in cfg.windows:
                names.append(f"beta_{w}")
            for w in cfg.windows:
                names.append(f"rsqr_{w}")
            for w in cfg.windows:
                names.append(f"resi_{w}")
            for w in cfg.windows:
                names.append(f"max_{w}")
            for w in cfg.windows:
                names.append(f"min_{w}")
            for w in cfg.windows:
                names.append(f"qtlu_{w}")
            for w in cfg.windows:
                names.append(f"qtld_{w}")
            for w in cfg.windows:
                names.append(f"rank_{w}")
            for w in cfg.windows:
                names.append(f"rsv_{w}")
            for w in cfg.windows:
                names.append(f"imax_{w}")
            for w in cfg.windows:
                names.append(f"imin_{w}")
            for w in cfg.windows:
                names.append(f"imxd_{w}")
            for w in cfg.windows:
                names.append(f"corr_{w}")
            for w in cfg.windows:
                names.append(f"cord_{w}")
            for w in cfg.windows:
                names.append(f"cntp_{w}")
            for w in cfg.windows:
                names.append(f"cntn_{w}")
            for w in cfg.windows:
                names.append(f"cntd_{w}")
            for w in cfg.windows:
                names.append(f"sump_{w}")
            for w in cfg.windows:
                names.append(f"sumn_{w}")
            for w in cfg.windows:
                names.append(f"sumd_{w}")
            for w in cfg.windows:
                names.append(f"vma_{w}")
            for w in cfg.windows:
                names.append(f"vstd_{w}")
            for w in cfg.windows:
                names.append(f"wvma_{w}")
            for w in cfg.windows:
                names.append(f"vsump_{w}")
            for w in cfg.windows:
                names.append(f"vsumn_{w}")
            for w in cfg.windows:
                names.append(f"vsumd_{w}")

        self._factor_names = names

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算全部 Alpha158 因子。

        Args:
            df: 输入面板数据，需包含列：
                - datetime: 日期
                - symbol: 标的代码
                - open, high, low, close, volume: OHLCV 数据
                可选列：
                - vwap: 成交均价（缺失时用 (high+low+close)/3 估算）

        Returns:
            添加了 158 个因子列的 DataFrame（保留原始列）。
        """
        result = df.copy()
        result = result.sort_values(["symbol", "datetime"]).reset_index(drop=True)

        grp = result["symbol"]
        eps = self.config.eps

        # 基础字段
        o = result["open"]
        h = result["high"]
        lo = result["low"]
        c = result["close"]
        v = result["volume"].astype(float)

        if "vwap" in result.columns:
            vwap = result["vwap"]
        else:
            vwap = (h + lo + c) / 3

        # 使用 dict 收集所有因子，最后一次性 concat，避免 DataFrame 碎片化
        factors: dict[str, pd.Series] = {}

        # =====================================================================
        # 1. K线形态特征 (9 个)
        # =====================================================================
        if self.config.include_candlestick:
            factors["kmid"] = (c - o) / (o + eps)
            factors["klen"] = (h - lo) / (o + eps)
            hl_range = h - lo + eps
            factors["kmid_2"] = (c - o) / hl_range
            max_oc = ts_greater(o, c)
            min_oc = ts_less_series(o, c)
            factors["kup"] = (h - max_oc) / (o + eps)
            factors["kup_2"] = (h - max_oc) / hl_range
            factors["klow"] = (min_oc - lo) / (o + eps)
            factors["klow_2"] = (min_oc - lo) / hl_range
            factors["ksft"] = (c * 2 - h - lo) / (o + eps)
            factors["ksft_2"] = (c * 2 - h - lo) / hl_range

        # =====================================================================
        # 2. 价格相对特征 (4 个)
        # =====================================================================
        if self.config.include_price_relative:
            factors["open_0"] = o / (c + eps)
            factors["high_0"] = h / (c + eps)
            factors["low_0"] = lo / (c + eps)
            factors["vwap_0"] = vwap / (c + eps)

        # =====================================================================
        # 3. 时序特征 (29 类 × 5 窗口 = 145 个)
        # =====================================================================
        if self.config.include_time_series:
            for w in self.config.windows:
                # 价格变化率
                factors[f"roc_{w}"] = ts_delay(c, w, grp) / (c + eps)

                # 均线偏离
                factors[f"ma_{w}"] = ts_mean(c, w, grp) / (c + eps)

                # 波动率
                factors[f"std_{w}"] = ts_std(c, w, grp) / (c + eps)

                # 趋势斜率
                factors[f"beta_{w}"] = ts_slope(c, w, grp) / (c + eps)

                # 拟合优度
                factors[f"rsqr_{w}"] = ts_rsquare(c, w, grp)

                # 回归残差
                factors[f"resi_{w}"] = ts_resi(c, w, grp) / (c + eps)

                # 最高价偏离
                factors[f"max_{w}"] = ts_max(h, w, grp) / (c + eps)

                # 最低价偏离
                factors[f"min_{w}"] = ts_min(lo, w, grp) / (c + eps)

                # 上分位数
                factors[f"qtlu_{w}"] = ts_quantile(c, w, 0.8, grp) / (c + eps)

                # 下分位数
                factors[f"qtld_{w}"] = ts_quantile(c, w, 0.2, grp) / (c + eps)

                # 时序排名
                factors[f"rank_{w}"] = ts_rank(c, w, grp)

                # RSV
                min_low = ts_min(lo, w, grp)
                max_high = ts_max(h, w, grp)
                factors[f"rsv_{w}"] = (c - min_low) / (max_high - min_low + eps)

                # 极值位置
                factors[f"imax_{w}"] = ts_argmax(h, w, grp) / w
                factors[f"imin_{w}"] = ts_argmin(lo, w, grp) / w
                factors[f"imxd_{w}"] = (
                    ts_argmax(h, w, grp) - ts_argmin(lo, w, grp)
                ) / w

                # 量价相关
                factors[f"corr_{w}"] = ts_corr(c, ts_log(v + 1), w, grp)

                # 收益-量变化相关
                ret = c / (ts_delay(c, 1, grp) + eps)
                vol_chg = v / (ts_delay(v, 1, grp) + eps)
                factors[f"cord_{w}"] = ts_corr(ret, ts_log(vol_chg + 1), w, grp)

                # 涨跌频率
                close_delta = c - ts_delay(c, 1, grp)
                up = (close_delta > 0).astype(float)
                down = (close_delta < 0).astype(float)

                cntp = ts_mean(up, w, grp)
                cntn = ts_mean(down, w, grp)
                factors[f"cntp_{w}"] = cntp
                factors[f"cntn_{w}"] = cntn
                factors[f"cntd_{w}"] = cntp - cntn

                # 涨跌幅度
                abs_delta = close_delta.abs()
                up_delta = ts_greater(close_delta, 0.0)
                down_delta = ts_greater(-close_delta, 0.0)
                sum_abs = ts_sum(abs_delta, w, grp) + eps

                sump = ts_sum(up_delta, w, grp) / sum_abs
                sumn = ts_sum(down_delta, w, grp) / sum_abs
                factors[f"sump_{w}"] = sump
                factors[f"sumn_{w}"] = sumn
                factors[f"sumd_{w}"] = sump - sumn

                # 成交量结构
                factors[f"vma_{w}"] = ts_mean(v, w, grp) / (v + eps)
                factors[f"vstd_{w}"] = ts_std(v, w, grp) / (v + eps)

                # 加权量波动
                abs_ret = (c / (ts_delay(c, 1, grp) + eps) - 1).abs()
                weighted_vol = abs_ret * v
                wvma_mean = ts_mean(weighted_vol, w, grp) + eps
                wvma_std = ts_std(weighted_vol, w, grp)
                factors[f"wvma_{w}"] = wvma_std / wvma_mean

                # 量的涨跌幅度
                vol_delta = v - ts_delay(v, 1, grp)
                abs_vol_delta = vol_delta.abs()
                sum_abs_vol = ts_sum(abs_vol_delta, w, grp) + eps

                vsump = ts_sum(ts_greater(vol_delta, 0.0), w, grp) / sum_abs_vol
                vsumn = ts_sum(ts_greater(-vol_delta, 0.0), w, grp) / sum_abs_vol
                factors[f"vsump_{w}"] = vsump
                factors[f"vsumn_{w}"] = vsumn
                factors[f"vsumd_{w}"] = vsump - vsumn

        # 逐列赋值避免 pd.concat → _consolidate_inplace 的大块连续内存分配
        # （concat 175 列 × 300 万行会触发 3-4 GiB 连续数组分配导致 OOM）
        if factors:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
                for col_name, col_data in factors.items():
                    result[col_name] = col_data
            del factors

        return result


def ts_less_series(s1: pd.Series, s2: pd.Series) -> pd.Series:
    """逐元素取较小值。"""
    return pd.concat([s1, s2], axis=1).min(axis=1)


def compute_alpha158(
    df: pd.DataFrame,
    windows: list[int] | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    """便捷函数：计算 Alpha158 因子。

    Args:
        df: 面板数据（需包含 datetime, symbol, open, high, low, close, volume）
        windows: 滚动窗口列表，默认 [5, 10, 20, 30, 60]
        **kwargs: 传递给 Alpha158Config

    Returns:
        添加了 158 个因子列的 DataFrame
    """
    config = Alpha158Config(**kwargs)
    if windows:
        config.windows = windows
    calculator = Alpha158(config)
    return calculator.compute(df)


def get_alpha158_factor_names(windows: list[int] | None = None) -> list[str]:
    """返回所有 Alpha158 因子名称。"""
    config = Alpha158Config()
    if windows:
        config.windows = windows
    calc = Alpha158(config)
    return calc.factor_names
