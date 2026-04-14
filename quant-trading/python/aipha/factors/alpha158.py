"""Alpha158 因子库 — 基于 Qlib 的 158 个量价因子

统一接口批量计算，适配 ML 模型训练。
参考: Microsoft Qlib Alpha158 Dataset

所有因子基于 OHLCV 数据，无需基本面字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aipha.factors.operators import (
    ts_delay,
    ts_mean,
    ts_std,
    ts_min,
    ts_max,
    ts_sum,
    ts_rank,
    ts_argmax,
    ts_argmin,
    ts_corr,
)

WINDOWS: list[int] = [5, 10, 20, 30, 60]

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
    windows: list[int] = field(default_factory=lambda: [5, 10, 20, 30, 60])
    include_candlestick: bool = True
    include_price_relative: bool = True
    include_time_series: bool = True
    eps: float = 1e-12


def _safe_div(a: pd.Series, b: pd.Series, eps: float = 1e-12) -> pd.Series:
    return a / b.replace(0, eps)


class Alpha158:
    """Alpha158 因子计算器。

    输入: DataFrame 含 datetime, symbol, open, high, low, close, volume
    输出: 原始列 + 158 个因子列
    """

    def __init__(self, config: Alpha158Config | None = None) -> None:
        self.config = config or Alpha158Config()
        self._factor_names: list[str] = []

    @property
    def factor_names(self) -> list[str]:
        if not self._factor_names:
            self._build_factor_names()
        return self._factor_names

    @property
    def factor_count(self) -> int:
        return len(self.factor_names)

    def _build_factor_names(self) -> None:
        names: list[str] = []
        cfg = self.config
        if cfg.include_candlestick:
            names.extend(["kmid", "klen", "kmid2", "kup", "kup2", "klow", "klow2", "ksft", "ksft2"])
        if cfg.include_price_relative:
            names.extend(["open_0", "high_0", "low_0", "vwap_0"])
        if cfg.include_time_series:
            for category in ["roc", "ma", "std", "beta", "rsqr", "resi",
                             "max", "min", "qtlu", "qtld", "rank", "rsv",
                             "imax", "imin", "imxd", "corr", "cord",
                             "cntp", "cntn", "cntd", "sump", "sumn",
                             "sumd", "vma", "vstd", "wvma", "vsump",
                             "vsumn", "vsumd"]:
                for w in cfg.windows:
                    names.append(f"{category}_{w}")
        self._factor_names = names

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """批量计算所有 Alpha158 因子。"""
        if "symbol" in df.columns:
            g = df.groupby("symbol", group_keys=False)
        else:
            g = None

        result = df.copy()
        cfg = self.config
        eps = cfg.eps

        o = df["open"]
        h = df["high"]
        lo = df["low"]
        c = df["close"]
        v = df["volume"].astype(float)

        # ── K线形态 (9 factors) ──
        if cfg.include_candlestick:
            result["kmid"] = _safe_div(c - o, o, eps)
            result["klen"] = _safe_div(h - lo, o, eps)
            result["kmid2"] = _safe_div(c - o, h - lo + eps)
            result["kup"] = _safe_div(h - np.maximum(o, c), o, eps)
            result["kup2"] = _safe_div(h - np.maximum(o, c), h - lo + eps)
            result["klow"] = _safe_div(np.minimum(o, c) - lo, o, eps)
            result["klow2"] = _safe_div(np.minimum(o, c) - lo, h - lo + eps)
            result["ksft"] = _safe_div(2 * c - h - lo, o, eps)
            result["ksft2"] = _safe_div(2 * c - h - lo, h - lo + eps)

        # ── 价格相对 (4 factors) ──
        if cfg.include_price_relative:
            result["open_0"] = _safe_div(o, c, eps) - 1.0
            result["high_0"] = _safe_div(h, c, eps) - 1.0
            result["low_0"] = _safe_div(lo, c, eps) - 1.0
            vwap = (h + lo + c) / 3.0
            result["vwap_0"] = _safe_div(vwap, c, eps) - 1.0

        # ── 时序特征 (145 factors, 29 categories × 5 windows) ──
        if cfg.include_time_series:
            dt = df["datetime"] if "datetime" in df.columns else None
            grp = df["symbol"] if "symbol" in df.columns else None

            for w in cfg.windows:
                # ROC: 变化率
                delayed = c.groupby(grp).shift(w) if grp is not None else c.shift(w)
                result[f"roc_{w}"] = _safe_div(c - delayed, delayed, eps)

                # MA: 均线偏离
                ma = c.groupby(grp).rolling(w).mean().droplevel(0) if grp is not None else c.rolling(w).mean()
                result[f"ma_{w}"] = _safe_div(ma, c, eps) - 1.0

                # STD: 波动率
                std = c.groupby(grp).rolling(w).std().droplevel(0) if grp is not None else c.rolling(w).std()
                result[f"std_{w}"] = _safe_div(std, c, eps)

                # Beta: 趋势斜率 (线性回归斜率 / close)
                result[f"beta_{w}"] = self._rolling_slope(c, w, grp) / (c + eps)

                # R²: 拟合优度
                result[f"rsqr_{w}"] = self._rolling_rsquare(c, w, grp)

                # Resi: 回归残差
                result[f"resi_{w}"] = self._rolling_residual(c, w, grp) / (c + eps)

                # Max/Min: 极值偏离
                rmax = c.groupby(grp).rolling(w).max().droplevel(0) if grp is not None else c.rolling(w).max()
                rmin = c.groupby(grp).rolling(w).min().droplevel(0) if grp is not None else c.rolling(w).min()
                result[f"max_{w}"] = _safe_div(rmax, c, eps) - 1.0
                result[f"min_{w}"] = _safe_div(rmin, c, eps) - 1.0

                # Quantile: 分位数
                result[f"qtlu_{w}"] = self._rolling_quantile(c, w, 0.8, grp) / (c + eps) - 1.0
                result[f"qtld_{w}"] = self._rolling_quantile(c, w, 0.2, grp) / (c + eps) - 1.0

                # Rank: 时序排名
                result[f"rank_{w}"] = self._rolling_rank(c, w, grp)

                # RSV: (close - min) / (max - min)
                result[f"rsv_{w}"] = _safe_div(c - rmin, rmax - rmin, eps)

                # Argmax/Argmin: 极值位置
                result[f"imax_{w}"] = self._rolling_argmax(c, w, grp) / w
                result[f"imin_{w}"] = self._rolling_argmin(c, w, grp) / w
                result[f"imxd_{w}"] = result[f"imax_{w}"] - result[f"imin_{w}"]

                # Corr: 量价相关
                result[f"corr_{w}"] = self._rolling_corr(c, np.log1p(v), w, grp)
                result[f"cord_{w}"] = self._rolling_corr(
                    _safe_div(c - delayed, delayed, eps), np.log1p(v), w, grp
                )

                # 涨跌频率和幅度
                ret = _safe_div(c - delayed.shift(-w + 1) if grp is None else c.groupby(grp).diff(), c.shift(1), eps)
                pos = (ret > 0).astype(float)
                neg = (ret < 0).astype(float)

                cntp = pos.groupby(grp).rolling(w).mean().droplevel(0) if grp is not None else pos.rolling(w).mean()
                cntn = neg.groupby(grp).rolling(w).mean().droplevel(0) if grp is not None else neg.rolling(w).mean()
                result[f"cntp_{w}"] = cntp
                result[f"cntn_{w}"] = cntn
                result[f"cntd_{w}"] = cntp - cntn

                ret_pos = ret.clip(lower=0)
                ret_neg = ret.clip(upper=0)
                sump = ret_pos.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else ret_pos.rolling(w).sum()
                sumn = ret_neg.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else ret_neg.rolling(w).sum()
                result[f"sump_{w}"] = _safe_div(sump, c, eps)
                result[f"sumn_{w}"] = _safe_div(sumn, c, eps)
                result[f"sumd_{w}"] = result[f"sump_{w}"] - result[f"sumn_{w}"]

                # 成交量特征
                vma = v.groupby(grp).rolling(w).mean().droplevel(0) if grp is not None else v.rolling(w).mean()
                vstd = v.groupby(grp).rolling(w).std().droplevel(0) if grp is not None else v.rolling(w).std()
                result[f"vma_{w}"] = _safe_div(vma, v + 1, eps)
                result[f"vstd_{w}"] = _safe_div(vstd, v + 1, eps)

                # 加权成交量
                wv = np.abs(ret) * v
                wvma = wv.groupby(grp).rolling(w).mean().droplevel(0) if grp is not None else wv.rolling(w).mean()
                result[f"wvma_{w}"] = _safe_div(wvma, v + 1, eps)

                # 成交量方向
                v_pos = (v * pos)
                v_neg = (v * neg)
                vsump = v_pos.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else v_pos.rolling(w).sum()
                vsumn = v_neg.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else v_neg.rolling(w).sum()
                result[f"vsump_{w}"] = _safe_div(vsump, v.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else v.rolling(w).sum() + 1, eps)
                result[f"vsumn_{w}"] = _safe_div(vsumn, v.groupby(grp).rolling(w).sum().droplevel(0) if grp is not None else v.rolling(w).sum() + 1, eps)
                result[f"vsumd_{w}"] = result[f"vsump_{w}"] - result[f"vsumn_{w}"]

        return result

    # ── 辅助函数 ──

    def _rolling_slope(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        """窗口线性回归斜率"""
        def slope_fn(x):
            n = len(x)
            if n < 2:
                return np.nan
            t = np.arange(n, dtype=float)
            return np.polyfit(t, x, 1)[0]
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(slope_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(slope_fn, raw=True)

    def _rolling_rsquare(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        def rsq_fn(x):
            n = len(x)
            if n < 2:
                return np.nan
            t = np.arange(n, dtype=float)
            corr = np.corrcoef(t, x)[0, 1]
            return corr ** 2 if np.isfinite(corr) else np.nan
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(rsq_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(rsq_fn, raw=True)

    def _rolling_residual(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        def resi_fn(x):
            n = len(x)
            if n < 2:
                return np.nan
            t = np.arange(n, dtype=float)
            coeffs = np.polyfit(t, x, 1)
            pred = coeffs[0] * (n - 1) + coeffs[1]
            return x[-1] - pred
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(resi_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(resi_fn, raw=True)

    def _rolling_quantile(self, s: pd.Series, w: int, q: float, grp: pd.Series | None) -> pd.Series:
        if grp is not None:
            return s.groupby(grp).rolling(w).quantile(q).droplevel(0)
        return s.rolling(w).quantile(q)

    def _rolling_rank(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        def rank_fn(x):
            return (x[-1] > x).sum() / max(len(x) - 1, 1)
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(rank_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(rank_fn, raw=True)

    def _rolling_argmax(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        def argmax_fn(x):
            return float(np.argmax(x))
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(argmax_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(argmax_fn, raw=True)

    def _rolling_argmin(self, s: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        def argmin_fn(x):
            return float(np.argmin(x))
        if grp is not None:
            return s.groupby(grp).rolling(w).apply(argmin_fn, raw=True).droplevel(0)
        return s.rolling(w).apply(argmin_fn, raw=True)

    def _rolling_corr(self, a: pd.Series, b: pd.Series, w: int, grp: pd.Series | None) -> pd.Series:
        if grp is not None:
            return a.groupby(grp).rolling(w).corr(b).droplevel(0)
        return a.rolling(w).corr(b)
