"""替代数据因子计算模块 — 筹码/资金流/分析师/融资融券因子。

基于 TuShareAlternativeProvider 的原始数据，计算以下因子：

**筹码分布因子 (5个)**:
- winner_rate: 获利比例
- cost_concentration: 成本集中度 (cost_95 - cost_5) / cost_50
- cost_skew: 成本偏度 (cost_85 + cost_15 - 2*cost_50)
- price_vs_cost: 价格 vs 中位成本偏离
- trapped_ratio: 套牢比率 (1 - winner_rate)

**资金流因子 (5个)**:
- smart_money_flow: 大单净流入 / 总成交额
- retail_flow: 小单净流入 / 总成交额
- flow_divergence: 大单方向 vs 小单方向分歧
- flow_persistence: 资金流方向持续性 (5日)
- volume_flow_ratio: 主动买入占比

**分析师因子 (4个)**:
- eps_revision: EPS 预测修正动量
- target_premium: 目标价溢价率
- rating_momentum: 评级动量
- consensus_dispersion: 一致预期分散度

**融资融券因子 (4个)**:
- margin_balance_ratio: 融资余额占比
- margin_buy_intensity: 融资买入力度
- margin_short_pressure: 融券做空压力
- margin_net_momentum: 融资净买入动量
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_chips_factors(chips_df: pd.DataFrame) -> pd.DataFrame:
    """从 cyq_perf 数据计算筹码分布因子。

    输入字段: ts_code, trade_date, cost_5pct, cost_15pct, cost_50pct,
              cost_85pct, cost_95pct, winner_rate
    输出: ts_code, trade_date, + 5 个因子列
    """
    if chips_df.empty:
        logger.warning("筹码数据为空，跳过筹码因子")
        return pd.DataFrame()

    df = chips_df.copy()
    # 标准化列名
    if "ts_code" in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        df = df.rename(columns={"trade_date": "datetime"})

    eps = 1e-8
    # 1. winner_rate: 直接使用
    if "winner_rate" in df.columns:
        df["chips_winner_rate"] = df["winner_rate"].fillna(0.5)
    else:
        df["chips_winner_rate"] = 0.5

    # 2. cost_concentration: 成本分散度
    c5 = df.get("cost_5pct", pd.Series(0, index=df.index))
    c50 = df.get("cost_50pct", pd.Series(1, index=df.index))
    c95 = df.get("cost_95pct", pd.Series(1, index=df.index))
    df["chips_cost_concentration"] = (c95 - c5) / (c50 + eps)

    # 3. cost_skew: 成本偏度
    c15 = df.get("cost_15pct", pd.Series(0, index=df.index))
    c85 = df.get("cost_85pct", pd.Series(1, index=df.index))
    df["chips_cost_skew"] = (c85 + c15 - 2 * c50) / (c95 - c5 + eps)

    # 4. trapped_ratio: 套牢比率
    df["chips_trapped_ratio"] = 1.0 - df["chips_winner_rate"]

    # 5. price_vs_cost: 由 merge 时计算（需要 close 价格）
    df["chips_price_vs_cost"] = np.nan  # placeholder

    keep = ["symbol", "datetime", "cost_50pct", "chips_winner_rate",
            "chips_cost_concentration", "chips_cost_skew",
            "chips_trapped_ratio", "chips_price_vs_cost"]
    keep = [c for c in keep if c in df.columns]
    logger.info("筹码因子: %d 行, %d 因子", len(df), len(keep) - 3)  # -3: symbol/datetime/cost_50pct
    return df[keep]


def compute_flow_factors(flow_df: pd.DataFrame) -> pd.DataFrame:
    """从 moneyflow 数据计算资金流因子。

    输入字段: ts_code, trade_date, buy_sm_vol, sell_sm_vol,
              buy_md_vol, sell_md_vol, buy_lg_vol, sell_lg_vol,
              buy_elg_vol, sell_elg_vol, net_mf_vol
    输出: symbol, datetime, + 5 个因子列
    """
    if flow_df.empty:
        logger.warning("资金流数据为空，跳过资金流因子")
        return pd.DataFrame()

    df = flow_df.copy()
    if "ts_code" in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        df = df.rename(columns={"trade_date": "datetime"})

    eps = 1e-8
    # 总成交量
    buy_total = (
        df.get("buy_sm_vol", 0) + df.get("buy_md_vol", 0)
        + df.get("buy_lg_vol", 0) + df.get("buy_elg_vol", 0)
    )
    sell_total = (
        df.get("sell_sm_vol", 0) + df.get("sell_md_vol", 0)
        + df.get("sell_lg_vol", 0) + df.get("sell_elg_vol", 0)
    )
    total_vol = buy_total + sell_total + eps

    # 1. smart_money_flow: 大单+特大单净流入占比
    lg_net = (
        df.get("buy_lg_vol", 0) - df.get("sell_lg_vol", 0)
        + df.get("buy_elg_vol", 0) - df.get("sell_elg_vol", 0)
    )
    df["flow_smart_money"] = lg_net / total_vol

    # 2. retail_flow: 小单净流入占比
    sm_net = df.get("buy_sm_vol", 0) - df.get("sell_sm_vol", 0)
    df["flow_retail"] = sm_net / total_vol

    # 3. flow_divergence: 大单 vs 小单方向分歧
    df["flow_divergence"] = np.sign(lg_net) - np.sign(sm_net)

    # 4. flow_persistence: 5 日方向持续性
    df = df.sort_values(["symbol", "datetime"])
    sign_col = np.sign(lg_net)
    df["_lg_sign"] = sign_col
    df["flow_persistence"] = (
        df.groupby("symbol")["_lg_sign"]
        .rolling(5, min_periods=1).mean()
        .reset_index(level=0, drop=True)
    )

    # 5. volume_flow_ratio: 主动买入占总成交
    df["flow_buy_ratio"] = buy_total / total_vol

    keep = ["symbol", "datetime", "flow_smart_money", "flow_retail",
            "flow_divergence", "flow_persistence", "flow_buy_ratio"]
    keep = [c for c in keep if c in df.columns]
    df = df.drop(columns=["_lg_sign"], errors="ignore")
    logger.info("资金流因子: %d 行, %d 因子", len(df), len(keep) - 2)
    return df[keep]


def compute_analyst_factors(
    analyst_df: pd.DataFrame, prices_df: pd.DataFrame,
) -> pd.DataFrame:
    """从 report_rc 数据计算分析师因子。

    先按 (ts_code, report_date) 聚合当日所有分析师预测的共识，
    然后计算 4 个因子。需要 prices_df 提供 close 价格。
    """
    if analyst_df.empty:
        logger.warning("分析师数据为空，跳过分析师因子")
        return pd.DataFrame()

    df = analyst_df.copy()
    if "ts_code" in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    # report_date → datetime
    date_col = "report_date" if "report_date" in df.columns else "datetime"
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col].astype(str))
        if date_col != "datetime":
            df = df.rename(columns={date_col: "datetime"})

    # 聚合每日每股共识
    agg_cols = {}
    if "eps_avg" in df.columns:
        agg_cols["eps_avg"] = "mean"
    if "pe_avg" in df.columns:
        agg_cols["pe_avg"] = "mean"
    if "target_price_avg" in df.columns:
        agg_cols["target_price_avg"] = "mean"
    if "rating_buy" in df.columns:
        agg_cols["rating_buy"] = "sum"
    if "rating_avg" in df.columns:
        agg_cols["rating_avg"] = "mean"

    if not agg_cols:
        logger.warning("分析师数据缺少关键列")
        return pd.DataFrame()

    consensus = df.groupby(["symbol", "datetime"]).agg(agg_cols)
    consensus["analyst_count"] = df.groupby(
        ["symbol", "datetime"]
    ).size()
    consensus = consensus.reset_index()

    # 1. eps_revision: EPS 变化动量 (当前 vs 20 日前)
    consensus = consensus.sort_values(["symbol", "datetime"])
    if "eps_avg" in consensus.columns:
        prev_eps = consensus.groupby("symbol")["eps_avg"].shift(20)
        eps = consensus["eps_avg"]
        consensus["analyst_eps_revision"] = (
            (eps - prev_eps) / (prev_eps.abs() + 1e-8)
        ).clip(-5, 5)
    else:
        consensus["analyst_eps_revision"] = np.nan

    # 2. target_premium: 目标价 vs 当前价溢价
    consensus["analyst_target_premium"] = np.nan  # 需 merge close

    # 3. rating_momentum: 买入评级占比
    if "rating_buy" in consensus.columns and "analyst_count" in consensus.columns:
        consensus["analyst_rating_momentum"] = (
            consensus["rating_buy"] / (consensus["analyst_count"] + 1e-8)
        )
    else:
        consensus["analyst_rating_momentum"] = np.nan

    # 4. consensus_dispersion: 分析师数量 (覆盖度代理)
    consensus["analyst_coverage"] = np.log1p(
        consensus.get("analyst_count", pd.Series(0, index=consensus.index))
    )

    keep = ["symbol", "datetime", "analyst_eps_revision",
            "analyst_target_premium", "analyst_rating_momentum",
            "analyst_coverage"]
    keep = [c for c in keep if c in consensus.columns]
    logger.info("分析师因子: %d 行, %d 因子", len(consensus), len(keep) - 2)
    return consensus[keep]


def compute_margin_factors(margin_df: pd.DataFrame) -> pd.DataFrame:
    """从 margin_detail 数据计算融资融券因子。

    输入字段: ts_code, trade_date, rzye (融资余额),
              rzmre (融资买入额), rzche (融资偿还额),
              rqye (融券余额), rqmcl (融券卖出量),
              rqchl (融券偿还量), rzrqye (融资融券余额)

    输出因子 (4个):
    - margin_balance_ratio: 融资余额 / 融资融券余额
    - margin_buy_intensity: 融资买入额 / (融资余额 + eps)
    - margin_short_pressure: 融券余额 / 融资融券余额
    - margin_net_momentum: (融资买入-融资偿还) 5日均值动量
    """
    if margin_df.empty:
        logger.warning("融资融券数据为空，跳过融资融券因子")
        return pd.DataFrame()

    df = margin_df.copy()
    if "ts_code" in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        df = df.rename(columns={"trade_date": "datetime"})

    eps = 1e-8
    rzye = df.get("rzye", pd.Series(0, index=df.index)).fillna(0)
    rzmre = df.get("rzmre", pd.Series(0, index=df.index)).fillna(0)
    rzche = df.get("rzche", pd.Series(0, index=df.index)).fillna(0)
    rqye = df.get("rqye", pd.Series(0, index=df.index)).fillna(0)
    rzrqye = df.get("rzrqye", pd.Series(0, index=df.index)).fillna(0)

    # 1. margin_balance_ratio: 融资余额占融资融券总余额比
    df["margin_balance_ratio"] = rzye / (rzrqye + eps)

    # 2. margin_buy_intensity: 融资买入力度
    df["margin_buy_intensity"] = rzmre / (rzye + eps)

    # 3. margin_short_pressure: 融券做空压力
    df["margin_short_pressure"] = rqye / (rzrqye + eps)

    # 4. margin_net_momentum: 融资净买入变化动量(5日均)
    df = df.sort_values(["symbol", "datetime"])
    net_buy = rzmre - rzche
    df["_net_buy"] = net_buy.values
    df["margin_net_momentum"] = (
        df.groupby("symbol")["_net_buy"]
        .rolling(5, min_periods=1).mean()
        .reset_index(level=0, drop=True)
    )
    # 标准化: 除以融资余额
    df["margin_net_momentum"] = (
        df["margin_net_momentum"] / (rzye + eps)
    )

    keep = [
        "symbol", "datetime",
        "margin_balance_ratio", "margin_buy_intensity",
        "margin_short_pressure", "margin_net_momentum",
    ]
    keep = [c for c in keep if c in df.columns]
    df = df.drop(columns=["_net_buy"], errors="ignore")
    logger.info(
        "融资融券因子: %d 行, %d 因子", len(df), len(keep) - 2,
    )
    return df[keep]


def merge_alternative_factors(
    prices_df: pd.DataFrame,
    chips_factors: Optional[pd.DataFrame] = None,
    flow_factors: Optional[pd.DataFrame] = None,
    analyst_factors: Optional[pd.DataFrame] = None,
    margin_factors: Optional[pd.DataFrame] = None,
    northbound_factors: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """合并所有替代因子到主数据表。

    将 chips/flow/analyst/margin/northbound 因子 left join 到 prices_df。
    同时填充需要 close 价格的因子 (price_vs_cost, target_premium)。
    """
    result = prices_df.copy()
    merged_count = 0

    if chips_factors is not None and not chips_factors.empty:
        result = result.merge(
            chips_factors, on=["symbol", "datetime"], how="left",
        )
        # 填充 price_vs_cost: (close - cost_50) / cost_50
        if "chips_price_vs_cost" in result.columns and "close" in result.columns:
            # 需要 cost_50pct，尝试从 chips 获取
            if "cost_50pct" in chips_factors.columns:
                c50 = result.merge(
                    chips_factors[["symbol", "datetime", "cost_50pct"]],
                    on=["symbol", "datetime"], how="left",
                ).get("cost_50pct", pd.Series(np.nan))
                result["chips_price_vs_cost"] = (
                    (result["close"] - c50) / (c50 + 1e-8)
                )
        merged_count += 1
        logger.info("合并筹码因子: %d 列", len(chips_factors.columns) - 2)

    if flow_factors is not None and not flow_factors.empty:
        result = result.merge(
            flow_factors, on=["symbol", "datetime"], how="left",
        )
        merged_count += 1
        logger.info("合并资金流因子: %d 列", len(flow_factors.columns) - 2)

    if analyst_factors is not None and not analyst_factors.empty:
        result = result.merge(
            analyst_factors, on=["symbol", "datetime"], how="left",
        )
        # 填充 target_premium: (target_price - close) / close
        if ("analyst_target_premium" in result.columns
                and "target_price_avg" in analyst_factors.columns):
            tp = result.merge(
                analyst_factors[["symbol", "datetime", "target_price_avg"]],
                on=["symbol", "datetime"], how="left",
            ).get("target_price_avg")
            if tp is not None:
                result["analyst_target_premium"] = (
                    (tp - result["close"]) / (result["close"] + 1e-8)
                ).clip(-2, 5)
        merged_count += 1
        logger.info("合并分析师因子: %d 列", len(analyst_factors.columns) - 2)

    if margin_factors is not None and not margin_factors.empty:
        result = result.merge(
            margin_factors, on=["symbol", "datetime"], how="left",
        )
        merged_count += 1
        logger.info(
            "合并融资融券因子: %d 列",
            len(margin_factors.columns) - 2,
        )

    if northbound_factors is not None and not northbound_factors.empty:
        result = result.merge(
            northbound_factors, on=["symbol", "datetime"], how="left",
        )
        merged_count += 1
        logger.info(
            "合并北向因子: %d 列",
            len(northbound_factors.columns) - 2,
        )

    logger.info("替代因子合并完成: %d 数据源, 总行数 %d", merged_count, len(result))
    return result
