"""
涨跌停与停牌检查器

实现A股市场的涨跌停和停牌检测：
- 涨停/跌停判断（基于前收盘价计算上下限）
- 停牌状态检测
- ST/风险警示股票识别（涨跌幅限制5%）
- 科创板/创业板特殊规则（涨跌幅限制20%）

核心概念：
- limit_up_price: 涨停价 = 前收盘价 * (1 + 涨幅限制)
- limit_down_price: 跌停价 = 前收盘价 * (1 - 跌幅限制)
- 停牌状态：整日停牌、盘中临时停牌
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from enum import Enum
from typing import Optional


class HaltStatus(Enum):
    """停牌状态"""

    TRADING = "trading"  # 正常交易
    HALTED = "halted"  # 停牌
    SUSPENDED = "suspended"  # 临时停牌（盘中）
    DELISTING = "delisting"  # 退市整理期


class LimitStatus(Enum):
    """涨跌停状态"""

    NORMAL = "normal"  # 正常
    LIMIT_UP = "limit_up"  # 涨停
    LIMIT_DOWN = "limit_down"  # 跌停
    APPROACHING_UP = "approaching_up"  # 接近涨停（<1%）
    APPROACHING_DOWN = "approaching_down"  # 接近跌停（<1%）


class BoardType(Enum):
    """板块类型（决定涨跌幅限制）"""

    MAIN = "main"  # 主板（沪深）- 10%
    SME = "sme"  # 中小板 - 10%
    GEM = "gem"  # 创业板 - 20%
    STAR = "star"  # 科创板 - 20%
    BSE = "bse"  # 北交所 - 30%
    ST = "st"  # ST股票 - 5%
    NEW_LISTING = "new_listing"  # 新股上市首日 - 无限制


@dataclass
class MarketStatus:
    """市场状态快照"""

    symbol: str
    timestamp: datetime
    halt_status: HaltStatus
    limit_status: LimitStatus
    current_price: Optional[Decimal]
    prev_close: Optional[Decimal]
    limit_up_price: Optional[Decimal]
    limit_down_price: Optional[Decimal]
    board_type: BoardType
    is_st: bool = False
    halt_reason: str = ""

    @property
    def can_buy(self) -> bool:
        """是否可买入"""
        if self.halt_status != HaltStatus.TRADING:
            return False
        if self.limit_status == LimitStatus.LIMIT_UP:
            return False  # 涨停不可买入（封单）
        return True

    @property
    def can_sell(self) -> bool:
        """是否可卖出"""
        if self.halt_status != HaltStatus.TRADING:
            return False
        if self.limit_status == LimitStatus.LIMIT_DOWN:
            return False  # 跌停不可卖出（封单）
        return True


class HaltChecker:
    """
    涨跌停与停牌检查器

    负责检测股票的涨跌停状态和停牌状态，
    并根据板块类型计算涨跌停价格。
    """

    # 涨跌幅限制配置
    LIMIT_CONFIG = {
        BoardType.MAIN: Decimal("0.10"),  # 主板 10%
        BoardType.SME: Decimal("0.10"),  # 中小板 10%
        BoardType.GEM: Decimal("0.20"),  # 创业板 20%
        BoardType.STAR: Decimal("0.20"),  # 科创板 20%
        BoardType.BSE: Decimal("0.30"),  # 北交所 30%
        BoardType.ST: Decimal("0.05"),  # ST 5%
        BoardType.NEW_LISTING: Decimal("10.0"),  # 新股无限制（设为1000%）
    }

    # 接近涨跌停阈值
    APPROACHING_THRESHOLD = Decimal("0.01")  # 1%

    def __init__(self):
        """初始化检查器"""
        pass

    def get_board_type(self, symbol: str, is_st: bool = False) -> BoardType:
        """
        根据股票代码判断板块类型

        Args:
            symbol: 股票代码（如 000001.SZ, 688001.SH, 300001.SZ）
            is_st: 是否为ST股票

        Returns:
            板块类型
        """
        if is_st:
            return BoardType.ST

        code = symbol.split(".")[0]

        # 科创板：688xxx
        if code.startswith("688"):
            return BoardType.STAR

        # 创业板：300xxx, 301xxx
        if code.startswith("30"):
            return BoardType.GEM

        # 北交所：8xxxxx, 43xxxx, 87xxxx
        if code.startswith(("8", "43", "87")):
            return BoardType.BSE

        # 主板：其他
        return BoardType.MAIN

    def calculate_limit_prices(
        self,
        prev_close: Decimal,
        board_type: BoardType,
    ) -> tuple[Decimal, Decimal]:
        """
        计算涨跌停价格

        Args:
            prev_close: 前收盘价
            board_type: 板块类型

        Returns:
            (涨停价, 跌停价)
        """
        limit_pct = self.LIMIT_CONFIG[board_type]

        # 涨停价 = 前收盘价 * (1 + 涨幅限制)，四舍五入到分
        limit_up = (prev_close * (1 + limit_pct)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )

        # 跌停价 = 前收盘价 * (1 - 跌幅限制)，四舍五入到分
        limit_down = (prev_close * (1 - limit_pct)).quantize(
            Decimal("0.01"), rounding=ROUND_UP
        )

        return limit_up, limit_down

    def check_limit_status(
        self,
        current_price: Decimal,
        prev_close: Decimal,
        board_type: BoardType,
    ) -> LimitStatus:
        """
        检查涨跌停状态

        Args:
            current_price: 当前价格
            prev_close: 前收盘价
            board_type: 板块类型

        Returns:
            涨跌停状态
        """
        limit_up, limit_down = self.calculate_limit_prices(prev_close, board_type)

        # 涨停判断
        if current_price >= limit_up:
            return LimitStatus.LIMIT_UP

        # 跌停判断
        if current_price <= limit_down:
            return LimitStatus.LIMIT_DOWN

        # 接近涨停判断
        up_distance = (limit_up - current_price) / prev_close
        if up_distance <= self.APPROACHING_THRESHOLD:
            return LimitStatus.APPROACHING_UP

        # 接近跌停判断
        down_distance = (current_price - limit_down) / prev_close
        if down_distance <= self.APPROACHING_THRESHOLD:
            return LimitStatus.APPROACHING_DOWN

        return LimitStatus.NORMAL

    def check_halt_status(
        self,
        is_halted: bool = False,
        is_suspended: bool = False,
        is_delisting: bool = False,
    ) -> HaltStatus:
        """
        检查停牌状态

        Args:
            is_halted: 是否停牌
            is_suspended: 是否临时停牌
            is_delisting: 是否退市整理

        Returns:
            停牌状态
        """
        if is_delisting:
            return HaltStatus.DELISTING
        if is_halted:
            return HaltStatus.HALTED
        if is_suspended:
            return HaltStatus.SUSPENDED
        return HaltStatus.TRADING

    def get_market_status(
        self,
        symbol: str,
        current_price: Optional[Decimal],
        prev_close: Optional[Decimal],
        timestamp: Optional[datetime] = None,
        is_st: bool = False,
        is_halted: bool = False,
        is_suspended: bool = False,
        is_delisting: bool = False,
        halt_reason: str = "",
    ) -> MarketStatus:
        """
        获取完整的市场状态

        Args:
            symbol: 股票代码
            current_price: 当前价格
            prev_close: 前收盘价
            timestamp: 时间戳
            is_st: 是否ST股票
            is_halted: 是否停牌
            is_suspended: 是否临时停牌
            is_delisting: 是否退市整理
            halt_reason: 停牌原因

        Returns:
            市场状态快照
        """
        if timestamp is None:
            timestamp = datetime.now()

        board_type = self.get_board_type(symbol, is_st)
        halt_status = self.check_halt_status(is_halted, is_suspended, is_delisting)

        # 计算涨跌停价格
        limit_up_price = None
        limit_down_price = None
        limit_status = LimitStatus.NORMAL

        if prev_close is not None and prev_close > 0:
            limit_up_price, limit_down_price = self.calculate_limit_prices(
                prev_close, board_type
            )

            if current_price is not None and current_price > 0:
                limit_status = self.check_limit_status(
                    current_price, prev_close, board_type
                )

        return MarketStatus(
            symbol=symbol,
            timestamp=timestamp,
            halt_status=halt_status,
            limit_status=limit_status,
            current_price=current_price,
            prev_close=prev_close,
            limit_up_price=limit_up_price,
            limit_down_price=limit_down_price,
            board_type=board_type,
            is_st=is_st,
            halt_reason=halt_reason,
        )

    def can_place_buy_order(
        self,
        symbol: str,
        order_price: Decimal,
        prev_close: Decimal,
        is_st: bool = False,
        is_halted: bool = False,
    ) -> tuple[bool, str]:
        """
        检查是否可以下买单

        Args:
            symbol: 股票代码
            order_price: 委托价格
            prev_close: 前收盘价
            is_st: 是否ST股票
            is_halted: 是否停牌

        Returns:
            (是否可下单, 拒绝原因)
        """
        if is_halted:
            return False, "股票停牌，无法交易"

        board_type = self.get_board_type(symbol, is_st)
        limit_up, limit_down = self.calculate_limit_prices(prev_close, board_type)

        if order_price > limit_up:
            return False, f"委托价格{order_price}超过涨停价{limit_up}"

        if order_price < limit_down:
            return False, f"委托价格{order_price}低于跌停价{limit_down}"

        return True, ""

    def can_place_sell_order(
        self,
        symbol: str,
        order_price: Decimal,
        prev_close: Decimal,
        is_st: bool = False,
        is_halted: bool = False,
    ) -> tuple[bool, str]:
        """
        检查是否可以下卖单

        Args:
            symbol: 股票代码
            order_price: 委托价格
            prev_close: 前收盘价
            is_st: 是否ST股票
            is_halted: 是否停牌

        Returns:
            (是否可下单, 拒绝原因)
        """
        if is_halted:
            return False, "股票停牌，无法交易"

        board_type = self.get_board_type(symbol, is_st)
        limit_up, limit_down = self.calculate_limit_prices(prev_close, board_type)

        if order_price > limit_up:
            return False, f"委托价格{order_price}超过涨停价{limit_up}"

        if order_price < limit_down:
            return False, f"委托价格{order_price}低于跌停价{limit_down}"

        return True, ""
