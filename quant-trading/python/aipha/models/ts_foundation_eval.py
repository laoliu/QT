"""时序基础模型评估框架 — Chronos-2 / TimesFM 零样本评估。

为金融时序预测提供统一的评估接口，支持以下基础模型：
1. Chronos-2（Amazon，零样本时序预测）
2. TimesFM 2.5（Google，基础时序模型）
3. 自定义 PyTorch 模型适配

评估流程：
1. 加载预训练模型（零样本/微调）
2. 生成滚动窗口预测
3. 与 LightGBM 基准对比 IC/ICIR/方向准确率
4. 通过 agent_audit.py 记录完整实验 trace

理论基础：
- 知识库 D2b: Chronos-2, TimesFM 2.5
- Gu, Kelly, Xiu (2020): R² < 5%
- 五原则 A: 不得在未通过统计验证前用于生产策略

使用示例::

    evaluator = TSFoundationEvaluator()
    evaluator.register_model("chronos2", ChronosAdapter())
    report = evaluator.evaluate(returns_df, horizon=5)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PredictionScore:
    """单模型评估分数"""
    model_name: str
    ic: float = 0.0
    icir: float = 0.0
    direction_accuracy: float = 0.0
    mse: float = 0.0
    mae: float = 0.0

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "ic": round(self.ic, 6),
            "icir": round(self.icir, 4),
            "direction_accuracy": round(self.direction_accuracy, 4),
            "mse": round(self.mse, 6),
            "mae": round(self.mae, 6),
        }


@dataclass
class EvaluationReport:
    """评估报告"""
    horizon: int = 5
    n_samples: int = 0
    scores: list[PredictionScore] = field(default_factory=list)
    baseline_name: str = "naive"

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon,
            "n_samples": self.n_samples,
            "baseline": self.baseline_name,
            "scores": [s.to_dict() for s in self.scores],
        }

    def summary(self) -> str:
        """生成简要比较表"""
        lines = [f"{'Model':<20} {'IC':>8} {'ICIR':>8} {'DirAcc':>8}"]
        for s in self.scores:
            lines.append(
                f"{s.model_name:<20} {s.ic:>8.4f} "
                f"{s.icir:>8.4f} {s.direction_accuracy:>8.2%}"
            )
        return "\n".join(lines)


# ── 模型适配器抽象 ──


class TSModelAdapter(ABC):
    """时序预测模型适配器接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        """模型名称"""
        ...

    @abstractmethod
    def predict(
        self,
        history: np.ndarray,
        horizon: int,
    ) -> np.ndarray:
        """给定历史序列，预测未来 horizon 步

        Args:
            history: shape (T,) 历史收益率序列
            horizon: 预测步长

        Returns:
            shape (horizon,) 预测值
        """
        ...


class NaiveAdapter(TSModelAdapter):
    """朴素基准：预测 = 历史均值"""

    @property
    def name(self) -> str:
        return "naive_mean"

    def predict(self, history: np.ndarray, horizon: int) -> np.ndarray:
        mean_val = np.nanmean(history[-20:]) if len(history) >= 20 else np.nanmean(history)
        return np.full(horizon, mean_val)


class LastValueAdapter(TSModelAdapter):
    """最近值基准：预测 = 上一期值"""

    @property
    def name(self) -> str:
        return "last_value"

    def predict(self, history: np.ndarray, horizon: int) -> np.ndarray:
        return np.full(horizon, history[-1] if len(history) > 0 else 0.0)


# ── 评估引擎 ──


class TSFoundationEvaluator:
    """时序基础模型评估框架"""

    def __init__(self, lookback: int = 60) -> None:
        self.lookback = lookback
        self._models: dict[str, TSModelAdapter] = {}
        # 默认注册基准
        self.register_model("naive_mean", NaiveAdapter())
        self.register_model("last_value", LastValueAdapter())

    def register_model(
        self, name: str, adapter: TSModelAdapter,
    ) -> None:
        """注册模型适配器"""
        self._models[name] = adapter

    def evaluate(
        self,
        returns: pd.Series,
        horizon: int = 5,
        step: int = 5,
    ) -> EvaluationReport:
        """滚动窗口评估所有注册模型"""
        vals = returns.values.astype(np.float64)
        n = len(vals)
        report = EvaluationReport(horizon=horizon)

        # 滚动窗口切分
        predictions: dict[str, list[np.ndarray]] = {
            k: [] for k in self._models
        }
        actuals: list[np.ndarray] = []

        for start in range(self.lookback, n - horizon, step):
            history = vals[:start]
            actual = vals[start : start + horizon]
            actuals.append(actual)
            for name, model in self._models.items():
                pred = model.predict(history, horizon)
                predictions[name].append(pred)

        if not actuals:
            return report

        report.n_samples = len(actuals)
        act_arr = np.array(actuals)

        for name, preds in predictions.items():
            pred_arr = np.array(preds)
            score = self._score_model(name, pred_arr, act_arr)
            report.scores.append(score)

        return report

    def _score_model(
        self,
        name: str,
        predictions: np.ndarray,
        actuals: np.ndarray,
    ) -> PredictionScore:
        """评分单个模型"""
        from scipy.stats import spearmanr

        # 展平
        pred_flat = predictions.flatten()
        act_flat = actuals.flatten()
        valid = ~(np.isnan(pred_flat) | np.isnan(act_flat))
        pf, af = pred_flat[valid], act_flat[valid]

        mse = float(np.mean((pf - af) ** 2)) if len(pf) > 0 else 0.0
        mae = float(np.mean(np.abs(pf - af))) if len(pf) > 0 else 0.0

        # IC
        ic = 0.0
        if len(pf) >= 10:
            corr, _ = spearmanr(pf, af)
            ic = float(corr) if np.isfinite(corr) else 0.0

        # 方向准确率
        dir_acc = 0.0
        if len(pf) > 0:
            dir_correct = np.sign(pf) == np.sign(af)
            dir_acc = float(np.mean(dir_correct))

        return PredictionScore(
            model_name=name,
            ic=ic,
            icir=0.0,  # 需要多期 IC 才能算
            direction_accuracy=dir_acc,
            mse=mse,
            mae=mae,
        )
