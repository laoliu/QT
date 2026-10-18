"""Historical standalone LightGBM ranker, isolated for explicit legacy replay.

New research uses ``aipha.research.ResearchPipeline`` and its versioned model
loader. This implementation retains its historical labels, calendar-day
splits and unversioned native Booster files; it is not a canonical PIT API.
Construction and native model loading require ``legacy_compat=True`` at the
call site. Stored configuration cannot grant that permission.

使用 LightGBM lambdarank 对 A股截面进行打分排名，
特征来自价值/质量/动量/波动率四因子（EP/ROE/MOM/IVOL）及扩展量价因子。

历史训练方案：Walk-Forward（3yr train / 6m test），不代表规范研究的防前视验收。
标签定义：持仓期（rebalance_days）内收益率的截面排名分位（0-3，越高越好）。

理论基础：
- Gu, Kelly, Xiu (2020) — 金融数据 R² < 5%, 特征工程 > 模型选择
- López de Prado (2018) — Purged K-Fold + Embargo 防泄漏
- Fama-French (1992/1993) — 价值/规模因子
- Jegadeesh & Titman (1993) — 动量因子

知识库对齐：知识库 G1 "LightGBM 截面模型"，D7 基准 ICIR=0.3458

与现有架构关系：
- 特征来自 equity_factors.py (EP/ROE/MOM/IVOL) + 扩展量价特征
- 推理输出对接 AStockMultiFactorStrategy._score_candidates()
- Walk-Forward 验证框架兼容 wf_config_runner.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _require_legacy(legacy_compat: bool) -> None:
    if legacy_compat is not True:
        raise ValueError(
            "standalone EquityLGBMRanker is disabled by default; use "
            "aipha.research.ResearchPipeline or explicitly pass legacy_compat=True "
            "for historical replay (unversioned models are not canonical artifacts)"
        )


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class EquityRankerConfig:
    """A股 LightGBM Ranker 配置。

    Attributes:
        feature_cols: 特征列名（按因子类别分组）
        label_col: 标签列（截面分位排名，0-3）
        group_col: 截面分组列（交易日期）
        label_quantiles: 排名分位数（标签离散化档数）
        n_estimators: 树数量
        learning_rate: 学习率
        max_depth: 最大树深度
        num_leaves: 叶节点数
        min_child_samples: 叶节点最少样本数
        subsample: 行采样率（防过拟合）
        colsample_bytree: 列采样率
        reg_alpha: L1 正则
        reg_lambda: L2 正则
        train_years: 训练窗口（年）
        test_months: 验证窗口（月）
        purge_days: 防泄漏间隔（日）
        rebalance_days: 持仓期（与调仓周期一致）
        random_state: 随机种子
    """
    feature_cols: list[str] = field(default_factory=lambda: [
        # 价值因子
        "ep_ratio",          # 盈利收益率 = 净利润TTM / 市值
        "bp_ratio",          # 账面市值比
        # 质量因子
        "roe",               # ROE（最新季度）
        "roa",               # ROA
        "gross_margin",      # 毛利率
        # 动量因子
        "mom_12_1",          # 12月去掉最近1月收益（MOM_12_1）
        "mom_3",             # 3月动量
        "mom_1",             # 1月动量（短期反转信号，用负向）
        # 波动率因子
        "ivol_neg",          # 特异波动率取负（IVOL反转）
        "beta",              # 市场 Beta
        # 规模因子
        "log_mv",            # 对数市值（规模）
        # 量价技术因子
        "turnover_rate",     # 换手率
        "amihud",            # Amihud 非流动性
        "price_to_high",     # 当前价对52周高的比率
    ])
    label_col: str = "fwd_return_rank"
    group_col: str = "trade_date"
    label_quantiles: int = 4          # 0/1/2/3 四档
    n_estimators: int = 300
    learning_rate: float = 0.05
    max_depth: int = 5
    num_leaves: int = 31
    min_child_samples: int = 30
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.1
    reg_lambda: float = 1.0
    train_years: int = 3
    test_months: int = 6
    purge_days: int = 10
    rebalance_days: int = 20
    random_state: int = 42


# ---------------------------------------------------------------------------
# Walk-Forward 验证工具
# ---------------------------------------------------------------------------

@dataclass
class WFSplit:
    """单个 Walk-Forward 折的训练/测试切分。"""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    fold_idx: int


@dataclass
class WFStats:
    """Walk-Forward 验证统计。"""
    n_folds: int
    ic_series: list[float]
    ndcg_series: list[float]
    mean_ic: float
    icir: float
    pass_rate: float        # IC > 0 的折数比例
    oos_is_ratio: float     # OOS IC / IS IC（衰减比）


# ---------------------------------------------------------------------------
# 核心模型
# ---------------------------------------------------------------------------

class EquityLGBMRanker:
    """A股 LightGBM 截面排名模型。

    Usage::

        ranker = EquityLGBMRanker(legacy_compat=True)
        ranker.fit(train_df)
        scores = ranker.predict(test_df)

    数据格式要求::

        train_df columns = [feature_cols..., label_col, group_col]
        其中 label_col = 截面分位排名（int，0=最差, 3=最好）
        group_col = 交易日期字符串（用于构建 LambdaRank query group）
    """

    def __init__(self, config: EquityRankerConfig | None = None, *, legacy_compat: bool = False) -> None:
        _require_legacy(legacy_compat)
        self._config = config or EquityRankerConfig()
        self._model: Any = None
        self._feature_importance: dict[str, float] = {}
        self._is_fitted: bool = False

    @property
    def config(self) -> EquityRankerConfig:
        return self._config

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(self, df: pd.DataFrame) -> "EquityLGBMRanker":
        """训练 LightGBM lambdarank 模型。

        Args:
            df: 训练集 DataFrame，必须含 feature_cols + label_col + group_col

        Returns:
            self（链式调用）
        """
        import lightgbm as lgb

        cfg = self._config
        features = [c for c in cfg.feature_cols if c in df.columns]
        dropped = [c for c in cfg.feature_cols if c not in df.columns]
        if dropped:
            logger.warning(
                "LGBMRanker.fit(): %d/%d feature_cols 在数据中缺失将被跳过: %s. "
                "若包含 roe/roa 等季报因子，请确保训练时启用 --deep-fundamental。",
                len(dropped), len(cfg.feature_cols), dropped,
            )
        if len(features) < 2:
            raise ValueError(
                f"Need at least 2 feature columns in df, got: {features}"
            )

        X = df[features].fillna(0.0).values
        y_raw = df[cfg.label_col].values if cfg.label_col in df.columns else np.zeros(len(df))
        # 确保标签为非负整数（LambdaRank relevance）
        y = np.clip(y_raw.astype(int), 0, cfg.label_quantiles - 1)

        # 构建 query groups（每个截面日期的样本数，顺序与 df 行顺序一致）
        if cfg.group_col in df.columns:
            groups = df.groupby(cfg.group_col, sort=False).size().values
        else:
            groups = np.array([len(df)])

        train_set = lgb.Dataset(X, label=y, group=groups, feature_name=features)

        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [5, 10, 20],
            "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth,
            "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample,
            "colsample_bytree": cfg.colsample_bytree,
            "reg_alpha": cfg.reg_alpha,
            "reg_lambda": cfg.reg_lambda,
            "verbose": -1,
            "seed": cfg.random_state,
        }

        self._model = lgb.train(
            params,
            train_set,
            num_boost_round=cfg.n_estimators,
        )

        imp_arr = self._model.feature_importance(importance_type="gain")
        self._feature_importance = {
            features[i]: float(imp_arr[i]) for i in range(len(features))
        }
        self._is_fitted = True
        logger.info(
            "EquityLGBMRanker fitted: %d samples, %d features, %d groups",
            len(df), len(features), len(groups),
        )
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """预测截面排名分数（分数越高 → 推荐买入）。

        Args:
            df: 待评分 DataFrame，含 feature_cols

        Returns:
            shape=(len(df),) 的 float 分数数组
        """
        if not self._is_fitted or self._model is None:
            raise RuntimeError("Model not fitted. Call fit() first.")
        cfg = self._config
        features = [c for c in cfg.feature_cols if c in df.columns]
        if not features:
            raise ValueError("No recognized feature columns found in df")
        X = df[features].fillna(0.0).values
        return self._model.predict(X)

    def get_feature_importance(self) -> dict[str, float]:
        """返回 feature importance（gain），按重要性降序排列。"""
        return dict(
            sorted(self._feature_importance.items(), key=lambda kv: kv[1], reverse=True)
        )

    def save(self, path: Path) -> None:
        """保存模型到文件。"""
        if not self._is_fitted or self._model is None:
            raise RuntimeError("No model to save")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path))
        logger.info("EquityLGBMRanker saved to %s", path)

    def load(self, path: Path, *, legacy_compat: bool = False) -> "EquityLGBMRanker":
        """Explicitly load a historical native Booster without a manifest."""
        _require_legacy(legacy_compat)
        import lightgbm as lgb

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {path}")
        self._model = lgb.Booster(model_file=str(path))
        self._is_fitted = True
        logger.info("EquityLGBMRanker loaded from %s", path)
        return self


# ---------------------------------------------------------------------------
# Walk-Forward 验证器
# ---------------------------------------------------------------------------

class EquityRankerWFValidator:
    """A股截面 ML 模型的 Purged Walk-Forward 验证器。

    实现论文：López de Prado (2018) Purged K-Fold Cross-Validation
    - 训练集与测试集之间保留 purge_days 天间隔（embargo）
    - 每折向前滚动 test_months 个月
    """

    def __init__(self, config: EquityRankerConfig | None = None, *, legacy_compat: bool = False) -> None:
        _require_legacy(legacy_compat)
        self._config = config or EquityRankerConfig()

    def generate_splits(
        self,
        df: pd.DataFrame,
        date_col: str = "trade_date",
    ) -> list[WFSplit]:
        """生成 Walk-Forward 切分列表。

        规则：
        - 训练集长度 = train_years 年
        - 测试集长度 = test_months 月
        - 训练末尾到测试开始之间保留 purge_days 天间隔
        - 每折向前滚动 test_months 月
        """
        cfg = self._config
        dates = pd.to_datetime(df[date_col].unique())
        dates = sorted(dates)
        if len(dates) == 0:
            return []

        min_date = dates[0]
        max_date = dates[-1]

        splits: list[WFSplit] = []
        fold_idx = 0

        # 训练窗口起点
        cursor = min_date + pd.DateOffset(years=cfg.train_years)

        while cursor <= max_date:
            train_end = cursor - pd.Timedelta(days=cfg.purge_days + 1)
            train_start = train_end - pd.DateOffset(years=cfg.train_years)

            # 测试集起点 = cursor，终点 = cursor + test_months
            test_start = cursor
            test_end = cursor + pd.DateOffset(months=cfg.test_months) - pd.Timedelta(days=1)
            test_end = min(test_end, max_date)

            if train_start >= min_date and test_start <= max_date:
                splits.append(WFSplit(
                    train_start=train_start.strftime("%Y-%m-%d"),
                    train_end=train_end.strftime("%Y-%m-%d"),
                    test_start=test_start.strftime("%Y-%m-%d"),
                    test_end=test_end.strftime("%Y-%m-%d"),
                    fold_idx=fold_idx,
                ))
                fold_idx += 1

            cursor += pd.DateOffset(months=cfg.test_months)

        return splits

    def run(
        self,
        df: pd.DataFrame,
        date_col: str = "trade_date",
        n_max_folds: int | None = None,
    ) -> WFStats:
        """执行 Walk-Forward 验证并返回统计。

        每折：训练 → 验证 → 计算 Spearman IC（预测分 vs 真实收益排名）

        Args:
            df: 完整数据集（含 label_col 和 group_col）
            date_col: 日期列名
            n_max_folds: 最多折数（None=全部折）

        Returns:
            WFStats 汇总统计
        """
        from scipy import stats as scipy_stats

        splits = self.generate_splits(df, date_col=date_col)
        if not splits:
            logger.warning("No Walk-Forward splits generated")
            return WFStats(
                n_folds=0, ic_series=[], ndcg_series=[],
                mean_ic=float("nan"), icir=float("nan"),
                pass_rate=float("nan"), oos_is_ratio=float("nan"),
            )

        if n_max_folds is not None:
            splits = splits[:n_max_folds]

        cfg = self._config
        ic_list: list[float] = []
        ndcg_list: list[float] = []
        is_ic_list: list[float] = []

        for sp in splits:
            train_mask = (df[date_col] >= sp.train_start) & (df[date_col] <= sp.train_end)
            test_mask = (df[date_col] >= sp.test_start) & (df[date_col] <= sp.test_end)
            train_df = df[train_mask].copy()
            test_df = df[test_mask].copy()

            if len(train_df) < 50 or len(test_df) < 10:
                logger.debug("Fold %d skipped: insufficient data", sp.fold_idx)
                continue

            # 训练
            fold_ranker = EquityLGBMRanker(cfg, legacy_compat=True)
            try:
                fold_ranker.fit(train_df)
            except Exception as exc:
                logger.warning("Fold %d fit failed: %s", sp.fold_idx, exc)
                continue

            # OOS 预测 IC（Spearman）
            try:
                scores = fold_ranker.predict(test_df)
                if cfg.label_col in test_df.columns:
                    corr, _ = scipy_stats.spearmanr(scores, test_df[cfg.label_col].values)
                    if not np.isnan(corr):
                        ic_list.append(float(corr))
            except Exception as exc:
                logger.warning("Fold %d predict failed: %s", sp.fold_idx, exc)
                continue

            # IS IC
            try:
                is_scores = fold_ranker.predict(train_df)
                if cfg.label_col in train_df.columns:
                    is_corr, _ = scipy_stats.spearmanr(is_scores, train_df[cfg.label_col].values)
                    if not np.isnan(is_corr):
                        is_ic_list.append(float(is_corr))
            except Exception:
                pass

        if not ic_list:
            return WFStats(
                n_folds=0, ic_series=[], ndcg_series=[],
                mean_ic=float("nan"), icir=float("nan"),
                pass_rate=float("nan"), oos_is_ratio=float("nan"),
            )

        ic_arr = np.array(ic_list)
        mean_ic = float(np.mean(ic_arr))
        ic_std = float(np.std(ic_arr, ddof=1)) if len(ic_arr) >= 2 else float("nan")
        icir = mean_ic / ic_std if (not np.isnan(ic_std) and ic_std > 0) else float("nan")
        pass_rate = float(np.mean(ic_arr > 0))

        # OOS/IS 衰减比
        oos_is_ratio = float("nan")
        if is_ic_list:
            mean_is_ic = float(np.mean(is_ic_list))
            if abs(mean_is_ic) > 1e-9:
                oos_is_ratio = mean_ic / mean_is_ic

        return WFStats(
            n_folds=len(ic_list),
            ic_series=ic_list,
            ndcg_series=ndcg_list,
            mean_ic=mean_ic,
            icir=icir,
            pass_rate=pass_rate,
            oos_is_ratio=oos_is_ratio,
        )


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def make_equity_ranker(
    feature_cols: list[str] | None = None,
    train_years: int = 3,
    test_months: int = 6,
    n_estimators: int = 300,
    *,
    legacy_compat: bool = False,
) -> EquityLGBMRanker:
    """Create an explicitly authorized historical standalone ranker."""
    _require_legacy(legacy_compat)
    cfg = EquityRankerConfig(
        train_years=train_years,
        test_months=test_months,
        n_estimators=n_estimators,
    )
    if feature_cols is not None:
        cfg.feature_cols = feature_cols
    return EquityLGBMRanker(cfg, legacy_compat=True)


def build_ranking_label(
    df: pd.DataFrame,
    return_col: str = "fwd_return",
    group_col: str = "trade_date",
    n_quantiles: int = 4,
    label_col: str = "fwd_return_rank",
) -> pd.DataFrame:
    """将前瞻收益率转换为截面分位排名标签（0~n_quantiles-1）。

    Args:
        df: 含 return_col 和 group_col 的 DataFrame
        return_col: 前瞻收益率列名
        group_col: 截面分组列名（交易日期）
        n_quantiles: 排名档数
        label_col: 输出标签列名

    Returns:
        添加了 label_col 列的 DataFrame（原始 df 副本）
    """
    df = df.copy()

    def _quantile_rank(series: pd.Series) -> pd.Series:
        """截面内将 return 转换为 0-base 分位整数。"""
        clean = series.dropna()
        if len(clean) < n_quantiles:
            return pd.Series(np.zeros(len(series), dtype=int), index=series.index)
        try:
            qrank = pd.qcut(series, n_quantiles, labels=False, duplicates="drop")
            return qrank.fillna(0).astype(int)
        except Exception:
            return pd.Series(np.zeros(len(series), dtype=int), index=series.index)

    df[label_col] = df.groupby(group_col)[return_col].transform(_quantile_rank)
    return df

