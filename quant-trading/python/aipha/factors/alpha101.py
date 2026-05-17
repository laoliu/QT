"""
Alpha101 因子库

WorldQuant 101 公式化 Alpha 因子的 Pandas 实现。
基于 OHLCV + VWAP 数据，全部使用 operators.py 中的运算符。

参考：
- Kakushadze, Z. (2016). "101 Formulaic Alphas"
- vnpy AlphaLab Alpha101 Dataset

需要 IndNeutralize 的因子 (48,56,58,59,63,67,69,70,76,79,80,82,87,89,90,91,93,97,100)
标记为 skipped，后续可通过行业中性化模块补充。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from aipha.factors.operators import (
    cs_rank,
    cs_scale,
    pow1,
    pow2,
    quesval,
    quesval2,
    safe_div,
    ts_abs,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_cov,
    ts_decay_linear,
    ts_delay,
    ts_delta,
    ts_greater,
    ts_less,
    ts_log,
    ts_max,
    ts_mean,
    ts_min,
    ts_product,
    ts_rank,
    ts_sign,
    ts_std,
    ts_sum,
)

# IndNeutralize 依赖的因子编号
SKIPPED_ALPHAS = {48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100}

logger = logging.getLogger(__name__)


class Alpha101:
    """WorldQuant 101 公式化 Alpha 因子计算器。

    输入 DataFrame 必须包含列:
        datetime, symbol, open, high, low, close, volume
    可选列:
        vwap (若缺失则用 (high+low+close)/3 近似)

    用法::

        calc = Alpha101(df)
        result = calc.compute()  # 返回包含所有因子列的 DataFrame
        # 或计算单个因子
        a1 = calc.alpha1()
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self.df.sort_values(["symbol", "datetime"], inplace=True)
        self.df.reset_index(drop=True, inplace=True)

        # 提取常用列
        self.g = self.df["symbol"]  # group key
        self.dt = self.df["datetime"]  # datetime key for cs_*
        self.o = self.df["open"].astype(float)
        self.h = self.df["high"].astype(float)
        self.lo = self.df["low"].astype(float)
        self.c = self.df["close"].astype(float)
        self.v = self.df["volume"].astype(float)

        # vwap: 若缺失用典型价近似
        if "vwap" in self.df.columns:
            self.vwap = self.df["vwap"].astype(float)
        else:
            self.vwap = (self.h + self.lo + self.c) / 3.0

        # 日收益率
        self.returns = self.c / ts_delay(self.c, 1, self.g) - 1.0

    def compute(self, alphas: list[int] | None = None) -> pd.DataFrame:
        """批量计算 Alpha 因子。

        Args:
            alphas: 要计算的因子编号列表。None 表示计算全部可用因子。

        Returns:
            原始 DataFrame 附加 alpha1..alpha101 列。
        """
        result = self.df[["datetime", "symbol"]].copy()

        if alphas is None:
            alphas = [i for i in range(1, 102) if i not in SKIPPED_ALPHAS]

        # 使用 dict 收集所有因子，最后一次性 concat，避免 DataFrame 碎片化
        factors: dict[str, pd.Series] = {}
        for i in alphas:
            if i in SKIPPED_ALPHAS:
                continue
            method = getattr(self, f"alpha{i}", None)
            if method is not None:
                try:
                    factors[f"alpha{i}"] = method()
                except Exception as exc:
                    logger.warning("alpha%d 计算失败: %s", i, exc)
                    factors[f"alpha{i}"] = pd.Series(np.nan, index=result.index)

        if factors:
            result = pd.concat(
                [result, pd.DataFrame(factors, index=result.index)],
                axis=1,
            )

        return result

    # =================================================================
    # Alpha 1 - 15
    # =================================================================

    def alpha1(self) -> pd.Series:
        """rank(ts_argmax(pow(if(returns<0, ts_std(returns,20), close), 2), 5)) - 0.5"""
        inner = quesval(0, self.returns, self.c, ts_std(self.returns, 20, self.g))
        return cs_rank(ts_argmax(pow1(inner, 2.0), 5, self.g), self.dt) - 0.5

    def alpha2(self) -> pd.Series:
        """-1 * corr(rank(delta(log(volume),2)), rank((close-open)/open), 6)"""
        s1 = cs_rank(ts_delta(ts_log(self.v), 2, self.g), self.dt)
        s2 = cs_rank(safe_div(self.c - self.o, self.o), self.dt)
        return -1.0 * ts_corr(s1, s2, 6, self.g)

    def alpha3(self) -> pd.Series:
        """-1 * corr(rank(open), rank(volume), 10)"""
        return -1.0 * ts_corr(
            cs_rank(self.o, self.dt), cs_rank(self.v, self.dt), 10, self.g
        )

    def alpha4(self) -> pd.Series:
        """-1 * ts_rank(rank(low), 9)"""
        return -1.0 * ts_rank(cs_rank(self.lo, self.dt), 9, self.g)

    def alpha5(self) -> pd.Series:
        """rank(open - ts_mean(vwap,10)) * (-1 * abs(rank(close - vwap)))"""
        s1 = cs_rank(self.o - ts_mean(self.vwap, 10, self.g), self.dt)
        s2 = -1.0 * ts_abs(cs_rank(self.c - self.vwap, self.dt))
        return s1 * s2

    def alpha6(self) -> pd.Series:
        """-1 * corr(open, volume, 10)"""
        return -1.0 * ts_corr(self.o, self.v, 10, self.g)

    def alpha7(self) -> pd.Series:
        """if(adv20 < volume, -1*ts_rank(abs(delta(close,7)),60)*sign(delta(close,7)), -1)"""
        adv20 = ts_mean(self.v, 20, self.g)
        inner = -1.0 * ts_rank(ts_abs(ts_delta(self.c, 7, self.g)), 60, self.g) * ts_sign(ts_delta(self.c, 7, self.g))
        return quesval2(adv20, self.v, inner, -1.0)

    def alpha8(self) -> pd.Series:
        """-1 * rank(ts_sum(open,5)*ts_sum(returns,5) - delay(ts_sum(open,5)*ts_sum(returns,5),10))"""
        inner = ts_sum(self.o, 5, self.g) * ts_sum(self.returns, 5, self.g)
        return -1.0 * cs_rank(inner - ts_delay(inner, 10, self.g), self.dt)

    def alpha9(self) -> pd.Series:
        """if(0<ts_min(delta(close,1),5), delta(close,1), if(ts_max(delta(close,1),5)<0, delta(close,1), -delta(close,1)))"""
        dc = ts_delta(self.c, 1, self.g)
        cond_min = ts_min(dc, 5, self.g)
        cond_max = ts_max(dc, 5, self.g)
        return quesval(0, cond_min, dc,
                        quesval(0, cond_max, -1.0 * dc, dc))

    def alpha10(self) -> pd.Series:
        """rank(alpha9 内部逻辑)"""
        return cs_rank(self.alpha9(), self.dt)

    def alpha11(self) -> pd.Series:
        """(rank(ts_max(vwap-close,3)) + rank(ts_min(vwap-close,3))) * rank(delta(volume,3))"""
        diff = self.vwap - self.c
        s1 = cs_rank(ts_max(diff, 3, self.g), self.dt)
        s2 = cs_rank(ts_min(diff, 3, self.g), self.dt)
        s3 = cs_rank(ts_delta(self.v, 3, self.g), self.dt)
        return (s1 + s2) * s3

    def alpha12(self) -> pd.Series:
        """sign(delta(volume,1)) * (-1 * delta(close,1))"""
        return ts_sign(ts_delta(self.v, 1, self.g)) * (-1.0 * ts_delta(self.c, 1, self.g))

    def alpha13(self) -> pd.Series:
        """-1 * rank(cov(rank(close), rank(volume), 5))"""
        return -1.0 * cs_rank(
            ts_cov(cs_rank(self.c, self.dt), cs_rank(self.v, self.dt), 5, self.g),
            self.dt,
        )

    def alpha14(self) -> pd.Series:
        """-1 * rank(delta(returns,3)) * corr(open, volume, 10)"""
        return (
            -1.0
            * cs_rank(ts_delta(self.returns, 3, self.g), self.dt)
            * ts_corr(self.o, self.v, 10, self.g)
        )

    def alpha15(self) -> pd.Series:
        """-1 * ts_sum(rank(corr(rank(high), rank(volume), 3)), 3)"""
        inner = ts_corr(
            cs_rank(self.h, self.dt), cs_rank(self.v, self.dt), 3, self.g
        )
        return -1.0 * ts_sum(cs_rank(inner, self.dt), 3, self.g)

    # =================================================================
    # Alpha 16 - 35
    # =================================================================

    def alpha16(self) -> pd.Series:
        """-1 * rank(cov(rank(high), rank(volume), 5))"""
        return -1.0 * cs_rank(
            ts_cov(cs_rank(self.h, self.dt), cs_rank(self.v, self.dt), 5, self.g),
            self.dt,
        )

    def alpha17(self) -> pd.Series:
        """(-1*rank(ts_rank(close,10))) * rank(delta(delta(close,1),1)) * rank(ts_rank(volume/adv20,5))"""
        adv20 = ts_mean(self.v, 20, self.g)
        s1 = -1.0 * cs_rank(ts_rank(self.c, 10, self.g), self.dt)
        s2 = cs_rank(ts_delta(ts_delta(self.c, 1, self.g), 1, self.g), self.dt)
        s3 = cs_rank(ts_rank(safe_div(self.v, adv20), 5, self.g), self.dt)
        return s1 * s2 * s3

    def alpha18(self) -> pd.Series:
        """-1 * rank(ts_std(abs(close-open),5) + (close-open) + corr(close,open,10))"""
        inner = (
            ts_std(ts_abs(self.c - self.o), 5, self.g)
            + (self.c - self.o)
            + ts_corr(self.c, self.o, 10, self.g)
        )
        return -1.0 * cs_rank(inner, self.dt)

    def alpha19(self) -> pd.Series:
        """-sign(delta(close,7)+delta(close,7)) * (1+rank(1+sum(returns,250)))"""
        s1 = -1.0 * ts_sign(ts_delta(self.c, 7, self.g) + (self.c - ts_delay(self.c, 7, self.g)))
        s2 = 1.0 + cs_rank(1.0 + ts_sum(self.returns, 250, self.g), self.dt)
        return s1 * s2

    def alpha20(self) -> pd.Series:
        """-rank(open-delay(high,1)) * rank(open-delay(close,1)) * rank(open-delay(low,1))"""
        s1 = -1.0 * cs_rank(self.o - ts_delay(self.h, 1, self.g), self.dt)
        s2 = cs_rank(self.o - ts_delay(self.c, 1, self.g), self.dt)
        s3 = cs_rank(self.o - ts_delay(self.lo, 1, self.g), self.dt)
        return s1 * s2 * s3

    def alpha21(self) -> pd.Series:
        """条件因子: 基于 close 均值±标准差与 volume/adv20 的分支"""
        ma8 = ts_mean(self.c, 8, self.g)
        std8 = ts_std(self.c, 8, self.g)
        ma2 = ts_mean(self.c, 2, self.g)
        vol_ratio = safe_div(self.v, ts_mean(self.v, 20, self.g))
        return quesval2(
            ma8 + std8, ma2, -1.0,
            quesval2(ma2, ma8 - std8, 1.0, quesval(1, vol_ratio, 1.0, -1.0)),
        )

    def alpha22(self) -> pd.Series:
        """-delta(corr(high,volume,5),5) * rank(std(close,20))"""
        return (
            -1.0
            * ts_delta(ts_corr(self.h, self.v, 5, self.g), 5, self.g)
            * cs_rank(ts_std(self.c, 20, self.g), self.dt)
        )

    def alpha23(self) -> pd.Series:
        """if(mean(high,20)<high, -delta(high,2), 0)"""
        return quesval2(ts_mean(self.h, 20, self.g), self.h,
                        -1.0 * ts_delta(self.h, 2, self.g), 0.0)

    def alpha24(self) -> pd.Series:
        """if delta(sma100,100)/delay(close,100) > 0.05: -delta(close,3) else: -(close-ts_min(close,100))"""
        sma100 = ts_sum(self.c, 100, self.g) / 100.0
        cond = safe_div(ts_delta(sma100, 100, self.g), ts_delay(self.c, 100, self.g))
        return quesval(0.05, cond, -1.0 * ts_delta(self.c, 3, self.g),
                        -1.0 * (self.c - ts_min(self.c, 100, self.g)))

    def alpha25(self) -> pd.Series:
        """rank(-returns * adv20 * vwap * (high-close))"""
        return cs_rank(
            -1.0 * self.returns * ts_mean(self.v, 20, self.g) * self.vwap * (self.h - self.c),
            self.dt,
        )

    def alpha26(self) -> pd.Series:
        """-ts_max(corr(ts_rank(volume,5), ts_rank(high,5), 5), 3)"""
        return -1.0 * ts_max(
            ts_corr(ts_rank(self.v, 5, self.g), ts_rank(self.h, 5, self.g), 5, self.g),
            3, self.g,
        )

    def alpha27(self) -> pd.Series:
        """if rank(mean(corr(rank(volume),rank(vwap),6),2)) > 0.5: -1 else 1"""
        inner = ts_mean(
            ts_corr(cs_rank(self.v, self.dt), cs_rank(self.vwap, self.dt), 6, self.g),
            2, self.g,
        )
        return quesval(0.5, cs_rank(inner, self.dt), -1.0, 1.0)

    def alpha28(self) -> pd.Series:
        """scale(corr(adv20, low, 5) + (high+low)/2 - close)"""
        adv20 = ts_mean(self.v, 20, self.g)
        return cs_scale(
            ts_corr(adv20, self.lo, 5, self.g) + (self.h + self.lo) / 2.0 - self.c,
            self.dt,
        )

    def alpha29(self) -> pd.Series:
        """简化: min(product(rank(rank(scale(log(sum(min(rank(rank(-rank(delta(close-1,5)))),2),1))))),1),5) + ts_rank(delay(-returns,6),5)"""
        inner = -1.0 * cs_rank(ts_delta(self.c - 1.0, 5, self.g), self.dt)
        r1 = cs_rank(cs_rank(inner, self.dt), self.dt)
        s = ts_sum(ts_min(r1, 2, self.g), 1, self.g)
        logged = cs_scale(ts_log(s.clip(lower=1e-10)), self.dt)
        r2 = cs_rank(cs_rank(logged, self.dt), self.dt)
        part1 = ts_min(ts_product(r2, 1, self.g), 5, self.g)
        part2 = ts_rank(ts_delay(-1.0 * self.returns, 6, self.g), 5, self.g)
        return part1 + part2

    def alpha30(self) -> pd.Series:
        """((rank(sign(close-delay(close,1))+sign(delay(close,1)-delay(close,2))+sign(delay(close,2)-delay(close,3)))*-1+1)*sum(volume,5))/sum(volume,20)"""
        dc1 = self.c - ts_delay(self.c, 1, self.g)
        dc2 = ts_delay(self.c, 1, self.g) - ts_delay(self.c, 2, self.g)
        dc3 = ts_delay(self.c, 2, self.g) - ts_delay(self.c, 3, self.g)
        signs = ts_sign(dc1) + ts_sign(dc2) + ts_sign(dc3)
        numer = (cs_rank(signs, self.dt) * -1.0 + 1.0) * ts_sum(self.v, 5, self.g)
        return safe_div(numer, ts_sum(self.v, 20, self.g))

    def alpha31(self) -> pd.Series:
        """rank(rank(rank(decay_linear(-rank(rank(delta(close,10))),10)))) + rank(-delta(close,3)) + sign(scale(corr(adv20,low,12)))"""
        inner = -1.0 * cs_rank(cs_rank(ts_delta(self.c, 10, self.g), self.dt), self.dt)
        dl = ts_decay_linear(inner, 10, self.g)
        s1 = cs_rank(cs_rank(cs_rank(dl, self.dt), self.dt), self.dt)
        s2 = cs_rank(-1.0 * ts_delta(self.c, 3, self.g), self.dt)
        adv20 = ts_mean(self.v, 20, self.g)
        s3 = ts_sign(cs_scale(ts_corr(adv20, self.lo, 12, self.g), self.dt))
        return s1 + s2 + s3

    def alpha32(self) -> pd.Series:
        """scale(sma7 - close) + 20*scale(corr(vwap, delay(close,5), 230))"""
        sma7 = ts_sum(self.c, 7, self.g) / 7.0
        s1 = cs_scale(sma7 - self.c, self.dt)
        s2 = 20.0 * cs_scale(ts_corr(self.vwap, ts_delay(self.c, 5, self.g), 230, self.g), self.dt)
        return s1 + s2

    def alpha33(self) -> pd.Series:
        """rank(-(1 - open/close))"""
        return cs_rank(-1.0 * (1.0 - safe_div(self.o, self.c)), self.dt)

    def alpha34(self) -> pd.Series:
        """rank(1 - rank(std(returns,2)/std(returns,5)) + 1 - rank(delta(close,1)))"""
        s1 = 1.0 - cs_rank(
            safe_div(ts_std(self.returns, 2, self.g), ts_std(self.returns, 5, self.g)),
            self.dt,
        )
        s2 = 1.0 - cs_rank(ts_delta(self.c, 1, self.g), self.dt)
        return cs_rank(s1 + s2, self.dt)

    def alpha35(self) -> pd.Series:
        """ts_rank(volume,32) * (1-ts_rank(close+high-low,16)) * (1-ts_rank(returns,32))"""
        s1 = ts_rank(self.v, 32, self.g)
        s2 = 1.0 - ts_rank(self.c + self.h - self.lo, 16, self.g)
        s3 = 1.0 - ts_rank(self.returns, 32, self.g)
        return s1 * s2 * s3

    # =================================================================
    # Alpha 36 - 55
    # =================================================================

    def alpha36(self) -> pd.Series:
        """2.21*rank(corr(close-open,delay(vol,1),15)) + 0.7*rank(open-close) + 0.73*rank(ts_rank(delay(-ret,6),5)) + rank(abs(corr(vwap,adv20,6))) + 0.6*rank((sma200-open)*(close-open))"""
        adv20 = ts_mean(self.v, 20, self.g)
        s1 = 2.21 * cs_rank(ts_corr(self.c - self.o, ts_delay(self.v, 1, self.g), 15, self.g), self.dt)
        s2 = 0.7 * cs_rank(self.o - self.c, self.dt)
        s3 = 0.73 * cs_rank(ts_rank(ts_delay(-1.0 * self.returns, 6, self.g), 5, self.g), self.dt)
        s4 = cs_rank(ts_abs(ts_corr(self.vwap, adv20, 6, self.g)), self.dt)
        sma200 = ts_sum(self.c, 200, self.g) / 200.0
        s5 = 0.6 * cs_rank((sma200 - self.o) * (self.c - self.o), self.dt)
        return s1 + s2 + s3 + s4 + s5

    def alpha37(self) -> pd.Series:
        """rank(corr(delay(open-close,1),close,200)) + rank(open-close)"""
        s1 = cs_rank(ts_corr(ts_delay(self.o - self.c, 1, self.g), self.c, 200, self.g), self.dt)
        s2 = cs_rank(self.o - self.c, self.dt)
        return s1 + s2

    def alpha38(self) -> pd.Series:
        """-1 * rank(ts_rank(close,10)) * rank(close/open)"""
        return (
            -1.0
            * cs_rank(ts_rank(self.c, 10, self.g), self.dt)
            * cs_rank(safe_div(self.c, self.o), self.dt)
        )

    def alpha39(self) -> pd.Series:
        """-rank(delta(close,7)*(1-rank(decay_linear(vol/adv20,9)))) * (1+rank(sum(ret,250)))"""
        adv20 = ts_mean(self.v, 20, self.g)
        dl = ts_decay_linear(safe_div(self.v, adv20), 9, self.g)
        s1 = -1.0 * cs_rank(
            ts_delta(self.c, 7, self.g) * (1.0 - cs_rank(dl, self.dt)),
            self.dt,
        )
        s2 = 1.0 + cs_rank(ts_sum(self.returns, 250, self.g), self.dt)
        return s1 * s2

    def alpha40(self) -> pd.Series:
        """-rank(std(high,10)) * corr(high,volume,10)"""
        return (
            -1.0
            * cs_rank(ts_std(self.h, 10, self.g), self.dt)
            * ts_corr(self.h, self.v, 10, self.g)
        )

    def alpha41(self) -> pd.Series:
        """pow(high*low, 0.5) - vwap"""
        return pow1(self.h * self.lo, 0.5) - self.vwap

    def alpha42(self) -> pd.Series:
        """rank(vwap-close) / rank(vwap+close)"""
        return safe_div(
            cs_rank(self.vwap - self.c, self.dt),
            cs_rank(self.vwap + self.c, self.dt),
        )

    def alpha43(self) -> pd.Series:
        """ts_rank(volume/adv20, 20) * ts_rank(-delta(close,7), 8)"""
        adv20 = ts_mean(self.v, 20, self.g)
        return (
            ts_rank(safe_div(self.v, adv20), 20, self.g)
            * ts_rank(-1.0 * ts_delta(self.c, 7, self.g), 8, self.g)
        )

    def alpha44(self) -> pd.Series:
        """-corr(high, rank(volume), 5)"""
        return -1.0 * ts_corr(self.h, cs_rank(self.v, self.dt), 5, self.g)

    def alpha45(self) -> pd.Series:
        """-rank(sum(delay(close,5),20)/20) * corr(close,vol,2) * rank(corr(sum(close,5),sum(close,20),2))"""
        sma5d = ts_sum(ts_delay(self.c, 5, self.g), 20, self.g) / 20.0
        s1 = -1.0 * cs_rank(sma5d, self.dt)
        s2 = ts_corr(self.c, self.v, 2, self.g)
        s3 = cs_rank(ts_corr(ts_sum(self.c, 5, self.g), ts_sum(self.c, 20, self.g), 2, self.g), self.dt)
        return s1 * s2 * s3

    def alpha46(self) -> pd.Series:
        """条件因子: 基于 10日/20日价格动量的分支"""
        d20_10 = (ts_delay(self.c, 20, self.g) - ts_delay(self.c, 10, self.g)) / 10.0
        d10_0 = (ts_delay(self.c, 10, self.g) - self.c) / 10.0
        cond = d20_10 - d10_0
        return quesval(0.25, cond, -1.0,
                        quesval(0, cond, -1.0 * ts_delta(self.c, 1, self.g), 1.0))

    def alpha47(self) -> pd.Series:
        """(rank(1/close)*vol/adv20 * high*rank(high-close)/(sum(high,5)/5)) - rank(vwap-delay(vwap,5))"""
        adv20 = ts_mean(self.v, 20, self.g)
        sma_h5 = ts_sum(self.h, 5, self.g) / 5.0
        part1 = (
            cs_rank(safe_div(pd.Series(1.0, index=self.c.index), self.c), self.dt)
            * safe_div(self.v, adv20)
            * self.h
            * cs_rank(self.h - self.c, self.dt)
            / sma_h5.replace(0, np.nan)
        )
        part2 = cs_rank(self.vwap - ts_delay(self.vwap, 5, self.g), self.dt)
        return part1 - part2

    # alpha48: skipped (IndNeutralize)

    def alpha49(self) -> pd.Series:
        """条件因子: 基于价格动量的分支"""
        d20_10 = (ts_delay(self.c, 20, self.g) - ts_delay(self.c, 10, self.g)) / 10.0
        d10_0 = (ts_delay(self.c, 10, self.g) - self.c) / 10.0
        cond = d20_10 - d10_0
        return quesval(-0.1, cond, -1.0 * ts_delta(self.c, 1, self.g), 1.0)

    def alpha50(self) -> pd.Series:
        """-ts_max(rank(corr(rank(volume), rank(vwap), 5)), 5)"""
        return -1.0 * ts_max(
            ts_corr(cs_rank(self.v, self.dt), cs_rank(self.vwap, self.dt), 5, self.g),
            5, self.g,
        )

    def alpha51(self) -> pd.Series:
        """条件因子: 类似 alpha49 但阈值 -0.05"""
        d20_10 = (ts_delay(self.c, 20, self.g) - ts_delay(self.c, 10, self.g)) / 10.0
        d10_0 = (ts_delay(self.c, 10, self.g) - self.c) / 10.0
        cond = d20_10 - d10_0
        return quesval(-0.05, cond, -1.0 * ts_delta(self.c, 1, self.g), 1.0)

    def alpha52(self) -> pd.Series:
        """((-ts_min(low,5))+delay(ts_min(low,5),5)) * rank((sum(ret,240)-sum(ret,20))/220) * ts_rank(volume,5)"""
        tmin5 = ts_min(self.lo, 5, self.g)
        s1 = -1.0 * tmin5 + ts_delay(tmin5, 5, self.g)
        s2 = cs_rank(
            safe_div(ts_sum(self.returns, 240, self.g) - ts_sum(self.returns, 20, self.g), 220.0),
            self.dt,
        )
        s3 = ts_rank(self.v, 5, self.g)
        return s1 * s2 * s3

    def alpha53(self) -> pd.Series:
        """-delta(((close-low)-(high-close))/(close-low), 9)"""
        inner = safe_div((self.c - self.lo) - (self.h - self.c), self.c - self.lo)
        return -1.0 * ts_delta(inner, 9, self.g)

    def alpha54(self) -> pd.Series:
        """(-(low-close)*pow(open,5)) / ((low-high)*pow(close,5))"""
        numer = -1.0 * (self.lo - self.c) * pow1(self.o, 5.0)
        denom = (self.lo - self.h) * pow1(self.c, 5.0)
        return safe_div(numer, denom)

    def alpha55(self) -> pd.Series:
        """-corr(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))), rank(volume), 6)"""
        lo12 = ts_min(self.lo, 12, self.g)
        hi12 = ts_max(self.h, 12, self.g)
        inner = safe_div(self.c - lo12, hi12 - lo12)
        return -1.0 * ts_corr(cs_rank(inner, self.dt), cs_rank(self.v, self.dt), 6, self.g)

    # =================================================================
    # Alpha 56 - 80
    # =================================================================

    # alpha56: skipped (IndNeutralize)

    def alpha57(self) -> pd.Series:
        """-(close - vwap) / decay_linear(rank(ts_argmax(close,30)), 2)"""
        dl = ts_decay_linear(cs_rank(ts_argmax(self.c, 30, self.g), self.dt), 2, self.g)
        return -1.0 * safe_div(self.c - self.vwap, dl)

    # alpha58: skipped (IndNeutralize)
    # alpha59: skipped (IndNeutralize)

    def alpha60(self) -> pd.Series:
        """-((2*scale(rank(((close-low)-(high-close))/(high-low)*volume))) - scale(rank(ts_argmax(close,10))))"""
        inner = safe_div((self.c - self.lo) - (self.h - self.c), self.h - self.lo) * self.v
        s1 = 2.0 * cs_scale(cs_rank(inner, self.dt), self.dt)
        s2 = cs_scale(cs_rank(ts_argmax(self.c, 10, self.g), self.dt), self.dt)
        return -1.0 * (s1 - s2)

    def alpha61(self) -> pd.Series:
        """rank(vwap-ts_min(vwap,16)) < rank(corr(vwap, adv180, 17))"""
        adv180 = ts_mean(self.v, 180, self.g)
        left = cs_rank(self.vwap - ts_min(self.vwap, 16, self.g), self.dt)
        right = cs_rank(ts_corr(self.vwap, adv180, 17, self.g), self.dt)
        return (left < right).astype(float)

    def alpha62(self) -> pd.Series:
        """-corr(high, rank(adv20), 5) + rank(open-close)"""
        adv20 = ts_mean(self.v, 20, self.g)
        return (
            -1.0 * ts_corr(self.h, cs_rank(adv20, self.dt), 5, self.g)
            + cs_rank(self.o - self.c, self.dt)
        )

    # alpha63: skipped (IndNeutralize)

    def alpha64(self) -> pd.Series:
        """rank(corr(sum(open*0.178404+low*0.821596,12),sum(adv120,12),16))
           < rank(delta(open*0.178404+high*0.821596,3))"""
        adv120 = ts_mean(self.v, 120, self.g)
        wt1 = self.o * 0.178404 + self.lo * 0.821596
        wt2 = self.o * 0.178404 + self.h * 0.821596
        left = cs_rank(ts_corr(ts_sum(wt1, 12, self.g), ts_sum(adv120, 12, self.g), 16, self.g), self.dt)
        right = cs_rank(ts_delta(wt2, 3, self.g), self.dt)
        return (left < right).astype(float)

    def alpha65(self) -> pd.Series:
        """rank(corr(open*0.00817205+vwap*0.99183,sum(adv60,8),6)) < rank(open-ts_min(open,14))"""
        adv60 = ts_mean(self.v, 60, self.g)
        wt = self.o * 0.00817205 + self.vwap * 0.99183
        left = cs_rank(ts_corr(wt, ts_sum(adv60, 8, self.g), 6, self.g), self.dt)
        right = cs_rank(self.o - ts_min(self.o, 14, self.g), self.dt)
        return (left < right).astype(float)

    def alpha66(self) -> pd.Series:
        """rank(decay_linear(delta(vwap,3),7)) + ts_rank(decay_linear((close*0.96633+low*0.03367-vwap)/(open-high+close),11),7)"""
        dl1 = ts_decay_linear(ts_delta(self.vwap, 3, self.g), 7, self.g)
        inner = safe_div(
            self.c * 0.96633 + self.lo * 0.03367 - self.vwap,
            self.o - self.h + self.c,
        )
        dl2 = ts_decay_linear(inner, 11, self.g)
        return cs_rank(dl1, self.dt) + ts_rank(dl2, 7, self.g)

    # alpha67: skipped (IndNeutralize)

    def alpha68(self) -> pd.Series:
        """ts_rank(corr(rank(high),rank(adv15),8),13) < rank(delta(close*0.518371+low*0.481629,1))"""
        adv15 = ts_mean(self.v, 15, self.g)
        left = ts_rank(
            ts_corr(cs_rank(self.h, self.dt), cs_rank(adv15, self.dt), 8, self.g),
            13, self.g,
        )
        wt = self.c * 0.518371 + self.lo * 0.481629
        right = cs_rank(ts_delta(wt, 1, self.g), self.dt)
        return (left < right).astype(float)

    # alpha69: skipped (IndNeutralize)
    # alpha70: skipped (IndNeutralize)

    def alpha71(self) -> pd.Series:
        """max(ts_rank(decay_linear(corr(ts_rank(close,3),ts_rank(adv180,12),18),4),15), ts_rank(decay_linear(rank(low+open-2*vwap)^2,16),4))"""
        adv180 = ts_mean(self.v, 180, self.g)
        dl1 = ts_decay_linear(
            ts_corr(ts_rank(self.c, 3, self.g), ts_rank(adv180, 12, self.g), 18, self.g),
            4, self.g,
        )
        s1 = ts_rank(dl1, 15, self.g)
        inner2 = pow1(cs_rank(self.lo + self.o - 2.0 * self.vwap, self.dt), 2.0)
        dl2 = ts_decay_linear(inner2, 16, self.g)
        s2 = ts_rank(dl2, 4, self.g)
        return ts_greater(s1, s2)

    def alpha72(self) -> pd.Series:
        """rank(decay_linear(corr(close*0.35+vwap*0.65,sum(adv40,4),18),8)) / rank(decay_linear(corr(ts_rank(high,3),ts_rank(adv180,12),18),4))"""
        adv40 = ts_mean(self.v, 40, self.g)
        adv180 = ts_mean(self.v, 180, self.g)
        wt = self.c * 0.35 + self.vwap * 0.65
        dl1 = ts_decay_linear(ts_corr(wt, ts_sum(adv40, 4, self.g), 18, self.g), 8, self.g)
        dl2 = ts_decay_linear(
            ts_corr(ts_rank(self.h, 3, self.g), ts_rank(adv180, 12, self.g), 18, self.g),
            4, self.g,
        )
        return safe_div(cs_rank(dl1, self.dt), cs_rank(dl2, self.dt))

    def alpha73(self) -> pd.Series:
        """max(rank(decay_linear(delta(vwap,4),2)),ts_rank(decay_linear(((delta(open*0.147155+low*0.852845,2)/(open*0.147155+low*0.852845))*-1),3),16))"""
        dl1 = ts_decay_linear(ts_delta(self.vwap, 4, self.g), 2, self.g)
        s1 = cs_rank(dl1, self.dt)
        wt = self.o * 0.147155 + self.lo * 0.852845
        inner = -1.0 * safe_div(ts_delta(wt, 2, self.g), wt)
        dl2 = ts_decay_linear(inner, 3, self.g)
        s2 = ts_rank(dl2, 16, self.g)
        return ts_greater(s1, s2)

    def alpha74(self) -> pd.Series:
        """rank(corr(close,sum(adv30,37),15)) < rank(corr(rank(high*0.0261661+vwap*0.9738339),rank(volume),11))"""
        adv30 = ts_mean(self.v, 30, self.g)
        left = cs_rank(ts_corr(self.c, ts_sum(adv30, 37, self.g), 15, self.g), self.dt)
        wt = self.h * 0.0261661 + self.vwap * 0.9738339
        right = cs_rank(
            ts_corr(cs_rank(wt, self.dt), cs_rank(self.v, self.dt), 11, self.g),
            self.dt,
        )
        return (left < right).astype(float)

    def alpha75(self) -> pd.Series:
        """rank(corr(vwap,volume,4)) < rank(corr(rank(low),rank(adv50),12))"""
        adv50 = ts_mean(self.v, 50, self.g)
        left = cs_rank(ts_corr(self.vwap, self.v, 4, self.g), self.dt)
        right = cs_rank(
            ts_corr(cs_rank(self.lo, self.dt), cs_rank(adv50, self.dt), 12, self.g),
            self.dt,
        )
        return (left < right).astype(float)

    # alpha76: skipped (IndNeutralize)

    def alpha77(self) -> pd.Series:
        """min(rank(decay_linear(high*0.5+low*0.5-vwap,20)), rank(decay_linear(corr(high*0.5+low*0.5,adv40,3),5)))"""
        hl_avg = self.h * 0.5 + self.lo * 0.5
        adv40 = ts_mean(self.v, 40, self.g)
        dl1 = ts_decay_linear(hl_avg - self.vwap, 20, self.g)
        dl2 = ts_decay_linear(ts_corr(hl_avg, adv40, 3, self.g), 5, self.g)
        return ts_less(cs_rank(dl1, self.dt), cs_rank(dl2, self.dt))

    def alpha78(self) -> pd.Series:
        """rank(corr(sum(low*0.352233+vwap*0.647767,19),sum(adv40,19),6))^rank(corr(rank(vwap),rank(volume),5))"""
        adv40 = ts_mean(self.v, 40, self.g)
        wt = self.lo * 0.352233 + self.vwap * 0.647767
        s1 = cs_rank(
            ts_corr(ts_sum(wt, 19, self.g), ts_sum(adv40, 19, self.g), 6, self.g),
            self.dt,
        )
        s2 = cs_rank(
            ts_corr(cs_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 5, self.g),
            self.dt,
        )
        return pow1(s1, s2)

    # alpha79: skipped (IndNeutralize)
    # alpha80: skipped (IndNeutralize)

    # =================================================================
    # Alpha 81 - 101
    # =================================================================

    def alpha81(self) -> pd.Series:
        """rank(log(product(rank(rank(corr(vwap,sum(adv10,49),8))^4),15))) < rank(corr(rank(vwap),rank(volume),5))"""
        adv10 = ts_mean(self.v, 10, self.g)
        inner = pow1(cs_rank(cs_rank(ts_corr(self.vwap, ts_sum(adv10, 49, self.g), 8, self.g), self.dt), self.dt), 4.0)
        left = cs_rank(ts_log(ts_product(inner, 15, self.g)), self.dt)
        right = cs_rank(ts_corr(cs_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 5, self.g), self.dt)
        return (left < right).astype(float)

    # alpha82: skipped (IndNeutralize)

    def alpha83(self) -> pd.Series:
        """rank(delay((high-low)/(sum(close,5)/5), 2)) * rank(rank(volume)) / ((high-low)/(sum(close,5)/5)) / (vwap-close)"""
        hl_ratio = safe_div(self.h - self.lo, ts_sum(self.c, 5, self.g) / 5.0)
        s1 = cs_rank(ts_delay(hl_ratio, 2, self.g), self.dt)
        s2 = cs_rank(cs_rank(self.v, self.dt), self.dt)
        denom = hl_ratio * (self.vwap - self.c)
        return safe_div(s1 * s2, denom.replace(0, np.nan))

    def alpha84(self) -> pd.Series:
        """pow(ts_rank(vwap-ts_max(vwap,15),20), delta(close,4))"""
        return pow1(
            ts_rank(self.vwap - ts_max(self.vwap, 15, self.g), 20, self.g),
            ts_delta(self.c, 4, self.g),
        )

    def alpha85(self) -> pd.Series:
        """rank(corr(high*0.876703+close*0.123297, adv30, 9))^rank(corr(ts_rank(high*0.876703+vwap*0.123297,3),ts_rank(adv60,4),7))"""
        adv30 = ts_mean(self.v, 30, self.g)
        adv60 = ts_mean(self.v, 60, self.g)
        wt1 = self.h * 0.876703 + self.c * 0.123297
        wt2 = self.h * 0.876703 + self.vwap * 0.123297
        s1 = cs_rank(ts_corr(wt1, adv30, 9, self.g), self.dt)
        s2 = cs_rank(ts_corr(ts_rank(wt2, 3, self.g), ts_rank(adv60, 4, self.g), 7, self.g), self.dt)
        return pow1(s1, s2)

    def alpha86(self) -> pd.Series:
        """ts_rank(corr(close,sum(adv20,14),6),20) < rank(open+close-2*vwap)"""
        adv20 = ts_mean(self.v, 20, self.g)
        left = ts_rank(ts_corr(self.c, ts_sum(adv20, 14, self.g), 6, self.g), 20, self.g)
        right = cs_rank(self.o + self.c - 2.0 * self.vwap, self.dt)
        return (left < right).astype(float)

    # alpha87: skipped (IndNeutralize)

    def alpha88(self) -> pd.Series:
        """min(rank(decay_linear(close*0.5+high*0.5-vwap,20)), rank(decay_linear(corr(high*0.5+low*0.5,adv40,3),5)))"""
        # NOTE: alpha88 vs alpha77 略有区别 (第一项权重不同)
        adv40 = ts_mean(self.v, 40, self.g)
        dl1 = ts_decay_linear(self.c * 0.5 + self.h * 0.5 - self.vwap, 20, self.g)
        hl_avg = self.h * 0.5 + self.lo * 0.5
        dl2 = ts_decay_linear(ts_corr(hl_avg, adv40, 3, self.g), 5, self.g)
        return ts_less(cs_rank(dl1, self.dt), cs_rank(dl2, self.dt))

    # alpha89: skipped (IndNeutralize)
    # alpha90: skipped (IndNeutralize)
    # alpha91: skipped (IndNeutralize)

    def alpha92(self) -> pd.Series:
        """min(ts_rank(decay_linear(((high+low)/2+close)<low+abs(close-open)/2,14),18), ts_rank(decay_linear(corr(rank(low),rank(adv30),7),6),6))"""
        adv30 = ts_mean(self.v, 30, self.g)
        inner1 = ((self.h + self.lo) / 2.0 + self.c < self.lo + ts_abs(self.c - self.o) / 2.0).astype(float)
        dl1 = ts_decay_linear(inner1, 14, self.g)
        s1 = ts_rank(dl1, 18, self.g)
        dl2 = ts_decay_linear(
            ts_corr(cs_rank(self.lo, self.dt), cs_rank(adv30, self.dt), 7, self.g),
            6, self.g,
        )
        s2 = ts_rank(dl2, 6, self.g)
        return ts_less(s1, s2)

    # alpha93: skipped (IndNeutralize)

    def alpha94(self) -> pd.Series:
        """-rank(vwap-ts_min(vwap,11)) * ts_rank(corr(ts_rank(close,19),ts_rank(adv60,4),18),2)"""
        adv60 = ts_mean(self.v, 60, self.g)
        s1 = -1.0 * cs_rank(self.vwap - ts_min(self.vwap, 11, self.g), self.dt)
        s2 = ts_rank(
            ts_corr(ts_rank(self.c, 19, self.g), ts_rank(adv60, 4, self.g), 18, self.g),
            2, self.g,
        )
        return s1 * s2

    def alpha95(self) -> pd.Series:
        """rank(open-ts_min(open,12)) < ts_rank(rank(corr(sum(high*0.5+low*0.5,19),sum(adv40,19),12))^5,11)"""
        adv40 = ts_mean(self.v, 40, self.g)
        left = cs_rank(self.o - ts_min(self.o, 12, self.g), self.dt)
        hl = self.h * 0.5 + self.lo * 0.5
        inner = pow1(cs_rank(ts_corr(ts_sum(hl, 19, self.g), ts_sum(adv40, 19, self.g), 12, self.g), self.dt), 5.0)
        right = ts_rank(inner, 11, self.g)
        return (left < right).astype(float)

    def alpha96(self) -> pd.Series:
        """max(ts_rank(decay_linear(corr(rank(vwap),rank(volume),3),7),3), ts_rank(decay_linear(ts_argmax(corr(ts_rank(close,7),ts_rank(adv60,4),4),12),14),13))"""
        adv60 = ts_mean(self.v, 60, self.g)
        dl1 = ts_decay_linear(
            ts_corr(cs_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 3, self.g),
            7, self.g,
        )
        s1 = ts_rank(dl1, 3, self.g)
        inner2 = ts_argmax(
            ts_corr(ts_rank(self.c, 7, self.g), ts_rank(adv60, 4, self.g), 4, self.g),
            12, self.g,
        )
        dl2 = ts_decay_linear(inner2, 14, self.g)
        s2 = ts_rank(dl2, 13, self.g)
        return ts_greater(s1, s2)

    # alpha97: skipped (IndNeutralize)

    def alpha98(self) -> pd.Series:
        """rank(decay_linear(corr(vwap,sum(adv5,26),4),7)) - rank(decay_linear(ts_rank(ts_argmin(corr(rank(open),rank(adv15),20),8),6),8))"""
        adv5 = ts_mean(self.v, 5, self.g)
        adv15 = ts_mean(self.v, 15, self.g)
        dl1 = ts_decay_linear(
            ts_corr(self.vwap, ts_sum(adv5, 26, self.g), 4, self.g),
            7, self.g,
        )
        inner2 = ts_argmin(
            ts_corr(cs_rank(self.o, self.dt), cs_rank(adv15, self.dt), 20, self.g),
            8, self.g,
        )
        dl2 = ts_decay_linear(ts_rank(inner2, 6, self.g), 8, self.g)
        return cs_rank(dl1, self.dt) - cs_rank(dl2, self.dt)

    def alpha99(self) -> pd.Series:
        """rank(corr(sum(high*0.5+low*0.5,19),sum(adv60,19),8)) < rank(corr(low,volume,6))"""
        adv60 = ts_mean(self.v, 60, self.g)
        hl = self.h * 0.5 + self.lo * 0.5
        left = cs_rank(ts_corr(ts_sum(hl, 19, self.g), ts_sum(adv60, 19, self.g), 8, self.g), self.dt)
        right = cs_rank(ts_corr(self.lo, self.v, 6, self.g), self.dt)
        return (left < right).astype(float)

    # alpha100: skipped (IndNeutralize)

    def alpha101(self) -> pd.Series:
        """(close-open) / ((high-low)+0.001)"""
        return safe_div(self.c - self.o, self.h - self.lo + 0.001)


# =================================================================
# 便捷函数
# =================================================================

def compute_alpha101(
    df: pd.DataFrame,
    alphas: list[int] | None = None,
) -> pd.DataFrame:
    """计算 WorldQuant Alpha101 因子（便捷入口）。

    Args:
        df: 包含 datetime, symbol, open, high, low, close, volume 的面板数据。
        alphas: 要计算的因子编号列表。None=全部。

    Returns:
        包含 datetime, symbol, alpha1..alphaN 的 DataFrame。
    """
    calc = Alpha101(df)
    return calc.compute(alphas=alphas)


def get_alpha101_factor_names(include_skipped: bool = False) -> list[str]:
    """返回所有 Alpha101 因子名称列表。"""
    if include_skipped:
        return [f"alpha{i}" for i in range(1, 102)]
    return [f"alpha{i}" for i in range(1, 102) if i not in SKIPPED_ALPHAS]
