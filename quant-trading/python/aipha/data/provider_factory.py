"""数据 Provider 工厂 — 统一数据路由

根据标的代码自动检测市场类型，并通过级联回退链路由到合适的数据源。

市场检测规则:
  - 6 位纯数字 (600xxx/000xxx/300xxx): cn_stock (A 股)
  - 常见指数代码 (000300/399001): index
  - 纯字母 (AAPL/GOOGL): us_stock
  - .HK 后缀: hk_stock
  - 12xxxx.SH (CB): convertible_bond

级联回退:
  A 股:  XTData → TuShare → AKShare
  CB:    TuShare (CB Provider) → AKShare (CB)
  指数:  AKShare → TuShare

使用示例::

    from aipha.data.provider_factory import detect_market, ProviderFactory

    market = detect_market("600519")  # -> "cn_stock"
    factory = ProviderFactory()
    bars = factory.load_bars(symbol="600519", ...)
"""

from __future__ import annotations

import enum
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 市场类型枚举
# ============================================================================


class MarketType(enum.Enum):
    """市场类型"""

    CN_STOCK = "cn_stock"
    CONVERTIBLE_BOND = "convertible_bond"
    INDEX = "index"
    ETF = "etf"
    US_STOCK = "us_stock"
    HK_STOCK = "hk_stock"
    UNKNOWN = "unknown"


# ============================================================================
# 常见指数代码集合
# ============================================================================

_INDEX_CODES = frozenset({
    "000001", "000016", "000300", "000510", "000688", "000852",
    "000905", "000906", "399001", "399005", "399006", "399673",
    "399303", "399330", "880823",
})

# CB 可转债/可交换债代码前缀 — 静态集合 + 动态自加载
# 来源: TuShare cb_basic 全量统计 (2026-04-30 确认)
# 沪: 100/110/111/113/117/118/132, 深: 120/121/123/124/125/126/127/128/129/145
_CB_PREFIX_3_STATIC = frozenset({
    "100", "110", "111", "113", "117", "118",
    "120", "121", "123", "124", "125", "126",
    "127", "128", "129", "132", "145",
})

# 动态 CB 前缀集 (首次使用时从 cb_basic 缓存加载, 与静态取 UNION)
_cb_prefix_3_dynamic: frozenset[str] | None = None


def _get_cb_prefix_set() -> frozenset[str]:
    """获取 CB 前缀集 — 优先从 cb_basic 缓存加载, 始终包含静态 fallback."""
    global _cb_prefix_3_dynamic
    if _cb_prefix_3_dynamic is not None:
        return _cb_prefix_3_dynamic
    try:
        from aipha.data.tushare_cb_provider import get_cb_provider
        provider = get_cb_provider()
        basic_df = provider.load_cb_basic(use_cache=True)
        if basic_df is not None and not basic_df.empty:
            dynamic = frozenset(
                c[:3] for c in basic_df["ts_code"].dropna()
                if len(c) >= 3
            )
            _cb_prefix_3_dynamic = dynamic | _CB_PREFIX_3_STATIC
            return _cb_prefix_3_dynamic
    except Exception:
        pass
    _cb_prefix_3_dynamic = _CB_PREFIX_3_STATIC
    return _cb_prefix_3_dynamic

# ETF 代码前缀 (沪: 510/512/513/515/518, 深: 159)
_ETF_PREFIX_RE = re.compile(
    r"^(510|512|513|515|516|517|518|159)\d{3}$"
)


def detect_market(symbol: str) -> MarketType:
    """根据标的代码格式自动检测市场类型。

    Args:
        symbol: 标的代码，支持纯数字 (600519)、带后缀 (600519.SH)、
                纯字母 (AAPL) 等格式。

    Returns:
        MarketType 枚举值
    """
    sym = symbol.strip().upper()

    # 去掉可能的后缀先做纯数字检测
    bare = sym.split(".")[0] if "." in sym else sym

    # 带明确后缀
    if sym.endswith(".HK"):
        return MarketType.HK_STOCK

    # 6 位纯数字分支
    if bare.isdigit() and len(bare) == 6:
        if bare in _INDEX_CODES:
            return MarketType.INDEX
        if bare[:3] in _get_cb_prefix_set():
            return MarketType.CONVERTIBLE_BOND
        if _ETF_PREFIX_RE.match(bare):
            return MarketType.ETF
        return MarketType.CN_STOCK

    # 纯字母 → 美股
    if bare.isalpha():
        return MarketType.US_STOCK

    return MarketType.UNKNOWN


def normalize_to_qmt(code: str) -> str:
    """将 6 位代码标准化为 QMT 格式 (如 600519 -> 600519.SH)。"""
    code = code.strip().split(".")[0]
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8", "9")):
        return f"{code}.BJ"
    return f"{code}.SH"


def normalize_to_tushare(code: str) -> str:
    """将 6 位代码标准化为 TuShare 格式 (如 600519 -> 600519.SH)。"""
    # TuShare 格式与 QMT 相同
    return normalize_to_qmt(code)
