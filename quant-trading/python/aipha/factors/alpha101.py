"""Alpha101 因子库 — WorldQuant 101 公式化 Alpha 因子。

基于 Kakushadze (2016) "101 Formulaic Alphas"，
使用 operators.py 中的运算符在面板数据上高效计算。

用法::

    calc = Alpha101(df)  # df 含 datetime, symbol, open, high, low, close, volume
    result = calc.compute()          # 全部可用因子
    result = calc.compute([1,2,3])   # 指定因子
    a1 = calc.alpha1()               # 单个因子
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.operators import (
    cs_rank, cs_scale, pow1, pow2, quesval, quesval2, safe_div,
    ts_abs, ts_argmax, ts_argmin, ts_corr, ts_cov, ts_decay_linear,
    ts_delay, ts_delta, ts_greater, ts_less, ts_log, ts_max, ts_mean,
    ts_min, ts_product, ts_rank, ts_sign, ts_std, ts_sum,
)

# IndNeutralize 依赖的因子 — 需行业中性化模块，标记跳过
SKIPPED_ALPHAS = {48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100}


class Alpha101:
    """WorldQuant 101 公式化 Alpha 因子计算器。

    输入 DataFrame 必须包含列: datetime, symbol, open, high, low, close, volume
    可选列: vwap (若缺失则用 (high+low+close)/3 近似)
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self.df.sort_values(["symbol", "datetime"], inplace=True)
        self.df.reset_index(drop=True, inplace=True)

        self.g = self.df["symbol"]
        self.dt = self.df["datetime"]
        self.o = self.df["open"].astype(float)
        self.h = self.df["high"].astype(float)
        self.lo = self.df["low"].astype(float)
        self.c = self.df["close"].astype(float)
        self.v = self.df["volume"].astype(float)

        if "vwap" in self.df.columns:
            self.vwap = self.df["vwap"].astype(float)
        else:
            self.vwap = (self.h + self.lo + self.c) / 3.0

        self.returns = self.c / ts_delay(self.c, 1, self.g) - 1.0

    def compute(self, alphas: list[int] | None = None) -> pd.DataFrame:
        """批量计算 Alpha 因子。"""
        result = self.df[["datetime", "symbol"]].copy()
        if alphas is None:
            alphas = [i for i in range(1, 102) if i not in SKIPPED_ALPHAS]

        factors: dict[str, pd.Series] = {}
        for i in alphas:
            if i in SKIPPED_ALPHAS:
                continue
            method = getattr(self, f"alpha{i}", None)
            if method is not None:
                try:
                    factors[f"alpha{i}"] = method()
                except Exception:
                    factors[f"alpha{i}"] = pd.Series(np.nan, index=result.index)

        if factors:
            result = pd.concat([result, pd.DataFrame(factors, index=result.index)], axis=1)
        return result

    # ═══════════════════════════════════════════════════════════
    # Alpha 1 - 20
    # ═══════════════════════════════════════════════════════════

    def alpha1(self) -> pd.Series:
        inner = quesval(0, self.returns, self.c, ts_std(self.returns, 20, self.g))
        return cs_rank(ts_argmax(pow1(inner, 2.0), 5, self.g), self.dt) - 0.5

    def alpha2(self) -> pd.Series:
        s1 = cs_rank(ts_delta(ts_log(self.v), 2, self.g), self.dt)
        s2 = cs_rank(safe_div(self.c - self.o, self.o), self.dt)
        return -1.0 * ts_corr(s1, s2, 6, self.g)

    def alpha3(self) -> pd.Series:
        return -1.0 * ts_corr(cs_rank(self.o, self.dt), cs_rank(self.v, self.dt), 10, self.g)

    def alpha4(self) -> pd.Series:
        return -1.0 * ts_rank(cs_rank(self.lo, self.dt), 9, self.g)

    def alpha5(self) -> pd.Series:
        s1 = cs_rank(self.o - ts_mean(self.vwap, 10, self.g), self.dt)
        s2 = -1.0 * ts_abs(cs_rank(self.c - self.vwap, self.dt))
        return s1 * s2

    def alpha6(self) -> pd.Series:
        return -1.0 * ts_corr(self.o, self.v, 10, self.g)

    def alpha7(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        inner = -1.0 * ts_rank(ts_abs(ts_delta(self.c, 7, self.g)), 60, self.g) * ts_sign(ts_delta(self.c, 7, self.g))
        return quesval2(adv20, self.v, inner, -1.0)

    def alpha8(self) -> pd.Series:
        inner = ts_sum(self.o, 5, self.g) * ts_sum(self.returns, 5, self.g)
        return -1.0 * cs_rank(inner - ts_delay(inner, 10, self.g), self.dt)

    def alpha9(self) -> pd.Series:
        dc = ts_delta(self.c, 1, self.g)
        cond_min = ts_min(dc, 5, self.g)
        cond_max = ts_max(dc, 5, self.g)
        return quesval(0, cond_min, dc, quesval(0, cond_max, -1.0 * dc, dc))

    def alpha10(self) -> pd.Series:
        dc = ts_delta(self.c, 1, self.g)
        cond_min = ts_min(dc, 4, self.g)
        cond_max = ts_max(dc, 4, self.g)
        return cs_rank(quesval(0, cond_min, dc, quesval(0, cond_max, -1.0 * dc, dc)), self.dt)

    def alpha11(self) -> pd.Series:
        return (cs_rank(ts_max(self.vwap - self.c, 3, self.g), self.dt)
                + cs_rank(ts_min(self.vwap - self.c, 3, self.g), self.dt)) * cs_rank(ts_delta(self.v, 3, self.g), self.dt)

    def alpha12(self) -> pd.Series:
        return ts_sign(ts_delta(self.v, 1, self.g)) * (-1.0 * ts_delta(self.c, 1, self.g))

    def alpha13(self) -> pd.Series:
        return -1.0 * cs_rank(ts_cov(cs_rank(self.c, self.dt), cs_rank(self.v, self.dt), 5, self.g), self.dt)

    def alpha14(self) -> pd.Series:
        return -1.0 * cs_rank(ts_delta(self.returns, 3, self.g), self.dt) * ts_corr(self.o, self.v, 10, self.g)

    def alpha15(self) -> pd.Series:
        return -1.0 * ts_sum(
            cs_rank(ts_corr(cs_rank(self.h, self.dt), cs_rank(self.v, self.dt), 3, self.g), self.dt), 3, self.g)

    def alpha16(self) -> pd.Series:
        return -1.0 * cs_rank(ts_cov(cs_rank(self.h, self.dt), cs_rank(self.v, self.dt), 5, self.g), self.dt)

    def alpha17(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (-1.0 * cs_rank(ts_rank(self.c, 10, self.g), self.dt)
                * cs_rank(ts_delta(ts_delta(self.c, 1, self.g), 1, self.g), self.dt)
                * cs_rank(ts_rank(self.v / adv20, 5, self.g), self.dt))

    def alpha18(self) -> pd.Series:
        return -1.0 * cs_rank(
            ts_std(ts_abs(self.c - self.o), 5, self.g) + (self.c - self.o) + ts_corr(self.c, self.o, 10, self.g),
            self.dt)

    def alpha19(self) -> pd.Series:
        return (-1.0 * ts_sign(self.c - ts_delay(self.c, 7, self.g) + ts_delta(self.c, 7, self.g))
                * (1.0 + cs_rank(1.0 + ts_sum(self.returns, 250, self.g), self.dt)))

    def alpha20(self) -> pd.Series:
        return cs_rank(self.o - ts_delay(self.h, 1, self.g), self.dt) * cs_rank(
            self.o - ts_delay(self.c, 1, self.g), self.dt) * cs_rank(
            self.o - ts_delay(self.lo, 1, self.g), self.dt)

    # ═══════════════════════════════════════════════════════════
    # Alpha 21 - 40
    # ═══════════════════════════════════════════════════════════

    def alpha21(self) -> pd.Series:
        cond1 = ts_mean(self.c, 8, self.g) + ts_std(self.c, 8, self.g) < ts_mean(self.c, 2, self.g)
        cond2 = ts_mean(self.v, 20, self.g) / self.v < 1
        val = pd.Series(np.where(cond1, -1, np.where(cond2, 1, -1)), index=self.c.index)
        return val.astype(float)

    def alpha22(self) -> pd.Series:
        return -1.0 * ts_delta(ts_corr(self.h, self.v, 5, self.g), 5, self.g) * cs_rank(ts_std(self.c, 20, self.g), self.dt)

    def alpha23(self) -> pd.Series:
        cond = ts_mean(self.h, 20, self.g) < self.h
        return pd.Series(np.where(cond, -1.0 * ts_delta(self.h, 2, self.g), 0.0), index=self.c.index)

    def alpha24(self) -> pd.Series:
        cond = ts_delta(ts_mean(self.c, 100, self.g), 100, self.g) / ts_delay(self.c, 100, self.g) <= 0.05
        return pd.Series(np.where(cond, -1.0 * (self.c - ts_min(self.c, 100, self.g)), -1.0 * ts_delta(self.c, 3, self.g)), index=self.c.index)

    def alpha25(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return cs_rank(-1.0 * self.returns * adv20 * self.vwap * (self.h - self.c), self.dt)

    def alpha26(self) -> pd.Series:
        return -1.0 * ts_max(ts_corr(ts_rank(self.v, 5, self.g), ts_rank(self.h, 5, self.g), 5, self.g), 3, self.g)

    def alpha27(self) -> pd.Series:
        cond = cs_rank(ts_mean(ts_corr(cs_rank(self.v, self.dt), cs_rank(self.vwap, self.dt), 6, self.g), 2, self.g), self.dt)
        return pd.Series(np.where(cond > 0.5, -1.0, 1.0), index=self.c.index)

    def alpha28(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return cs_scale(ts_corr(adv20, self.lo, 5, self.g) + (self.h + self.lo) / 2.0 - self.c, self.dt)

    def alpha29(self) -> pd.Series:
        return (ts_min(ts_rank(
            cs_rank(cs_rank(ts_log(ts_sum(ts_min(cs_rank(-1.0 * cs_rank(ts_delta(self.c, 5, self.g), self.dt), self.dt), 2, self.g), 1, self.g)), self.dt), self.dt), 5, self.g), 5, self.g)
                + ts_rank(ts_delay(-1.0 * self.returns, 6, self.g), 5, self.g))

    def alpha30(self) -> pd.Series:
        dc = ts_delta(self.c, 1, self.g)
        sign_dc = ts_sign(dc)
        return ((1.0 - cs_rank(sign_dc + ts_delay(sign_dc, 1, self.g) + ts_delay(sign_dc, 2, self.g), self.dt))
                * ts_sum(self.v, 5, self.g)) / ts_sum(self.v, 20, self.g)

    def alpha31(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (cs_rank(cs_rank(cs_rank(ts_decay_linear(-1.0 * cs_rank(ts_delta(self.c, 10, self.g), self.dt), 10, self.g), self.dt), self.dt)
                + cs_rank(-1.0 * ts_delta(self.c, 3, self.g), self.dt)
                + ts_sign(cs_scale(ts_corr(adv20, self.lo, 12, self.g), self.dt))))

    def alpha32(self) -> pd.Series:
        return cs_scale(ts_mean(self.c, 7, self.g) - self.c, self.dt) + 20.0 * cs_scale(
            ts_corr(self.vwap, ts_delay(self.c, 5, self.g), 230, self.g), self.dt)

    def alpha33(self) -> pd.Series:
        return cs_rank(-1.0 * (1.0 - self.o / self.c), self.dt)

    def alpha34(self) -> pd.Series:
        return cs_rank(
            (1.0 - cs_rank(ts_std(self.returns, 2, self.g) / ts_std(self.returns, 5, self.g), self.dt))
            + (1.0 - cs_rank(ts_delta(self.c, 1, self.g), self.dt)), self.dt)

    def alpha35(self) -> pd.Series:
        return ts_rank(self.v, 32, self.g) * (1.0 - ts_rank(self.c + self.h - self.lo, 16, self.g)) * (
            1.0 - ts_rank(self.returns, 32, self.g))

    def alpha36(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (2.21 * cs_rank(ts_corr(self.c - self.o, ts_delay(self.v, 1, self.g), 15, self.g), self.dt)
                + 0.7 * cs_rank(self.o - self.c, self.dt)
                + 0.73 * cs_rank(ts_rank(ts_delay(-1.0 * self.returns, 6, self.g), 5, self.g), self.dt)
                + cs_rank(ts_abs(ts_corr(self.vwap, adv20, 6, self.g)), self.dt)
                + 0.6 * cs_rank(ts_mean(self.c, 200, self.g) - self.o, self.dt) * cs_rank(self.c - self.o, self.dt))

    def alpha37(self) -> pd.Series:
        return cs_rank(ts_corr(ts_delay(self.o - self.c, 1, self.g), self.c, 200, self.g), self.dt) + cs_rank(
            self.o - self.c, self.dt)

    def alpha38(self) -> pd.Series:
        return -1.0 * cs_rank(ts_rank(self.c, 10, self.g), self.dt) * cs_rank(self.c / self.o, self.dt)

    def alpha39(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (-1.0 * cs_rank(ts_delta(self.c, 7, self.g) * (1.0 - cs_rank(
            ts_decay_linear(self.v / adv20, 9, self.g), self.dt)), self.dt)
                * (1.0 + cs_rank(ts_sum(self.returns, 250, self.g), self.dt)))

    def alpha40(self) -> pd.Series:
        return -1.0 * cs_rank(ts_std(self.h, 10, self.g), self.dt) * ts_corr(self.h, self.v, 10, self.g)

    # ═══════════════════════════════════════════════════════════
    # Alpha 41 - 60
    # ═══════════════════════════════════════════════════════════

    def alpha41(self) -> pd.Series:
        return pow1(self.h * self.lo, 0.5) - self.vwap

    def alpha42(self) -> pd.Series:
        return cs_rank(self.vwap - self.c, self.dt) / cs_rank(self.vwap + self.c, self.dt)

    def alpha43(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return ts_rank(self.v / adv20, 20, self.g) * ts_rank(-1.0 * ts_delta(self.c, 7, self.g), 8, self.g)

    def alpha44(self) -> pd.Series:
        return -1.0 * ts_corr(self.h, cs_rank(self.v, self.dt), 5, self.g)

    def alpha45(self) -> pd.Series:
        return -1.0 * cs_rank(ts_mean(ts_delay(self.c, 5, self.g), 20, self.g), self.dt) * ts_corr(
            self.c, self.v, 2, self.g) * cs_rank(ts_corr(ts_sum(self.c, 5, self.g), ts_sum(self.c, 20, self.g), 2, self.g), self.dt)

    def alpha46(self) -> pd.Series:
        cond = 0.25 < ts_delay((self.c - ts_delay(self.c, 20, self.g)) / ts_delay(self.c, 20, self.g), 10, self.g)
        return pd.Series(
            np.where(cond, -1.0 * ts_delta(self.c, 1, self.g),
                     np.where(ts_delay((self.c - ts_delay(self.c, 20, self.g)) / ts_delay(self.c, 20, self.g), 10, self.g) < 0,
                              1.0, -1.0 * ts_delta(self.c, 1, self.g))),
            index=self.c.index)

    def alpha47(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return cs_rank(1.0 / self.c, self.dt) * self.v / adv20 * (
            self.h * cs_rank(self.h - self.c, self.dt) / ts_mean(self.h, 5, self.g)) - cs_rank(
            self.vwap - ts_delay(self.vwap, 5, self.g), self.dt)

    def alpha49(self) -> pd.Series:
        cond = ts_delay((self.c - ts_delay(self.c, 10, self.g)) / ts_delay(self.c, 10, self.g), 1, self.g) < -0.1
        return pd.Series(np.where(cond, 1.0, -1.0 * ts_delta(self.c, 1, self.g)), index=self.c.index)

    def alpha50(self) -> pd.Series:
        return -1.0 * ts_max(
            cs_rank(ts_corr(cs_rank(self.v, self.dt), cs_rank(self.vwap, self.dt), 5, self.g), self.dt), 5, self.g)

    def alpha51(self) -> pd.Series:
        cond = ts_delay((self.c - ts_delay(self.c, 10, self.g)) / ts_delay(self.c, 10, self.g), 1, self.g) < -0.05
        return pd.Series(np.where(cond, 1.0, -1.0 * ts_delta(self.c, 1, self.g)), index=self.c.index)

    def alpha52(self) -> pd.Series:
        return (ts_delta(ts_min(self.lo, 5, self.g), 5, self.g)
                * cs_rank((ts_sum(self.returns, 240, self.g) - ts_sum(self.returns, 20, self.g)) / 220.0, self.dt)
                * ts_rank(self.v, 5, self.g))

    def alpha53(self) -> pd.Series:
        return -1.0 * ts_delta(safe_div(self.c - self.lo, self.h - self.lo + 1e-10), 9, self.g)

    def alpha54(self) -> pd.Series:
        return safe_div(-1.0 * (self.lo - self.c) * pow1(self.o, 5.0), (self.lo - self.h) * pow1(self.c, 5.0))

    def alpha55(self) -> pd.Series:
        inner = safe_div(self.c - ts_min(self.lo, 12, self.g), ts_max(self.h, 12, self.g) - ts_min(self.lo, 12, self.g))
        return -1.0 * ts_corr(cs_rank(inner, self.dt), cs_rank(self.v, self.dt), 6, self.g)

    def alpha57(self) -> pd.Series:
        return -1.0 * (self.c - self.vwap) / ts_decay_linear(cs_rank(ts_argmax(self.c, 30, self.g), self.dt), 2, self.g)

    def alpha60(self) -> pd.Series:
        inner = safe_div(self.c - self.lo - (self.h - self.c), self.h - self.lo + 1e-10)
        return -1.0 * (2.0 * cs_scale(cs_rank(inner, self.dt), self.dt) - cs_scale(
            cs_rank(ts_argmax(self.c, 10, self.g), self.dt), self.dt))

    # ═══════════════════════════════════════════════════════════
    # Alpha 61 - 101 (selected non-IndNeutralize alphas)
    # ═══════════════════════════════════════════════════════════

    def alpha61(self) -> pd.Series:
        return cs_rank(self.vwap - ts_min(self.vwap, 16, self.g), self.dt) < cs_rank(
            ts_corr(self.vwap, ts_mean(self.v, 180, self.g), 18, self.g), self.dt).astype(float)

    def alpha62(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (-1.0 * ts_corr(self.h, cs_rank(adv20, self.dt), 5, self.g)
                + cs_rank(self.o, self.dt) * cs_rank(self.o, self.dt))

    def alpha64(self) -> pd.Series:
        adv120 = ts_mean(self.v, 120, self.g)
        return cs_rank(
            ts_corr(ts_sum((self.o * 0.178404 + self.lo * (1.0 - 0.178404)), 13, self.g),
                     ts_sum(adv120, 13, self.g), 17, self.g), self.dt) < cs_rank(
            ts_delta(safe_div(self.vwap - ts_mean(self.vwap, 3, self.g), self.o - self.c + 1e-10), 3, self.g),
            self.dt).astype(float)

    def alpha65(self) -> pd.Series:
        adv60 = ts_mean(self.v, 60, self.g)
        return cs_rank(ts_corr(
            (self.o * 0.00817205 + self.vwap * (1.0 - 0.00817205)),
            ts_sum(ts_mean(self.v, 60, self.g), 9, self.g), 6, self.g), self.dt) < cs_rank(
            self.o - ts_min(self.o, 14, self.g), self.dt).astype(float)

    def alpha66(self) -> pd.Series:
        return -1.0 * cs_rank(
            ts_decay_linear(ts_delta(self.vwap, 4, self.g), 7, self.g), self.dt) + ts_decay_linear(
            cs_rank(ts_decay_linear(safe_div(self.lo - self.vwap, self.o - (self.h + self.lo) / 2.0 + 1e-10), 11, self.g), self.dt), 7, self.g)

    def alpha68(self) -> pd.Series:
        adv15 = ts_mean(self.v, 15, self.g)
        return -1.0 * ts_rank(
            ts_corr(cs_rank(self.h, self.dt), cs_rank(adv15, self.dt), 9, self.g), 14, self.g) < cs_rank(
            ts_delta(self.c * 0.518371 + self.lo * (1.0 - 0.518371), 1, self.g), self.dt).astype(float)

    def alpha71(self) -> pd.Series:
        adv180 = ts_mean(self.v, 180, self.g)
        p1 = ts_rank(ts_decay_linear(ts_corr(ts_rank(self.c, self.dt), cs_rank(adv180, self.dt), 18, self.g), 4, self.g), 16, self.g)
        p2 = ts_rank(ts_decay_linear(
            cs_rank(self.lo + self.o - 2.0 * self.vwap, self.dt), 8, self.g), 7, self.g)
        return ts_greater(p1, p2)

    def alpha72(self) -> pd.Series:
        adv40 = ts_mean(self.v, 40, self.g)
        return cs_rank(ts_decay_linear(
            ts_corr(self.h + self.lo / 2.0, adv40, 9, self.g), 10, self.g), self.dt) / cs_rank(
            ts_decay_linear(ts_corr(ts_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 7, self.g), 3, self.g), self.dt)

    def alpha73(self) -> pd.Series:
        return (ts_greater(
            cs_rank(ts_decay_linear(ts_delta(self.vwap, 5, self.g), 3, self.g), self.dt),
            ts_rank(ts_decay_linear(
                safe_div(-1.0 * ts_delta(self.returns * 0.147155 + self.o * (1.0 - 0.147155), 2, self.g),
                         self.o - self.c + 1e-10), 14, self.g), 3, self.g)) * -1)

    def alpha74(self) -> pd.Series:
        adv30 = ts_mean(self.v, 30, self.g)
        return (cs_rank(ts_corr(self.c, ts_sum(ts_mean(self.v, 30, self.g), 37, self.g), 15, self.g), self.dt)
                < cs_rank(ts_corr(cs_rank(self.h * 0.0261661 + self.vwap * (1.0 - 0.0261661), self.dt),
                                  cs_rank(self.v, self.dt), 11, self.g), self.dt)).astype(float) * -1

    def alpha75(self) -> pd.Series:
        adv50 = ts_mean(self.v, 50, self.g)
        return (cs_rank(ts_corr(self.vwap, self.v, 4, self.g), self.dt)
                < cs_rank(ts_corr(cs_rank(self.lo, self.dt), cs_rank(ts_mean(self.v, 50, self.g), self.dt), 12, self.g), self.dt)).astype(float)

    def alpha77(self) -> pd.Series:
        adv40 = ts_mean(self.v, 40, self.g)
        return ts_less(
            cs_rank(ts_decay_linear(ts_delta(self.vwap, 1, self.g), 11, self.g), self.dt) + ts_decay_linear(
                ts_corr(self.h, adv40, 7, self.g), 9, self.g),
            ts_rank(ts_delta(self.h + self.lo - 2 * self.c, 1, self.g), 3, self.g))

    def alpha78(self) -> pd.Series:
        adv40 = ts_mean(self.v, 40, self.g)
        return (cs_rank(ts_corr(ts_sum(self.lo * 0.352233 + self.vwap * (1.0 - 0.352233), 20, self.g),
                                 ts_sum(adv40, 20, self.g), 7, self.g), self.dt)
                ** cs_rank(ts_corr(cs_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 6, self.g), self.dt))

    def alpha81(self) -> pd.Series:
        adv10 = ts_mean(self.v, 10, self.g)
        return (cs_rank(ts_log(ts_product(cs_rank(cs_rank(ts_corr(self.vwap, ts_sum(adv10, 50, self.g), 8, self.g), self.dt), self.dt), 15, self.g)), self.dt)
                < cs_rank(ts_corr(self.c, ts_sum(adv10, 50, self.g), 5, self.g), self.dt)).astype(float) * -1

    def alpha83(self) -> pd.Series:
        return safe_div(
            cs_rank(ts_delay(safe_div(self.h - self.lo, ts_mean(self.c, 5, self.g)), 2, self.g), self.dt) * cs_rank(
                cs_rank(self.v, self.dt), self.dt),
            safe_div(self.h - self.lo, ts_mean(self.c, 5, self.g)) / (self.vwap - self.c + 1e-10))

    def alpha84(self) -> pd.Series:
        return pow1(ts_rank(self.vwap - ts_max(self.vwap, 15, self.g), 21, self.g),
                    ts_delta(self.c, 5, self.g))

    def alpha85(self) -> pd.Series:
        adv30 = ts_mean(self.v, 30, self.g)
        return pow1(cs_rank(ts_corr(self.h * 0.876703 + self.c * (1.0 - 0.876703), adv30, 10, self.g), self.dt),
                    cs_rank(ts_corr(ts_rank(self.h + self.lo / 2.0, self.dt), cs_rank(self.v, self.dt), 7, self.g), self.dt))

    def alpha86(self) -> pd.Series:
        adv20 = ts_mean(self.v, 20, self.g)
        return (ts_rank(ts_corr(self.c, ts_sum(adv20, 15, self.g), 6, self.g), 20, self.g)
                < cs_rank(self.o + self.c - 2.0 * self.vwap, self.dt)).astype(float) * -1

    def alpha88(self) -> pd.Series:
        adv60 = ts_mean(self.v, 60, self.g)
        return cs_rank(ts_decay_linear(ts_corr(self.o, ts_sum(adv60, 16, self.g), 17, self.g), 5, self.g), self.dt) - cs_rank(
            ts_decay_linear(ts_delta(self.vwap, 5, self.g), 7, self.g), self.dt)

    def alpha92(self) -> pd.Series:
        adv30 = ts_mean(self.v, 30, self.g)
        p1 = ts_rank(ts_decay_linear(safe_div(self.h + self.lo / 2.0 + self.c, 3.0) - self.c, 15, self.g), 18, self.g)
        p2 = ts_rank(ts_decay_linear(ts_corr(cs_rank(self.lo, self.dt), cs_rank(adv30, self.dt), 8, self.g), 7, self.g), 7, self.g)
        return ts_less(p1, p2)

    def alpha94(self) -> pd.Series:
        adv60 = ts_mean(self.v, 60, self.g)
        return (-1.0 * cs_rank(self.vwap - ts_min(self.vwap, 12, self.g), self.dt) ** ts_rank(
            ts_corr(ts_rank(self.vwap, self.dt), cs_rank(adv60, self.dt), 4, self.g), 13, self.g))

    def alpha95(self) -> pd.Series:
        adv40 = ts_mean(self.v, 40, self.g)
        return (cs_rank(self.o - ts_min(self.o, 12, self.g), self.dt)
                < ts_rank(cs_rank(ts_corr(
            ts_sum(self.h + self.lo / 2.0, 19, self.g), ts_sum(adv40, 19, self.g), 13, self.g), self.dt), 12, self.g)).astype(float)

    def alpha96(self) -> pd.Series:
        adv60 = ts_mean(self.v, 60, self.g)
        return ts_greater(
            ts_rank(ts_decay_linear(ts_corr(cs_rank(self.vwap, self.dt), cs_rank(self.v, self.dt), 4, self.g), 4, self.g), 8, self.g),
            ts_rank(ts_decay_linear(ts_argmax(ts_corr(ts_rank(self.c, self.dt), cs_rank(adv60, self.dt), 4, self.g), 13, self.g), 14, self.g), 13, self.g)) * -1

    def alpha98(self) -> pd.Series:
        adv5 = ts_mean(self.v, 5, self.g)
        adv15 = ts_mean(self.v, 15, self.g)
        return cs_rank(ts_decay_linear(ts_corr(self.vwap, ts_sum(adv5, 26, self.g), 5, self.g), 7, self.g), self.dt) - cs_rank(
            ts_decay_linear(ts_rank(ts_argmin(ts_corr(cs_rank(self.o, self.dt), cs_rank(adv15, self.dt), 21, self.g), 9, self.g), 7, self.g), 8, self.g), self.dt)

    def alpha99(self) -> pd.Series:
        adv60 = ts_mean(self.v, 60, self.g)
        return (-1.0 * cs_rank(ts_corr(ts_sum(self.h + self.lo / 2.0, 20, self.g), ts_sum(adv60, 20, self.g), 9, self.g), self.dt)
                < cs_rank(ts_corr(self.lo, self.v, 6, self.g), self.dt)).astype(float) * -1

    def alpha101(self) -> pd.Series:
        return safe_div(self.c - self.o, (self.h - self.lo) + 0.001)
