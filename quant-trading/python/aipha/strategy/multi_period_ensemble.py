"""多周期 ML 预测集成策略

融合不同预测周期（5d/10d/20d）的 OOS 预测信号，
通过 IC 加权实现更鲁棒的选股排名，天然降低换手率。

理论基础：
- 多周期信号融合稀释单一周期噪声
- IC 加权动态适应各周期预测力变化
- 长周期信号（20d）稳定性高，降低换手

配置示例（YAML）::

    strategy:
      name: ml_factor_ensemble
      parameters:
        top_k: 50
        rebalance_days: 5
        blend_method: ic_weighted
        periods:
          - horizon_days: 5
            predictions_path: models/.../oos_predictions.parquet
          - horizon_days: 10
            predictions_path: models/.../oos_predictions.parquet
          - horizon_days: 20
            predictions_path: models/.../oos_predictions.parquet
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class PeriodConfig:
    """单个预测周期配置。"""
    horizon_days: int = 5
    predictions_path: str = ""
    weight: float = 0.0
    ic_lookback: int = 60

@dataclass
class MultiPeriodEnsembleConfig(EquityRotationConfig):
    """多周期集成策略配置。"""
    periods: list[dict] = field(default_factory=list)
    blend_method: str = "ic_weighted"
    ic_min_weight: float = 0.05
    ic_decay_halflife: int = 20
    score_normalize: bool = True
    oos_predictions_path: str = ""
    forward_days: int = 5
    fallback_weights: dict[str, float] = field(
        default_factory=lambda: {
            "pe_ttm": -1.0, "pb": -1.0, "roe": 1.0,
            "circ_mv": -0.5, "turnover_rate": -0.3,
        },
    )


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("ml_factor_ensemble")
class MultiPeriodEnsembleStrategy(EquityRotationStrategy):
    """多周期 ML 预测集成策略。"""

    name = "ml_factor_ensemble"

    def __init__(
        self,
        config: MultiPeriodEnsembleConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        if config is not None:
            self._config = config
        else:
            valid = {
                f.name
                for f in MultiPeriodEnsembleConfig.__dataclass_fields__.values()
            }
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            self._config = MultiPeriodEnsembleConfig(**filtered)

        # 解析 PeriodConfig 列表
        self._period_configs: list[PeriodConfig] = []
        for p in self._config.periods:
            if isinstance(p, dict):
                self._period_configs.append(PeriodConfig(**p))
            elif isinstance(p, PeriodConfig):
                self._period_configs.append(p)

        # {horizon_days: {date_str: {symbol: score}}}
        self._period_scores: dict[int, dict[str, dict[str, float]]] = {}
        # {horizon_days: {date_str: {symbol: fwd_return}}}
        self._period_returns: dict[int, dict[str, dict[str, float]]] = {}
        # IC 缓存 {horizon_days: {date_str: rank_ic}}
        self._ic_cache: dict[int, dict[str, float]] = {}
        self._model_ready = False

        if self._period_configs:
            self._load_all_predictions()
        elif self._config.oos_predictions_path:
            # 单周期回退模式
            self._period_configs = [
                PeriodConfig(
                    horizon_days=self._config.forward_days,
                    predictions_path=self._config.oos_predictions_path,
                )
            ]
            self._load_all_predictions()

    @property
    def config(self) -> MultiPeriodEnsembleConfig:
        return self._config

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """多周期融合排名。"""
        if not candidates:
            return []
        if not self._model_ready:
            return self._linear_fallback(candidates)

        date_str = context.current_time.strftime("%Y-%m-%d")
        symbols = [c.symbol for c in candidates]
        blended = self._blend_scores(date_str, symbols)

        # 过滤掉零分（无数据的标的）
        scored = {s: v for s, v in blended.items() if v != 0.0}
        if not scored:
            return self._linear_fallback(candidates)

        ranked = sorted(scored, key=lambda x: scored[x], reverse=True)
        return ranked

    def _load_all_predictions(self) -> None:
        """加载所有周期的 OOS 预测文件。"""
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        loaded = 0

        for pc in self._period_configs:
            h = pc.horizon_days
            fpath = Path(pc.predictions_path)
            if not fpath.is_absolute():
                fpath = project_root / fpath
            if not fpath.exists():
                logger.warning(
                    "OOS 预测文件不存在 (horizon=%dd): %s", h, fpath,
                )
                continue
            try:
                df = pd.read_parquet(fpath)
                df["date_str"] = pd.to_datetime(
                    df["datetime"],
                ).dt.strftime("%Y-%m-%d")

                scores: dict[str, dict[str, float]] = {}
                returns: dict[str, dict[str, float]] = {}
                for date_str, grp in df.groupby("date_str"):
                    scores[date_str] = dict(
                        zip(grp["symbol"], grp["pred_score"]),
                    )
                    if "fwd_return" in grp.columns:
                        returns[date_str] = dict(
                            zip(grp["symbol"], grp["fwd_return"]),
                        )

                self._period_scores[h] = scores
                self._period_returns[h] = returns
                self._ic_cache[h] = {}
                loaded += 1
                logger.info(
                    "加载 OOS 预测 (horizon=%dd): %d dates, %d rows",
                    h, len(scores), len(df),
                )
            except Exception as e:
                logger.warning(
                    "加载 OOS 预测失败 (horizon=%dd): %s", h, e,
                )

        if loaded > 0:
            self._model_ready = True
            logger.info(
                "多周期集成: 成功加载 %d/%d 个周期",
                loaded, len(self._period_configs),
            )

    def _compute_ic_weights(self, date_str: str) -> dict[int, float]:
        """基于滚动 Rank IC 计算各周期权重。

        算法：
        1. 对每个周期，取最近 ic_lookback 天的 rank_ic
        2. EWM 加权计算平滑 IC
        3. 归一化为权重（下限 ic_min_weight）

        Returns:
            {horizon_days: weight}, 权重之和为 1.0
        """
        horizons = list(self._period_scores.keys())
        if len(horizons) == 1:
            return {horizons[0]: 1.0}

        if self._config.blend_method == "equal":
            w = 1.0 / len(horizons)
            return {h: w for h in horizons}

        if self._config.blend_method == "fixed":
            total = sum(
                pc.weight for pc in self._period_configs
                if pc.horizon_days in horizons and pc.weight > 0
            )
            if total > 0:
                return {
                    pc.horizon_days: pc.weight / total
                    for pc in self._period_configs
                    if pc.horizon_days in horizons and pc.weight > 0
                }
            w = 1.0 / len(horizons)
            return {h: w for h in horizons}

        # IC-weighted (default)
        ic_vals: dict[int, float] = {}
        for h in horizons:
            ic_vals[h] = self._get_smoothed_ic(h, date_str)

        min_w = self._config.ic_min_weight
        raw = {h: max(v, min_w) for h, v in ic_vals.items()}
        total = sum(raw.values())
        if total <= 0:
            w = 1.0 / len(horizons)
            return {h: w for h in horizons}
        return {h: v / total for h, v in raw.items()}

    def _get_smoothed_ic(self, horizon: int, date_str: str) -> float:
        """计算某周期在 date_str 的 EWM 平滑 Rank IC。"""
        cached = self._ic_cache.get(horizon, {})
        if date_str in cached:
            return cached[date_str]

        returns_map = self._period_returns.get(horizon, {})
        scores_map = self._period_scores.get(horizon, {})
        if not returns_map or not scores_map:
            return self._config.ic_min_weight

        # 取 date_str 之前的日期
        all_dates = sorted(scores_map.keys())
        past_dates = [d for d in all_dates if d < date_str]
        lookback = self._period_configs[0].ic_lookback
        window = past_dates[-lookback:] if len(past_dates) > lookback else past_dates

        if len(window) < 5:
            return self._config.ic_min_weight

        ics: list[float] = []
        for d in window:
            sc = scores_map.get(d, {})
            rt = returns_map.get(d, {})
            common = set(sc.keys()) & set(rt.keys())
            if len(common) < 20:
                continue
            s_arr = [sc[sym] for sym in common]
            r_arr = [rt[sym] for sym in common]
            rho, _ = sp_stats.spearmanr(s_arr, r_arr)
            if math.isfinite(rho):
                ics.append(rho)

        if not ics:
            return self._config.ic_min_weight

        # EWM 平滑
        halflife = self._config.ic_decay_halflife
        alpha = 1 - math.exp(-math.log(2) / max(halflife, 1))
        ewm = ics[0]
        for ic in ics[1:]:
            ewm = alpha * ic + (1 - alpha) * ewm

        result = max(ewm, 0.0)
        self._ic_cache.setdefault(horizon, {})[date_str] = result
        return result

    def _blend_scores(
        self, date_str: str, symbols: list[str],
    ) -> dict[str, float]:
        """融合多周期分数。

        流程：
        1. 每个周期做截面 z-score 标准化（可选）
        2. IC 加权求和
        3. 返回 {symbol: blended_score}
        """
        weights = self._compute_ic_weights(date_str)
        horizons = list(weights.keys())
        symbol_set = set(symbols)
        blended: dict[str, float] = {s: 0.0 for s in symbols}
        total_weight_applied: dict[str, float] = {s: 0.0 for s in symbols}

        for h in horizons:
            w = weights[h]
            day_scores = self._period_scores.get(h, {}).get(date_str, {})
            if not day_scores:
                continue

            # 提取有分数的标的
            scored = {
                s: day_scores[s] for s in symbol_set if s in day_scores
            }
            if not scored:
                continue

            if self._config.score_normalize and len(scored) > 1:
                # 截面 z-score 标准化
                vals = np.array(list(scored.values()))
                mu = float(np.mean(vals))
                sigma = float(np.std(vals))
                if sigma > 1e-10:
                    scored = {
                        s: (v - mu) / sigma for s, v in scored.items()
                    }
                else:
                    scored = {s: 0.0 for s in scored}

            for s, v in scored.items():
                blended[s] += w * v
                total_weight_applied[s] += w

        # 对未被所有周期覆盖的标的按实际权重归一
        for s in symbols:
            tw = total_weight_applied[s]
            if 0 < tw < 0.99:
                blended[s] /= tw

        return blended

    def _linear_fallback(
        self, candidates: list[EquityCandidateInfo],
    ) -> list[str]:
        """无模型时使用线性打分回退。"""
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
