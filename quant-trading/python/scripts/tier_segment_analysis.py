#!/usr/bin/env python
"""R135: 分层策略分段验证 + Alpha衰减检查 + 跨tier相关性分析。

分析内容:
1. 全量期 vs 新规后(2022.08+) 分段对比
2. 滚动12月SR/AR检查alpha是否衰减
3. 跨tier日收益相关系数矩阵
4. 剔除单年后的鲁棒性检查
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def load_nav(output_name: str) -> pd.Series | None:
    """从output目录加载NAV序列。"""
    # Try daily_nav.csv first (most common format)
    csv_path = ROOT / "output" / output_name / "daily_nav.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return df.iloc[:, 0]
    # fallback: parquet
    nav_path = ROOT / "output" / output_name / "nav_series.parquet"
    if nav_path.exists():
        df = pd.read_parquet(nav_path)
        return df.iloc[:, 0] if isinstance(df, pd.DataFrame) else df
    return None


def load_daily_returns(output_name: str) -> pd.Series | None:
    """从output目录加载日收益序列。"""
    ret_path = ROOT / "output" / output_name / "daily_returns.parquet"
    if ret_path.exists():
        df = pd.read_parquet(ret_path)
        s = df.iloc[:, 0] if isinstance(df, pd.DataFrame) else df
        s.index = pd.to_datetime(s.index)
        return s
    # derive from NAV
    nav = load_nav(output_name)
    if nav is not None:
        nav.index = pd.to_datetime(nav.index)
        return nav.pct_change().dropna()
    return None


def compute_metrics(returns: pd.Series) -> dict:
    """从日收益计算核心指标。"""
    if len(returns) < 20:
        return {"AR": np.nan, "SR": np.nan, "MDD": np.nan, "Calmar": np.nan, "n_days": len(returns)}
    ann_factor = 252
    ar = float((1 + returns).prod() ** (ann_factor / len(returns)) - 1)
    vol = float(returns.std() * np.sqrt(ann_factor))
    sr = ar / vol if vol > 1e-9 else 0.0
    cum = (1 + returns).cumprod()
    dd = cum / cum.cummax() - 1
    mdd = float(-dd.min())
    calmar = ar / mdd if mdd > 1e-9 else 0.0
    return {"AR": round(ar, 4), "SR": round(sr, 2), "MDD": round(mdd, 4),
            "Calmar": round(calmar, 2), "n_days": len(returns), "vol": round(vol, 4)}


def segment_analysis(returns: pd.Series, name: str) -> None:
    """分段验证: 全量期 vs 新规后(2022.08+) vs 近期(2024+)。"""
    print(f"\n{'='*60}")
    print(f"📊 分段分析: {name}")
    print(f"{'='*60}")

    # 全量期
    m_full = compute_metrics(returns)
    print(f"  全量期 ({returns.index[0].date()} ~ {returns.index[-1].date()}):")
    print(f"    AR={m_full['AR']:.1%}, SR={m_full['SR']:.2f}, MDD={m_full['MDD']:.1%}, Calmar={m_full['Calmar']:.2f}")

    # 新规后 2022-08-01+
    post_reg = returns[returns.index >= "2022-08-01"]
    if len(post_reg) > 20:
        m_post = compute_metrics(post_reg)
        print(f"  新规后 (2022.08+, {len(post_reg)}天):")
        print(f"    AR={m_post['AR']:.1%}, SR={m_post['SR']:.2f}, MDD={m_post['MDD']:.1%}, Calmar={m_post['Calmar']:.2f}")

    # 近期 2024+
    recent = returns[returns.index >= "2024-01-01"]
    if len(recent) > 20:
        m_rec = compute_metrics(recent)
        print(f"  近期 (2024+, {len(recent)}天):")
        print(f"    AR={m_rec['AR']:.1%}, SR={m_rec['SR']:.2f}, MDD={m_rec['MDD']:.1%}, Calmar={m_rec['Calmar']:.2f}")

    return m_full, m_post if len(post_reg) > 20 else None, m_rec if len(recent) > 20 else None


def rolling_sr_check(returns: pd.Series, name: str, window: int = 252) -> None:
    """滚动12月SR检查alpha衰减。"""
    print(f"\n{'='*60}")
    print(f"📈 滚动{window}日SR: {name}")
    print(f"{'='*60}")
    if len(returns) < window + 20:
        print("  数据不足，跳过")
        return
    rolling_ar = returns.rolling(window).apply(lambda x: (1+x).prod()**(252/len(x))-1, raw=True)
    rolling_vol = returns.rolling(window).std() * np.sqrt(252)
    rolling_sr = rolling_ar / rolling_vol
    rolling_sr = rolling_sr.dropna()

    # 按年取年末快照
    for yr in range(2019, 2027):
        mask = rolling_sr.index.year == yr
        if mask.any():
            yr_data = rolling_sr[mask]
            print(f"  {yr}: 均值SR={yr_data.mean():.2f}, 最低={yr_data.min():.2f}, 最高={yr_data.max():.2f}")

    # 全量 vs 近3年
    full_sr_mean = rolling_sr.mean()
    recent_3y = rolling_sr[rolling_sr.index >= "2023-01-01"]
    if len(recent_3y) > 0:
        recent_sr_mean = recent_3y.mean()
        decay_pct = 1 - recent_sr_mean / full_sr_mean if full_sr_mean > 0 else np.nan
        print(f"\n  全量期滚动SR均值: {full_sr_mean:.2f}")
        print(f"  近3年滚动SR均值: {recent_sr_mean:.2f}")
        print(f"  衰减比例: {decay_pct:.1%}")
        if decay_pct > 0.6:
            print(f"  ⚠️ 严重衰减 (>60%)")
        elif decay_pct > 0.4:
            print(f"  ⚠️ 显著衰减 (>40%)")
        else:
            print(f"  ✅ 衰减在可接受范围")


def cross_tier_correlation(tier_returns: dict[str, pd.Series]) -> None:
    """跨tier日收益相关系数矩阵。"""
    print(f"\n{'='*60}")
    print(f"🔗 跨Tier日收益相关系数矩阵")
    print(f"{'='*60}")
    # align all series
    df = pd.DataFrame(tier_returns)
    df = df.dropna(how="any")
    if df.shape[1] < 2:
        print("  数据不足，跳过")
        return
    corr = df.corr()
    print(corr.to_string(float_format=lambda x: f"{x:.3f}"))

    # check principle E violations
    print("\n  原则E检查 (任意两策略日收益相关 < 0.75):")
    violations = []
    for i, c1 in enumerate(corr.columns):
        for j, c2 in enumerate(corr.columns):
            if j > i and corr.loc[c1, c2] > 0.75:
                violations.append((c1, c2, corr.loc[c1, c2]))
    if violations:
        for c1, c2, r in violations:
            print(f"  ❌ {c1} vs {c2}: r={r:.3f} > 0.75")
    else:
        print(f"  ✅ 所有配对相关系数 < 0.75")

    # mean correlation
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    mean_corr = corr.values[mask].mean()
    print(f"  平均相关系数: {mean_corr:.3f} {'✅ <0.60' if mean_corr < 0.60 else '⚠️ ≥0.60'}")


def robustness_leave_one_year_out(returns: pd.Series, name: str) -> None:
    """逐年剔除鲁棒性检查。"""
    print(f"\n{'='*60}")
    print(f"🛡️ 剔除单年鲁棒性: {name}")
    print(f"{'='*60}")
    full = compute_metrics(returns)
    years = sorted(returns.index.year.unique())
    for yr in years:
        r_excl = returns[returns.index.year != yr]
        m = compute_metrics(r_excl)
        delta = m["AR"] - full["AR"]
        flag = "🔴" if m["AR"] < 0.05 else ("🟡" if delta > 0.03 else "")
        print(f"  剔除{yr}: AR={m['AR']:.1%} (Δ={delta:+.1%}) SR={m['SR']:.2f} {flag}")


# ── 分析目标配置 ──
TIER_CONFIGS = {
    "CSI500_baseline": "risk_controlled_csi500_lgb_p3",
    "CSI500_≥30B": "risk_controlled_csi500_p3_mv30",
    "CSI500_≥50B": "risk_controlled_csi500_p3_mv50",
    "CSI500_≥100B": "risk_controlled_csi500_p3_mv100",
    "CSI500_30-100B": "risk_controlled_csi500_p3_mv30_100",
    "CSI500_50-200B": "risk_controlled_csi500_p3_mv50_200",
    "CSI300_baseline": "risk_controlled_csi300_lgb_p3",
    "CSI300_≥100B": "risk_controlled_csi300_p3_mv100",
    "CSI300_≥200B": "risk_controlled_csi300_p3_mv200",
    "CSI300_100-500B": "risk_controlled_csi300_p3_mv100_500",
    "CSI2000_<20B": "tier_p3_mv0_20",
    "CSI2000_baseline": "risk_controlled_csi2000_lgb_p3",
}


def main():
    print("=" * 70)
    print("R135: 分层策略深度验证 — 分段 + 衰减 + 相关性 + 鲁棒性")
    print("=" * 70)

    # Load all available returns
    all_returns: dict[str, pd.Series] = {}
    for label, output_name in TIER_CONFIGS.items():
        ret = load_daily_returns(output_name)
        if ret is not None:
            all_returns[label] = ret
            print(f"  ✅ {label}: {len(ret)}天 ({ret.index[0].date()}~{ret.index[-1].date()})")
        else:
            print(f"  ❌ {label}: 数据未找到")

    if not all_returns:
        print("无可用数据，退出")
        sys.exit(1)

    # 1. 分段分析 (核心Tiers)
    print("\n" + "=" * 70)
    print("Part 1: 分段分析 (全量 vs 新规后 vs 近期)")
    print("=" * 70)
    for label in ["CSI500_baseline", "CSI500_30-100B", "CSI500_≥100B", "CSI300_baseline", "CSI300_≥100B", "CSI300_≥200B", "CSI2000_<20B"]:
        if label in all_returns:
            segment_analysis(all_returns[label], label)

    # 2. 滚动SR检查
    print("\n" + "=" * 70)
    print("Part 2: 滚动252日SR衰减检查")
    print("=" * 70)
    for label in ["CSI500_baseline", "CSI500_30-100B", "CSI500_≥100B", "CSI300_baseline", "CSI300_≥100B"]:
        if label in all_returns:
            rolling_sr_check(all_returns[label], label)

    # 3. 跨tier相关性
    print("\n" + "=" * 70)
    print("Part 3: 跨Tier日收益相关性")
    print("=" * 70)
    # Select key tiers for correlation
    corr_tiers = {k: v for k, v in all_returns.items()
                  if k in ["CSI500_30-100B", "CSI500_≥100B", "CSI2000_<20B",
                           "CSI300_baseline", "CSI300_≥100B", "CSI300_≥200B"]}
    if len(corr_tiers) >= 2:
        cross_tier_correlation(corr_tiers)

    # 4. 鲁棒性检查 (核心tier)
    print("\n" + "=" * 70)
    print("Part 4: 剔除单年鲁棒性")
    print("=" * 70)
    for label in ["CSI500_30-100B", "CSI500_≥100B", "CSI300_≥100B"]:
        if label in all_returns:
            robustness_leave_one_year_out(all_returns[label], label)

    print("\n" + "=" * 70)
    print("分析完成")
    print("=" * 70)


if __name__ == "__main__":
    main()


