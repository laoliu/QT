#!/usr/bin/env python
"""R135: 多层级组合回测模拟。

基于分层实验结果，模拟多tier组合的历史表现。
使用各tier的daily_nav.csv作为输入，按权重合成组合NAV。
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def load_daily_returns(output_name: str) -> pd.Series | None:
    """从output目录加载日收益序列。"""
    csv_path = ROOT / "output" / output_name / "daily_nav.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        nav = df.iloc[:, 0]
        nav.index = pd.to_datetime(nav.index)
        return nav.pct_change().dropna()
    return None


def compute_metrics(returns: pd.Series, label: str = "") -> dict:
    """从日收益计算核心指标。"""
    if len(returns) < 20:
        return {}
    ann = 252
    ar = float((1 + returns).prod() ** (ann / len(returns)) - 1)
    vol = float(returns.std() * np.sqrt(ann))
    sr = ar / vol if vol > 1e-9 else 0.0
    cum = (1 + returns).cumprod()
    dd = cum / cum.cummax() - 1
    mdd = float(-dd.min())
    calmar = ar / mdd if mdd > 1e-9 else 0.0
    # recovery
    dd_end = dd.idxmin()
    recovery = cum[cum.index > dd_end]
    rec_days = 0
    if len(recovery) > 0:
        rec_mask = recovery >= cum[:dd_end].max()
        if rec_mask.any():
            rec_days = (rec_mask.idxmax() - dd_end).days
    # yearly
    yearly = {}
    for yr in sorted(returns.index.year.unique()):
        yr_ret = returns[returns.index.year == yr]
        yearly[str(yr)] = round(float((1 + yr_ret).prod() - 1), 4)
    return {
        "label": label, "AR": round(ar, 4), "SR": round(sr, 2),
        "MDD": round(mdd, 4), "Calmar": round(calmar, 2),
        "vol": round(vol, 4), "n_days": len(returns),
        "recovery_days": rec_days, "yearly": yearly,
    }


def simulate_portfolio(components: dict[str, tuple[str, float]],
                       label: str = "Portfolio") -> pd.Series | None:
    """合成多tier组合。components = {name: (output_dir, weight)}"""
    all_ret = {}
    for name, (output_dir, weight) in components.items():
        ret = load_daily_returns(output_dir)
        if ret is None:
            print(f"  ❌ {name}: 数据未找到")
            return None
        all_ret[name] = ret * weight

    # align dates (intersection)
    df = pd.DataFrame(all_ret).dropna(how="any")
    portfolio_ret = df.sum(axis=1)
    return portfolio_ret


def print_metrics(m: dict) -> None:
    """打印指标摘要。"""
    print(f"  {m['label']}:")
    print(f"    全量期: AR={m['AR']:.1%}, SR={m['SR']:.2f}, MDD={m['MDD']:.1%}, "
          f"Calmar={m['Calmar']:.2f}, Vol={m['vol']:.1%}")
    # segments
    yr = m.get("yearly", {})
    if yr:
        print(f"    逐年:")
        for y, r in sorted(yr.items()):
            flag = "🔴" if r < -0.05 else ("🟡" if r < 0 else "")
            print(f"      {y}: {r:+.1%} {flag}")


def segment_metrics(returns: pd.Series, label: str) -> None:
    """分段指标输出。"""
    m_full = compute_metrics(returns, f"{label} 全量期")
    print_metrics(m_full)

    post_reg = returns[returns.index >= "2022-08-01"]
    if len(post_reg) > 20:
        m_post = compute_metrics(post_reg, f"{label} 新规后(2022.08+)")
        print_metrics(m_post)

    recent = returns[returns.index >= "2024-01-01"]
    if len(recent) > 20:
        m_rec = compute_metrics(recent, f"{label} 近期(2024+)")
        print_metrics(m_rec)


# ── 组合方案定义 ──
PORTFOLIOS = {
    "P1_大盘核心+微盘卫星": {
        "CSI300≥200B": ("risk_controlled_csi300_p3_mv200", 0.45),
        "CSI300_100-500B": ("risk_controlled_csi300_p3_mv100_500", 0.15),
        "CSI2000<20B": ("tier_p3_mv0_20", 0.10),
        "CB(占位现金)": (None, 0.05),  # CB暂用0收益
        "ETF(占位国债)": (None, 0.25),  # ETF暂用0收益
    },
    "P2_三层均衡": {
        "CSI300≥200B": ("risk_controlled_csi300_p3_mv200", 0.30),
        "CSI500_30-100B": ("risk_controlled_csi500_p3_mv30_100", 0.30),
        "CSI2000<20B": ("tier_p3_mv0_20", 0.10),
        "CB(占位现金)": (None, 0.05),
        "ETF(占位国债)": (None, 0.25),
    },
    "P3_中盘主力": {
        "CSI500_30-100B": ("risk_controlled_csi500_p3_mv30_100", 0.40),
        "CSI300≥100B": ("risk_controlled_csi300_p3_mv100", 0.20),
        "CSI2000<20B": ("tier_p3_mv0_20", 0.10),
        "CB(占位现金)": (None, 0.05),
        "ETF(占位国债)": (None, 0.25),
    },
    "P4_大盘进取": {
        "CSI300≥100B": ("risk_controlled_csi300_p3_mv100", 0.50),
        "CSI300≥200B": ("risk_controlled_csi300_p3_mv200", 0.15),
        "CSI2000<20B": ("tier_p3_mv0_20", 0.05),
        "CB(占位现金)": (None, 0.05),
        "ETF(占位国债)": (None, 0.25),
    },
    "P5_仅活跃ML(无占位)": {
        "CSI300≥200B": ("risk_controlled_csi300_p3_mv200", 0.35),
        "CSI500_30-100B": ("risk_controlled_csi500_p3_mv30_100", 0.35),
        "CSI300≥100B": ("risk_controlled_csi300_p3_mv100", 0.15),
        "CSI2000<20B": ("tier_p3_mv0_20", 0.15),
    },
}


def main():
    print("=" * 70)
    print("R135: 多层级组合回测模拟")
    print("=" * 70)

    for port_name, components in PORTFOLIOS.items():
        print(f"\n{'='*60}")
        print(f"📊 {port_name}")
        print(f"{'='*60}")

        # Print allocation
        total_active = 0.0
        for comp_name, (output_dir, weight) in components.items():
            status = "🟢 Active" if output_dir else "⚪ Placeholder(0%)"
            print(f"  {comp_name}: {weight:.0%} {status}")
            if output_dir:
                total_active += weight

        # Build active-only portfolio (exclude placeholders)
        active_components = {
            name: (odir, w / total_active)  # rescale to sum=1 for active parts
            for name, (odir, w) in components.items()
            if odir is not None
        }

        # Simulate with rescaled active weights
        port_ret = simulate_portfolio(active_components, port_name)
        if port_ret is None:
            print("  ⚠️ 数据不完整，跳过")
            continue

        # Scale back: active portion * total_active_weight
        port_ret_scaled = port_ret * total_active

        print(f"\n  活跃权重合计: {total_active:.0%}")
        segment_metrics(port_ret_scaled, port_name)

    print(f"\n{'='*70}")
    print("组合模拟完成")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()


