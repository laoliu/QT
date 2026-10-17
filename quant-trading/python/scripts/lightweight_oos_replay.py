"""轻量级 OOS Replay — 绕过重型回测引擎。

从 oos_predictions.parquet 加载预测分数，
从月度缓存 parquet 加载价格数据，
模拟等权 top-K 组合的收益。

用法:
    python scripts/lightweight_oos_replay.py
    python scripts/lightweight_oos_replay.py --index csi500 --top_k 30
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("oos_replay")


# ---- 配置 ----

INDEX_CONFIGS = {
    "csi2000": {
        "predictions": "models/ml_csi2000_lgb_fund/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 5,
    },
    "csi500": {
        "predictions": "models/ml_csi500_lightgbm/oos_predictions.parquet",
        "top_k": 30,
        "rebalance_days": 5,
    },
    "csi1000": {
        "predictions": "models/ml_csi1000_lightgbm/oos_predictions.parquet",
        "top_k": 50,
        "rebalance_days": 5,
    },
}

# 费用参数 — §13.1: 对齐 MarketRulesCNEquity
COMMISSION_RATE = 8.5e-05  # 万0.85（A股默认值）
COMMISSION_MIN = 5.0
STAMP_TAX_SELL = 0.0005
TRANSFER_FEE = 0.00001
SLIPPAGE_BPS = 10.0  # §13.1 MarketRulesCNEquity.slippage_bps_default


def load_predictions(path: Path) -> pd.DataFrame:
    """加载 OOS 预测。返回 DataFrame(date, symbol, score)。"""
    logger.info("加载预测: %s", path)
    df = pd.read_parquet(path)
    # 标准化列名
    col_map = {}
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
        # 取第一个数值列
        num_cols = df.select_dtypes(include=[np.number]).columns
        score_col = [c for c in num_cols if c not in ("date",)][0]
        df = df.rename(columns={score_col: "score"})
    df["date"] = pd.to_datetime(df["date"].astype(str))
    logger.info("  预测行数=%d, 日期范围=%s~%s",
                len(df), df["date"].min().date(), df["date"].max().date())
    return df[["date", "symbol", "score"]].copy()


def load_equity_prices(cache_dir: Path) -> pd.DataFrame:
    """从月度缓存文件加载价格数据。返回 pivot 表 (date × symbol)。"""
    logger.info("加载价格数据: %s", cache_dir)
    parts = []
    for f in sorted(cache_dir.glob("daily_*.parquet")):
        df = pd.read_parquet(f)
        parts.append(df)
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
        elif cl == "close":
            col_map[c] = "close"
        elif cl == "open":
            col_map[c] = "open"
    all_df = all_df.rename(columns=col_map)
    all_df["date"] = pd.to_datetime(all_df["date"].astype(str))
    logger.info("  价格行数=%d, 股票数=%d, 日期=%s~%s",
                len(all_df), all_df["symbol"].nunique(),
                all_df["date"].min().date(), all_df["date"].max().date())
    return all_df


def run_replay(
    predictions: pd.DataFrame,
    prices: pd.DataFrame,
    top_k: int = 50,
    rebalance_days: int = 5,
    initial_capital: float = 1_000_000.0,
) -> dict:
    """执行轻量级回测。

    逻辑:
    1. 在每个调仓日, 取分数最高的 top_k 只股票
    2. 用次日开盘价买入 (Next-Bar-Open, 无前视)
    3. 等权持仓直到下一个调仓日
    4. 扣除双边交易成本
    """
    # 创建 close 和 open pivot 表
    close_pivot = prices.pivot_table(
        index="date", columns="symbol", values="close",
    )
    open_pivot = prices.pivot_table(
        index="date", columns="symbol", values="open",
    )
    trade_dates = sorted(close_pivot.index)
    pred_dates = sorted(predictions["date"].unique())

    # 筛选预测日在交易日范围内
    valid_pred_dates = [d for d in pred_dates if d in close_pivot.index]
    logger.info("有效预测日: %d / %d", len(valid_pred_dates), len(pred_dates))

    # 按调仓周期取信号日
    signal_dates = valid_pred_dates[::rebalance_days]
    logger.info("调仓信号日: %d 个 (每 %d 天)", len(signal_dates), rebalance_days)

    # 模拟
    nav = initial_capital
    nav_history = {}
    holdings: dict[str, float] = {}  # symbol -> shares
    prev_portfolio_value = initial_capital

    for i, sig_date in enumerate(signal_dates):
        # 获取信号日的 top-K
        day_preds = predictions[predictions["date"] == sig_date]
        day_preds = day_preds.sort_values("score", ascending=False)
        top_symbols = day_preds["symbol"].head(top_k).tolist()

        # 找到下一个交易日 (执行日)
        sig_idx = trade_dates.index(sig_date)
        if sig_idx + 1 >= len(trade_dates):
            break
        exec_date = trade_dates[sig_idx + 1]

        # 确定持有期终止日 (下一个调仓信号日的执行日)
        if i + 1 < len(signal_dates):
            next_sig = signal_dates[i + 1]
            next_sig_idx = trade_dates.index(next_sig)
            if next_sig_idx + 1 < len(trade_dates):
                end_date = trade_dates[next_sig_idx + 1]
            else:
                end_date = trade_dates[-1]
        else:
            end_date = trade_dates[-1]

        # 计算换手成本
        old_syms = set(holdings.keys())
        new_syms = set(top_symbols)
        n_sell = len(old_syms - new_syms)
        n_buy = len(new_syms - old_syms)
        turnover = (n_sell + n_buy) / max(len(old_syms | new_syms), 1)

        # 交易成本: 买入(佣金+滑点) + 卖出(佣金+印花税+滑点)
        sell_cost = turnover * 0.5 * (COMMISSION_RATE + STAMP_TAX_SELL
                                      + SLIPPAGE_BPS / 10000)
        buy_cost = turnover * 0.5 * (COMMISSION_RATE + SLIPPAGE_BPS / 10000)
        total_cost_rate = sell_cost + buy_cost

        # 在持有期内每天计算 NAV
        hold_dates = [d for d in trade_dates
                      if exec_date <= d < end_date]
        if not hold_dates:
            continue

        # 获取执行日开盘价作为买入价
        buy_prices = {}
        for sym in top_symbols:
            if sym in open_pivot.columns:
                p = open_pivot.loc[exec_date, sym]
                if pd.notna(p) and p > 0:
                    buy_prices[sym] = p
        valid_syms = list(buy_prices.keys())
        if not valid_syms:
            continue

        # 扣除交易成本
        nav *= (1 - total_cost_rate)

        # 等权分配
        per_stock = nav / len(valid_syms)

        # 每日跟踪 NAV (相对于买入价的收益)
        for hd in hold_dates:
            day_vals = []
            for sym in valid_syms:
                if sym in close_pivot.columns:
                    cp = close_pivot.loc[hd, sym]
                    bp = buy_prices[sym]
                    if pd.notna(cp) and bp > 0:
                        day_vals.append(per_stock * cp / bp)
                    else:
                        day_vals.append(per_stock)
                else:
                    day_vals.append(per_stock)
            nav_today = sum(day_vals)
            nav_history[hd] = nav_today

        # 更新 NAV 为持有期末值
        if hold_dates:
            nav = nav_history[hold_dates[-1]]

    nav_series = pd.Series(nav_history).sort_index()
    logger.info("回测完成: %d 天, 初始=%,.0f, 终值=%,.0f",
                len(nav_series), initial_capital, nav_series.iloc[-1])
    return {"nav_series": nav_series, "initial_capital": initial_capital}


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
    yearly = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[str(yr)] = round(float((1 + grp).prod() - 1), 4)

    return {
        "annualized_return": round(ar, 4),
        "volatility": round(vol, 4),
        "sharpe_ratio": round(sr, 2),
        "max_drawdown": round(mdd, 4),
        "calmar_ratio": round(calmar, 2),
        "n_years": round(n_years, 2),
        "total_return": round(cum - 1, 4),
        "yearly_returns": yearly,
    }


def main():
    """入口。"""
    parser = argparse.ArgumentParser(description="轻量级 OOS Replay")
    parser.add_argument("--index", default="csi2000",
                        choices=list(INDEX_CONFIGS.keys()))
    parser.add_argument("--top_k", type=int, default=None)
    parser.add_argument("--rebalance", type=int, default=None)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cfg = INDEX_CONFIGS[args.index]
    top_k = args.top_k or cfg["top_k"]
    rebalance_days = args.rebalance or cfg["rebalance_days"]

    t0 = time.time()

    # 加载数据
    pred_path = ROOT / cfg["predictions"]
    predictions = load_predictions(pred_path)

    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    prices = load_equity_prices(cache_dir)

    # 执行回测
    result = run_replay(
        predictions, prices,
        top_k=top_k, rebalance_days=rebalance_days,
        initial_capital=args.capital,
    )

    # 计算指标
    metrics = compute_metrics(result["nav_series"])
    elapsed = time.time() - t0

    # 输出
    output_name = args.output or f"lightweight_{args.index}_oos"
    out_dir = ROOT / "output" / output_name
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "index": args.index,
        "top_k": top_k,
        "rebalance_days": rebalance_days,
        "initial_capital": args.capital,
        "elapsed_seconds": round(elapsed, 1),
        **metrics,
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    nav_df = result["nav_series"].reset_index()
    nav_df.columns = ["date", "nav"]
    nav_df["date"] = nav_df["date"].dt.strftime("%Y-%m-%d")
    nav_df.to_csv(out_dir / "daily_nav.csv", index=False)

    logger.info("=" * 60)
    logger.info("轻量级 OOS Replay 结果 — %s", args.index.upper())
    logger.info("=" * 60)
    for k, v in sorted(summary.items()):
        if k != "yearly_returns":
            logger.info("  %-22s: %s", k, v)
    logger.info("  逐年:")
    for yr, ret in sorted(metrics["yearly_returns"].items()):
        logger.info("    %s: %+.1f%%", yr, ret * 100)
    logger.info("耗时: %.1f 秒", elapsed)
    logger.info("结果保存: %s", out_dir)


if __name__ == "__main__":
    main()
