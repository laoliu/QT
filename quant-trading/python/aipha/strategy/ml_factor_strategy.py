"""ML 多因子选股策略

基于 Alpha158 量价因子 + LightGBM Walk-Forward 的 A 股截面选股策略。
继承 ``EquityRotationStrategy``，复用预过滤/止损/调仓/缓冲带机制，
覆盖 ``_score_candidates()`` 使用 ML 模型排名。

工作流程
--------
1. 首次调用时：从历史 OHLCV 批量计算 Alpha158 因子 → WF 训练
2. 每个调仓日：对当前截面标的计算最新 Alpha158 因子 → ML 预测 → top_k
3. 若模型未就绪，回退为线性多因子等权打分

配置示例 (configs/backtest/ml_factor_10y.yml)::

    strategy:
      name: ml_factor
      parameters:
        top_k: 50
        rebalance_days: 5
        model_objective: regression
        train_years: 3
        predict_months: 3

理论基础：
- Qlib Alpha158 + LightGBM: A 股 excess AR=17.8%, IR=2.0
- Zhang et al. 2021: 68 因子 Learn-to-Rank OOS AR=38%
- Gu, Kelly, Xiu 2020: 金融 ML 特征工程 > 模型选择
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 默认回退权重（无 ML 模型时用线性打分）
# ---------------------------------------------------------------------------

_DEFAULT_FALLBACK: dict[str, float] = {
    "pe_ttm": -1.0,
    "pb": -1.0,
    "roe": 1.0,
    "circ_mv": -0.5,
    "turnover_rate": -0.3,
}


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class MLFactorConfig(EquityRotationConfig):
    """ML 多因子策略配置。"""

    # --- ML 训练 ---
    model_objective: str = "regression"
    train_years: int = 3
    predict_months: int = 3
    forward_days: int = 5
    n_estimators: int = 300
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    purge_days: int = 10

    # --- LightGBM 正则化 (B-01 fix: 降低 IS-OOS 衰减) ---
    reg_alpha: float = 0.1       # L1 正则化
    reg_lambda: float = 1.0      # L2 正则化
    min_child_samples: int = 50   # 叶节点最小样本数
    subsample: float = 0.8       # 行采样率
    colsample_bytree: float = 0.8  # 列采样率
    min_gain_to_split: float = 0.0  # 最小分裂增益

    # --- Alpha158 ---
    alpha158_windows: list[int] = field(
        default_factory=lambda: [5, 10, 20, 30, 60],
    )

    # --- 回退 ---
    fallback_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_FALLBACK),
    )

    # --- 预训练模型路径 ---
    model_path: str = ""
    model_type: str = "lightgbm"  # "lightgbm" | "double_ensemble"

    # --- OOS 预测回放（Walk-Forward 评估专用） ---
    oos_predictions_path: str = ""

    # --- 多模型 OOS 集成（rank-average） ---
    oos_predictions_paths: list[str] = field(default_factory=list)
    ensemble_method: str = "rank_average"  # "rank_average" | "score_average"
    ensemble_weights: dict[str, float] = field(default_factory=dict)

    # --- 内部 ---
    min_train_samples: int = 5000
    retrain_interval_days: int = 60


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("ml_factor")
class MLFactorStrategy(EquityRotationStrategy):
    """ML 多因子选股策略。"""

    name = "ml_factor"

    def __init__(
        self, config: MLFactorConfig | None = None, **kwargs: Any,
    ) -> None:
        super().__init__()
        if config is not None:
            self._config = config
            self._ml_extra_kwargs: dict[str, Any] = {}
        else:
            valid = {
                f.name
                for f in MLFactorConfig.__dataclass_fields__.values()
            }
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            # Store model-specific params for passthrough to MLSelectorConfig
            _ML_PREFIXES = (
                "master_", "gat_", "gru_", "tra_", "xgb_", "en_",
            )
            self._ml_extra_kwargs = {
                k: v for k, v in kwargs.items()
                if k not in valid and any(k.startswith(p) for p in _ML_PREFIXES)
            }
            self._config = MLFactorConfig(**filtered)
        self._selector: Any | None = None
        self._model_ready = False
        self._last_train_bar: int = -9999
        self._ohlcv_buffer: dict[str, list[dict]] = {}

        # 加载 OOS 预测（优先），或预训练模型
        self._oos_scores: dict[str, dict[str, float]] = {}
        self._multi_oos_scores: dict[str, dict[str, dict[str, float]]] = {}
        if self._config.oos_predictions_paths:
            self._load_multi_oos_predictions()
        elif self._config.oos_predictions_path:
            self._load_oos_predictions()
        elif self._config.model_path:
            self._load_pretrained_model()

    def _load_pretrained_model(self) -> None:
        """从磁盘加载预训练 ML 模型。"""
        from pathlib import Path
        from aipha.models.ml_stock_selector import (
            MLSelectorConfig,
            MLStockSelector,
        )

        model_dir = Path(self._config.model_path)
        if not model_dir.is_absolute():
            model_dir = Path(__file__).resolve().parents[3] / model_dir
        if not model_dir.exists():
            logger.warning("模型路径不存在: %s, 回退线性打分", model_dir)
            return
        try:
            # Build MLSelectorConfig with model-specific params passthrough
            from dataclasses import fields as dc_fields
            sel_valid = {f.name for f in dc_fields(MLSelectorConfig)}
            sel_kwargs: dict[str, Any] = {
                "model_type": self._config.model_type,
            }
            for k, v in self._ml_extra_kwargs.items():
                if k in sel_valid:
                    sel_kwargs[k] = v
            selector = MLStockSelector(MLSelectorConfig(**sel_kwargs))
            selector.load(model_dir)
            self._selector = selector
            self._model_ready = True
            logger.info(
                "已加载 ML 模型: %s (%d folds)",
                model_dir, selector.n_folds,
            )
        except Exception as e:
            logger.warning("加载模型失败: %s, 回退线性打分", e)

    def _load_oos_predictions(self) -> None:
        """加载 Walk-Forward OOS 预测文件用于回测回放。

        文件格式: parquet, 列 = [datetime, symbol, pred_score, ...]
        加载后存为 {date_str: {symbol: score}} 字典。
        """
        from pathlib import Path

        fpath = Path(self._config.oos_predictions_path)
        if not fpath.is_absolute():
            fpath = Path(__file__).resolve().parents[3] / fpath
        if not fpath.exists():
            logger.warning(
                "OOS 预测文件不存在: %s, 回退线性打分", fpath,
            )
            return
        try:
            df = pd.read_parquet(fpath)
            df["date_str"] = pd.to_datetime(
                df["datetime"],
            ).dt.strftime("%Y-%m-%d")
            for date_str, grp in df.groupby("date_str"):
                self._oos_scores[date_str] = dict(
                    zip(grp["symbol"], grp["pred_score"]),
                )
            self._model_ready = True
            logger.info(
                "已加载 OOS 预测: %s (%d dates, %d rows)",
                fpath, len(self._oos_scores), len(df),
            )
        except Exception as e:
            logger.warning("加载 OOS 预测失败: %s", e)

    def _load_multi_oos_predictions(self) -> None:
        """加载多个 OOS 预测文件用于集成回测。

        每个文件按模型名存入 _multi_oos_scores dict。
        回测时通过 rank-average 集成多模型信号。
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        loaded = 0
        for p in self._config.oos_predictions_paths:
            fpath = Path(p)
            if not fpath.is_absolute():
                fpath = root / fpath
            if not fpath.exists():
                logger.warning("OOS 文件不存在: %s, 跳过", fpath)
                continue
            try:
                # 模型名从目录名提取
                model_name = fpath.parent.name
                df = pd.read_parquet(fpath)
                df["date_str"] = pd.to_datetime(
                    df["datetime"],
                ).dt.strftime("%Y-%m-%d")
                model_scores: dict[str, dict[str, float]] = {}
                for date_str, grp in df.groupby("date_str"):
                    model_scores[date_str] = dict(
                        zip(grp["symbol"], grp["pred_score"]),
                    )
                self._multi_oos_scores[model_name] = model_scores
                loaded += 1
            except Exception as e:
                logger.warning("加载 OOS 失败: %s — %s", fpath, e)
        if loaded > 0:
            self._model_ready = True
            logger.info(
                "已加载 %d 个 OOS 模型用于集成", loaded,
            )

    @property
    def config(self) -> MLFactorConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """ML 排名或线性回退。"""
        if not candidates:
            return []

        # 优先: 多模型 OOS 集成
        if self._multi_oos_scores:
            return self._ensemble_oos_score(candidates, context)

        # 其次: 单模型 OOS 预测回放
        if self._oos_scores:
            return self._oos_replay_score(candidates, context)

        if self._model_ready and self._selector is not None:
            return self._ml_score(candidates, context)
        return self._linear_fallback(candidates)

    # -- OOS 预测回放 --

    def _oos_replay_score(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """使用预计算的 OOS 预测分数对候选标的排名。"""
        date_str = context.current_time.strftime("%Y-%m-%d")
        day_scores = self._oos_scores.get(date_str, {})
        if not day_scores:
            return self._linear_fallback(candidates)
        scored = {}
        for c in candidates:
            s = day_scores.get(c.symbol)
            if s is not None:
                scored[c.symbol] = s
        if not scored:
            return self._linear_fallback(candidates)
        ranked = sorted(scored, key=lambda x: scored[x], reverse=True)
        return ranked

    # -- 多模型 OOS 集成 --

    def _ensemble_oos_score(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多模型 OOS 预测的 rank-average 集成。"""
        date_str = context.current_time.strftime("%Y-%m-%d")
        symbols = [c.symbol for c in candidates]
        method = self._config.ensemble_method
        weights = self._config.ensemble_weights

        # 收集各模型当日分数
        model_day: dict[str, dict[str, float]] = {}
        for mname, mscores in self._multi_oos_scores.items():
            ds = mscores.get(date_str, {})
            if ds:
                model_day[mname] = ds

        if not model_day:
            # 回退到单模型或线性
            if self._oos_scores:
                return self._oos_replay_score(candidates, context)
            return self._linear_fallback(candidates)

        return self._rank_average_ensemble(
            symbols, model_day, method, weights,
        )

    def _rank_average_ensemble(
        self,
        symbols: list[str],
        model_day: dict[str, dict[str, float]],
        method: str,
        weights: dict[str, float],
    ) -> list[str]:
        """对多模型分数做 rank-average 融合。"""
        model_names = list(model_day.keys())
        n_models = len(model_names)
        if n_models == 0:
            return []

        # 每个模型: 排名归一化 (0=worst, 1=best)
        model_ranks: dict[str, dict[str, float]] = {}
        for mname in model_names:
            scores = model_day[mname]
            valid = {s: scores[s] for s in symbols if s in scores}
            if not valid:
                continue
            n = len(valid)
            sorted_syms = sorted(valid, key=lambda x: valid[x])
            ranks = {s: i / max(n - 1, 1) for i, s in enumerate(sorted_syms)}
            model_ranks[mname] = ranks

        if not model_ranks:
            return []

        # 融合: 加权 rank 平均
        combined: dict[str, float] = {}
        for sym in symbols:
            total_w = 0.0
            total_rank = 0.0
            for mname, ranks in model_ranks.items():
                if sym not in ranks:
                    continue
                w = weights.get(mname, 1.0) if weights else 1.0
                total_rank += ranks[sym] * w
                total_w += w
            if total_w > 0:
                combined[sym] = total_rank / total_w

        if not combined:
            return []

        return sorted(combined, key=lambda x: combined[x], reverse=True)

    # -- ML 打分 --

    def _ml_score(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """使用 ML 模型对候选标的打分。

        从当日 bar.metadata 提取 alpha158 特征，
        调用 MLStockSelector.predict() 获取分数。
        """
        feature_records: list[dict[str, Any]] = []
        for c in candidates:
            row: dict[str, Any] = {"symbol": c.symbol}
            # 从 metadata 提取 alpha158 特征
            meta = c.metadata or {}
            for key, val in meta.items():
                if key.startswith(("kmid", "klen", "kup", "klow", "ksft",
                                   "open_0", "high_0", "low_0", "vwap_0",
                                   "roc_", "ma_", "std_", "beta_", "rsqr_",
                                   "resi_", "max_", "min_", "qtlu_", "qtld_",
                                   "rank_", "rsv_", "imax_", "imin_", "imxd_",
                                   "corr_", "cord_", "cntp_", "cntn_",
                                   "sump_", "sumn_", "vma_", "vstd_",
                                   "wvma_", "turn_",
                                   # R56 A4: 基本面 + 深度基本面因子
                                   "pe_ttm", "pb", "total_mv", "circ_mv",
                                   "turnover_rate", "dv_ratio", "dv_ttm",
                                   "roe", "roa", "grossprofit_margin",
                                   "netprofit_margin", "debt_to_assets",
                                   "cs_rank_fina_", "roe_debt_quality",
                                   "cs_rank_roe_debt_q", "accruals_ratio",
                                   "cs_rank_accruals", "gm_std_",
                                   )):
                    try:
                        fv = float(val)
                        row[key] = fv if math.isfinite(fv) else 0.0
                    except (TypeError, ValueError):
                        row[key] = 0.0
            feature_records.append(row)

        if not feature_records:
            return []

        df = pd.DataFrame(feature_records)
        try:
            scores = self._selector.predict(df)
            scored = dict(zip(df["symbol"], scores.values))
            ranked = sorted(scored, key=lambda s: scored[s], reverse=True)
            return ranked
        except Exception as e:
            logger.warning("ML 预测失败(%s), 回退线性打分", e)
            return self._linear_fallback(candidates)

    # -- 线性回退 --

    def _linear_fallback(
        self,
        candidates: list[EquityCandidateInfo],
    ) -> list[str]:
        """无模型时使用加权线性打分。

        每个特征先做排名标准化(0-1)，再乘权重累加。
        """
        if not candidates:
            return []

        weights = self._config.fallback_weights
        records: list[dict[str, Any]] = []
        for c in candidates:
            row: dict[str, Any] = {"symbol": c.symbol}
            meta = c.metadata or {}
            for feat in weights:
                val = meta.get(feat, np.nan)
                try:
                    val = float(val) if val is not None else np.nan
                    if not math.isfinite(val):
                        val = np.nan
                except (TypeError, ValueError):
                    val = np.nan
                row[feat] = val
            records.append(row)

        df = pd.DataFrame(records).set_index("symbol")
        # 中位数填充
        for col in df.columns:
            med = df[col].median()
            df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

        scores: dict[str, float] = {s: 0.0 for s in df.index}
        for col in df.columns:
            w = weights.get(col, 0.0)
            if w == 0.0 or len(df) <= 1:
                continue
            ranked = df[col].rank(method="average", ascending=True)
            normed = (ranked - 1) / max(len(df) - 1, 1)
            for sym in df.index:
                scores[sym] += w * normed.loc[sym]

        return sorted(scores, key=lambda s: scores[s], reverse=True)
