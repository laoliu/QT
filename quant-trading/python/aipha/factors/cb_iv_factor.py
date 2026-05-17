"""CB 隐含波动率(IV)因子计算引擎。

从 CB 市场价格反解隐含波动率，构建 IVP/IVPR 时序因子：
- BSM 反解 σ_implied（给定 CB 价格、正股价格、转股价、剩余期限、无风险率）
- IVP (IV Percentile): 当前 IV 在过去 N 日 IV 分布中的分位数
- IVPR (IV Rank): (当前 IV - N 日最低 IV) / (N 日最高 IV - N 日最低 IV)
- IV 溢价率: (σ_impl - σ_hist) / σ_hist

理论基础:
- §4 cb_iv_option_pricing_research.md: BSM 反解流程
- §11.2 CB 定价：二叉树/BSM/MC
- McLean & Pontiff (2016): alpha 衰减
- 中信证券 2024-11: IV 极端低位信号

参数参考:
- 无风险率 r: 1.5%-2.5% (国债收益率)
- 信用利差 s: AAA 30-60bps, AA+ 60-100bps
- 面值 F: 100 元
- 搜索区间: σ ∈ [0.01, 2.0]
- Brent 容差: 1e-6

Usage::

    from aipha.factors.cb_iv_factor import CBIVCalculator

    calc = CBIVCalculator(risk_free_rate=0.02, credit_spread=0.005)
    iv = calc.compute_iv(
        market_price=115.0, stock_price=12.5,
        conversion_price=10.0, years_to_maturity=3.5
    )
    print(f"Implied Vol: {iv:.2%}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 常量
FACE_VALUE = 100.0
DEFAULT_RISK_FREE_RATE = 0.02  # §13.1 统一默认无风险率
DEFAULT_CREDIT_SPREAD = 0.005  # AAA 50bps, discount = rf + spread
DEFAULT_VOL_SEARCH_MIN = 0.01
DEFAULT_VOL_SEARCH_MAX = 2.0
BRENT_TOL = 1e-6
MAX_ITER = 100


def _norm_cdf(x: float) -> float:
    """标准正态 CDF (使用 math.erfc 精确实现)。"""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _bsm_call_price(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0
) -> float:
    """BSM 欧式看涨期权价格。"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K * math.exp(-r * T), 0.0)

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


@dataclass
class BondFloorParams:
    """纯债价值参数。"""
    face_value: float = FACE_VALUE
    coupon_rates: tuple[float, ...] = (0.003, 0.005, 0.010, 0.015, 0.018, 0.020)
    compensation: float = 6.0  # 到期补偿（元）


class CBIVCalculator:
    """CB 隐含波动率计算器。"""

    def __init__(
        self,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        credit_spread: float = DEFAULT_CREDIT_SPREAD,
        bond_params: Optional[BondFloorParams] = None,
    ) -> None:
        self._r = risk_free_rate
        self._s = credit_spread
        self._bond_params = bond_params or BondFloorParams()

    def compute_bond_floor(self, years_to_maturity: float) -> float:
        """计算纯债价值 (Bond Floor)。

        BF = Σ(coupon_t / (1+r+s)^t) + (face + compensation) / (1+r+s)^T
        """
        if years_to_maturity <= 0:
            return self._bond_params.face_value + self._bond_params.compensation

        discount_rate = self._r + self._s
        bp = self._bond_params
        years_int = max(1, int(math.ceil(years_to_maturity)))

        bf = 0.0
        for t in range(1, years_int + 1):
            # 渐进式票息
            coupon_idx = min(t - 1, len(bp.coupon_rates) - 1)
            coupon = bp.face_value * bp.coupon_rates[coupon_idx]
            frac = min(float(t), years_to_maturity)
            bf += coupon / (1 + discount_rate) ** frac

        # 面值 + 到期补偿
        bf += (bp.face_value + bp.compensation) / (1 + discount_rate) ** years_to_maturity
        return bf

    def compute_iv(
        self,
        market_price: float,
        stock_price: float,
        conversion_price: float,
        years_to_maturity: float,
    ) -> Optional[float]:
        """从 CB 市场价格反解隐含波动率。

        Steps:
        1. 计算 Bond Floor
        2. 剥离期权成分: V_option = market_price - bond_floor
        3. Brent 法求解 BSM(σ) = V_option

        Returns:
            隐含波动率 (年化)，若无法求解返回 None
        """
        if market_price <= 0 or stock_price <= 0 or conversion_price <= 0:
            return None
        if years_to_maturity <= 0.01:
            return None

        bond_floor = self.compute_bond_floor(years_to_maturity)
        v_option = market_price - bond_floor

        # 期权成分为负（市场价低于债底），IV 趋近于 0
        if v_option <= 0:
            return 0.0

        # 转股比例 R = 面值 / 转股价
        conversion_ratio = FACE_VALUE / conversion_price

        # 目标: 找 σ 使得 R * BSM_call(S, K=conversion_price, T, r, σ) = v_option
        def objective(sigma: float) -> float:
            call_val = _bsm_call_price(
                S=stock_price,
                K=conversion_price,
                T=years_to_maturity,
                r=self._r,
                sigma=sigma,
            )
            return conversion_ratio * call_val - v_option

        # Brent 法求根
        a, b = DEFAULT_VOL_SEARCH_MIN, DEFAULT_VOL_SEARCH_MAX
        fa, fb = objective(a), objective(b)

        # 若 fa > 0，即使最低 vol 也高于目标，IV 极低
        if fa >= 0:
            return DEFAULT_VOL_SEARCH_MIN
        # 若 fb < 0，即使最高 vol 也低于目标，IV 极高或无解
        if fb <= 0:
            return None

        # Brent 迭代
        c, fc = b, fb
        d = e = b - a
        for _ in range(MAX_ITER):
            if (fb > 0 and fc > 0) or (fb < 0 and fc < 0):
                c, fc = a, fa
                d = e = b - a

            if abs(fc) < abs(fb):
                a, b, c = b, c, b
                fa, fb, fc = fb, fc, fb

            tol = 2.0 * BRENT_TOL * abs(b) + 0.5 * BRENT_TOL
            m = 0.5 * (c - b)

            if abs(m) <= tol or fb == 0.0:
                return b

            if abs(e) >= tol and abs(fa) > abs(fb):
                s_val = fb / fa
                if a == c:
                    p_val = 2.0 * m * s_val
                    q_val = 1.0 - s_val
                else:
                    q_val = fa / fc
                    r_val = fb / fc
                    p_val = s_val * (2.0 * m * q_val * (q_val - r_val) - (b - a) * (r_val - 1.0))
                    q_val = (q_val - 1.0) * (r_val - 1.0) * (s_val - 1.0)

                if p_val > 0:
                    q_val = -q_val
                else:
                    p_val = -p_val

                if 2.0 * p_val < min(3.0 * m * q_val - abs(tol * q_val), abs(e * q_val)):
                    e = d
                    d = p_val / q_val
                else:
                    d = m
                    e = m
            else:
                d = m
                e = m

            a, fa = b, fb
            if abs(d) > tol:
                b += d
            else:
                b += tol if m > 0 else -tol
            fb = objective(b)

        # 未收敛
        logger.warning("IV Brent 未收敛: price=%.2f, S=%.2f, K=%.2f", market_price, stock_price, conversion_price)
        return None

    def compute_ivp(
        self,
        current_iv: float,
        iv_history: np.ndarray,
    ) -> float:
        """计算 IV Percentile (IVP)。

        IVP = |{iv in history where iv <= current}| / len(history)

        Returns:
            IVP 值，范围 [0, 1]
        """
        if len(iv_history) == 0:
            return 0.5  # 无历史数据时返回中性值
        valid = iv_history[~np.isnan(iv_history)]
        if len(valid) == 0:
            return 0.5
        return float(np.sum(valid <= current_iv) / len(valid))

    def compute_ivpr(
        self,
        current_iv: float,
        iv_history: np.ndarray,
    ) -> float:
        """计算 IV Rank (IVPR)。

        IVPR = (current_iv - min(history)) / (max(history) - min(history))

        Returns:
            IVPR 值，范围 [0, 1]
        """
        if len(iv_history) == 0:
            return 0.5
        valid = iv_history[~np.isnan(iv_history)]
        if len(valid) == 0:
            return 0.5
        iv_min = float(np.min(valid))
        iv_max = float(np.max(valid))
        if iv_max - iv_min < 1e-8:
            return 0.5
        return float((current_iv - iv_min) / (iv_max - iv_min))

    def compute_iv_premium(
        self,
        implied_vol: float,
        historical_vol: float,
    ) -> float:
        """计算 IV 溢价率。

        IV_prem = (σ_impl - σ_hist) / σ_hist
        正值 = 市场看多情绪（IV > HV），负值 = 低估

        Returns:
            IV 溢价率
        """
        if historical_vol <= 0.001:
            return 0.0
        return (implied_vol - historical_vol) / historical_vol


# ---------------------------------------------------------------------------
# 批量计算接口（面向策略集成）
# ---------------------------------------------------------------------------


@dataclass
class CBIVFactors:
    """单个 CB 的 IV 因子集合。"""
    ts_code: str
    implied_vol: Optional[float]  # σ_impl
    ivp: float  # IV Percentile [0,1]
    ivpr: float  # IV Rank [0,1]
    iv_premium: float  # (IV - HV) / HV
    bond_floor: float  # 纯债价值
    option_value: float  # 隐含期权价值 = price - bond_floor


def compute_iv_factors_batch(
    calc: CBIVCalculator,
    cb_data: "pd.DataFrame",
    stock_prices: dict[str, float],
    iv_histories: Optional[dict[str, np.ndarray]] = None,
    hv_map: Optional[dict[str, float]] = None,
) -> list[CBIVFactors]:
    """批量计算 CB IV 因子。

    Args:
        calc: IV 计算器实例
        cb_data: CB 行情 DataFrame，需含 ts_code/close/cb_value/
                 conversion_price/years_to_maturity
        stock_prices: {正股代码: 当前价格}，或 {CB代码: 对应正股价格}
        iv_histories: {ts_code: 历史 IV 数组}，用于 IVP/IVPR
        hv_map: {ts_code: 正股历史波动率}，用于 IV premium

    Returns:
        每个 CB 的 IV 因子列表
    """
    import pandas as pd  # noqa: F811 - lazy import

    results: list[CBIVFactors] = []
    iv_hist = iv_histories or {}
    hv = hv_map or {}

    for _, row in cb_data.iterrows():
        ts_code = str(row.get("ts_code", ""))
        close = float(row.get("close", 0))
        conv_price = float(row.get("conversion_price", 0))
        years = float(row.get("years_to_maturity", 0))

        # 正股价格：优先 stock_prices[ts_code]，否则从 cb_value 反算
        s_price = stock_prices.get(ts_code, 0.0)
        if s_price <= 0:
            cb_val = float(row.get("cb_value", 0))
            if cb_val > 0 and conv_price > 0:
                s_price = cb_val * conv_price / FACE_VALUE
            else:
                results.append(CBIVFactors(
                    ts_code=ts_code, implied_vol=None, ivp=0.5,
                    ivpr=0.5, iv_premium=0.0, bond_floor=0.0, option_value=0.0,
                ))
                continue

        # 计算 IV
        iv = calc.compute_iv(close, s_price, conv_price, years)

        # Bond floor & option value
        bf = calc.compute_bond_floor(years)
        opt_val = max(close - bf, 0.0)

        # IVP / IVPR
        hist = iv_hist.get(ts_code, np.array([]))
        ivp_val = calc.compute_ivp(iv if iv is not None else 0.0, hist)
        ivpr_val = calc.compute_ivpr(iv if iv is not None else 0.0, hist)

        # IV premium
        hist_vol = hv.get(ts_code, 0.3)  # 默认 30% HV
        iv_prem = calc.compute_iv_premium(iv if iv is not None else 0.0, hist_vol)

        results.append(CBIVFactors(
            ts_code=ts_code,
            implied_vol=iv,
            ivp=ivp_val,
            ivpr=ivpr_val,
            iv_premium=iv_prem,
            bond_floor=bf,
            option_value=opt_val,
        ))

    return results
