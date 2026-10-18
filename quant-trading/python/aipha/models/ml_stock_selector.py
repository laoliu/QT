"""ML 股票截面选择器 — Walk-Forward LightGBM 训练 + 预测管线。

基于 Alpha158 量价因子 + LightGBM，通过 Walk-Forward 滚动训练
对 A 股截面进行排名打分，输出每日选股信号。

训练方案：
- 滚动窗口: train_years 训练 → predict_months 预测 → 滚动前移
- Purge gap: 训练集末尾与预测集起始之间预留 purge_days
- 标签: forward_return_N 的截面排名分位 (0~1)
- 目标: lambdarank (NDCG@top_k) 或 regression (MSE)

理论基础：
- Gu, Kelly, Xiu (2020): 金融ML R²<5%, 特征工程>模型选择
- López de Prado (2018): Purged K-Fold 时序校验
- Microsoft Qlib Alpha158: 158 量价因子标准集

与现有架构关系：
- 特征来自 factors/qlib_alpha.py
- 推理结果对接 strategy/ml_factor_strategy.py
- 参考 models/cb_lgb_ranker.py 的 LightGBM 集成模式
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


def _ordered_fold_files(path: Path, suffix: str) -> list[Path]:
    """Read saved folds in numeric training order, excluding auxiliary files."""
    indexed: dict[int, Path] = {}
    for artifact in path.glob(f"fold_*{suffix}"):
        if suffix == ".pkl" and artifact.stem.endswith(("_fcols", "_dl_meta", "_gru_meta")):
            continue
        raw_index = artifact.stem.removeprefix("fold_")
        if not raw_index.isdecimal():
            raise ValueError(f"invalid model fold artifact name: {artifact.name}")
        index = int(raw_index)
        if index in indexed:
            raise ValueError(f"duplicate model fold index {index}")
        indexed[index] = artifact
    return [indexed[index] for index in sorted(indexed)]


class _ElasticNetWrapper:
    """ElasticNet + StandardScaler 包装器，统一 predict 接口。"""

    def __init__(self, model: Any, scaler: Any, feature_cols: list[str]):
        self.model = model
        self.scaler = scaler
        self.feature_cols = feature_cols

    def predict(self, X: np.ndarray) -> np.ndarray:
        X_scaled = self.scaler.transform(X)
        return self.model.predict(X_scaled)

    def feature_importance(self) -> dict[str, float]:
        coefs = np.abs(self.model.coef_)
        return {
            self.feature_cols[i]: float(coefs[i])
            for i in range(len(self.feature_cols))
        }


class _XGBoostWrapper:
    """XGBoost Booster 包装器，统一 predict 接口。"""

    def __init__(self, booster: Any, feature_cols: list[str]):
        self.booster = booster
        self.feature_cols = feature_cols

    def predict(self, X: np.ndarray) -> np.ndarray:
        import xgboost as xgb
        dmat = xgb.DMatrix(X, feature_names=self.feature_cols)
        return self.booster.predict(dmat)

    def feature_importance(
        self, importance_type: str = "gain",
    ) -> np.ndarray:
        scores = self.booster.get_score(
            importance_type=importance_type,
        )
        result = np.zeros(len(self.feature_cols))
        for i, col in enumerate(self.feature_cols):
            result[i] = scores.get(col, 0.0)
        return result


@dataclass
class MLSelectorConfig:
    """ML 股票选择器配置。"""

    # --- 特征 ---
    feature_cols: list[str] = field(default_factory=list)

    # --- 标签 ---
    label_col: str = "fwd_return_rank"
    forward_days: int = 5
    label_skip_days: int = 0       # T+1 对齐：跳过信号日后 N 天再计算收益窗口
    label_price_field: str = "close"  # 收益计算价格字段："close" 或 "open"
    label_type: str = "rank"       # "rank"|"triple_barrier" — 标签生成方式

    # --- Triple-Barrier 专有（仅 label_type="triple_barrier" 时生效）---
    tb_upper_bps: float = 100.0    # 上障碍基点数（止盈）
    tb_lower_bps: float = 100.0    # 下障碍基点数（止损）
    tb_dynamic: bool = False       # 动态障碍（基于近期波动率）
    tb_vol_lookback: int = 20      # 动态障碍波动率回看窗口
    tb_vol_multiplier: float = 1.5  # 动态障碍波动率乘数

    # --- Walk-Forward ---
    train_years: int = 3
    predict_months: int = 3
    purge_days: int = 10  # 最小值；实际 purge = max(purge_days, forward_days + 5)

    # --- 模型类型 ---
    model_type: str = "lightgbm"  # "lightgbm"|"double_ensemble"|"xgboost"|"elasticnet"|"gru"|"tra"|"master"|"gat"|"hist"
    # Experimental implementations require a deliberate legacy opt-in.
    # The canonical research API accepts only LightGBM and ElasticNet.
    allow_experimental_models: bool = False

    # --- LightGBM ---
    n_estimators: int = 300
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 50
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.1
    reg_lambda: float = 1.0
    objective: str = "lambdarank"
    n_rank_bins: int = 5          # LambdaRank relevance label 分级数
    truncate_quantile: float = 0.0  # 截断训练: 仅用 Top/Bottom q 样本 (0=全量, 0.3=Top30%+Bot30%)
    size_neutral_label: bool = False  # R132 B3: 目标变量去市值化 (label = residual of fwd_return ~ log(circ_mv))
    seed: int = 42

    # --- DoubleEnsemble 专有 ---
    de_num_models: int = 6
    de_enable_sr: bool = True
    de_enable_fs: bool = True
    de_epochs: int = 100
    de_early_stopping: int = 50

    # --- XGBoost 专有 ---
    xgb_n_estimators: int = 300
    xgb_max_depth: int = 6
    xgb_eta: float = 0.05
    xgb_subsample: float = 0.8
    xgb_colsample_bytree: float = 0.8
    xgb_reg_alpha: float = 0.1
    xgb_reg_lambda: float = 1.0

    # --- ElasticNet 专有 ---
    en_alpha: float = 0.01
    en_l1_ratio: float = 0.5
    en_max_iter: int = 1000

    # --- GRU 专有 ---
    gru_hidden_dim: int = 128
    gru_n_layers: int = 2
    gru_dropout: float = 0.2
    gru_seq_len: int = 20
    gru_lr: float = 1e-3
    gru_epochs: int = 80
    gru_patience: int = 10
    gru_batch_size: int = 2048
    gru_weight_decay: float = 1e-4
    gru_bidirectional: bool = False

    # --- TRA 专有 ---
    tra_num_states: int = 3
    tra_seq_len: int = 20
    tra_epochs: int = 100
    tra_patience: int = 15
    tra_pretrain_epochs: int = 20
    tra_transport_method: str = "router"  # "router"|"optimal_transport"

    # --- MASTER 专有 ---
    master_d_model: int = 256
    master_t_nhead: int = 4
    master_s_nhead: int = 2
    master_dropout: float = 0.5
    master_beta: float = 5.0
    master_seq_len: int = 8
    master_lr: float = 1e-4
    master_epochs: int = 30
    master_patience: int = 5
    master_batch_size: int = 2048

    # --- HIST 专有 ---
    hist_hidden_size: int = 128
    hist_num_gru_layers: int = 2
    hist_num_concepts: int = 64
    hist_num_hidden_concepts: int = 32
    hist_dropout: float = 0.3
    hist_seq_len: int = 20
    hist_lr: float = 1e-3
    hist_epochs: int = 50
    hist_patience: int = 8
    hist_batch_size: int = 2048
    hist_concept_matrix_path: str = ""

    # --- GAT 专有 ---
    gat_hidden_dim: int = 64
    gat_n_heads: int = 4
    gat_n_layers: int = 2
    gat_dropout: float = 0.3
    gat_lr: float = 1e-3
    gat_epochs: int = 100
    gat_patience: int = 10
    gat_batch_size: int = 512

    # --- 增量训练 (Warm-start between WF folds) ---
    enable_incremental: bool = False             # 启用 fold 间增量训练
    incremental_n_estimators: int = 100          # 增量训练额外 boosting 轮数
    incremental_psi_threshold: float = 0.20      # PSI 超阈值则放弃增量，从头训练

    # --- SHAP 特征诊断 (§短板1: 集成到训练管线) ---
    enable_shap: bool = False            # 训练后自动运行 TreeSHAP 特征诊断
    shap_max_samples: int = 5000         # SHAP 采样上限 (速度控制)

    # --- Meta-Labeling (§短板1: 二级模型过滤假信号) ---
    enable_meta_labeling: bool = False   # 在 OOS 预测后叠加 meta-labeling 过滤
    meta_threshold: float = 0.5          # meta-label 概率阈值 (低于此值降权)

    # --- 分数阶差分 (§短板1: 长记忆保留) ---
    enable_frac_diff: bool = False       # 对特征做分数阶差分预处理
    frac_diff_d: float = 0.5             # 差分阶 (典型 0.3-0.7)
    frac_diff_columns: list[str] = field(default_factory=list)  # 空=全部数值列

    # --- 选股 ---
    top_k: int = 50
    min_samples_per_date: int = 100


class MLStockSelector:
    """Walk-Forward LightGBM 股票截面选择器。"""

    def __init__(self, config: MLSelectorConfig | None = None) -> None:
        self._config = config or MLSelectorConfig()
        self._validate_model_type(
            self._config.model_type,
            allow_experimental=self._config.allow_experimental_models is True,
        )
        self._models: list[Any] = []
        self._wf_results: list[dict[str, Any]] = []
        self._feature_importance: dict[str, float] = {}
        self._shap_importance: dict[str, float] = {}
        self._shap_diag: Any = None  # SHAPFeatureDiagnostics (lazy)
        self._full_df: pd.DataFrame | None = None  # for GRU temporal lookback

    @property
    def config(self) -> MLSelectorConfig:
        return self._config

    @staticmethod
    def _validate_model_type(model_type: str, *, allow_experimental: bool = False) -> None:
        canonical = {"lightgbm", "elasticnet"}
        experimental = {"double_ensemble", "xgboost", "gru", "tra", "master", "gat", "hist"}
        if model_type not in canonical | experimental:
            raise ValueError(f"unknown ML model type: {model_type}")
        if model_type in experimental and not allow_experimental:
            raise ValueError(
                "experimental ML model is disabled; use ml_ranking for lightgbm/linear "
                "or explicitly set allow_experimental_models=True for a legacy backtest"
            )

    @property
    def n_folds(self) -> int:
        return len(self._models)

    # ------------------------------------------------------------------
    # 数据准备
    # ------------------------------------------------------------------

    def prepare_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算前瞻收益标签并添加截面排名。

        支持两种标签模式：
        - "rank"（默认）：简单 N 日收益率截面排名
        - "triple_barrier"：López de Prado Triple-Barrier 标签

        Args:
            df: 必须含 datetime, symbol, close 列。

        Returns:
            添加了 fwd_return / fwd_return_rank 列的 DataFrame。
            当 label_type="triple_barrier" 时，额外添加 tb_label / tb_return / tb_holding_days。
        """
        cfg = self._config
        df.sort_values(["symbol", "datetime"], inplace=True)
        df.reset_index(drop=True, inplace=True)

        # --- Triple-Barrier 标签路径 ---
        if cfg.label_type == "triple_barrier":
            from aipha.models.label_eng import TripleBarrierConfig, TripleBarrierLabeler

            tb_cfg = TripleBarrierConfig(
                upper_bps=cfg.tb_upper_bps,
                lower_bps=cfg.tb_lower_bps,
                max_holding_days=cfg.forward_days,
                dynamic_barrier=cfg.tb_dynamic,
                vol_lookback=cfg.tb_vol_lookback,
                vol_multiplier_upper=cfg.tb_vol_multiplier,
                vol_multiplier_lower=cfg.tb_vol_multiplier,
                price_col=cfg.label_price_field,
                label_mode="ternary_rank",  # 排名化以兼容 LambdaRank
                skip_days=cfg.label_skip_days,
            )
            labeler = TripleBarrierLabeler(tb_cfg)
            df = labeler.compute_labels(df)
            # tb_label (0~1 rank) 作为 fwd_return_rank 的替代
            df["fwd_return_rank"] = df["tb_label"]
            # 也计算简单前瞻收益（用于诊断和归因）
            price_col = cfg.label_price_field if cfg.label_price_field in df.columns else "close"
            skip = cfg.label_skip_days
            fwd = cfg.forward_days
            if "fwd_return" not in df.columns:
                df["fwd_return"] = df.groupby("symbol")[price_col].transform(
                    lambda s: s.shift(-(skip + fwd)) / s.shift(-skip) - 1.0
                )
            logger.info(
                "Triple-Barrier labels prepared: %d samples, label_col=fwd_return_rank",
                len(df),
            )
            return df

        # --- 原始 rank 标签路径（默认）---
        # 前瞻 N 日收益
        # label_skip_days > 0 时，跳过信号日后 N 天再开始收益窗口
        # 用于对齐 T+1 执行：skip=1 → close(D+1)→close(D+6) 或 open(D+1)→open(D+6)
        price_col = cfg.label_price_field
        if price_col not in df.columns:
            logger.warning(
                "label_price_field=%s 不在数据中，回退到 close", price_col,
            )
            price_col = "close"
        skip = cfg.label_skip_days
        fwd = cfg.forward_days
        df["fwd_return"] = df.groupby("symbol")[price_col].transform(
            lambda s: s.shift(-(skip + fwd)) / s.shift(-skip) - 1.0
        )
        df.dropna(subset=["fwd_return"], inplace=True)
        # 截面排名 (0~1), 每个日期内排名
        # --- R132 B3: 目标变量去市值化 ---
        if cfg.size_neutral_label and "circ_mv" in df.columns:
            logger.info("Applying size-neutral label: regressing fwd_return ~ log(circ_mv) per date")
            log_mv = np.log1p(df["circ_mv"].clip(lower=0))
            df["fwd_return_raw"] = df["fwd_return"].copy()

            def _neutralize_group(g: pd.DataFrame) -> pd.Series:
                y = g["fwd_return"].values
                x = np.log1p(g["circ_mv"].clip(lower=0)).values
                mask = np.isfinite(y) & np.isfinite(x)
                if mask.sum() < 10:
                    return pd.Series(y, index=g.index)
                x_m, y_m = x[mask], y[mask]
                # OLS: y = a + b*x + residual
                x_mat = np.column_stack([np.ones(len(x_m)), x_m])
                try:
                    beta = np.linalg.lstsq(x_mat, y_m, rcond=None)[0]
                except np.linalg.LinAlgError:
                    return pd.Series(y, index=g.index)
                residual = np.full(len(y), np.nan)
                residual[mask] = y_m - x_mat @ beta
                return pd.Series(residual, index=g.index)

            df["fwd_return"] = df.groupby("datetime", group_keys=False).apply(
                _neutralize_group
            )
            df.drop(columns=["fwd_return_raw"], inplace=True, errors="ignore")
            df.dropna(subset=["fwd_return"], inplace=True)
            n_neutral = len(df)
            logger.info("Size-neutral labels computed: %d samples", n_neutral)

        df["fwd_return_rank"] = df.groupby("datetime")["fwd_return"].rank(
            method="average", pct=True
        )
        return df

    def _generate_wf_splits(
        self, dates: list[Any],
    ) -> list[tuple[list[Any], list[Any]]]:
        """生成 Walk-Forward 时间分割。

        Returns:
            List of (train_dates, test_dates) tuples.
        """
        cfg = self._config
        dates_sorted = sorted(dates)
        n = len(dates_sorted)
        # 估算每年/每月交易日数
        td_per_year = TRADING_DAYS_PER_YEAR
        td_per_month = 20
        train_len = cfg.train_years * td_per_year
        test_len = cfg.predict_months * td_per_month
        # Include the delayed entry as well as the holding horizon. A long
        # label_skip_days must never let the final training label reach OOS.
        purge = max(cfg.purge_days, cfg.forward_days + 5, cfg.forward_days + cfg.label_skip_days)

        splits: list[tuple[list[Any], list[Any]]] = []
        start = 0
        while start + train_len + purge + test_len <= n:
            train_end = start + train_len
            test_start = train_end + purge
            test_end = min(test_start + test_len, n)
            train_dates = dates_sorted[start:train_end]
            test_dates = dates_sorted[test_start:test_end]
            if len(test_dates) > 0:
                splits.append((train_dates, test_dates))
            # 滚动步长 = 预测窗口长度（而非训练窗口），确保产生足够多的 WF 折
            start += test_len

        # 不完整末窗：恢复尾部 OOS 数据，避免丢弃最近数月的预测
        if start + train_len + purge < n:
            train_end = start + train_len
            test_start = train_end + purge
            test_end = n
            tail_test = dates_sorted[test_start:test_end]
            if len(tail_test) > 0:
                splits.append(
                    (dates_sorted[start:train_end], tail_test)
                )

        return splits

    # ------------------------------------------------------------------
    # Walk-Forward 训练
    # ------------------------------------------------------------------

    def walk_forward_train(self, df: pd.DataFrame) -> pd.DataFrame:
        """Walk-Forward 滚动训练并生成 OOS 预测。

        Args:
            df: 含 datetime, symbol, feature_cols, label_col 的面板数据。

        Returns:
            OOS 预测结果 DataFrame (datetime, symbol, pred_score)。
        """
        cfg = self._config
        if cfg.label_col not in df.columns:
            df = self.prepare_labels(df)

        # Store full df for temporal model lookback (GRU/TRA/MASTER/HIST)
        if cfg.model_type in ("gru", "tra", "master", "hist"):
            self._full_df = df

        dates = sorted(df["datetime"].unique())
        splits = self._generate_wf_splits(dates)
        if not splits:
            raise ValueError(
                f"数据不足以生成 WF 分割: {len(dates)} 日, "
                f"需 {cfg.train_years}Y+{cfg.predict_months}M"
            )

        feature_cols = self._resolve_features(df)

        # --- 分数阶差分预处理 (§短板1: 长记忆保留) ---
        if cfg.enable_frac_diff:
            df = self._apply_frac_diff(df, feature_cols)
            # FracDiff 新增列追加到特征列
            frac_cols = [c for c in df.columns if c.endswith("_fd") and c not in feature_cols]
            feature_cols = feature_cols + frac_cols
            logger.info("FracDiff: added %d fractionally differenced features", len(frac_cols))

        logger.info(
            "WF training: %d folds, %d features, %d dates",
            len(splits), len(feature_cols), len(dates),
        )

        # Pre-build feature store for temporal models (shared across all folds)
        _gru_store = None
        if cfg.model_type in ("gru", "tra", "master", "hist"):
            from aipha.models.gru_ranker import build_feature_store
            logger.info("Building temporal feature store (once) for %s...", cfg.model_type)
            _gru_store = build_feature_store(df, feature_cols)
            logger.info("Feature store: %d symbols", len(_gru_store[0]))

        all_oos: list[pd.DataFrame] = []
        self._models.clear()
        self._wf_results.clear()

        # --- 增量训练状态 ---
        prev_model: Any = None
        prev_train_features: dict[str, list[float]] | None = None
        use_incremental = (
            cfg.enable_incremental
            and cfg.model_type == "lightgbm"
        )
        if use_incremental:
            from aipha.monitoring.psi_calculator import PSICalculator
            psi_calc = PSICalculator(
                psi_threshold=cfg.incremental_psi_threshold,
            )

        for fold_idx, (train_dates, test_dates) in enumerate(splits):
            train_mask = df["datetime"].isin(set(train_dates))
            test_mask = df["datetime"].isin(set(test_dates))
            train_df = df.loc[train_mask]
            test_df = df.loc[test_mask]

            # --- 决定是否使用增量训练 ---
            fold_init_model: Any = None
            cur_features: dict[str, list[float]] | None = None
            if use_incremental and prev_model is not None:
                cur_features = self._extract_feature_dist(
                    train_df, feature_cols,
                )
                drift = psi_calc.compute(
                    prev_train_features, cur_features,  # type: ignore[arg-type]
                )
                if drift.has_significant_drift:
                    logger.warning(
                        "Fold %d: PSI drift detected (%.4f, %d drifted) "
                        "→ training from scratch",
                        fold_idx, drift.overall_psi, drift.n_drifted,
                    )
                else:
                    fold_init_model = prev_model
                    logger.info(
                        "Fold %d: PSI stable (%.4f) → incremental training",
                        fold_idx, drift.overall_psi,
                    )

            model = self._fit_one_fold(
                train_df, feature_cols, fold_idx,
                _gru_store=_gru_store,
                init_model=fold_init_model,
            )
            self._models.append(model)

            # 保存当前 fold 状态供下一 fold 使用
            if use_incremental:
                prev_model = model
                prev_train_features = (
                    cur_features
                    if cur_features is not None
                    else self._extract_feature_dist(train_df, feature_cols)
                )

            oos_pred = self._predict_one_fold(
                model, test_df, feature_cols, fold_idx,
                _gru_store=_gru_store,
            )
            all_oos.append(oos_pred)
            purge_dates = [day for day in dates if train_dates[-1] < day < test_dates[0]]
            self._wf_results[-1].update({
                "train_dates": [pd.Timestamp(day).date().isoformat() for day in train_dates],
                "train_start": str(train_df["datetime"].min()),
                "train_end": str(train_df["datetime"].max()),
                "train_samples": len(train_df),
                "purge_start": str(purge_dates[0]) if purge_dates else None,
                "purge_end": str(purge_dates[-1]) if purge_dates else None,
                "purge_sessions": len(purge_dates),
            })

            # IS evaluation: predict on training data for decay measurement
            # (called after _predict_one_fold so fold_info is in _wf_results)
            self._evaluate_is_fold(
                model, train_df, feature_cols, fold_idx,
                _gru_store=_gru_store,
            )

            # --- SHAP per-fold diagnostics (§短板1: SHAP 特征诊断) ---
            if cfg.enable_shap and cfg.model_type in ("lightgbm", "xgboost"):
                self._compute_fold_shap(model, test_df, feature_cols, fold_idx)

        oos_df = pd.concat(all_oos, ignore_index=True)
        self._compute_aggregate_importance(feature_cols)

        # --- SHAP 汇总报告 ---
        if cfg.enable_shap and self._shap_diag is not None:
            try:
                shap_report = self._shap_diag.get_aggregate_importance()
                self._shap_importance = shap_report["mean_abs_shap"].to_dict()
                logger.info("SHAP importance (top 10): %s",
                            dict(list(self._shap_importance.items())[:10]))
            except Exception:
                logger.warning("SHAP aggregation failed", exc_info=True)

        # --- Meta-Labeling 后处理 (§短板1: 二级模型过滤假信号) ---
        if cfg.enable_meta_labeling:
            oos_df = self._apply_meta_labeling(oos_df, df, feature_cols)

        return oos_df

    def predict(self, df: pd.DataFrame) -> pd.Series:
        """使用最新模型预测截面分数。

        Args:
            df: 含 feature_cols 的 DataFrame，index 或列含 symbol。

        Returns:
            pd.Series, index=symbol, values=pred_score。
        """
        if not self._models:
            raise RuntimeError("No model available. Call walk_forward_train first.")
        verified = getattr(self, "_verified_feature_columns", None)
        if verified is not None:
            missing = [column for column in verified if column not in df]
            if missing:
                raise ValueError(f"verified model input is missing feature columns: {missing}")
        model = self._models[-1]  # 最新折模型
        feature_cols = self._resolve_features(df)
        X = df[feature_cols].fillna(0.0).values
        scores = model.predict(X)
        if "symbol" in df.columns:
            return pd.Series(scores, index=df["symbol"].values, name="pred_score")
        return pd.Series(scores, index=df.index, name="pred_score")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_features(self, df: pd.DataFrame) -> list[str]:
        """确定实际可用的特征列。

        优先级:
        1. config.feature_cols (显式指定)
        2. 模型自身的 feature_name() (LightGBM Booster 保存的训练特征)
        3. 自动检测 (排除已知非特征列)
        """
        cfg = self._config
        if cfg.feature_cols:
            return [c for c in cfg.feature_cols if c in df.columns]

        # 从最新折模型获取训练时的特征名 (LightGBM Booster)
        if self._models:
            model = self._models[-1]
            try:
                import lightgbm as lgb
                booster = model if isinstance(model, lgb.Booster) else None
                if booster is not None:
                    model_features = booster.feature_name()
                    matched = [c for c in model_features if c in df.columns]
                    if matched:
                        logger.debug(
                            "Using model feature_name: %d/%d matched",
                            len(matched), len(model_features),
                        )
                        return matched
            except Exception:
                pass  # 非 LGB 模型或无 feature_name, 降级到自动检测

        # 自动检测: 排除非特征列
        exclude = {
            "datetime", "symbol", "date", "trade_date",
            "fwd_return", "fwd_return_rank", "fwd_return_raw", cfg.label_col,
            "open", "high", "low", "close", "volume", "amount",
            "vol", "pre_close", "change", "pct_chg",
            # 辅助列 (非数值型或非特征)
            "industry", "sector", "sw_industry",
        }
        # 安全: 仅保留数值类型列
        return [
            c for c in df.columns
            if c not in exclude and df[c].dtype.kind in ("f", "i", "u")
        ]

    @staticmethod
    def _extract_feature_dist(
        df: pd.DataFrame,
        feature_cols: list[str],
        max_samples: int = 50_000,
    ) -> dict[str, list[float]]:
        """提取特征分布用于 PSI 计算（采样以控制内存）。"""
        if len(df) > max_samples:
            sample = df[feature_cols].sample(
                n=max_samples, random_state=42,
            )
        else:
            sample = df[feature_cols]
        return {
            col: sample[col].dropna().tolist()
            for col in feature_cols
        }

    def _fit_one_fold(
        self,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
        init_model: Any | None = None,
    ) -> Any:
        """训练单折模型 (LightGBM 单模型或 DoubleEnsemble)。

        支持截断训练 (truncate_quantile > 0):
        仅保留截面排名 Top-q% 和 Bottom-q% 的样本，
        丢弃中间段，让梯度集中在尾部区分。
        理论依据: MSE 优化中 97.5% 梯度浪费在对组合无贡献的中间排名段。
        """
        cfg = self._config

        # --- 截断训练: 过滤中间排名段 ---
        if cfg.truncate_quantile > 0:
            q = cfg.truncate_quantile
            rank_col = cfg.label_col  # fwd_return_rank, 已是 0~1
            mask = (train_df[rank_col] <= q) | (train_df[rank_col] >= (1.0 - q))
            n_before = len(train_df)
            train_df = train_df.loc[mask].copy()
            n_after = len(train_df)
            logger.info(
                "Fold %d truncated training: keep Top/Bot %.0f%%, "
                "%d -> %d samples (%.1f%% retained)",
                fold_idx, q * 100, n_before, n_after,
                n_after / n_before * 100 if n_before > 0 else 0,
            )

        X = train_df[feature_cols].fillna(0.0).values
        y = train_df[cfg.label_col].values

        if cfg.model_type == "double_ensemble":
            return self._fit_double_ensemble(
                X, y, feature_cols, train_df, fold_idx,
            )
        if cfg.model_type == "xgboost":
            return self._fit_xgboost(
                X, y, feature_cols, train_df, fold_idx,
            )
        if cfg.model_type == "elasticnet":
            return self._fit_elasticnet(
                X, y, feature_cols, train_df, fold_idx,
            )
        if cfg.model_type == "gru":
            return self._fit_gru(train_df, feature_cols, fold_idx, _gru_store)
        if cfg.model_type == "tra":
            return self._fit_tra(train_df, feature_cols, fold_idx, _gru_store)
        if cfg.model_type == "master":
            return self._fit_master(train_df, feature_cols, fold_idx, _gru_store)
        if cfg.model_type == "hist":
            return self._fit_hist(train_df, feature_cols, fold_idx, _gru_store)
        if cfg.model_type == "gat":
            return self._fit_gat(X, y, feature_cols, train_df, fold_idx)
        # 默认: 单 LightGBM
        return self._fit_lightgbm(
            X, y, feature_cols, train_df, fold_idx,
            init_model=init_model,
        )

    def _fit_lightgbm(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_cols: list[str],
        train_df: pd.DataFrame,
        fold_idx: int,
        init_model: Any | None = None,
    ) -> Any:
        """训练单折 LightGBM 模型。"""
        import lightgbm as lgb

        cfg = self._config
        # 增量训练时使用更少的 boosting 轮数
        n_rounds = (
            cfg.incremental_n_estimators
            if init_model is not None
            else cfg.n_estimators
        )
        if cfg.objective == "lambdarank":
            groups = train_df.groupby("datetime").size().values
            n_bins = cfg.n_rank_bins
            y_binned = np.clip(
                (y * n_bins).astype(int), 0, n_bins - 1,
            )
            train_set = lgb.Dataset(
                X, label=y_binned, group=groups,
                feature_name=feature_cols,
            )
            params = self._get_lgb_params_rank()
            model = lgb.train(
                params, train_set,
                num_boost_round=n_rounds,
                init_model=init_model,
            )
        else:
            train_set = lgb.Dataset(
                X, label=y, feature_name=feature_cols,
            )
            params = self._get_lgb_params_reg()
            model = lgb.train(
                params, train_set,
                num_boost_round=n_rounds,
                init_model=init_model,
            )
        mode_str = "incremental" if init_model is not None else "full"
        logger.info(
            "Fold %d LightGBM [%s]: %d samples, %d features, %d rounds",
            fold_idx, mode_str, len(train_df), len(feature_cols), n_rounds,
        )
        return model

    def _fit_double_ensemble(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_cols: list[str],
        train_df: pd.DataFrame,
        fold_idx: int,
    ) -> Any:
        """训练单折 DoubleEnsemble 模型。"""
        from aipha.models.double_ensemble import (
            DEnsembleConfig,
            DoubleEnsembleModel,
        )

        cfg = self._config
        # 切分训练集最后 20% 为验证集
        dates = sorted(train_df["datetime"].unique())
        val_cut = int(len(dates) * 0.8)
        val_dates = set(dates[val_cut:])
        val_mask = train_df["datetime"].isin(val_dates)
        X_val = X[val_mask.values]
        y_val = y[val_mask.values]
        X_tr = X[~val_mask.values]
        y_tr = y[~val_mask.values]

        de_cfg = DEnsembleConfig(
            num_models=cfg.de_num_models,
            enable_sr=cfg.de_enable_sr,
            enable_fs=cfg.de_enable_fs,
            epochs=cfg.de_epochs,
            early_stopping_rounds=cfg.de_early_stopping,
            learning_rate=cfg.learning_rate,
            max_depth=cfg.max_depth,
            num_leaves=cfg.num_leaves,
            subsample=cfg.subsample,
            colsample_bytree=cfg.colsample_bytree,
            reg_alpha=cfg.reg_alpha,
            reg_lambda=cfg.reg_lambda,
            seed=cfg.seed,
        )
        model = DoubleEnsembleModel(de_cfg)
        model.fit(X_tr, y_tr, X_val, y_val, feature_names=feature_cols)
        logger.info(
            "Fold %d DoubleEnsemble: %d train + %d valid, "
            "%d features, %d sub-models",
            fold_idx, len(X_tr), len(X_val),
            len(feature_cols), cfg.de_num_models,
        )
        return model

    def _fit_xgboost(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_cols: list[str],
        train_df: pd.DataFrame,
        fold_idx: int,
    ) -> Any:
        """训练单折 XGBoost 模型。"""
        import xgboost as xgb

        cfg = self._config
        if cfg.objective == "lambdarank":
            groups = train_df.groupby("datetime").size().values
            n_bins = cfg.n_rank_bins
            y_binned = np.clip(
                (y * n_bins).astype(int), 0, n_bins - 1,
            )
            dtrain = xgb.DMatrix(
                X, label=y_binned, feature_names=feature_cols,
            )
            dtrain.set_group(groups)
            params = {
                "objective": "rank:ndcg",
                "eta": cfg.xgb_eta,
                "max_depth": cfg.xgb_max_depth,
                "subsample": cfg.xgb_subsample,
                "colsample_bytree": cfg.xgb_colsample_bytree,
                "reg_alpha": cfg.xgb_reg_alpha,
                "reg_lambda": cfg.xgb_reg_lambda,
                "seed": cfg.seed,
                "verbosity": 0,
            }
            model = xgb.train(
                params, dtrain,
                num_boost_round=cfg.xgb_n_estimators,
            )
        else:
            dtrain = xgb.DMatrix(
                X, label=y, feature_names=feature_cols,
            )
            params = {
                "objective": "reg:squarederror",
                "eta": cfg.xgb_eta,
                "max_depth": cfg.xgb_max_depth,
                "subsample": cfg.xgb_subsample,
                "colsample_bytree": cfg.xgb_colsample_bytree,
                "reg_alpha": cfg.xgb_reg_alpha,
                "reg_lambda": cfg.xgb_reg_lambda,
                "seed": cfg.seed,
                "verbosity": 0,
            }
            model = xgb.train(
                params, dtrain,
                num_boost_round=cfg.xgb_n_estimators,
            )
        logger.info(
            "Fold %d XGBoost: %d samples, %d features",
            fold_idx, len(train_df), len(feature_cols),
        )
        return _XGBoostWrapper(model, feature_cols)

    def _fit_elasticnet(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_cols: list[str],
        train_df: pd.DataFrame,
        fold_idx: int,
    ) -> Any:
        """训练单折 ElasticNet 模型。"""
        from sklearn.linear_model import ElasticNet
        from sklearn.preprocessing import StandardScaler

        cfg = self._config
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        model = ElasticNet(
            alpha=cfg.en_alpha,
            l1_ratio=cfg.en_l1_ratio,
            max_iter=cfg.en_max_iter,
            random_state=cfg.seed,
        )
        model.fit(X_scaled, y)
        # 包装为带 scaler 的可预测对象
        wrapper = _ElasticNetWrapper(model, scaler, feature_cols)
        logger.info(
            "Fold %d ElasticNet: %d samples, %d features, "
            "alpha=%.4f, l1_ratio=%.2f",
            fold_idx, len(train_df), len(feature_cols),
            cfg.en_alpha, cfg.en_l1_ratio,
        )
        return wrapper

    def _fit_gru(
        self,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> Any:
        """训练单折 GRU 时序模型。"""
        from aipha.models.gru_ranker import GRUConfig, train_gru_one_fold

        cfg = self._config
        gru_cfg = GRUConfig(
            hidden_dim=cfg.gru_hidden_dim,
            n_layers=cfg.gru_n_layers,
            dropout=cfg.gru_dropout,
            seq_len=cfg.gru_seq_len,
            lr=cfg.gru_lr,
            epochs=cfg.gru_epochs,
            patience=cfg.gru_patience,
            batch_size=cfg.gru_batch_size,
            weight_decay=cfg.gru_weight_decay,
            bidirectional=cfg.gru_bidirectional,
        )
        wrapper = train_gru_one_fold(
            train_df=train_df,
            feature_cols=feature_cols,
            label_col=cfg.label_col,
            cfg=gru_cfg,
            full_df=self._full_df,
            fold_idx=fold_idx,
            prebuilt_store=_gru_store,
        )
        logger.info(
            "Fold %d GRU: %d samples, %d features, seq=%d, hidden=%d",
            fold_idx, len(train_df), len(feature_cols),
            cfg.gru_seq_len, cfg.gru_hidden_dim,
        )
        return wrapper

    def _fit_tra(
        self,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> Any:
        """训练单折 TRA 时序路由模型。"""
        from aipha.models.tra_model import TRAConfig, train_tra_one_fold

        cfg = self._config
        tra_cfg = TRAConfig(
            num_states=cfg.tra_num_states,
            seq_len=cfg.tra_seq_len,
            epochs=cfg.tra_epochs,
            patience=cfg.tra_patience,
            pretrain_epochs=cfg.tra_pretrain_epochs,
            transport_method=cfg.tra_transport_method,
        )
        wrapper = train_tra_one_fold(
            train_df=train_df,
            feature_cols=feature_cols,
            label_col=cfg.label_col,
            cfg=tra_cfg,
            full_df=self._full_df,
            fold_idx=fold_idx,
            prebuilt_store=_gru_store,
        )
        logger.info(
            "Fold %d TRA: %d samples, %d features, states=%d, seq=%d",
            fold_idx, len(train_df), len(feature_cols),
            cfg.tra_num_states, cfg.tra_seq_len,
        )
        return wrapper

    def _fit_master(
        self,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> Any:
        """训练单折 MASTER 模型。"""
        from aipha.models.master_model import MASTERConfig, train_master_one_fold

        cfg = self._config
        master_cfg = MASTERConfig(
            d_model=cfg.master_d_model,
            t_nhead=cfg.master_t_nhead,
            s_nhead=cfg.master_s_nhead,
            dropout=cfg.master_dropout,
            beta=cfg.master_beta,
            seq_len=cfg.master_seq_len,
            lr=cfg.master_lr,
            epochs=cfg.master_epochs,
            patience=cfg.master_patience,
            batch_size=cfg.master_batch_size,
        )
        wrapper = train_master_one_fold(
            train_df=train_df,
            feature_cols=feature_cols,
            label_col=cfg.label_col,
            cfg=master_cfg,
            full_df=self._full_df,
            fold_idx=fold_idx,
            prebuilt_store=_gru_store,
        )
        logger.info(
            "Fold %d MASTER: %d samples, %d features, d_model=%d, seq=%d",
            fold_idx, len(train_df), len(feature_cols),
            cfg.master_d_model, cfg.master_seq_len,
        )
        return wrapper

    def _fit_hist(
        self,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> Any:
        """训练单折 HIST 概念图谱模型。"""
        from aipha.models.hist_model import HISTConfig, train_hist_one_fold

        cfg = self._config
        hist_cfg = HISTConfig(
            d_feat=len(feature_cols),
            hidden_size=cfg.hist_hidden_size,
            num_gru_layers=cfg.hist_num_gru_layers,
            num_concepts=cfg.hist_num_concepts,
            num_hidden_concepts=cfg.hist_num_hidden_concepts,
            dropout=cfg.hist_dropout,
            seq_len=cfg.hist_seq_len,
            lr=cfg.hist_lr,
            epochs=cfg.hist_epochs,
            patience=cfg.hist_patience,
            batch_size=cfg.hist_batch_size,
        )
        wrapper = train_hist_one_fold(
            train_df=train_df,
            feature_cols=feature_cols,
            label_col=cfg.label_col,
            cfg=hist_cfg,
            full_df=self._full_df,
            fold_idx=fold_idx,
            prebuilt_store=_gru_store,
            concept_matrix_path=cfg.hist_concept_matrix_path or None,
        )
        logger.info(
            "Fold %d HIST: %d samples, %d features, hidden=%d, seq=%d, concepts=%d",
            fold_idx, len(train_df), len(feature_cols),
            cfg.hist_hidden_size, cfg.hist_seq_len, cfg.hist_num_concepts,
        )
        return wrapper

    def _fit_gat(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_cols: list[str],
        train_df: pd.DataFrame,
        fold_idx: int,
    ) -> Any:
        """训练单折 GAT 图注意力模型。"""
        from aipha.models.gat_ranker import GATFactorRanker, GATRankerConfig

        cfg = self._config
        gat_cfg = GATRankerConfig(
            input_dim=len(feature_cols),
            hidden_dim=cfg.gat_hidden_dim,
            n_heads=cfg.gat_n_heads,
            n_layers=cfg.gat_n_layers,
            dropout=cfg.gat_dropout,
            learning_rate=cfg.gat_lr,
            epochs=cfg.gat_epochs,
            patience=cfg.gat_patience,
            batch_size=cfg.gat_batch_size,
            label_col=cfg.label_col,
            date_col="datetime",  # align with WF pipeline convention
            code_col="symbol",    # align with WF pipeline convention
        )
        ranker = GATFactorRanker(config=gat_cfg)
        # GAT uses its own fit() with DataFrame (needs industry graph)
        ranker.fit(train_df, label_col=cfg.label_col)
        ranker._predict_df = True  # marker for _predict_one_fold dispatch
        logger.info(
            "Fold %d GAT: %d samples, %d features, heads=%d, layers=%d",
            fold_idx, len(train_df), len(feature_cols),
            cfg.gat_n_heads, cfg.gat_n_layers,
        )
        return ranker

    def get_feature_importance(self) -> dict[str, float]:
        """获取特征重要性。"""
        return dict(self._feature_importance)

    def get_shap_importance(self) -> dict[str, float]:
        """获取 SHAP 特征重要性（需先启用 enable_shap 并完成训练）。"""
        return dict(self._shap_importance)

    # ------------------------------------------------------------------
    # SHAP / MetaLabeler / FracDiff 集成方法
    # ------------------------------------------------------------------

    def _compute_fold_shap(
        self, model: Any, test_df: pd.DataFrame,
        feature_cols: list[str], fold_idx: int,
    ) -> None:
        """对单折 OOS 数据计算 TreeSHAP 特征重要性。"""
        try:
            from aipha.models.feature_importance import SHAPFeatureDiagnostics, SHAPDiagConfig
        except ImportError:
            logger.warning("shap 未安装, 跳过 SHAP 诊断")
            return
        if self._shap_diag is None:
            self._shap_diag = SHAPFeatureDiagnostics(
                SHAPDiagConfig(max_samples=self._config.shap_max_samples),
            )
        X = test_df[feature_cols].fillna(0.0)
        dates = test_df["datetime"] if "datetime" in test_df.columns else None
        try:
            self._shap_diag.compute_fold_shap(model, X, fold_id=fold_idx, dates=dates)
        except Exception:
            logger.warning("SHAP fold %d 计算失败", fold_idx, exc_info=True)

    def _apply_meta_labeling(
        self, oos_df: pd.DataFrame, full_df: pd.DataFrame,
        feature_cols: list[str],
    ) -> pd.DataFrame:
        """使用 MetaLabeler 对 OOS 预测做二级过滤。

        二级模型预测主模型信号的正确概率，低概率信号被降权。
        """
        try:
            from aipha.models.meta_labeler import MetaLabeler, MetaLabelConfig
        except ImportError:
            logger.warning("meta_labeler 导入失败, 跳过 meta-labeling")
            return oos_df
        cfg = self._config
        try:
            ml_cfg = MetaLabelConfig(threshold=cfg.meta_threshold)
            meta = MetaLabeler(ml_cfg)
            # 构造主模型 side 信号: top_k → 1, else → 0
            if "pred_score" in oos_df.columns:
                oos_df["_meta_side"] = (
                    oos_df.groupby("datetime")["pred_score"]
                    .rank(ascending=False, pct=True)
                    .le(0.2)  # top 20% = buy signal
                    .astype(int)
                )
                meta_probs = meta.fit_predict(
                    oos_df, feature_cols, side_col="_meta_side",
                )
                # 低概率信号降权
                if meta_probs is not None and len(meta_probs) == len(oos_df):
                    scale = np.where(meta_probs >= cfg.meta_threshold, 1.0, meta_probs / cfg.meta_threshold)
                    oos_df["pred_score"] = oos_df["pred_score"] * scale
                    n_filtered = int((meta_probs < cfg.meta_threshold).sum())
                    logger.info("Meta-labeling: %d/%d 信号被降权", n_filtered, len(oos_df))
                oos_df.drop(columns=["_meta_side"], errors="ignore", inplace=True)
        except Exception:
            logger.warning("Meta-labeling 执行失败, 保持原始分数", exc_info=True)
        return oos_df

    def _apply_frac_diff(
        self, df: pd.DataFrame, feature_cols: list[str],
    ) -> pd.DataFrame:
        """对特征列做分数阶差分预处理。"""
        try:
            from aipha.models.feature_eng import FractionalDiffTransformer
        except ImportError:
            logger.warning("FractionalDiffTransformer 导入失败")
            return df
        cfg = self._config
        columns = cfg.frac_diff_columns or [
            c for c in feature_cols
            if df[c].dtype in (np.float64, np.float32, np.int64, np.int32, float, int)
        ]
        if not columns:
            return df
        fdt = FractionalDiffTransformer(
            d=cfg.frac_diff_d, columns=columns, suffix="_fd",
        )
        fdt.fit(df)
        df = fdt.transform(df)
        logger.info("FracDiff(d=%.2f): processed %d columns", cfg.frac_diff_d, len(columns))
        return df

    def _evaluate_is_fold(
        self,
        model: Any,
        train_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> None:
        """计算单折 IS (In-Sample) IC，用于 IS-OOS 衰减分析。"""
        if "fwd_return" not in train_df.columns:
            return
        # GRU/TRA/MASTER: use temporal prediction with prebuilt store
        if hasattr(model, "predict_temporal_fast") and _gru_store is not None:
            scores = model.predict_temporal_fast(
                train_df, feature_cols, _gru_store,
            )
        elif hasattr(model, "predict_temporal") and hasattr(self, "_full_df"):
            scores = model.predict_temporal(train_df, feature_cols, self._full_df)
        elif hasattr(model, "_predict_df"):
            # GAT expects DataFrame input
            scores = model.predict(train_df)
        else:
            X = train_df[feature_cols].fillna(0.0).values
            scores = model.predict(X)
        is_result = train_df[["datetime", "symbol"]].copy()
        is_result["pred_score"] = scores
        is_result["fwd_return"] = train_df["fwd_return"].values
        by_date = is_result.groupby("datetime").apply(
            lambda g: g["pred_score"].corr(
                g["fwd_return"], method="spearman",
            )
            if len(g) > 10 else np.nan,
            include_groups=False,
        )
        is_ic = float(by_date.mean())
        is_std = float(by_date.std())
        is_ir = float(is_ic / is_std) if is_std > 0 else 0.0
        # Attach IS metrics to the latest wf_result entry
        if self._wf_results and self._wf_results[-1]["fold"] == fold_idx:
            self._wf_results[-1]["is_mean_ic"] = is_ic
            self._wf_results[-1]["is_ic_std"] = is_std
            self._wf_results[-1]["is_ic_ir"] = is_ir
        else:
            self._is_fold_metrics = getattr(self, "_is_fold_metrics", [])
            self._is_fold_metrics.append({
                "fold": fold_idx,
                "is_mean_ic": is_ic,
                "is_ic_std": is_std,
                "is_ic_ir": is_ir,
            })
        logger.info(
            "Fold %d IS: IC=%.4f, ICIR=%.2f",
            fold_idx, is_ic, is_ir,
        )

    def _predict_one_fold(
        self,
        model: Any,
        test_df: pd.DataFrame,
        feature_cols: list[str],
        fold_idx: int,
        _gru_store: tuple | None = None,
    ) -> pd.DataFrame:
        """单折 OOS 预测并记录 fold 绩效。"""
        cfg = self._config
        # GRU/TRA/MASTER: use temporal prediction with prebuilt store
        if hasattr(model, "predict_temporal_fast") and _gru_store is not None:
            scores = model.predict_temporal_fast(
                test_df, feature_cols, _gru_store,
            )
        elif hasattr(model, "predict_temporal") and hasattr(self, "_full_df"):
            scores = model.predict_temporal(test_df, feature_cols, self._full_df)
        elif hasattr(model, "_predict_df"):
            # GAT: predict expects DataFrame
            scores = model.predict(test_df)
        else:
            X = test_df[feature_cols].fillna(0.0).values
            scores = model.predict(X)

        result = test_df[["datetime", "symbol"]].copy()
        result["pred_score"] = scores
        if "fwd_return" in test_df.columns:
            result["fwd_return"] = test_df["fwd_return"].values

        # 记录 fold 信息
        fold_info: dict[str, Any] = {
            "fold": fold_idx,
            "train_dates": None,
            "test_start": str(test_df["datetime"].min()),
            "test_end": str(test_df["datetime"].max()),
            "test_samples": len(test_df),
        }

        # 计算 OOS IC (rank correlation)
        if "fwd_return" in result.columns:
            by_date = result.groupby("datetime").apply(
                lambda g: g["pred_score"].corr(
                    g["fwd_return"], method="spearman",
                )
                if len(g) > 10 else np.nan,
                include_groups=False,
            )
            fold_info["mean_ic"] = float(by_date.mean())
            fold_info["ic_std"] = float(by_date.std())
            fold_info["ic_ir"] = (
                float(by_date.mean() / by_date.std())
                if by_date.std() > 0 else 0.0
            )
        self._wf_results.append(fold_info)
        logger.info(
            "Fold %d OOS: %d samples, IC=%.4f",
            fold_idx, len(test_df),
            fold_info.get("mean_ic", 0.0),
        )
        return result

    def get_wf_summary(self) -> dict[str, Any]:
        """获取 Walk-Forward 训练摘要（含 IS-OOS 衰减分析）。"""
        if not self._wf_results:
            return {"n_folds": 0}

        ics = np.asarray([r["mean_ic"] for r in self._wf_results if r.get("mean_ic") is not None], dtype=float)
        ics = ics[np.isfinite(ics)]

        # IS-OOS decay analysis
        is_ics = [r.get("is_mean_ic") for r in self._wf_results]
        oos_ics = [r.get("mean_ic") for r in self._wf_results]
        decay_per_fold = []
        for is_ic, oos_ic in zip(is_ics, oos_ics):
            if is_ic is not None and np.isfinite(is_ic) and is_ic > 0 and oos_ic is not None and np.isfinite(oos_ic):
                decay_per_fold.append(
                    (is_ic - oos_ic) / is_ic
                )

        summary: dict[str, Any] = {
            "n_folds": len(self._wf_results),
            "mean_ic": float(ics.mean()) if len(ics) else None,
            "std_ic": float(ics.std()) if len(ics) else None,
            "valid_ic_folds": len(ics),
            "ic_ir": (
                float(ics.mean() / ics.std())
                if len(ics) and ics.std() > 0 else (0.0 if len(ics) else None)
            ),
            "folds": self._wf_results,
            "top_features": dict(
                sorted(
                    self._feature_importance.items(),
                    key=lambda x: x[1], reverse=True,
                )[:20]
            ),
        }

        # Add IS-OOS decay metrics if IS evaluation was performed
        if decay_per_fold:
            is_valid = [x for x in is_ics if x is not None and np.isfinite(x)]
            summary["is_mean_ic"] = float(np.nanmean(is_valid))
            summary["is_oos_decay_per_fold"] = decay_per_fold
            summary["is_oos_mean_decay"] = float(np.nanmean(decay_per_fold))
            summary["is_oos_max_decay"] = float(np.nanmax(decay_per_fold))

        return summary

    def save(self, path: Path) -> None:
        """保存所有折模型到目录。"""
        import joblib

        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        cfg = self._config
        for i, model in enumerate(self._models):
            if cfg.model_type in ("double_ensemble", "elasticnet"):
                joblib.dump(model, path / f"fold_{i}.pkl")
            elif cfg.model_type == "xgboost":
                model.booster.save_model(str(path / f"fold_{i}.json"))
                # 保存 feature_cols 供 load 时重建 wrapper
                joblib.dump(
                    model.feature_cols, path / f"fold_{i}_fcols.pkl",
                )
            elif cfg.model_type in ("gru", "master", "tra", "gat", "hist"):
                import torch
                state = model.model.state_dict() if hasattr(model, "model") else {}
                torch.save(state, path / f"fold_{i}.pt")
                meta_dl = {"cfg": getattr(model, "cfg", None)}
                if hasattr(model, "_feature_names"):
                    meta_dl["feature_names"] = model._feature_names
                joblib.dump(meta_dl, path / f"fold_{i}_dl_meta.pkl")
            else:
                model.save_model(str(path / f"fold_{i}.txt"))
        joblib.dump(
            {
                "config": self._config,
                "wf_results": self._wf_results,
                "feature_importance": self._feature_importance,
            },
            path / "meta.pkl",
        )
        logger.info("Saved %d fold models to %s", len(self._models), path)

    def load(self, path: Path, *, allow_experimental_models: bool = False) -> None:
        """加载模型。"""
        import joblib

        path = Path(path)
        meta = joblib.load(path / "meta.pkl")
        # A saved training opt-in is data, not the caller's permission to
        # load experiments into a new runtime. Validate before changing state.
        loaded_config = meta["config"]
        self._validate_model_type(
            loaded_config.model_type,
            allow_experimental=allow_experimental_models is True,
        )
        self._config = loaded_config
        self._config.allow_experimental_models = allow_experimental_models is True
        self._wf_results = meta["wf_results"]
        self._feature_importance = meta["feature_importance"]
        self._models.clear()
        mtype = self._config.model_type
        if mtype in ("double_ensemble", "elasticnet"):
            fold_files = _ordered_fold_files(path, ".pkl")
            for fp in fold_files:
                self._models.append(joblib.load(fp))
        elif mtype == "xgboost":
            import xgboost as xgb
            fold_files = _ordered_fold_files(path, ".json")
            for fp in fold_files:
                booster = xgb.Booster(model_file=str(fp))
                fcols_fp = fp.with_name(
                    fp.stem + "_fcols.pkl",
                )
                fcols = joblib.load(fcols_fp) if fcols_fp.exists() else []
                self._models.append(
                    _XGBoostWrapper(booster, fcols),
                )
        elif mtype == "gru":
            import torch
            from aipha.models.gru_ranker import GRUNetwork, _GRUWrapper
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            fold_files = _ordered_fold_files(path, ".pt")
            for fp in fold_files:
                meta_fp = fp.with_name(fp.stem + "_gru_meta.pkl")
                gru_meta = joblib.load(meta_fp)
                gru_cfg = gru_meta["cfg"]
                n_features = len(gru_meta["feature_names"])
                net = GRUNetwork(n_features, gru_cfg).to(device)
                state = torch.load(fp, map_location=device, weights_only=True)
                net.load_state_dict(state)
                wrapper = _GRUWrapper(net, gru_cfg, device)
                wrapper._feature_names = gru_meta["feature_names"]
                self._models.append(wrapper)
        else:
            import lightgbm as lgb
            fold_files = _ordered_fold_files(path, ".txt")
            for fp in fold_files:
                self._models.append(lgb.Booster(model_file=str(fp)))
        logger.info("Loaded %d fold models from %s", len(self._models), path)

    # ------------------------------------------------------------------
    # LightGBM 参数
    # ------------------------------------------------------------------

    def _get_lgb_params_rank(self) -> dict[str, Any]:
        """LambdaRank 参数。"""
        cfg = self._config
        return {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [10, 30, 50],
            "label_gain": ",".join(
                str(2**i - 1) for i in range(cfg.n_rank_bins + 1)
            ),
            "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth,
            "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample,
            "colsample_bytree": cfg.colsample_bytree,
            "reg_alpha": cfg.reg_alpha,
            "reg_lambda": cfg.reg_lambda,
            "verbose": -1,
            "seed": cfg.seed,
        }

    def _get_lgb_params_reg(self) -> dict[str, Any]:
        """回归参数。"""
        cfg = self._config
        return {
            "objective": "regression",
            "metric": "mse",
            "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth,
            "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample,
            "colsample_bytree": cfg.colsample_bytree,
            "reg_alpha": cfg.reg_alpha,
            "reg_lambda": cfg.reg_lambda,
            "verbose": -1,
            "seed": cfg.seed,
        }

    def _compute_aggregate_importance(
        self, feature_cols: list[str],
    ) -> None:
        """聚合所有折的特征重要性。"""
        if not self._models:
            return
        cfg = self._config
        if cfg.model_type == "double_ensemble":
            agg: dict[str, float] = {}
            for model in self._models:
                fimp = model.feature_importance()
                for k, v in fimp.items():
                    agg[k] = agg.get(k, 0.0) + v
            n = max(len(self._models), 1)
            self._feature_importance = {k: v / n for k, v in agg.items()}
        elif cfg.model_type == "elasticnet":
            agg_en: dict[str, float] = {}
            for model in self._models:
                fimp = model.feature_importance()
                for k, v in fimp.items():
                    agg_en[k] = agg_en.get(k, 0.0) + v
            n = max(len(self._models), 1)
            self._feature_importance = {
                k: v / n for k, v in agg_en.items()
            }
        elif cfg.model_type == "xgboost":
            agg_arr = np.zeros(len(feature_cols))
            for model in self._models:
                imp = model.feature_importance(
                    importance_type="gain",
                )
                agg_arr += imp
            agg_arr /= max(len(self._models), 1)
            self._feature_importance = {
                feature_cols[i]: float(agg_arr[i])
                for i in range(len(feature_cols))
            }
        elif cfg.model_type in ("gru", "master", "tra", "gat", "hist"):
            # DL models: no native tree-style feature importance
            self._feature_importance = {c: 0.0 for c in feature_cols}
        else:
            agg_arr = np.zeros(len(feature_cols))
            for model in self._models:
                imp = model.feature_importance(importance_type="gain")
                if len(imp) == len(feature_cols):
                    agg_arr += imp
            agg_arr /= max(len(self._models), 1)
            self._feature_importance = {
                feature_cols[i]: float(agg_arr[i])
                for i in range(len(feature_cols))
            }
