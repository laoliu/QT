"""CB→正股数据桥接层

将可转债的 stk_code（正股代码）映射到正股基本面数据，为 CB 策略提供
正股 ROE、EP（E/P）、波动率、动量等增强因子。

核心设计：
- **PIT 合规**：基本面数据通过 TuSharePITProvider 获取，严格按 ann_date 对齐
- **估值数据**：PE/PB 通过 TuShareEquityProvider.load_daily_basic() 获取
- **技术指标**：波动率/动量从正股日线 OHLCV 计算
- **积极缓存**：回测期间数据缓存在内存中避免重复 IO

使用方式：
    bridge = CBStockBridge(cache_dir="data/cache")
    bridge.preload_daily_basic(start_date="20170101", end_date="20241231")
    fundamentals = bridge.get_fundamentals(stk_code="000001.SZ", query_date=date(2024,1,15))
    daily = bridge.get_daily_metrics(stk_code="000001.SZ", query_date=date(2024,1,15), bars_hist=...)

架构约束（对齐 copilot-instructions.md §4.3）：
- 本模块属于数据层，策略通过本模块间接访问正股数据
- 不在策略层直接调用 TuShareEquityProvider / TuSharePITProvider
- 策略层只读取本模块返回的 dict/dataclass
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float | None:
    """安全转换为 float，无效值返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 返回值数据结构
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StockFundamentals:
    """正股基本面数据快照（PIT 合规）。

    Attributes:
        stk_code: 正股代码（如 000001.SZ）
        query_date: 查询日期
        roe: 净资产收益率（%），来源 fina_indicator PIT
        ep: 盈利收益率 = 1 / PE_TTM，PE 来源 daily_basic
        pe_ttm: 滚动市盈率
        pb: 市净率
        total_mv: 总市值（万元）
        turnover_rate: 换手率（%）
    """

    stk_code: str
    query_date: date
    roe: float | None = None
    ep: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    turnover_rate: float | None = None


@dataclass(slots=True)
class StockDailyMetrics:
    """正股日频技术指标。

    Attributes:
        stk_code: 正股代码
        query_date: 查询日期
        vol_20d: 20 日收益率波动率（年化）
        vol_60d: 60 日收益率波动率（年化）
        mom_20d: 20 日动量（累计收益率）
        mom_60d: 60 日动量
    """

    stk_code: str
    query_date: date
    vol_20d: float | None = None
    vol_60d: float | None = None
    mom_20d: float | None = None
    mom_60d: float | None = None


# ---------------------------------------------------------------------------
# CBStockBridge 主类（骨架）
# ---------------------------------------------------------------------------

class CBStockBridge:
    """CB→正股数据桥接层。

    将正股代码映射到基本面 + 技术指标，为 CB 策略提供增强因子数据。
    使用策略在 __init__ 或 on_start 中实例化本类，在 _on_bar_update_state 中
    调用 get_fundamentals / get_daily_metrics。

    Args:
        cache_dir: 本地缓存目录（传给 TuShareEquityProvider / PIT Provider）
        pit_provider: 可选的预构建 PIT Provider 实例
        equity_provider: 可选的预构建 Equity Provider 实例
    """

    def __init__(
        self,
        cache_dir: str = "data/cache",
        pit_provider: Any | None = None,
        equity_provider: Any | None = None,
    ) -> None:
        self._cache_dir = cache_dir

        # 延迟导入 -> 测试时可注入 mock provider
        if equity_provider is not None:
            self._equity = equity_provider
        else:
            from aipha.data.tushare_equity_provider import TuShareEquityProvider

            equity_cache = f"{cache_dir}/equity_tushare"
            self._equity = TuShareEquityProvider(cache_dir=equity_cache)

        if pit_provider is not None:
            self._pit = pit_provider
        else:
            from aipha.data.tushare_pit_provider import TuSharePITProvider

            # TuSharePITProvider 需要 equity_provider 实例来获取原始财务数据
            self._pit = TuSharePITProvider(equity_provider=self._equity)

        # 内存缓存：{trade_date_str: DataFrame}  用于 daily_basic
        self._daily_basic_cache: dict[str, Any] = {}
        # 内存缓存：{(stk_code, query_date): StockFundamentals}
        self._fundamentals_cache: dict[tuple[str, date], StockFundamentals] = {}

        logger.info("CBStockBridge 初始化完成")

    # ----- 批量预加载 -----

    def preload_daily_basic(
        self,
        start_date: str,
        end_date: str,
        trade_dates: list[str] | None = None,
    ) -> None:
        """预加载 daily_basic 数据（PE/PB/MV 等），用于 EP 计算。

        将指定交易日范围的 daily_basic 批量加载到内存缓存。
        回测开始前调用一次，避免逐日 IO。

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            trade_dates: 指定的交易日列表 YYYYMMDD；为 None 则按日历生成
        """
        import pandas as pd

        if trade_dates is None:
            # 使用 equity provider 按月缓存 -> 按月遍历更高效
            from aipha.data.calendar import CNStockCalendar

            cal = CNStockCalendar()
            sd = datetime.strptime(start_date[:8], "%Y%m%d").date()
            ed = datetime.strptime(end_date[:8], "%Y%m%d").date()
            all_dates = cal.get_trading_days(sd, ed)
            trade_dates = [
                d.strftime("%Y%m%d") if isinstance(d, date) else str(d)
                for d in all_dates
            ]

        loaded = 0
        for td in trade_dates:
            if td in self._daily_basic_cache:
                continue
            try:
                df = self._equity.load_daily_basic(td)
                if df is not None and not df.empty:
                    self._daily_basic_cache[td] = df
                    loaded += 1
            except Exception as e:
                logger.debug(f"daily_basic 加载失败 {td}: {e}")

        logger.info(
            f"CBStockBridge.preload_daily_basic: 新加载 {loaded} 日，"
            f"缓存共 {len(self._daily_basic_cache)} 日"
        )

    def preload_pit(self, stk_codes: list[str]) -> None:
        """预加载 PIT 财务指标数据。

        委托给 TuSharePITProvider.preload_batch()，
        将正股的 fina_indicator 数据加载到 PIT Provider 缓存。

        Args:
            stk_codes: 正股代码列表（如 ["000001.SZ", "600519.SH"]）
        """
        if not stk_codes:
            return
        unique_codes = list(set(stk_codes))
        logger.info(f"CBStockBridge.preload_pit: 预加载 {len(unique_codes)} 只正股")
        self._pit.preload_batch(unique_codes, delay=0.2)

    # ----- 单只查询 -----

    def get_fundamentals(
        self,
        stk_code: str,
        query_date: date,
    ) -> StockFundamentals:
        """获取正股基本面数据（PIT 合规）。

        数据来源：
        - ROE: TuSharePITProvider (fina_indicator, PIT 按 ann_date 对齐)
        - PE/PB/MV/换手率: daily_basic 缓存（按交易日）
        - EP: 1/PE_TTM（PE>0 时）

        Args:
            stk_code: 正股代码（如 000001.SZ）
            query_date: 查询日期

        Returns:
            StockFundamentals 实例（缺失字段为 None）
        """
        cache_key = (stk_code, query_date)
        if cache_key in self._fundamentals_cache:
            return self._fundamentals_cache[cache_key]

        result = StockFundamentals(stk_code=stk_code, query_date=query_date)

        # --- PIT 基本面：ROE ---
        try:
            indicator = self._pit.get_indicator_pit(
                stk_code, query_date, ["roe"]
            )
            if indicator is not None:
                roe_val = indicator.indicators.get("roe")
                if roe_val is not None:
                    result.roe = float(roe_val)
        except Exception as e:
            logger.debug(f"PIT ROE 查询失败 {stk_code}: {e}")

        # --- daily_basic：PE/PB/MV/换手率 ---
        td_str = query_date.strftime("%Y%m%d")
        db_row = self._lookup_daily_basic(stk_code, td_str)
        if db_row is not None:
            pe_ttm = _safe_float(db_row.get("pe_ttm"))
            result.pe_ttm = pe_ttm
            result.ep = self.compute_ep(pe_ttm)
            result.pb = _safe_float(db_row.get("pb"))
            result.total_mv = _safe_float(db_row.get("total_mv"))
            result.turnover_rate = _safe_float(db_row.get("turnover_rate"))

        self._fundamentals_cache[cache_key] = result
        return result

    def _lookup_daily_basic(
        self,
        stk_code: str,
        trade_date: str,
    ) -> dict[str, Any] | None:
        """从内存缓存中查找某只股票某日的 daily_basic 行。

        若缓存中无该日数据，尝试按需从 equity provider 加载。

        Args:
            stk_code: 正股代码
            trade_date: 交易日期 YYYYMMDD

        Returns:
            dict of field values，无数据返回 None
        """
        import pandas as pd

        df = self._daily_basic_cache.get(trade_date)
        if df is None:
            # 按需加载
            try:
                df = self._equity.load_daily_basic(trade_date)
                if df is not None and not df.empty:
                    self._daily_basic_cache[trade_date] = df
            except Exception as e:
                logger.debug(f"daily_basic 按需加载失败 {trade_date}: {e}")
                return None

        if df is None or (hasattr(df, "empty") and df.empty):
            return None

        # 查找特定股票
        mask = df["ts_code"] == stk_code
        matched = df[mask]
        if matched.empty:
            return None
        return matched.iloc[0].to_dict()

    def get_daily_metrics(
        self,
        stk_code: str,
        query_date: date,
        close_history: list[float] | None = None,
    ) -> StockDailyMetrics:
        """获取正股日频技术指标（波动率、动量）。

        波动率和动量需要历史价格序列。策略可在 _on_bar_update_state 中
        维护正股价格历史，并在调用时传入 close_history。

        若未提供 close_history，所有指标为 None。

        Args:
            stk_code: 正股代码
            query_date: 查询日期
            close_history: 正股收盘价历史（时间正序，最后一个为最新价）

        Returns:
            StockDailyMetrics 实例
        """
        result = StockDailyMetrics(stk_code=stk_code, query_date=query_date)

        if close_history is None or len(close_history) < 3:
            return result

        # 计算日收益率
        returns: list[float] = []
        for i in range(1, len(close_history)):
            if close_history[i - 1] > 0:
                r = close_history[i] / close_history[i - 1] - 1.0
                returns.append(r)

        if len(returns) < 3:
            return result

        # 20 日波动率 & 动量
        if len(returns) >= 20:
            result.vol_20d = self.compute_volatility(returns[-20:])
        if len(close_history) >= 21:
            result.mom_20d = self.compute_momentum(close_history[-21:])

        # 60 日波动率 & 动量
        if len(returns) >= 60:
            result.vol_60d = self.compute_volatility(returns[-60:])
        if len(close_history) >= 61:
            result.mom_60d = self.compute_momentum(close_history[-61:])

        return result

    # ----- 批量查询（回测热路径）-----

    def get_fundamentals_batch(
        self,
        stk_codes_dates: list[tuple[str, date]],
    ) -> dict[str, StockFundamentals]:
        """批量获取基本面数据，返回 {stk_code: StockFundamentals}。

        回测热路径优化：单日内所有 CB 对应的正股一次性查询。

        Args:
            stk_codes_dates: [(stk_code, query_date), ...] 列表

        Returns:
            {stk_code: StockFundamentals} 字典
        """
        result: dict[str, StockFundamentals] = {}
        for stk_code, qd in stk_codes_dates:
            result[stk_code] = self.get_fundamentals(stk_code, qd)
        return result

    # ----- 辅助方法 -----

    @staticmethod
    def compute_ep(pe_ttm: float | None) -> float | None:
        """计算 EP = 1/PE_TTM，PE=None/0/负数时返回 None。"""
        if pe_ttm is None or pe_ttm <= 0:
            return None
        return 1.0 / pe_ttm

    @staticmethod
    def compute_volatility(
        returns: list[float],
        annualize_factor: float = float(TRADING_DAYS_PER_YEAR),
    ) -> float | None:
        """从日收益率序列计算年化波动率。

        Args:
            returns: 日收益率列表（非百分比，如 0.01 表示 1%）
            annualize_factor: 年化系数，默认 252 交易日

        Returns:
            年化波动率，样本不足 (<3) 时返回 None
        """
        if len(returns) < 3:
            return None
        n = len(returns)
        mean_r = sum(returns) / n
        var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        if var <= 0:
            return None
        daily_vol = math.sqrt(var)
        return daily_vol * math.sqrt(annualize_factor)

    @staticmethod
    def compute_momentum(prices: list[float]) -> float | None:
        """从价格序列计算累计收益率（动量）。

        momentum = prices[-1] / prices[0] - 1

        Args:
            prices: 价格序列（时间正序）

        Returns:
            累计收益率，价格不足或首价为0时返回 None
        """
        if len(prices) < 2:
            return None
        if prices[0] <= 0:
            return None
        return prices[-1] / prices[0] - 1.0
