"""统一交易操作层 — 回测/模拟盘/实盘共用的交易基础设施.

本模块是所有场景下交易操作的**唯一实现**（§13.2 单一实现原则）。
日频 EOD 脚本和实时引擎均调用此处的费用计算、滑点、T+N 检查、
调仓执行、NAV 计算等函数。

核心组件::

    FeeParams          — 资产类型费率参数（从 MarketRules 派生）
    get_fee_params()   — 工厂: 按 asset_type 获取费率
    calc_trade_cost()  — 唯一费用计算入口
    apply_slippage()   — 统一滑点计算
    calc_nav()         — 统一 NAV 计算
    execute_rebalance()— 先卖后买调仓（含 T+N、涨跌停检查）

Usage::

    from aipha.execution.trading_ops import (
        get_fee_params, calc_trade_cost, apply_slippage,
        calc_nav, execute_rebalance,
    )

    fee = get_fee_params("cn_equity")
    cost = calc_trade_cost(100000.0, "sell", fee)
    result = execute_rebalance(
        cash=500000, positions=[...], target_codes=[...],
        prices={...}, per_stock_value=50000, trade_date="20260411",
        fee_params=fee, lot_size=100, slippage_bps=10.0, t_plus_n=1,
    )
"""

from __future__ import annotations

import enum
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aipha.market.price_limits import is_at_limit_down, is_at_limit_up

logger = logging.getLogger(__name__)

# CB 代码集合 — 动态加载 + 静态 fallback (§13.2 单一实现)
# 静态 fallback 前缀仅在 cb_basic 缓存不可用时使用，不再作为唯一来源
_CB_CODE_PREFIXES_FALLBACK = frozenset({
    "100", "110", "111", "113", "117", "118",
    "120", "121", "123", "124", "125", "126",
    "127", "128", "129", "132", "145",
})

# 运行时动态加载的 CB 代码全集 (from cb_basic cache)
_cb_code_set: frozenset[str] | None = None
_cb_prefix_set: frozenset[str] | None = None


def _load_cb_code_set() -> tuple[frozenset[str], frozenset[str]]:
    """从 cb_basic 缓存动态加载全量 CB 代码集和前缀集.

    返回 (code_set, prefix_set):
      - code_set: 所有 CB 的 ts_code (如 '132026.SH')
      - prefix_set: 所有 CB 的 3位前缀 — **始终包含静态 fallback 集**,
        即使缓存不完整也能检测到已知前缀

    缓存命中时零 API 开销。仅在首次调用时加载，之后复用模块级缓存。
    """
    global _cb_code_set, _cb_prefix_set
    if _cb_code_set is not None:
        return _cb_code_set, _cb_prefix_set  # type: ignore[return-value]

    try:
        from aipha.data.tushare_cb_provider import get_cb_provider
        provider = get_cb_provider()
        basic_df = provider.load_cb_basic(use_cache=True)
        if basic_df is not None and not basic_df.empty:
            codes = frozenset(basic_df["ts_code"].dropna().tolist())
            # 前缀集 = 从数据中提取的 UNION 静态 fallback
            # 确保即使缓存陈旧，已知前缀仍能被检测
            prefixes = (
                frozenset(c[:3] for c in codes if len(c) >= 3)
                | _CB_CODE_PREFIXES_FALLBACK
            )
            _cb_code_set = codes
            _cb_prefix_set = prefixes
            logger.info(
                "动态加载 CB 代码集: %d 只, %d 个前缀 (%s)",
                len(codes), len(prefixes), sorted(prefixes))
            return codes, prefixes
    except Exception as exc:
        logger.warning("动态加载 CB 代码集失败, 回退静态前缀: %s", exc)

    # 静态 fallback
    _cb_code_set = frozenset()
    _cb_prefix_set = _CB_CODE_PREFIXES_FALLBACK
    return _cb_code_set, _cb_prefix_set


def is_cb_code(code: str) -> bool:
    """判断代码是否为 CB (动态检测, 非人工枚举).

    优先从 cb_basic 全量代码集精确匹配，
    回退到 3位前缀集匹配。
    """
    code_set, prefix_set = _load_cb_code_set()
    if code_set and code in code_set:
        return True
    return code[:3] in prefix_set

# 买入冻结资金缓冲比例 — 覆盖滑点+手续费估算误差
# 回测/模拟/实盘共用同一比例（§13.2 单一实现）
BUY_FREEZE_BUFFER = 1.05


# =====================================================================
# Fee Parameters
# =====================================================================
@dataclass(frozen=True, slots=True)
class FeeParams:
    """资产类型的费率参数集.

    从 MarketRules* 类派生，作为 calc_trade_cost 的输入。
    """

    commission_rate: float = 0.0000854   # 佣金费率
    commission_min: float = 5.0          # 最低佣金
    stamp_tax_sell: float = 0.0005       # 印花税（仅卖出）
    transfer_fee: float = 0.00001        # 过户费
    lot_size: int = 100                  # 最小交易单位
    t_plus_n: int = 1                    # T+N 结算
    slippage_bps: float = 10.0           # 默认滑点 bps
    asset_type: str = "cn_equity"        # 资产类型（涨跌停检查依赖）


def get_fee_params(asset_type: str) -> FeeParams:
    """按资产类型获取费率参数（从 MarketRules 派生）.

    Args:
        asset_type: ``"cn_equity"`` | ``"convertible_bond"`` | ``"etf"``
            也接受别名: ``"a_stock"`` → ``"cn_equity"``, ``"cb"`` → ``"convertible_bond"``

    Returns:
        对应资产类型的 FeeParams 实例

    Raises:
        ValueError: 不支持的 asset_type
    """
    # §13.12: 别名归一化
    _ALIAS_MAP = {"a_stock": "cn_equity", "cb": "convertible_bond", "bond_etf": "etf"}
    asset_type = _ALIAS_MAP.get(asset_type, asset_type)

    from aipha.market.rules_cn_equity import MarketRulesCNEquity
    from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

    if asset_type == "cn_equity":
        r = MarketRulesCNEquity()
        return FeeParams(
            commission_rate=float(r.commission_rate),
            commission_min=float(r.commission_min),
            stamp_tax_sell=float(r.stamp_tax_rate),
            transfer_fee=float(r.transfer_fee_rate),
            lot_size=r.lot_size,
            t_plus_n=r.t_plus,
            slippage_bps=r.slippage_bps_default,
            asset_type="cn_equity",
        )
    if asset_type == "convertible_bond":
        r = MarketRulesConvertibleBond()
        return FeeParams(
            commission_rate=float(r.commission_rate),
            commission_min=float(r.commission_min),
            stamp_tax_sell=float(r.stamp_tax_rate),  # 0 for CB
            transfer_fee=0.0,  # CB 无过户费
            lot_size=r.lot_size,
            t_plus_n=r.t_plus,
            slippage_bps=r.slippage_bps_default,
            asset_type="convertible_bond",
        )
    if asset_type == "etf":
        from aipha.market.rules_etf import MarketRulesETF
        r = MarketRulesETF()
        return FeeParams(
            commission_rate=float(r.commission_rate),
            commission_min=float(r.commission_min),
            stamp_tax_sell=float(r.stamp_tax_rate),
            transfer_fee=float(r.transfer_fee_rate),
            lot_size=r.lot_size,
            t_plus_n=r.t_plus,
            slippage_bps=r.slippage_bps_default,
            asset_type="etf",
        )
    raise ValueError(f"不支持的 asset_type: {asset_type!r}")


# ── 组件名→资产类型标准映射 (§13.2 唯一定义) ──────────────────
# Paper 脚本的组件名（如 "equity", "cb", "bond_etf"）统一映射到
# get_fee_params 接受的标准 asset_type。
COMPONENT_ASSET_MAP: dict[str, str] = {
    "equity": "cn_equity",
    "ml": "cn_equity",
    "cn_equity": "cn_equity",
    "convertible_bond": "convertible_bond",
    "cb": "convertible_bond",
    "bond_etf": "etf",
    "etf": "etf",
    "etf_cross": "etf",
}


def resolve_asset_type(component: str) -> str:
    """将组件名/别名映射为标准 asset_type.

    Args:
        component: 组件名（如 "equity", "cb", "ml", "bond_etf"）

    Returns:
        标准 asset_type: "cn_equity" | "convertible_bond" | "etf"
    """
    return COMPONENT_ASSET_MAP.get(component, "cn_equity")


def get_fee_params_with_overrides(
    component: str,
    overrides: dict[str, Any] | None = None,
) -> FeeParams:
    """获取费率参数，支持组件名映射和配置覆盖.

    先通过 resolve_asset_type 映射组件名，再从 MarketRules 派生默认值，
    最后用 overrides 中的键值覆盖。Paper 脚本应使用此函数替代
    各自维护的 _ASSET_DEFAULTS + _get_fee_params_rNNN 模式。

    Args:
        component: 组件名（如 "equity", "cb", "bond_etf"）
        overrides: 可选覆盖参数字典, 支持的键与 FeeParams 字段同名

    Returns:
        合并后的 FeeParams 实例
    """
    at = resolve_asset_type(component)
    base = get_fee_params(at)
    if not overrides:
        return base
    return FeeParams(
        commission_rate=float(overrides.get("commission_rate", base.commission_rate)),
        commission_min=float(overrides.get("commission_min", base.commission_min)),
        stamp_tax_sell=float(overrides.get("stamp_tax_sell", base.stamp_tax_sell)),
        transfer_fee=float(overrides.get("transfer_fee", base.transfer_fee)),
        lot_size=int(overrides.get("lot_size", base.lot_size)),
        t_plus_n=int(overrides.get("t_plus_n", base.t_plus_n)),
        slippage_bps=float(overrides.get("slippage_bps", base.slippage_bps)),
        asset_type=at,
    )


# =====================================================================
# Trade Cost Calculation
# =====================================================================

# 时变印花税：委托 fees.py 的 TimeVaryingStampTaxModel（§13.2 单一实现原则）
_TIME_VARYING_STAMP_TAX: "TimeVaryingStampTaxModel | None" = None


def _get_time_varying_stamp_tax() -> "TimeVaryingStampTaxModel":
    """延迟初始化时变印花税模型（避免循环导入）。"""
    global _TIME_VARYING_STAMP_TAX  # noqa: PLW0603
    if _TIME_VARYING_STAMP_TAX is None:
        from aipha.execution.fees import TimeVaryingStampTaxModel
        _TIME_VARYING_STAMP_TAX = TimeVaryingStampTaxModel()
    return _TIME_VARYING_STAMP_TAX


def calc_trade_cost(
    amount: float,
    side: str,
    fee_params: FeeParams,
    *,
    trade_date: str | None = None,
) -> float:
    """计算单笔交易的总费用.

    §13.2: 委托给 fees.calc_fee_from_amount() 共享实现.

    Args:
        amount: 成交金额 (price × shares)
        side: ``"buy"`` 或 ``"sell"``
        fee_params: 费率参数
        trade_date: 交易日期 (``YYYYMMDD`` 或 ``YYYY-MM-DD``)。
            若提供且为 A 股，印花税使用时变模型。

    Returns:
        总费用 = 佣金 + 印花税(仅卖出) + 过户费
    """
    from aipha.execution.fees import calc_fee_from_amount

    td_obj = None
    use_tvst = False
    if trade_date is not None and fee_params.asset_type == "cn_equity":
        from datetime import date as _date
        td_clean = trade_date.replace("-", "")
        td_obj = _date(int(td_clean[:4]), int(td_clean[4:6]), int(td_clean[6:8]))
        use_tvst = True

    breakdown = calc_fee_from_amount(
        amount=amount,
        side=side.upper(),
        commission_rate=fee_params.commission_rate,
        commission_min=fee_params.commission_min,
        stamp_tax_rate=fee_params.stamp_tax_sell,
        transfer_fee_rate=fee_params.transfer_fee,
        trade_date=td_obj,
        use_time_varying_stamp_tax=use_tvst,
    )
    return breakdown.total


def create_fee_model(
    asset_type: str,
    *,
    commission_rate: float | None = None,
    commission_min: float | None = None,
    stamp_tax_rate: float | None = None,
    transfer_fee_rate: float | None = None,
    use_time_varying_stamp_tax: bool = True,
) -> "FeeModelCNEquity":
    """创建 FeeModelCNEquity 实例 — §13.7 共享工厂.

    根据 asset_type 从 MarketRules 获取默认参数，可通过关键字覆盖。

    Args:
        asset_type: ``"cn_equity"`` 或 ``"convertible_bond"``
        commission_rate: 覆盖佣金率
        commission_min: 覆盖最低佣金
        stamp_tax_rate: 覆盖印花税率
        transfer_fee_rate: 覆盖过户费率
        use_time_varying_stamp_tax: A 股是否启用时变印花税

    Returns:
        FeeModelCNEquity 实例
    """
    from aipha.execution.fees import (
        CommissionModel,
        FeeModelCNEquity,
        StampTaxModel,
        TimeVaryingStampTaxModel,
        TransferFeeModel,
    )

    params = get_fee_params(asset_type)
    cr = commission_rate if commission_rate is not None else params.commission_rate
    cm = commission_min if commission_min is not None else params.commission_min
    st = stamp_tax_rate if stamp_tax_rate is not None else params.stamp_tax_sell
    tf = transfer_fee_rate if transfer_fee_rate is not None else params.transfer_fee

    commission = CommissionModel(rate=cr, min_fee=cm)
    if use_time_varying_stamp_tax and asset_type == "cn_equity":
        stamp_tax: StampTaxModel | TimeVaryingStampTaxModel = TimeVaryingStampTaxModel()
    else:
        stamp_tax = StampTaxModel(sell_rate=st)
    transfer_fee = TransferFeeModel(rate=tf)

    return FeeModelCNEquity(
        commission=commission,
        stamp_tax=stamp_tax,
        transfer_fee=transfer_fee,
    )


# =====================================================================
# Slippage
# =====================================================================
def apply_slippage(
    price: float,
    side: str,
    bps: float,
    *,
    volume: float | None = None,
    order_qty: float | None = None,
    impact_coeff: float | None = None,
) -> float:
    """对价格施加滑点.

    买入: price × (1 + slip/10000)
    卖出: price × (1 - slip/10000)

    默认使用固定 bps 滑点。若同时提供 *volume* / *order_qty* / *impact_coeff*,
    则使用线性冲击模型 ``effective_bps = bps + impact_coeff × (order_qty / volume)``,
    与回测引擎 ``LinearImpactSlippageModel`` 逻辑一致 (§13.2)。
    """
    effective_bps = bps
    if volume and order_qty and impact_coeff:
        participation = order_qty / volume if volume > 0 else 0
        effective_bps = bps + impact_coeff * participation * 10_000
    factor = effective_bps / 10_000
    if side == "buy":
        return price * (1.0 + factor)
    return price * (1.0 - factor)


# =====================================================================
# NAV Calculation
# =====================================================================

class FallbackPolicy(enum.Enum):
    """NAV 缺价回退策略 (§13.11).

    所有调用方**必须显式指定**回退策略，禁止各脚本自行发明回退逻辑。
    """

    AVG_COST = "avg_cost"       # 使用成本价估值（paper 脚本默认）
    LAST_PRICE = "last_price"   # 使用最近有效价格
    ZERO = "zero"               # 缺价标的估值为 0（最保守）
    FACE_VALUE = "face_value"   # CB 面值 100（pillar_orchestrator CB 默认）


@dataclass
class NavResult:
    """NAV 计算结果."""

    nav: float
    cash: float
    position_value: float
    unpriced_count: int = 0
    unpriced_codes: list[str] = field(default_factory=list)


def calc_nav(
    cash: float,
    positions: list[dict[str, Any]],
    prices: dict[str, float],
    fallback: str | FallbackPolicy = FallbackPolicy.AVG_COST,
    last_prices: dict[str, float] | None = None,
) -> NavResult:
    """计算 NAV（净值）.

    Args:
        cash: 现金余额
        positions: 持仓列表 [{code, shares, avg_cost, ...}]
        prices: {code: close_price}
        fallback: 缺价格时的估值策略 (FallbackPolicy 枚举或兼容字符串)
        last_prices: 最近有效价格映射，用于 LAST_PRICE 回退
    """
    # 兼容旧的字符串参数
    if isinstance(fallback, str):
        try:
            fallback = FallbackPolicy(fallback)
        except ValueError:
            fallback = FallbackPolicy.AVG_COST

    pos_value = 0.0
    unpriced: list[str] = []
    for p in positions:
        code = p["code"]
        shares = p["shares"]
        px = prices.get(code)
        if px is not None and math.isfinite(px) and px > 0:
            pos_value += shares * px
        else:
            unpriced.append(code)
            if fallback == FallbackPolicy.AVG_COST:
                pos_value += shares * p.get("avg_cost", 0.0)
            elif fallback == FallbackPolicy.LAST_PRICE:
                last_px = None
                if last_prices is not None:
                    last_px = last_prices.get(code)
                if last_px is None:
                    last_px = p.get("last_price")
                if last_px is not None and math.isfinite(last_px) and last_px > 0:
                    pos_value += shares * last_px
            elif fallback == FallbackPolicy.FACE_VALUE:
                pos_value += shares * 100.0
            # ZERO or unresolved LAST_PRICE → no addition
    return NavResult(
        nav=cash + pos_value,
        cash=cash,
        position_value=pos_value,
        unpriced_count=len(unpriced),
        unpriced_codes=unpriced,
    )


# =====================================================================
# Trade Record
# =====================================================================
@dataclass
class TradeRecord:
    """标准化交易记录."""

    code: str
    side: str          # "buy" | "sell"
    shares: int
    price: float       # 含滑点后的成交价
    amount: float      # price × shares
    cost: float        # 手续费总计
    net: float         # 卖: amount - cost; 买: -(amount + cost)
    trade_date: str
    component: str = ""


# =====================================================================
# Rebalance Result
# =====================================================================
@dataclass
class RebalanceResult:
    """调仓执行结果."""

    new_cash: float
    new_positions: list[dict[str, Any]]
    sells: list[TradeRecord] = field(default_factory=list)
    buys: list[TradeRecord] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# =====================================================================
# Order Intent — Paper/Live 共享决策层 (§4.3 统一执行架构)
# =====================================================================
@dataclass
class ExecutionIntent:
    """执行层订单意图 — Paper 和 Live 共享的调仓决策输出.

    由 generate_order_intents() 产出, 只描述"应该做什么",
    不执行任何成交。Paper 和 Live 各自消费 intents 列表。

    与 domain.models.OrderIntent（策略层）是不同的流水线阶段:
    - 策略层 OrderIntent: symbol + Side enum + qty
    - 执行层 ExecutionIntent: code + str side + shares
    """

    code: str
    side: str           # "buy" | "sell"
    shares: int         # 目标交易股数（已取整 lot_size）
    signal_price: float  # 信号日价格（记录/对照用）
    component: str = ""
    reason: str = ""    # "exit_position" | "new_position"


# 向后兼容别名 — 下游若直接引用 OrderIntent 仍可工作
OrderIntent = ExecutionIntent


@dataclass
class OrderIntentResult:
    """generate_order_intents() 的返回值."""

    intents: list[ExecutionIntent] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    frozen_cash: float = 0.0     # 买入意图预估占用资金
    estimated_release: float = 0.0  # 卖出意图预估释放资金


# =====================================================================
# Price Limit Helper (§4.3: 回测-Paper 一致性)
# =====================================================================
def _get_price_limits(
    code: str,
    prev_close: float,
    fee_params: FeeParams,
    trade_date: str = "",
) -> tuple[float | None, float | None]:
    """按资产类型获取涨跌停价格，与回测引擎 MarketRules 保持一致.

    Returns:
        (limit_up, limit_down) — None 表示无涨跌停限制
    """
    from datetime import date as _date

    if prev_close <= 0:
        return None, None

    at = fee_params.asset_type
    if at == "convertible_bond":
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
        rules = MarketRulesConvertibleBond()
        td = None
        if trade_date:
            try:
                td = _date(int(trade_date[:4]), int(trade_date[4:6]),
                           int(trade_date[6:8]))
            except (ValueError, IndexError):
                pass
        if td is None:
            # 防御：trade_date 缺失/无效时，默认使用新规后日期以启用 ±20% 限制
            logger.warning("CB 涨跌停检查: trade_date=%r 无效, 默认启用新规限制",
                           trade_date)
            td = _date(2025, 1, 1)
        return rules.get_price_limits(prev_close, trade_date=td)
    if at not in ("cn_equity", "etf"):
        logger.warning("_get_price_limits: 未知 asset_type=%r, 使用 A 股规则",
                        at)
    # cn_equity / etf — 使用 A 股规则
    from aipha.market.rules_cn_equity import MarketRulesCNEquity
    rules_eq = MarketRulesCNEquity()
    return rules_eq.get_price_limits(prev_close, symbol=code)


# =====================================================================
# generate_order_intents — Paper/Live 共享决策 (§4.3)
# =====================================================================
def generate_order_intents(
    current_positions: list[dict[str, Any]],
    target_codes: list[str],
    prices: dict[str, float],
    per_stock_value: float,
    trade_date: str,
    *,
    fee_params: FeeParams,
    component: str = "",
    lot_size: int | None = None,
    slippage_bps: float | None = None,
    t_plus_n: int | None = None,
    check_price_limits: bool = True,
    prev_closes: dict[str, float] | None = None,
    cash_available: float = 0.0,
    cash_floor: float = 0.0,
    total_nav: float = 0.0,
    fat_finger_per_stock_pct: float = 0.15,
    fat_finger_target_pct: float = 0.70,
    suspended_codes: set[str] | None = None,
) -> OrderIntentResult:
    """生成调仓订单意图 — Paper 和 Live 的共享决策层.

    只做决策不做执行: 计算应该卖什么、买什么, 含全部安全检查
    (T+N / 涨跌停 / cash_floor / lot_size / fat-finger). Paper 和
    Live 各自消费返回的 intents 列表, 分别做虚拟成交或 QMT 提交.

    Args:
        current_positions: 当前持仓 [{code, shares, component?, buy_date?}]
        target_codes: 目标持仓代码列表
        prices: {code: price} 价格字典
        per_stock_value: 每只目标股的分配金额
        trade_date: 信号日期 YYYYMMDD
        fee_params: 费率参数
        component: 组件名（多组件时过滤持仓）
        lot_size: 最小交易单位
        slippage_bps: 滑点基点（用于估算买入金额）
        t_plus_n: T+N 规则
        check_price_limits: 是否检查涨跌停
        prev_closes: {code: prev_close} 前收盘价
        cash_available: 可用现金（卖出释放的资金会累加）
        cash_floor: 最低保留现金
        total_nav: 总 NAV（fat-finger 检查用，0=跳过检查）
        fat_finger_per_stock_pct: 单票/NAV 上限 (默认 15%)
        fat_finger_target_pct: 总目标/NAV 上限 (默认 70%)
        suspended_codes: 停牌标的集合 (买入跳过, 卖出保留为挂单)
    """
    lot = lot_size if lot_size is not None else fee_params.lot_size
    slip = slippage_bps if slippage_bps is not None else fee_params.slippage_bps
    tpn = t_plus_n if t_plus_n is not None else fee_params.t_plus_n

    # ── Fat-finger 保护 (§13.2 统一实现) ──
    if total_nav > 0:
        if per_stock_value / total_nav > fat_finger_per_stock_pct:
            logger.error(
                "fat-finger拦截: per_stock/NAV=%.1f%% > %.0f%% "
                "(component=%s)",
                per_stock_value / total_nav * 100,
                fat_finger_per_stock_pct * 100, component,
            )
            return OrderIntentResult(
                intents=[], skipped=[], warnings=[
                    f"fat-finger: per_stock/NAV="
                    f"{per_stock_value / total_nav:.1%}"],
                frozen_cash=0.0, estimated_release=0.0,
            )
        target_value = per_stock_value * len(target_codes)
        if target_value / total_nav > fat_finger_target_pct:
            logger.error(
                "fat-finger拦截: target/NAV=%.1f%% > %.0f%% "
                "(component=%s)",
                target_value / total_nav * 100,
                fat_finger_target_pct * 100, component,
            )
            return OrderIntentResult(
                intents=[], skipped=[], warnings=[
                    f"fat-finger: target/NAV="
                    f"{target_value / total_nav:.1%}"],
                frozen_cash=0.0, estimated_release=0.0,
            )

    if check_price_limits and not prev_closes:
        logger.warning(
            "generate_order_intents: check_price_limits=True 但 "
            "prev_closes 为空 — 降级关闭涨跌停检查 (trade_date=%s)",
            trade_date,
        )
        check_price_limits = False

    target_set = set(target_codes)
    intents: list[ExecutionIntent] = []
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []
    estimated_release = 0.0

    # ── 分离本组件持仓 ──
    my_positions = [
        p for p in current_positions
        if not component or p.get("component", "") == component
    ]
    held_codes = {p["code"] for p in my_positions}

    def _resolve_pc(code: str) -> float | None:
        if not check_price_limits:
            return None
        raw = prev_closes.get(code) if prev_closes else None
        if raw is None:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) and v > 0 else None

    # ════════ Phase 1: SELL intents ════════
    for p in my_positions:
        code = p["code"]
        if code in target_set:
            continue  # 保留

        # 停牌标的无法卖出, 跳过并保留持仓
        if suspended_codes and code in suspended_codes:
            skipped.append({"code": code, "reason": "停牌无法卖出"})
            continue

        if tpn > 0 and p.get("buy_date", "") == trade_date:
            skipped.append({"code": code,
                            "reason": f"T+{tpn} 当日买入不可卖出"})
            continue

        px = prices.get(code)
        if px is None or not math.isfinite(px) or px <= 0:
            skipped.append({"code": code, "reason": "无有效价格"})
            continue

        if check_price_limits:
            pc = _resolve_pc(code)
            if pc is None:
                skipped.append({"code": code,
                                "reason": "缺少昨收无法校验涨跌停"})
                continue
            _lu, _ld = _get_price_limits(code, pc, fee_params, trade_date)
            if is_at_limit_down(px, _ld):
                skipped.append({"code": code, "reason": "跌停无法卖出"})
                continue

        sell_px = apply_slippage(px, "sell", slip)
        shares = p["shares"]
        amount = sell_px * shares
        cost = calc_trade_cost(amount, "sell", fee_params, trade_date=trade_date)
        estimated_release += amount - cost

        intents.append(ExecutionIntent(
            code=code, side="sell", shares=shares,
            signal_price=px, component=component,
            reason="exit_position",
        ))

    # ════════ Phase 2: BUY intents ════════
    budget = cash_available + estimated_release
    frozen_cash = 0.0

    for code in target_codes:
        if code in held_codes:
            continue

        # 停牌检查: 停牌标的不可买入 (§3.2 风险排除)
        if suspended_codes and code in suspended_codes:
            skipped.append({"code": code, "reason": "停牌无法买入"})
            continue

        px = prices.get(code)
        if px is None or not math.isfinite(px) or px <= 0:
            skipped.append({"code": code, "reason": "无有效价格"})
            continue

        if check_price_limits:
            pc = _resolve_pc(code)
            if pc is None:
                skipped.append({"code": code,
                                "reason": "缺少昨收无法校验涨跌停"})
                continue
            _lu, _ld = _get_price_limits(code, pc, fee_params, trade_date)
            if is_at_limit_up(px, _lu):
                skipped.append({"code": code, "reason": "涨停无法买入"})
                continue

        buy_px = apply_slippage(px, "buy", slip)
        shares = int(per_stock_value / buy_px / lot) * lot
        if shares <= 0:
            skipped.append({"code": code, "reason": "分配额不足一手"})
            continue

        amount = buy_px * shares
        cost = calc_trade_cost(amount, "buy", fee_params, trade_date=trade_date)
        total = amount + cost

        available = max(budget - frozen_cash - cash_floor, 0.0)
        if total > available:
            alt = int(available * 0.95 / buy_px / lot) * lot
            if alt > 0:
                amount = buy_px * alt
                cost = calc_trade_cost(amount, "buy", fee_params, trade_date=trade_date)
                total = amount + cost
                shares = alt
                if total > available:
                    skipped.append({"code": code,
                                    "reason": "资金不足(含现金保留)"})
                    continue
            else:
                skipped.append({"code": code, "reason": "资金不足"})
                continue

        frozen_cash += total
        intents.append(ExecutionIntent(
            code=code, side="buy", shares=shares,
            signal_price=px, component=component,
            reason="new_position",
        ))

    return OrderIntentResult(
        intents=intents,
        skipped=skipped,
        warnings=warnings,
        frozen_cash=frozen_cash,
        estimated_release=estimated_release,
    )


# =====================================================================
# Core: execute_rebalance
# =====================================================================
def execute_rebalance(
    cash: float,
    positions: list[dict[str, Any]],
    target_codes: list[str],
    prices: dict[str, float],
    per_stock_value: float,
    trade_date: str,
    *,
    fee_params: FeeParams,
    lot_size: int | None = None,
    slippage_bps: float | None = None,
    component: str = "",
    t_plus_n: int | None = None,
    check_price_limits: bool = False,
    prev_closes: dict[str, float] | None = None,
    retry_on_insufficient: bool = True,
    cash_floor: float = 0.0,
    volumes: dict[str, float] | None = None,
    max_participation_rate: float | None = None,
    opening_volume_fraction: float = 0.15,
) -> RebalanceResult:
    """执行先卖后买的目标持仓调仓.

    这是所有模拟盘/回测/实盘场景的**唯一调仓实现**。

    算法:
        1. 卖出: 当前持仓中不在 target_codes 中的标的
        2. 买入: target_codes 中不在当前持仓中的标的
        3. 持仓保留: 同时在当前持仓和 target_codes 中的标的

    Args:
        cash: 当前现金
        positions: 当前持仓 [{code, shares, avg_cost, component?, buy_date?}]
        target_codes: 目标持仓代码列表
        prices: {code: price} 当日收盘价
        per_stock_value: 每只目标股的分配金额
        trade_date: 交易日期 YYYYMMDD
        fee_params: 费率参数
        lot_size: 最小交易单位（None 则用 fee_params.lot_size）
        slippage_bps: 滑点基点（None 则用 fee_params.slippage_bps）
        component: 组件名（多组件组合时用于过滤持仓）
        t_plus_n: T+N 规则（None 则用 fee_params.t_plus_n）
        check_price_limits: 是否检查涨跌停
        prev_closes: {code: prev_close} 前一日收盘价，涨跌停检查用
        retry_on_insufficient: 资金不足时是否降级重算股数
        cash_floor: 最低保留现金 (默认0). 买入时不会让现金低于此值.
        volumes: {code: volume} 前一交易日成交量（股/张），用于参与率约束
        max_participation_rate: 最大参与率 (0-1], None=不限制
        opening_volume_fraction: 开盘集合竞价占全天量比例 (默认0.15)
    """
    lot = lot_size if lot_size is not None else fee_params.lot_size
    slip = slippage_bps if slippage_bps is not None else fee_params.slippage_bps
    tpn = t_plus_n if t_plus_n is not None else fee_params.t_plus_n

    # §R140+P0-5 → §审计20260421 A-1: prev_closes 为空时不再全局关闭涨跌停检查,
    # 而是逐标的降级——缺 prev_close 的个股跳过+warning, 有的正常检查 (§13.3).
    if check_price_limits and not prev_closes:
        logger.warning(
            "check_price_limits=True 但 prev_closes 为空/None — "
            "将逐标的降级: 缺 prev_close 的个股跳过交易并记录 warning "
            "(trade_date=%s, targets=%d). 请检查昨收数据获取链路.",
            trade_date, len(target_codes),
        )
        # 注意: 不再设 check_price_limits=False
        # 逐标的降级已由 _resolve_prev_close() + 下游 pc is None 分支处理

    target_set = set(target_codes)
    sells: list[TradeRecord] = []
    buys: list[TradeRecord] = []
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []

    # ── 分离本组件持仓 vs 其他组件持仓 ──
    my_positions = []
    other_positions = []
    for p in positions:
        if component and p.get("component", "") != component:
            other_positions.append(p)
        else:
            my_positions.append(p)

    held_codes = {p["code"] for p in my_positions}
    kept: list[dict[str, Any]] = []

    def _resolve_prev_close(code: str) -> float | None:
        if not check_price_limits:
            return None
        raw = prev_closes.get(code) if prev_closes else None
        if raw is None:
            return None
        try:
            prev_close = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(prev_close) or prev_close <= 0:
            return None
        return prev_close

    # ════════════ Phase 1: SELL ════════════
    for p in my_positions:
        code = p["code"]
        if code in target_set:
            kept.append(p)
            continue

        # ── T+N 检查：当日买入不可卖出 ──
        if tpn > 0:
            buy_date = p.get("buy_date", "")
            if buy_date == trade_date:
                kept.append(p)
                skipped.append({"code": code, "reason": f"T+{tpn} 当日买入不可卖出"})
                warnings.append(f"{code}: T+{tpn} skip sell (bought today)")
                continue

        px = prices.get(code)
        if px is None or not math.isfinite(px) or px <= 0:
            kept.append(p)
            skipped.append({"code": code, "reason": "无有效价格"})
            continue

        # ── 涨跌停检查（卖出方向：跌停不可卖）——使用 MarketRules (§4.3) ──
        if check_price_limits:
            pc = _resolve_prev_close(code)
            if pc is None:
                kept.append(p)
                skipped.append({"code": code, "reason": "缺少有效昨收，无法校验涨跌停"})
                warnings.append(f"{code}: missing prev_close, skip sell")
                continue
            _lu, _ld = _get_price_limits(code, pc, fee_params, trade_date)
            if is_at_limit_down(px, _ld):
                kept.append(p)
                skipped.append({"code": code, "reason": "跌停无法卖出"})
                continue

        sell_px = apply_slippage(px, "sell", slip)
        shares = p["shares"]
        amount = sell_px * shares
        cost = calc_trade_cost(amount, "sell", fee_params, trade_date=trade_date)
        net = amount - cost
        cash += net

        sells.append(TradeRecord(
            code=code, side="sell", shares=shares, price=sell_px,
            amount=amount, cost=cost, net=net,
            trade_date=trade_date, component=component,
        ))

    # ════════════ Phase 2: BUY ════════════
    for code in target_codes:
        if code in held_codes:
            continue

        px = prices.get(code)
        if px is None or not math.isfinite(px) or px <= 0:
            skipped.append({"code": code, "reason": "无有效价格"})
            continue

        # ── 涨跌停检查（买入方向：涨停不可买）——使用 MarketRules (§4.3) ──
        if check_price_limits:
            pc = _resolve_prev_close(code)
            if pc is None:
                skipped.append({"code": code, "reason": "缺少有效昨收，无法校验涨跌停"})
                warnings.append(f"{code}: missing prev_close, skip buy")
                continue
            _lu, _ld = _get_price_limits(code, pc, fee_params, trade_date)
            if is_at_limit_up(px, _lu):
                skipped.append({"code": code, "reason": "涨停无法买入"})
                continue

        buy_px = apply_slippage(px, "buy", slip)
        shares = int(per_stock_value / buy_px / lot) * lot
        if shares <= 0:
            skipped.append({"code": code, "reason": "分配额不足一手"})
            continue

        # ── Volume constraint: 参与率截断 (§P0 容量修正) ──
        if max_participation_rate and volumes:
            bar_vol = volumes.get(code)
            if bar_vol and bar_vol > 0:
                effective_vol = bar_vol * opening_volume_fraction
                max_qty = int(effective_vol * max_participation_rate // lot) * lot
                if max_qty <= 0:
                    skipped.append({"code": code, "reason": "流动性不足(参与率限制)"})
                    continue
                if shares > max_qty:
                    shares = max_qty

        amount = buy_px * shares
        cost = calc_trade_cost(amount, "buy", fee_params, trade_date=trade_date)
        total = amount + cost

        # ── cash_floor 保护：保留最低现金 ──
        available_cash = max(cash - cash_floor, 0.0)

        # ── 资金不足处理 ──
        if total > available_cash:
            if retry_on_insufficient:
                # 降级：用可用现金的 95% 重算
                alt_shares = int(available_cash * 0.95 / buy_px / lot) * lot
                if alt_shares > 0:
                    amount = buy_px * alt_shares
                    cost = calc_trade_cost(amount, "buy", fee_params, trade_date=trade_date)
                    total = amount + cost
                    shares = alt_shares
                    if total > available_cash:
                        skipped.append({"code": code, "reason": "资金不足(含现金保留)"})
                        continue
                else:
                    skipped.append({"code": code, "reason": "资金不足"})
                    continue
            else:
                skipped.append({"code": code, "reason": "资金不足"})
                continue

        cash -= total
        buys.append(TradeRecord(
            code=code, side="buy", shares=shares, price=buy_px,
            amount=amount, cost=cost, net=-(total),
            trade_date=trade_date, component=component,
        ))
        new_pos: dict[str, Any] = {
            "code": code,
            "shares": shares,
            "avg_cost": buy_px,
            "last_price": buy_px,
            "component": component,
            "buy_date": trade_date,
        }
        kept.append(new_pos)

    # ── 合并结果 ──
    new_positions = other_positions + kept
    return RebalanceResult(
        new_cash=cash,
        new_positions=new_positions,
        sells=sells,
        buys=buys,
        skipped=skipped,
        warnings=warnings,
    )


# =====================================================================
# Fill Order Intents — T+1 开盘价成交模型 (Phase 2)
# =====================================================================
def fill_order_intents(
    cash: float,
    positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    fill_prices: dict[str, float],
    trade_date: str,
    *,
    fee_params: FeeParams,
    fee_params_by_component: dict[str, FeeParams] | None = None,
) -> RebalanceResult:
    """将 pending order intents 以给定成交价填充.

    用于 Paper T+1 模型: T 日生成 intents, T+1 用开盘价填充.
    先处理卖出(释放资金), 再处理买入(消耗资金).

    Args:
        cash: 当前可用现金.
        positions: 当前持仓列表.
        intents: 序列化的 OrderIntent 字典列表
                 (keys: code, side, shares, signal_price, component).
        fill_prices: {code: fill_price} 成交价 (通常为 T+1 开盘价).
        trade_date: 成交日期 (T+1).
        fee_params: 费率参数 (默认, 当 intent 无 component 信息时使用).
        fee_params_by_component: {component: FeeParams} 按组件覆盖费率.
            传入此参数时, 每笔 intent 按其 component 使用对应费率.
    """
    sells_out: list[TradeRecord] = []
    buys_out: list[TradeRecord] = []
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []
    _fp_map = fee_params_by_component or {}

    def _resolve_fp(comp: str) -> FeeParams:
        return _fp_map.get(comp, fee_params)

    slip = fee_params.slippage_bps

    # 按 component 索引持仓, 便于快速查找
    pos_index: dict[str, int] = {}
    for idx, p in enumerate(positions):
        pos_index[p["code"]] = idx

    # ── Phase 1: SELL ──
    sell_intents = [i for i in intents if i.get("side") == "sell"]
    remove_indices: set[int] = set()
    for intent in sell_intents:
        code = intent["code"]
        shares = intent.get("shares", 0)
        component = intent.get("component", "")
        fp = fill_prices.get(code)
        if fp is None or fp <= 0:
            skipped.append({"code": code, "reason": "T+1无有效开盘价"})
            warnings.append(f"{code}: no fill price, skip sell")
            continue
        idx = pos_index.get(code)
        if idx is None:
            skipped.append({"code": code, "reason": "持仓已不存在"})
            continue
        pos = positions[idx]
        actual = min(shares, pos.get("shares", 0))
        if actual <= 0:
            skipped.append({"code": code, "reason": "可卖股数为0"})
            continue
        sell_px = apply_slippage(fp, "sell", slip)
        amount = sell_px * actual
        intent_fp = _resolve_fp(component)
        cost = calc_trade_cost(amount, "sell", intent_fp, trade_date=trade_date)
        net = amount - cost
        cash += net
        sells_out.append(TradeRecord(
            code=code, side="sell", shares=actual,
            price=sell_px, amount=amount, cost=cost, net=net,
            trade_date=trade_date, component=component,
        ))
        if actual >= pos.get("shares", 0):
            remove_indices.add(idx)
        else:
            positions[idx] = {
                **pos, "shares": pos["shares"] - actual,
            }

    # 移除已清仓的持仓
    if remove_indices:
        positions = [
            p for i, p in enumerate(positions)
            if i not in remove_indices
        ]

    # ── Phase 2: BUY ──
    buy_intents = [i for i in intents if i.get("side") == "buy"]
    for intent in buy_intents:
        code = intent["code"]
        shares = intent.get("shares", 0)
        component = intent.get("component", "")
        fp = fill_prices.get(code)
        if fp is None or fp <= 0:
            skipped.append({"code": code, "reason": "T+1无有效开盘价"})
            warnings.append(f"{code}: no fill price, skip buy")
            continue
        buy_px = apply_slippage(fp, "buy", slip)
        amount = buy_px * shares
        intent_fp = _resolve_fp(component)
        cost = calc_trade_cost(amount, "buy", intent_fp, trade_date=trade_date)
        total = amount + cost
        if total > cash:
            # 降级: 用可用资金 95% 重算
            lot = intent_fp.lot_size
            alt = int(cash * 0.95 / buy_px / lot) * lot
            if alt > 0:
                amount = buy_px * alt
                cost = calc_trade_cost(amount, "buy", intent_fp, trade_date=trade_date)
                total = amount + cost
                shares = alt
                if total > cash:
                    skipped.append({
                        "code": code, "reason": "资金不足",
                    })
                    continue
            else:
                skipped.append({"code": code, "reason": "资金不足"})
                continue
        cash -= total
        buys_out.append(TradeRecord(
            code=code, side="buy", shares=shares,
            price=buy_px, amount=amount, cost=cost,
            net=-(total), trade_date=trade_date,
            component=component,
        ))
        positions.append({
            "code": code, "shares": shares,
            "avg_cost": buy_px, "last_price": buy_px,
            "component": component, "buy_date": trade_date,
        })

    return RebalanceResult(
        new_cash=cash, new_positions=positions,
        sells=sells_out, buys=buys_out,
        skipped=skipped, warnings=warnings,
    )


# =====================================================================
# Price Fetching — 共享价格获取 (§13.9)
# =====================================================================

# 默认重试参数（涉及真金白银，不轻易放弃）
PRICE_FETCH_RETRIES: int = 30
PRICE_FETCH_INTERVAL: float = 2.0  # 秒

# Deadline-driven 重试阶梯: (累计秒数上限, 每次间隔秒数, 阶段名)
_RETRY_SCHEDULE: list[tuple[float, float, str]] = [
    (120, 2.0, "快速"),          # 0~2min: 2s 间隔
    (600, 30.0, "慢速"),         # 2~10min: 30s 间隔
    (float("inf"), 300.0, "持续"),  # 10min~截止: 5min 间隔
]

# 默认截止时间 (HH:MM), 超过此时间停止重试并保持仓位
_DEFAULT_PRICE_DEADLINE = "23:30"


def _get_cb_snapshot(trade_date: str) -> "pd.DataFrame | None":
    """获取 CB 日线快照（通过 Provider 单例，缓存优先）。

    统一数据入口（§Plan Phase 2）：所有 trading_ops 中的 CB 数据获取
    均通过此函数，底层由 TuShareCBProvider.get_daily_snapshot() 处理。
    缓存命中时零 API 开销；未命中时 3s 重试永不放弃。
    """
    try:
        from aipha.data.tushare_cb_provider import get_cb_provider
        provider = get_cb_provider()
        df = provider.get_daily_snapshot(trade_date)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.warning("_get_cb_snapshot(%s) 失败: %s", trade_date, exc)
    return None


def _get_equity_snapshot(trade_date: str) -> "pd.DataFrame | None":
    """获取 A 股全市场日线快照（通过 Provider 单例，缓存优先）。

    与 _get_cb_snapshot 完全对齐：缓存命中时零 API 开销；
    未命中时 3s 重试永不放弃。
    """
    try:
        from aipha.data.tushare_equity_provider import get_equity_provider
        provider = get_equity_provider()
        df = provider.get_daily_snapshot(trade_date)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.warning("_get_equity_snapshot(%s) 失败: %s", trade_date, exc)
    return None


def _get_etf_snapshot(trade_date: str) -> "pd.DataFrame | None":
    """获取 ETF 全市场日线快照（通过 Provider 单例，缓存优先）。

    与 _get_cb_snapshot/_get_equity_snapshot 完全对齐。
    """
    try:
        from aipha.data.tushare_etf_provider import get_etf_provider
        provider = get_etf_provider()
        df = provider.get_daily_snapshot(trade_date)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.warning("_get_etf_snapshot(%s) 失败: %s", trade_date, exc)
    return None


def _fetch_prices_once(
    gw: Any,
    codes: list[str],
    trade_date: str,
) -> dict[str, float]:
    """单次尝试获取 A股+CB+ETF 的收盘价.

    按标的代码格式自动分派到不同 TuShare 接口。
    """
    prices: dict[str, float] = {}
    if not codes:
        return prices

    etf_codes = [c for c in codes if c.startswith("5")
                 or c.startswith("159")]
    cb_codes = [c for c in codes if is_cb_code(c)
                and c not in etf_codes]
    eq_codes = [c for c in codes
                if c not in etf_codes and c not in cb_codes]

    if eq_codes:
        df = _get_equity_snapshot(trade_date)
        if df is not None and not df.empty:
            sub = df[df["ts_code"].isin(eq_codes)]
            prices.update(dict(zip(sub["ts_code"], sub["close"])))
        else:
            # 降级: Provider 不可用时回退到直接 gw.call
            try:
                df = gw.call("daily", trade_date=trade_date,
                             fields="ts_code,close")
                if df is not None and not df.empty:
                    sub = df[df["ts_code"].isin(eq_codes)]
                    prices.update(dict(zip(sub["ts_code"], sub["close"])))
            except Exception:
                logger.debug("equity price fetch retry failed", exc_info=True)

    if cb_codes:
        cb_df = _get_cb_snapshot(trade_date)
        if cb_df is not None and not cb_df.empty:
            sub = cb_df[cb_df["ts_code"].isin(cb_codes)]
            prices.update(dict(zip(sub["ts_code"], sub["close"])))

    if etf_codes:
        etf_df = _get_etf_snapshot(trade_date)
        if etf_df is not None and not etf_df.empty:
            sub = etf_df[etf_df["ts_code"].isin(etf_codes)]
            prices.update(dict(zip(sub["ts_code"], sub["close"])))
        else:
            # 降级: Provider 不可用时逐只查 fund_daily
            for c in etf_codes:
                try:
                    edf = gw.call("fund_daily", ts_code=c,
                                  trade_date=trade_date,
                                  fields="ts_code,close")
                    if edf is not None and not edf.empty:
                        prices[c] = float(edf["close"].iloc[0])
                except Exception:
                    logger.debug("ETF fund_daily fetch failed for %s", c, exc_info=True)
        # 降级: 通过 equity snapshot 补漏 (部分 ETF 有 daily 数据)
        missing_etf = [c for c in etf_codes if c not in prices]
        if missing_etf:
            eq_df = _get_equity_snapshot(trade_date)
            if eq_df is not None and not eq_df.empty:
                sub = eq_df[eq_df["ts_code"].isin(missing_etf)]
                prices.update(dict(zip(sub["ts_code"], sub["close"])))

    # ══ Cross-API Fallback ══
    # 如果任何代码在其分类 API 中未找到，尝试其他 API 兜底
    # 这是防止分类错误导致无限重试的根本安全网
    all_missing = [c for c in codes if c not in prices]
    if all_missing:
        # 尝试从 CB 快照中查找（可能是被错误分类为 A股的 CB）
        cb_df = _get_cb_snapshot(trade_date)
        if cb_df is not None and not cb_df.empty:
            sub = cb_df[cb_df["ts_code"].isin(all_missing)]
            if not sub.empty:
                found = dict(zip(sub["ts_code"], sub["close"]))
                prices.update(found)
                logger.info(
                    "Cross-API fallback: %d 只从 CB 快照补获 %s",
                    len(found), list(found.keys())[:5])

        # 仍缺失的尝试从 A股 快照查找
        still_missing = [c for c in all_missing if c not in prices]
        if still_missing:
            eq_df = _get_equity_snapshot(trade_date)
            if eq_df is not None and not eq_df.empty:
                sub = eq_df[eq_df["ts_code"].isin(still_missing)]
                if not sub.empty:
                    found = dict(zip(sub["ts_code"], sub["close"]))
                    prices.update(found)
                    logger.info(
                        "Cross-API fallback: %d 只从 A股快照补获 %s",
                        len(found), list(found.keys())[:5])

    return prices


def _get_deadline_dt(
    cfg: dict | None = None,
    tz: Any | None = None,
) -> datetime:
    """计算本次运行的价格获取截止时间."""
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo as _ZI

    _tz = tz or _ZI("Asia/Shanghai")
    deadline_str = (cfg or {}).get(
        "price_fetch_deadline", _DEFAULT_PRICE_DEADLINE)
    hh, mm = map(int, deadline_str.split(":"))
    now = datetime.now(tz=_tz)
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += _td(days=1)
    return deadline


def _get_retry_interval(elapsed: float) -> tuple[float, str]:
    """根据已耗时查找当前重试间隔和阶段名."""
    for threshold, interval, name in _RETRY_SCHEDULE:
        if elapsed < threshold:
            return interval, name
    return 300.0, "持续"


# 告警升级阈值: (累计秒数, 日志级别, 是否推送)
_ALERT_THRESHOLDS: list[tuple[int, str, bool]] = [
    (5 * 60, "INFO", False),
    (15 * 60, "WARNING", True),
    (30 * 60, "ERROR", True),
    (60 * 60, "CRITICAL", True),
]


def _check_price_alert_escalation(
    elapsed: float,
    sent_levels: set[str],
    trade_date: str,
    total: int,
    got: int,
    alert_fn: Any | None = None,
) -> None:
    """根据已耗时触发告警升级."""
    cov_pct = got / total * 100 if total else 0
    for threshold_sec, level, push in _ALERT_THRESHOLDS:
        if elapsed >= threshold_sec and level not in sent_levels:
            sent_levels.add(level)
            mins = int(elapsed / 60)
            msg = (f"价格获取已持续 {mins} 分钟, "
                   f"覆盖 {got}/{total} ({cov_pct:.0f}%)")
            log_fn = getattr(logger, level.lower(), logger.warning)
            log_fn("[ALERT-%s] %s", level, msg)
            if push and alert_fn:
                alert_fn(
                    f"价格获取延迟 {mins}min ({level})",
                    f"交易日: {trade_date}, "
                    f"覆盖率: {got}/{total} ({cov_pct:.0f}%)")
    # >=60min 后每 30min 重复
    if elapsed >= 3600:
        repeats_due = int((elapsed - 3600) / 1800) + 1
        rk = f"CRITICAL_REPEAT_{repeats_due}"
        if rk not in sent_levels:
            sent_levels.add(rk)
            mins = int(elapsed / 60)
            if alert_fn:
                alert_fn(
                    f"价格获取严重延迟 {mins}min (第{repeats_due}次)",
                    f"交易日: {trade_date}, "
                    f"覆盖率: {got}/{total} ({cov_pct:.0f}%)")


def fetch_prices(
    gw: Any,
    codes: list[str],
    trade_date: str,
    *,
    retries: int = PRICE_FETCH_RETRIES,
    interval: float = PRICE_FETCH_INTERVAL,
    deadline: str | None = None,
    min_coverage: float = 0.0,
    cfg: dict | None = None,
    alert_fn: Any | None = None,
) -> dict[str, float]:
    """获取 A股+CB+ETF 收盘价 — Paper/Live 统一入口 (§13.9).

    支持两种模式:
    - **固定重试模式** (默认): retries × interval, 兼容回测/轻量调用
    - **Deadline 模式**: 指定 deadline 或 cfg 时启用, 持续重试到截止时间,
      含告警升级 + 逐只补查, 用于 Paper/Live 生产环境

    Args:
        gw: TuShareGateway 实例
        codes: 标的代码列表
        trade_date: 交易日期 YYYYMMDD
        retries: 固定模式最大重试次数 (默认 30)
        interval: 固定模式每次间隔秒数 (默认 2.0)
        deadline: 截止时间 "HH:MM" (启用 deadline 模式)
        min_coverage: 覆盖率阈值 (0~1), 达标即返回
        cfg: YAML 配置 dict (可含 price_fetch_deadline)
        alert_fn: 告警回调 fn(title, body), 为 None 则仅日志

    Returns:
        {ts_code: close_price}

    Raises:
        RuntimeError: 完全无数据时
    """
    import time

    total = len(codes)
    if not total:
        return {}

    use_deadline = deadline is not None or cfg is not None

    if use_deadline:
        return _fetch_prices_deadline(
            gw, codes, trade_date,
            min_coverage=min_coverage, cfg=cfg,
            alert_fn=alert_fn, deadline_str=deadline,
        )

    # ── 固定重试模式 (向后兼容) ──
    for attempt in range(retries):
        try:
            prices = _fetch_prices_once(gw, codes, trade_date)
            if prices or not codes:
                if attempt > 0:
                    logger.info(
                        "价格获取成功 (第 %d 次尝试), %d/%d",
                        attempt + 1, len(prices), len(codes))
                return prices
            logger.warning(
                "API 返回空价格 (尝试 %d/%d), %.0fs 后重试",
                attempt + 1, retries, interval)
        except Exception as exc:
            if attempt < 3 or attempt % 10 == 0:
                logger.warning(
                    "价格获取失败 (尝试 %d/%d): %s",
                    attempt + 1, retries, exc)
        time.sleep(interval)

    # 最后一搏
    try:
        prices = _fetch_prices_once(gw, codes, trade_date)
        if prices:
            return prices
    except Exception:
        logger.debug("final price fetch attempt failed", exc_info=True)

    raise RuntimeError(
        f"价格获取彻底失败: {total} 个标的在 "
        f"{retries} 次重试后仍无法获取价格")


def _fetch_prices_deadline(
    gw: Any,
    codes: list[str],
    trade_date: str,
    *,
    min_coverage: float = 0.0,
    cfg: dict | None = None,
    alert_fn: Any | None = None,
    deadline_str: str | None = None,
) -> dict[str, float]:
    """Deadline-driven 价格获取 — 生产级 (内部函数)."""
    import time
    from zoneinfo import ZoneInfo as _ZI

    _tz = _ZI("Asia/Shanghai")
    total = len(codes)

    # 构建 deadline datetime
    if deadline_str:
        eff_cfg = dict(cfg or {})
        eff_cfg["price_fetch_deadline"] = deadline_str
    else:
        eff_cfg = cfg
    deadline_dt = _get_deadline_dt(eff_cfg, tz=_tz)

    best_prices: dict[str, float] = {}
    attempt = 0
    start_time = time.monotonic()
    sent_levels: set[str] = set()

    logger.info("价格获取开始: %d 个标的, 截止 %s",
                total, deadline_dt.strftime("%H:%M"))

    while datetime.now(tz=_tz) < deadline_dt:
        attempt += 1
        elapsed = time.monotonic() - start_time
        intv, stage = _get_retry_interval(elapsed)

        try:
            prices = _fetch_prices_once(gw, codes, trade_date)
            if prices:
                best_prices.update(prices)
            cov = len(best_prices) / total
            if cov >= max(min_coverage, 1e-9):
                if attempt > 1:
                    logger.info(
                        "价格获取成功 (%s, #%d, %.1fmin), %d/%d",
                        stage, attempt, elapsed / 60,
                        len(best_prices), total)
                return best_prices
            _check_price_alert_escalation(
                elapsed, sent_levels, trade_date,
                total, len(best_prices), alert_fn)
            if attempt <= 3 or attempt % 5 == 0:
                logger.info(
                    "[%s] 覆盖 %d/%d (%.0f%%), #%d (%.0fs间隔)",
                    stage, len(best_prices), total,
                    cov * 100, attempt, intv)
        except Exception as exc:
            if attempt <= 3 or attempt % 10 == 0:
                logger.warning("价格获取异常 (#%d): %s", attempt, exc)
        time.sleep(intv)

    # ── 截止 — 逐只补查 ──
    elapsed = time.monotonic() - start_time
    if best_prices and len(best_prices) < total:
        missing = [c for c in codes if c not in best_prices]
        logger.info("截止前逐只补查 %d 只: %s",
                    len(missing), missing[:10])
        for code in missing:
            if datetime.now(tz=_tz) >= deadline_dt:
                break
            try:
                one = _fetch_prices_once(gw, [code], trade_date)
                if one:
                    best_prices.update(one)
            except Exception:
                logger.debug("single-code fetch failed for %s", code, exc_info=True)
            time.sleep(1.0)

    final_cov = len(best_prices) / total
    total_mins = int(elapsed / 60)

    if best_prices and final_cov >= max(min_coverage, 1e-9):
        logger.info("补查后达标: %d/%d (%.0f%%), %dmin",
                    len(best_prices), total,
                    final_cov * 100, total_mins)
        return best_prices

    if best_prices:
        logger.warning(
            "截止, 覆盖 %d/%d (%.0f%%) < 要求 %.0f%%, "
            "%dmin %d次. 返回已有数据.",
            len(best_prices), total, final_cov * 100,
            min_coverage * 100, total_mins, attempt)
        if alert_fn:
            alert_fn(
                "价格获取截止 — 覆盖率不足",
                f"交易日: {trade_date}, "
                f"覆盖率: {len(best_prices)}/{total}, "
                f"耗时: {total_mins}min")
        return best_prices

    if alert_fn:
        alert_fn(
            "价格获取彻底失败!",
            f"交易日: {trade_date}, {total} 标的 "
            f"{attempt} 次 ({total_mins}min) 无数据")
    raise RuntimeError(
        f"价格获取彻底失败: {total} 个标的经 {attempt} 次"
        f"({total_mins}min) 仍无数据")


def fetch_open_prices(
    gw: Any,
    codes: list[str],
    trade_date: str,
    *,
    max_retries: int = 3,
) -> dict[str, float]:
    """获取 A股+CB+ETF 的开盘价 — 用于 T+1 成交模拟.

    Paper 两阶段模式中, T+1 执行日需要用开盘价作为成交价.
    含重试机制 (§13.9): 至少 3 次重试, 间隔 2s.
    """
    prices: dict[str, float] = {}
    if not codes:
        return prices

    etf_codes = [c for c in codes if c.startswith("5")
                 or c.startswith("159")]
    cb_codes = [c for c in codes if is_cb_code(c)
                and c not in etf_codes]
    eq_codes = [c for c in codes
                if c not in etf_codes and c not in cb_codes]

    for attempt in range(1, max_retries + 1):
        try:
            if eq_codes and not all(c in prices for c in eq_codes):
                df = _get_equity_snapshot(trade_date)
                if df is not None and not df.empty:
                    sub = df[df["ts_code"].isin(eq_codes)]
                    for _, row in sub.iterrows():
                        op = row.get("open")
                        if op and float(op) > 0:
                            prices[row["ts_code"]] = float(op)

            if cb_codes and not all(c in prices for c in cb_codes):
                cb_df = _get_cb_snapshot(trade_date)
                if cb_df is not None and not cb_df.empty:
                    sub = cb_df[cb_df["ts_code"].isin(cb_codes)]
                    for _, row in sub.iterrows():
                        op = row.get("open")
                        if op and float(op) > 0:
                            prices[row["ts_code"]] = float(op)

            if etf_codes and not all(c in prices for c in etf_codes):
                etf_df = _get_etf_snapshot(trade_date)
                if etf_df is not None and not etf_df.empty:
                    sub = etf_df[etf_df["ts_code"].isin(etf_codes)]
                    for _, row in sub.iterrows():
                        op = row.get("open")
                        if op and float(op) > 0:
                            prices[row["ts_code"]] = float(op)
                # 降级: equity snapshot 补漏
                missing_etf = [c for c in etf_codes if c not in prices]
                if missing_etf:
                    eq_df = _get_equity_snapshot(trade_date)
                    if eq_df is not None and not eq_df.empty:
                        sub = eq_df[eq_df["ts_code"].isin(missing_etf)]
                        for _, row in sub.iterrows():
                            op = row.get("open")
                            if op and float(op) > 0:
                                prices[row["ts_code"]] = float(op)

            # 全部获取到 → 提前退出
            if len(prices) >= len(codes):
                break
            # 有部分数据且非异常 → 不再重试 (API 正常但标的确实无数据)
            if prices:
                break
        except Exception as exc:
            logger.warning("fetch_open_prices 第%d/%d次失败: %s",
                           attempt, max_retries, exc)
            if attempt < max_retries:
                import time
                time.sleep(2.0)

    missing = set(codes) - set(prices)
    if missing:
        logger.warning(
            "fetch_open_prices: %d/%d 标的缺少开盘价: %s",
            len(missing), len(codes), list(missing)[:10],
        )

    return prices


# =====================================================================
# Stock metadata — ST / list_status enrichment  (§13.15 Universe gap fix)
# =====================================================================
_stock_meta_cache: dict[str, dict[str, object]] = {}
_stock_meta_cache_date: str = ""

_ST_PREFIXES = ("*ST", "ST", "SST", "S*ST")


def fetch_stock_metadata(
    gw: Any,
    codes: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    """获取 A 股 ST/退市/停牌元数据 — Paper Bar 填充用.

    每日至多查询 TuShare stock_basic 一次（模块级缓存）。
    CB 标的自动标记 is_st=False。

    Returns:
        {ts_code: {"is_st": bool, "list_status": str, "name": str}}
    """
    global _stock_meta_cache, _stock_meta_cache_date  # noqa: PLW0603

    today = datetime.now().strftime("%Y%m%d")
    if _stock_meta_cache_date != today:
        _stock_meta_cache.clear()
        _stock_meta_cache_date = today

    # 如果缓存已有数据且所有 codes 已覆盖，直接返回
    if codes and _stock_meta_cache and all(c in _stock_meta_cache for c in codes):
        return {c: _stock_meta_cache[c] for c in codes}

    # 首次调用当日 → 拉取全量 stock_basic（~5000 行，一次缓存）
    if not _stock_meta_cache:
        _populate_stock_meta_cache(gw)

    if codes is None:
        return dict(_stock_meta_cache)
    result: dict[str, dict[str, object]] = {}
    for c in codes:
        if c in _stock_meta_cache:
            result[c] = _stock_meta_cache[c]
        else:
            # CB / ETF / 未知标的 → 安全默认值
            result[c] = {"is_st": False, "list_status": "L", "name": ""}
    return result


def _populate_stock_meta_cache(gw: Any) -> None:
    """从 TuShare stock_basic 填充模块级缓存。"""
    global _stock_meta_cache  # noqa: PLW0603
    fields = "ts_code,name,list_status"
    for status in ("L", "D", "P"):
        try:
            df = gw.call("stock_basic", list_status=status, fields=fields)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                code = str(row.get("ts_code", ""))
                name = str(row.get("name", ""))
                ls = str(row.get("list_status", status))
                _stock_meta_cache[code] = {
                    "is_st": name.startswith(_ST_PREFIXES),
                    "list_status": ls,
                    "name": name,
                }
        except Exception:
            logger.warning(
                "stock_basic(list_status=%s) 查询失败, "
                "部分 ST/退市检查将使用默认值", status, exc_info=True,
            )


def fetch_prev_closes(
    gw: Any,
    codes: list[str],
    trade_date: str,
) -> dict[str, float]:
    """获取前一日收盘价（涨跌停检查用）.

    利用 TuShare daily/cb_daily API 返回的 pre_close 字段。
    3 次重试 + 失败降级: 异常时不返回空 dict (会导致全面跳过),
    而是返回已获取的部分数据, 让涨跌停检查在有数据时生效。
    """
    prev_closes: dict[str, float] = {}
    if not codes:
        return prev_closes

    # 分类: ETF / CB / Equity (与 _fetch_prices_once 保持一致)
    etf_codes = [c for c in codes if c.startswith("5")
                 or c.startswith("159")]
    cb_codes = [c for c in codes if is_cb_code(c)
                and c not in etf_codes]
    eq_codes = [c for c in codes
                if c not in etf_codes and c not in cb_codes]

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            if eq_codes and not all(c in prev_closes for c in eq_codes):
                df = _get_equity_snapshot(trade_date)
                if df is not None and not df.empty and "pre_close" in df.columns:
                    sub = df[df["ts_code"].isin(eq_codes)]
                    for _, row in sub.iterrows():
                        pc = row.get("pre_close")
                        if pc and float(pc) > 0:
                            prev_closes[row["ts_code"]] = float(pc)
                else:
                    # 降级: Provider 不可用时回退到 gw.call
                    try:
                        df = gw.call("daily", trade_date=trade_date,
                                     fields="ts_code,pre_close")
                        if df is not None and not df.empty and "pre_close" in df.columns:
                            sub = df[df["ts_code"].isin(eq_codes)]
                            for _, row in sub.iterrows():
                                pc = row.get("pre_close")
                                if pc and float(pc) > 0:
                                    prev_closes[row["ts_code"]] = float(pc)
                    except Exception:
                        logger.debug("equity pre_close fetch failed", exc_info=True)

            if cb_codes and not all(c in prev_closes for c in cb_codes):
                cb_df = _get_cb_snapshot(trade_date)
                if cb_df is not None and not cb_df.empty and "pre_close" in cb_df.columns:
                    sub = cb_df[cb_df["ts_code"].isin(cb_codes)]
                    for _, row in sub.iterrows():
                        pc = row.get("pre_close")
                        if pc and float(pc) > 0:
                            prev_closes[row["ts_code"]] = float(pc)

            # ETF: 通过 fund_daily 快照获取 pre_close, 降级到 equity
            if etf_codes and not all(c in prev_closes for c in etf_codes):
                etf_df = _get_etf_snapshot(trade_date)
                if etf_df is not None and not etf_df.empty and "pre_close" in etf_df.columns:
                    sub = etf_df[etf_df["ts_code"].isin(etf_codes)]
                    for _, row in sub.iterrows():
                        pc = row.get("pre_close")
                        if pc and float(pc) > 0:
                            prev_closes[row["ts_code"]] = float(pc)
                else:
                    # 降级: Provider 不可用时回退到 gw.call
                    for c in etf_codes:
                        if c in prev_closes:
                            continue
                        try:
                            edf = gw.call("fund_daily", ts_code=c,
                                          trade_date=trade_date,
                                          fields="ts_code,pre_close")
                            if edf is not None and not edf.empty and "pre_close" in edf.columns:
                                pc = edf["pre_close"].iloc[0]
                                if pc and float(pc) > 0:
                                    prev_closes[c] = float(pc)
                        except Exception:
                            logger.debug("ETF pre_close fetch failed for %s", c, exc_info=True)
                # 降级: equity snapshot 补漏
                missing_etf = [c for c in etf_codes if c not in prev_closes]
                if missing_etf:
                    eq_df = _get_equity_snapshot(trade_date)
                    if eq_df is not None and not eq_df.empty and "pre_close" in eq_df.columns:
                        sub = eq_df[eq_df["ts_code"].isin(missing_etf)]
                        for _, row in sub.iterrows():
                            pc = row.get("pre_close")
                            if pc and float(pc) > 0:
                                prev_closes[row["ts_code"]] = float(pc)

            # 获取到全部数据, 提前退出
            if len(prev_closes) >= len(codes):
                break
            # 获取到部分数据, 不再重试 (API 正常但某些标的确实没有)
            if prev_closes:
                break
        except Exception as exc:
            logger.warning("fetch_prev_closes 第%d/%d次失败: %s",
                           attempt, max_retries, exc)
            if attempt < max_retries:
                import time
                time.sleep(2.0)

    # §R140: 部分数据缺失时记录 warning
    missing = set(codes) - set(prev_closes)
    if missing:
        logger.warning(
            "fetch_prev_closes: %d/%d 标的缺少 pre_close — "
            "这些标的将被阻断交易: %s",
            len(missing), len(codes),
            list(missing)[:10],  # 最多显示 10 个
        )

    return prev_closes


# =====================================================================
# MA5 Fetch — §14.1 Fat-finger Layer ② 所需的 5日均价
# =====================================================================
def fetch_ma5_prices(
    gw: Any,
    codes: list[str],
    trade_date: str,
) -> dict[str, float]:
    """获取标的近5个交易日收盘均价 (§14.1 Layer ② fat-finger 保护).

    使用交易日历获取前5个交易日，逐日从缓存快照中取 close，
    对每只标的计算可用日的平均值。缓存命中时零 API 开销。

    Args:
        gw: TuShareGateway 实例（用于查询交易日历）
        codes: 标的代码列表
        trade_date: 当前交易日 YYYYMMDD

    Returns:
        {ts_code: ma5_price} — 仅包含有足够数据的标的（≥3日）
    """
    if not codes:
        return {}

    # Step 1: 获取前5个交易日（不含当日）
    try:
        from datetime import timedelta as td_delta
        end = trade_date
        start = (datetime.strptime(trade_date, "%Y%m%d") - td_delta(days=15)).strftime("%Y%m%d")
        cal_df = gw.call("trade_cal", exchange="SSE",
                         start_date=start, end_date=end, is_open="1")
        if cal_df is None or cal_df.empty:
            logger.warning("MA5: 交易日历为空, 跳过 Layer ② 检查")
            return {}
        all_dates = sorted(cal_df["cal_date"].tolist())
        # 排除当日，取最近5个
        prev_dates = [d for d in all_dates if d < trade_date][-5:]
    except Exception as exc:
        logger.warning("MA5: 交易日历获取失败: %s", exc)
        return {}

    if len(prev_dates) < 3:
        logger.warning("MA5: 可用交易日不足 (%d < 3), 跳过", len(prev_dates))
        return {}

    # Step 2: 逐日收集 close 价格
    cb_codes_set = {c for c in codes if is_cb_code(c)}
    eq_codes_set = set(codes) - cb_codes_set

    # {code: [close1, close2, ...]}
    price_history: dict[str, list[float]] = {c: [] for c in codes}

    for d in prev_dates:
        # Equity snapshot
        if eq_codes_set:
            eq_df = _get_equity_snapshot(d)
            if eq_df is not None and not eq_df.empty and "close" in eq_df.columns:
                sub = eq_df[eq_df["ts_code"].isin(eq_codes_set)]
                for _, row in sub.iterrows():
                    close = row.get("close")
                    if close and float(close) > 0:
                        price_history[row["ts_code"]].append(float(close))
        # CB snapshot
        if cb_codes_set:
            cb_df = _get_cb_snapshot(d)
            if cb_df is not None and not cb_df.empty and "close" in cb_df.columns:
                sub = cb_df[cb_df["ts_code"].isin(cb_codes_set)]
                for _, row in sub.iterrows():
                    close = row.get("close")
                    if close and float(close) > 0:
                        price_history[row["ts_code"]].append(float(close))

    # Step 3: 计算均值（至少3日数据才纳入）
    ma5: dict[str, float] = {}
    for code, closes in price_history.items():
        if len(closes) >= 3:
            ma5[code] = sum(closes) / len(closes)

    logger.debug("MA5: %d/%d 标的有效 (from %d days)", len(ma5), len(codes), len(prev_dates))
    return ma5


# =====================================================================
# Volume Fetch — 成交量获取 (§13.15 / Phase R2c)
# =====================================================================
def fetch_volumes(
    gw: Any,
    codes: list[str],
    trade_date: str,
) -> dict[str, float]:
    """获取标的当日成交量 (ADR-V2-7).

    Returns:
        dict mapping ts_code → volume (股/张)。缺失标的不在结果中。
    """
    volumes: dict[str, float] = {}
    if not codes:
        return volumes

    cb_codes = [c for c in codes if is_cb_code(c)]
    eq_codes = [c for c in codes if c not in cb_codes]

    try:
        if eq_codes:
            df = _get_equity_snapshot(trade_date)
            if df is not None and not df.empty and "vol" in df.columns:
                sub = df[df["ts_code"].isin(eq_codes)]
                for _, row in sub.iterrows():
                    v = row.get("vol")
                    if v is not None and float(v) >= 0:
                        # TuShare daily vol 单位: 手 → 转股
                        volumes[row["ts_code"]] = float(v) * 100
        if cb_codes:
            cb_df = _get_cb_snapshot(trade_date)
            if cb_df is not None and not cb_df.empty and "vol" in cb_df.columns:
                sub = cb_df[cb_df["ts_code"].isin(cb_codes)]
                for _, row in sub.iterrows():
                    v = row.get("vol")
                    if v is not None and float(v) >= 0:
                        # CB vol 单位: 手 → 张
                        volumes[row["ts_code"]] = float(v) * 10
    except Exception as exc:
        logger.warning("fetch_volumes 失败: %s", exc)

    return volumes


def build_market_state(
    gw: Any,
    codes: list[str],
    trade_date: str,
    prices: dict[str, float],
    prev_closes: dict[str, float],
) -> dict[str, dict]:
    """构建合规检查用的 market_state (ADR-V2-7).

    替代各处硬编码的 ``{is_halted: False, volume: 0}``。
    停牌启发式: volume == 0 且 |close - prev_close| < 0.001。

    Returns:
        dict mapping ts_code → {is_halted, is_limit_up, is_limit_down, volume}
    """
    volumes = fetch_volumes(gw, codes, trade_date)
    result: dict[str, dict] = {}
    for code in codes:
        vol = volumes.get(code, 0.0)
        px = prices.get(code, 0.0)
        pc = prev_closes.get(code, 0.0)
        is_halted = (vol == 0.0 and pc > 0 and abs(px - pc) < 0.001)
        # 涨跌停判断: CB ±20% (19.5%阈值), A股 ±10% (9.5%阈值)
        is_cb = is_cb_code(code)
        limit_pct = 0.195 if is_cb else 0.095
        if pc > 0:
            chg = (px - pc) / pc
            is_limit_up = chg >= limit_pct
            is_limit_down = chg <= -limit_pct
        else:
            is_limit_up = False
            is_limit_down = False
        result[code] = {
            "is_halted": is_halted,
            "is_limit_up": is_limit_up,
            "is_limit_down": is_limit_down,
            "volume": vol,
        }
    return result


# =====================================================================
# Rebalance Frequency — 调仓频率判断 (§13.10)
# =====================================================================

# 自然日→交易日换算系数 (5 trading days per 7 calendar days)
_NATURAL_TO_TRADING_RATIO = 7.0 / 5.0


def _parse_date_str(s: str) -> datetime:
    """解析日期字符串 YYYYMMDD 或 YYYY-MM-DD."""
    s = s.replace("-", "")
    return datetime.strptime(s, "%Y%m%d")


def get_rebalance_interval_trading_days(frequency: str) -> int:
    """返回给定频率的调仓间隔（单位：交易日）.

    Args:
        frequency: ``"daily"`` | ``"weekly"`` | ``"monthly"``
            | ``"every_{N}d"`` (N 为交易日数)

    Returns:
        交易日间隔数。``"daily"`` 返回 1，``"monthly"`` 返回 0（特殊处理）。
    """
    if frequency == "daily":
        return 1
    if frequency == "weekly":
        return 5
    m = re.match(r"every_(\d+)d$", frequency)
    if m:
        return int(m.group(1))
    # 未知频率视为 weekly
    logger.warning("未知调仓频率 %r, 按 weekly(5) 处理", frequency)
    return 5


def should_rebalance(
    frequency: str,
    current_date: str,
    last_rebalance_date: str | None = None,
) -> bool:
    """判断今天是否为调仓日 (§13.10 统一语义).

    使用自然日差值乘以换算系数近似交易日数。
    ``"monthly"`` 使用月份变化判断。

    Args:
        frequency: ``"daily"`` | ``"weekly"`` | ``"monthly"``
            | ``"every_{N}d"``
        current_date: 当前日期 (YYYYMMDD 或 YYYY-MM-DD)
        last_rebalance_date: 上次调仓日期，None 表示首次运行（总是调仓）
    """
    if last_rebalance_date is None:
        return True
    if frequency == "daily":
        return True

    cur = _parse_date_str(current_date)
    last = _parse_date_str(last_rebalance_date)

    if frequency == "monthly":
        return cur.month != last.month or cur.year != last.year

    # 用自然日近似交易日:
    # N 个交易日 ≈ ceil(N * 7/5) 个自然日
    interval_td = get_rebalance_interval_trading_days(frequency)
    threshold_natural = math.ceil(interval_td * _NATURAL_TO_TRADING_RATIO)
    natural_days = (cur - last).days
    return natural_days >= threshold_natural


# =====================================================================
# Data Preflight — 数据预检 (§13.9)
# =====================================================================

#: 默认允许的最大数据滞后交易日数
DATA_MAX_STALE_TRADING_DAYS: int = 3
#: 默认允许的最大数据滞后自然日数 (假期容错)
DATA_MAX_STALE_CALENDAR_DAYS: int = 10
#: 默认缓存文件最大过期天数
CACHE_MAX_STALE_DAYS: int = 3


def _check_cache_freshness(
    cache_dir: "Path",
    patterns: tuple[str, ...] = ("*.parquet",),
    max_stale_days: int = CACHE_MAX_STALE_DAYS,
) -> tuple[bool, str]:
    """检查本地缓存文件新鲜度 (§13.9).

    Args:
        cache_dir: 缓存目录
        patterns: glob 模式
        max_stale_days: 最大允许过期天数

    Returns:
        (ok, message): ok=True 表示缓存足够新鲜
    """
    from datetime import date as date_cls
    from pathlib import Path as _Path

    cache_dir = _Path(cache_dir)
    if not cache_dir.exists():
        return False, f"缓存目录不存在: {cache_dir}"

    latest_mtime: float = 0.0
    for pat in patterns:
        for f in cache_dir.rglob(pat):
            mtime = f.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime

    if latest_mtime == 0.0:
        return False, f"缓存目录无匹配文件: {cache_dir} patterns={patterns}"

    from datetime import datetime as dt_cls
    file_date = dt_cls.fromtimestamp(latest_mtime).date()
    stale_days = (date_cls.today() - file_date).days
    if stale_days > max_stale_days:
        return False, (
            f"缓存数据滞后 {stale_days} 天 (最大允许 {max_stale_days}): "
            f"最新文件日期 {file_date}"
        )
    return True, f"缓存新鲜 (最新文件 {stale_days} 天前)"


def data_preflight(
    trade_date: str,
    *,
    max_stale_trading_days: int = DATA_MAX_STALE_TRADING_DAYS,
    max_stale_calendar_days: int = DATA_MAX_STALE_CALENDAR_DAYS,
    check_cache: bool = False,
    cache_max_stale_days: int = CACHE_MAX_STALE_DAYS,
) -> None:
    """检查交易日期新鲜度，过期则抛异常 (§13.9 预检).

    Args:
        trade_date: 交易日期 YYYYMMDD
        max_stale_trading_days: 允许的最大交易日滞后
        max_stale_calendar_days: 允许的最大自然日滞后 (日历不可用时降级)
        check_cache: 是否检查缓存文件新鲜度
        cache_max_stale_days: 缓存文件最大过期天数

    Raises:
        RuntimeError: 数据滞后超过阈值
    """
    from datetime import date as date_cls

    td = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date()
    cal_days = (date_cls.today() - td).days

    stale_tdays = cal_days  # 默认：降级到自然日
    try:
        from aipha.data.calendar import CNStockCalendar
        cal = CNStockCalendar()
        tdays = cal.get_trading_days(td, date_cls.today())
        stale_tdays = max(0, len(tdays) - 1)
    except Exception:
        logger.debug("CNStockCalendar unavailable, using calendar days", exc_info=True)

    if stale_tdays > max_stale_trading_days:
        raise RuntimeError(
            f"trade_date {trade_date} 滞后 {stale_tdays} 交易日 "
            f"({cal_days} 自然日), 超过阈值 {max_stale_trading_days}, "
            f"数据可能过期"
        )

    # §13.9 缓存文件新鲜度检查
    if check_cache:
        from pathlib import Path as _Path
        cache_dir = _Path(__file__).resolve().parents[3] / "data" / "cache"
        ok, msg = _check_cache_freshness(
            cache_dir, patterns=("*.parquet",), max_stale_days=cache_max_stale_days
        )
        if not ok:
            logger.error("缓存新鲜度检查失败: %s", msg)
            raise RuntimeError(f"缓存新鲜度检查失败: {msg}")


# =====================================================================
# Trading Day Detection — 交易日检测 (§13.10)
# =====================================================================

#: 数据可用性最小行情条目数（daily_basic 返回 >100 条视为有效）
DATA_AVAILABILITY_MIN_ROWS: int = 100


def get_latest_trade_date(
    gw: Any,
    *,
    verify_data: bool = True,
) -> str:
    """获取最新可用数据的交易日 (§13.10).

    基于 R115 增强版：从交易日历倒序遍历，校验实际行情数据是否已入库。

    Args:
        gw: TuShareGateway 实例
        verify_data: 是否验证数据可用性（默认 True）。
            False 时仅查日历，不调 daily_basic（速度更快但可能返回未入库日期）。

    Returns:
        最新交易日 YYYYMMDD 字符串
    """
    from datetime import timedelta as td_delta

    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - td_delta(days=10)).strftime("%Y%m%d")
    df = gw.call(
        "trade_cal", exchange="SSE",
        start_date=start, end_date=today, is_open="1",
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"交易日历为空，无法确定最新交易日: start={start}, end={today}"
        )
    dates = sorted(df["cal_date"].tolist(), reverse=True)
    if not dates:
        raise RuntimeError(
            f"交易日历缺少 cal_date，无法确定最新交易日: start={start}, end={today}"
        )

    if not verify_data:
        return dates[0]

    # 从最新交易日开始，找到实际有数据的日期
    for d in dates:
        try:
            db = gw.call("daily_basic", trade_date=d,
                         fields="ts_code,close")
            if db is not None and len(db) > DATA_AVAILABILITY_MIN_ROWS:
                if d != dates[0]:
                    logger.info(
                        "数据可用日 %s (日历最新 %s)", d, dates[0])
                return d
        except Exception as exc:
            logger.warning("验证交易日 %s 行情可用性失败: %s", d, exc)

    raise RuntimeError(
        f"最近 {len(dates)} 个交易日均未通过行情可用性校验，禁止回退到 today={today}"
    )


# =====================================================================
# Weight Drift Detection (Phase 1 — 专家评审 2026-04-21)
# =====================================================================

@dataclass
class DriftLevel:
    """单个组件的权重漂移信息."""
    component: str
    target_pct: float       # 目标权重 (0-1)
    actual_pct: float       # 实际权重 (0-1)
    drift_pp: float         # 漂移百分点 (actual - target) × 100
    abs_drift_pp: float     # 绝对漂移百分点
    severity: str           # green / yellow / orange / red


@dataclass
class DriftReport:
    """组合权重漂移检测报告."""
    trade_date: str
    nav: float
    levels: list[DriftLevel]
    max_abs_drift_pp: float
    any_alert: bool         # 是否有 >= yellow 的漂移

    def summary_text(self) -> str:
        """生成人类可读的漂移摘要."""
        lines = [f"权重漂移检测 ({self.trade_date})  NAV={self.nav:,.0f}"]
        for lv in self.levels:
            icon = {"green": "✅", "yellow": "⚠️",
                    "orange": "🟠", "red": "🔴"}.get(lv.severity, "❓")
            lines.append(
                f"  {icon} {lv.component}: "
                f"target={lv.target_pct:.1%} actual={lv.actual_pct:.1%} "
                f"drift={lv.drift_pp:+.1f}pp"
            )
        return "\n".join(lines)


def check_weight_drift(
    positions: list[dict[str, Any]],
    cash: float,
    prices: dict[str, float],
    target_weights: dict[str, float],
    trade_date: str,
    *,
    warn_pp: float = 5.0,
    alert_pp: float = 8.0,
    critical_pp: float = 10.0,
) -> DriftReport:
    """检测组合各组件的权重漂移.

    Args:
        positions: 当前持仓 [{code, shares, avg_cost, component, ...}]
        cash: 当前现金
        prices: {code: price} 当日价格
        target_weights: {component: weight} 目标权重 (0-1)
        trade_date: 交易日期
        warn_pp: 黄色告警阈值 (百分点, 默认 5)
        alert_pp: 橙色告警阈值 (百分点, 默认 8)
        critical_pp: 红色告警阈值 (百分点, 默认 10)

    Returns:
        DriftReport 含各组件漂移状态
    """
    comp_value: dict[str, float] = {}
    for p in positions:
        comp = p.get("component", "unknown")
        code = p.get("code", "")
        shares = p.get("shares", 0)
        px = prices.get(code, p.get("avg_cost", 0))
        comp_value[comp] = comp_value.get(comp, 0.0) + shares * px

    total = sum(comp_value.values()) + cash
    if total <= 0:
        return DriftReport(
            trade_date=trade_date, nav=0, levels=[],
            max_abs_drift_pp=0, any_alert=False,
        )

    levels: list[DriftLevel] = []
    max_abs = 0.0
    any_alert = False

    for comp, target_w in sorted(target_weights.items()):
        actual_val = comp_value.get(comp, 0.0)
        actual_w = actual_val / total
        drift_pp = (actual_w - target_w) * 100
        abs_pp = abs(drift_pp)
        max_abs = max(max_abs, abs_pp)

        if abs_pp >= critical_pp:
            severity = "red"
        elif abs_pp >= alert_pp:
            severity = "orange"
        elif abs_pp >= warn_pp:
            severity = "yellow"
        else:
            severity = "green"

        if severity != "green":
            any_alert = True

        levels.append(DriftLevel(
            component=comp,
            target_pct=target_w,
            actual_pct=actual_w,
            drift_pp=round(drift_pp, 2),
            abs_drift_pp=round(abs_pp, 2),
            severity=severity,
        ))

    return DriftReport(
        trade_date=trade_date,
        nav=round(total, 2),
        levels=levels,
        max_abs_drift_pp=round(max_abs, 2),
        any_alert=any_alert,
    )


# ---------------------------------------------------------------------------
# §13.2 共享因子计算 — 12-1月动量
# ---------------------------------------------------------------------------

def calc_momentum_12_1(
    gw: Any,
    codes: list[str],
    trade_date: str,
) -> Any:
    """计算 12-1 月动量因子 (skip recent 1 month).

    从 TuShare Gateway 获取 12 个月前和 1 个月前的收盘价,
    计算 (P1/P12 - 1) 动量. 带日期回退(最多 5 天)以应对非交易日.

    Returns:
        numpy array, 与 codes 对齐, 缺失值填 0.
    """
    import pandas as pd
    from datetime import timedelta

    td = pd.Timestamp(trade_date)
    d12 = (td - pd.DateOffset(months=12)).strftime("%Y%m%d")
    d1 = (td - pd.DateOffset(months=1)).strftime("%Y%m%d")

    df12 = _get_equity_snapshot(d12)
    df1 = _get_equity_snapshot(d1)

    for offset in range(1, 6):
        if df12 is None or df12.empty:
            d = (td - pd.DateOffset(months=12) - timedelta(days=offset)).strftime("%Y%m%d")
            df12 = _get_equity_snapshot(d)
        if df1 is None or df1.empty:
            d = (td - pd.DateOffset(months=1) - timedelta(days=offset)).strftime("%Y%m%d")
            df1 = _get_equity_snapshot(d)

    if df12 is None or df1 is None or df12.empty or df1.empty:
        return pd.Series(0.0, index=range(len(codes))).values

    p12 = df12.set_index("ts_code")["close"]
    p1 = df1.set_index("ts_code")["close"]
    mom = (p1 / p12 - 1).reindex(codes).fillna(0)
    return mom.values
