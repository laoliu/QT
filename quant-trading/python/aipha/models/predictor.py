"""
ML模型预测器

提供统一的预测接口，支持模型加载、批量预测和在线预测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """预测结果"""

    predictions: np.ndarray
    symbols: list[str]
    timestamp: datetime
    model_id: str

    # 可选信息
    confidence: np.ndarray | None = None
    feature_contributions: dict[str, np.ndarray] | None = None

    def to_dataframe(self) -> pd.DataFrame:
        """转换为DataFrame"""
        df = pd.DataFrame({
            "symbol": self.symbols,
            "prediction": self.predictions,
            "timestamp": self.timestamp,
        })
        if self.confidence is not None:
            df["confidence"] = self.confidence
        return df

    def get_top_n(self, n: int = 10, ascending: bool = False) -> pd.DataFrame:
        """获取Top N"""
        df = self.to_dataframe()
        return df.nlargest(n, "prediction") if not ascending else df.nsmallest(n, "prediction")


@dataclass
class PredictorConfig:
    """预测器配置"""

    model_path: Path | None = None
    model_name: str = ""
    model_version: int | None = None

    # 预测参数
    batch_size: int = 10000
    output_type: str = "raw"  # raw / rank / zscore

    # 缓存
    enable_cache: bool = False
    cache_ttl_seconds: int = 300


class Predictor:
    """模型预测器

    支持单模型和多模型集成预测。
    """

    def __init__(self, config: PredictorConfig | None = None):
        self.config = config or PredictorConfig()
        self._model = None
        self._model_id: str = ""
        self._feature_names: list[str] = []
        self._is_loaded = False

        # 缓存
        self._cache: dict[str, tuple[PredictionResult, datetime]] = {}

    def load(self, path: Path | None = None, *, verify_hash: bool = True) -> bool:
        """加载模型（安全增强版）。

        安全措施（C-3 安全序列化改进）：
        1. SHA-256 哈希校验：若 ``{model_path}.sha256`` 文件存在，验证模型文件
           的 SHA-256 哈希是否与记录一致。校验失败时记录 WARNING 但仍允许加载
           （可通过 ``verify_hash=False`` 完全跳过）。
        2. 日志审计：记录模型路径、文件大小和哈希前 16 位。

        Args:
            path: 模型文件路径，None 时使用 config.model_path。
            verify_hash: 是否进行哈希校验（默认 True）。

        Returns:
            加载是否成功。
        """
        import hashlib
        import pickle

        model_path = path or self.config.model_path
        if model_path is None:
            logger.error("No model path specified")
            return False

        model_path = Path(model_path)

        # 哈希校验
        if verify_hash:
            self._verify_model_hash(model_path)

        try:
            file_size = model_path.stat().st_size
            with open(model_path, "rb") as f:
                raw = f.read()

            file_hash = hashlib.sha256(raw).hexdigest()[:16]
            logger.info(
                "Loading model %s (size=%d bytes, sha256=%s...)",
                model_path.name, file_size, file_hash,
            )

            data = pickle.loads(raw)  # noqa: S301
            self._model = data["model"]
            self._feature_names = data.get("feature_names", [])
            self._model_id = model_path.stem
            self._is_loaded = True

            logger.info("Loaded model from %s", model_path)
            return True
        except Exception as e:
            logger.error("Failed to load model: %s", e)
            return False

    @staticmethod
    def _verify_model_hash(model_path: Path) -> bool:
        """校验模型文件的 SHA-256 哈希。

        查找 ``{model_path}.sha256`` 文件（内容为十六进制哈希字符串）。
        若哈希文件不存在，记录 INFO 并跳过校验。
        若哈希不匹配，记录 WARNING（潜在文件篡改或版本不一致）。

        Returns:
            校验通过或无校验文件时返回 True，哈希不匹配时返回 False。
        """
        import hashlib

        hash_file = model_path.with_suffix(model_path.suffix + ".sha256")
        if not hash_file.exists():
            logger.info("No hash file found at %s; skipping integrity check", hash_file.name)
            return True

        try:
            expected_hash = hash_file.read_text(encoding="utf-8").strip().lower()
            with open(model_path, "rb") as f:
                actual_hash = hashlib.sha256(f.read()).hexdigest().lower()

            if actual_hash != expected_hash:
                logger.warning(
                    "Model hash mismatch for %s: expected=%s..., actual=%s...",
                    model_path.name, expected_hash[:16], actual_hash[:16],
                )
                return False

            logger.info("Model hash verified: %s", model_path.name)
            return True
        except Exception as e:
            logger.warning("Failed to verify model hash: %s", e)
            return False

    @staticmethod
    def save_model_hash(model_path: Path) -> str:
        """为模型文件生成并保存 SHA-256 哈希。

        将哈希写入 ``{model_path}.sha256``，返回哈希字符串。
        用于模型训练后生成校验文件。
        """
        import hashlib

        model_path = Path(model_path)
        with open(model_path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        hash_file = model_path.with_suffix(model_path.suffix + ".sha256")
        hash_file.write_text(file_hash, encoding="utf-8")
        logger.info("Saved model hash to %s", hash_file.name)
        return file_hash

    def predict(
        self,
        X: pd.DataFrame,
        symbols: list[str] | None = None,
    ) -> PredictionResult:
        """执行预测"""
        if not self._is_loaded:
            raise RuntimeError("Model not loaded")

        # 检查特征
        missing_features = set(self._feature_names) - set(X.columns)
        if missing_features:
            logger.warning(f"Missing features: {missing_features}")

        # 确保特征顺序
        if self._feature_names:
            # 填充缺失特征
            for feat in self._feature_names:
                if feat not in X.columns:
                    X[feat] = 0
            X = X[self._feature_names]

        # 批量预测
        predictions = self._predict_batch(X)

        # 后处理
        predictions = self._postprocess(predictions)

        # 构造结果
        if symbols is None:
            symbols = list(X.index) if hasattr(X.index, "__iter__") else []

        return PredictionResult(
            predictions=predictions,
            symbols=symbols if isinstance(symbols, list) else list(symbols),
            timestamp=datetime.now(),
            model_id=self._model_id,
        )

    def _predict_batch(self, X: pd.DataFrame) -> np.ndarray:
        """批量预测"""
        n_samples = len(X)
        batch_size = self.config.batch_size

        if n_samples <= batch_size:
            return self._model.predict(X)

        # 分批处理
        predictions = []
        for i in range(0, n_samples, batch_size):
            batch = X.iloc[i : i + batch_size]
            pred = self._model.predict(batch)
            predictions.append(pred)

        return np.concatenate(predictions)

    def _postprocess(self, predictions: np.ndarray) -> np.ndarray:
        """预测后处理"""
        output_type = self.config.output_type

        if output_type == "raw":
            return predictions
        elif output_type == "rank":
            # 转换为排名
            from scipy.stats import rankdata
            return rankdata(predictions) / len(predictions)
        elif output_type == "zscore":
            # Z-score标准化
            mean = np.mean(predictions)
            std = np.std(predictions)
            if std > 0:
                return (predictions - mean) / std
            return predictions - mean
        else:
            return predictions

    def is_loaded(self) -> bool:
        """检查模型是否已加载"""
        return self._is_loaded

    def get_model_id(self) -> str:
        """获取模型ID"""
        return self._model_id

    def get_feature_names(self) -> list[str]:
        """获取特征名列表"""
        return self._feature_names.copy()


class EnsemblePredictor:
    """集成预测器

    支持多模型加权集成。
    """

    def __init__(self):
        self._predictors: dict[str, Predictor] = {}
        self._weights: dict[str, float] = {}

    def add_predictor(
        self,
        name: str,
        predictor: Predictor,
        weight: float = 1.0,
    ) -> None:
        """添加预测器"""
        self._predictors[name] = predictor
        self._weights[name] = weight

    def remove_predictor(self, name: str) -> bool:
        """移除预测器"""
        if name in self._predictors:
            del self._predictors[name]
            del self._weights[name]
            return True
        return False

    def set_weights(self, weights: dict[str, float]) -> None:
        """设置权重"""
        for name, weight in weights.items():
            if name in self._predictors:
                self._weights[name] = weight

    def predict(
        self,
        X: pd.DataFrame,
        symbols: list[str] | None = None,
        method: str = "weighted",  # weighted / average / median
    ) -> PredictionResult:
        """集成预测"""
        if not self._predictors:
            raise RuntimeError("No predictors added")

        all_predictions = []
        all_weights = []

        for name, predictor in self._predictors.items():
            result = predictor.predict(X, symbols)
            all_predictions.append(result.predictions)
            all_weights.append(self._weights[name])

        predictions_array = np.array(all_predictions)

        if method == "weighted":
            # 加权平均
            weights = np.array(all_weights)
            weights = weights / weights.sum()
            final_predictions = np.average(predictions_array, axis=0, weights=weights)
        elif method == "average":
            final_predictions = np.mean(predictions_array, axis=0)
        elif method == "median":
            final_predictions = np.median(predictions_array, axis=0)
        else:
            final_predictions = np.mean(predictions_array, axis=0)

        if symbols is None:
            symbols = list(X.index) if hasattr(X.index, "__iter__") else []

        return PredictionResult(
            predictions=final_predictions,
            symbols=symbols if isinstance(symbols, list) else list(symbols),
            timestamp=datetime.now(),
            model_id="ensemble",
        )

    def get_predictor_names(self) -> list[str]:
        """获取预测器名列表"""
        return list(self._predictors.keys())


class OnlinePredictor:
    """在线预测器

    支持实时预测，低延迟场景。
    """

    def __init__(self, predictor: Predictor):
        self._predictor = predictor
        self._last_features: dict[str, dict[str, float]] = {}
        self._last_predictions: dict[str, float] = {}

    def update_features(
        self,
        symbol: str,
        features: dict[str, float],
    ) -> None:
        """更新特征"""
        self._last_features[symbol] = features

    def get_prediction(self, symbol: str) -> float | None:
        """获取单个预测"""
        if symbol not in self._last_features:
            return self._last_predictions.get(symbol)

        features = self._last_features[symbol]
        df = pd.DataFrame([features])

        result = self._predictor.predict(df, [symbol])
        prediction = float(result.predictions[0])

        self._last_predictions[symbol] = prediction
        return prediction

    def batch_predict(self, symbols: list[str]) -> dict[str, float]:
        """批量预测"""
        # 筛选有特征的股票
        available = [s for s in symbols if s in self._last_features]
        if not available:
            return {s: self._last_predictions.get(s, 0.0) for s in symbols}

        # 构造DataFrame
        rows = [self._last_features[s] for s in available]
        df = pd.DataFrame(rows)

        result = self._predictor.predict(df, available)

        # 更新缓存
        predictions = {}
        for i, symbol in enumerate(available):
            pred = float(result.predictions[i])
            self._last_predictions[symbol] = pred
            predictions[symbol] = pred

        # 补充未更新的股票
        for symbol in symbols:
            if symbol not in predictions:
                predictions[symbol] = self._last_predictions.get(symbol, 0.0)

        return predictions

    def clear_cache(self, symbol: str | None = None) -> None:
        """清除缓存"""
        if symbol:
            self._last_features.pop(symbol, None)
            self._last_predictions.pop(symbol, None)
        else:
            self._last_features.clear()
            self._last_predictions.clear()


class CrossSectionalPredictor:
    """截面预测器

    专门用于横截面因子排名预测。
    """

    def __init__(self, predictor: Predictor):
        self._predictor = predictor

    def predict_and_rank(
        self,
        X: pd.DataFrame,
        symbols: list[str],
        ascending: bool = False,
    ) -> pd.DataFrame:
        """预测并排名"""
        result = self._predictor.predict(X, symbols)

        df = pd.DataFrame({
            "symbol": symbols,
            "prediction": result.predictions,
        })

        # 排名
        df["rank"] = df["prediction"].rank(ascending=ascending)
        df["percentile"] = df["rank"] / len(df)

        # 分组
        df["quantile"] = pd.qcut(
            df["prediction"],
            q=5,
            labels=["Q1", "Q2", "Q3", "Q4", "Q5"],
            duplicates="drop",
        )

        return df.sort_values("rank")

    def get_long_short_portfolio(
        self,
        X: pd.DataFrame,
        symbols: list[str],
        top_n: int = 50,
        bottom_n: int = 50,
    ) -> tuple[list[str], list[str]]:
        """获取多空组合"""
        ranked = self.predict_and_rank(X, symbols, ascending=False)

        long_symbols = ranked.head(top_n)["symbol"].tolist()
        short_symbols = ranked.tail(bottom_n)["symbol"].tolist()

        return long_symbols, short_symbols
