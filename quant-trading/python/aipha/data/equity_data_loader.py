"""A 股数据整合层

统一入口，聚合多种数据源为策略和因子提供标准化数据包。
避免策略/因子直接调用底层 Provider，实现数据获取与使用的解耦。

数据包含：
- 日线 OHLCV（前复权）
- daily_basic（市值/PE/PB/换手率）
- PIT 财务指标（ROE/ROA/毛利率/增长率等）
- 行业分类（stock_basic.industry / 申万一级）
- 指数成分股

输出标准 EquityDataBundle，供策略和因子消费。

设计原则：
- 所有数据口径在 configs/ 中声明，此处不硬编码
- 数据加载失败不中断流程（降级为空数据 + 告警）
- 支持增量和批量加载两种模式
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class EquityDataBundle:
    """A 股数据包

    策略和因子通过此统一数据结构获取所需数据。

    Attributes:
        daily_bars: 日线 OHLCV 数据 (trade_date, ts_code → OHLCV)
        daily_basic: 每日基本面指标 (market cap, PE, PB, turnover)
        stock_basic: 股票基本信息 (name, industry, list_date, list_status)
        industry_mapping: 行业分类 {ts_code: industry_name}
        index_members: 指数成分股 {index_code: set[ts_code]}
        metadata: 附加元数据（数据版本、加载时间等）
    """

    daily_bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    daily_basic: pd.DataFrame = field(default_factory=pd.DataFrame)
    stock_basic: pd.DataFrame = field(default_factory=pd.DataFrame)
    industry_mapping: dict[str, str] = field(default_factory=dict)
    index_members: dict[str, set[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        """所有可用股票代码"""
        if not self.daily_bars.empty and "ts_code" in self.daily_bars.columns:
            return sorted(self.daily_bars["ts_code"].unique().tolist())
        if not self.stock_basic.empty and "ts_code" in self.stock_basic.columns:
            return sorted(self.stock_basic["ts_code"].unique().tolist())
        return []

    @property
    def trade_dates(self) -> list[str]:
        """所有交易日期"""
        if not self.daily_bars.empty and "trade_date" in self.daily_bars.columns:
            return sorted(self.daily_bars["trade_date"].unique().tolist())
        return []

    def get_daily_bar(self, trade_date: str) -> pd.DataFrame:
        """获取单日行情"""
        if self.daily_bars.empty:
            return pd.DataFrame()
        return self.daily_bars[self.daily_bars["trade_date"] == trade_date]

    def get_daily_basic_for_date(self, trade_date: str) -> pd.DataFrame:
        """获取单日基本面"""
        if self.daily_basic.empty:
            return pd.DataFrame()
        return self.daily_basic[self.daily_basic["trade_date"] == trade_date]

    def get_industry(self, ts_code: str) -> str:
        """获取行业分类"""
        return self.industry_mapping.get(ts_code, "未知")

    def is_index_member(self, ts_code: str, index_code: str) -> bool:
        """判断是否为指数成分股"""
        members = self.index_members.get(index_code, set())
        return ts_code in members


class EquityDataLoader:
    """A 股数据加载器

    聚合 TuShareEquityProvider 和 TuSharePITProvider，
    输出标准 EquityDataBundle。

    Args:
        equity_provider: TuShareEquityProvider 实例
        pit_provider: TuSharePITProvider 实例（可选）
    """

    def __init__(
        self,
        equity_provider: Any,
        pit_provider: Any = None,
    ) -> None:
        self._equity = equity_provider
        self._pit = pit_provider

    def load_bundle(
        self,
        start_date: str,
        end_date: str,
        load_ohlcv: bool = True,
        load_basic: bool = True,
        load_industry: bool = True,
        index_codes: list[str] | None = None,
        industry_source: str = "stock_basic",
    ) -> EquityDataBundle:
        """加载完整数据包

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
            load_ohlcv: 是否加载日线 OHLCV
            load_basic: 是否加载 daily_basic
            load_industry: 是否加载行业分类
            index_codes: 需要加载成分股的指数列表
            industry_source: 行业分类来源 ("stock_basic" / "sw_l1")

        Returns:
            EquityDataBundle
        """
        bundle = EquityDataBundle()

        # 1. stock_basic（基础信息）
        try:
            bundle.stock_basic = self._equity.load_stock_basic()
            logger.info(f"stock_basic 加载完成: {len(bundle.stock_basic)} 只")
        except Exception as e:
            logger.warning(f"stock_basic 加载失败: {e}")

        # 2. 日线 OHLCV
        if load_ohlcv:
            try:
                bundle.daily_bars = self._equity.load_daily_bars_range(
                    start_date, end_date
                )
                n_rows = len(bundle.daily_bars)
                n_dates = bundle.daily_bars["trade_date"].nunique() if n_rows > 0 else 0
                logger.info(f"daily_bars 加载完成: {n_rows} 行, {n_dates} 个交易日")
            except Exception as e:
                logger.warning(f"daily_bars 加载失败: {e}")

        # 3. daily_basic
        if load_basic:
            try:
                bundle.daily_basic = self._equity.load_daily_basic_range(
                    start_date, end_date
                )
                logger.info(f"daily_basic 加载完成: {len(bundle.daily_basic)} 行")
            except Exception as e:
                logger.warning(f"daily_basic 加载失败: {e}")

        # 4. 行业分类
        if load_industry:
            try:
                if industry_source == "sw_l1":
                    bundle.industry_mapping = self._equity.load_sw_industry_mapping(
                        level="L1"
                    )
                else:
                    bundle.industry_mapping = self._equity.load_industry_mapping()
                logger.info(f"行业分类加载完成: {len(bundle.industry_mapping)} 只")
            except Exception as e:
                logger.warning(f"行业分类加载失败: {e}")

        # 5. 指数成分股（使用回测起始日避免前视偏差）
        if index_codes:
            for idx_code in index_codes:
                try:
                    members_df = self._equity.load_index_members(
                        index_code=idx_code,
                        trade_date=start_date,
                    )
                    if not members_df.empty:
                        con_col = "con_code" if "con_code" in members_df.columns else "ts_code"
                        bundle.index_members[idx_code] = set(
                            members_df[con_col].tolist()
                        )
                    logger.info(
                        f"指数成分 {idx_code}: {len(bundle.index_members.get(idx_code, set()))} 只"
                    )
                except Exception as e:
                    logger.warning(f"指数成分 {idx_code} 加载失败: {e}")

        # 元数据
        bundle.metadata = {
            "start_date": start_date,
            "end_date": end_date,
            "load_ohlcv": load_ohlcv,
            "load_basic": load_basic,
            "industry_source": industry_source,
            "index_codes": index_codes or [],
        }

        return bundle

    def merge_ohlcv_and_basic(
        self,
        bundle: EquityDataBundle,
    ) -> pd.DataFrame:
        """合并日线 OHLCV 与 daily_basic

        Returns:
            合并后的 DataFrame，包含 OHLCV + 市值/PE/PB/换手率
        """
        if bundle.daily_bars.empty:
            return bundle.daily_basic.copy() if not bundle.daily_basic.empty else pd.DataFrame()

        if bundle.daily_basic.empty:
            return bundle.daily_bars.copy()

        merged = pd.merge(
            bundle.daily_bars,
            bundle.daily_basic.drop(columns=["close"], errors="ignore"),
            on=["ts_code", "trade_date"],
            how="left",
        )

        return merged
