"""可转债专属因子模块 — 8 个正交因子 (5 截面 + 3 时序)

基于专家评估 (R18) 的 Alpha-Plus 路线图 Phase 1 设计。

**原始 5 因子 (截面, 无需历史)**:

1. **iv_bias** (隐含波动率偏差)
   - 公式: time_value / (close * sqrt(years_to_mat))
   - 经济逻辑: CB 内嵌期权的时间价值效率。低 IV 表示期权被低估，
     买入低 IV 的 CB 相当于廉价做多波动率 (Brennan & Schwartz 1980)
   - 方向: 升序 (越低越优 — 期权越便宜)

2. **gamma_convexity** (Gamma 凸性价值)
   - 公式: gamma_proxy * sqrt(years_to_mat)
   - 经济逻辑: ATM 附近的 CB 具有最高 Gamma，残余期限越长凸性价值越大;
     做多高 Gamma CB = 做多正股波动率 (Tsiveriotis-Fernandes 1998)
   - 方向: 降序 (越高越优 — 凸性越大)

3. **credit_spread** (信用利差)
   - 公式: approx_ytm - risk_free_rate
   - 经济逻辑: 高利差意味更高的信用风险补偿, 在系统性利差收窄时获利;
     信用利差因子在 A 股 CB 市场有效 (Zhang & Li 2021)
   - 方向: 降序 (越高越优 — 风险补偿越大)

4. **liquidity_activity** (流动性活跃度)
   - 公式: amount / issue_size
   - 经济逻辑: 高活跃度反映信息交易和市场关注度, 流动性好的标的
     换仓摩擦小, 异常活跃可能预示事件驱动机会 (Amihud 2002)
   - 方向: 降序 (越高越优 — 流动性越好)

5. **maturity_convexity** (到期凸性)
   - 公式: max(0, 1 - |years - sweet_spot| / half_range)
   - 经济逻辑: 2-4 年的 CB 处于债股平衡最优区间 — 既有足够的
     正股上涨期权价值, 又有合理的债底保护和促转股预期
   - 方向: 降序 (越高越优 — 越接近最优区间)

**Phase 1 新增 3 因子 (时序, 需历史缓冲区)**:

6. **delta_accel** (Delta 加速度)
   - 公式: delta_t - delta_{t-1}, 其中 delta = cb_value / close
   - 经济逻辑: Delta 加速意味正股趋势加速, CB 从债性转向股性;
     Delta 加速的 CB 具有正股动量溢出效应
   - 方向: 降序 (越高越优 — 加速越强)

7. **credit_spread_mom** (信用利差动量)
   - 公式: spread_t - spread_{t-lookback}
   - 经济逻辑: 利差收窄(负值) = 信用改善, 资本利得机会;
     信用动量因子在债券市场有效 (Jostova et al. 2013)
   - 方向: 升序 (越负越优 — 利差收窄越多)

8. **liquidity_shock** (流动性突变)
   - 公式: amount_t / MA_lookback(amount)
   - 经济逻辑: 异常放量 = 信息驱动交易, 可能预示价格发现;
     成交量突变在 A 股 CB 市场与短期收益正相关
   - 方向: 降序 (越高越优 — 放量越明显)

数据依赖: 全部使用 cb_daily + cb_basic 已有字段, 无需分钟数据或正股数据。
时序因子需策略维护历史 deque 并传入。
对齐因子分析基础设施: factors/analysis.py FactorAnalyzer 可直接使用。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipha.strategy.cb_rotation_base import CandidateInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量与默认参数
# ---------------------------------------------------------------------------

DEFAULT_RISK_FREE_RATE: float = 0.02  # §13.1 全局统一 RISK_FREE_RATE_DEFAULT=0.02
DEFAULT_PAR_VALUE: float = 100.0
DEFAULT_MATURITY_SWEET_SPOT: float = 3.0  # 最优到期年限中心
DEFAULT_MATURITY_HALF_RANGE: float = 3.0  # 有效区间半径 (0~6 年)


@dataclass
class CBFactorValues:
    """单只 CB 的 5 因子计算结果。"""

    symbol: str
    iv_bias: float
    gamma_convexity: float
    credit_spread: float
    liquidity_activity: float
    maturity_convexity: float

    def as_dict(self) -> dict[str, float]:
        """返回因子名→值字典。"""
        return {
            "iv_bias": self.iv_bias,
            "gamma_convexity": self.gamma_convexity,
            "credit_spread": self.credit_spread,
            "liquidity_activity": self.liquidity_activity,
            "maturity_convexity": self.maturity_convexity,
        }


# ---------------------------------------------------------------------------
# Factor 1: iv_bias (隐含波动率偏差)
# ---------------------------------------------------------------------------

def compute_iv_bias(
    close: float,
    cb_value: float | None,
    maturity_date: str | None,
    now: datetime,
    par_value: float = DEFAULT_PAR_VALUE,
) -> float:
    """计算隐含波动率偏差 (时间价值效率)。

    公式:
        bond_floor = par_value (简化: 忽略贴现, 面值保底)
        intrinsic = max(bond_floor, cb_value)
        time_value = max(0, close - intrinsic)
        iv_bias = time_value / (close * sqrt(years_to_maturity))

    值越低表示期权越便宜 (被低估), 是买入信号。

    Args:
        close: CB 收盘价
        cb_value: 转股价值
        maturity_date: 到期日 YYYYMMDD
        now: 当前日期
        par_value: 面值, 默认 100

    Returns:
        iv_bias 值, 数据不足返回 0.5 (中性)
    """
    if close <= 0 or maturity_date is None:
        return 0.5

    years = _years_to_maturity(maturity_date, now)
    if years <= 0.1:
        return 0.5

    cv = cb_value if cb_value is not None and cb_value > 0 else 0.0
    intrinsic = max(par_value, cv)
    time_value = max(0.0, close - intrinsic)

    return time_value / (close * math.sqrt(years))


# ---------------------------------------------------------------------------
# Factor 2: gamma_convexity (Gamma 凸性价值)
# ---------------------------------------------------------------------------

def compute_gamma_convexity(
    cb_value: float | None,
    maturity_date: str | None,
    now: datetime,
    par_value: float = DEFAULT_PAR_VALUE,
) -> float:
    """计算 Gamma 凸性价值。

    公式:
        delta_proxy = cb_value / par_value  (clamp to [0, 1])
        gamma_proxy = 4 * delta * (1 - delta)  (ATM 最大)
        gamma_convexity = gamma_proxy * sqrt(years_to_maturity)

    值越高表示凸性越大 — 正股波动越大, CB 收益越好。

    Args:
        cb_value: 转股价值
        maturity_date: 到期日 YYYYMMDD
        now: 当前日期
        par_value: 面值

    Returns:
        gamma_convexity 值, 数据不足返回 0.0
    """
    if cb_value is None or cb_value <= 0 or maturity_date is None:
        return 0.0

    years = _years_to_maturity(maturity_date, now)
    if years <= 0.1:
        return 0.0

    delta = max(0.0, min(1.0, cb_value / par_value))
    gamma = 4.0 * delta * (1.0 - delta)

    return gamma * math.sqrt(years)


# ---------------------------------------------------------------------------
# Factor 3: credit_spread (信用利差)
# ---------------------------------------------------------------------------

def compute_credit_spread(
    close: float,
    maturity_date: str | None,
    now: datetime,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    par_value: float = DEFAULT_PAR_VALUE,
) -> float:
    """计算信用利差 = 近似 YTM - 无风险利率。

    近似 YTM: (par / close) ^ (1 / years) - 1
    credit_spread = ytm - risk_free_rate

    值越高表示信用风险补偿越大 — 在评级合理前提下是价值信号。

    Args:
        close: CB 收盘价
        maturity_date: 到期日 YYYYMMDD
        now: 当前日期
        risk_free_rate: 无风险利率
        par_value: 面值

    Returns:
        credit_spread 值, 数据不足返回 0.0
    """
    if close <= 0 or close >= 200 or maturity_date is None:
        return 0.0

    years = _years_to_maturity(maturity_date, now)
    if years <= 0.1:
        return 0.0

    try:
        ytm = (par_value / close) ** (1.0 / years) - 1.0
    except (OverflowError, ZeroDivisionError):
        return 0.0

    return ytm - risk_free_rate


# ---------------------------------------------------------------------------
# Factor 4: liquidity_activity (流动性活跃度)
# ---------------------------------------------------------------------------

def compute_liquidity_activity(
    amount: float | None,
    issue_size: float | None,
) -> float:
    """计算流动性活跃度 = 成交额 / 发行规模。

    衡量当日交易活跃度相对于标的规模的比例。
    高活跃度反映市场关注和信息流动。

    Args:
        amount: 当日成交额 (万元)
        issue_size: 发行规模 (万元)

    Returns:
        activity 比率, 数据不足返回 0.0
    """
    if amount is None or issue_size is None:
        return 0.0
    if amount <= 0 or issue_size <= 0:
        return 0.0

    return amount / issue_size


# ---------------------------------------------------------------------------
# Factor 5: maturity_convexity (到期凸性)
# ---------------------------------------------------------------------------

def compute_maturity_convexity(
    maturity_date: str | None,
    now: datetime,
    sweet_spot: float = DEFAULT_MATURITY_SWEET_SPOT,
    half_range: float = DEFAULT_MATURITY_HALF_RANGE,
) -> float:
    """计算到期凸性 — 衡量距最优到期区间的亲近度。

    2-4 年的 CB 处于债股平衡最优区间:
    - 足够的正股上涨期权价值
    - 合理的债底保护
    - 发行人促转股动力较强

    公式: score = max(0, 1 - |years - sweet_spot| / half_range)
    在 sweet_spot 处为 1.0, 线性衰减到 half_range 边界为 0.0。

    Args:
        maturity_date: 到期日 YYYYMMDD
        now: 当前日期
        sweet_spot: 最优年限中心 (默认 3 年)
        half_range: 有效区间半径 (默认 3 年, 即 0~6 年)

    Returns:
        [0, 1] 的凸性分数
    """
    if maturity_date is None:
        return 0.0

    years = _years_to_maturity(maturity_date, now)
    if years <= 0 or half_range <= 0:
        return 0.0

    distance = abs(years - sweet_spot)
    return max(0.0, 1.0 - distance / half_range)


# ---------------------------------------------------------------------------
# Factor 6: delta_accel (Delta 加速度) — 时序因子
# ---------------------------------------------------------------------------

def compute_delta_accel(
    delta_history: Sequence[float],
    min_length: int = 2,
) -> float:
    """计算 Delta 加速度 = delta_t - delta_{t-1}。

    delta_proxy = cb_value / close (策略层维护并传入历史)。
    正加速表示 CB 从债性转向股性 (正股上涨趋势加速)。

    Args:
        delta_history: Delta 代理历史序列, 最新值在末尾
        min_length: 最少需要的历史长度

    Returns:
        delta 一阶差分, 数据不足返回 0.0
    """
    if len(delta_history) < min_length:
        return 0.0
    return float(delta_history[-1] - delta_history[-2])


# ---------------------------------------------------------------------------
# Factor 7: credit_spread_mom (信用利差均值回归) — 时序因子
# ---------------------------------------------------------------------------

def compute_credit_spread_mom(
    spread_history: Sequence[float],
    lookback: int = 20,
) -> float:
    """计算信用利差均值回归信号 = spread_t - spread_{t-lookback}。

    正值表示利差近期扩大 (信用恶化) — IC 分析证实这类 CB 倾向
    均值回归, 后续收益更高 (即 "买跌" 信号)。
    策略层维护历史 credit_spread 序列并传入。

    Args:
        spread_history: 信用利差历史序列, 最新值在末尾
        lookback: 回看期 (默认 20 个交易日)

    Returns:
        利差变化量, 数据不足返回 0.0
    """
    if len(spread_history) < lookback + 1:
        return 0.0
    return float(spread_history[-1] - spread_history[-lookback - 1])


# ---------------------------------------------------------------------------
# Factor 8: liquidity_shock (流动性突变) — 时序因子
# ---------------------------------------------------------------------------

def compute_liquidity_shock(
    amount_history: Sequence[float],
    lookback: int = 20,
) -> float:
    """计算流动性突变 = amount_t / MA_lookback(amount)。

    值 > 1 表示放量, 远大于 1 表示异常放量 (信息驱动)。
    策略层维护历史 amount 序列并传入。

    Args:
        amount_history: 成交额历史序列, 最新值在末尾
        lookback: 移动平均回看期 (默认 20 交易日)

    Returns:
        成交量比率, 数据不足或 MA=0 时返回 1.0 (中性)
    """
    if len(amount_history) < lookback + 1:
        return 1.0

    current = amount_history[-1]
    # MA 取 [-lookback-1:-1] 的均值 (不含当日)
    window = amount_history[-lookback - 1:-1]
    ma = sum(window) / len(window)

    if ma <= 0:
        return 1.0
    return float(current / ma)


# ---------------------------------------------------------------------------
# 综合计算入口
# ---------------------------------------------------------------------------

def compute_all_factors(
    candidate: CandidateInfo,
    now: datetime,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    par_value: float = DEFAULT_PAR_VALUE,
    maturity_sweet_spot: float = DEFAULT_MATURITY_SWEET_SPOT,
    maturity_half_range: float = DEFAULT_MATURITY_HALF_RANGE,
) -> CBFactorValues:
    """为单只 CB 计算全部 5 个新因子。

    Args:
        candidate: CandidateInfo 实例
        now: 当前日期
        risk_free_rate: 无风险利率
        par_value: 面值
        maturity_sweet_spot: 到期凸性最优年限
        maturity_half_range: 到期凸性有效区间

    Returns:
        CBFactorValues 包含 5 个因子值
    """
    return CBFactorValues(
        symbol=candidate.symbol,
        iv_bias=compute_iv_bias(
            candidate.close, candidate.cb_value,
            candidate.maturity_date, now, par_value,
        ),
        gamma_convexity=compute_gamma_convexity(
            candidate.cb_value, candidate.maturity_date, now, par_value,
        ),
        credit_spread=compute_credit_spread(
            candidate.close, candidate.maturity_date, now,
            risk_free_rate, par_value,
        ),
        liquidity_activity=compute_liquidity_activity(
            candidate.amount, candidate.issue_size,
        ),
        maturity_convexity=compute_maturity_convexity(
            candidate.maturity_date, now,
            maturity_sweet_spot, maturity_half_range,
        ),
    )


def compute_batch_factors(
    candidates: list[CandidateInfo],
    now: datetime,
    **kwargs: Any,
) -> list[CBFactorValues]:
    """批量计算所有候选 CB 的 5 因子值。

    Args:
        candidates: 候选 CB 列表
        now: 当前日期
        **kwargs: 传递给 compute_all_factors 的额外参数

    Returns:
        与 candidates 等长的 CBFactorValues 列表
    """
    return [compute_all_factors(c, now, **kwargs) for c in candidates]


# ---------------------------------------------------------------------------
# 因子元信息 (用于 IC 分析和报告)
# ---------------------------------------------------------------------------

FACTOR_META: dict[str, dict[str, Any]] = {
    "iv_bias": {
        "name_cn": "隐含波动率偏差",
        "direction": "ascending",   # 越低越优
        "description": "CB 内嵌期权时间价值效率, 低值=期权被低估",
        "economic_logic": "买入低 IV CB = 廉价做多波动率",
    },
    "gamma_convexity": {
        "name_cn": "Gamma 凸性价值",
        "direction": "descending",  # 越高越优
        "description": "ATM 附近 Gamma 最大, 残余期限放大凸性",
        "economic_logic": "高 Gamma CB 在正股波动时收益非线性放大",
    },
    "credit_spread": {
        "name_cn": "信用利差",
        "direction": "descending",  # 越高越优
        "description": "近似 YTM 与无风险利率之差",
        "economic_logic": "高利差=高风险补偿, 利差收窄时获资本利得",
    },
    "liquidity_activity": {
        "name_cn": "流动性活跃度",
        "direction": "ascending",   # IC<0: 高活跃=过热, 低活跃标的回报更高
        "description": "成交额/发行规模, 衡量市场关注度",
        "economic_logic": "低活跃度标的未被充分定价, 存在价值回归空间",
    },
    "maturity_convexity": {
        "name_cn": "到期凸性",
        "direction": "descending",  # 越高越优
        "description": "距最优到期区间(2-4年)的接近度",
        "economic_logic": "2-4 年 CB 债股平衡最优, 促转股动力最强",
    },
    "delta_accel": {
        "name_cn": "Delta 加速度",
        "direction": "descending",  # 越高越优
        "description": "Delta 代理 (cb_value/close) 的一阶差分",
        "economic_logic": "Delta 加速=正股趋势加速, CB 从债性转向股性",
        "requires_history": True,
    },
    "credit_spread_mom": {
        "name_cn": "信用利差均值回归",
        "direction": "descending",  # IC>0: 利差扩大后均值回归, 高值=买入机会
        "description": "信用利差的 20 日变化量",
        "economic_logic": "利差近期扩大的 CB 倾向均值回归, 回报更高",
        "requires_history": True,
    },
    "liquidity_shock": {
        "name_cn": "流动性突变",
        "direction": "descending",  # 越高越优 (放量越多)
        "description": "当日成交额相对 20 日均量的比率",
        "economic_logic": "异常放量=信息驱动交易, 预示价格发现",
        "requires_history": True,
    },
    # BSM IV factors (Phase 3 / C4)
    "iv": {
        "name_cn": "BSM 隐含波动率",
        "direction": "ascending",
        "description": "BSM 模型反解的隐含波动率",
        "economic_logic": "低 IV = 期权被低估, 买入低 IV CB",
    },
    "iv_prem": {
        "name_cn": "IV 溢价率",
        "direction": "ascending",
        "description": "(σ_impl - σ_hist) / σ_hist",
        "economic_logic": ">0 期权偏贵(看多), <0 潜在低估",
    },
    "ivpr": {
        "name_cn": "IV 百分位排名",
        "direction": "ascending",
        "description": "IV 在历史窗口中的百分位",
        "economic_logic": "≤0.10 历史低位(便宜), ≥0.90 历史高位",
    },
    "pricing_bias": {
        "name_cn": "定价偏差",
        "direction": "ascending",
        "description": "(市价 - 理论价) / 理论价",
        "economic_logic": "<0 市场低估, >0 市场高估",
    },
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _years_to_maturity(maturity_date: str, now: datetime) -> float:
    """计算距到期日的年数。

    Args:
        maturity_date: YYYYMMDD 格式的到期日
        now: 当前时间

    Returns:
        剩余年数, 解析失败返回 0.0
    """
    try:
        mat = datetime.strptime(maturity_date[:8], "%Y%m%d")
        days = (mat - now).days
        return max(0.0, days / 365.25)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# BSM IV Factor integration (Phase 3 / C4)
# ---------------------------------------------------------------------------

def compute_iv_factors_for_candidate(
    candidate: CandidateInfo,
    now: datetime,
    stock_price: float | None = None,
    stock_returns: Sequence[float] | None = None,
    iv_history: list[float] | None = None,
    rating: str | None = None,
) -> dict[str, float] | None:
    """Compute BSM-based IV/Greeks factors for a single CB.

    Bridge function connecting cb_iv_greeks to cb_factors interface.

    Returns:
        Dict of factor values, or None if IV cannot be computed.
    """
    from aipha.factors.cb_iv_greeks import compute_cb_iv_factors

    T = _years_to_maturity(
        getattr(candidate, "maturity_date", None) or "", now,
    )
    if T <= 0:
        return None

    conv_price = getattr(candidate, "conversion_price", None)
    if conv_price is None or conv_price <= 0:
        return None

    cb_price = getattr(candidate, "close", 0.0)
    if cb_price <= 0:
        return None

    # Derive stock price from cb_value if not provided
    if stock_price is None:
        cb_val = getattr(candidate, "cb_value", None)
        if cb_val and cb_val > 0:
            stock_price = cb_val * conv_price / 100.0
        else:
            return None

    result = compute_cb_iv_factors(
        symbol=candidate.symbol,
        cb_price=cb_price,
        stock_price=stock_price,
        conversion_price=conv_price,
        T=T,
        stock_returns=list(stock_returns) if stock_returns else None,
        iv_history=iv_history,
        rating=rating,
    )
    return result.as_dict() if result is not None else None
