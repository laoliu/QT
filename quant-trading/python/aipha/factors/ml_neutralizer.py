"""ML 训练管道因子中性化模块 (Du 2025)

基于 Du (2025) 偏差校正多因子中性化体系：
  Step 1 — 行业中性化 (行业哑变量 OLS)
  Step 2 — 规模中性化 (含二次项 ln(cap) + ln(cap)²)
  Step 3 — 自适应强度调节 (基于 IC 反馈)

参考: Du (2025) arXiv:2507.07107
  IC 提升 +78%, ICIR 提升 +214%
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 核心中性化函数
# ---------------------------------------------------------------------------


def neutralize_cross_section(
    y: np.ndarray,
    X_reg: np.ndarray,
    min_samples: int = 30,
) -> np.ndarray:
    """单截面 OLS 中性化: y → 残差。

    Args:
        y: (N,) 因子值
        X_reg: (N, K) 回归矩阵 [intercept, log_cap, log_cap², ind_dummies...]
        min_samples: 最少有效样本数

    Returns:
        (N,) 残差, NaN 位置保持 NaN
    """
    result = np.full_like(y, np.nan, dtype=np.float64)
    mask = np.isfinite(y) & np.all(np.isfinite(X_reg), axis=1)
    n_valid = mask.sum()
    if n_valid < min_samples:
        return result
    try:
        beta, _, _, _ = np.linalg.lstsq(
            X_reg[mask], y[mask], rcond=None,
        )
        result[mask] = y[mask] - X_reg[mask] @ beta
    except np.linalg.LinAlgError:
        result[mask] = y[mask]
    return result


def build_regression_matrix(
    log_cap: np.ndarray,
    industry_codes: np.ndarray | None = None,
    quadratic_size: bool = True,
) -> np.ndarray:
    """构建截面回归矩阵。

    Args:
        log_cap: (N,) 对数市值
        industry_codes: (N,) 行业编码 (整数化后), None 则跳过
        quadratic_size: 是否包含 ln(cap)² 项

    Returns:
        (N, K) 回归矩阵
    """
    n = len(log_cap)
    parts: list[np.ndarray] = [np.ones((n, 1))]
    # 规模: ln(cap)
    lc = log_cap.reshape(-1, 1)
    parts.append(lc)
    # 二次规模: ln(cap)²
    if quadratic_size:
        parts.append(lc ** 2)
    # 行业哑变量
    if industry_codes is not None:
        uniq = np.unique(industry_codes[~np.isnan(industry_codes)])
        if len(uniq) > 1:
            for u in uniq[:-1]:  # drop-last 避免共线性
                parts.append((industry_codes == u).astype(np.float64).reshape(-1, 1))
    return np.hstack(parts)


def compute_adaptive_alpha(
    df: pd.DataFrame,
    date_col: str = "datetime",
    alpha_0: float = 1.0,
    beta_vol: float = 0.5,
    vol_window: int = 20,
    vol_long_window: int = 252,
    market_returns: pd.Series | None = None,
) -> pd.Series:
    """计算 Du 2025 自适应中性化强度 alpha_t.

    公式: alpha_t = alpha_0 * (1 + beta_vol * (sigma_short - sigma_long) / sigma_long)
    高波动时加强中性化，低波动时降低中性化保留更多信号。

    Args:
        df: 含 datetime 的面板数据 (用于提取日期和内部计算波动率)
        date_col: 日期列名
        alpha_0: 基础中性化强度（1.0=完全中性化）
        beta_vol: 波动率敏感度
        vol_window: 短期波动率窗口（交易日）
        vol_long_window: 长期波动率窗口（交易日）
        market_returns: 外部传入的市场日度收益率序列 (index=date)，
                        若为 None 则从截面数据内部估算

    Returns:
        按日期索引的 alpha_t Series
    """
    unique_dates = np.sort(df[date_col].unique())

    if market_returns is not None and len(market_returns) > 0:
        # 使用外部市场收益率序列的绝对值作为波动率代理
        vol_proxy = market_returns.abs()
    elif "close" in df.columns:
        # 内部估算: 计算全市场等权日收益率
        # 先按 symbol 排序确保时序正确，再按日聚合
        daily_mean_close = df.groupby(date_col)["close"].mean().sort_index()
        # 市场组合日收益率 (等权)
        mkt_ret = daily_mean_close.pct_change().fillna(0.0)
        vol_proxy = mkt_ret.abs()
    else:
        # 无法计算波动率，返回固定 alpha
        return pd.Series(alpha_0, index=unique_dates)

    # 确保 vol_proxy 按日期排序
    vol_proxy = vol_proxy.sort_index()

    # 滚动窗口计算短期/长期波动率 (使用 std 而非 mean of abs)
    sigma_short = vol_proxy.rolling(vol_window, min_periods=max(1, vol_window // 2)).std()
    sigma_long = vol_proxy.rolling(vol_long_window, min_periods=max(1, vol_long_window // 4)).std()

    # 避免除零
    sigma_long_safe = sigma_long.replace(0, np.nan)
    vol_ratio = (sigma_short - sigma_long_safe) / sigma_long_safe
    vol_ratio = vol_ratio.fillna(0.0)

    alpha_t = alpha_0 * (1.0 + beta_vol * vol_ratio)
    # 限制在 [0.3, 1.5] 区间，防止极端值
    alpha_t = alpha_t.clip(0.3, 1.5)

    return alpha_t


def neutralize_ml_features(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    industry_col: str = "industry",
    cap_col: str = "circ_mv",
    date_col: str = "datetime",
    quadratic_size: bool = True,
    min_samples: int = 30,
    adaptive: bool = False,
    alpha_0: float = 1.0,
    beta_vol: float = 0.5,
    vol_window: int = 20,
    vol_long_window: int = 252,
) -> pd.DataFrame:
    """对 ML 训练 DataFrame 进行按日截面中性化。

    Du 2025 三阶段: 行业 + 规模(含二次项) + 自适应强度。

    Args:
        df: 含 datetime/symbol/因子列/industry/circ_mv 的 DataFrame
        feature_cols: 需要中性化的因子列名列表
        industry_col: 行业列名
        cap_col: 市值列名
        date_col: 日期列名
        quadratic_size: 是否包含二次规模项
        min_samples: 每个截面最少样本数
        adaptive: 是否启用自适应强度调节 (Du 2025 Step 3)
        alpha_0: 基础中性化强度（1.0=完全中性化）
        beta_vol: 波动率敏感度（>0: 高波动加强中性化）
        vol_window: 短期波动率窗口
        vol_long_window: 长期波动率窗口

    Returns:
        中性化后的 DataFrame (原地修改 feature_cols)
    """
    # 不做 df.copy() 以节省内存 (5.5M × 260 cols ≈ 11GB)
    valid_fcols = [c for c in feature_cols if c in df.columns]
    if not valid_fcols:
        logger.warning("neutralize_ml_features: 无有效因子列")
        return df

    # Step 3 准备: 保存原始值用于自适应混合
    if adaptive and alpha_0 < 1.0:
        # 仅在非完全中性化时需要保存原始值
        raw_values = {col: df[col].values.copy() for col in valid_fcols}
    else:
        raw_values = None

    has_industry = industry_col in df.columns
    has_cap = cap_col in df.columns
    if not has_cap:
        logger.warning("neutralize_ml_features: 缺少市值列 '%s', 跳过", cap_col)
        return df

    # 预计算对数市值
    df["_log_cap"] = np.log1p(df[cap_col].clip(lower=0).values)

    # 行业编码 → 整数
    if has_industry:
        ind_cat = df[industry_col].astype("category")
        df["_ind_code"] = ind_cat.cat.codes.astype(np.float64)
        df.loc[df[industry_col].isna(), "_ind_code"] = np.nan
    else:
        df["_ind_code"] = np.nan

    # ===== 纯 numpy 高速实现 (分块, 低内存) =====
    log_cap_arr = df["_log_cap"].values
    ind_code_arr = df["_ind_code"].values if has_industry else None
    date_arr = df[date_col].values

    # 按日期分组的行索引 (一次性计算)
    unique_dates, inverse = np.unique(date_arr, return_inverse=True)
    n_groups = len(unique_dates)

    # 分块处理因子，每块 ≤ CHUNK_SIZE 列, 控制内存
    CHUNK_SIZE = 60
    n_chunks = (len(valid_fcols) + CHUNK_SIZE - 1) // CHUNK_SIZE
    logger.info(
        "中性化: %d 因子 × %d 截面日 (%d 块, numpy-fast)...",
        len(valid_fcols), n_groups, n_chunks,
    )

    n_dates = 0
    for chunk_idx in range(n_chunks):
        chunk_start = chunk_idx * CHUNK_SIZE
        chunk_end = min(chunk_start + CHUNK_SIZE, len(valid_fcols))
        chunk_cols = valid_fcols[chunk_start:chunk_end]
        p = len(chunk_cols)

        # 提取当前块因子到 numpy (5.5M × 60 ≈ 2.6GB)
        feat_chunk = df[chunk_cols].values.astype(np.float64).copy()

        for d_idx in range(n_groups):
            rows = np.where(inverse == d_idx)[0]
            if len(rows) < min_samples:
                continue

            log_cap = log_cap_arr[rows]
            ind_codes = ind_code_arr[rows] if has_industry else None
            X_reg = build_regression_matrix(log_cap, ind_codes, quadratic_size)

            Y = feat_chunk[rows]  # numpy slice
            X_finite = np.all(np.isfinite(X_reg), axis=1)
            common_mask = X_finite & np.all(np.isfinite(Y), axis=1)
            n_common = common_mask.sum()

            out = np.full_like(Y, np.nan)

            if n_common >= min_samples:
                try:
                    Beta, _, _, _ = np.linalg.lstsq(
                        X_reg[common_mask], Y[common_mask], rcond=None,
                    )
                    out[common_mask] = Y[common_mask] - X_reg[common_mask] @ Beta
                except np.linalg.LinAlgError:
                    out[common_mask] = Y[common_mask]

            # 少数因子有独立 NaN 模式: 回退到逐列
            for j in range(p):
                if np.isnan(out[:, j]).all() and not np.isnan(Y[:, j]).all():
                    col_mask = X_finite & np.isfinite(Y[:, j])
                    if col_mask.sum() >= min_samples:
                        out[:, j] = neutralize_cross_section(
                            Y[:, j], X_reg, min_samples,
                        )

            feat_chunk[rows] = out
            if chunk_idx == 0:
                n_dates += 1

        # 写回当前块
        for j, col in enumerate(chunk_cols):
            df[col] = feat_chunk[:, j]
        del feat_chunk

    df.drop(columns=["_log_cap", "_ind_code"], inplace=True, errors="ignore")
    logger.info("中性化完成: %d 因子, %d 截面日 (quadratic=%s)", len(valid_fcols), n_dates, quadratic_size)

    # ===== Step 3: 自适应中性化强度混合 (Du 2025 Eq.8) =====
    if adaptive:
        alpha_t_series = compute_adaptive_alpha(
            df, date_col=date_col, alpha_0=alpha_0,
            beta_vol=beta_vol, vol_window=vol_window,
            vol_long_window=vol_long_window,
        )
        if raw_values is not None:
            # 逐日混合: f_final = alpha_t * f_neutralized + (1 - alpha_t) * f_raw
            date_arr_final = df[date_col].values
            # 预计算 alpha_mapped 一次 (向量化, 避免 255 次重复 dict lookup)
            unique_dates_blend, inv_blend = np.unique(
                date_arr_final, return_inverse=True,
            )
            alpha_per_date = np.array([
                alpha_t_series.get(d, alpha_0)
                for d in unique_dates_blend
            ], dtype=np.float64)
            alpha_mapped = alpha_per_date[inv_blend]  # O(N) broadcast
            one_minus_alpha = 1.0 - alpha_mapped
            for col in valid_fcols:
                f_neut = df[col].values
                f_raw = raw_values[col]
                df[col] = alpha_mapped * f_neut + one_minus_alpha * f_raw
            logger.info(
                "自适应混合完成: alpha_0=%.2f, beta_vol=%.2f, "
                "alpha_t 范围 [%.3f, %.3f]",
                alpha_0, beta_vol,
                alpha_t_series.min(), alpha_t_series.max(),
            )
        else:
            logger.info(
                "自适应中性化: alpha_0=1.0 (完全中性化), alpha_t 仅监控用途. "
                "范围 [%.3f, %.3f]",
                alpha_t_series.min(), alpha_t_series.max(),
            )

    return df
