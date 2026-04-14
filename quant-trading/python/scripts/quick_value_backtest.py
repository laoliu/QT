"""轻量级价值因子回测：直接从信号文件+行情缓存计算组合收益。

绕过全引擎数据加载，仅验证因子选股能力。
策略：每20个交易日调仓，选top_k=30，等权持有，次日开盘价成交。
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import numpy as np
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "tushare"

TOP_K = 30
REBAL_DAYS = 20
SLIPPAGE_BPS = 15.0
COMMISSION_RATE = 0.0000854 * 2  # 双边
STAMP_TAX = 0.0005  # 卖出
START = "2015-01-05"
END = "2025-12-31"


def load_signals() -> pd.DataFrame:
    """加载预计算信号。"""
    p = ROOT / "output" / "value_signals_csi2000.parquet"
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["datetime"]).dt.normalize()
    return df[["date", "symbol", "pred_score"]]


def load_daily_prices(symbols: set[str]) -> pd.DataFrame:
    """从缓存加载日线数据（open/close）。"""
    frames = []
    daily_dir = CACHE / "daily"
    for sym in symbols:
        # 尝试 qfq / hfq / 无复权
        for adj in ["qfq", "hfq", ""]:
            pattern = f"{sym}_*_D_{adj}.parquet" if adj else f"{sym}_*_D_.parquet"
            files = sorted(daily_dir.glob(pattern))
            if files:
                break
        if not files:
            continue
        parts = []
        for f in files:
            try:
                d = pd.read_parquet(f, columns=["trade_date", "open", "close"])
                parts.append(d)
            except Exception:
                continue
        if parts:
            df = pd.concat(parts, ignore_index=True)
            df["symbol"] = sym
            frames.append(df)
    if not frames:
        raise RuntimeError("No price data loaded")
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["trade_date"])
    out = out.drop(columns=["trade_date"])
    out = out.drop_duplicates(subset=["date", "symbol"])
    out = out.sort_values(["date", "symbol"]).reset_index(drop=True)
    return out


def run_backtest(signals: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """收益率加权回测：等权top_k，每REBAL_DAYS天调仓。
    
    次日开盘价成交，持有期使用 close-to-close 收益。
    调仓日扣除单边滑点+佣金+印花税。
    """
    # 准备 pivot 表: date x symbol
    close_pv = prices.pivot_table(index="date", columns="symbol", values="close")
    open_pv = prices.pivot_table(index="date", columns="symbol", values="open")
    
    # 日收益率 (close-to-close)
    daily_ret = close_pv.pct_change()
    
    all_dates = sorted(close_pv.index)
    all_dates = [d for d in all_dates if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
    
    nav = 1.0
    holdings: list[str] = []
    records = []
    last_rebal_idx = -999
    one_way_cost = SLIPPAGE_BPS / 10000 + COMMISSION_RATE
    
    for i, dt in enumerate(all_dates):
        # 当日组合收益
        if holdings:
            rets = daily_ret.loc[dt, holdings].dropna()
            if len(rets) > 0:
                port_ret = rets.mean()  # 等权
            else:
                port_ret = 0.0
        else:
            port_ret = 0.0
        
        nav *= (1 + port_ret)
        
        # 调仓检查
        need_rebal = (i - last_rebal_idx >= REBAL_DAYS) or (i == 0)
        if need_rebal:
            day_sig = signals[signals["date"] == dt]
            if len(day_sig) >= TOP_K:
                new_top = day_sig.nlargest(TOP_K, "pred_score")["symbol"].tolist()
                # 仅保留有价格数据的
                available = [s for s in new_top if s in close_pv.columns]
                if available:
                    # 计算换手成本
                    old_set = set(holdings)
                    new_set = set(available)
                    n_sell = len(old_set - new_set)
                    n_buy = len(new_set - old_set)
                    n_total = max(len(old_set), len(new_set), 1)
                    turnover = (n_sell + n_buy) / (2 * n_total)
                    # 扣除交易成本 (买入单边 + 卖出单边含印花税)
                    buy_cost = (n_buy / n_total) * one_way_cost
                    sell_cost = (n_sell / n_total) * (one_way_cost + STAMP_TAX)
                    nav *= (1 - buy_cost - sell_cost)
                    
                    holdings = available
                    last_rebal_idx = i

        records.append({"date": dt, "nav": nav, "positions": len(holdings)})
    
    return pd.DataFrame(records)


def print_report(nav_df: pd.DataFrame) -> None:
    """打印回测报告。"""
    nav_df = nav_df.copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_s = nav_df.set_index("date")["nav"]
    
    total_ret = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    n_years = (nav_s.index[-1] - nav_s.index[0]).days / 365.25
    ar = (1 + total_ret) ** (1 / n_years) - 1
    
    daily_ret = nav_s.pct_change().dropna()
    sr = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    
    # Max drawdown
    cum_max = nav_s.cummax()
    dd = (nav_s - cum_max) / cum_max
    mdd = dd.min()
    calmar = ar / abs(mdd) if abs(mdd) > 0 else 0
    
    print(f"\n{'='*50}")
    print(f"总收益: {total_ret:.1%}")
    print(f"年化收益: {ar:.1%}")
    print(f"最大回撤: {mdd:.1%}")
    print(f"夏普比率: {sr:.3f}")
    print(f"卡尔马比率: {calmar:.3f}")
    print(f"回测年数: {n_years:.1f}")
    print(f"{'='*50}")
    
    # 逐年收益
    nav_df["year"] = nav_df["date"].dt.year
    print("\n逐年收益:")
    for yr, grp in nav_df.groupby("year"):
        yr_ret = grp.iloc[-1]["nav"] / grp.iloc[0]["nav"] - 1
        print(f"  {yr}: {yr_ret:+6.1%}  (持仓={grp['positions'].mean():.0f})")
    
    # 保存结果
    out_dir = ROOT / "output" / "value_factor_quick_bt"
    out_dir.mkdir(parents=True, exist_ok=True)
    nav_df.to_csv(out_dir / "daily_nav.csv", index=False)
    print(f"\n结果已保存: {out_dir / 'daily_nav.csv'}")


def main():
    print("=== Quick Value Factor Backtest ===")
    signals = load_signals()
    print(f"Signals: {len(signals)} rows, {signals['date'].nunique()} dates")

    # 获取所有出现过的symbol
    all_symbols = set(signals["symbol"].unique())
    print(f"Loading prices for {len(all_symbols)} symbols...")
    prices = load_daily_prices(all_symbols)
    print(f"Prices: {len(prices)} rows")

    nav_df = run_backtest(signals, prices)
    print_report(nav_df)


if __name__ == "__main__":
    main()
