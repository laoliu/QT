#!/usr/bin/env python
"""P4 因子分层 IC 验证 — Phase 0 门禁实验。

验证 3 个 P0 因子在不同市值段的 IC (Rank IC):
  1. EV/EBITDA — 企业价值/EBITDA
  2. Piotroski F-Score — 9 维质量评分
  3. Analyst Revision — 分析师 EPS 一致预期修正率

分层: <20B / 20-50B / 50-130B / >130B / 全截面
Go/No-Go 阈值: 50-130B 中 ≥2 个因子 IC > 2.5%

Usage:
    python scripts/validate_p4_factors_ic.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "cache" / "equity_tushare"
ALT_CACHE = ROOT / "data" / "cache" / "alternative"
FWD_DAYS = 5

MV_BINS = {
    "<20B": (0, 20),
    "20-50B": (20, 50),
    "50-130B": (50, 130),
    ">130B": (130, 1e6),
    "ALL": (0, 1e6),
}


# ── 数据加载 ──────────────────────────────────────────────
def load_daily_data() -> pd.DataFrame:
    """加载全市场日线 + daily_basic (circ_mv/pe_ttm/pb/total_mv)."""
    # 加载日线 OHLCV
    parts: list[pd.DataFrame] = []
    for f in sorted(CACHE_DIR.glob("daily_2*.parquet")):
        if "basic" in f.name:
            continue
        parts.append(pd.read_parquet(f))
    if not parts:
        raise FileNotFoundError("No daily_*.parquet in cache")
    daily = pd.concat(parts, ignore_index=True)
    # 加载 daily_basic
    basic_parts: list[pd.DataFrame] = []
    for f in sorted(CACHE_DIR.glob("daily_basic_*.parquet")):
        basic_parts.append(pd.read_parquet(f))
    if basic_parts:
        basic = pd.concat(basic_parts, ignore_index=True)
        basic_cols = ["ts_code", "trade_date", "circ_mv", "total_mv",
                      "pe_ttm", "pb", "turnover_rate"]
        basic = basic[[c for c in basic_cols if c in basic.columns]]
        # 去重
        basic = basic.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        daily = daily.merge(basic, on=["ts_code", "trade_date"], how="left",
                           suffixes=("", "_basic"))
    daily["datetime"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    daily = daily.rename(columns={"ts_code": "symbol"})
    for col in ["circ_mv", "total_mv", "pe_ttm", "pb"]:
        if col in daily.columns:
            daily[col] = pd.to_numeric(daily[col], errors="coerce")
    # circ_mv 万元 → 亿元
    if "circ_mv" in daily.columns:
        daily["circ_mv_yi"] = daily["circ_mv"] / 10000.0
    daily = daily.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    # 前向收益
    daily["fwd_return"] = daily.groupby("symbol")["close"].transform(
        lambda s: s.shift(-FWD_DAYS) / s - 1
    )
    logger.info("日线: %d 行, %d 只, %s~%s",
                len(daily), daily["symbol"].nunique(),
                daily["datetime"].min().date(), daily["datetime"].max().date())
    return daily


def load_fina_indicator_bulk(symbols: list[str]) -> pd.DataFrame:
    """批量加载 fina_indicator PIT 数据."""
    cols_need = [
        "ts_code", "ann_date", "end_date",
        "roe", "roa", "cfps", "eps", "bps",
        "grossprofit_margin", "netprofit_margin",
        "debt_to_assets", "current_ratio", "quick_ratio",
        "dt_netprofit_yoy", "op_yoy", "revenue_yoy",
    ]
    parts = []
    for sym in symbols:
        fp = CACHE_DIR / f"fina_indicator_{sym.replace('.', '_')}.parquet"
        if not fp.exists():
            continue
        try:
            p = pd.read_parquet(fp)
            keep = [c for c in cols_need if c in p.columns]
            p = p[keep].copy()
            p["symbol"] = sym
            parts.append(p)
        except Exception:
            continue
    if not parts:
        return pd.DataFrame()
    fina = pd.concat(parts, ignore_index=True)
    for c in cols_need[3:]:
        if c in fina.columns:
            fina[c] = pd.to_numeric(fina[c], errors="coerce")
    fina["ann_date"] = pd.to_datetime(fina["ann_date"], format="%Y%m%d", errors="coerce")
    fina = fina.dropna(subset=["ann_date"])
    fina = fina.sort_values(["symbol", "ann_date", "end_date"])
    fina = fina.drop_duplicates(subset=["symbol", "ann_date"], keep="last")
    logger.info("fina_indicator: %d 标的, %d 条", fina["symbol"].nunique(), len(fina))
    return fina


def load_balancesheet_bulk(symbols: list[str]) -> pd.DataFrame:
    """批量加载资产负债表 PIT 数据."""
    cols_need = [
        "ts_code", "ann_date", "end_date",
        "total_liab", "money_cap", "total_assets",
        "total_hldr_eqy_exc_min_int", "total_cur_assets",
    ]
    parts = []
    for sym in symbols:
        fp = CACHE_DIR / f"balancesheet_{sym.replace('.', '_')}.parquet"
        if not fp.exists():
            continue
        try:
            p = pd.read_parquet(fp)
            keep = [c for c in cols_need if c in p.columns]
            p = p[keep].copy()
            p["symbol"] = sym
            parts.append(p)
        except Exception:
            continue
    if not parts:
        return pd.DataFrame()
    bs = pd.concat(parts, ignore_index=True)
    for c in cols_need[3:]:
        if c in bs.columns:
            bs[c] = pd.to_numeric(bs[c], errors="coerce")
    bs["ann_date"] = pd.to_datetime(bs["ann_date"], format="%Y%m%d", errors="coerce")
    bs = bs.dropna(subset=["ann_date"])
    bs = bs.sort_values(["symbol", "ann_date", "end_date"])
    bs = bs.drop_duplicates(subset=["symbol", "ann_date"], keep="last")
    logger.info("balancesheet: %d 标的, %d 条", bs["symbol"].nunique(), len(bs))
    return bs


def load_income_bulk(symbols: list[str]) -> pd.DataFrame:
    """批量加载利润表 PIT 数据."""
    cols_need = [
        "ts_code", "ann_date", "end_date",
        "operate_profit", "fin_exp", "total_profit",
        "n_income", "revenue",
    ]
    parts = []
    for sym in symbols:
        fp = CACHE_DIR / f"income_{sym.replace('.', '_')}.parquet"
        if not fp.exists():
            continue
        try:
            p = pd.read_parquet(fp)
            keep = [c for c in cols_need if c in p.columns]
            p = p[keep].copy()
            p["symbol"] = sym
            parts.append(p)
        except Exception:
            continue
    if not parts:
        return pd.DataFrame()
    inc = pd.concat(parts, ignore_index=True)
    for c in cols_need[3:]:
        if c in inc.columns:
            inc[c] = pd.to_numeric(inc[c], errors="coerce")
    inc["ann_date"] = pd.to_datetime(inc["ann_date"], format="%Y%m%d", errors="coerce")
    inc = inc.dropna(subset=["ann_date"])
    inc = inc.sort_values(["symbol", "ann_date", "end_date"])
    inc = inc.drop_duplicates(subset=["symbol", "ann_date"], keep="last")
    logger.info("income: %d 标的, %d 条", inc["symbol"].nunique(), len(inc))
    return inc


def load_analyst_data() -> pd.DataFrame:
    """加载分析师 EPS 一致预期数据 (来自 report_rc API)."""
    parts = []
    for fp in sorted(ALT_CACHE.glob("analyst_*.parquet")):
        try:
            p = pd.read_parquet(fp)
            parts.append(p)
        except Exception:
            continue
    if not parts:
        logger.warning("无分析师数据")
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["report_date"] = pd.to_datetime(df["report_date"], format="%Y%m%d", errors="coerce")
    df["eps"] = pd.to_numeric(df["eps"], errors="coerce")
    df = df.dropna(subset=["report_date", "eps", "ts_code"])
    df = df.rename(columns={"ts_code": "symbol"})
    logger.info("分析师: %d 条, %d 只, %s~%s",
                len(df), df["symbol"].nunique(),
                df["report_date"].min().date(), df["report_date"].max().date())
    return df


# ── 因子计算 ──────────────────────────────────────────────
def _pit_merge(daily: pd.DataFrame, stmt: pd.DataFrame,
               value_cols: list[str], prefix: str = "") -> None:
    """PIT 合并报表数据到日线 (原地修改). ann_date <= datetime 取最新."""
    if stmt.empty:
        return
    keep = ["symbol", "ann_date"] + [c for c in value_cols if c in stmt.columns]
    sub = stmt[keep].copy()
    for c in value_cols:
        if c in sub.columns:
            sub[c] = sub[c].astype(np.float32)
    merged_parts = []
    for sym, grp in daily.groupby("symbol"):
        grp_s = grp.sort_values("datetime")
        stmt_s = sub[sub["symbol"] == sym].sort_values("ann_date")
        if stmt_s.empty:
            merged_parts.append(grp_s)
            continue
        m = pd.merge_asof(
            grp_s, stmt_s.drop(columns=["symbol"]),
            left_on="datetime", right_on="ann_date", direction="backward",
        )
        merged_parts.append(m)
    merged = pd.concat(merged_parts, ignore_index=True)
    for c in value_cols:
        if c in merged.columns:
            col_name = f"{prefix}{c}" if prefix else c
            daily[col_name] = merged[c].values
            daily[col_name] = daily.groupby("symbol")[col_name].ffill(limit=60)


def compute_ev_ebitda(daily: pd.DataFrame) -> pd.Series:
    """EV/EBITDA 因子. EV = total_mv*1e4 + total_liab - money_cap.
    EBITDA ≈ operate_profit + fin_exp (近似,无折旧摊销分项).
    返回 -EV/EBITDA (负值=便宜, 与收益正相关).
    """
    # total_mv 单位万元, total_liab/money_cap 单位元(千元?)
    ev = daily["total_mv"].fillna(0) * 1e4 + \
         daily.get("bs_total_liab", pd.Series(0, index=daily.index)).fillna(0) - \
         daily.get("bs_money_cap", pd.Series(0, index=daily.index)).fillna(0)
    ebitda = daily.get("inc_operate_profit", pd.Series(np.nan, index=daily.index)).fillna(0) + \
             daily.get("inc_fin_exp", pd.Series(0, index=daily.index)).fillna(0)
    # 避免除零
    ebitda = ebitda.replace(0, np.nan)
    ratio = ev / ebitda
    # 过滤极端值
    ratio = ratio.clip(-200, 200)
    # 负 EV/EBITDA = 便宜 → 高分
    return -ratio


def compute_piotroski_fscore(daily: pd.DataFrame) -> pd.Series:
    """Piotroski F-Score (0-9), 向量化版本.
    需要 fina_indicator 的 PIT 列已合并到 daily.
    F1: ROA > 0, F2: CFO > 0, F3: ΔROA > 0, F4: CFO > EPS (应计)
    F5: 负债率下降, F6: 流动比率提升, F7: 无增发(近似: 用 NaN→0)
    F8: 毛利率提升, F9: 资产周转率提升(用revenue/total_assets近似)
    """
    score = pd.Series(0.0, index=daily.index)
    counted = pd.Series(0, index=daily.index)
    def _add(cond: pd.Series, valid: pd.Series) -> None:
        nonlocal score, counted
        v = valid.fillna(False)
        score = score + cond.fillna(False).astype(float).where(v, 0)
        counted = counted + v.astype(int)
    roa = daily.get("roe")  # 使用 roa 如果有, 否则 roe
    if "roa" in daily.columns:
        roa = daily["roa"]
    if roa is not None:
        _add(roa > 0, roa.notna())
    cfps = daily.get("cfps")
    if cfps is not None:
        _add(cfps > 0, cfps.notna())
    eps = daily.get("eps")
    if cfps is not None and eps is not None:
        _add(cfps > eps, cfps.notna() & eps.notna())
    # Δ因子: 用 symbol 分组 shift(60) 近似上一季度
    for col, higher_better in [
        ("roa", True), ("grossprofit_margin", True),
        ("current_ratio", True), ("debt_to_assets", False),
    ]:
        if col not in daily.columns:
            continue
        prev = daily.groupby("symbol")[col].shift(60)
        curr = daily[col]
        valid = curr.notna() & prev.notna()
        if higher_better:
            _add(curr > prev, valid)
        else:
            _add(curr < prev, valid)
    # 资产周转率近似: revenue / total_assets
    if "inc_revenue" in daily.columns and "bs_total_assets" in daily.columns:
        at = daily["inc_revenue"] / daily["bs_total_assets"].replace(0, np.nan)
        at_prev = daily.groupby("symbol")["inc_revenue"].shift(60) / \
                  daily.groupby("symbol")["bs_total_assets"].shift(60).replace(0, np.nan)
        valid = at.notna() & at_prev.notna()
        _add(at > at_prev, valid)
    # 维度不足(<5) 置为 NaN
    score = score.where(counted >= 5, np.nan)
    return score


def compute_analyst_revision(daily: pd.DataFrame,
                             analyst_df: pd.DataFrame) -> pd.Series:
    """分析师 EPS 一致预期修正率.
    对每只股票每月: 当月平均EPS预测 vs 上月平均EPS预测 的变化率.
    """
    if analyst_df.empty:
        return pd.Series(np.nan, index=daily.index)
    # 选取当年预测 (quarter 含当年Q4)
    adf = analyst_df.copy()
    adf["year_month"] = adf["report_date"].dt.to_period("M")
    # 聚合: 每月每只股票的平均 EPS 预测
    monthly_eps = adf.groupby(["symbol", "year_month"])["eps"].mean().reset_index()
    monthly_eps = monthly_eps.sort_values(["symbol", "year_month"])
    # 修正率: (当月EPS - 上月EPS) / |上月EPS|
    monthly_eps["eps_prev"] = monthly_eps.groupby("symbol")["eps"].shift(1)
    monthly_eps["revision"] = (
        (monthly_eps["eps"] - monthly_eps["eps_prev"])
        / monthly_eps["eps_prev"].abs().clip(0.01)
    )
    monthly_eps["revision"] = monthly_eps["revision"].clip(-5, 5)
    # 合并到日线 — 先转为月度 key
    daily["_ym"] = daily["datetime"].dt.to_period("M")
    # 转换 Period 为字符串做合并
    monthly_eps["_ym_str"] = monthly_eps["year_month"].astype(str)
    daily["_ym_str"] = daily["_ym"].astype(str)
    rev_map = monthly_eps.set_index(["symbol", "_ym_str"])["revision"]
    daily["_key"] = list(zip(daily["symbol"], daily["_ym_str"]))
    result = daily["_key"].map(rev_map)
    # 清理临时列
    daily.drop(columns=["_ym", "_ym_str", "_key"], inplace=True, errors="ignore")
    return result


# ── IC 计算 ──────────────────────────────────────────────
def compute_stratified_ic(
    df: pd.DataFrame,
    factor_col: str,
    fwd_col: str = "fwd_return",
    mv_col: str = "circ_mv_yi",
) -> pd.DataFrame:
    """分层 IC: 各市值段、各月的 Rank IC."""
    df["_month"] = df["datetime"].dt.to_period("M")
    records = []
    for bin_name, (lo, hi) in MV_BINS.items():
        mask = (df[mv_col] >= lo) & (df[mv_col] < hi) if bin_name != "ALL" else pd.Series(True, index=df.index)
        sub = df.loc[mask & df[factor_col].notna() & df[fwd_col].notna()]
        for period, grp in sub.groupby("_month"):
            if len(grp) < 30:
                continue
            ic, _ = stats.spearmanr(grp[factor_col], grp[fwd_col])
            records.append({
                "factor": factor_col, "mv_bin": bin_name,
                "period": str(period), "ic": ic, "n": len(grp),
            })
    df.drop(columns=["_month"], inplace=True, errors="ignore")
    return pd.DataFrame(records)


def print_ic_summary(results: dict[str, pd.DataFrame]) -> None:
    """打印分层 IC 摘要表."""
    print("\n" + "=" * 80)
    print("P4 因子分层 IC 摘要 (月频 Rank IC)")
    print("=" * 80)
    header = f"{'因子':<25} {'市值段':<10} {'IC均值':>8} {'IC_IR':>8} {'t值':>8} {'月数':>6} {'>0%':>6}"
    print(header)
    print("-" * 80)
    for factor_name, ic_df in results.items():
        if ic_df.empty:
            print(f"{factor_name:<25} {'N/A':<10} {'--':>8}")
            continue
        for mv_bin in MV_BINS:
            sub = ic_df[ic_df["mv_bin"] == mv_bin]
            if sub.empty:
                continue
            ic_mean = sub["ic"].mean()
            ic_std = sub["ic"].std()
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0
            t_val = ic_mean / (ic_std / np.sqrt(len(sub))) if ic_std > 0 else 0
            pct_pos = (sub["ic"] > 0).mean() * 100
            mark = " ✓" if mv_bin == "50-130B" and abs(ic_mean) > 0.025 else ""
            print(f"{factor_name:<25} {mv_bin:<10} {ic_mean:>8.4f} {ic_ir:>8.2f} "
                  f"{t_val:>8.2f} {len(sub):>6} {pct_pos:>5.1f}%{mark}")


# ── 主入口 ───────────────────────────────────────────────
def main() -> None:
    logger.info("=" * 60)
    logger.info("P4 因子分层 IC 验证 — Phase 0 门禁")
    logger.info("=" * 60)

    # 1. 加载日线数据
    daily = load_daily_data()
    symbols = daily["symbol"].unique().tolist()

    # 2. 加载报表数据
    fina = load_fina_indicator_bulk(symbols)
    bs = load_balancesheet_bulk(symbols)
    inc = load_income_bulk(symbols)
    analyst_df = load_analyst_data()

    # 3. PIT 合并
    logger.info("PIT 合并 fina_indicator...")
    fina_cols = [c for c in fina.columns if c not in
                 ("ts_code", "ann_date", "end_date", "symbol")]
    _pit_merge(daily, fina, fina_cols)

    logger.info("PIT 合并 balancesheet...")
    bs_cols = [c for c in bs.columns if c not in
               ("ts_code", "ann_date", "end_date", "symbol")]
    _pit_merge(daily, bs, bs_cols, prefix="bs_")

    logger.info("PIT 合并 income...")
    inc_cols = [c for c in inc.columns if c not in
                ("ts_code", "ann_date", "end_date", "symbol")]
    _pit_merge(daily, inc, inc_cols, prefix="inc_")

    # 4. 过滤有效数据 (2017+, 有 circ_mv)
    daily = daily[daily["datetime"] >= "2017-01-01"].copy()
    daily = daily[daily["circ_mv_yi"].notna() & (daily["circ_mv_yi"] > 0)]
    logger.info("有效数据: %d 行", len(daily))

    # 5. 计算因子
    logger.info("计算 EV/EBITDA...")
    daily["ev_ebitda"] = compute_ev_ebitda(daily)
    n1 = daily["ev_ebitda"].notna().sum()
    logger.info("  EV/EBITDA 有效: %d/%d (%.1f%%)", n1, len(daily), 100*n1/len(daily))

    logger.info("计算 Piotroski F-Score...")
    daily["fscore"] = compute_piotroski_fscore(daily)
    n2 = daily["fscore"].notna().sum()
    logger.info("  F-Score 有效: %d/%d (%.1f%%)", n2, len(daily), 100*n2/len(daily))

    logger.info("计算 Analyst Revision...")
    daily["analyst_rev"] = compute_analyst_revision(daily, analyst_df)
    n3 = daily["analyst_rev"].notna().sum()
    logger.info("  Analyst Rev 有效: %d/%d (%.1f%%)", n3, len(daily), 100*n3/len(daily))

    # 6. 分层 IC
    logger.info("计算分层 IC...")
    results = {}
    for col in ["ev_ebitda", "fscore", "analyst_rev"]:
        results[col] = compute_stratified_ic(daily, col)

    # 7. 输出结果
    print_ic_summary(results)

    # 8. Go/No-Go 判定
    print("\n" + "=" * 80)
    print("Go/No-Go 判定 (阈值: 50-130B IC > 2.5%, ≥2/3 通过)")
    print("=" * 80)
    passed = 0
    for col, ic_df in results.items():
        tier_c = ic_df[ic_df["mv_bin"] == "50-130B"]
        if tier_c.empty:
            print(f"  {col}: NO DATA")
            continue
        ic_mean = tier_c["ic"].mean()
        verdict = "GO ✓" if abs(ic_mean) > 0.025 else "NO-GO ✗"
        if abs(ic_mean) > 0.025:
            passed += 1
        print(f"  {col}: IC={ic_mean:.4f} → {verdict}")
    final = "GO" if passed >= 2 else "NO-GO"
    print(f"\n  总判定: {passed}/3 通过 → **{final}**")


if __name__ == "__main__":
    main()
