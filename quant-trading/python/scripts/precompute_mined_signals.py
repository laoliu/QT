"""预计算挖掘因子信号——为回测生成 OOS 回放文件.

从 independent_factors.json 加载 Top-N 因子表达式，
使用 FactorExpressionParser 在面板数据上计算，
rank-normalize 后等权/ICIR 加权合成综合信号，
输出为 parquet (datetime, symbol, pred_score) 供 ml_factor 策略回放.

用法:
    python scripts/precompute_mined_signals.py \
        --factors output/auto_mining_v6/independent_factors.json \
        --top 8 \
        --out output/mined_factor_signals.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from aipha.factors.factor_expression_parser import FactorExpressionParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("precompute_signals")

# ---------------------------------------------------------------------------
# 主函数骨架
# ---------------------------------------------------------------------------


def load_factors(path: str, top_n: int) -> list[dict]:
    """加载因子列表，按 composite_score 取 Top-N."""
    data = json.load(open(path, encoding="utf-8"))
    # 按 composite_score 降序
    data.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    selected = data[:top_n]
    logger.info(
        "选择 Top-%d 因子 (共 %d), score 范围: %.3f ~ %.3f",
        len(selected), len(data),
        selected[-1].get("composite_score", 0) if selected else 0,
        selected[0].get("composite_score", 0) if selected else 0,
    )
    for i, f in enumerate(selected, 1):
        logger.info(
            "  %d. %s  ICIR=%.3f  score=%.3f  expr=%s",
            i, f["name"], f.get("kb_icir", 0),
            f.get("composite_score", 0),
            f.get("expression", "?")[:60],
        )
    return selected


def load_panel_data(universe: str, start: str, end: str) -> pd.DataFrame:
    """加载面板数据（复用 train_ml_factor 数据管道）."""
    from train_ml_factor import filter_universe, load_daily_data
    df = load_daily_data(start, end)
    df = filter_universe(df, universe)
    logger.info(
        "面板数据: %d 行, %d 股, %s ~ %s",
        len(df), df["symbol"].nunique(),
        df["datetime"].min().strftime("%Y-%m-%d"),
        df["datetime"].max().strftime("%Y-%m-%d"),
    )
    return df


def compute_factor_signals(
    df: pd.DataFrame,
    factors: list[dict],
    weight_mode: str = "icir",
) -> pd.DataFrame:
    """计算全部因子表达式并合成综合信号.

    Args:
        df: 面板 DataFrame (symbol, datetime, close, open, ...)
        factors: 因子列表 (含 expression, kb_icir 等)
        weight_mode: "equal" 等权 | "icir" ICIR 绝对值加权

    Returns:
        DataFrame with columns: datetime, symbol, pred_score
    """
    parser = FactorExpressionParser()
    n_factors = len(factors)
    factor_cols: list[str] = []
    weights: list[float] = []

    for i, finfo in enumerate(factors):
        expr = finfo["expression"]
        name = finfo["name"]
        icir = finfo.get("kb_icir", 0)
        col_name = f"_fac_{i}"
        logger.info("[%d/%d] 计算因子: %s", i + 1, n_factors, name)

        try:
            parsed = parser.parse(expr)
            vals = parser.execute(parsed, df)
            df[col_name] = vals
        except Exception as e:
            logger.warning("因子 %s 计算失败: %s, 跳过", name, e)
            continue

        factor_cols.append(col_name)
        # ICIR 方向: 负 ICIR → 反转信号 (乘 -1 使得 higher = better)
        if weight_mode == "icir":
            w = abs(icir) if icir != 0 else 0.5
        else:
            w = 1.0
        sign = -1.0 if icir < 0 else 1.0
        weights.append(w * sign)

    if not factor_cols:
        raise RuntimeError("没有成功计算的因子")

    # 逐日截面 rank-normalize + 加权合成
    logger.info("合成 %d 个因子信号 (mode=%s)...", len(factor_cols), weight_mode)
    total_w = sum(abs(w) for w in weights)

    results: list[pd.DataFrame] = []
    for dt, grp in df.groupby("datetime"):
        if len(grp) < 30:
            continue
        score = np.zeros(len(grp))
        for col, w in zip(factor_cols, weights):
            vals = grp[col].values
            valid = np.isfinite(vals)
            if valid.sum() < 10:
                continue
            # 截面排名归一化 (0~1)
            ranks = np.full_like(vals, np.nan)
            ranks[valid] = pd.Series(vals[valid]).rank(pct=True).values
            ranks = np.nan_to_num(ranks, nan=0.5)
            score += ranks * (w / total_w)
        sub = pd.DataFrame({
            "datetime": dt,
            "symbol": grp["symbol"].values,
            "pred_score": score,
        })
        results.append(sub)

    out = pd.concat(results, ignore_index=True)
    logger.info("信号生成完毕: %d 行, %d 日", len(out), out["datetime"].nunique())

    # 清理临时列
    for col in factor_cols:
        df.drop(columns=[col], inplace=True, errors="ignore")
    return out


def main() -> None:
    """CLI 入口."""
    ap = argparse.ArgumentParser(description="预计算挖掘因子信号")
    ap.add_argument("--factors", default="output/auto_mining_v6/independent_factors.json",
                    help="独立因子 JSON 路径")
    ap.add_argument("--top", type=int, default=8, help="选取 Top-N 因子")
    ap.add_argument("--out", default="output/mined_factor_signals.parquet",
                    help="输出 parquet 路径")
    ap.add_argument("--universe", default="csi2000", help="Universe")
    ap.add_argument("--start", default="2015-01-05", help="起始日期")
    ap.add_argument("--end", default="2026-02-28", help="结束日期")
    ap.add_argument("--weight", default="icir", choices=["equal", "icir"],
                    help="因子权重模式")
    args = ap.parse_args()

    factors = load_factors(str(ROOT / args.factors), args.top)
    df = load_panel_data(args.universe, args.start, args.end)
    signals = compute_factor_signals(df, factors, weight_mode=args.weight)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    signals.to_parquet(out_path, index=False)
    logger.info("信号已保存: %s (%d 行)", out_path, len(signals))


if __name__ == "__main__":
    main()
