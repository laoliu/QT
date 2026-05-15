"""AKShare 可转债数据提供者（集思录数据源）

专为双低策略设计的可转债数据接口，支持：
- 可转债实时列表（含溢价率、双低值）
- 强赎信息
- 转股价调整记录
- 可转债等权指数

数据来源：集思录（通过 AKShare 免费获取）

使用示例：
    provider = AKShareCBProvider(cache_dir="data/cache/cb")
    bonds = provider.load_bond_list_jsl()
    redeem_info = provider.load_redeem_info()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from aipha.data.provider_base import DataFingerprint
from aipha.data.rate_limiter import RateLimiter, get_akshare_limiter, get_rate_limit_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JSLBondSnapshot:
    """集思录可转债快照数据

    包含实时行情和关键指标，专为双低策略设计。

    Attributes:
        code: 可转债代码（纯数字，如 113676）
        name: 可转债简称
        price: 现价
        change_pct: 涨跌幅（%）
        stock_code: 正股代码
        stock_name: 正股简称
        stock_price: 正股价
        stock_change_pct: 正股涨跌幅（%）
        stock_pb: 正股市净率
        conversion_price: 转股价
        conversion_value: 转股价值
        premium_rate: 转股溢价率（%）
        rating: 债券评级
        put_trigger_price: 回售触发价
        call_trigger_price: 强赎触发价
        cb_over_stock_pct: 转债占比（%）
        maturity_date: 到期时间
        years_to_maturity: 剩余年限
        remain_amount: 剩余规模（亿元）
        turnover: 成交额（万元）
        turnover_rate: 换手率（%）
        ytm: 到期税前收益（%）
        double_low: 双低值
    """

    code: str
    name: str
    price: Decimal
    change_pct: float
    stock_code: str
    stock_name: str
    stock_price: Decimal
    stock_change_pct: float
    stock_pb: float
    conversion_price: Decimal
    conversion_value: Decimal
    premium_rate: float
    rating: str
    put_trigger_price: Optional[Decimal]
    call_trigger_price: Optional[Decimal]
    cb_over_stock_pct: float
    maturity_date: date
    years_to_maturity: float
    remain_amount: Decimal
    turnover: Decimal
    turnover_rate: float
    ytm: float
    double_low: float

    @property
    def ts_code(self) -> str:
        """标准化代码（带交易所后缀）"""
        # 11开头为沪市，12开头为深市
        if self.code.startswith("11"):
            return f"{self.code}.SH"
        return f"{self.code}.SZ"

    @property
    def is_low_price(self) -> bool:
        """是否低价（< 110）"""
        return self.price < Decimal("110")

    @property
    def is_low_premium(self) -> bool:
        """是否低溢价（< 20%）"""
        return self.premium_rate < 20.0

    @property
    def is_double_low(self) -> bool:
        """是否双低（双低值 < 130）"""
        return self.double_low < 130.0


@dataclass(frozen=True, slots=True)
class JSLRedeemInfo:
    """集思录强赎信息

    Attributes:
        code: 可转债代码
        name: 可转债简称
        price: 现价
        stock_code: 正股代码
        stock_name: 正股简称
        issue_amount: 发行规模（亿元）
        remain_amount: 剩余规模（亿元）
        conv_start_date: 转股起始日
        last_trade_date: 最后交易日
        maturity_date: 到期日
        conversion_price: 转股价
        call_trigger_ratio: 强赎触发比（%）
        call_trigger_price: 强赎触发价
        stock_price: 正股价
        redemption_price: 强赎价
        call_days_count: 强赎天计数
        call_clause: 强赎条款
        call_status: 强赎状态
    """

    code: str
    name: str
    price: Decimal
    stock_code: str
    stock_name: str
    issue_amount: Decimal
    remain_amount: Decimal
    conv_start_date: Optional[date]
    last_trade_date: Optional[date]
    maturity_date: Optional[date]
    conversion_price: Decimal
    call_trigger_ratio: float
    call_trigger_price: Decimal
    stock_price: Decimal
    redemption_price: Optional[Decimal]
    call_days_count: int
    call_clause: str
    call_status: str


@dataclass(frozen=True, slots=True)
class JSLPriceAdjustment:
    """集思录转股价调整记录

    Attributes:
        bond_name: 转债名称
        meeting_date: 股东大会日
        old_conv_price: 下修前转股价
        new_conv_price: 下修后转股价
        effective_date: 新转股价生效日期
        floor_price: 下修底价
    """

    bond_name: str
    meeting_date: Optional[date]
    old_conv_price: Decimal
    new_conv_price: Decimal
    effective_date: Optional[date]
    floor_price: Optional[Decimal]


class AKShareCBProvider:
    """AKShare 可转债数据提供者（集思录数据源）

    专为双低策略设计，提供：
    - 可转债实时数据（含溢价率、双低值）
    - 强赎信息
    - 转股价调整记录

    免费使用，无需 Token。
    注意：集思录接口（bond_cb_jsl）不带 cookie 时仅返回 30 条，
    使用 load_bond_list_full() 可获取完整列表（使用 bond_zh_cov）。

    Args:
        cache_dir: 本地缓存目录（可选）
        max_retries: 最大重试次数
        backoff_factor: 重试退避因子（秒）
        jsl_cookie: 集思录 cookie（可选，用于获取完整数据）
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        jsl_cookie: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        if cache_dir is None:
            from aipha.config import get_cache_dir
            cache_dir = str(get_cache_dir() / "cb")
        self.cache_dir = cache_dir
        self.jsl_cookie = jsl_cookie

        # 从配置加载速率限制参数
        config = get_rate_limit_config("AKSHARE")
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.backoff_factor = backoff_factor if backoff_factor is not None else config.backoff_factor

        # 速率限制器（默认使用全局共享限制器）
        self._rate_limiter = rate_limiter or get_akshare_limiter()

        # 导入 akshare
        try:
            import akshare as ak
            self._ak = ak
        except ImportError as err:
            raise ImportError("请安装 akshare: pip install akshare") from err

        # 创建缓存目录
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

        logger.debug(
            f"初始化 AKShareCBProvider: max_retries={self.max_retries}, "
            f"backoff_factor={self.backoff_factor}"
        )

    def fingerprint(self) -> DataFingerprint:
        """生成数据指纹"""
        return DataFingerprint(
            provider="akshare_jsl",
            snapshot=datetime.now().strftime("%Y%m%d_%H%M%S"),
            calendar="cn_stock_v1",
            adjust="none",
            schema="jsl_cb_v1",
        )

    def load_bond_list_jsl(
        self,
        *,
        min_amount: float = 0.0,
        max_price: float = 200.0,
        min_rating: str = "",
        exclude_called: bool = True,
    ) -> list[JSLBondSnapshot]:
        """获取可转债列表（集思录数据）

        Args:
            min_amount: 最小剩余规模（亿元）
            max_price: 最高价格
            min_rating: 最低评级（如 "AA-"）
            exclude_called: 是否排除已触发强赎

        Returns:
            可转债快照列表
        """
        df = self._call_api_with_retry("bond_cb_jsl")

        if df is None or df.empty:
            return []

        bonds = []
        for _, row in df.iterrows():
            try:
                bond = self._parse_jsl_bond(row)

                # 过滤条件
                if float(bond.remain_amount) < min_amount:
                    continue
                if float(bond.price) > max_price:
                    continue
                if min_rating and self._rating_score(bond.rating) < self._rating_score(min_rating):
                    continue

                bonds.append(bond)

            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析可转债数据失败: {e}")
                continue

        return bonds

    def load_double_low_bonds(
        self,
        *,
        top_n: int = 20,
        max_double_low: float = 130.0,
        min_amount: float = 1.0,
        min_rating: str = "AA-",
    ) -> list[JSLBondSnapshot]:
        """获取双低可转债列表

        专为双低策略设计，按双低值排序返回。

        Args:
            top_n: 返回前 N 只
            max_double_low: 最大双低值
            min_amount: 最小剩余规模（亿元）
            min_rating: 最低评级

        Returns:
            按双低值升序排列的可转债列表
        """
        bonds = self.load_bond_list_jsl(
            min_amount=min_amount,
            min_rating=min_rating,
        )

        # 筛选和排序
        filtered = [b for b in bonds if b.double_low <= max_double_low]
        sorted_bonds = sorted(filtered, key=lambda x: x.double_low)

        return sorted_bonds[:top_n]

    def load_bond_list_full(
        self,
        *,
        min_amount: float = 0.0,
        max_price: float = 200.0,
        min_rating: str = "",
    ) -> list[JSLBondSnapshot]:
        """获取完整可转债列表（使用 bond_zh_cov，无需 cookie）

        此方法使用 bond_zh_cov 接口获取 1000+ 只可转债的完整列表。
        字段比集思录少，但数据完整，适合开发测试阶段使用。

        注意：双低值由 price + premium_rate 计算得出。

        Args:
            min_amount: 最小发行规模（亿元）
            max_price: 最高价格
            min_rating: 最低评级（如 "AA-"）

        Returns:
            可转债快照列表
        """
        df = self._call_api_with_retry("bond_zh_cov")

        if df is None or df.empty:
            return []

        bonds = []
        for _, row in df.iterrows():
            try:
                bond = self._parse_bond_zh_cov(row)

                # 过滤条件
                if float(bond.remain_amount) < min_amount:
                    continue
                if float(bond.price) > max_price:
                    continue
                if min_rating and self._rating_score(bond.rating) < self._rating_score(min_rating):
                    continue

                bonds.append(bond)

            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析 bond_zh_cov 数据失败: {e}")
                continue

        return bonds

    def load_double_low_bonds_full(
        self,
        *,
        top_n: int = 20,
        max_double_low: float = 130.0,
        min_amount: float = 1.0,
        min_rating: str = "AA-",
    ) -> list[JSLBondSnapshot]:
        """获取双低可转债列表（使用完整数据源）

        使用 bond_zh_cov 数据源，无数量限制。

        Args:
            top_n: 返回前 N 只
            max_double_low: 最大双低值
            min_amount: 最小发行规模（亿元）
            min_rating: 最低评级

        Returns:
            按双低值升序排列的可转债列表
        """
        bonds = self.load_bond_list_full(
            min_amount=min_amount,
            min_rating=min_rating,
        )

        # 筛选和排序
        filtered = [b for b in bonds if b.double_low <= max_double_low]
        sorted_bonds = sorted(filtered, key=lambda x: x.double_low)

        return sorted_bonds[:top_n]

    def load_redeem_info(self) -> list[JSLRedeemInfo]:
        """获取强赎信息（集思录数据）

        Returns:
            强赎信息列表
        """
        df = self._call_api_with_retry("bond_cb_redeem_jsl")

        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            try:
                info = self._parse_redeem_info(row)
                result.append(info)
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析强赎数据失败: {e}")
                continue

        return result

    def load_price_adjustments(self) -> list[JSLPriceAdjustment]:
        """获取转股价调整记录（集思录数据）

        Returns:
            转股价调整记录列表
        """
        df = self._call_api_with_retry("bond_cb_adj_logs_jsl")

        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            try:
                adj = self._parse_price_adjustment(row)
                result.append(adj)
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析转股价调整数据失败: {e}")
                continue

        return result

    def load_cb_index_jsl(self) -> dict[str, Any]:
        """获取可转债等权指数（集思录数据）

        Returns:
            等权指数数据字典
        """
        df = self._call_api_with_retry("bond_cb_index_jsl")

        if df is None or df.empty:
            return {}

        # 返回最新一行的数据
        latest = df.iloc[-1] if len(df) > 0 else {}
        return dict(latest) if isinstance(latest, pd.Series) else {}

    def load_cb_daily_history(
        self,
        cb_code: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        use_cache: bool = True,
    ) -> list[dict]:
        """获取可转债历史日线数据

        使用新浪财经数据源获取可转债历史K线数据。
        支持本地 Parquet 缓存，避免重复拉取。

        Args:
            cb_code: 可转债代码（如 127042 或 113676）
            start_date: 开始日期（可选）
            end_date: 结束日期（可选）
            use_cache: 是否使用本地缓存（默认 True）

        Returns:
            历史日线数据列表，每个元素包含:
            - date: 日期
            - open: 开盘价
            - high: 最高价
            - low: 最低价
            - close: 收盘价
            - volume: 成交量

        注意：大量抓取容易封 IP，请配合速率限制使用
        """
        # 规范化代码
        code = str(cb_code).replace(".SH", "").replace(".SZ", "").strip()

        # 检查缓存
        cache_path = None
        if use_cache and self.cache_dir:
            cache_subdir = Path(self.cache_dir) / "history"
            cache_subdir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_subdir / f"{code}.parquet"

            if cache_path.exists():
                try:
                    df = pd.read_parquet(cache_path)
                    result = self._parse_history_df(df, start_date, end_date)
                    if result:
                        logger.debug(f"从缓存加载 {cb_code} 历史数据: {len(result)} 条")
                        return result
                except Exception as e:
                    logger.warning(f"读取缓存失败 {cache_path}: {e}")

        # 转换代码格式: 11xxxx -> sh, 12xxxx -> sz
        if code.startswith("11"):
            symbol = f"sh{code}"
        elif code.startswith("12"):
            symbol = f"sz{code}"
        else:
            # 尝试自动判断
            symbol = f"sz{code}" if code.startswith("1") else f"sh{code}"

        try:
            # 速率限制
            self._rate_limiter.acquire()

            df = self._ak.bond_zh_hs_cov_daily(symbol=symbol)

            if df is None or df.empty:
                return []

            # 保存到缓存
            if cache_path is not None and not df.empty:
                try:
                    df.to_parquet(cache_path, index=False)
                    logger.debug(f"保存缓存: {cache_path}")
                except Exception as e:
                    logger.warning(f"保存缓存失败 {cache_path}: {e}")

            return self._parse_history_df(df, start_date, end_date)

        except Exception as e:
            logger.warning(f"获取可转债 {cb_code} 历史数据失败: {e}")
            return []

    def _parse_history_df(
        self,
        df: pd.DataFrame,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict]:
        """解析历史数据 DataFrame"""
        result = []
        for _, row in df.iterrows():
            try:
                bar_date = self._parse_date(row.get("date"))
                if bar_date is None:
                    continue

                # 日期过滤
                if start_date and bar_date < start_date:
                    continue
                if end_date and bar_date > end_date:
                    continue

                result.append({
                    "date": bar_date,
                    "open": float(row.get("open", 0) or 0),
                    "high": float(row.get("high", 0) or 0),
                    "low": float(row.get("low", 0) or 0),
                    "close": float(row.get("close", 0) or 0),
                    "volume": int(row.get("volume", 0) or 0),
                })
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析历史数据行失败: {e}")
                continue

        return result

    def load_cb_batch_history(
        self,
        cb_codes: list[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        max_bonds: int = 50,
    ) -> dict[str, list[dict]]:
        """批量获取可转债历史日线数据

        Args:
            cb_codes: 可转债代码列表
            start_date: 开始日期
            end_date: 结束日期
            max_bonds: 最大获取数量（防止封 IP）

        Returns:
            {代码: 历史数据列表} 字典
        """
        result = {}
        codes_to_load = cb_codes[:max_bonds]

        logger.info(f"批量加载 {len(codes_to_load)} 只可转债历史数据...")

        for i, code in enumerate(codes_to_load):
            try:
                history = self.load_cb_daily_history(code, start_date, end_date)
                if history:
                    result[code] = history
                    logger.debug(f"[{i+1}/{len(codes_to_load)}] {code}: {len(history)} 条记录")
            except Exception as e:
                logger.warning(f"[{i+1}/{len(codes_to_load)}] {code} 加载失败: {e}")
                continue

        logger.info(f"加载完成: {len(result)} 只可转债")
        return result

    def load_stock_daily_history(
        self,
        stock_code: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        use_cache: bool = True,
    ) -> list[dict]:
        """获取正股历史日线数据

        用于计算可转债历史溢价率。

        Args:
            stock_code: 正股代码（如 000001 或 600000）
            start_date: 开始日期
            end_date: 结束日期
            use_cache: 是否使用缓存

        Returns:
            历史日线数据列表
        """
        code = str(stock_code).replace(".SH", "").replace(".SZ", "").strip()

        # 检查缓存
        cache_path = None
        if use_cache and self.cache_dir:
            cache_subdir = Path(self.cache_dir) / "stock_history"
            cache_subdir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_subdir / f"{code}.parquet"

            if cache_path.exists():
                try:
                    df = pd.read_parquet(cache_path)
                    result = self._parse_stock_history_df(df, start_date, end_date)
                    if result:
                        logger.debug(f"从缓存加载正股 {stock_code} 历史: {len(result)} 条")
                        return result
                except Exception as e:
                    logger.warning(f"读取正股缓存失败 {cache_path}: {e}")

        try:
            self._rate_limiter.acquire()

            # 使用 AKShare 获取正股日线
            start_str = start_date.strftime("%Y%m%d") if start_date else "19900101"
            end_str = end_date.strftime("%Y%m%d") if end_date else "20991231"

            df = self._ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust="qfq",  # 前复权
            )

            if df is None or df.empty:
                return []

            # 保存缓存
            if cache_path is not None and not df.empty:
                try:
                    df.to_parquet(cache_path, index=False)
                except Exception as e:
                    logger.warning(f"保存正股缓存失败: {e}")

            return self._parse_stock_history_df(df, start_date, end_date)

        except Exception as e:
            logger.warning(f"获取正股 {stock_code} 历史数据失败: {e}")
            return []

    def _parse_stock_history_df(
        self,
        df: pd.DataFrame,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict]:
        """解析正股历史 DataFrame"""
        result = []
        for _, row in df.iterrows():
            try:
                # 正股数据列名可能是中文
                bar_date = self._parse_date(row.get("日期") or row.get("date"))
                if bar_date is None:
                    continue

                if start_date and bar_date < start_date:
                    continue
                if end_date and bar_date > end_date:
                    continue

                result.append({
                    "date": bar_date,
                    "open": float(row.get("开盘") or row.get("open", 0) or 0),
                    "high": float(row.get("最高") or row.get("high", 0) or 0),
                    "low": float(row.get("最低") or row.get("low", 0) or 0),
                    "close": float(row.get("收盘") or row.get("close", 0) or 0),
                    "volume": int(row.get("成交量") or row.get("volume", 0) or 0),
                })
            except (ValueError, KeyError, TypeError) as e:
                logger.debug(f"解析正股历史数据失败: {e}")
                continue

        return result

    def load_cb_history_with_premium(
        self,
        cb_code: str,
        stock_code: str,
        conversion_price: float,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict]:
        """获取可转债历史数据（含计算的溢价率）

        通过正股价格和转股价计算每日溢价率。

        Args:
            cb_code: 可转债代码
            stock_code: 正股代码
            conversion_price: 转股价
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            增强的历史数据，含 premium_rate 字段
        """
        # 获取可转债历史
        cb_history = self.load_cb_daily_history(cb_code, start_date, end_date)
        if not cb_history:
            return []

        # 获取正股历史
        stock_history = self.load_stock_daily_history(stock_code, start_date, end_date)
        if not stock_history:
            logger.warning(f"无法获取正股 {stock_code} 历史，使用当前溢价率")
            return cb_history

        # 构建正股价格映射
        stock_prices = {h["date"]: h["close"] for h in stock_history}

        # 计算每日溢价率
        enhanced_history = []
        for bar in cb_history:
            bar_date = bar["date"]
            cb_price = bar["close"]

            stock_price = stock_prices.get(bar_date)
            if stock_price and stock_price > 0 and conversion_price > 0:
                # 转股价值 = 100 / 转股价 * 正股价
                conversion_value = (100.0 / conversion_price) * stock_price
                # 溢价率 = (转债价格 / 转股价值 - 1) * 100
                premium_rate = (cb_price / conversion_value - 1) * 100 if conversion_value > 0 else 0
            else:
                premium_rate = None

            enhanced_bar = bar.copy()
            enhanced_bar["premium_rate"] = premium_rate
            enhanced_bar["conversion_value"] = (
                (100.0 / conversion_price) * stock_price
                if stock_price and conversion_price > 0
                else None
            )
            enhanced_history.append(enhanced_bar)

        return enhanced_history

    def _call_api_with_retry(self, api_name: str, **kwargs) -> pd.DataFrame | None:
        """带重试和速率限制的 API 调用

        使用速率限制器控制请求频率，并支持自动重试。
        """
        for attempt in range(self.max_retries):
            try:
                # 速率限制：阻塞直到可以发起请求
                self._rate_limiter.acquire()

                api_func = getattr(self._ak, api_name)
                df = api_func(**kwargs)

                logger.debug(f"AKShare API {api_name} 调用成功")
                return df

            except Exception as e:
                logger.warning(
                    f"AKShare API {api_name} 调用失败 "
                    f"(尝试 {attempt+1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    wait_time = self.backoff_factor ** attempt
                    logger.debug(f"等待 {wait_time:.1f}s 后重试...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"AKShare API {api_name} 最终失败: {e}")
                    return None
        return None

    def _parse_jsl_bond(self, row: pd.Series) -> JSLBondSnapshot:
        """解析集思录可转债数据"""
        # 处理日期
        maturity_str = row.get("到期时间", "")
        maturity_date = self._parse_date(maturity_str) or date(2030, 1, 1)

        return JSLBondSnapshot(
            code=str(row["代码"]),
            name=str(row["转债名称"]),
            price=Decimal(str(row["现价"])),
            change_pct=float(row.get("涨跌幅", 0) or 0),
            stock_code=str(row.get("正股代码", "")),
            stock_name=str(row.get("正股名称", "")),
            stock_price=Decimal(str(row.get("正股价", 0) or 0)),
            stock_change_pct=float(row.get("正股涨跌", 0) or 0),
            stock_pb=float(row.get("正股PB", 0) or 0),
            conversion_price=Decimal(str(row.get("转股价", 0) or 0)),
            conversion_value=Decimal(str(row.get("转股价值", 0) or 0)),
            premium_rate=float(row.get("转股溢价率", 0) or 0),
            rating=str(row.get("债券评级", "")),
            put_trigger_price=self._safe_decimal(row.get("回售触发价")),
            call_trigger_price=self._safe_decimal(row.get("强赎触发价")),
            cb_over_stock_pct=float(row.get("转债占比", 0) or 0),
            maturity_date=maturity_date,
            years_to_maturity=float(row.get("剩余年限", 0) or 0),
            remain_amount=Decimal(str(row.get("剩余规模", 0) or 0)),
            turnover=Decimal(str(row.get("成交额", 0) or 0)),
            turnover_rate=float(row.get("换手率", 0) or 0),
            ytm=float(row.get("到期税前收益", 0) or 0),
            double_low=float(row.get("双低", 0) or 0),
        )

    def _parse_bond_zh_cov(self, row: pd.Series) -> JSLBondSnapshot:
        """解析 bond_zh_cov 数据（东方财富数据源）

        列名: ['债券代码', '债券简称', '申购日期', '申购代码', '申购上限',
               '正股代码', '正股简称', '正股价', '转股价', '转股价值',
               '债现价', '转股溢价率', '原股东配售-股权登记日',
               '原股东配售-每股配售额', '发行规模', '中签号发布日',
               '中签率', '上市时间', '信用评级']
        """
        # 获取价格和溢价率
        price = float(row.get("债现价", 0) or 0)
        premium_str = str(row.get("转股溢价率", "0%")).replace("%", "")
        try:
            premium_rate = float(premium_str)
        except ValueError:
            premium_rate = 0.0

        # 计算双低值 = 价格 + 溢价率
        double_low = price + premium_rate if price > 0 else 0.0

        # 发行规模（亿元）
        issue_size = float(row.get("发行规模", 0) or 0)

        # 上市时间
        listing_date = self._parse_date(row.get("上市时间"))

        return JSLBondSnapshot(
            code=str(row.get("债券代码", "")).strip(),
            name=str(row.get("债券简称", "")),
            price=Decimal(str(price)) if price else Decimal("0"),
            change_pct=0.0,  # bond_zh_cov 不含涨跌幅
            stock_code=str(row.get("正股代码", "")),
            stock_name=str(row.get("正股简称", "")),
            stock_price=Decimal(str(row.get("正股价", 0) or 0)),
            stock_change_pct=0.0,  # bond_zh_cov 不含正股涨跌
            stock_pb=0.0,  # bond_zh_cov 不含 PB
            conversion_price=Decimal(str(row.get("转股价", 0) or 0)),
            conversion_value=Decimal(str(row.get("转股价值", 0) or 0)),
            premium_rate=premium_rate,
            rating=str(row.get("信用评级", "")),
            put_trigger_price=None,  # bond_zh_cov 不含回售触发价
            call_trigger_price=None,  # bond_zh_cov 不含强赎触发价
            cb_over_stock_pct=0.0,  # bond_zh_cov 不含转债占比
            maturity_date=date(2030, 1, 1),  # bond_zh_cov 不含到期日，使用默认值
            years_to_maturity=0.0,  # bond_zh_cov 不含剩余年限
            remain_amount=Decimal(str(issue_size)),  # 使用发行规模近似剩余规模
            turnover=Decimal("0"),  # bond_zh_cov 不含成交额
            turnover_rate=0.0,  # bond_zh_cov 不含换手率
            ytm=0.0,  # bond_zh_cov 不含 YTM
            double_low=double_low,
        )

    def _parse_redeem_info(self, row: pd.Series) -> JSLRedeemInfo:
        """解析强赎信息"""
        return JSLRedeemInfo(
            code=str(row["代码"]),
            name=str(row["名称"]),
            price=Decimal(str(row.get("现价", 0) or 0)),
            stock_code=str(row.get("正股代码", "")),
            stock_name=str(row.get("正股名称", "")),
            issue_amount=Decimal(str(row.get("规模", 0) or 0)),
            remain_amount=Decimal(str(row.get("剩余规模", 0) or 0)),
            conv_start_date=self._parse_date(row.get("转股起始日")),
            last_trade_date=self._parse_date(row.get("最后交易日")),
            maturity_date=self._parse_date(row.get("到期日")),
            conversion_price=Decimal(str(row.get("转股价", 0) or 0)),
            call_trigger_ratio=float(row.get("强赎触发比", 0) or 0),
            call_trigger_price=Decimal(str(row.get("强赎触发价", 0) or 0)),
            stock_price=Decimal(str(row.get("正股价", 0) or 0)),
            redemption_price=self._safe_decimal(row.get("强赎价")),
            call_days_count=int(row.get("强赎天计数", 0) or 0),
            call_clause=str(row.get("强赎条款", "")),
            call_status=str(row.get("强赎状态", "")),
        )

    def _parse_price_adjustment(self, row: pd.Series) -> JSLPriceAdjustment:
        """解析转股价调整记录"""
        return JSLPriceAdjustment(
            bond_name=str(row.get("转债名称", "")),
            meeting_date=self._parse_date(row.get("股东大会日")),
            old_conv_price=Decimal(str(row.get("下修前转股价", 0) or 0)),
            new_conv_price=Decimal(str(row.get("下修后转股价", 0) or 0)),
            effective_date=self._parse_date(row.get("新转股价生效日期")),
            floor_price=self._safe_decimal(row.get("下修底价")),
        )

    def _parse_date(self, value: Any) -> date | None:
        """安全解析日期"""
        if value is None or pd.isna(value):
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        try:
            s = str(value).strip()
            if not s or s == "-":
                return None
            # 尝试多种格式
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]:
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None
        except Exception:
            return None

    def _safe_decimal(self, value: Any) -> Decimal | None:
        """安全转换为 Decimal"""
        if value is None or pd.isna(value):
            return None
        try:
            s = str(value).strip()
            if not s or s == "-":
                return None
            return Decimal(s)
        except Exception:
            return None

    def _rating_score(self, rating: str) -> int:
        """评级转换为分数（用于比较）"""
        rating_map = {
            "AAA": 100,
            "AA+": 90,
            "AA": 80,
            "AA-": 70,
            "A+": 60,
            "A": 50,
            "A-": 40,
            "BBB+": 30,
            "BBB": 20,
            "BBB-": 10,
        }
        return rating_map.get(rating.upper().strip(), 0)
