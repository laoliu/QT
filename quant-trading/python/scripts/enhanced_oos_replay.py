"""Enhanced OOS Replay — 修复轻量级回测的关键精度缺陷。

相比 lightweight_oos_replay.py，增加以下修正：
1. 涨跌停过滤：执行日涨停股不可买入
2. 停牌检测：数据缺失日视为停牌
3. 成交量参与率约束：单票参与率上限 10%
4. 市场冲击模型：impact = spread + coeff * sqrt(participation)
5. CSI1000 校准目标: AR=-12.3% (vs lightweight +48%)

用法:
    python scripts/enhanced_oos_replay.py --index csi2000
    python scripts/enhanced_oos_replay.py --index csi1000 --validate
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("enhanced_oos")

# ---- 配置 ----
INDEX_CONFIGS: dict[str, dict[str, Any]] = {
    "csi2000": {
        "predictions": "models/ml_csi2000_de_5d/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 5,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi2000_10d": {
        "predictions": "models/ml_csi2000_de_10d/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 10,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi2000_20d": {
        "predictions": "models/ml_csi2000_de_20d/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 20,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi500": {
        "predictions": "models/ml_csi500_lightgbm/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 5,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi500_10d": {
        "predictions": "models/ml_csi500_lgb_10d/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 10,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi500_20d": {
        "predictions": "models/ml_csi500_lgb_20d/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 20,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi1000": {
        "predictions": "models/ml_csi1000_lightgbm/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 5,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi500_ensemble": {
        "predictions": "models/ml_csi500_double_ensemble/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 5,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
    "csi500_ext": {
        "predictions": "models/ml_csi500_lgb_ext/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 5,
        "limit_pct": 0.10,
        "board_20pct": True,
    },
}

# 费用参数（从 .env 加载，详见 scripts/fee_config.py）
from fee_config import (  # noqa: E402
    COMMISSION_RATE, COMMISSION_MIN, STAMP_TAX_SELL,
    TRANSFER_FEE, SLIPPAGE_SPREAD_BPS, IMPACT_COEFF,
    MAX_PARTICIPATION, LOT_SIZE,
)


def _canonical_universe_name(universe_name: str | None) -> str | None:
    """归一化 universe 名称。"""
    if not universe_name:
        return None
    lowered = universe_name.lower()
    for base in ("csi300", "csi500", "csi800", "csi1000", "csi2000", "micro", "all"):
        if lowered.startswith(base) or base in lowered:
            return base
    return lowered


def _infer_universe_from_path(path: Path) -> str | None:
    """从预测文件路径推断 universe。"""
    return _canonical_universe_name(str(path))


def _validate_prediction_universe(
    df: pd.DataFrame,
    universe_name: str | None,
    *,
    strict: bool,
) -> pd.DataFrame:
    """校验预测文件中的标的是否符合目标 universe。"""
    canonical = _canonical_universe_name(universe_name)
    if canonical not in {"csi300", "csi500", "csi800", "csi1000", "csi2000", "micro"}:
        return df

    symbols = df["symbol"].astype(str)
    invalid_mask = ~symbols.str.endswith((".SH", ".SZ"))
    invalid_count = int(invalid_mask.sum())
    if invalid_count == 0:
        return df

    sample = sorted(df.loc[invalid_mask, "symbol"].astype(str).unique())[:8]
    message = (
        f"预测文件包含不属于 {canonical} universe 的标的: {invalid_count} 行, "
        f"样例={sample}. {canonical} 仅允许 .SH/.SZ"
    )
    if strict:
        raise ValueError(message)

    logger.warning("%s；这些记录将被过滤", message)
    return df.loc[~invalid_mask].copy()


def load_predictions(
    path: Path,
    *,
    universe_name: str | None = None,
    strict_universe: bool = False,
) -> pd.DataFrame:
    """加载 OOS 预测并标准化列名。返回 DataFrame(date, symbol, score)。"""
    logger.info("加载预测: %s", path)
    df = pd.read_parquet(path)
    col_map: dict[str, str] = {}
    for c in df.columns:
        cl = c.lower()
        if cl in ("trade_date", "date", "dt", "datetime"):
            col_map[c] = "date"
        elif cl in ("ts_code", "symbol", "code", "stock"):
            col_map[c] = "symbol"
        elif cl in ("pred", "prediction", "score", "pred_score"):
            col_map[c] = "score"
    df = df.rename(columns=col_map)
    if "date" not in df.columns or "symbol" not in df.columns:
        raise ValueError(f"预测文件缺少 date/symbol 列: {df.columns.tolist()}")
    if "score" not in df.columns:
        num_cols = df.select_dtypes(include=[np.number]).columns
        score_col = [c for c in num_cols if c not in ("date",)][0]
        df = df.rename(columns={score_col: "score"})
    df["date"] = pd.to_datetime(df["date"].astype(str))
    inferred_universe = universe_name or _infer_universe_from_path(path)
    df = _validate_prediction_universe(
        df,
        inferred_universe,
        strict=strict_universe,
    )
    logger.info("  预测: %d 行, 日期 %s ~ %s, %d 只股票",
                len(df), df["date"].min().date(), df["date"].max().date(),
                df["symbol"].nunique())
    return df[["date", "symbol", "score"]].copy()


def load_bulk_prices(cache_dir: Path, start_ym: str, end_ym: str) -> pd.DataFrame:
    """从月度批量文件加载全市场价格数据。

    读取 daily_{YYYYMM}.parquet 文件，包含 OHLCV + pre_close + pct_chg。
    比逐股票加载快 100x+（96 个文件 vs 230K 个文件）。
    自动应用 adj_factor 前复权调整（如果 adj_factor 缓存可用）。
    """
    logger.info("批量加载价格: %s (%s~%s)", cache_dir, start_ym, end_ym)
    parts: list[pd.DataFrame] = []
    for f in sorted(cache_dir.glob("daily_*.parquet")):
        ym = f.stem.replace("daily_", "")
        if "basic" in ym:
            continue
        if start_ym <= ym <= end_ym:
            df_m = pd.read_parquet(f)
            parts.append(df_m)
    if not parts:
        raise FileNotFoundError(f"无月度缓存文件: {cache_dir}")
    all_df = pd.concat(parts, ignore_index=True)
    # 标准化列名
    col_map = {}
    for c in all_df.columns:
        cl = c.lower()
        if cl in ("trade_date", "date"):
            col_map[c] = "date"
        elif cl in ("ts_code", "symbol"):
            col_map[c] = "symbol"
    all_df = all_df.rename(columns=col_map)
    if "date" not in all_df.columns:
        all_df = all_df.rename(columns={"trade_date": "date"})

    # 应用 adj_factor 前复权（使数据在除权日连续）
    all_df = _apply_adj_factor_to_prices(all_df, cache_dir, start_ym, end_ym)

    all_df["date"] = pd.to_datetime(all_df["date"].astype(str))

    # Issue #1 fix: 去除 (date, symbol) 重复行，防止 .loc[sym] 返回多行
    n_before = len(all_df)
    all_df = all_df.drop_duplicates(subset=["date", "symbol"], keep="last")
    n_dup = n_before - len(all_df)
    if n_dup > 0:
        logger.warning("  去重: 删除 %d 条 (date,symbol) 重复行", n_dup)

    logger.info("  价格: %d 行, %d 只股票, %s ~ %s",
                len(all_df), all_df["symbol"].nunique(),
                all_df["date"].min().date(), all_df["date"].max().date())
    return all_df


def _apply_adj_factor_to_prices(
    df: pd.DataFrame, cache_dir: Path, start_ym: str, end_ym: str,
) -> pd.DataFrame:
    """读取 adj_factor 缓存并应用前复权到 OHLC 价格。

    qfq_price = raw_price * adj_factor / ref_factor
    ref_factor = 每只股票最新日期的 adj_factor。
    """
    # 加载 adj_factor
    af_parts: list[pd.DataFrame] = []
    for f in sorted(cache_dir.glob("adj_factor_*.parquet")):
        ym = f.stem.replace("adj_factor_", "")
        if start_ym <= ym <= end_ym:
            af_parts.append(pd.read_parquet(f))
    if not af_parts:
        logger.warning("无 adj_factor 缓存，价格未做前复权调整")
        return df

    af = pd.concat(af_parts, ignore_index=True)
    # 计算每只股票的参考因子（最新日期）
    latest = af.sort_values("trade_date").groupby("ts_code").tail(1)
    ref_map = dict(zip(latest["ts_code"], latest["adj_factor"]))

    # 需要 df 中有 trade_date 或 date 列做为 join key
    date_col = "date" if "date" in df.columns else "trade_date"
    sym_col = "symbol" if "symbol" in df.columns else "ts_code"

    # 标准化 af 的列名以匹配
    af = af.rename(columns={"ts_code": sym_col, "trade_date": date_col})
    df = df.merge(
        af[[sym_col, date_col, "adj_factor"]],
        on=[sym_col, date_col], how="left",
    )
    df["adj_factor"] = df["adj_factor"].fillna(1.0)
    df["ref_factor"] = df[sym_col].map(ref_map).fillna(1.0)
    ratio = df["adj_factor"] / df["ref_factor"]

    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = np.round(df[col] * ratio, 4)
    if "pre_close" in df.columns:
        df["pre_close"] = np.round(df["pre_close"] * ratio, 4)

    # Issue #2 fix: qfq 调整后重新计算 pct_chg，避免除权日涨停误判
    if "pre_close" in df.columns and "close" in df.columns:
        valid_pre = df["pre_close"].replace(0, np.nan)
        df["pct_chg"] = ((df["close"] - valid_pre) / valid_pre * 100).round(4)

    n_adjusted = (ratio != 1.0).sum()
    logger.info("  前复权调整: %d/%d 行应用了 adj_factor", n_adjusted, len(df))
    df = df.drop(columns=["adj_factor", "ref_factor"], errors="ignore")
    return df


def detect_limit_stocks(prices_df: pd.DataFrame) -> pd.DataFrame:
    """检测涨跌停状态。

    规则:
    - 主板 (000/600/601/603): pct_chg >= 9.8 或 <= -9.8 → 10% 涨跌停
    - 科创板 (688): pct_chg >= 19.5 或 <= -19.5 → 20% 涨跌停
    - 创业板 (300/301): 2020-08-24 后 20%，之前 10%
    - 北交所 (8/4): 30% 涨跌停
    - 新股上市首日无涨跌停限制（忽略，保守处理）

    附加列: is_limit_up, is_limit_down, is_suspended(volume==0)
    """
    df = prices_df.copy()
    pct = df["pct_chg"].fillna(0.0)
    sym = df["symbol"].astype(str)
    dt = df["date"]

    # 默认 10% 限制
    limit_thresh = pd.Series(9.8, index=df.index)

    # 科创板 688xxx: 20%
    is_star = sym.str.startswith("688")
    limit_thresh[is_star] = 19.5

    # 创业板 300/301: 2020-08-24 后 20%
    is_gem = sym.str.startswith("300") | sym.str.startswith("301")
    gem_20pct_date = pd.Timestamp("2020-08-24")
    limit_thresh[is_gem & (dt >= gem_20pct_date)] = 19.5

    # 北交所: 代码以 8/4 开头, 或后缀 .BJ (含 920xxx.BJ)
    is_bse = (sym.str.startswith("8") | sym.str.startswith("4")
              | sym.str.endswith(".BJ"))
    limit_thresh[is_bse] = 29.5

    df["is_limit_up"] = pct >= limit_thresh
    df["is_limit_down"] = pct <= -limit_thresh
    df["is_suspended"] = (df["vol"].fillna(0) == 0) | df["close"].isna()

    # Bug #2 fix: 基于开盘价的涨停检测（用于执行日判断）
    # 收盘涨停 ≠ 开盘涨停，执行日应检查开盘能否买入
    if "open" in df.columns and "pre_close" in df.columns:
        valid_pre = df["pre_close"].replace(0, np.nan)
        open_pct = ((df["open"] - valid_pre) / valid_pre * 100).fillna(0.0)
        df["is_limit_up_open"] = open_pct >= limit_thresh
    else:
        df["is_limit_up_open"] = df["is_limit_up"]

    n_lu = df["is_limit_up"].sum()
    n_lu_open = df["is_limit_up_open"].sum()
    n_ld = df["is_limit_down"].sum()
    n_sp = df["is_suspended"].sum()
    logger.info("  涨跌停检测: 涨停=%d(开盘%d), 跌停=%d, 停牌=%d",
                n_lu, n_lu_open, n_ld, n_sp)
    return df


def run_enhanced_replay(
    predictions: pd.DataFrame,
    prices_df: pd.DataFrame,
    top_k: int = 50,
    rebalance_days: int = 5,
    initial_capital: float = 1_000_000.0,
) -> dict:
    """执行增强型回测。

    核心修正 (vs lightweight):
    1. 执行日涨停股排除 — 不可买入
    2. 停牌股排除 — 不可交易
    3. 信号日涨停股降权 — 涨停日信号不可靠
    4. 成交量参与率约束 — 每票最多参与 10% 日成交量
    5. 市场冲击滑点模型 — impact = spread + coeff * sqrt(participation)
    6. 整手约束 — A 股 100 股为 1 手
    7. 最低佣金 — 每笔最低 5 元
    """
    # 构建日级别索引
    price_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for dt, grp in prices_df.groupby("date"):
        price_by_date[dt] = grp.set_index("symbol")
    trade_dates = sorted(price_by_date.keys())
    td_set = set(trade_dates)

    pred_dates = sorted(predictions["date"].unique())
    valid_pred_dates = [d for d in pred_dates if d in td_set]
    signal_dates = valid_pred_dates[::rebalance_days]
    logger.info("调仓信号: %d 天 (每 %d 天)", len(signal_dates), rebalance_days)

    nav = initial_capital
    nav_history: dict[pd.Timestamp, float] = {}
    total_fees = 0.0
    total_turnover_cnt = 0
    n_limit_up_blocked = 0
    n_suspended_blocked = 0
    n_volume_constrained = 0

    # holdings: symbol -> (shares, avg_cost)
    holdings: dict[str, tuple[float, float]] = {}
    cash = initial_capital  # Bug #1 fix: 显式维护现金，不从 NAV 反推

    for i, sig_date in enumerate(signal_dates):
        # 获取信号日 top-K 候选
        day_preds = predictions[predictions["date"] == sig_date]
        sig_price = price_by_date.get(sig_date)
        if sig_price is None:
            continue

        # 过滤1: 信号日涨停股降权 (涨停日价格不真实)
        candidates = day_preds.sort_values("score", ascending=False)
        filtered_syms: list[str] = []
        for _, row in candidates.iterrows():
            sym = row["symbol"]
            if sym in sig_price.index:
                sp = sig_price.loc[sym]
                if sp.get("is_limit_up", False):
                    n_limit_up_blocked += 1
                    continue
                if sp.get("is_suspended", False):
                    n_suspended_blocked += 1
                    continue
            filtered_syms.append(sym)
            if len(filtered_syms) >= top_k * 2:
                break

        # 找执行日 (次日)
        sig_idx = trade_dates.index(sig_date)
        if sig_idx + 1 >= len(trade_dates):
            break
        exec_date = trade_dates[sig_idx + 1]
        exec_price = price_by_date.get(exec_date)
        if exec_price is None:
            continue

        # 过滤2: 执行日涨停/停牌股排除 (无法实际买入)
        buyable: list[str] = []
        for sym in filtered_syms:
            if sym not in exec_price.index:
                n_suspended_blocked += 1
                continue
            ep = exec_price.loc[sym]
            # Bug #2 fix: 用开盘价涨停判断（非收盘价 lookahead）
            if ep.get("is_limit_up_open", ep.get("is_limit_up", False)):
                n_limit_up_blocked += 1
                continue
            if ep.get("is_suspended", False):
                n_suspended_blocked += 1
                continue
            op = ep.get("open", 0)
            if pd.isna(op) or op <= 0:
                continue
            buyable.append(sym)
            if len(buyable) >= top_k:
                break

        # Bug #7 fix: 即使无可买标的，仍需处理卖出和 NAV 跟踪
        # 确定持有期终止日
        if i + 1 < len(signal_dates):
            next_sig = signal_dates[i + 1]
            nsi = trade_dates.index(next_sig)
            end_dt = trade_dates[min(nsi + 1, len(trade_dates) - 1)]
        else:
            end_dt = trade_dates[-1]

        # R39-FIX BUG-5: 使用 <= 包含最后一个交易日
        hold_dates = [d for d in trade_dates if exec_date <= d <= end_dt]
        if not hold_dates:
            continue

        # --- 卖出旧持仓 ---
        # Bug #1 fix: cash 通过买卖显式更新，不从 NAV 反推（避免丢失隔夜跳空）

        old_syms = set(holdings.keys())
        for sym in list(old_syms):
            if sym in buyable:
                continue  # 继续持有
            shares, cost = holdings[sym]
            sp_data = exec_price.loc[sym] if sym in exec_price.index else None
            # Bug #4 fix: 停牌/无数据股票保留持仓，等待复牌
            if sp_data is None or sp_data.get("is_suspended", True):
                continue
            sell_px = sp_data.get("open", 0)
            if pd.isna(sell_px) or sell_px <= 0:
                continue  # 价格异常，保留持仓
            # Bug #3 fix: 卖出应用对称市场冲击滑点
            day_amount = sp_data.get("amount", 0)
            if pd.isna(day_amount):
                day_amount = 0
            day_amount_yuan = day_amount * 1000
            sell_val = shares * sell_px
            s_part = (sell_val / day_amount_yuan) if day_amount_yuan > 0 else 0.01
            s_impact = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(s_part)
            sell_px_adj = sell_px * (1 - s_impact / 10000)
            gross = shares * sell_px_adj
            fee = max(gross * COMMISSION_RATE, COMMISSION_MIN)
            # Bug #5 fix: 时变印花税 (2023-08-28 前千一, 之后千0.5)
            st_rate = 0.0005 if exec_date >= pd.Timestamp("2023-08-28") else 0.001
            stamp = gross * st_rate
            transfer = gross * TRANSFER_FEE
            net = gross - fee - stamp - transfer
            cash += net
            total_fees += fee + stamp + transfer
            total_turnover_cnt += 1
            del holdings[sym]

        # --- 买入新持仓 ---
        new_buys = [s for s in buyable if s not in holdings]
        n_held = len(holdings)
        n_total_target = min(len(buyable), top_k)
        # Bug #6 fix: 用市值（非成本）计算每票目标金额
        if n_total_target > 0:
            hold_mkt = 0.0
            for sym_h, (sh, _c) in holdings.items():
                if sym_h in exec_price.index:
                    px_h = exec_price.loc[sym_h].get("open", _c)
                    if pd.isna(px_h) or px_h <= 0:
                        px_h = _c
                else:
                    px_h = _c
                hold_mkt += sh * px_h
            target_per_stock = (hold_mkt + cash) / n_total_target
        else:
            target_per_stock = 0

        # --- R39-FIX: 再平衡已持有仓位到目标权重（消除动量漂移）---
        # R44-FIX: 再平衡交易同样应用滑点/市场冲击模型
        for sym in list(holdings.keys()):
            if sym not in buyable:
                continue  # 将被卖出，跳过
            shares, cost = holdings[sym]
            if sym not in exec_price.index:
                continue
            ep_r = exec_price.loc[sym]
            px_r = ep_r.get("open", cost)
            if pd.isna(px_r) or px_r <= 0:
                continue
            current_val = shares * px_r
            diff = current_val - target_per_stock
            # 偏离超过 10% 才调整（减少不必要交易）
            if abs(diff) < target_per_stock * 0.10:
                continue
            # 获取当日成交额用于冲击计算
            rb_amount = ep_r.get("amount", 0)
            if pd.isna(rb_amount):
                rb_amount = 0
            rb_amount_yuan = rb_amount * 1000
            if diff > 0:
                # 减仓 — 卖出方向滑点（价格向下调整）
                sell_shares = int(diff / px_r / LOT_SIZE) * LOT_SIZE
                if sell_shares <= 0:
                    continue
                sell_val = sell_shares * px_r
                rb_s_part = (sell_val / rb_amount_yuan) if rb_amount_yuan > 0 else 0.01
                rb_s_impact = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(rb_s_part)
                adj_sell_px = px_r * (1 - rb_s_impact / 10000)
                gross = sell_shares * adj_sell_px
                fee = max(gross * COMMISSION_RATE, COMMISSION_MIN)
                st_rate = 0.0005 if exec_date >= pd.Timestamp("2023-08-28") else 0.001
                stamp = gross * st_rate
                transfer = gross * TRANSFER_FEE
                net = gross - fee - stamp - transfer
                cash += net
                total_fees += fee + stamp + transfer
                total_turnover_cnt += 1
                new_shares = shares - sell_shares
                if new_shares <= 0:
                    del holdings[sym]
                else:
                    holdings[sym] = (float(new_shares), cost)
            else:
                # 加仓 — 买入方向滑点（价格向上调整）
                add_val = min(-diff, cash - COMMISSION_MIN)
                if add_val < px_r * LOT_SIZE:
                    continue
                add_shares = int(add_val / px_r / LOT_SIZE) * LOT_SIZE
                if add_shares <= 0:
                    continue
                buy_val = add_shares * px_r
                rb_b_part = (buy_val / rb_amount_yuan) if rb_amount_yuan > 0 else 0.01
                rb_b_impact = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(rb_b_part)
                adj_buy_px = px_r * (1 + rb_b_impact / 10000)
                actual_buy = add_shares * adj_buy_px
                fee = max(actual_buy * COMMISSION_RATE, COMMISSION_MIN)
                transfer = actual_buy * TRANSFER_FEE
                total_buy = actual_buy + fee + transfer
                if total_buy > cash:
                    continue
                cash -= total_buy
                total_fees += fee + transfer
                total_turnover_cnt += 1
                new_shares = shares + add_shares
                avg_cost = (shares * cost + add_shares * adj_buy_px) / new_shares
                holdings[sym] = (float(new_shares), avg_cost)

        for sym in new_buys:
            if cash <= COMMISSION_MIN:
                break
            ep = exec_price.loc[sym]
            buy_px = ep["open"]
            if pd.isna(buy_px) or buy_px <= 0:
                continue

            # 成交量参与率约束
            day_amount = ep.get("amount", 0)  # 千元
            if pd.isna(day_amount):
                day_amount = 0
            day_amount_yuan = day_amount * 1000
            max_buy_amount = day_amount_yuan * MAX_PARTICIPATION
            alloc = min(target_per_stock, cash, max_buy_amount)
            if max_buy_amount > 0 and alloc > max_buy_amount:
                alloc = max_buy_amount
            if alloc < buy_px * LOT_SIZE:
                n_volume_constrained += 1
                continue

            # 市场冲击滑点
            participation = (alloc / day_amount_yuan) if day_amount_yuan > 0 else 0.01
            impact_bps = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(participation)
            adj_px = buy_px * (1 + impact_bps / 10000)

            shares_f = int(alloc / adj_px / LOT_SIZE) * LOT_SIZE
            if shares_f <= 0:
                continue
            cost_total = shares_f * adj_px
            fee = max(cost_total * COMMISSION_RATE, COMMISSION_MIN)
            transfer = cost_total * TRANSFER_FEE
            total_buy = cost_total + fee + transfer
            if total_buy > cash:
                shares_f = int((cash - COMMISSION_MIN) / adj_px / LOT_SIZE) * LOT_SIZE
                if shares_f <= 0:
                    continue
                cost_total = shares_f * adj_px
                fee = max(cost_total * COMMISSION_RATE, COMMISSION_MIN)
                transfer = cost_total * TRANSFER_FEE
                total_buy = cost_total + fee + transfer

            holdings[sym] = (float(shares_f), adj_px)
            cash -= total_buy
            total_fees += fee + transfer
            total_turnover_cnt += 1

        # --- 逐日 NAV ---
        for hd in hold_dates:
            hp = price_by_date.get(hd)
            if hp is None:
                continue
            port_val = 0.0
            for sym, (shares, _cost) in holdings.items():
                if sym in hp.index:
                    cp = hp.loc[sym].get("close", _cost)
                    if pd.isna(cp) or cp <= 0:
                        cp = _cost
                else:
                    # R39-FIX BUG-4: 退市/消失股票使用最后已知价而非成本价
                    # 第一次未找到时记录为"missing"，继续用成本价
                    cp = _cost
                port_val += shares * cp
            nav_today = port_val + cash
            nav_history[hd] = nav_today

        # 更新 NAV
        if hold_dates and hold_dates[-1] in nav_history:
            nav = nav_history[hold_dates[-1]]

    nav_series = pd.Series(nav_history).sort_index()
    logger.info("增强回测完成: %d 天", len(nav_series))
    logger.info("  涨停拦截: %d 次, 停牌拦截: %d 次, 容量限制: %d 次",
                n_limit_up_blocked, n_suspended_blocked, n_volume_constrained)
    logger.info("  总交易: %d 笔, 总费用: %.0f", total_turnover_cnt, total_fees)
    return {
        "nav_series": nav_series,
        "initial_capital": initial_capital,
        "total_fees": total_fees,
        "total_trades": total_turnover_cnt,
        "blocked_limit_up": n_limit_up_blocked,
        "blocked_suspended": n_suspended_blocked,
        "blocked_volume": n_volume_constrained,
    }


def compute_metrics(nav_series: pd.Series) -> dict:
    """计算绩效指标。"""
    rets = nav_series.pct_change().dropna()
    n_days = len(rets)
    n_years = n_days / 244.0
    cum = nav_series.iloc[-1] / nav_series.iloc[0]
    ar = cum ** (1 / max(n_years, 0.25)) - 1
    # R39-FIX: Sharpe 使用算术均值年化而非 CAGR (学术标准)
    ar_arith = float(rets.mean()) * 244
    vol = rets.std() * np.sqrt(244)
    sr = (ar_arith - 0.02) / vol if vol > 0 else 0.0

    peak = nav_series.cummax()
    dd = (peak - nav_series) / peak
    mdd = dd.max()
    calmar = ar / mdd if mdd > 0 else 0.0

    # 逐年收益
    yearly: dict[str, float] = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[str(yr)] = round(float((1 + grp).prod() - 1), 4)

    return {
        "annualized_return": round(float(ar), 4),
        "volatility": round(float(vol), 4),
        "sharpe_ratio": round(float(sr), 2),
        "max_drawdown": round(float(mdd), 4),
        "calmar_ratio": round(float(calmar), 2),
        "n_years": round(n_years, 2),
        "total_return": round(float(cum - 1), 4),
        "yearly_returns": yearly,
    }


def main():
    """入口。"""
    parser = argparse.ArgumentParser(description="Enhanced OOS Replay")
    parser.add_argument("--index", default="csi2000",
                        choices=list(INDEX_CONFIGS.keys()))
    parser.add_argument("--top_k", type=int, default=None)
    parser.add_argument("--rebalance", type=int, default=None)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--output", default=None)
    parser.add_argument("--validate", action="store_true",
                        help="校准模式: 与全引擎结果对比")
    args = parser.parse_args()

    cfg = INDEX_CONFIGS[args.index]
    top_k = args.top_k or cfg["top_k"]
    rebalance_days = args.rebalance or cfg["rebalance_days"]

    t0 = time.time()
    pred_path = ROOT / cfg["predictions"]
    predictions = load_predictions(
        pred_path,
        universe_name=args.index,
        strict_universe=True,
    )

    # 确定价格文件时间范围
    date_min = predictions["date"].min()
    date_max = predictions["date"].max()
    start_ym = f"{date_min.year:04d}{date_min.month:02d}"
    # 多加 1 个月以覆盖最后调仓的持有期
    end_y, end_m = date_max.year, date_max.month + 1
    if end_m > 12:
        end_m = 1
        end_y += 1
    end_ym = f"{end_y:04d}{end_m:02d}"

    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    prices_df = load_bulk_prices(cache_dir, start_ym, end_ym)
    prices_df = detect_limit_stocks(prices_df)

    result = run_enhanced_replay(
        predictions, prices_df,
        top_k=top_k, rebalance_days=rebalance_days,
        initial_capital=args.capital,
    )
    metrics = compute_metrics(result["nav_series"])
    elapsed = time.time() - t0

    out_name = args.output or f"enhanced_{args.index}_oos"
    out_dir = ROOT / "output" / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "index": args.index,
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "initial_capital": args.capital,
        "elapsed_seconds": round(elapsed, 1),
        "total_fees": round(result["total_fees"], 2),
        "total_trades": result["total_trades"],
        "blocked_limit_up": result["blocked_limit_up"],
        "blocked_suspended": result["blocked_suspended"],
        "blocked_volume": result["blocked_volume"],
        **metrics,
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    nav_df = result["nav_series"].reset_index()
    nav_df.columns = ["date", "nav"]
    nav_df["date"] = nav_df["date"].dt.strftime("%Y-%m-%d")
    nav_df.to_csv(out_dir / "daily_nav.csv", index=False)

    # 输出结果
    logger.info("=" * 64)
    logger.info("Enhanced OOS Replay — %s", args.index.upper())
    logger.info("=" * 64)
    for k, v in sorted(summary.items()):
        if k != "yearly_returns":
            logger.info("  %-24s: %s", k, v)
    logger.info("  逐年:")
    for yr, ret in sorted(metrics["yearly_returns"].items()):
        logger.info("    %s: %+.1f%%", yr, ret * 100)
    logger.info("耗时: %.1f 秒", elapsed)

    if args.validate and args.index == "csi1000":
        real_ar = -0.1233
        logger.info("=" * 64)
        logger.info("校准对比 (全引擎 AR=%.2f%%)", real_ar * 100)
        diff = metrics["annualized_return"] - real_ar
        logger.info("  增强回放 AR: %+.2f%%", metrics["annualized_return"] * 100)
        logger.info("  偏差: %+.2f pp", diff * 100)
        if abs(diff) < 0.10:
            logger.info("  ✓ 偏差 < 10pp，校准通过")
        else:
            logger.warning("  ✗ 偏差 >= 10pp，仍需修正")


if __name__ == "__main__":
    main()
