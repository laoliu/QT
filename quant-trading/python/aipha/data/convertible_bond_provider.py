"""可转债数据提供者

支持从 TuShare Pro 获取可转债数据：
- 可转债基础信息（cb_basic）
- 可转债日线行情（cb_daily）
- 转股价变动（cb_price_chg）
- 强赎公告（cb_call）
- 正股行情（用于计算溢价率）

使用示例：
    provider = ConvertibleBondProvider(
        token="your_token",
        cache_dir="data/cache/cb",
    )
    bonds = provider.load_bond_list()
    bars = provider.load_bond_bars(symbol="127075.SZ", start=..., end=...)
    metrics = provider.load_metrics(symbol="127075.SZ", trade_date=...)
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from aipha.data.provider_base import DataFingerprint
from aipha.domain.convertible_bond import (
    BondCorporateAction,
    BondStatus,
    ConversionMetrics,
    ConvertibleBond,
    ConvertibleBondBar,
    CorporateActionType,
)
from aipha.domain.models import Bar


class ConvertibleBondProvider:
    """可转债数据提供者

    基于 TuShare Pro 实现，支持：
    - 可转债基础信息获取
    - 可转债日线行情
    - 正股联动数据
    - 条款变更事件
    - 双低指标计算
    """

    def __init__(
        self,
        token: str | None = None,
        cache_dir: str | None = None,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        from aipha.data.tushare_gateway import get_tushare_gateway

        if cache_dir is None:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "cb")

        self._gateway = get_tushare_gateway(token=token)
        self.token = self._gateway._token  # 向后兼容
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        # 通过网关获取 API 对象（向后兼容需要 self.api 的调用方）
        self.api = self._gateway.api

        # 创建缓存目录
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def fingerprint(self) -> DataFingerprint:
        """生成数据指纹"""
        return DataFingerprint(
            provider="tushare_cb",
            snapshot=datetime.now().strftime("%Y%m%d"),
            calendar="SSE",
            adjust="none",
            schema="cb_v1",
        )

    def load_bond_list(
        self,
        *,
        exchange: str | None = None,
        status: str | None = "L",  # L=上市, D=退市, P=待上市
    ) -> list[ConvertibleBond]:
        """获取可转债列表

        Args:
            exchange: 交易所（SSE上交所/SZSE深交所），None则全部
            status: 状态（L上市/D退市/P待上市），None则全部

        Returns:
            可转债基础信息列表
        """
        df = self._call_api_with_retry(
            "cb_basic",
            exchange=exchange or "",
            list_status=status or "",
        )

        if df is None or df.empty:
            return []

        bonds = []
        for _, row in df.iterrows():
            try:
                bond = self._parse_bond_info(row)
                bonds.append(bond)
            except (ValueError, KeyError):
                # 忽略解析失败的记录
                continue

        return bonds

    def load_bond_info(self, *, symbol: str) -> ConvertibleBond | None:
        """获取单个可转债基础信息

        Args:
            symbol: 可转债代码（如 127075.SZ）

        Returns:
            可转债基础信息，不存在则返回 None
        """
        df = self._call_api_with_retry("cb_basic", ts_code=symbol)

        if df is None or df.empty:
            return None

        return self._parse_bond_info(df.iloc[0])

    def load_bond_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
    ) -> list[ConvertibleBondBar]:
        """获取可转债日线行情

        Args:
            symbol: 可转债代码
            start: 开始日期
            end: 结束日期（包含）

        Returns:
            行情数据列表（按时间升序）
        """
        # 尝试从缓存加载
        if self.cache_dir:
            cache_path = self._cache_path(symbol, start, end, "cb_daily")
            if cache_path.exists():
                df = pd.read_parquet(cache_path)
                return self._parse_bond_bars(df)

        # 从 API 加载
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        df = self._call_api_with_retry(
            "cb_daily",
            ts_code=symbol,
            start_date=start_str,
            end_date=end_str,
        )

        if df is None or df.empty:
            return []

        # 缓存
        if self.cache_dir:
            cache_path = self._cache_path(symbol, start, end, "cb_daily")
            df.to_parquet(cache_path)

        return self._parse_bond_bars(df)

    def load_stock_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
    ) -> list[Bar]:
        """获取正股日线行情

        用于计算转股溢价率。

        Args:
            symbol: 正股代码
            start: 开始日期
            end: 结束日期

        Returns:
            正股行情列表
        """
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        df = self._call_api_with_retry(
            "daily",
            ts_code=symbol,
            start_date=start_str,
            end_date=end_str,
        )

        if df is None or df.empty:
            return []

        return self._parse_stock_bars(df)

    def load_metrics(
        self,
        *,
        symbol: str,
        trade_date: date,
        bond_info: ConvertibleBond | None = None,
    ) -> ConversionMetrics | None:
        """计算单日转股指标

        Args:
            symbol: 可转债代码
            trade_date: 交易日期
            bond_info: 可转债基础信息（可选，不提供则自动获取）

        Returns:
            转股指标
        """
        if bond_info is None:
            bond_info = self.load_bond_info(symbol=symbol)
            if bond_info is None:
                return None

        # 获取可转债当日价格
        bond_bars = self.load_bond_bars(
            symbol=symbol,
            start=trade_date,
            end=trade_date,
        )
        if not bond_bars:
            return None

        bond_price = Decimal(str(bond_bars[0].close))

        # 获取正股当日价格
        stock_bars = self.load_stock_bars(
            symbol=bond_info.stk_code,
            start=trade_date,
            end=trade_date,
        )
        if not stock_bars:
            return None

        stock_price = Decimal(str(stock_bars[0].close))

        return ConversionMetrics.calculate(
            ts_code=symbol,
            trade_date=trade_date,
            bond_price=bond_price,
            stock_price=stock_price,
            conv_price=bond_info.conv_price,
            par_value=bond_info.par_value,
        )

    def load_corporate_actions(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
    ) -> list[BondCorporateAction]:
        """获取条款变更事件

        Args:
            symbol: 可转债代码
            start: 开始日期
            end: 结束日期

        Returns:
            公司行动列表
        """
        actions = []

        # 获取转股价变动
        price_changes = self._load_price_changes(symbol, start, end)
        actions.extend(price_changes)

        # 获取强赎公告
        calls = self._load_redemption_calls(symbol, start, end)
        actions.extend(calls)

        return sorted(actions, key=lambda x: x.announce_date)

    def load_double_low_ranking(
        self,
        *,
        trade_date: date,
        top_n: int = 30,
        max_price: float = 130.0,
        max_premium: float = 50.0,
        exclude_redeemed: bool = True,
    ) -> list[ConversionMetrics]:
        """获取双低排名

        筛选条件：
        - 价格 < max_price
        - 溢价率 < max_premium
        - 排除已公告强赎

        Args:
            trade_date: 交易日期
            top_n: 返回前 N 只
            max_price: 最高价格限制
            max_premium: 最高溢价率限制
            exclude_redeemed: 是否排除已强赎

        Returns:
            按双低指标排序的转股指标列表
        """
        # 获取所有上市可转债
        bonds = self.load_bond_list(status="L")

        # 计算每只可转债的指标
        metrics_list = []
        for bond in bonds:
            if exclude_redeemed and bond.status == BondStatus.REDEEMED:
                continue

            metrics = self.load_metrics(
                symbol=bond.ts_code,
                trade_date=trade_date,
                bond_info=bond,
            )

            if metrics is None:
                continue

            # 筛选条件
            if float(metrics.bond_price) > max_price:
                continue
            if float(metrics.premium_rate) > max_premium:
                continue

            metrics_list.append(metrics)

        # 按双低指标排序
        metrics_list.sort(key=lambda x: float(x.double_low))

        return metrics_list[:top_n]

    # ==================== Private Methods ====================

    def _call_api_with_retry(
        self,
        api_name: str,
        **kwargs,
    ) -> pd.DataFrame | None:
        """带重试的 API 调用（通过统一网关限流/并发控制）"""
        return self._gateway.call(api_name, max_retries=self.max_retries, **kwargs)

    def _parse_bond_info(self, row: pd.Series) -> ConvertibleBond:
        """解析可转债基础信息"""

        def parse_date(val: object) -> date:
            """解析日期字段，缺失时返回远期默认值。"""
            if pd.isna(val):
                return date(2099, 12, 31)  # 默认远期日期
            return datetime.strptime(str(val)[:8], "%Y%m%d").date()

        def parse_decimal(val: object) -> Decimal:
            """解析数值字段为Decimal，缺失时返回0。"""
            if pd.isna(val):
                return Decimal("0")
            return Decimal(str(val))

        status_map = {
            "L": BondStatus.LISTED,
            "D": BondStatus.DELISTED,
            "R": BondStatus.REDEEMED,
        }

        return ConvertibleBond(
            ts_code=row["ts_code"],
            bond_short_name=row.get("bond_short_name", ""),
            stk_code=row.get("stk_code", ""),
            stk_short_name=row.get("stk_short_name", ""),
            conv_price=parse_decimal(row.get("conv_price")),
            conv_start_date=parse_date(row.get("conv_start_date")),
            conv_end_date=parse_date(row.get("conv_end_date")),
            maturity_date=parse_date(row.get("maturity_date")),
            issue_size=parse_decimal(row.get("issue_size", 0)),
            par_value=Decimal("100"),
            status=status_map.get(row.get("list_status", "L"), BondStatus.LISTED),
            list_date=parse_date(row.get("list_date"))
            if not pd.isna(row.get("list_date"))
            else None,
            delist_date=parse_date(row.get("delist_date"))
            if not pd.isna(row.get("delist_date"))
            else None,
        )

    def _parse_bond_bars(self, df: pd.DataFrame) -> list[ConvertibleBondBar]:
        """解析可转债行情数据"""
        bars = []

        # 按日期升序排序
        df = df.sort_values("trade_date", ascending=True)

        for _, row in df.iterrows():
            trade_date = datetime.strptime(str(row["trade_date"]), "%Y%m%d")
            bar = ConvertibleBondBar(
                symbol=row["ts_code"],
                start_ts=trade_date.replace(hour=9, minute=30),
                end_ts=trade_date.replace(hour=15, minute=0),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("vol", 0)) * 10,  # 手→张 (§4.2 统一单位)
                amount=float(row.get("amount", 0)) * 1000,  # 单位转换
                vwap=None,
                is_suspended=(
                    float(row.get("vol", 0)) <= 0
                    or float(row.get("open", 0)) <= 0
                ),  # §13.3 停牌检测（vol<=0 或 open<=0）
            )
            bars.append(bar)

        return bars

    def _parse_stock_bars(self, df: pd.DataFrame) -> list[Bar]:
        """解析正股行情数据"""
        bars = []

        df = df.sort_values("trade_date", ascending=True)

        for _, row in df.iterrows():
            trade_date = datetime.strptime(str(row["trade_date"]), "%Y%m%d")
            bar = Bar(
                symbol=row["ts_code"],
                start_ts=trade_date.replace(hour=9, minute=30),
                end_ts=trade_date.replace(hour=15, minute=0),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("vol", 0)),
                vwap=None,
                is_suspended=(
                    float(row.get("vol", 0)) <= 0
                    or float(row.get("open", 0)) <= 0
                ),
            )
            bars.append(bar)

        return bars

    def _load_price_changes(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[BondCorporateAction]:
        """获取转股价变动"""
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        try:
            df = self._call_api_with_retry(
                "cb_price_chg",
                ts_code=symbol,
                start_date=start_str,
                end_date=end_str,
            )
        except RuntimeError:
            return []

        if df is None or df.empty:
            return []

        actions = []
        for _, row in df.iterrows():
            action = BondCorporateAction(
                ts_code=row["ts_code"],
                action_type=CorporateActionType.CONVERSION_PRICE_DOWN,
                announce_date=datetime.strptime(
                    str(row["publish_date"])[:8], "%Y%m%d"
                ).date(),
                effective_date=datetime.strptime(
                    str(row["change_date"])[:8], "%Y%m%d"
                ).date(),
                old_value=Decimal(str(row.get("conv_price_bef", 0))),
                new_value=Decimal(str(row.get("conv_price_aft", 0))),
                memo=row.get("change_reason", ""),
            )
            actions.append(action)

        return actions

    def _load_redemption_calls(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[BondCorporateAction]:
        """获取强赎公告"""
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        try:
            df = self._call_api_with_retry(
                "cb_call",
                ts_code=symbol,
                start_date=start_str,
                end_date=end_str,
            )
        except RuntimeError:
            return []

        if df is None or df.empty:
            return []

        actions = []
        for _, row in df.iterrows():
            action = BondCorporateAction(
                ts_code=row["ts_code"],
                action_type=CorporateActionType.REDEMPTION_CALL,
                announce_date=datetime.strptime(
                    str(row["ann_date"])[:8], "%Y%m%d"
                ).date(),
                effective_date=datetime.strptime(
                    str(row.get("call_date", row["ann_date"]))[:8], "%Y%m%d"
                ).date(),
                new_value=Decimal(str(row.get("call_price", 0))),
                memo="强制赎回公告",
            )
            actions.append(action)

        return actions

    def _cache_path(
        self,
        symbol: str,
        start: date,
        end: date,
        data_type: str,
    ) -> Path:
        """生成缓存文件路径"""
        key = f"{symbol}_{start}_{end}_{data_type}"
        filename = hashlib.md5(key.encode()).hexdigest()[:16] + ".parquet"
        return Path(self.cache_dir) / data_type / filename
