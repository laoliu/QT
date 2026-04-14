"""可转债数据适配器

将 TuShareCBProvider 的 DataFrame 数据转换为 BacktestEngine 所需的
``dict[date, dict[str, Bar]]`` 格式，并将可转债特有字段填入 Bar.metadata。

**数据单位约定（TuShare → 内部）**::

    TuShare cb_basic:
        remain_size: 元 → 转换为 万元（÷10000）
        issue_size:  元 → 转换为 万元（÷10000）

    TuShare cb_daily:
        vol:    手（1手=10张）→ 转换为 张（×10）
        amount: 万元 → 保持不变（万元）
        cb_over_rate: %（百分比）→ 保持不变

    内部统一单位:
        Bar.volume:          张
        metadata.remain_size: 万元
        metadata.amount:      万元
        metadata.cb_over_rate: %

.. warning::

    remain_size 来自 cb_basic（最新快照），不是历史时点数据。
    用于历史回测时存在前视偏差（survivorship bias），
    详见 docs/known_limitations.md。

典型用法::

    adapter = CBDataAdapter(cache_dir="data/cache/cb_tushare")
    bars_by_date = adapter.load("20220101", "20241231")

    engine = BacktestEngine(config, strategy, ...)
    result = engine.run(bars_by_date)
"""

from __future__ import annotations

import bisect
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aipha.data.tushare_cb_provider import TuShareCBProvider
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


class CBDataAdapter:
    """可转债数据适配器

    负责：
    1. 从 TuShareCBProvider 加载日行情 + 基础信息
    2. 合并后转为 ``dict[date, dict[str, Bar]]``
    3. 每只 Bar 的 metadata 包含 CB 特有字段

    Args:
        cache_dir: TuShare 本地缓存目录
        token: TuShare Pro API Token（可选）
    """

    def __init__(
        self,
        cache_dir: str = "data/cache/cb_tushare",
        token: Optional[str] = None,
    ):
        self.provider = TuShareCBProvider(token=token, cache_dir=cache_dir)
        # 强赎公告数据（在 load() 中延迟加载）
        self._force_redeem_data: Optional[pd.DataFrame] = None
        # 强赎预索引: {ts_code: (ann_dates, statuses, is_called_flags)}
        self._force_redeem_index: dict[str, tuple[list[str], list[str], list[bool]]] = {}

    def load(
        self,
        start_date: str,
        end_date: str,
    ) -> dict[date, dict[str, Bar]]:
        """加载日期范围内的可转债数据

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            date -> {ts_code -> Bar} 映射
        """
        logger.info(f"加载可转债数据: {start_date} - {end_date}")

        # 1. 加载基础信息
        basic = self.provider.load_cb_basic()
        logger.info(f"可转债基础信息: {len(basic)} 条")

        # 2. 加载日行情
        daily = self.provider.load_cb_daily(
            start_date=start_date,
            end_date=end_date,
        )
        logger.info(f"可转债日行情: {len(daily)} 条")

        if daily is None or len(daily) == 0:
            logger.warning("无可转债行情数据")
            return {}

        # 2.5 加载强赎公告数据（用于 PIT 强赎状态注入）
        self._force_redeem_data = self._load_force_redeem_data()
        self._force_redeem_index = self._build_force_redeem_index()

        # 3. 单位转换（TuShare 原始单位 → 内部统一单位）
        # remain_size: 元 → 万元
        if "remain_size" in basic.columns:
            basic = basic.copy()
            basic["remain_size"] = basic["remain_size"] / 10000.0
            logger.debug("remain_size 单位转换: 元 → 万元 (÷10000)")

        # issue_size: 元 → 万元（与 remain_size 单位统一）
        if "issue_size" in basic.columns:
            basic["issue_size"] = basic["issue_size"] / 10000.0
            logger.debug("issue_size 单位转换: 元 → 万元 (÷10000)")

        # 4. 合并基础信息
        basic_cols = [
            "ts_code",
            "bond_short_name",
            "stk_short_name",
            "stk_code",
            "remain_size",
            "issue_size",
            "list_date",
            "delist_date",
            "maturity_date",
            "rating",
        ]
        available_cols = [c for c in basic_cols if c in basic.columns]
        merged = daily.merge(basic[available_cols], on="ts_code", how="left")

        # 5. vol 单位转换: 手(1手=10张) → 张
        if "vol" in merged.columns:
            merged = merged.copy()
            merged["vol"] = merged["vol"] * 10.0
            logger.debug("vol 单位转换: 手 → 张 (×10)")

        # 5.5 数据质量检查 & 清洗
        merged = self._quality_check_and_clean(merged)

        # 5.6 计算 prev_close（前一交易日收盘价）
        # 用于 2022-08-01 新规涨跌停检查，从历史close按symbol + trade_date推算
        if "close" in merged.columns and "trade_date" in merged.columns:
            merged = merged.sort_values(["ts_code", "trade_date"])
            merged["prev_close"] = merged.groupby("ts_code")["close"].shift(1)
            logger.debug("prev_close 已从前一交易日 close 推算")

        # 6. 转换为 dict[date, dict[str, Bar]]
        bars_by_date: dict[date, dict[str, Bar]] = {}

        for trade_date_str, group in merged.groupby("trade_date"):
            td = self._parse_date(str(trade_date_str))
            if td is None:
                continue

            bars: dict[str, Bar] = {}
            dt_start = datetime.combine(td, datetime.min.time().replace(hour=9, minute=30))
            dt_end = datetime.combine(td, datetime.min.time().replace(hour=15))

            day_dropped = 0
            for row in group.itertuples(index=False):
                row = row._asdict()  # namedtuple → dict; 比 iterrows (pd.Series) 快 50-100x
                ts_code = str(row["ts_code"])
                close_val = row.get("close", 0)
                if pd.isna(close_val) or close_val <= 0:
                    day_dropped += 1
                    continue

                # 构建 metadata（传入 trade_date 用于 PIT 强赎状态查询）
                metadata = self._build_metadata(row, trade_date_str=str(trade_date_str))

                open_val = self._safe_float(row.get("open", close_val))
                vol_val = self._safe_float(row.get("vol", 0))

                # 停牌检测：open=0 且 volume=0 表示当日无交易（停牌/暂停交易）
                # TuShare 数据中停牌日 close 仍保留最近收盘价，但 open=0, vol=0
                is_suspended = (open_val <= 0 or vol_val <= 0) and close_val > 0

                # 前收盘价（用于 2022 新规涨跌停检查）
                raw_prev_close = row.get("prev_close")
                prev_close_val: float | None = None
                if raw_prev_close is not None and not pd.isna(raw_prev_close):
                    prev_close_val = self._safe_float(raw_prev_close)
                    if prev_close_val <= 0:
                        prev_close_val = None

                bar = Bar(
                    symbol=ts_code,
                    start_ts=dt_start,
                    end_ts=dt_end,
                    open=open_val,
                    high=self._safe_float(row.get("high", close_val)),
                    low=self._safe_float(row.get("low", close_val)),
                    close=self._safe_float(close_val),
                    volume=vol_val,
                    prev_close=prev_close_val,
                    is_suspended=is_suspended,
                    metadata=metadata,
                )
                bars[ts_code] = bar

            if bars:
                bars_by_date[td] = bars
            if day_dropped > 0:
                logger.debug(
                    f"{td}: 剔除 {day_dropped} 条记录 (close<=0 或 NaN)，"
                    f"保留 {len(bars)} 只"
                )

        # 强赎状态来源统计
        n_call, n_heuristic, n_none = 0, 0, 0
        for day_bars in bars_by_date.values():
            for bar in day_bars.values():
                fr_status = (bar.metadata or {}).get("force_redeem_status", "")
                if "公告" in fr_status or "强赎" in fr_status and "启发式" not in fr_status:
                    n_call += 1
                elif "启发式" in fr_status:
                    n_heuristic += 1
                else:
                    n_none += 1
        logger.info(
            f"强赎状态来源统计: 公告={n_call}, 启发式={n_heuristic}, 无={n_none}"
        )

        # 数据加载质量汇总
        total_bars = sum(len(v) for v in bars_by_date.values())
        logger.info(
            f"转换完成: {len(bars_by_date)} 个交易日, "
            f"{total_bars} 条有效 Bar 记录"
        )
        return bars_by_date

    def _quality_check_and_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """对合并后的 DataFrame 执行数据质量检查与清洗。

        检查项：
        - 重复记录（同一 trade_date + ts_code）
        - 排序一致性（按 trade_date 升序）
        - 关键字段缺失率统计（cb_value, cb_over_rate, remain_size）

        Args:
            df: 合并后的行情 + 基础信息 DataFrame

        Returns:
            去重、排序后的 DataFrame
        """
        n_before = len(df)

        # 去重
        if "trade_date" in df.columns and "ts_code" in df.columns:
            df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
            n_deduped = n_before - len(df)
            if n_deduped > 0:
                logger.warning(f"数据去重: 移除 {n_deduped} 条重复记录 (trade_date+ts_code)")

        # 排序
        if "trade_date" in df.columns:
            df = df.sort_values("trade_date").reset_index(drop=True)

        # 关键字段缺失率
        quality_fields = ["cb_value", "cb_over_rate", "remain_size", "close", "vol"]
        missing_stats = {}
        for field in quality_fields:
            if field in df.columns:
                n_missing = df[field].isna().sum()
                if n_missing > 0:
                    pct = n_missing / len(df) * 100
                    missing_stats[field] = f"{n_missing}({pct:.1f}%)"

        if missing_stats:
            detail = ", ".join(f"{k}={v}" for k, v in missing_stats.items())
            logger.info(f"字段缺失统计: {detail}")

        # remain_size PIT 偏差警告
        if "remain_size" in df.columns:
            logger.debug(
                "remain_size 来自 cb_basic (最新快照), 用于历史回测存在前视偏差。"
                "请参考 docs/known_limitations.md"
            )

        return df

    def _load_force_redeem_data(self) -> pd.DataFrame:
        """加载并预处理强赎公告数据，用于 PIT 查询。

        Returns:
            强赎公告 DataFrame（仅 call_type='强赎'），按 ann_date 升序排列。
            如加载失败或无数据，返回空 DataFrame。
        """
        try:
            cb_call = self.provider.load_cb_call(use_cache=True)
        except Exception as e:
            logger.warning(
                f"加载强赎公告数据失败（降级为无强赎数据模式）: {e}，"
                "降级为 cb_value≥130 启发式判断，强赎状态不可靠"
            )
            return pd.DataFrame()

        if cb_call is None or cb_call.empty:
            logger.warning("无强赎公告数据，将使用 cb_value 启发式判断")
            return pd.DataFrame()

        # 只保留强赎类型
        force_redeem = cb_call[cb_call["call_type"] == "强赎"].copy()
        if force_redeem.empty:
            return pd.DataFrame()

        # 确保 ann_date 为字符串且非空
        force_redeem = force_redeem[
            force_redeem["ann_date"].notna()
            & (force_redeem["ann_date"] != "")
        ].copy()
        force_redeem["ann_date"] = force_redeem["ann_date"].astype(str)

        # 按 ann_date 升序排列，便于后续取 <= 某日的最新记录
        force_redeem = force_redeem.sort_values("ann_date").reset_index(drop=True)

        logger.info(f"强赎公告数据加载完成: {len(force_redeem)} 条记录")
        return force_redeem

    def _build_force_redeem_index(
        self,
    ) -> dict[str, tuple[list[str], list[str], list[bool]]]:
        """预建强赎数据索引，按 ts_code 分组，每组按 ann_date 排序。

        将 DataFrame 全表扫描转化为 dict + bisect 查询，
        单次查询从 O(n) 降至 O(log k)（k 为单只转债的公告数）。

        Returns:
            {ts_code: (ann_dates_sorted, statuses, is_called_flags)}
        """
        if self._force_redeem_data is None or self._force_redeem_data.empty:
            return {}

        raw: dict[str, list[tuple[str, str, bool]]] = {}
        for fr_row in self._force_redeem_data.itertuples(index=False):
            ts_code = str(fr_row.ts_code)
            ann_date = str(fr_row.ann_date)
            status = str(getattr(fr_row, "is_call", "")).strip()
            is_called = status == "公告实施强赎"

            if ts_code not in raw:
                raw[ts_code] = []
            raw[ts_code].append((ann_date, status, is_called))

        # 拆分为平行列表（DataFrame 已按 ann_date 升序排列）
        index: dict[str, tuple[list[str], list[str], list[bool]]] = {}
        for ts_code, entries in raw.items():
            index[ts_code] = (
                [e[0] for e in entries],
                [e[1] for e in entries],
                [e[2] for e in entries],
            )

        logger.debug(f"强赎预索引构建完成: {len(index)} 只转债")
        return index

    def _get_force_redeem_status(
        self, ts_code: str, trade_date: str,
    ) -> dict:
        """获取指定转债在指定日期的 PIT 强赎状态。

        使用预建索引 + bisect 二分查找，O(log k) 时间复杂度。

        Args:
            ts_code: 转债代码
            trade_date: 交易日期 YYYYMMDD

        Returns:
            {"is_called": bool | None, "force_redeem_status": str}
        """
        if not self._force_redeem_index:
            return {"is_called": None, "force_redeem_status": ""}

        entry = self._force_redeem_index.get(ts_code)
        if not entry:
            return {"is_called": None, "force_redeem_status": ""}

        ann_dates, statuses, flags = entry
        # bisect_right 找到 ann_date <= trade_date 的最后一个位置
        idx = bisect.bisect_right(ann_dates, trade_date) - 1

        if idx < 0:
            return {"is_called": None, "force_redeem_status": ""}

        return {
            "is_called": flags[idx],
            "force_redeem_status": statuses[idx],
        }

    def _build_metadata(self, row, trade_date_str: str = "") -> dict:
        """从 DataFrame 行构建 Bar.metadata

        Args:
            row: 合并后的 DataFrame 行（dict 或 pd.Series）
            trade_date_str: 交易日期 YYYYMMDD，用于 PIT 强赎状态查询
        """
        meta: dict = {}

        # 转股价值
        cb_value = row.get("cb_value")
        if cb_value is not None and not pd.isna(cb_value):
            meta["cb_value"] = float(cb_value)

        # 转股溢价率
        cb_over_rate = row.get("cb_over_rate")
        if cb_over_rate is not None and not pd.isna(cb_over_rate):
            meta["cb_over_rate"] = float(cb_over_rate)

        # 剩余规模（万元，已在 load() 中从元转换）
        remain_size = row.get("remain_size")
        if remain_size is not None and not pd.isna(remain_size):
            meta["remain_size"] = float(remain_size)

        # 成交额（万元）
        amount = row.get("amount")
        if amount is not None and not pd.isna(amount):
            meta["amount"] = float(amount)

        # 正股简称
        stk_short_name = row.get("stk_short_name")
        if stk_short_name is not None and not pd.isna(stk_short_name):
            meta["stk_short_name"] = str(stk_short_name)

        # 转债简称
        bond_short_name = row.get("bond_short_name")
        if bond_short_name is not None and not pd.isna(bond_short_name):
            meta["bond_short_name"] = str(bond_short_name)

        # 到期日期
        maturity_date = row.get("maturity_date")
        if maturity_date is not None and not pd.isna(maturity_date):
            meta["maturity_date"] = str(maturity_date).replace("-", "")[:8]

        # 摘牌日期
        delist_date = row.get("delist_date")
        if delist_date is not None and not pd.isna(delist_date):
            meta["delist_date"] = str(delist_date).replace("-", "")[:8]

        # 正股代码
        stk_code = row.get("stk_code")
        if stk_code is not None and not pd.isna(stk_code):
            meta["stk_code"] = str(stk_code)

        # 发行规模（万元，已在 load() 中从元转换）
        issue_size = row.get("issue_size")
        if issue_size is not None and not pd.isna(issue_size):
            meta["issue_size"] = float(issue_size)

        # 上市日期
        list_date = row.get("list_date")
        if list_date is not None and not pd.isna(list_date):
            meta["list_date"] = str(list_date).replace("-", "")[:8]

        # 信用评级（如 AAA, AA+, AA, AA-, A+, A 等）
        rating = row.get("rating")
        if rating is not None and not pd.isna(rating):
            meta["rating"] = str(rating).strip()

        # 转股价（当前有效转股价，元），来自 cb_basic
        conv_price = row.get("conv_price")
        if conv_price is not None and not pd.isna(conv_price):
            try:
                meta["conv_price"] = float(conv_price)
            except (TypeError, ValueError):
                pass

        # 回售条款（原始文本），来自 cb_basic
        put_clause = row.get("put_clause")
        if put_clause is not None and not pd.isna(put_clause):
            clause_str = str(put_clause).strip()
            if clause_str:
                meta["put_clause"] = clause_str

        # 强赎状态（is_called）—— PIT 安全
        # 来源优先级：
        #   1. 行数据中已有 force_redeem_status（上游直接注入，如 cb_basic 或手动标记）
        #   2. cb_call 公告数据（PIT 安全查询）
        #   3. cb_value 启发式判断（降级方案）
        ts_code = str(row.get("ts_code", ""))
        trade_date = trade_date_str or str(row.get("trade_date", ""))

        # 优先检查行数据中的 force_redeem_status（向后兼容旧数据或直接注入）
        force_redeem_status = row.get("force_redeem_status")
        if force_redeem_status is not None and not pd.isna(force_redeem_status):
            status_str = str(force_redeem_status).strip()
            if status_str:
                meta["is_called"] = status_str in (
                    "已公告强赎", "公告强赎", "强赎中", "赎回中",
                    "公告实施强赎", "已满足强赎条件", "已触发强赎",
                )
                meta["force_redeem_status"] = status_str
                return meta

        # cb_call PIT 查询（使用预建索引，O(log k) 查找）
        if ts_code and trade_date and self._force_redeem_index:
            fr_status = self._get_force_redeem_status(ts_code, trade_date)
            if fr_status["force_redeem_status"]:
                meta["is_called"] = fr_status["is_called"]
                meta["force_redeem_status"] = fr_status["force_redeem_status"]
                return meta

        # 降级：基于 cb_value 的启发式判断
        cb_val = meta.get("cb_value")
        if cb_val is not None and cb_val >= 130.0:
            meta["is_called"] = None  # 可能触发但未确认
            meta["force_redeem_status"] = f"启发式: cb_value={cb_val:.2f}≥130"
        else:
            meta["is_called"] = False

        return meta

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        """安全转换为 float"""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        """解析日期字符串"""
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str.strip()[:10], fmt).date()
            except ValueError:
                continue
        return None
