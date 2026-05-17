"""
Triple-Barrier 标签生成器

实现 López de Prado (2018) 的三重障碍标签方法，替代简单 N 日收益排名标签。

原理：
- 上障碍 (Take-Profit)：价格先触及 +τ_u → label=+1
- 下障碍 (Stop-Loss)：价格先触及 -τ_d → label=-1
- 时间障碍 (Max Holding)：两个障碍都没触及，按最终收益方向 → label=0 或 sign(ret)

优势：
- 更贴近实际交易决策（何时获利/止损/超时）
- 相比简单 N 日收益率标签，IC 预期提升 15-30%
- 与 Meta-Labeling 天然配合（先判断方向，再判断信号可信度）

学术依据：
- López de Prado (2018) "Advances in Financial Machine Learning" Ch.3 §3.4
- 预期 IC 改善来自标签噪声降低：简单收益率标签包含大量接近零的噪声样本

使用方式
--------
>>> from aipha.models.label_eng import TripleBarrierLabeler, TripleBarrierConfig
>>> labeler = TripleBarrierLabeler(TripleBarrierConfig(upper_bps=100, lower_bps=100))
>>> df = labeler.compute_labels(df)  # 添加 tb_label, tb_return, tb_holding 列
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TripleBarrierConfig:
    """Triple-Barrier 标签配置。

    Attributes:
        upper_bps: 上障碍基点数（止盈），默认 100bps = 1%
        lower_bps: 下障碍基点数（止损），默认 100bps = 1%
        max_holding_days: 最大持仓天数（时间障碍），默认 20
        dynamic_barrier: 是否使用动态障碍（基于近期波动率自适应）
        vol_lookback: 动态障碍波动率回看窗口
        vol_multiplier_upper: 动态上障碍 = vol_multiplier_upper × σ_daily × √T
        vol_multiplier_lower: 动态下障碍 = vol_multiplier_lower × σ_daily × √T
        price_col: 价格列名
        label_mode: 标签模式
            - "ternary": 三值 {-1, 0, +1}
            - "binary_sign": 二值 {0, 1}（按最终收益方向）
            - "ternary_rank": 三值后截面排名 (0~1)
        skip_days: 跳过信号日后 N 天（T+1 对齐）
        min_samples_per_date: 每个截面日期最少样本数（过少则跳过）
    """

    upper_bps: float = 100.0
    lower_bps: float = 100.0
    max_holding_days: int = 20
    dynamic_barrier: bool = False
    vol_lookback: int = 20
    vol_multiplier_upper: float = 1.5
    vol_multiplier_lower: float = 1.5
    price_col: str = "close"
    label_mode: Literal["ternary", "binary_sign", "ternary_rank"] = "ternary"
    skip_days: int = 0
    min_samples_per_date: int = 30


class TripleBarrierLabeler:
    """Triple-Barrier 标签计算器。"""

    def __init__(self, config: TripleBarrierConfig | None = None) -> None:
        self._config = config or TripleBarrierConfig()

    @property
    def config(self) -> TripleBarrierConfig:
        return self._config

    def compute_labels(
        self,
        df: pd.DataFrame,
        *,
        symbol_col: str = "symbol",
        date_col: str = "datetime",
    ) -> pd.DataFrame:
        """为面板数据计算 Triple-Barrier 标签。

        Args:
            df: 必须含 symbol, datetime, price_col 列，按 (symbol, datetime) 排序
            symbol_col: 股票标识列
            date_col: 日期列

        Returns:
            添加了 tb_label / tb_return / tb_holding_days 列的 DataFrame。
            tb_label: 标签值（取决于 label_mode）
            tb_return: 触及障碍时的实际收益率
            tb_holding_days: 实际持仓天数
        """
        cfg = self._config
        df = df.copy()
        df.sort_values([symbol_col, date_col], inplace=True)
        df.reset_index(drop=True, inplace=True)

        upper_frac = cfg.upper_bps / 10000.0
        lower_frac = cfg.lower_bps / 10000.0

        all_labels = np.full(len(df), np.nan)
        all_returns = np.full(len(df), np.nan)
        all_holding = np.full(len(df), np.nan)

        for sym, grp in df.groupby(symbol_col, sort=False):
            idx = grp.index.values
            prices = grp[cfg.price_col].values.astype(np.float64)

            if len(prices) < cfg.max_holding_days + cfg.skip_days + 2:
                continue

            # 障碍设置
            if cfg.dynamic_barrier:
                daily_vol = self._compute_daily_volatility(
                    prices, cfg.vol_lookback
                )
                sqrt_t = np.sqrt(cfg.max_holding_days)
                upper_b = cfg.vol_multiplier_upper * daily_vol * sqrt_t
                lower_b = cfg.vol_multiplier_lower * daily_vol * sqrt_t
                # 下限保护：至少 20bps
                upper_b = np.maximum(upper_b, 0.002)
                lower_b = np.maximum(lower_b, 0.002)
            else:
                upper_b = np.full(len(prices), upper_frac)
                lower_b = np.full(len(prices), lower_frac)

            labels, returns, holding = self._compute_single_stock(
                prices, upper_b, lower_b,
            )

            all_labels[idx] = labels
            all_returns[idx] = returns
            all_holding[idx] = holding

        df["tb_label"] = all_labels
        df["tb_return"] = all_returns
        df["tb_holding_days"] = all_holding

        # 丢弃无法计算标签的行
        before = len(df)
        df.dropna(subset=["tb_label"], inplace=True)
        after = len(df)
        if before > after:
            logger.info(
                "TripleBarrier: dropped %d/%d rows without labels",
                before - after, before,
            )

        # 根据 label_mode 做后处理
        if cfg.label_mode == "binary_sign":
            # -1/0/+1 → 0/1（正收益=1，其他=0）
            df["tb_label"] = (df["tb_label"] > 0).astype(int)

        elif cfg.label_mode == "ternary_rank":
            # 三值标签在截面内排名 (0~1)
            df["tb_label"] = df.groupby(date_col)["tb_label"].rank(
                method="average", pct=True,
            )

        # tb_label 转为 int（ternary模式下）或 float
        if cfg.label_mode == "ternary":
            df["tb_label"] = df["tb_label"].astype(int)

        n_pos = (df["tb_label"] > 0).sum()
        n_neg = (df["tb_label"] < 0).sum() if cfg.label_mode != "binary_sign" else 0
        n_zero = (df["tb_label"] == 0).sum()
        logger.info(
            "TripleBarrier labels: %d samples, +1=%d, 0=%d, -1=%d "
            "(mode=%s, upper=%.0fbps, lower=%.0fbps, max_hold=%d)",
            len(df), n_pos, n_zero, n_neg,
            cfg.label_mode, cfg.upper_bps, cfg.lower_bps, cfg.max_holding_days,
        )

        return df

    def _compute_single_stock(
        self,
        prices: np.ndarray,
        upper_barriers: np.ndarray,
        lower_barriers: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """单只股票的 Triple-Barrier 标签计算。

        Args:
            prices: 价格序列 shape=(T,)
            upper_barriers: 上障碍比例 shape=(T,), 如 0.01 表示 1%
            lower_barriers: 下障碍比例 shape=(T,), 如 0.01 表示 1%

        Returns:
            (labels, returns, holding_days) 各 shape=(T,)
        """
        cfg = self._config
        n = len(prices)
        skip = cfg.skip_days
        max_hold = cfg.max_holding_days

        labels = np.full(n, np.nan)
        returns = np.full(n, np.nan)
        holding = np.full(n, np.nan)

        for i in range(n):
            entry_idx = i + skip
            if entry_idx >= n:
                break
            entry_price = prices[entry_idx]
            if entry_price <= 0 or not np.isfinite(entry_price):
                continue

            upper_thresh = entry_price * (1.0 + upper_barriers[i])
            lower_thresh = entry_price * (1.0 - lower_barriers[i])

            # 扫描未来 max_hold 天
            end_idx = min(entry_idx + max_hold + 1, n)
            hit_label = 0  # 默认：时间障碍到期
            hit_return = 0.0
            hit_day = max_hold

            for j in range(entry_idx + 1, end_idx):
                p = prices[j]
                if not np.isfinite(p):
                    continue
                # 上障碍触及
                if p >= upper_thresh:
                    hit_label = 1
                    hit_return = (p - entry_price) / entry_price
                    hit_day = j - entry_idx
                    break
                # 下障碍触及
                if p <= lower_thresh:
                    hit_label = -1
                    hit_return = (p - entry_price) / entry_price
                    hit_day = j - entry_idx
                    break
            else:
                # 时间障碍到期：用最后可用价格
                final_idx = end_idx - 1
                if final_idx > entry_idx and np.isfinite(prices[final_idx]):
                    hit_return = (prices[final_idx] - entry_price) / entry_price
                    hit_label = int(np.sign(hit_return))
                    hit_day = final_idx - entry_idx

            labels[i] = hit_label
            returns[i] = hit_return
            holding[i] = hit_day

        return labels, returns, holding

    def _compute_daily_volatility(
        self,
        prices: np.ndarray,
        lookback: int,
    ) -> np.ndarray:
        """计算日收益率的滚动波动率（用于动态障碍）。

        Returns:
            shape=(T,) 的年化日波动率数组，前 lookback 个位置用全局均值填充。
        """
        log_ret = np.diff(np.log(np.maximum(prices, 1e-8)))
        vol = np.full(len(prices), np.nan)
        for i in range(lookback, len(log_ret)):
            window = log_ret[i - lookback:i]
            vol[i + 1] = np.std(window, ddof=1) if len(window) > 1 else 0.0
        # 前 lookback+1 个位置用后面的均值填充
        valid_vol = vol[np.isfinite(vol)]
        fill_val = float(np.mean(valid_vol)) if len(valid_vol) > 0 else 0.01
        vol = np.where(np.isfinite(vol), vol, fill_val)
        return vol
