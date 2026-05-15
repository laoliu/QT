#!/usr/bin/env python3
"""
Institution Activity Analyzer — proxy institutional behavior for A-share stocks.

This script does not attempt to identify named quant firms directly. Instead it
combines public proxy signals such as dragon tiger list, fund flow, northbound
context, block trades, and kline-based pattern analysis to estimate whether a
stock looks like genuine accumulation or bait/distribution.

Actions:
  scan   — scan the market or a supplied stock list
  detail — drill into a single symbol with evidence timeline
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any


# Proxy bypass for Chinese data hosts.
for _k in (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests
    import requests.utils
    import requests.adapters

    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]

    # Patch requests.Session.send to inject a default timeout (30s) so that
    # AkShare API calls that hang on socket read don't block the entire scan.
    # AkShare passes timeout=None explicitly, so we must override None too.
    _original_session_send = requests.Session.send

    def _timeout_send(self, request, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = 30
        return _original_session_send(self, request, **kwargs)

    requests.Session.send = _timeout_send  # type: ignore[assignment]
except ImportError:
    pass


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

from data_providers.akshare_provider import get_klines
from engines.chip_distribution import fetch_chip_distribution, score_chip_distribution
from engines.dragon_tiger import run_dragon_tiger
from engines.fund_flow import run_fund_flow
from scripts.investment_planner import (  # type: ignore[attr-defined]
    _compute_technicals_from_klines,
    _detect_manipulation_patterns,
    _fetch_tencent_batch,
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        num = float(val)
        if num != num:
            return default
        return num
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _clean_text(val: Any) -> str:
    text = str(val or "").strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _compact_to_display(date_str: str) -> str:
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def _latest_trade_date() -> datetime:
    now = datetime.now()
    if now.weekday() == 5:
        return now - timedelta(days=1)
    if now.weekday() == 6:
        return now - timedelta(days=2)
    return now


def _parse_date_like(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    for candidate in (normalized[:10], normalized[:19]):
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def _low_freq_window_days(lookback_days: int, minimum: int = 45, maximum: int = 120) -> int:
    return min(max(max(lookback_days, 1) * 5, minimum), maximum)


def _normalize_date(date_str: str | None) -> tuple[str, str]:
    if date_str:
        ds = str(date_str).strip()
        if len(ds) == 10 and "-" in ds:
            compact = ds.replace("-", "")
            return compact, ds
        if len(ds) == 8 and ds.isdigit():
            return ds, _compact_to_display(ds)
    latest = _latest_trade_date().strftime("%Y%m%d")
    return latest, _compact_to_display(latest)


def _normalize_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return []


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _amount_score(amount: float, tiers: list[tuple[float, float]], fallback: float = 2.0) -> float:
    abs_amount = abs(amount)
    for threshold, score in tiers:
        if abs_amount >= threshold:
            return score
    return fallback


def _clip_score(score: float) -> float:
    return round(max(0.0, min(score, 100.0)), 1)

def _threshold_config(config: dict[str, Any]) -> dict[str, float | int]:
    """Normalize user-configurable thresholds for pre-accumulation/rush detection."""
    pre_days = max(5, min(30, _safe_int(config.get("pre_accum_days"), 10)))
    pre_count = max(1, min(pre_days, _safe_int(config.get("pre_accum_count_min"), 2)))
    pre_vol_ratio = max(1.0, min(5.0, _safe_float(config.get("pre_accum_volume_ratio"), 1.5)))
    rush_change = max(1.0, min(20.0, _safe_float(config.get("rush_change_pct"), 5.0)))
    rush_vol_ratio = max(1.0, min(10.0, _safe_float(config.get("rush_volume_ratio"), 2.0)))
    rush_turnover = max(0.0, min(50.0, _safe_float(config.get("rush_turnover_pct"), 3.0)))
    kline_eval_limit = max(5, min(60, _safe_int(config.get("kline_eval_limit"), 20)))
    return {
        "pre_accum_days": pre_days,
        "pre_accum_count_min": pre_count,
        "pre_accum_volume_ratio": pre_vol_ratio,
        "rush_change_pct": rush_change,
        "rush_volume_ratio": rush_vol_ratio,
        "rush_turnover_pct": rush_turnover,
        "kline_eval_limit": kline_eval_limit,
    }


def _make_event(
    *,
    symbol: str,
    trade_date: str,
    source: str,
    source_label: str,
    event_type: str,
    evidence_type: str,
    direction: str,
    title: str,
    detail: str,
    score_impact: float,
    entity_type: str = "proxy_institution",
    entity_name: str = "公开代理信号",
    amount: float | None = None,
    net_amount: float | None = None,
    premium_discount: float | None = None,
    turnover_rate: float | None = None,
    raw_source_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "tradeDate": trade_date,
        "source": source,
        "sourceLabel": source_label,
        "eventType": event_type,
        "evidenceType": evidence_type,
        "direction": direction,
        "title": title,
        "detail": detail,
        "scoreImpact": round(score_impact, 1),
        "entityType": entity_type,
        "entityName": entity_name,
        "amount": amount,
        "netAmount": net_amount,
        "premiumDiscount": premium_discount,
        "turnoverRate": turnover_rate,
        "rawSourceFields": raw_source_fields or {},
    }


def _fallback_name(symbol: str, events: list[dict[str, Any]]) -> str:
    for event in events:
        raw = event.get("rawSourceFields") or {}
        name = raw.get("name") or raw.get("证券简称") or raw.get("名称")
        if name:
            return str(name)
    return symbol


def _build_action_tag(build_score: float, bait_score: float, distribution_score: float, confidence: float) -> str:
    total_risk = bait_score + distribution_score
    if total_risk >= 18 and build_score < 12:
        return "高风险钓鱼"
    if build_score >= 24 and bait_score <= 8 and distribution_score <= 8 and confidence >= 55:
        return "可试跟"
    if build_score >= 14 and total_risk < 14:
        return "可观察"
    if total_risk >= 10:
        return "避免追入"
    return "中性观察"


def _follow_window(build_score: float, confidence: float, action_tag: str) -> str:
    if action_tag not in ("可试跟", "可观察"):
        return "观望"
    if build_score >= 24 and confidence >= 60:
        return "3-10日"
    if build_score >= 16 and confidence >= 45:
        return "2-5日"
    return "1-3日"


def _fetch_dragon_tiger_events(date_compact: str, limit: int, warnings: list[str]) -> list[dict[str, Any]]:
    result = run_dragon_tiger({"action": "daily", "date": date_compact, "limit": limit})
    if result.get("error"):
        warnings.append(f"龙虎榜: {result['error']}")
        return []

    events: list[dict[str, Any]] = []
    for stock in result.get("stocks", []):
        code = str(stock.get("code") or "").strip()
        if not code:
            continue
        net_amount = _safe_float(stock.get("netAmount"))
        turnover = _safe_float(stock.get("turnoverRate"))
        score = _amount_score(net_amount, [(5e8, 12), (2e8, 9), (1e8, 7), (5e7, 5)], 3)
        if turnover and turnover <= 8:
            score += 1
        if net_amount > 0:
            events.append(
                _make_event(
                    symbol=code,
                    trade_date=_compact_to_display(date_compact),
                    source="dragon_tiger",
                    source_label="龙虎榜",
                    event_type="dragon_tiger_net_buy",
                    evidence_type="build",
                    direction="buy",
                    title="龙虎榜机构净买",
                    detail=(
                        f"净买额{net_amount / 1e8:.2f}亿，换手{turnover:.1f}%，原因: "
                        f"{str(stock.get('reason') or '未披露')}"
                    ),
                    score_impact=score,
                    entity_type="institution_seat",
                    entity_name="龙虎榜机构席位",
                    amount=_safe_float(stock.get("buyAmount")),
                    net_amount=net_amount,
                    turnover_rate=turnover,
                    raw_source_fields=stock,
                )
            )
        elif net_amount < 0:
            events.append(
                _make_event(
                    symbol=code,
                    trade_date=_compact_to_display(date_compact),
                    source="dragon_tiger",
                    source_label="龙虎榜",
                    event_type="dragon_tiger_net_sell",
                    evidence_type="distribution",
                    direction="sell",
                    title="龙虎榜机构净卖",
                    detail=(
                        f"净卖额{abs(net_amount) / 1e8:.2f}亿，换手{turnover:.1f}%，原因: "
                        f"{str(stock.get('reason') or '未披露')}"
                    ),
                    score_impact=score,
                    entity_type="institution_seat",
                    entity_name="龙虎榜机构席位",
                    amount=_safe_float(stock.get("sellAmount")),
                    net_amount=net_amount,
                    turnover_rate=turnover,
                    raw_source_fields=stock,
                )
            )
    return events


def _fetch_fund_flow_rank_events(limit: int, warnings: list[str]) -> list[dict[str, Any]]:
    result = run_fund_flow({"action": "rank", "limit": limit})
    if result.get("error"):
        warnings.append(f"资金流: {result['error']}")
        return []

    events: list[dict[str, Any]] = []
    today = _compact_to_display(_latest_trade_date().strftime("%Y%m%d"))
    for stock in result.get("stocks", []):
        code = str(stock.get("code") or "").strip()
        if not code:
            continue
        net_amount = _safe_float(stock.get("mainNetIn"))
        net_pct = _safe_float(stock.get("mainNetInPct"))
        score = _amount_score(net_amount, [(20e8, 12), (10e8, 10), (5e8, 8), (2e8, 6)], 4)
        if net_pct >= 8:
            score += 2
        elif net_pct >= 4:
            score += 1

        events.append(
            _make_event(
                symbol=code,
                trade_date=today,
                source="fund_flow_rank",
                source_label="主力资金",
                event_type="main_fund_inflow",
                evidence_type="build",
                direction="buy",
                title="主力资金净流入",
                detail=f"主力净流入{net_amount / 1e8:.2f}亿，净占比{net_pct:.2f}%",
                score_impact=score,
                entity_type="main_fund",
                entity_name="主力资金",
                amount=net_amount,
                net_amount=net_amount,
                raw_source_fields=stock,
            )
        )
    return events


def _fetch_northbound_context(warnings: list[str]) -> dict[str, Any]:
    result = run_fund_flow({"action": "northbound"})
    if result.get("error"):
        warnings.append(f"北向资金: {result['error']}")
        return {
            "northboundNetIn": None,
            "marketTone": "未知",
            "summary": "北向资金数据暂不可用",
            "score": 0.0,
        }

    realtime = result.get("realtime") or {}
    history = result.get("history") or []
    north_net = _safe_float(realtime.get("northTotal"))
    if not north_net and history:
        north_net = _safe_float(history[-1].get("northNetIn"))

    tone = "中性"
    score = 0.0
    if north_net >= 30e8:
        tone = "偏多"
        score = 2.0
    elif north_net >= 10e8:
        tone = "温和偏多"
        score = 1.0
    elif north_net <= -30e8:
        tone = "偏空"
        score = -2.0
    elif north_net <= -10e8:
        tone = "温和偏空"
        score = -1.0

    summary = f"北向资金当日净流{'入' if north_net >= 0 else '出'} {abs(north_net) / 1e8:.2f}亿，市场背景{tone}"
    return {
        "northboundNetIn": round(north_net, 2),
        "marketTone": tone,
        "summary": summary,
        "score": score,
    }


def _fetch_block_trade_daily(date_compact: str, limit: int, warnings: list[str]) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError:
        warnings.append("大宗交易: akshare 未安装")
        return []

    try:
        df = ak.stock_dzjy_mrmx(symbol="A股", start_date=date_compact, end_date=date_compact)
    except Exception as err:
        message = str(err)
        if "'NoneType' object is not subscriptable" in message:
            return []
        warnings.append(f"大宗交易: {err}")
        return []

    if df is None or df.empty:
        return []

    events: list[dict[str, Any]] = []
    sorted_df = df.sort_values(by="成交额", ascending=False).head(limit)
    for _, row in sorted_df.iterrows():
        code = str(row.get("证券代码") or "").strip()
        if not code:
            continue
        amount = _safe_float(row.get("成交额"))
        premium = _safe_float(row.get("折溢率"))
        ratio = _safe_float(row.get("成交额/流通市值"))
        if amount < 3e7:
            continue

        base_score = _amount_score(amount, [(5e8, 10), (2e8, 8), (1e8, 6), (5e7, 5)], 4)
        if ratio >= 1.0:
            base_score += 1

        trade_date = str(row.get("交易日期") or _compact_to_display(date_compact))[:10]
        raw_fields = {
            "name": str(row.get("证券简称") or ""),
            "成交额": amount,
            "折溢率": premium,
            "成交额/流通市值": ratio,
            "买方营业部": str(row.get("买方营业部") or ""),
            "卖方营业部": str(row.get("卖方营业部") or ""),
        }

        if premium >= -1.5:
            evidence_type = "build"
            direction = "buy"
            title = "大宗交易偏强成交"
            detail = f"成交额{amount / 1e8:.2f}亿，折溢率{premium:.2f}%"
        elif premium <= -6.0:
            evidence_type = "bait"
            direction = "sell"
            title = "大宗交易深折价"
            detail = f"成交额{amount / 1e8:.2f}亿，深折价{premium:.2f}%"
            base_score += 1
        else:
            evidence_type = "context"
            direction = "mixed"
            title = "大宗交易中性成交"
            detail = f"成交额{amount / 1e8:.2f}亿，折溢率{premium:.2f}%"
            base_score = max(2.0, base_score - 2.0)

        events.append(
            _make_event(
                symbol=code,
                trade_date=trade_date,
                source="block_trade",
                source_label="大宗交易",
                event_type="block_trade",
                evidence_type=evidence_type,
                direction=direction,
                title=title,
                detail=detail,
                score_impact=base_score,
                entity_type="block_trade_desk",
                entity_name=str(row.get("买方营业部") or "大宗交易席位"),
                amount=amount,
                premium_discount=premium,
                raw_source_fields=raw_fields,
            )
        )
    return events


def _fetch_block_trade_symbol_events(
    symbol: str,
    end_date_compact: str,
    lookback_days: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError:
        warnings.append("大宗交易明细: akshare 未安装")
        return []

    end_dt = datetime.strptime(end_date_compact, "%Y%m%d")
    start_dt = end_dt - timedelta(days=max(lookback_days, 5))
    try:
        df = ak.stock_dzjy_mrmx(
            symbol="A股",
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=end_date_compact,
        )
    except Exception as err:
        warnings.append(f"大宗交易明细: {err}")
        return []

    if df is None or df.empty:
        return []

    subset = df[df["证券代码"].astype(str) == symbol].sort_values(by="交易日期", ascending=False).head(6)
    events: list[dict[str, Any]] = []
    for _, row in subset.iterrows():
        amount = _safe_float(row.get("成交额"))
        premium = _safe_float(row.get("折溢率"))
        evidence_type = "build" if premium >= -1.5 else "bait" if premium <= -6.0 else "context"
        title = "近期大宗交易"
        if evidence_type == "build":
            title = "近期大宗交易偏强"
        elif evidence_type == "bait":
            title = "近期大宗交易深折价"
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=str(row.get("交易日期") or "")[:10],
                source="block_trade_detail",
                source_label="大宗交易",
                event_type="block_trade_recent",
                evidence_type=evidence_type,
                direction="buy" if evidence_type == "build" else "sell" if evidence_type == "bait" else "mixed",
                title=title,
                detail=f"成交额{amount / 1e8:.2f}亿，折溢率{premium:.2f}%",
                score_impact=3.0 if evidence_type == "context" else 5.0,
                entity_type="block_trade_desk",
                entity_name=str(row.get("买方营业部") or "大宗交易席位"),
                amount=amount,
                premium_discount=premium,
                raw_source_fields={
                    "name": str(row.get("证券简称") or ""),
                    "买方营业部": str(row.get("买方营业部") or ""),
                    "卖方营业部": str(row.get("卖方营业部") or ""),
                },
            )
        )
    return events


def _fetch_symbol_fund_flow_detail(symbol: str, warnings: list[str]) -> list[dict[str, Any]]:
    result = run_fund_flow({"action": "stock", "symbol": symbol})
    if result.get("error"):
        warnings.append(f"{symbol} 资金流历史: {result['error']}")
        return []

    flows = result.get("flows") or []
    if not flows:
        return []

    recent_3 = flows[-3:]
    recent_5 = flows[-5:]
    net_3 = sum(_safe_float(item.get("mainNetIn")) for item in recent_3)
    net_5 = sum(_safe_float(item.get("mainNetIn")) for item in recent_5)
    pos_days = sum(1 for item in recent_5 if _safe_float(item.get("mainNetIn")) > 0)
    neg_days = sum(1 for item in recent_5 if _safe_float(item.get("mainNetIn")) < 0)
    latest = recent_3[-1]
    latest_date = str(latest.get("date") or _compact_to_display(_latest_trade_date().strftime("%Y%m%d")))
    events: list[dict[str, Any]] = []

    if net_3 > 0 and pos_days >= 2:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=latest_date,
                source="fund_flow_history",
                source_label="主力资金",
                event_type="main_fund_continuation",
                evidence_type="build",
                direction="buy",
                title="近3日主力持续净流入",
                detail=f"近3日净流入{net_3 / 1e8:.2f}亿，近5日正流入{pos_days}天",
                score_impact=7.0 if net_3 >= 5e8 else 5.0,
                entity_type="main_fund",
                entity_name="主力资金",
                amount=net_3,
                net_amount=net_3,
                raw_source_fields={"flows": recent_5},
            )
        )
    if net_5 < 0 and neg_days >= 3:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=latest_date,
                source="fund_flow_history",
                source_label="主力资金",
                event_type="main_fund_outflow",
                evidence_type="distribution",
                direction="sell",
                title="近5日主力持续流出",
                detail=f"近5日净流出{abs(net_5) / 1e8:.2f}亿，负流出{neg_days}天",
                score_impact=7.0 if abs(net_5) >= 5e8 else 5.0,
                entity_type="main_fund",
                entity_name="主力资金",
                amount=abs(net_5),
                net_amount=net_5,
                raw_source_fields={"flows": recent_5},
            )
        )
    return events


def _fetch_symbol_dragon_tiger_detail(symbol: str, warnings: list[str]) -> list[dict[str, Any]]:
    result = run_dragon_tiger({"action": "stock", "symbol": symbol, "limit": 5})
    if result.get("error"):
        return []

    records = result.get("records") or []
    if not records:
        return []

    first = records[0]
    total_net = _safe_float(first.get("totalNet"))
    appearances = _safe_int(first.get("appearances"))
    if total_net == 0:
        return []

    evidence_type = "build" if total_net > 0 else "distribution"
    title = "近月龙虎榜累计净买" if total_net > 0 else "近月龙虎榜累计净卖"
    return [
        _make_event(
            symbol=symbol,
            trade_date=_compact_to_display(_latest_trade_date().strftime("%Y%m%d")),
            source="dragon_tiger_history",
            source_label="龙虎榜",
            event_type="dragon_tiger_monthly",
            evidence_type=evidence_type,
            direction="buy" if total_net > 0 else "sell",
            title=title,
            detail=f"近一月上榜{appearances}次，累计净额{abs(total_net) / 1e8:.2f}亿",
            score_impact=6.0 if abs(total_net) >= 2e8 else 4.0,
            entity_type="institution_seat",
            entity_name="龙虎榜机构席位",
            amount=abs(total_net),
            net_amount=total_net,
            raw_source_fields=first,
        )
    ]


_mgmt_change_cache: dict[str, tuple[float, Any]] = {}
_MGMT_CACHE_TTL = 300  # 5 minutes


def _fetch_management_change_events(
    end_date_compact: str,
    lookback_days: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError:
        warnings.append("高管增减持: akshare 未安装")
        return []

    end_dt = datetime.strptime(end_date_compact, "%Y%m%d")
    start_dt = end_dt - timedelta(days=_low_freq_window_days(lookback_days))
    configs = [
        {
            "symbol": "增持",
            "evidence_type": "build",
            "direction": "buy",
            "event_type": "management_increase",
            "title": "高管近期开启增持",
        },
        {
            "symbol": "减持",
            "evidence_type": "distribution",
            "direction": "sell",
            "event_type": "management_decrease",
            "title": "高管近期开启减持",
        },
    ]
    events: list[dict[str, Any]] = []

    for cfg in configs:
        cache_key = cfg["symbol"]
        cache_path = os.path.join(_FUND_HOLD_CACHE_DIR, f"mgmt_{cache_key}.json")
        df = None

        # Use file cache with TTL
        if os.path.exists(cache_path):
            try:
                age = __import__("time").time() - os.path.getmtime(cache_path)
                if age < _MGMT_CACHE_TTL:
                    with open(cache_path, encoding="utf-8") as _f:
                        cached_records = json.load(_f)
                    import pandas as pd
                    df = pd.DataFrame(cached_records)
            except Exception:
                pass

        if df is None:
            try:
                df = ak.stock_hold_management_detail_cninfo(symbol=cfg["symbol"])
                if df is not None and not df.empty:
                    os.makedirs(_FUND_HOLD_CACHE_DIR, exist_ok=True)
                    records = df.to_dict(orient="records")
                    for rec in records:
                        for k, v in rec.items():
                            if isinstance(v, (date, datetime)):
                                rec[k] = v.isoformat()
                    with open(cache_path, "w", encoding="utf-8") as _f:
                        json.dump(records, _f, ensure_ascii=False, default=str)
            except Exception as err:
                warnings.append(f"高管{cfg['symbol']}: {err}")
                continue

        if df is None or df.empty:
            continue

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in df.to_dict(orient="records"):
            code = str(row.get("证券代码") or row.get("代码") or "").strip()
            event_dt = _parse_date_like(row.get("截止日期") or row.get("公告日期") or row.get("日期"))
            if not code or event_dt is None or event_dt < start_dt or event_dt > end_dt:
                continue
            qty = abs(_safe_float(row.get("变动数量") or row.get("变动股数")))
            ratio = abs(_safe_float(row.get("变动比例")))
            if qty <= 0 and ratio <= 0:
                continue
            buckets[code].append(
                {
                    "date": event_dt,
                    "row": row,
                    "qty": qty,
                    "ratio": ratio,
                    "price": _safe_float(row.get("成交均价")),
                    "reason": _clean_text(row.get("持股变动原因") or row.get("变动原因")),
                    "person": _clean_text(row.get("董监高姓名") or row.get("高管姓名") or row.get("变动人")),
                    "name": _clean_text(row.get("证券简称") or row.get("名称")),
                }
            )

        for code, rows in buckets.items():
            rows.sort(key=lambda item: item["date"], reverse=True)
            latest = rows[0]
            total_qty = sum(item["qty"] for item in rows)
            total_ratio = sum(item["ratio"] for item in rows)
            weighted_notional = sum(item["qty"] * item["price"] for item in rows if item["price"] > 0)
            weighted_qty = sum(item["qty"] for item in rows if item["price"] > 0)
            weighted_price = weighted_notional / weighted_qty if weighted_qty > 0 else 0.0
            if total_ratio < 0.05 and total_qty < 100_000 and weighted_notional < 2_000_000:
                continue
            participants = _unique([item["person"] for item in rows if item["person"]])
            reasons = _unique([item["reason"] for item in rows if item["reason"]])
            recency_days = max(0, (end_dt - latest["date"]).days)
            score = max(
                _amount_score(total_ratio, [(5.0, 8.0), (2.0, 6.0), (0.5, 4.5), (0.1, 3.5)], 3.0),
                _amount_score(weighted_notional, [(5e7, 8.0), (2e7, 6.0), (5e6, 4.5), (2e6, 3.5)], 3.0),
            )
            if total_qty >= 1_000_000 or weighted_notional >= 5e7:
                score += 1.0
            if recency_days <= max(lookback_days * 2, 12):
                score += 1.0
            score = round(score, 1)
            detail = (
                f"近{_low_freq_window_days(lookback_days)}日{len(rows)}次{cfg['symbol']}，"
                f"累计{total_qty / 10000:.1f}万股，合计比例{total_ratio:.2f}%"
            )
            if weighted_price > 0:
                detail += f"，均价{weighted_price:.2f}"
            if reasons:
                detail += f"，原因: {'/'.join(reasons[:2])}"

            events.append(
                _make_event(
                    symbol=code,
                    trade_date=latest["date"].date().isoformat(),
                    source="management_change",
                    source_label="高管增减持",
                    event_type=str(cfg["event_type"]),
                    evidence_type=str(cfg["evidence_type"]),
                    direction=str(cfg["direction"]),
                    title=str(cfg["title"]),
                    detail=detail,
                    score_impact=score,
                    entity_type="management_holder",
                    entity_name="、".join(participants[:2]) or "董监高",
                    amount=round(total_qty, 2),
                    raw_source_fields={
                        "name": latest["name"],
                        "participants": participants[:4],
                        "reasons": reasons[:4],
                        "records": [item["row"] for item in rows[:4]],
                        "totalQty": round(total_qty, 2),
                        "totalRatio": round(total_ratio, 4),
                    },
                )
            )
    return events


_FUND_HOLD_CACHE_DIR = os.path.join(
    os.environ.get("QTDATA_DIR", "G:/qtdata"), "cache", "institution"
) if os.path.isdir(os.path.join(os.environ.get("QTDATA_DIR", "G:/qtdata"), "cache")) else os.path.join(_PYTHON_ROOT, "cache")


def _latest_report_quarter(ref_date: datetime) -> str:
    """Return the latest quarterly report date (as YYYYMMDD) that could be available.

    Financial reports are typically disclosed 1-2 months after quarter end.
    """
    year = ref_date.year
    month = ref_date.month
    # Work backward: data for a quarter is available ~2 months after quarter end
    if month >= 12:
        return f"{year}0930"
    if month >= 9:
        return f"{year}0630"
    if month >= 6:
        return f"{year}0331"
    if month >= 3:
        return f"{year - 1}1231"
    return f"{year - 1}0930"


def _prev_report_quarter(quarter: str) -> str:
    """Return the quarter before the given one."""
    y, m = int(quarter[:4]), int(quarter[4:6])
    if m == 3:
        return f"{y - 1}1231"
    if m == 6:
        return f"{y}0331"
    if m == 9:
        return f"{y}0630"
    return f"{y}0930"


def _load_fund_hold_cached(
    holder_type: str, quarter: str, warnings: list[str]
) -> dict[str, dict[str, Any]] | None:
    """Load fund hold data from cache or API, return {symbol: record}."""
    os.makedirs(_FUND_HOLD_CACHE_DIR, exist_ok=True)
    safe_type = holder_type.replace("/", "_")
    cache_path = os.path.join(_FUND_HOLD_CACHE_DIR, f"fund_hold_{safe_type}_{quarter}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except Exception:
            pass

    try:
        import akshare as ak
    except ImportError:
        return None

    try:
        df = ak.stock_report_fund_hold(symbol=holder_type, date=quarter)
    except Exception as err:
        warnings.append(f"{holder_type}({quarter}): {err}")
        return None

    if df is None or df.empty:
        return None

    by_code: dict[str, dict[str, Any]] = {}
    for row in df.to_dict(orient="records"):
        code = str(row.get("股票代码") or "").strip()
        if not code:
            continue
        by_code[code] = {
            "name": _clean_text(row.get("股票简称")),
            "holders": _safe_int(row.get("持有基金家数")),
            "totalShares": _safe_float(row.get("持股总数")),
            "totalValue": _safe_float(row.get("持股市值")),
            "change": _clean_text(row.get("持股变化")),
            "changePct": _safe_float(row.get("持股变动比例")),
            "changeQty": _safe_float(row.get("持股变动数值")),
        }

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(by_code, f, ensure_ascii=False)
    except Exception:
        pass

    return by_code


_HOLDER_TYPES: list[tuple[str, str]] = [
    ("基金持仓", "fund"),
    ("QFII持仓", "qfii"),
    ("社保持仓", "ssf"),
    ("券商持仓", "broker"),
    ("保险持仓", "insurance"),
    ("信托持仓", "trust"),
]


def _fetch_institutional_hold_events(
    end_date_compact: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Fetch fund/QFII/social-security holding events from quarterly reports (cached)."""
    ref_dt = datetime.strptime(end_date_compact, "%Y%m%d")
    quarter = _latest_report_quarter(ref_dt)
    prev_quarter = _prev_report_quarter(quarter)
    events: list[dict[str, Any]] = []

    for holder_label, holder_tag in _HOLDER_TYPES:
        current = _load_fund_hold_cached(holder_label, quarter, warnings)
        if not current:
            continue
        previous = _load_fund_hold_cached(holder_label, prev_quarter, warnings)

        for code, info in current.items():
            change = info.get("change", "")
            change_pct = info.get("changePct", 0.0)
            holders = info.get("holders", 0)
            total_value = info.get("totalValue", 0.0)

            # Determine signal direction — only pass through significant signals
            # Fund (基金) has many holders → need ≥10; others are concentrated → ≥3
            min_holders = 10 if holder_tag == "fund" else 3
            if change == "新进" and holders >= max(min_holders, 5):
                evidence_type = "build"
                direction = "buy"
                event_type = f"{holder_tag}_new_entry"
                title = f"{holder_label}新进"
            elif change == "增仓" and change_pct >= 80 and holders >= min_holders:
                evidence_type = "build"
                direction = "buy"
                event_type = f"{holder_tag}_increase"
                title = f"{holder_label}大幅增仓"
            elif change == "减仓" and abs(change_pct) >= 50 and holders >= min_holders:
                evidence_type = "distribution"
                direction = "sell"
                event_type = f"{holder_tag}_decrease"
                title = f"{holder_label}大幅减仓"
            else:
                continue

            # Score based on holder count + value + change magnitude
            if change == "新进":
                score = _amount_score(
                    holders,
                    [(50, 7.5), (20, 6.0), (10, 5.0), (5, 4.0)],
                    3.5,
                )
            elif direction == "buy":
                score = _amount_score(
                    change_pct,
                    [(200, 7.5), (100, 6.0), (50, 4.5)],
                    4.0,
                )
            else:
                score = _amount_score(
                    abs(change_pct),
                    [(60, 7.0), (50, 5.5), (40, 4.5)],
                    4.0,
                )

            if total_value >= 1e10:
                score += 1.0
            elif total_value >= 1e9:
                score += 0.5
            score = round(score, 1)

            detail = f"{holder_label} {holders}家持仓"
            if total_value > 0:
                detail += f"，持仓市值{total_value / 1e8:.1f}亿"
            if change == "新进":
                detail += "，本季新进"
            else:
                detail += f"，{change}{abs(change_pct):.1f}%"

            quarter_display = f"{quarter[:4]}Q{'1' if quarter[4:6] == '03' else '2' if quarter[4:6] == '06' else '3' if quarter[4:6] == '09' else '4'}"

            events.append(
                _make_event(
                    symbol=code,
                    trade_date=f"{quarter[:4]}-{quarter[4:6]}-{quarter[6:8]}",
                    source="institutional_hold",
                    source_label=holder_label,
                    event_type=event_type,
                    evidence_type=evidence_type,
                    direction=direction,
                    title=title,
                    detail=detail,
                    score_impact=score,
                    entity_type="institutional_holder",
                    entity_name=holder_label,
                    amount=round(total_value, 2) if total_value else None,
                    raw_source_fields={
                        "name": info.get("name"),
                        "holderType": holder_label,
                        "quarter": quarter_display,
                        "holders": holders,
                        "totalShares": info.get("totalShares"),
                        "totalValue": total_value,
                        "change": change,
                        "changePct": change_pct,
                    },
                )
            )

    return events


_CONTROLLER_CACHE_TTL = 3600  # 1 hour — controller data changes rarely


def _fetch_controller_context(symbol: str, warnings: list[str]) -> dict[str, Any] | None:
    """Look up actual-controller info for a symbol from CNINFO (~0.6s)."""
    os.makedirs(_FUND_HOLD_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_FUND_HOLD_CACHE_DIR, "controller_all.json")

    by_code: dict[str, dict[str, Any]] | None = None

    # Try file cache
    if os.path.exists(cache_path):
        try:
            age = __import__("time").time() - os.path.getmtime(cache_path)
            if age < _CONTROLLER_CACHE_TTL:
                with open(cache_path, encoding="utf-8") as f:
                    by_code = json.load(f)
        except Exception:
            pass

    if by_code is None:
        try:
            import akshare as ak
        except ImportError:
            return None

        try:
            df = ak.stock_hold_control_cninfo(symbol="全部")
        except Exception as err:
            warnings.append(f"实控人查询: {err}")
            return None

        if df is None or df.empty:
            return None

        by_code = {}
        for row in df.to_dict(orient="records"):
            code = str(row.get("证券代码") or "").strip()
            if not code:
                continue
            ratio = _safe_float(row.get("控股比例"))
            controller = _clean_text(row.get("实际控制人名称")).replace("\r", "")
            direct = _clean_text(row.get("直接控制人名称")).replace("\r", "")
            control_type = _clean_text(row.get("控制类型"))
            date_val = _parse_date_like(row.get("变动日期"))
            by_code[code] = {
                "controller": controller or "无",
                "directController": direct,
                "controlType": control_type,
                "ratio": round(ratio, 2),
                "qty": _safe_float(row.get("控股数量")),
                "asOfDate": date_val.date().isoformat() if date_val else "",
            }

        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(by_code, f, ensure_ascii=False)
        except Exception:
            pass

    return by_code.get(symbol)


def _build_kline_events(
    symbol: str,
    end_date_compact: str,
    threshold_cfg: dict[str, float | int],
    quote_turnover: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start_date = (datetime.strptime(end_date_compact, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")
    try:
        klines = get_klines(symbol, "1d", start_date, end_date_compact, "cn_stock")
    except Exception:
        klines = []

    if not klines or len(klines) < 20:
        return [], {}

    techs = _compute_technicals_from_klines(klines)
    manip = _detect_manipulation_patterns(klines)
    last_date = _compact_to_display(end_date_compact)
    events: list[dict[str, Any]] = []

    price_position = _safe_float(techs.get("price_position"), 0.5)
    volume_ratio = _safe_float(techs.get("volume_ratio"), 1.0)
    momentum_5d = _safe_float(techs.get("momentum_5d"), 0.0)
    rsi14 = _safe_float(techs.get("rsi14"), 50.0)

    if price_position < 0.35 and volume_ratio >= 1.2 and momentum_5d >= -2:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_structure",
                source_label="价量结构",
                event_type="low_zone_support",
                evidence_type="build",
                direction="buy",
                title="低位承接结构",
                detail=f"价格处于60日区间低位{price_position:.2f}，量比{volume_ratio:.2f}",
                score_impact=4.0,
            )
        )
    if price_position > 0.82 and volume_ratio >= 1.6 and abs(momentum_5d) < 2.0:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_structure",
                source_label="价量结构",
                event_type="high_zone_stall",
                evidence_type="bait",
                direction="sell",
                title="高位放量滞涨",
                detail=f"价格位于区间高位{price_position:.2f}，量比{volume_ratio:.2f}但5日动量仅{momentum_5d:.2f}%",
                score_impact=6.0,
            )
        )
    if rsi14 >= 75:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_structure",
                source_label="技术指标",
                event_type="rsi_overheat",
                evidence_type="bait",
                direction="sell",
                title="RSI 过热",
                detail=f"RSI14={rsi14:.1f}，短期追高风险上升",
                score_impact=3.0,
            )
        )
    elif 40 <= rsi14 <= 65:
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_structure",
                source_label="技术指标",
                event_type="rsi_healthy",
                evidence_type="build",
                direction="buy",
                title="RSI 健康区间",
                detail=f"RSI14={rsi14:.1f}，短期节奏相对健康",
                score_impact=2.0,
            )
        )

    pattern = str(manip.get("pattern") or "neutral")
    confidence = _safe_float(manip.get("confidence"), 0.0)
    signals = manip.get("signals") or []
    if pattern == "accumulation":
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_pattern",
                source_label="形态识别",
                event_type="accumulation_pattern",
                evidence_type="build",
                direction="buy",
                title="K线检测到吸筹形态",
                detail="; ".join(str(s) for s in signals[:3]) or "价量结构偏向吸筹",
                score_impact=round(4.0 + confidence * 6.0, 1),
            )
        )
    elif pattern == "distribution":
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_pattern",
                source_label="形态识别",
                event_type="distribution_pattern",
                evidence_type="distribution",
                direction="sell",
                title="K线检测到派发形态",
                detail="; ".join(str(s) for s in signals[:3]) or "价量结构偏向派发",
                score_impact=round(4.0 + confidence * 6.0, 1),
            )
        )
    elif pattern == "churning":
        events.append(
            _make_event(
                symbol=symbol,
                trade_date=last_date,
                source="kline_pattern",
                source_label="形态识别",
                event_type="churning_pattern",
                evidence_type="bait",
                direction="mixed",
                title="K线检测到对倒/异常换手",
                detail="; ".join(str(s) for s in signals[:3]) or "量大价不动，疑似对倒",
                score_impact=round(5.0 + confidence * 6.0, 1),
            )
        )

    # ── Configurable threshold hits (前期吸筹 / 抢筹) ──
    closes = [_safe_float(b.get("close")) for b in klines]
    volumes = [_safe_float(b.get("volume")) for b in klines]
    opens = [_safe_float(b.get("open")) for b in klines]
    pre_days = int(threshold_cfg["pre_accum_days"])
    pre_need = int(threshold_cfg["pre_accum_count_min"])
    pre_vol_ratio = _safe_float(threshold_cfg["pre_accum_volume_ratio"])
    rush_change_pct = _safe_float(threshold_cfg["rush_change_pct"])
    rush_vol_ratio = _safe_float(threshold_cfg["rush_volume_ratio"])
    rush_turnover_pct = _safe_float(threshold_cfg["rush_turnover_pct"])

    pre_volume_hits = 0
    if len(klines) >= max(pre_days + 5, 25):
        start_idx = max(4, len(klines) - pre_days)
        for i in range(start_idx, len(klines)):
            ma5 = sum(volumes[i - 4 : i + 1]) / 5
            if ma5 > 0 and volumes[i] >= ma5 * pre_vol_ratio:
                pre_volume_hits += 1

        ma20_now = sum(closes[-20:]) / 20
        ma20_prev = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma20_now
        if pre_volume_hits >= pre_need and ma20_now > ma20_prev:
            events.append(
                _make_event(
                    symbol=symbol,
                    trade_date=last_date,
                    source="kline_threshold",
                    source_label="阈值规则",
                    event_type="pre_accumulation_threshold_hit",
                    evidence_type="build",
                    direction="buy",
                    title="前期吸筹阈值命中",
                    detail=(
                        f"近{pre_days}天放量命中{pre_volume_hits}次(阈值{pre_need}次), "
                        f"放量阈值≥{pre_vol_ratio:.2f}×MA5, MA20上行"
                    ),
                    score_impact=6.0,
                )
            )

    if len(klines) >= 6:
        last_open = opens[-1]
        last_close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else last_open
        # 用收收涨幅（标准日涨幅）；若无昨收则退回盘中涨幅
        day_change = (last_close / prev_close - 1) * 100 if prev_close > 0 else (
            (last_close / last_open - 1) * 100 if last_open > 0 else 0.0
        )
        vol_ref = sum(volumes[-6:-1]) / 5 if sum(volumes[-6:-1]) > 0 else 0.0
        vol_attack = volumes[-1] / vol_ref if vol_ref > 0 else 0.0
        turnover_ok = quote_turnover is None or quote_turnover <= 0 or quote_turnover >= rush_turnover_pct
        if day_change >= rush_change_pct and vol_attack >= rush_vol_ratio and turnover_ok:
            events.append(
                _make_event(
                    symbol=symbol,
                    trade_date=last_date,
                    source="kline_threshold",
                    source_label="阈值规则",
                    event_type="institutional_rush_threshold_hit",
                    evidence_type="build",
                    direction="buy",
                    title="机构抢筹阈值命中",
                    detail=(
                        f"今日涨幅{day_change:.2f}%≥{rush_change_pct:.2f}%, "
                        f"量比(当日/近5日均量){vol_attack:.2f}≥{rush_vol_ratio:.2f}"
                        + (
                            f", 换手{quote_turnover:.2f}%≥{rush_turnover_pct:.2f}%"
                            if quote_turnover is not None and quote_turnover > 0
                            else ""
                        )
                    ),
                    score_impact=8.0,
                    turnover_rate=quote_turnover,
                )
            )

    # ── Chip distribution (筹码分布 CYQ) enrichment ──
    cyq = fetch_chip_distribution(symbol)
    if cyq:
        current_price = klines[-1].get("close", 0) if klines else 0
        cyq_score, cyq_reasons, cyq_evidence = score_chip_distribution(cyq, current_price)
        if cyq_score != 0:
            events.append(
                _make_event(
                    symbol=symbol,
                    trade_date=last_date,
                    source="chip_distribution",
                    source_label="筹码分布",
                    event_type="cyq_signal",
                    evidence_type=cyq_evidence if cyq_evidence != "neutral" else "build",
                    direction="buy" if cyq_score > 0 else "sell",
                    title="筹码分布信号",
                    detail="; ".join(cyq_reasons[:3]) or "筹码分布中性",
                    score_impact=abs(cyq_score),
                )
            )

    return events, {
        "technicals": techs,
        "manipulation": manip,
        "chipDistribution": cyq,
        "thresholdConfig": threshold_cfg,
        "thresholdHits": {
            "preAccumulationVolumeHits": pre_volume_hits,
        },
    }


def _collect_scan_sources(
    date_compact: str,
    warnings: list[str],
    lookback_days: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _fetch_dragon_tiger_events(date_compact, 60, warnings):
        by_symbol[event["symbol"]].append(event)
    for event in _fetch_fund_flow_rank_events(60, warnings):
        by_symbol[event["symbol"]].append(event)
    for event in _fetch_block_trade_daily(date_compact, 80, warnings):
        by_symbol[event["symbol"]].append(event)
    for event in _fetch_management_change_events(date_compact, lookback_days, warnings):
        by_symbol[event["symbol"]].append(event)
    for event in _fetch_institutional_hold_events(date_compact, warnings):
        by_symbol[event["symbol"]].append(event)
    return by_symbol, _fetch_northbound_context(warnings)


def _pre_rank_symbols(by_symbol: dict[str, list[dict[str, Any]]]) -> list[str]:
    positive: list[tuple[float, str]] = []
    negative: list[tuple[float, str]] = []
    for symbol, events in by_symbol.items():
        build = sum(_safe_float(event.get("scoreImpact")) for event in events if event.get("evidenceType") == "build")
        risk = sum(
            _safe_float(event.get("scoreImpact"))
            for event in events
            if event.get("evidenceType") in ("bait", "distribution")
        )
        positive.append((build - risk * 0.6, symbol))
        negative.append((risk, symbol))
    positive_symbols = [symbol for _, symbol in sorted(positive, reverse=True)[:30]]
    negative_symbols = [symbol for _, symbol in sorted(negative, reverse=True)[:20]]
    return _unique(positive_symbols + negative_symbols)


def _quote_snapshot(symbol: str, quote_map: dict[str, dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    quote = quote_map.get(symbol) or {}
    if quote:
        return {
            "name": quote.get("name") or symbol,
            "price": _safe_float(quote.get("price")),
            "changePct": _safe_float(quote.get("changePct")),
            "turnoverRate": _safe_float(quote.get("turnoverRate")),
        }
    name = _fallback_name(symbol, events)
    for event in events:
        raw = event.get("rawSourceFields") or {}
        for key in ("price", "close", "收盘价"):
            if raw.get(key) is not None:
                return {
                    "name": name,
                    "price": _safe_float(raw.get(key)),
                    "changePct": _safe_float(raw.get("changePct") or raw.get("涨跌幅")),
                    "turnoverRate": _safe_float(raw.get("turnoverRate") or raw.get("换手率")),
                }
    return {"name": name, "price": 0.0, "changePct": 0.0, "turnoverRate": 0.0}


def _summarize_symbol(
    symbol: str,
    events: list[dict[str, Any]],
    quote_map: dict[str, dict[str, Any]],
    market_context: dict[str, Any],
    end_date_compact: str,
    threshold_cfg: dict[str, float | int],
    *,
    skip_klines: bool = False,
) -> dict[str, Any]:
    quote = _quote_snapshot(symbol, quote_map, events)
    if skip_klines:
        kline_events, kline_state = [], {}
    else:
        kline_events, kline_state = _build_kline_events(
            symbol,
            end_date_compact,
            threshold_cfg,
            _safe_float(quote.get("turnoverRate")) or None,
        )
    all_events = list(events) + kline_events
    all_events.sort(key=lambda item: (str(item.get("tradeDate") or ""), _safe_float(item.get("scoreImpact"))), reverse=True)

    build_items = [event for event in all_events if event.get("evidenceType") == "build"]
    bait_items = [event for event in all_events if event.get("evidenceType") == "bait"]
    distribution_items = [event for event in all_events if event.get("evidenceType") == "distribution"]
    context_items = [event for event in all_events if event.get("evidenceType") == "context"]

    build_score = _clip_score(sum(_safe_float(event.get("scoreImpact")) for event in build_items))
    bait_score = _clip_score(sum(_safe_float(event.get("scoreImpact")) for event in bait_items))
    distribution_score = _clip_score(sum(_safe_float(event.get("scoreImpact")) for event in distribution_items))
    sources = sorted({str(event.get("sourceLabel") or event.get("source") or "") for event in all_events})
    signal_count = len(build_items) + len(bait_items) + len(distribution_items)
    dominant = max(build_score, bait_score + distribution_score)
    secondary = min(build_score, bait_score + distribution_score)
    confidence = 18 + len(sources) * 12 + min(signal_count, 6) * 6 + max(0.0, dominant - secondary) * 1.2
    if signal_count <= 1:
        confidence = min(confidence, 42)
    if build_score > 0 and (bait_score + distribution_score) > 0:
        confidence -= 8
    confidence = _clip_score(confidence)

    net_conviction = round(
        build_score - bait_score - distribution_score * 0.8 + _safe_float(market_context.get("score"), 0.0),
        1,
    )
    action_tag = _build_action_tag(build_score, bait_score, distribution_score, confidence)
    follow_window = _follow_window(build_score, confidence, action_tag)
    quote = _quote_snapshot(symbol, quote_map, all_events)

    positive_evidence = [
        {
            "source": event.get("sourceLabel"),
            "title": event.get("title"),
            "detail": event.get("detail"),
            "score": _safe_float(event.get("scoreImpact")),
        }
        for event in sorted(build_items, key=lambda item: _safe_float(item.get("scoreImpact")), reverse=True)[:6]
    ]
    negative_evidence = [
        {
            "source": event.get("sourceLabel"),
            "title": event.get("title"),
            "detail": event.get("detail"),
            "score": _safe_float(event.get("scoreImpact")),
        }
        for event in sorted(bait_items + distribution_items, key=lambda item: _safe_float(item.get("scoreImpact")), reverse=True)[:6]
    ]
    top_evidence = [str(item.get("title") or "") for item in positive_evidence[:3]]
    top_counter_evidence = [str(item.get("title") or "") for item in negative_evidence[:3]]

    timeline = [
        {
            "tradeDate": str(event.get("tradeDate") or ""),
            "source": str(event.get("sourceLabel") or event.get("source") or ""),
            "eventType": str(event.get("eventType") or ""),
            "evidenceType": str(event.get("evidenceType") or ""),
            "title": str(event.get("title") or ""),
            "detail": str(event.get("detail") or ""),
            "scoreImpact": _safe_float(event.get("scoreImpact")),
        }
        for event in all_events[:16]
    ]

    return {
        "symbol": symbol,
        "name": quote.get("name") or symbol,
        "price": round(_safe_float(quote.get("price")), 2),
        "changePct": round(_safe_float(quote.get("changePct")), 2),
        "turnoverRate": round(_safe_float(quote.get("turnoverRate")), 2),
        "buildScore": build_score,
        "baitRiskScore": bait_score,
        "distributionScore": distribution_score,
        "confidence": confidence,
        "netConviction": net_conviction,
        "followWindow": follow_window,
        "expectedHoldDays": follow_window,
        "actionTag": action_tag,
        "sourceTags": sources,
        "topEvidence": top_evidence,
        "topCounterEvidence": top_counter_evidence,
        "positiveEvidence": positive_evidence,
        "negativeEvidence": negative_evidence,
        "scoreBreakdown": {
            "build": positive_evidence,
            "bait": [
                item for item in negative_evidence
                if item["title"] in {str(event.get("title") or "") for event in bait_items}
            ],
            "distribution": [
                item for item in negative_evidence
                if item["title"] in {str(event.get("title") or "") for event in distribution_items}
            ],
            "context": [
                {
                    "source": event.get("sourceLabel"),
                    "title": event.get("title"),
                    "detail": event.get("detail"),
                    "score": _safe_float(event.get("scoreImpact")),
                }
                for event in sorted(context_items, key=lambda item: _safe_float(item.get("scoreImpact")), reverse=True)[:4]
            ],
        },
        "timeline": timeline,
        "rawEvents": all_events,
        "marketContext": market_context,
        "technicals": kline_state.get("technicals") or {},
        "manipulation": kline_state.get("manipulation") or {},
        "chipDistribution": kline_state.get("chipDistribution"),
        "thresholdConfig": threshold_cfg,
        "thresholdHits": kline_state.get("thresholdHits") or {},
    }


def analyze_scan(config: dict[str, Any]) -> dict[str, Any]:
    date_compact, date_display = _normalize_date(config.get("as_of_date") or config.get("asOfDate"))
    explicit_symbols = _normalize_symbols(config.get("symbols"))
    lookback_days = _safe_int(config.get("lookback_days") or config.get("lookbackDays"), 10)
    min_confidence = _safe_float(config.get("min_confidence") or config.get("minConfidence"), 0.0)
    threshold_cfg = _threshold_config(config)
    warnings: list[str] = []
    by_symbol, market_context = _collect_scan_sources(date_compact, warnings, lookback_days)

    candidate_symbols = explicit_symbols or _pre_rank_symbols(by_symbol)
    if not candidate_symbols:
        return {
            "action": "scan",
            "asOfDate": date_display,
            "marketSummary": {
                "dragonTigerCount": 0,
                "fundFlowCount": 0,
                "blockTradeCount": 0,
                "managementChangeCount": 0,
                "institutionalHoldCount": 0,
                "northboundNetIn": market_context.get("northboundNetIn"),
                "marketTone": market_context.get("marketTone"),
            },
            "candidates": [],
            "leaders": [],
            "highRiskBaits": [],
            "stats": {"candidateCount": 0, "leaderCount": 0, "baitCount": 0},
            "warnings": _unique(warnings or ["今日未抓到足够的公开代理信号"]),
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
        }

    quote_map = _fetch_tencent_batch(candidate_symbols)
    kline_eval_limit = int(threshold_cfg["kline_eval_limit"])
    candidates = []
    for idx, symbol in enumerate(candidate_symbols):
        candidates.append(
            _summarize_symbol(
                symbol,
                by_symbol.get(symbol, []),
                quote_map,
                market_context,
                date_compact,
                threshold_cfg,
                skip_klines=idx >= kline_eval_limit,
            )
        )
    if min_confidence > 0:
        candidates = [item for item in candidates if _safe_float(item.get("confidence")) >= min_confidence]

    leaders = sorted(
        [item for item in candidates if _safe_float(item.get("netConviction")) > 0],
        key=lambda item: (_safe_float(item.get("netConviction")), _safe_float(item.get("confidence"))),
        reverse=True,
    )[:12]
    high_risk_baits = sorted(
        [
            item for item in candidates
            if _safe_float(item.get("baitRiskScore")) + _safe_float(item.get("distributionScore")) > 0
        ],
        key=lambda item: (
            _safe_float(item.get("baitRiskScore")) + _safe_float(item.get("distributionScore")),
            _safe_float(item.get("confidence")),
        ),
        reverse=True,
    )[:10]

    # Count events scoped to the candidate pool (not global)
    pool_set = set(candidate_symbols)
    pool_events = {s: evts for s, evts in by_symbol.items() if s in pool_set}
    market_summary = {
        "dragonTigerCount": sum(1 for events in pool_events.values() for item in events if item.get("source") == "dragon_tiger"),
        "fundFlowCount": sum(1 for events in pool_events.values() for item in events if item.get("source") == "fund_flow_rank"),
        "blockTradeCount": sum(1 for events in pool_events.values() for item in events if item.get("source") == "block_trade"),
        "managementChangeCount": sum(
            1 for events in pool_events.values() for item in events if item.get("source") == "management_change"
        ),
        "institutionalHoldCount": sum(
            1 for events in pool_events.values() for item in events if item.get("source") == "institutional_hold"
        ),
        "northboundNetIn": market_context.get("northboundNetIn"),
        "marketTone": market_context.get("marketTone"),
        "summary": market_context.get("summary"),
    }

    return {
        "action": "scan",
        "asOfDate": date_display,
        "marketSummary": market_summary,
        "candidates": candidates,
        "leaders": leaders,
        "highRiskBaits": high_risk_baits,
        "stats": {
            "candidateCount": len(candidates),
            "leaderCount": len(leaders),
            "baitCount": len(high_risk_baits),
        },
        "warnings": _unique(warnings + [
            "实名量化机构日频交易不可见，当前为公开代理信号推断。",
            "盘口骗单/撤单诱导在无 Level2/逐笔数据时仅做弱风险提示。",
            f"前期吸筹阈值: {int(threshold_cfg['pre_accum_days'])}日内放量≥{int(threshold_cfg['pre_accum_count_min'])}次且MA20上行。",
            f"抢筹阈值: 当日涨幅≥{_safe_float(threshold_cfg['rush_change_pct']):.1f}% + 量比≥{_safe_float(threshold_cfg['rush_volume_ratio']):.1f} + 换手≥{_safe_float(threshold_cfg['rush_turnover_pct']):.1f}%（有换手数据时）。",
        ]),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "thresholds": threshold_cfg,
    }


def analyze_detail(config: dict[str, Any]) -> dict[str, Any]:
    symbol = str(config.get("symbol") or "").strip()
    if not symbol:
        return {"error": "缺少 symbol 参数"}

    date_compact, date_display = _normalize_date(config.get("as_of_date") or config.get("asOfDate"))
    lookback_days = _safe_int(config.get("lookback_days") or config.get("lookbackDays"), 10)
    threshold_cfg = _threshold_config(config)
    warnings: list[str] = []

    # Parallelize independent data fetches (group by data source to avoid throttling)
    # Group A (CNINFO-heavy): _collect_scan_sources + controller — run sequentially within group
    # Group B (other sources): symbol-specific fund_flow, dragon_tiger, block_trade, quote — parallel

    scan_warnings: list[str] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    market_context: dict[str, Any] = {}
    controller_ctx: dict[str, Any] | None = None

    def _do_cninfo_group() -> None:
        nonlocal by_symbol, market_context, controller_ctx
        w: list[str] = []
        by_symbol, market_context = _collect_scan_sources(date_compact, w, lookback_days)
        scan_warnings.extend(w)
        w2: list[str] = []
        controller_ctx = _fetch_controller_context(symbol, w2)
        scan_warnings.extend(w2)

    fund_flow_events: list[dict[str, Any]] = []
    dragon_tiger_events: list[dict[str, Any]] = []
    block_trade_events: list[dict[str, Any]] = []
    quote_map: dict[str, dict[str, Any]] = {}

    def _do_symbol_extras() -> None:
        nonlocal fund_flow_events, dragon_tiger_events, block_trade_events, quote_map
        w: list[str] = []
        fund_flow_events = list(_fetch_symbol_fund_flow_detail(symbol, w))
        scan_warnings.extend(w)
        w2: list[str] = []
        dragon_tiger_events = list(_fetch_symbol_dragon_tiger_detail(symbol, w2))
        scan_warnings.extend(w2)
        w3: list[str] = []
        block_trade_events = list(_fetch_block_trade_symbol_events(symbol, date_compact, lookback_days, w3))
        scan_warnings.extend(w3)
        quote_map = _fetch_tencent_batch([symbol])

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cninfo = pool.submit(_do_cninfo_group)
        f_extras = pool.submit(_do_symbol_extras)
        f_cninfo.result()
        f_extras.result()

    warnings.extend(scan_warnings)

    symbol_events = list(by_symbol.get(symbol, []))
    symbol_events.extend(fund_flow_events)
    symbol_events.extend(dragon_tiger_events)
    symbol_events.extend(block_trade_events)

    summary = _summarize_symbol(
        symbol,
        symbol_events,
        quote_map,
        market_context,
        date_compact,
        threshold_cfg,
    )
    confidence_bucket = "低"
    if _safe_float(summary.get("confidence")) >= 60:
        confidence_bucket = "高"
    elif _safe_float(summary.get("confidence")) >= 40:
        confidence_bucket = "中"

    return {
        "action": "detail",
        "symbol": symbol,
        "asOfDate": date_display,
        "summary": {
            "symbol": summary.get("symbol"),
            "name": summary.get("name"),
            "price": summary.get("price"),
            "changePct": summary.get("changePct"),
            "buildScore": summary.get("buildScore"),
            "baitRiskScore": summary.get("baitRiskScore"),
            "distributionScore": summary.get("distributionScore"),
            "confidence": summary.get("confidence"),
            "netConviction": summary.get("netConviction"),
            "actionTag": summary.get("actionTag"),
            "followWindow": summary.get("followWindow"),
            "sourceTags": summary.get("sourceTags"),
            "marketContext": market_context,
        },
        "scoreBreakdown": summary.get("scoreBreakdown"),
        "positiveEvidence": summary.get("positiveEvidence"),
        "negativeEvidence": summary.get("negativeEvidence"),
        "timeline": summary.get("timeline"),
        "eventStudy": {
            "followWindow": summary.get("followWindow"),
            "confidenceBucket": confidence_bucket,
            "note": "第一版跟随窗口为规则推断，后续会用历史事件研究继续校准。",
        },
        "rawEvents": summary.get("rawEvents"),
        "controllerContext": controller_ctx,
        "warnings": _unique(warnings + [
            "实名量化机构日频交易不可见，当前为公开代理信号推断。",
            "盘口骗单/撤单诱导在无 Level2/逐笔数据时仅做弱风险提示。",
        ]),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "thresholds": threshold_cfg,
    }


def main() -> dict[str, Any]:
    raw = (os.environ.get("QUANT_CONFIG") or sys.stdin.read()).strip()
    config = json.loads(raw) if raw else {}
    action = str(config.get("action") or "scan").strip()
    if action == "detail":
        return analyze_detail(config)
    return analyze_scan(config)


def _sanitize_for_json(obj: object) -> object:
    """Replace NaN/Infinity with None so output is valid JSON for JS parsers."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


class _SafeEncoder(json.JSONEncoder):
    """Handle date/datetime and NaN/Infinity from pandas/akshare DataFrames."""

    def default(self, o: object) -> object:
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


if __name__ == "__main__":
    try:
        result = _sanitize_for_json(main())
        print(json.dumps(result, ensure_ascii=False, cls=_SafeEncoder))
    except Exception:
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
