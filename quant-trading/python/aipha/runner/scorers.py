"""Scorer 注册表 —— 组件 → 当日选股代码 (ts_code 列表)。

对外契约由四个既有调用点决定，不可改::

    scorer_fn = get_scorer(name)
    picks: list[str] = scorer_fn(gw, comp_cfg, trade_date)

- ``aipha/runner/component_rebalance.py``：Paper/Live 共用的单组件调仓
- ``aipha/runner/live_engine_eod.py``：实盘 EOD
- ``aipha/runner/paper_engine_eod.py``：模拟盘 EOD（经 component_rebalance）
- ``aipha/runner/combo_orchestrator.py``：``_run_scorers`` 遍历 ``scorer_map``

名字从哪来
----------
两条路径不同，别名表必须一致：

- ComboOrchestrator 走 ``scorer_map``，名字由 ``_infer_scorer_name`` 推导，
  得到的是规范名（``ml_csi2000`` / ``cb_stock_quality`` / ``etf_faa`` …）；
- component_rebalance 与 live_engine_eod 走 ``comp_cfg.get("scorer", comp_name)``
  的回落路径，而 ``configs/`` 下没有任何文件声明 ``scorer:``，所以传进来的
  是**组件名**（``ml`` / ``cb`` / ``etf``）。

因此 :data:`_ALIASES` 与 ``ComboOrchestrator._infer_scorer_name`` 里的
``default_by_name`` 保持同一份映射；改一处必须改另一处。

设计约束
--------
1. **未注册的名字 raise，不返回空选股。** 空选股在上层被当成「今日无合格
   标的」（component_rebalance 只 warning 然后跳过），配置名写错就会静默
   不调仓，事后极难发现。
2. **数据/模型缺失同样 raise。** ComboOrchestrator._run_scorers 捕获异常后
   推送告警（§13.13 #1：禁止 silent failure）；Live/Paper 路径则让当日调仓
   失败。两者都比拿一份不可信的选股去下单好。
3. **打分复用回测侧实现**，避免训练/上线口径漂移：
   - CB → ``aipha.factors.cb_quality_ranking.rank_cb_quality``
   - ETF → ``ETFRotationStrategy`` 的 FAA 因子公式
   - ML → ``scripts/train_ml_factor.py`` 的特征管线 + ``MLStockSelector``
   ML 侧另有特征覆盖率校验：模型训练时的特征名对不上就 raise，不允许
   ``_resolve_features`` 静默取交集后带着残缺特征推理。
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: scorer 函数签名：(gateway, 组件配置, 交易日 YYYYMMDD) → ts_code 列表
ScorerFn = Callable[[Any, dict, str], list[str]]

#: 组件名 → 规范 scorer 名。与 ComboOrchestrator._infer_scorer_name 同步。
_ALIASES: dict[str, str] = {
    "ml": "ml_csi2000",
    "cb": "cb_stock_quality",
    "etf": "etf_faa",
}

#: ML 特征至少要匹配上模型训练特征的比例，低于此值视为管线不一致。
MIN_FEATURE_COVERAGE = 0.9

_REGISTRY: dict[str, ScorerFn] = {}


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def register_scorer(name: str, fn: ScorerFn, *, override: bool = False) -> None:
    """注册一个 scorer。

    Args:
        name: 规范名（如 ``cb_stock_quality``）。
        fn: ``(gw, comp_cfg, trade_date) -> list[str]``。
        override: 允许覆盖同名注册。默认禁止 —— 重名通常是导入了两份实现，
            静默覆盖会让线上跑的是哪一份变得不可知。
    """
    key = str(name).strip()
    if not key:
        raise ValueError("scorer 名不能为空")
    if key in _REGISTRY and not override:
        raise ValueError(f"scorer {key!r} 已注册，如确实要替换请传 override=True")
    _REGISTRY[key] = fn


def available_scorers() -> list[str]:
    """已注册的规范名（排序后）。"""
    return sorted(_REGISTRY)


def resolve_scorer_name(comp_name: str, comp_cfg: dict | None = None) -> str:
    """把组件名/配置解析成规范 scorer 名。

    与调用点的 ``comp_cfg.get("scorer", comp_name)` 等价，额外过一遍别名表。
    """
    cfg = comp_cfg or {}
    explicit = str(cfg.get("scorer", "") or "").strip()
    raw = explicit or str(comp_name or "").strip()
    return _ALIASES.get(raw, raw)


def get_scorer(name: str) -> ScorerFn:
    """按名字取 scorer 函数。

    Raises:
        KeyError: 名字未注册。错误信息里列出全部可用名 —— 配置名拼错时
            这是唯一的线索来源。
    """
    key = _ALIASES.get(str(name).strip(), str(name).strip())
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"未知 scorer {name!r}（解析为 {key!r}）。"
            f"可用: {', '.join(available_scorers())}。"
            f"组件名别名: {_ALIASES}",
        ) from None


# ---------------------------------------------------------------------------
# 配置读取 —— 组件配置来自 YAML，类型不保证
# ---------------------------------------------------------------------------

def _as_float(cfg: dict, key: str, default: float) -> float:
    try:
        value = cfg.get(key, default)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        logger.warning("配置 %s=%r 不是数字，用默认值 %s", key, cfg.get(key), default)
        return default


def _as_int(cfg: dict, key: str, default: int) -> int:
    try:
        value = cfg.get(key, default)
        return default if value is None else int(value)
    except (TypeError, ValueError):
        logger.warning("配置 %s=%r 不是整数，用默认值 %s", key, cfg.get(key), default)
        return default


def _as_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default if value is None else bool(value)


# ---------------------------------------------------------------------------
# 日期工具
# ---------------------------------------------------------------------------

def _to_date(trade_date: str) -> date:
    text = str(trade_date).strip().replace("-", "")[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"trade_date 需为 YYYYMMDD，收到 {trade_date!r}") from exc


def _lookback_start(trade_date: str, n_trading_days: int) -> str:
    """往前推 n 个交易日，返回 YYYYMMDD。"""
    from aipha.data.calendar import CNStockCalendar

    end = _to_date(trade_date)
    cal = CNStockCalendar()
    start = cal.get_prev_trading_day(end, skip_days=max(1, n_trading_days))
    return start.strftime("%Y%m%d")


def _days_between(raw: object, trade_day: date) -> float:
    """``raw``（YYYYMMDD 等）到 trade_day 的天数；不可解析返回 nan。"""
    if raw is None:
        return float("nan")
    text = str(raw).strip()[:10]
    if not text or text.lower() in {"nan", "none", "nat"}:
        return float("nan")
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return float((trade_day - datetime.strptime(text, fmt).date()).days)
        except ValueError:
            continue
    return float("nan")


# ---------------------------------------------------------------------------
# CB —— 正股质量增强可转债
# ---------------------------------------------------------------------------

def score_cb_stock_quality(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """CB 正股质量打分（5+1 因子）。

    因子与权重口径完全交给 :func:`rank_cb_quality`，与回测策略
    ``aipha.strategy.cb_stock_quality`` 共用同一实现。本函数只负责把
    当日行情/静态字段/正股基本面拼成它要的那张表。

    配置键（见 ``configs/paper/r115_combo.yml`` 的 ``cb`` 段）：
        top_k, max_price, max_premium, min_turnover(万元),
        max_issue_size(万元), min_age_days, min_days_to_maturity,
        factor_weights{price, premium, stock_roe, stock_ep, stock_vol, issue_size},
        vol_lookback
    """
    from aipha.data.cb_data_enricher import fetch_cb_daily_enriched
    from aipha.factors.cb_quality_ranking import CBQualityFilters, rank_cb_quality

    top_k = _as_int(comp_cfg, "top_k", 10)
    if top_k <= 0:
        raise ValueError(f"cb_stock_quality: top_k 必须为正，收到 {top_k}")

    df = fetch_cb_daily_enriched(gw, trade_date)
    if df is None or df.empty:
        logger.warning("[cb_stock_quality] %s 无 cb_daily 数据", trade_date)
        return []

    df = _attach_cb_static(gw, df, comp_cfg, trade_date)
    if df.empty:
        logger.warning("[cb_stock_quality] %s 静态字段过滤后无候选", trade_date)
        return []

    df = _attach_stock_quality(df, comp_cfg, trade_date)

    # amount：cb_daily 的成交额单位是万元，与 min_turnover 同单位，直接用。
    if "amount" not in df.columns:
        # 缺列时 rank_cb_quality 会跳过流动性过滤 —— 明确记一笔，
        # 否则「今天为什么选出了个没量的债」无从解释。
        logger.warning("[cb_stock_quality] cb_daily 缺 amount 列，流动性过滤失效")

    filters = CBQualityFilters(
        max_price=_as_float(comp_cfg, "max_price", 150.0),
        max_premium=_as_float(comp_cfg, "max_premium", 80.0),
        min_turnover=_as_float(comp_cfg, "min_turnover", 20.0),
        min_days_to_maturity=_as_int(comp_cfg, "min_days_to_maturity", 30),
    )
    weights = comp_cfg.get("factor_weights")
    if weights is not None and not isinstance(weights, dict):
        raise ValueError(
            f"cb_stock_quality: factor_weights 需为字典，收到 {type(weights).__name__}",
        )

    picks = rank_cb_quality(df, weights, top_k, filters)
    logger.info(
        "[cb_stock_quality] %s 候选 %d → 选出 %d: %s",
        trade_date, len(df), len(picks), picks,
    )
    return picks


def _attach_cb_static(
    gw: Any, df: Any, comp_cfg: dict, trade_date: str,
) -> Any:
    """并入 cb_basic 静态字段，并做次新/到期/规模过滤。

    到期过滤必须在这里做：``CBQualityFilters.min_days_to_maturity`` 虽然存在，
    但 ``_compute_ranked_df`` 只过滤价格/溢价/成交额，不看到期日。
    """
    import pandas as pd

    trade_day = _to_date(trade_date)
    basic = gw.call(
        "cb_basic",
        fields="ts_code,stk_code,issue_size,list_date,maturity_date",
    )
    if basic is None or basic.empty:
        raise RuntimeError(
            "cb_basic 为空 —— 缺发行规模/上市日/到期日，无法执行次新与"
            "强赎风险过滤。宁可当日不调仓，也不能带着未过滤的近到期债下单。",
        )

    keep = [c for c in ("ts_code", "stk_code", "issue_size", "list_date",
                        "maturity_date") if c in basic.columns]
    merged = df.merge(basic[keep], on="ts_code", how="left")

    # issue_size：TuShare 单位为元，全仓统一按万元（见 cb_data_adapter）。
    if "issue_size" in merged.columns:
        merged["issue_size"] = pd.to_numeric(
            merged["issue_size"], errors="coerce",
        ) / 10000.0
    else:
        merged["issue_size"] = 0.0

    before = len(merged)

    # 次新过滤：上市不足 min_age_days 的债价格未稳定。
    min_age = _as_int(comp_cfg, "min_age_days", 3)
    if min_age > 0 and "list_date" in merged.columns:
        age = merged["list_date"].map(lambda v: _days_between(v, trade_day))
        # 解析不出上市日的保留 —— 不能因为字段缺失就当成次新剔除。
        merged = merged[~(age < min_age).fillna(False)]

    # 到期过滤：临近到期的债强赎/回售风险高。
    min_dtm = _as_int(comp_cfg, "min_days_to_maturity", 30)
    if min_dtm > 0 and "maturity_date" in merged.columns:
        dtm = merged["maturity_date"].map(
            lambda v: -_days_between(v, trade_day),
        )
        merged = merged[~(dtm < min_dtm).fillna(False)]

    # 规模硬上限：回测策略默认 inf（不过滤），但 r115 的 cb 段显式给了
    # max_issue_size，配置里写了就执行。
    max_size = _as_float(comp_cfg, "max_issue_size", float("inf"))
    if math.isfinite(max_size) and max_size > 0:
        merged = merged[~(merged["issue_size"] > max_size).fillna(False)]

    if before > len(merged):
        logger.info(
            "[cb_stock_quality] 静态过滤 %d → %d（次新/到期/规模）",
            before, len(merged),
        )
    return merged.drop(columns=["list_date", "maturity_date"], errors="ignore")


def _attach_stock_quality(df: Any, comp_cfg: dict, trade_date: str) -> Any:
    """并入正股 ROE / EP / 波动率三列。

    正股数据取不到时填 0 —— ``_rank_series`` 对缺失也是填 0 后排名，
    填 0 等于给中性偏低的排名，不会把没有基本面的债排到前面。
    这与回测策略 ``default_roe`` / ``default_ep`` 的处理一致。

    注意列名冲突：``fetch_cb_daily_enriched`` 返回的 ``vol`` 是**转债成交量
    （张）**，而 ``rank_cb_quality`` 把 ``stock_vol`` 权重映射到 ``vol`` 当作
    **正股 60 日波动率**（越小越好）。同名不同义，若沿用原列就等于拿 0.15
    的权重按转债成交量升序打分 —— 悄悄偏好最不活跃的债。这里先把成交量
    改名让开，再无条件重置三列。
    """
    if "vol" in df.columns:
        df = df.rename(columns={"vol": "cb_vol"})
    for column in ("roe", "ep", "vol"):
        df[column] = 0.0

    if "stk_code" not in df.columns:
        logger.warning("[cb_stock_quality] 无 stk_code，正股因子全部为 0")
        return df

    stk_codes = [c for c in df["stk_code"].dropna().unique().tolist() if c]
    if not stk_codes:
        logger.warning("[cb_stock_quality] stk_code 全空，正股因子全部为 0")
        return df

    trade_day = _to_date(trade_date)
    vol_lookback = _as_int(comp_cfg, "vol_lookback", 60)

    try:
        from aipha.data.cb_stock_bridge import CBStockBridge

        bridge = CBStockBridge()
        bridge.preload_daily_basic(trade_date, trade_date)
        bridge.preload_pit(stk_codes)
    except Exception as exc:  # 正股桥接不可用 → 退化为纯 CB 因子
        logger.warning(
            "[cb_stock_quality] 正股桥接不可用，退化为纯 CB 因子（价格+溢价）: %s",
            exc,
        )
        return df

    roe_map: dict[str, float] = {}
    ep_map: dict[str, float] = {}
    for code in stk_codes:
        try:
            fund = bridge.get_fundamentals(code, trade_day)
        except Exception as exc:
            logger.debug("正股基本面失败 %s: %s", code, exc)
            continue
        if fund.roe is not None:
            roe_map[code] = float(fund.roe)
        if fund.ep is not None:
            ep_map[code] = float(fund.ep)

    vol_map = _stock_vol_map(bridge, stk_codes, trade_date, vol_lookback)

    df["roe"] = df["stk_code"].map(roe_map).fillna(0.0)
    df["ep"] = df["stk_code"].map(ep_map).fillna(0.0)
    df["vol"] = df["stk_code"].map(vol_map).fillna(0.0)
    logger.info(
        "[cb_stock_quality] 正股因子覆盖 roe=%d ep=%d vol=%d / %d 只正股",
        len(roe_map), len(ep_map), len(vol_map), len(stk_codes),
    )
    return df


def _stock_vol_map(
    bridge: Any, stk_codes: list[str], trade_date: str, lookback: int,
) -> dict[str, float]:
    """正股年化波动率 —— 一次拉全市场日线，再按代码切片。"""
    try:
        from aipha.data.tushare_equity_provider import TuShareEquityProvider

        provider = TuShareEquityProvider()
        start = _lookback_start(trade_date, lookback + 5)
        bars = provider.load_daily_bars_range(start, trade_date)
    except Exception as exc:
        logger.warning("[cb_stock_quality] 正股日线加载失败，vol 因子为 0: %s", exc)
        return {}

    if bars is None or bars.empty or "ts_code" not in bars.columns:
        logger.warning("[cb_stock_quality] 正股日线为空，vol 因子为 0")
        return {}

    wanted = set(stk_codes)
    bars = bars[bars["ts_code"].isin(wanted)]
    if bars.empty:
        return {}

    trade_day = _to_date(trade_date)
    result: dict[str, float] = {}
    for code, group in bars.sort_values("trade_date").groupby("ts_code"):
        closes = [float(x) for x in group["close"].tolist() if x and x > 0]
        metrics = bridge.get_daily_metrics(str(code), trade_day, closes)
        value = metrics.vol_60d if lookback >= 60 else metrics.vol_20d
        if value is None:
            value = metrics.vol_20d if lookback >= 60 else metrics.vol_60d
        if value is not None:
            result[str(code)] = float(value)
    return result


# ---------------------------------------------------------------------------
# ETF —— Keller FAA 轮动
# ---------------------------------------------------------------------------

def score_etf_faa(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """ETF FAA 打分（动量 + 低波 + 相关性 + 成交活跃度）。

    因子公式与 ``aipha.strategy.etf_rotation.ETFRotationStrategy._score_etfs``
    一致：原始值 → 排名归一化到 [0,1] → 加权求和 → 取 top_k。方向已统一为
    「越大越好」（波动率与反转取负）。

    C 因子（与核心策略收益的滚动相关性）需要 ``benchmark_returns_path``；
    没给就退化为常量，与回测里 ``_benchmark_returns`` 为空时的行为相同。

    配置键（见 ``configs/paper/r115_combo.yml`` 的 ``etf`` 段）：
        symbols（必填）, top_k, weight_momentum, weight_volatility,
        weight_correlation, weight_volume, weight_reversion,
        momentum_lookback, volatility_lookback, reversion_lookback,
        trend_ma_period, min_amount(万元), correlation_lookback,
        correlation_hard_exclude, benchmark_returns_path
    """
    del gw  # ETF 行情走 TuShareETFProvider（fund_daily），不用通用 gateway

    symbols = comp_cfg.get("symbols") or []
    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise ValueError(
            "etf_faa: 配置缺 symbols —— ETF 轮动池必须显式给定，"
            "不存在「全市场 ETF」的合理默认值",
        )
    symbols = [str(s).strip() for s in symbols if str(s).strip()]

    top_k = _as_int(comp_cfg, "top_k", 3)
    if top_k <= 0:
        raise ValueError(f"etf_faa: top_k 必须为正，收到 {top_k}")

    mom_lb = _as_int(comp_cfg, "momentum_lookback", 60)
    vol_lb = _as_int(comp_cfg, "volatility_lookback", 20)
    rev_lb = _as_int(comp_cfg, "reversion_lookback", 5)
    ma_period = _as_int(comp_cfg, "trend_ma_period", 120)
    corr_lb = _as_int(comp_cfg, "correlation_lookback", 60)
    min_amount = _as_float(comp_cfg, "min_amount", 0.0)
    hard_exclude = _as_float(comp_cfg, "correlation_hard_exclude", 0.60)

    w_mom = _as_float(comp_cfg, "weight_momentum", 0.35)
    w_vol = _as_float(comp_cfg, "weight_volatility", 0.25)
    w_cor = _as_float(comp_cfg, "weight_correlation", 0.30)
    w_amt = _as_float(comp_cfg, "weight_volume", 0.10)
    w_rev = _as_float(comp_cfg, "weight_reversion", 0.0)

    need = max(mom_lb, vol_lb + 1, rev_lb + 1, ma_period, corr_lb, 10)
    history = _load_etf_history(symbols, trade_date, need)
    if not history:
        raise RuntimeError(
            f"etf_faa: {trade_date} 未取到任何 ETF 日线（{len(symbols)} 只标的）",
        )

    benchmark = _load_benchmark_returns(
        str(comp_cfg.get("benchmark_returns_path", "") or ""),
    )
    if w_cor > 0 and not benchmark:
        logger.warning(
            "[etf_faa] 未配置 benchmark_returns_path，C 因子退化为常量"
            "（权重 %.2f 不产生区分度）",
            w_cor,
        )

    candidates: list[str] = []
    raw: dict[str, list[float]] = {
        "mom": [], "vol": [], "rev": [], "amt": [], "cor": [],
    }
    for sym in symbols:
        rows = history.get(sym) or []
        closes = [float(c) for _, c, _ in rows]
        if len(closes) < max(mom_lb, vol_lb + 1, 10):
            logger.debug("[etf_faa] %s 历史不足 %d 条，跳过", sym, len(closes))
            continue

        # 趋势过滤：跌破均线不参与
        if ma_period > 0 and len(closes) >= ma_period:
            ma = sum(closes[-ma_period:]) / ma_period
            if closes[-1] < ma:
                continue

        amounts = [float(a) for _, _, a in rows]
        avg_amt = sum(amounts) / len(amounts) if amounts else 0.0
        if min_amount > 0 and avg_amt < min_amount:
            continue

        corr_val = None
        if w_cor > 0 and benchmark:
            corr_val = _corr_with_benchmark(rows, benchmark, corr_lb)
            if (
                corr_val is not None
                and hard_exclude > 0
                and corr_val > hard_exclude
            ):
                logger.debug(
                    "[etf_faa] C 因子硬排除 %s: corr=%.3f > %.2f",
                    sym, corr_val, hard_exclude,
                )
                continue

        candidates.append(sym)
        raw["mom"].append(closes[-1] / closes[-mom_lb] - 1.0)
        raw["vol"].append(-_realized_vol(closes, vol_lb))
        raw["rev"].append(
            -(closes[-1] / closes[-rev_lb] - 1.0)
            if len(closes) > rev_lb else 0.0
        )
        raw["amt"].append(math.log1p(max(0.0, avg_amt)))
        raw["cor"].append(-corr_val if corr_val is not None else 0.0)

    if not candidates:
        logger.warning("[etf_faa] %s 过滤后无候选 ETF", trade_date)
        return []

    ranks = {key: _rank_normalize(values) for key, values in raw.items()}
    scored = [
        (
            candidates[i],
            w_mom * ranks["mom"][i]
            + w_vol * ranks["vol"][i]
            + w_rev * ranks["rev"][i]
            + w_amt * ranks["amt"][i]
            + w_cor * ranks["cor"][i],
        )
        for i in range(len(candidates))
    ]
    # 同分时按代码排序，保证同一份数据给出同一份选股（可复现）
    scored.sort(key=lambda item: (-item[1], item[0]))
    picks = [sym for sym, _ in scored[:top_k]]
    logger.info(
        "[etf_faa] %s 候选 %d → 选出 %d: %s",
        trade_date, len(candidates), len(picks), picks,
    )
    return picks


def _load_etf_history(
    symbols: list[str], trade_date: str, need: int,
) -> dict[str, list[tuple[str, float, float]]]:
    """逐只取 ETF 日线，返回 {代码: [(日期, 收盘, 成交额万元), ...]}（时间正序）。"""
    from aipha.data.tushare_etf_provider import get_etf_provider

    provider = get_etf_provider()
    start = _lookback_start(trade_date, need + 10)
    result: dict[str, list[tuple[str, float, float]]] = {}
    for sym in symbols:
        try:
            df = provider.load_fund_daily(sym, start, trade_date)
        except Exception as exc:
            logger.warning("[etf_faa] %s 日线加载失败: %s", sym, exc)
            continue
        if df is None or df.empty:
            logger.warning("[etf_faa] %s 无日线数据 %s~%s", sym, start, trade_date)
            continue
        df = df.sort_values("trade_date")
        rows: list[tuple[str, float, float]] = []
        for _, row in df.iterrows():
            close = row.get("close")
            if close is None or not math.isfinite(float(close)) or float(close) <= 0:
                continue
            amount = row.get("amount") or 0.0
            # fund_daily 的 amount 单位为千元，全仓统一万元。
            rows.append((
                str(row.get("trade_date", "")),
                float(close),
                float(amount) / 10.0,
            ))
        if rows:
            result[sym] = rows
    return result


def _realized_vol(closes: list[float], lookback: int) -> float:
    """已实现波动率（日频，未年化）—— 与 ETFRotationStrategy 口径一致。"""
    if len(closes) < lookback + 1:
        return 0.0
    returns = [
        closes[i] / closes[i - 1] - 1.0
        for i in range(len(closes) - lookback, len(closes))
        if closes[i - 1] > 0
    ]
    if not returns:
        return 0.0
    return (sum(r * r for r in returns) / len(returns)) ** 0.5


def _rank_normalize(values: list[float]) -> list[float]:
    """排名归一化到 [0,1]，同值取平均排名；n<=1 时全给 0.5。"""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[order[j + 1]] == values[order[j]]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return [r / (n - 1) for r in ranks]


def _load_benchmark_returns(path: str) -> dict[str, float]:
    """读核心策略日收益 CSV（date,return），返回 {YYYYMMDD: 收益}。"""
    if not path:
        return {}
    file = Path(path)
    if not file.exists():
        logger.warning("[etf_faa] benchmark_returns_path 不存在: %s", path)
        return {}
    try:
        import pandas as pd

        df = pd.read_csv(file)
    except Exception as exc:
        logger.warning("[etf_faa] 基准收益读取失败 %s: %s", path, exc)
        return {}

    date_col = next((c for c in df.columns if c.lower() in {"date", "trade_date"}), None)
    ret_col = next(
        (c for c in df.columns if c.lower() in {"return", "ret", "daily_return"}),
        None,
    )
    if date_col is None or ret_col is None:
        logger.warning("[etf_faa] 基准收益缺 date/return 列: %s", list(df.columns))
        return {}

    out: dict[str, float] = {}
    for raw_date, raw_ret in zip(df[date_col], df[ret_col]):
        key = str(raw_date).strip().replace("-", "").replace("/", "")[:8]
        try:
            value = float(raw_ret)
        except (TypeError, ValueError):
            continue
        if key and math.isfinite(value):
            out[key] = value
    logger.info("[etf_faa] C 因子基准加载 %d 天", len(out))
    return out


def _corr_with_benchmark(
    rows: list[tuple[str, float, float]],
    benchmark: dict[str, float],
    lookback: int,
) -> float | None:
    """ETF 与基准的 Pearson 相关性，按日期对齐后取最近 lookback 天。"""
    pairs: list[tuple[float, float]] = []
    for i in range(1, len(rows)):
        prev_close = rows[i - 1][1]
        if prev_close <= 0:
            continue
        day = rows[i][0].replace("-", "")[:8]
        bench = benchmark.get(day)
        if bench is None:
            continue
        pairs.append((rows[i][1] / prev_close - 1.0, bench))
    if len(pairs) < 10:
        return None
    pairs = pairs[-lookback:]
    etf_rets = [p[0] for p in pairs]
    bm_rets = [p[1] for p in pairs]
    return _pearson(etf_rets, bm_rets)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    value = cov / math.sqrt(vx * vy)
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# ML —— 截面排名选股
# ---------------------------------------------------------------------------

def _model_dirs(comp_cfg: dict) -> list[Path]:
    """取模型目录列表：``model_dirs``（集成）优先，其次 ``model_dir``。"""
    raw = comp_cfg.get("model_dirs")
    if isinstance(raw, str):
        raw = [raw]
    if not raw:
        single = comp_cfg.get("model_dir")
        raw = [single] if single else []
    dirs = [Path(str(d)) for d in raw if str(d).strip()]
    missing = [str(d) for d in dirs if not (d / "meta.pkl").exists()]
    if missing:
        raise FileNotFoundError(
            f"模型目录缺 meta.pkl: {missing}。"
            "模型没加载成功时必须报错 —— 用退化打分下单等于拿真钱试错。",
        )
    return dirs


def _score_ml(
    gw: Any, comp_cfg: dict, trade_date: str, *, default_universe: str,
) -> list[str]:
    """ML 截面打分的公共实现。

    特征管线直接复用训练脚本 ``scripts/train_ml_factor.py``：训练与推理
    共用同一份代码是避免口径漂移的唯一可靠办法。跑完还要核对模型自身的
    ``feature_name()``，覆盖率低于 :data:`MIN_FEATURE_COVERAGE` 就 raise。
    """
    del gw  # ML 侧读本地 parquet 缓存 + 模型文件，不经 gateway

    model_dirs = _model_dirs(comp_cfg)
    if not model_dirs:
        raise ValueError(
            "ML scorer 配置缺 model_dir/model_dirs —— 没有模型就没有选股依据",
        )

    universe = str(comp_cfg.get("universe") or default_universe).strip()
    top_k = _as_int(comp_cfg, "top_k", 50)
    if top_k <= 0:
        raise ValueError(f"ML scorer: top_k 必须为正，收到 {top_k}")

    cross = _load_ml_cross_section(comp_cfg, universe, trade_date)
    if cross.empty:
        logger.warning("[ml:%s] %s 截面为空", universe, trade_date)
        return []

    ranks = None
    for model_dir in model_dirs:
        model_rank = _predict_ranks(model_dir, cross)
        ranks = model_rank if ranks is None else ranks.add(model_rank, fill_value=0.0)
    assert ranks is not None  # model_dirs 非空，循环至少执行一次

    ranks = ranks / float(len(model_dirs))  # rank_average 集成
    picks = ranks.sort_values(ascending=False).head(top_k).index.tolist()
    picks = [str(p) for p in picks]
    logger.info(
        "[ml:%s] %s 截面 %d 只 / %d 个模型 → 选出 %d",
        universe, trade_date, len(cross), len(model_dirs), len(picks),
    )
    return picks


def _load_ml_cross_section(comp_cfg: dict, universe: str, trade_date: str) -> Any:
    """构建 trade_date 当日的特征截面。

    Returns:
        DataFrame，含 ``symbol`` 列与全部特征列，仅一天。
    """
    import pandas as pd

    # Canonical deployments provide a frozen PIT feature snapshot explicitly.
    # This branch never imports the legacy training script and therefore keeps
    # the runtime scorer on the same provenance contract as research runs.
    snapshot_path = comp_cfg.get("feature_snapshot_path") or comp_cfg.get("research_snapshot_path")
    if snapshot_path:
        return _load_canonical_ml_cross_section(
            str(snapshot_path), universe, trade_date,
            feature_version=str(comp_cfg.get("feature_version", "cn_equity_v1")),
        )

    if comp_cfg.get("legacy_ml_compat") is not True:
        raise RuntimeError(
            "ML scorer has no canonical feature_snapshot_path. The legacy cache/"
            "train_ml_factor pipeline is disabled by default; set legacy_ml_compat: true "
            "only for an explicitly retained historical Paper configuration."
        )
    logger.warning(
        "[ml:%s] using explicitly enabled legacy feature/cache pipeline; migrate this config to feature_snapshot_path",
        universe,
    )
    train_ml = _import_train_ml_factor()

    # Alpha158 最长窗口 60 天，行为/基本面因子还要更长；留 250 个交易日。
    lookback = _as_int(comp_cfg, "feature_lookback_days", 250)
    start = _lookback_start(trade_date, lookback)

    panel = train_ml.load_daily_data(start, trade_date)
    panel = train_ml.filter_universe(panel, universe)
    if panel is None or panel.empty:
        raise RuntimeError(
            f"ML scorer: universe={universe} 在 {start}~{trade_date} 无日线数据"
            "（检查 data/cache/equity_tushare 是否已同步）",
        )

    panel = train_ml.compute_features(
        panel,
        deep_fundamental=_as_bool(comp_cfg, "deep_fundamental", True),
        behavioral=_as_bool(comp_cfg, "behavioral", True),
        behavioral_only=_as_bool(comp_cfg, "behavioral_only", False),
    )

    panel["datetime"] = pd.to_datetime(panel["datetime"])
    target = pd.Timestamp(_to_date(trade_date))
    cross = panel[panel["datetime"] == target].copy()
    if cross.empty:
        # 非交易日或缓存尚未更新到当日 —— 用上一天的特征去下单会引入
        # 一天的信息滞后，宁可报错让上层告警。
        latest = panel["datetime"].max()
        raise RuntimeError(
            f"ML scorer: {trade_date} 无截面数据（缓存最新到 "
            f"{latest.date() if latest is not pd.NaT else 'N/A'}）",
        )

    cross = _apply_ml_filters(cross, comp_cfg)
    return cross


def _load_canonical_ml_cross_section(
    snapshot_path: str,
    universe: str,
    trade_date: str,
    *,
    feature_version: str,
) -> Any:
    """Read one date from an attested canonical PIT feature snapshot.

    ``snapshot_path`` may contain ``{trade_date}`` (``YYYYMMDD``) or
    ``{date}`` (``YYYY-MM-DD``).  A missing/invalid snapshot is fatal: using
    the old cache as an implicit fallback would defeat the provenance gate.
    """
    import pandas as pd

    path_text = str(snapshot_path)
    path_text = path_text.replace("{trade_date}", str(trade_date).replace("-", ""))
    path_text = path_text.replace("{date}", str(trade_date)[:10])
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"canonical feature snapshot not found: {path}")
    if path.suffix.lower() != ".parquet":
        raise ValueError("canonical feature_snapshot_path must point to Parquet")

    from aipha.research.snapshots import verify_attested_parquet

    metadata = verify_attested_parquet(
        path,
        require_artifact_hash=True,
        verify_content_hash=True,
        require_source_signature=True,
    )
    if metadata.get("provider") != "tushare_pit":
        raise ValueError("canonical ML scorer requires provider=tushare_pit")
    if metadata.get("feature_version") != feature_version:
        raise ValueError(
            f"feature version mismatch: snapshot={metadata.get('feature_version')!r}, "
            f"requested={feature_version!r}"
        )
    expected_indices = {
        "csi300": "000300.SH", "csi500": "000905.SH",
        "csi1000": "000852.SH", "csi2000": "932000.CSI",
    }
    expected_index = expected_indices.get(str(universe).lower())
    if expected_index and str(metadata.get("index_code", "")).upper() != expected_index:
        raise ValueError(
            f"universe mismatch: snapshot index={metadata.get('index_code')!r}, "
            f"requested {universe} ({expected_index})"
        )
    panel = pd.read_parquet(path)
    required = {"trade_date", "symbol", "feature_version", "is_index_member", "is_st", "listing_days"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"canonical feature snapshot missing columns: {', '.join(sorted(missing))}")
    versions = panel["feature_version"].dropna().astype(str).unique().tolist()
    if versions != [feature_version]:
        raise ValueError(f"row feature_version mismatch: {versions!r}")
    if "price_adjustment" not in panel or not panel["price_adjustment"].eq("point_in_time_factor").all():
        raise ValueError("canonical feature snapshot must use point_in_time_factor adjusted prices")
    panel = panel.copy()
    panel["datetime"] = pd.to_datetime(panel["trade_date"], errors="raise")
    target = pd.Timestamp(_to_date(trade_date))
    cross = panel[panel["datetime"] == target].copy()
    if cross.empty:
        raise RuntimeError(f"canonical feature snapshot has no rows for {trade_date}")
    # The snapshot is normally already scoped to its configured index.  The
    # row-level check makes this true even when a combined snapshot is used.
    cross = cross[cross["is_index_member"].astype(bool)]
    cross = cross[~cross["is_st"].astype(bool)]
    cross = cross[cross["listing_days"] >= 252]
    if "is_suspended" in cross:
        cross = cross[~cross["is_suspended"].astype(bool)]
    cross.attrs["feature_version"] = feature_version
    cross.attrs["universe"] = universe
    cross.attrs["canonical_pit"] = True
    return cross


def _apply_ml_filters(cross: Any, comp_cfg: dict) -> Any:
    """价格/流动性过滤 —— 与 EquityRotationConfig 同口径（成交额万元）。"""
    before = len(cross)
    min_price = _as_float(comp_cfg, "min_price", 0.0)
    max_price = _as_float(comp_cfg, "max_price", 0.0)
    min_turnover = _as_float(comp_cfg, "min_turnover", 0.0)

    if "close" in cross.columns:
        if min_price > 0:
            cross = cross[cross["close"] >= min_price]
        if max_price > 0:
            cross = cross[cross["close"] <= max_price]
    if min_turnover > 0 and "amount" in cross.columns:
        # TuShare daily.amount 单位千元 → 万元。
        cross = cross[(cross["amount"] / 10.0) >= min_turnover]

    if before > len(cross):
        logger.info("[ml] 价格/流动性过滤 %d → %d", before, len(cross))
    return cross


def _import_train_ml_factor() -> Any:
    """导入训练脚本里的特征管线。

    ``scripts/`` 不是包，只能临时加到 sys.path —— 这是刻意的分层例外：
    上线特征必须和训练时逐列一致，重写一份等于把口径漂移风险写进代码。
    ``scripts/factor_mining_daemon.py`` 已是同样的做法。
    """
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if not (scripts_dir / "train_ml_factor.py").exists():
        raise RuntimeError(
            f"找不到特征管线 {scripts_dir / 'train_ml_factor.py'} —— "
            "ML scorer 依赖它保证训练/推理特征一致",
        )
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import train_ml_factor  # noqa: PLC0415

    return train_ml_factor


def _predict_ranks(model_dir: Path, cross: Any) -> Any:
    """单模型推理 → 截面百分位排名 Series（index=symbol）。

    返回排名而非原始分数：多模型集成时 ``rank_average`` 才有意义，
    不同模型的分数量纲不可比。
    """
    manifest = model_dir / "model_manifest.json"
    if manifest.is_file():
        from aipha.research import load_verified_selector

        selector = load_verified_selector(
            model_dir,
            expected_feature_version=str(cross.attrs.get("feature_version", "cn_equity_v1")),
        )
    else:
        if cross.attrs.get("canonical_pit"):
            raise ValueError(
                f"canonical PIT inference rejects legacy model without model_manifest.json: {model_dir}"
            )
        from aipha.models.ml_stock_selector import MLStockSelector

        selector = MLStockSelector()
        selector.load(model_dir, allow_experimental_models=True)
    _check_feature_coverage(selector, cross, model_dir)

    scores = selector.predict(cross)
    ranks = scores.rank(pct=True)
    # 同一 symbol 理论上当日只有一行；真出现重复取均值，避免 reindex 报错。
    return ranks.groupby(level=0).mean()


def _check_feature_coverage(selector: Any, cross: Any, model_dir: Path) -> None:
    """核对模型训练特征在当日截面里的覆盖率。

    ``MLStockSelector._resolve_features`` 对缺失特征取交集后照常推理，
    缺一半特征也不会报错 —— 那是最危险的一种失败：预测照样出数，只是不
    可信。这里把它变成显式错误。
    """
    expected: list[str] = []
    models = getattr(selector, "_models", None)
    if models:
        try:
            import lightgbm as lgb

            booster = models[-1]
            if isinstance(booster, lgb.Booster):
                expected = list(booster.feature_name())
        except Exception:  # 非 LGB 模型没有 feature_name，跳过
            expected = []
    if not expected:
        cfg_features = getattr(getattr(selector, "_config", None), "feature_cols", None)
        expected = list(cfg_features or [])
    if not expected:
        logger.warning(
            "[ml] %s 无法取到训练特征名，跳过覆盖率校验", model_dir,
        )
        return

    matched = [c for c in expected if c in cross.columns]
    coverage = len(matched) / len(expected)
    if coverage < MIN_FEATURE_COVERAGE:
        missing = [c for c in expected if c not in cross.columns]
        raise RuntimeError(
            f"ML scorer: {model_dir} 特征覆盖率 {coverage:.1%} < "
            f"{MIN_FEATURE_COVERAGE:.0%}（缺 {len(missing)} 个，"
            f"例如 {missing[:8]}）。特征管线与训练时不一致，"
            "带着残缺特征推理出的分数不可用。",
        )
    if coverage < 1.0:
        logger.warning(
            "[ml] %s 特征覆盖率 %.1f%%（缺 %d 个）",
            model_dir, coverage * 100, len(expected) - len(matched),
        )


def score_ml_csi2000(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """CSI2000 小微盘 ML 选股（r115 的 ml 组件）。"""
    return _score_ml(gw, comp_cfg, trade_date, default_universe="csi2000")


def score_ml_csi1000(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """CSI1000 中小盘 ML 选股。"""
    return _score_ml(gw, comp_cfg, trade_date, default_universe="csi1000")


def score_ml_stocks(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """通用 A 股 ML 选股 —— universe 由配置给定，默认全市场。"""
    return _score_ml(gw, comp_cfg, trade_date, default_universe="all")


def score_v2_ml_ensemble(gw: Any, comp_cfg: dict, trade_date: str) -> list[str]:
    """V2 多模型集成（rank_average），见 ``configs/paper/v2_ml_ensemble.yml``。"""
    method = str(comp_cfg.get("ensemble_method", "rank_average") or "").strip()
    if method and method != "rank_average":
        raise ValueError(
            f"v2_ml_ensemble: 只实现了 rank_average，配置要求 {method!r}",
        )
    if not comp_cfg.get("model_dirs"):
        raise ValueError("v2_ml_ensemble: 配置缺 model_dirs")
    return _score_ml(gw, comp_cfg, trade_date, default_universe="csi2000")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

register_scorer("cb_stock_quality", score_cb_stock_quality)
register_scorer("etf_faa", score_etf_faa)
register_scorer("ml_csi2000", score_ml_csi2000)
register_scorer("ml_csi1000", score_ml_csi1000)
register_scorer("ml_stocks", score_ml_stocks)
register_scorer("v2_ml_ensemble", score_v2_ml_ensemble)


__all__ = [
    "MIN_FEATURE_COVERAGE",
    "ScorerFn",
    "available_scorers",
    "get_scorer",
    "register_scorer",
    "resolve_scorer_name",
    "score_cb_stock_quality",
    "score_etf_faa",
    "score_ml_csi1000",
    "score_ml_csi2000",
    "score_ml_stocks",
    "score_v2_ml_ensemble",
]
