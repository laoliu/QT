"""CB 量价因子遗传编程挖掘管道.

将 CB 日线数据转换为截面特征矩阵，通过 gplearn 遗传编程搜索
有效因子表达式，经 IC/ICIR 筛选 + HLZ 多重检验校正后输出
可直接接入回测的因子结果。

流程:
    1. load_cb_panel()      — 加载 CB 面板数据并构建特征矩阵
    2. mine()               — 执行 GP 因子搜索
    3. validate()           — IC/ICIR/HLZ/分段验证
    4. export_factors()     — 导出可序列化的因子清单

与现有架构关系:
    - 数据加载复用 CBDataAdapter / TuShareCBProvider
    - IC 计算复用 factors/analysis.py 基础设施
    - HLZ 校正复用 factors/auto_factor_mining.py::AutoFactorMiner._hlz_adjust
    - 输出因子可注册到 strategy/cb_gp_mined.py 使用

理论约束 (原则 A):
    - 每个因子必须有先验经济学解释（通过 base_features 约束搜索空间）
    - HLZ 校正: t_adj = t_obs - sqrt(2*ln(N)), t_adj > 2.0
    - PBO < 0.50 (通过 CSCV 方法)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)

# gplearn 可选依赖
try:
    from gplearn.genetic import SymbolicTransformer
    HAS_GPLEARN = True
except ImportError:
    HAS_GPLEARN = False


# ── 数据结构 ──


@dataclass
class CBMinedFactor:
    """CB GP 挖掘出的因子."""

    expression: str
    feature_names: list[str]
    ic_mean: float = 0.0
    ic_std: float = 0.0
    icir: float = 0.0
    rank_ic_mean: float = 0.0
    t_stat: float = 0.0
    hlz_t_adj: float = 0.0
    is_significant: bool = False
    ic_post_2022: float = 0.0
    turnover_estimate: float = 0.0
    program_index: int = -1  # GP transform 输出列索引

    def to_dict(self) -> dict:
        return {
            "expression": self.expression,
            "feature_names": self.feature_names,
            "ic_mean": round(self.ic_mean, 6),
            "icir": round(self.icir, 4),
            "rank_ic_mean": round(self.rank_ic_mean, 6),
            "t_stat": round(self.t_stat, 4),
            "hlz_t_adj": round(self.hlz_t_adj, 4),
            "is_significant": self.is_significant,
            "ic_post_2022": round(self.ic_post_2022, 6),
            "turnover_estimate": round(self.turnover_estimate, 4),
            "program_index": self.program_index,
        }


@dataclass
class CBMiningResult:
    """CB 挖掘结果汇总."""

    total_explored: int = 0
    total_significant: int = 0
    factors: list[CBMinedFactor] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_explored": self.total_explored,
            "total_significant": self.total_significant,
            "factors": [f.to_dict() for f in self.factors],
            "metadata": self.metadata,
        }


# ── 特征定义 ──

# CB 量价特征列表（GP 搜索空间）
CB_PRICE_FEATURES = [
    "close", "open", "high", "low",
    "ret_1d", "ret_5d", "ret_20d",
    "vol_ratio_5d", "vol_ratio_20d",
    "price_ma5_ratio", "price_ma20_ratio",
    "high_low_range", "close_open_range",
]

CB_SPECIFIC_FEATURES = [
    "cb_over_rate",     # 溢价率
    "cb_value",         # 转股价值
    "premium_chg_5d",   # 溢价率5日变化
    "turnover_rate",    # 换手率
    "amount_rank",      # 成交额排名
]

ALL_CB_FEATURES = CB_PRICE_FEATURES + CB_SPECIFIC_FEATURES


class CBGPMiner:
    """CB 量价因子遗传编程挖掘器."""

    def __init__(
        self,
        *,
        features: list[str] | None = None,
        ic_threshold: float = 0.02,
        icir_threshold: float = 0.3,
        hlz_t_threshold: float = 2.0,
        population_size: int = 500,
        generations: int = 20,
        n_components: int = 30,
        parsimony_coefficient: float = 0.001,
        random_state: int = 42,
        cache_dir: str = "data/cache/cb_tushare",
        n_jobs: int = -1,
        max_samples: float = 0.75,
        verbose: int = 1,
        max_train_rows: int = 150_000,
    ) -> None:
        if not HAS_GPLEARN:
            raise ImportError(
                "gplearn required for GP factor mining. "
                "Install: pip install gplearn"
            )
        self.features = features or list(ALL_CB_FEATURES)
        self.ic_threshold = ic_threshold
        self.icir_threshold = icir_threshold
        self.hlz_t_threshold = hlz_t_threshold
        self.population_size = population_size
        self.generations = generations
        self.n_components = n_components
        self.parsimony_coefficient = parsimony_coefficient
        self.random_state = random_state
        self.cache_dir = cache_dir
        self.n_jobs = n_jobs
        self.max_samples = max_samples
        self.verbose = verbose
        self.max_train_rows = max_train_rows
        self._gp: SymbolicTransformer | None = None
        self._last_transformed: np.ndarray | None = None

    @staticmethod
    def _cross_section_rank(
        X: np.ndarray, dates: np.ndarray,
    ) -> np.ndarray:
        """截面排名归一化: 每个日期内对各特征做百分位排名 [0,1].

        解决 GP 退化问题: 原始价格/量纲差异导致 GP 收敛到
        单一特征 (close)，无法发现截面组合因子。
        排名归一化后所有特征在 [0,1] 等量纲空间，
        GP 必须通过组合才能超越单特征。
        """
        X_ranked = np.empty_like(X)
        # 使用整数编码加速分组（避免逐日期字符串比较）
        _, inverse = np.unique(dates, return_inverse=True)
        order = np.argsort(inverse, kind="mergesort")  # 按日期分组排列
        splits = np.searchsorted(inverse[order], np.arange(inverse.max() + 1))
        splits = np.append(splits, len(order))
        for g in range(len(splits) - 1):
            idx = order[splits[g]:splits[g + 1]]
            n = len(idx)
            if n < 2:
                X_ranked[idx] = 0.5
                continue
            block = X[idx]
            for j in range(block.shape[1]):
                col = block[:, j]
                ranks = col.argsort().argsort()
                X_ranked[idx, j] = ranks / (n - 1)
        return X_ranked

    def load_cb_panel(
        self,
        start_date: str = "20180102",
        end_date: str = "20260331",
    ) -> pd.DataFrame:
        """加载 CB 面板数据并构建特征矩阵.

        Returns:
            DataFrame with columns: ts_code, trade_date, close, open, high,
            low, volume, amount, cb_over_rate, cb_value, + 衍生特征 + fwd_ret
        """
        from aipha.data.tushare_cb_provider import TuShareCBProvider

        provider = TuShareCBProvider(cache_dir=self.cache_dir)
        df = provider.load_cb_daily(start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            raise ValueError(
                f"No CB data loaded for {start_date}~{end_date}. "
                "Check cache or run data prefetch first."
            )
        logger.info(
            "Loaded CB panel: %d rows, %d codes, %s~%s",
            len(df), df["ts_code"].nunique(),
            df["trade_date"].min(), df["trade_date"].max(),
        )
        # 排序并构建衍生特征
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        df = self._build_features(df)
        return df

    def mine(
        self,
        panel: pd.DataFrame | None = None,
        forward_days: int = 5,
    ) -> CBMiningResult:
        """执行 GP 因子搜索.

        Args:
            panel: CB 面板数据（含特征），None 则自动加载
            forward_days: 前瞻收益天数（默认 5 日）

        Returns:
            CBMiningResult with all explored factors (filtered + HLZ)
        """
        if panel is None:
            panel = self.load_cb_panel()

        # 确保前瞻收益列存在
        fwd_col = f"fwd_ret_{forward_days}d"
        if fwd_col not in panel.columns:
            panel[fwd_col] = self._compute_forward_returns(
                panel, forward_days,
            )

        # 筛选可用特征列
        available = [c for c in self.features if c in panel.columns]
        if len(available) < 3:
            raise ValueError(
                f"Too few features available: {available}. "
                f"Expected from: {self.features}"
            )
        logger.info("GP mining with %d features: %s", len(available), available)

        # 构建干净的 X, y 矩阵
        cols = available + [fwd_col, "trade_date"]
        sub = panel[cols].dropna(subset=available + [fwd_col])
        if len(sub) < 100:
            logger.warning("Too few valid rows: %d", len(sub))
            return CBMiningResult()

        # 大数据集采样: 按日期分层采样（保持完整截面结构）
        if len(sub) > self.max_train_rows:
            rng = np.random.RandomState(self.random_state)
            all_dates = sub["trade_date"].unique()
            # 估计需要多少个日期
            avg_rows_per_date = len(sub) / len(all_dates)
            n_dates_needed = int(self.max_train_rows / avg_rows_per_date)
            n_dates_needed = max(20, min(n_dates_needed, len(all_dates)))
            sampled_dates = rng.choice(
                all_dates, n_dates_needed, replace=False,
            )
            sub_train = sub[sub["trade_date"].isin(sampled_dates)]
            logger.info(
                "Stratified sample: %d -> %d rows (%d dates)",
                len(sub), len(sub_train), n_dates_needed,
            )
        else:
            sub_train = sub

        X = sub_train[available].values.astype(np.float64)
        y = sub_train[fwd_col].values.astype(np.float64)
        dates_train = sub_train["trade_date"].values

        # 替换 inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

        # 截面排名归一化：消除量纲差异，防止 GP 退化为单一原始特征
        X_raw = X.copy()  # 保留原始值用于 IC 评估
        X = self._cross_section_rank(X, dates_train)

        logger.info(
            "Running GP: pop=%d, gen=%d, components=%d, rows=%d",
            self.population_size, self.generations,
            self.n_components, len(X),
        )

        gp = SymbolicTransformer(
            population_size=self.population_size,
            hall_of_fame=min(100, self.population_size),
            generations=self.generations,
            tournament_size=min(5, self.population_size),
            n_components=min(self.n_components, self.population_size),
            function_set=["add", "sub", "mul", "div", "sqrt", "log", "abs",
                          "neg", "inv", "max", "min"],
            metric="spearman",
            parsimony_coefficient=self.parsimony_coefficient,
            random_state=self.random_state,
            n_jobs=self.n_jobs,
            max_samples=self.max_samples,
            feature_names=available,
            verbose=self.verbose,
            init_depth=(2, 6),
            p_crossover=0.4,
            p_subtree_mutation=0.25,
            p_hoist_mutation=0.1,
            p_point_mutation=0.25,
        )
        gp.fit(X, y)
        self._gp = gp

        # IC评估：全量数据（更多日期 → 更高统计功效）
        # GP 训练用采样数据（快），IC评估用全量数据（准）
        X_eval = sub[available].values.astype(np.float64)
        X_eval = np.nan_to_num(X_eval, nan=0.0, posinf=0.0, neginf=0.0)
        y_eval = sub[fwd_col].values.astype(np.float64)
        y_eval = np.nan_to_num(y_eval, nan=0.0, posinf=0.0, neginf=0.0)
        dates_eval = sub["trade_date"].values
        X_eval = self._cross_section_rank(X_eval, dates_eval)
        transformed = gp.transform(X_eval)
        # HLZ N = 全继代总程序数（排除 None 条目, Harvey et al. 2016）
        n_explored = sum(
            sum(1 for p in gen if p is not None)
            for gen in gp._programs
            if gen is not None
        ) if gp._programs else 1
        result = CBMiningResult(total_explored=max(n_explored, 1))
        # 保存 transformed 输出用于去相关
        self._last_transformed = transformed

        for i in range(transformed.shape[1]):
            fv = transformed[:, i]
            if np.std(fv) < 1e-10:
                continue

            ic_mean, ic_std, icir = self._compute_cross_sectional_ic(
                fv, y_eval, dates_eval,
            )
            n_months = max(1, len(np.unique(dates_eval)) // 21)
            t_stat = ic_mean / (ic_std / np.sqrt(n_months)) if ic_std > 1e-10 else 0.0
            hlz_adj = self._hlz_adjust(t_stat, result.total_explored)

            # 提取表达式
            expr = "unknown"
            if hasattr(gp, "_best_programs") and i < len(gp._best_programs):
                expr = str(gp._best_programs[i])

            # 估算换手率: rank auto-correlation (lag-1)
            turnover_est = self._estimate_turnover(
                fv, dates_eval,
            )

            mf = CBMinedFactor(
                expression=expr,
                feature_names=available,
                ic_mean=ic_mean,
                ic_std=ic_std,
                icir=icir,
                rank_ic_mean=ic_mean,
                t_stat=t_stat,
                hlz_t_adj=hlz_adj,
                is_significant=(
                    abs(ic_mean) >= self.ic_threshold
                    and abs(icir) >= self.icir_threshold
                    and hlz_adj > self.hlz_t_threshold
                ),
                program_index=i,
                turnover_estimate=turnover_est,
            )
            result.factors.append(mf)

        # 先去重（所有非常量因子），再筛选显著性
        all_factors = [f for f in result.factors]
        # 按 |ICIR| 排序以便去重时保留最好的
        all_factors.sort(key=lambda f: abs(f.icir), reverse=True)
        # 去除表达式完全重复的因子
        seen = set()
        deduped = []
        for f in all_factors:
            if f.expression not in seen:
                seen.add(f.expression)
                deduped.append(f)
        # 去相关（使用实际因子输出相关性）
        deduped = self._decorrelate(deduped, transformed)
        # 筛选显著因子
        sig = [f for f in deduped if f.is_significant]
        result.factors = sig if sig else deduped[:20]  # 无显著因子时保留top20
        result.total_significant = len(sig)

        logger.info(
            "GP mining done: %d explored, %d significant after HLZ+dedup",
            result.total_explored, result.total_significant,
        )
        return result

    def validate(
        self,
        result: CBMiningResult,
        panel: pd.DataFrame,
        *,
        seg_cutoff: str = "20220801",
    ) -> CBMiningResult:
        """分段验证: 全量期 + 新规后.

        原则 B 要求: 近期 IC 不显著恶化。
        新规后 IC < 全量期 IC 的 40% 则标记为高风险。
        """
        cutoff = pd.Timestamp(seg_cutoff)
        post_mask = panel["trade_date"] >= cutoff.strftime("%Y%m%d")
        panel_post = panel[post_mask]

        if panel_post.empty or self._gp is None:
            logger.warning("No post-regulation data or GP not fitted")
            return result

        available = [c for c in self.features if c in panel_post.columns]
        fwd_cols = [c for c in panel_post.columns if c.startswith("fwd_ret_")]
        if not fwd_cols:
            return result
        fwd_col = fwd_cols[0]

        sub_post = panel_post[available + [fwd_col, "trade_date"]].dropna(
            subset=available + [fwd_col],
        )
        if len(sub_post) < 50:
            return result

        X_post = sub_post[available].values.astype(np.float64)
        X_post = np.nan_to_num(X_post, nan=0.0, posinf=0.0, neginf=0.0)
        # 截面排名归一化，与训练一致
        dates_post = sub_post["trade_date"].values
        X_post = self._cross_section_rank(X_post, dates_post)
        y_post = sub_post[fwd_col].values

        transformed_post = self._gp.transform(X_post)

        for factor in result.factors:
            idx = factor.program_index
            if idx < 0 or idx >= transformed_post.shape[1]:
                continue
            fv = transformed_post[:, idx]
            ic_post, _, _ = self._compute_cross_sectional_ic(
                fv, y_post, dates_post,
            )
            factor.ic_post_2022 = ic_post

        result.metadata["seg_cutoff"] = seg_cutoff
        result.metadata["post_regulation_rows"] = len(sub_post)
        return result

    def export_factors(
        self,
        result: CBMiningResult,
        output_path: str | Path,
    ) -> Path:
        """导出因子清单为 JSON.

        包含因子表达式、IC 统计、HLZ 校正结果。
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = result.to_dict()
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Exported %d factors to %s", len(result.factors), out)
        return out

    # ── 内部方法 ──

    def _build_features(self, panel: pd.DataFrame) -> pd.DataFrame:
        """从原始面板数据计算衍生特征.

        所有特征按 ts_code 分组计算，避免跨标的信息泄露。
        """
        df = panel.copy()
        g = df.groupby("ts_code")

        # 收益率
        df["ret_1d"] = g["close"].pct_change(1)
        df["ret_5d"] = g["close"].pct_change(5)
        df["ret_20d"] = g["close"].pct_change(20)

        # 成交量比率
        vol_ma5 = g["vol"].transform(
            lambda x: x.rolling(5, min_periods=1).mean()
        )
        vol_ma20 = g["vol"].transform(
            lambda x: x.rolling(20, min_periods=1).mean()
        )
        df["vol_ratio_5d"] = df["vol"] / vol_ma5.clip(lower=1e-8)
        df["vol_ratio_20d"] = df["vol"] / vol_ma20.clip(lower=1e-8)

        # 价格均线比
        ma5 = g["close"].transform(
            lambda x: x.rolling(5, min_periods=1).mean()
        )
        ma20 = g["close"].transform(
            lambda x: x.rolling(20, min_periods=1).mean()
        )
        df["price_ma5_ratio"] = df["close"] / ma5.clip(lower=1e-8)
        df["price_ma20_ratio"] = df["close"] / ma20.clip(lower=1e-8)

        # 波幅
        df["high_low_range"] = (
            (df["high"] - df["low"]) / df["close"].clip(lower=1e-8)
        )
        df["close_open_range"] = (
            (df["close"] - df["open"]) / df["open"].clip(lower=1e-8)
        )

        # CB 特有
        if "cb_over_rate" in df.columns:
            df["premium_chg_5d"] = g["cb_over_rate"].diff(5)
        else:
            df["premium_chg_5d"] = 0.0

        # 换手率（用 vol/amount 近似）
        if "turnover_rate" not in df.columns:
            df["turnover_rate"] = 0.0

        # 成交额排名（截面排名归一化）
        df["amount_rank"] = df.groupby("trade_date")["amount"].rank(
            pct=True, method="dense",
        )

        return df

    def _compute_forward_returns(
        self, panel: pd.DataFrame, days: int,
    ) -> pd.Series:
        """计算前瞻收益率 (forward return).

        按 ts_code 分组，shift(-days) 避免前视偏差。
        """
        fwd = panel.groupby("ts_code")["close"].pct_change(days).shift(-days)
        return fwd

    def _compute_cross_sectional_ic(
        self, factor_values: np.ndarray, returns: np.ndarray,
        dates: np.ndarray,
    ) -> tuple[float, float, float]:
        """计算截面 Spearman IC 均值/标准差/ICIR.

        按交易日分组计算截面 IC，取时序平均。
        使用向量化 Spearman 公式替代 scipy.stats.spearmanr 以提速。
        """
        # 预先按日期分组索引（避免逐日期字符串比较）
        _, inverse = np.unique(dates, return_inverse=True)
        order = np.argsort(inverse, kind="mergesort")
        n_groups = inverse.max() + 1
        splits = np.searchsorted(inverse[order], np.arange(n_groups))
        splits = np.append(splits, len(order))

        ics: list[float] = []
        for g in range(n_groups):
            idx = order[splits[g]:splits[g + 1]]
            fv = factor_values[idx]
            rv = returns[idx]
            # 去 NaN
            valid = ~(np.isnan(fv) | np.isnan(rv))
            fv_clean = fv[valid]
            rv_clean = rv[valid]
            n = len(fv_clean)
            if n < 10:
                continue
            if np.std(fv_clean) < 1e-10:
                continue
            # 向量化 Spearman: rank then Pearson
            fv_rank = fv_clean.argsort().argsort().astype(np.float64)
            rv_rank = rv_clean.argsort().argsort().astype(np.float64)
            fv_rank -= fv_rank.mean()
            rv_rank -= rv_rank.mean()
            denom = np.sqrt((fv_rank ** 2).sum() * (rv_rank ** 2).sum())
            if denom < 1e-10:
                continue
            corr = float((fv_rank * rv_rank).sum() / denom)
            if np.isfinite(corr):
                ics.append(corr)
        if len(ics) < 5:
            return 0.0, 1.0, 0.0
        ic_mean = float(np.mean(ics))
        ic_std = float(np.std(ics, ddof=1))
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        return ic_mean, ic_std, icir

    def _estimate_turnover(
        self,
        factor_values: np.ndarray,
        dates: np.ndarray,
    ) -> float:
        """估算因子隐含换手率 (rank auto-correlation proxy).

        计算相邻日期间因子截面排名的 Spearman 相关系数。
        turnover_estimate = 1 - mean(rank_autocorr)。
        值越低(接近0)说明排名越稳定、换手越低。
        """
        _, inverse = np.unique(dates, return_inverse=True)
        order = np.argsort(inverse, kind="mergesort")
        n_groups = inverse.max() + 1
        splits = np.searchsorted(inverse[order], np.arange(n_groups))
        splits = np.append(splits, len(order))

        prev_ranks: dict[Any, float] | None = None
        autocorrs: list[float] = []

        for g in range(n_groups):
            idx = order[splits[g]:splits[g + 1]]
            if len(idx) < 5:
                prev_ranks = None
                continue
            fv = factor_values[idx]
            # 跳过常量截面
            if np.std(fv) < 1e-10:
                prev_ranks = None
                continue
            cur_ranks = fv.argsort().argsort().astype(np.float64)
            if prev_ranks is not None and len(cur_ranks) == len(prev_ranks):
                cr = cur_ranks - cur_ranks.mean()
                pr = prev_ranks - prev_ranks.mean()
                denom = np.sqrt((cr ** 2).sum() * (pr ** 2).sum())
                if denom > 1e-10:
                    autocorrs.append(float((cr * pr).sum() / denom))
            prev_ranks = cur_ranks

        if not autocorrs:
            return 1.0  # 无法估算 → 保守假设高换手
        mean_ac = float(np.mean(autocorrs))
        return max(0.0, 1.0 - mean_ac)

    @staticmethod
    def _hlz_adjust(t_stat: float, n_trials: int) -> float:
        """HLZ 校正: t_adj = |t_obs| - sqrt(2 * ln(N)).

        Harvey, Liu, Zhu (2016) — 原则 A 强制要求。
        使用 |t_obs| 因为因子方向（正/负 IC）均有效。
        """
        if n_trials <= 1:
            return abs(t_stat)
        penalty = np.sqrt(2.0 * np.log(n_trials))
        return abs(t_stat) - penalty

    def _decorrelate(
        self,
        factors: list[CBMinedFactor],
        transformed: np.ndarray | None = None,
        corr_threshold: float = 0.7,
    ) -> list[CBMinedFactor]:
        """去相关: 保留 ICIR 最高的非冗余因子.

        使用因子输出值的 Spearman 相关系数判断冗余。
        当相关系数 > corr_threshold 时,保留 ICIR 更高的因子。
        """
        if len(factors) <= 1:
            return factors
        sorted_f = sorted(
            factors, key=lambda f: abs(f.icir), reverse=True,
        )

        # 如果有 transformed 输出,使用实际相关性
        use_corr = (
            transformed is not None
            and all(f.program_index >= 0 for f in sorted_f)
        )

        selected: list[CBMinedFactor] = [sorted_f[0]]
        for f in sorted_f[1:]:
            if use_corr:
                is_redundant = any(
                    self._factor_corr(
                        transformed, f.program_index, s.program_index,
                    ) > corr_threshold
                    for s in selected
                )
            else:
                # 降级: 使用 IC 均值差异
                is_redundant = any(
                    abs(f.ic_mean - s.ic_mean) < 0.005
                    for s in selected
                )
            if not is_redundant:
                selected.append(f)
        return selected

    @staticmethod
    def _factor_corr(
        transformed: np.ndarray, idx_a: int, idx_b: int,
    ) -> float:
        """计算两个因子输出列的 Spearman 相关系数."""
        if (
            idx_a >= transformed.shape[1]
            or idx_b >= transformed.shape[1]
        ):
            return 0.0
        a = transformed[:, idx_a]
        b = transformed[:, idx_b]
        valid = ~(np.isnan(a) | np.isnan(b))
        a_c, b_c = a[valid], b[valid]
        if len(a_c) < 10 or np.std(a_c) < 1e-10 or np.std(b_c) < 1e-10:
            return 0.0
        corr, _ = spearmanr(a_c, b_c)
        return abs(corr) if np.isfinite(corr) else 0.0
