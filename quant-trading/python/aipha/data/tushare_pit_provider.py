"""TuShare Pro 点时(PIT)财务数据提供者

实现 PITFinancialProvider 接口，对接 TuShare Pro 的财务报表 API：
- fina_indicator: 财务指标（ROE/ROA/毛利率/增长率等）
- income: 利润表（营收/净利润/扣非净利等）
- balancesheet: 资产负债表（总资产/净资产/货币资金等）
- cashflow: 现金流量表（经营/投资/筹资现金流）

PIT 核心原则：
- 使用 max(ann_date, f_ann_date) 判断报表可用时点，实际公告日为空时用 ann_date
- 对给定 query_date，选择已可得的最新报告期及该期最新可得修订
- 避免前视偏差：因子计算只能看到"当时已公告"的财报

缓存策略：
- 按个股缓存财务指标（首次全量 → 增量更新）
- 提供 preload_batch() 批量预下载，避免策略运行时逐条查询

数据口径：
- ROE: roe（摊薄）或 roe_waa（加权）
- 毛利率: grossprofit_margin（百分比）
- 增长率: revenue_yoy / dt_netprofit_yoy（百分比，同比）

设计原则（对齐 copilot-instructions.md §7.1）：
- 不 Mock 核心业务对象，使用真实数据结构
- 通过 Provider 接口隔离第三方 IO
"""

from __future__ import annotations

import logging
import time
from datetime import date
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal
from typing import Any

import pandas as pd

from aipha.data.pit_financial import (
    FinancialFields,
    FinancialIndicator,
    FinancialStatement,
    PITFinancialProvider,
    ReportDate,
    ReportPeriod,
    latest_available_financial,
)

logger = logging.getLogger(__name__)


def _end_date_to_report_date(end_date_str: str) -> ReportDate:
    """将 TuShare end_date (YYYYMMDD) 转为 ReportDate。

    规则：
    - 0331 → Q1, 0630 → Q2, 0930 → Q3, 1231 → Q4
    """
    year = int(end_date_str[:4])
    mmdd = end_date_str[4:8]
    period_map = {
        "0331": ReportPeriod.Q1,
        "0630": ReportPeriod.Q2,
        "0930": ReportPeriod.Q3,
        "1231": ReportPeriod.Q4,
    }
    period = period_map.get(mmdd, ReportPeriod.Q4)
    return ReportDate(year=year, period=period)


def _parse_date(s: str | None) -> date | None:
    """安全解析日期字符串 YYYYMMDD -> date。"""
    if not s or not isinstance(s, str) or len(s) < 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, TypeError):
        return None


def _to_decimal(val: Any) -> Decimal | None:
    """安全转 Decimal。"""
    if val is None or (isinstance(val, float) and (val != val)):  # NaN check
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _announcement_date(row) -> date | None:
    """Actual publication cannot make a filing visible before either date."""
    nominal = row.get("ann_date", "")
    announced = nominal.date() if isinstance(nominal, pd.Timestamp) else (
        nominal if isinstance(nominal, date) else _parse_date(str(nominal))
    )
    actual = row.get("f_ann_date")
    if actual is None or pd.isna(actual) or str(actual).strip() == "":
        return announced
    parsed = actual.date() if isinstance(actual, pd.Timestamp) else (
        actual if isinstance(actual, date) else _parse_date(str(actual))
    )
    if parsed is None:
        raise ValueError("invalid financial f_ann_date")
    return max(announced, parsed) if announced else parsed


def _source_report_date(end_date: str) -> ReportDate:
    parsed = _parse_date(end_date)
    if parsed is None or parsed.strftime("%m%d") not in {"0331", "0630", "0930", "1231"}:
        raise ValueError("financial end_date must be a valid quarter end")
    return _end_date_to_report_date(end_date)


def _validate_records(records, *, conflict_policy="raise", audit=None, dataset=""):
    """Refuse conflicting versions without a distinct availability date."""
    if conflict_policy not in {"raise", "quarantine"}:
        raise ValueError("financial conflict_policy must be raise or quarantine")
    unique = {}
    for record in records:
        if record.announce_date < record.report_date.to_date():
            raise ValueError("financial announcement cannot precede report period end")
        key = (record.report_date.to_date(), record.announce_date)
        if conflict_policy == "quarantine":
            unique.setdefault(key, []).append(record)
            continue
        if key in unique and record != unique[key]:
            raise ValueError("conflicting financial revisions share the same report and announcement date")
        unique[key] = record
    if conflict_policy == "quarantine":
        resolved = {}
        for key, group in sorted(unique.items()):
            field = "indicators" if isinstance(group[0], FinancialIndicator) else "data"
            observations = [getattr(record, field) for record in group]
            values = {}
            for metric in sorted(set().union(*(row.keys() for row in observations))):
                candidates = {row.get(metric) for row in observations}
                if len(candidates) > 1:
                    # Keep an explicit null event at the original availability
                    # date. Dropping it would carry a stale prior value forward.
                    values[metric] = None
                    if audit is not None:
                        audit.append({
                            "dataset": dataset, "symbol": group[0].symbol,
                            "report_date": key[0].isoformat(), "announce_date": key[1].isoformat(),
                            "metric_name": metric, "action": "missing",
                            "source_values": sorted("null" if value is None else str(value) for value in candidates),
                        })
                else:
                    values[metric] = next(iter(candidates))
            resolved[key] = replace(group[0], **{field: values})
        unique = resolved
    return sorted(unique.values(), key=lambda record: (record.announce_date, record.report_date.to_date()))


class TuSharePITProvider(PITFinancialProvider):
    """TuShare Pro PIT 财务数据提供者

    通过 TuShareEquityProvider 获取原始财务数据，
    转为 PIT 接口的 FinancialStatement / FinancialIndicator。

    Args:
        equity_provider: TuShareEquityProvider 实例（用于调 API + 缓存）
    """

    def __init__(
        self, equity_provider: Any, *, conflict_policy: str = "raise",
        history_start: str | None = None, history_end: str | None = None,
    ) -> None:
        if conflict_policy not in {"raise", "quarantine"}:
            raise ValueError("financial conflict_policy must be raise or quarantine")
        self._provider = equity_provider
        self.conflict_policy = conflict_policy
        self._history_kwargs = {}
        for key, value in (("start_date", history_start), ("end_date", history_end)):
            if value is not None:
                try:
                    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
                        raise ValueError("expected YYYYMMDD")
                    date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
                except ValueError as exc:
                    raise ValueError(f"invalid financial history {key}") from exc
                self._history_kwargs[key] = value
        if history_start and history_end and history_start > history_end:
            raise ValueError("financial history start_date must not exceed end_date")
        self._financial_conflicts: list[dict[str, Any]] = []
        # 内存缓存：{ts_code: [FinancialIndicator, ...]} 已按 ann_date 排序
        self._indicator_cache: dict[str, list[FinancialIndicator]] = {}
        # 内存缓存：{(ts_code, statement_type): [FinancialStatement, ...]}
        self._statement_cache: dict[tuple[str, str], list[FinancialStatement]] = {}

    def get_data_quality_report(self, *, end_date: date | None = None) -> dict[str, Any]:
        """Disclose quarantined metrics without exposing future events."""
        events = [deepcopy(event) for event in self._financial_conflicts
                  if end_date is None or date.fromisoformat(event["announce_date"]) <= end_date]
        events.sort(key=lambda row: (row["symbol"], row["announce_date"], row["report_date"], row["dataset"], row["metric_name"]))
        counts: dict[str, int] = {}
        for event in events:
            metric = f"{event['dataset']}.{event['metric_name']}"
            counts[metric] = counts.get(metric, 0) + 1
        return {"policy": self.conflict_policy, "history_request": dict(self._history_kwargs),
                "quarantined_metric_events": len(events),
                "affected_symbols": len({event["symbol"] for event in events}),
                "by_metric": dict(sorted(counts.items())), "events": events}

    # ------------------------------------------------------------------
    # PITFinancialProvider 接口实现
    # ------------------------------------------------------------------

    def get_statement_pit(
        self,
        symbol: str,
        query_date: date,
        statement_type: str,
    ) -> FinancialStatement | None:
        """获取 query_date 时可用的最新财务报表"""
        key = (symbol, statement_type)
        if key not in self._statement_cache:
            self._load_statements(symbol, statement_type)

        statements = self._statement_cache.get(key, [])
        # 找 ann_date <= query_date 的最新报表
        return latest_available_financial(statements, query_date)

    def get_indicator_pit(
        self,
        symbol: str,
        query_date: date,
        indicator_names: list[str],
    ) -> FinancialIndicator | None:
        """获取 query_date 时可用的最新财务指标"""
        if symbol not in self._indicator_cache:
            self._load_indicators(symbol)

        indicators = self._indicator_cache.get(symbol, [])
        available = [i for i in indicators if i.announce_date <= query_date]
        if not available:
            return None

        latest = latest_available_financial(available, query_date)

        # 过滤请求的指标
        filtered = {
            k: v for k, v in latest.indicators.items()
            if k in indicator_names
        }

        return FinancialIndicator(
            symbol=symbol,
            report_date=latest.report_date,
            announce_date=latest.announce_date,
            indicators=filtered,
        )

    def get_indicator_history(self, symbol: str) -> list[FinancialIndicator]:
        """Return the symbol's immutable announcement-dated indicator history.

        Consumers must still join by ``announce_date <= trade_date``. This
        method exposes source rows for snapshot materialization without
        bypassing the provider's cache and TuShare normalization.
        """
        if symbol not in self._indicator_cache:
            self._load_indicators(symbol)
        return list(self._indicator_cache.get(symbol, []))

    def get_statement_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        statement_type: str,
    ) -> list[FinancialStatement]:
        """获取历史报表（按公告日期过滤）"""
        key = (symbol, statement_type)
        if key not in self._statement_cache:
            self._load_statements(symbol, statement_type)

        statements = self._statement_cache.get(key, [])
        return [
            s for s in statements
            if start_date <= s.announce_date <= end_date
        ]

    # ------------------------------------------------------------------
    # 扩展接口：批量预加载
    # ------------------------------------------------------------------

    def preload_batch(
        self,
        symbols: list[str],
        delay: float = 0.3,
    ) -> None:
        """批量预加载财务指标数据

        适用于回测前预下载所有股票的财务数据。

        Args:
            symbols: 股票代码列表
            delay: 每次 API 调用间隔（秒），遵守限流
        """
        total = len(symbols)
        for i, sym in enumerate(symbols):
            if sym in self._indicator_cache:
                continue
            try:
                self._load_indicators(sym)
                if delay > 0:
                    time.sleep(delay)
            except Exception as e:
                logger.warning(f"预加载失败 ({i+1}/{total}): {sym} - {e}")
            if (i + 1) % 100 == 0:
                logger.info(f"预加载进度: {i+1}/{total}")

        logger.info(f"预加载完成: {total} 只股票")

    def preload_statements_batch(
        self,
        symbols: list[str],
        statement_type: str = "income",
        delay: float = 0.3,
    ) -> None:
        """批量预加载报表数据"""
        total = len(symbols)
        for i, sym in enumerate(symbols):
            key = (sym, statement_type)
            if key in self._statement_cache:
                continue
            try:
                self._load_statements(sym, statement_type)
                if delay > 0:
                    time.sleep(delay)
            except Exception as e:
                logger.warning(f"报表预加载失败 ({i+1}/{total}): {sym} - {e}")
            if (i + 1) % 100 == 0:
                logger.info(f"报表预加载进度: {i+1}/{total}")

    # ------------------------------------------------------------------
    # 派生查询：年度同比
    # ------------------------------------------------------------------

    def get_yoy_growth(
        self,
        symbol: str,
        query_date: date,
        metric: str = "dt_netprofit_yoy",
    ) -> Decimal | None:
        """获取同比增长率指标（PIT）

        常用 metric:
        - revenue_yoy: 营收同比增长率
        - dt_netprofit_yoy: 扣非净利润同比增长率
        - net_profit_yoy: 净利润同比增长率
        - op_yoy: 营业利润同比增长率
        """
        indicator = self.get_indicator_pit(symbol, query_date, [metric])
        if indicator is None:
            return None
        return indicator.indicators.get(metric)

    def get_quality_metrics(
        self,
        symbol: str,
        query_date: date,
    ) -> dict[str, Decimal | None]:
        """获取一组质量指标（PIT）

        Returns:
            {roe, roa, grossprofit_margin, netprofit_margin, current_ratio}
        """
        names = [
            FinancialFields.ROE,
            FinancialFields.ROA,
            FinancialFields.GROSS_MARGIN,
            FinancialFields.NET_MARGIN,
            "current_ratio",
        ]
        indicator = self.get_indicator_pit(symbol, query_date, names)
        if indicator is None:
            return {n: None for n in names}
        return {n: indicator.indicators.get(n) for n in names}

    # ------------------------------------------------------------------
    # TS-1 Stage 2: cashflow PIT (R156)
    # ------------------------------------------------------------------

    def get_cashflow_pit(
        self,
        symbol: str,
        query_date: date,
        fields: list[str] | None = None,
    ) -> dict[str, Decimal | None]:
        """PIT-aligned cashflow statement for FCF / accrual factors.

        Returns dict with mapped field names (see _STATEMENT_FIELD_MAP):
          cash_from_operations, cash_from_investing, cash_from_financing,
          capex, c_fr_sale_sg, c_paid_goods_s

        If *fields* is given, only those keys are returned.
        """
        stmt = self.get_statement_pit(symbol, query_date, "cashflow")
        if stmt is None:
            return {} if fields is None else {f: None for f in fields}

        data = stmt.data  # dict[str, Decimal]
        if fields is not None:
            return {f: data.get(f) for f in fields}
        return dict(data)

    # ------------------------------------------------------------------
    # 内部数据加载
    # ------------------------------------------------------------------

    def _load_indicators(self, symbol: str) -> None:
        """从 TuShareEquityProvider 加载 fina_indicator 并缓存"""
        df = self._provider.load_fina_indicator(symbol, **self._history_kwargs)
        if df is None or df.empty:
            self._indicator_cache[symbol] = []
            return

        indicators: list[FinancialIndicator] = []
        for _, row in df.iterrows():
            ann_date = _announcement_date(row)
            end_date = str(row.get("end_date", ""))

            if ann_date is None or not end_date or len(end_date) < 8:
                continue

            report_date = _source_report_date(end_date)

            ind_data: dict[str, Decimal] = {}
            for field_name in [
                "roe", "roe_waa", "roa",
                "grossprofit_margin", "netprofit_margin",
                "profit_to_gr",
                "revenue_yoy", "net_profit_yoy", "dt_netprofit_yoy",
                "op_yoy",
                "eps", "bps", "cfps",
                "current_ratio", "quick_ratio",
                "debt_to_assets",
                # TS-1 Stage 1 expansion (R156)
                "or_yoy", "netprofit_yoy", "assets_yoy",
                "ar_turn", "inv_turn", "roic", "ocf_to_or",
                "basic_eps_yoy", "q_sales_yoy", "q_roe",
                "ocfps", "assets_turn",
            ]:
                val = _to_decimal(row.get(field_name))
                if val is not None:
                    # 统一字段映射到 FinancialFields 常量
                    mapped_name = _FINA_FIELD_MAP.get(field_name, field_name)
                    ind_data[mapped_name] = val

            indicators.append(FinancialIndicator(
                symbol=symbol,
                report_date=report_date,
                announce_date=ann_date,
                indicators=ind_data,
            ))

        # 按 ann_date 排序
        self._indicator_cache[symbol] = _validate_records(
            indicators, conflict_policy=self.conflict_policy,
            audit=self._financial_conflicts, dataset="fina_indicator",
        )

    def _load_statements(self, symbol: str, statement_type: str) -> None:
        """加载报表数据并转为 FinancialStatement"""
        loader_map = {
            "income": self._provider.load_income,
            "balance": self._provider.load_balancesheet,
            "cashflow": self._provider.load_cashflow,
        }

        loader = loader_map.get(statement_type)
        if loader is None:
            logger.warning(f"未知报表类型: {statement_type}")
            self._statement_cache[(symbol, statement_type)] = []
            return

        df = loader(symbol, **self._history_kwargs)
        if df is None or df.empty:
            self._statement_cache[(symbol, statement_type)] = []
            return

        statements: list[FinancialStatement] = []
        for _, row in df.iterrows():
            # Consolidated cumulative filings and their restatements only.
            # Parent-only and single-quarter forms have different semantics.
            report_type = pd.to_numeric(row.get("report_type"), errors="coerce")
            if pd.isna(report_type):
                raise ValueError("financial statements require report_type")
            if report_type not in {1, 4}:
                continue
            ann_date = _announcement_date(row)
            end_date = str(row.get("end_date", ""))

            if ann_date is None or not end_date or len(end_date) < 8:
                continue

            report_date = _source_report_date(end_date)

            # 将所有数值列放入 data
            stmt_data: dict[str, Decimal] = {}
            for col in df.columns:
                if col in ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                           "comp_type", "end_type", "update_flag"):
                    continue
                val = _to_decimal(row.get(col))
                if val is not None:
                    mapped = _STATEMENT_FIELD_MAP.get(col, col)
                    stmt_data[mapped] = val

            statements.append(FinancialStatement(
                symbol=symbol,
                report_date=report_date,
                announce_date=ann_date,
                statement_type=statement_type,
                data=stmt_data,
            ))

        self._statement_cache[(symbol, statement_type)] = _validate_records(
            statements, conflict_policy=self.conflict_policy,
            audit=self._financial_conflicts, dataset=statement_type,
        )


# ---------------------------------------------------------------------------
# TuShare 字段 → FinancialFields 映射
# ---------------------------------------------------------------------------

_FINA_FIELD_MAP: dict[str, str] = {
    "roe": FinancialFields.ROE,
    "roe_waa": "roe_waa",  # 保留原名
    "roa": FinancialFields.ROA,
    "grossprofit_margin": FinancialFields.GROSS_MARGIN,
    "netprofit_margin": FinancialFields.NET_MARGIN,
    "profit_to_gr": "profit_to_gr",
    "revenue_yoy": "revenue_yoy",
    "net_profit_yoy": "net_profit_yoy",
    "dt_netprofit_yoy": "dt_netprofit_yoy",
    "op_yoy": "op_yoy",
    "eps": "eps",
    "bps": "bps",
    "cfps": "cfps",
    "current_ratio": "current_ratio",
    "quick_ratio": "quick_ratio",
    "debt_to_assets": "debt_to_assets",
    # TS-1 Stage 1 expansion (R156)
    "or_yoy": "or_yoy",
    "netprofit_yoy": "netprofit_yoy",
    "assets_yoy": "assets_yoy",
    "ar_turn": "ar_turn",
    "inv_turn": "inv_turn",
    "roic": "roic",
    "ocf_to_or": "ocf_to_or",
    "basic_eps_yoy": "basic_eps_yoy",
    "q_sales_yoy": "q_sales_yoy",
    "q_roe": "q_roe",
    "ocfps": "ocfps",
    "assets_turn": "assets_turn",
}

_STATEMENT_FIELD_MAP: dict[str, str] = {
    # 利润表
    "revenue": FinancialFields.REVENUE,
    "operate_profit": FinancialFields.OPERATING_PROFIT,
    "n_income": FinancialFields.NET_PROFIT,
    "n_income_attr_p": FinancialFields.NET_PROFIT,  # 归母净利优先
    "total_cogs": "total_cogs",
    "oper_cost": "oper_cost",
    "sell_exp": "sell_exp",
    "admin_exp": "admin_exp",
    "fin_exp": "fin_exp",
    # 资产负债表
    "total_assets": FinancialFields.TOTAL_ASSETS,
    "total_liab": FinancialFields.TOTAL_LIABILITIES,
    "total_hldr_eqy_exc_min_int": FinancialFields.TOTAL_EQUITY,
    "total_hldr_eqy_inc_min_int": "total_equity_inc_minority",
    "money_cap": FinancialFields.CASH,
    "accounts_receiv": "accounts_receiv",
    "inventories": "inventories",
    "total_cur_assets": "total_cur_assets",
    "total_nca": "total_nca",
    # 现金流量表
    "n_cashflow_act": FinancialFields.CASH_FROM_OPERATIONS,
    "n_cashflow_inv_act": FinancialFields.CASH_FROM_INVESTING,
    "n_cash_flows_fnc_act": FinancialFields.CASH_FROM_FINANCING,
    "c_fr_sale_sg": "c_fr_sale_sg",
    "c_paid_goods_s": "c_paid_goods_s",
    # TS-1 Stage 2 (R156): capex for FCF
    "c_pay_acq_const_fiolta": "capex",
}
