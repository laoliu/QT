"""统一推理接口 + SHA-256 模型完整性校验。"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """模型预测结果"""
    predictions: np.ndarray
    symbols: list[str]
    timestamp: str = ""
    model_id: str = ""
    confidence: Optional[np.ndarray] = None
    feature_contributions: Optional[dict[str, np.ndarray]] = None

    def to_dataframe(self) -> pd.DataFrame:
        df = pd.DataFrame({"symbol": self.symbols, "score": self.predictions})
        if self.confidence is not None:
            df["confidence"] = self.confidence
        return df.sort_values("score", ascending=False).reset_index(drop=True)

    def get_top_n(self, n: int = 30, ascending: bool = False) -> pd.DataFrame:
        df = self.to_dataframe()
        return df.head(n) if not ascending else df.tail(n)


@dataclass
class PredictorConfig:
    model_path: str = ""
    batch_size: int = 1024
    output_type: str = "rank"  # raw | rank | zscore
    cache_ttl_seconds: int = 300


class Predictor:
    """统一推理接口"""

    def __init__(self, config: PredictorConfig | None = None):
        self._config = config or PredictorConfig()
        self._model: Any = None
        self._model_type: str = ""

    def load(self, path: str, verify_hash: bool = True) -> None:
        """加载模型，可选 SHA-256 完整性校验。"""
        import joblib

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")

        if verify_hash:
            hash_path = p.with_suffix(p.suffix + ".sha256")
            if hash_path.exists():
                expected = hash_path.read_text().strip()
                actual = self._compute_hash(p)
                if actual != expected:
                    raise ValueError(
                        f"模型完整性校验失败: expected={expected[:16]}... actual={actual[:16]}..."
                    )
                logger.info("模型完整性校验通过: %s", path)

        self._model = joblib.load(path)
        logger.info("模型已加载: %s", path)

    def predict(self, X: pd.DataFrame | np.ndarray, symbols: list[str] | None = None) -> PredictionResult:
        """执行预测。"""
        if self._model is None:
            raise RuntimeError("模型未加载")

        if hasattr(self._model, "predict"):
            raw_pred = self._model.predict(X)
        elif isinstance(self._model, dict) and "model" in self._model:
            raw_pred = self._model["model"].predict(X)
        else:
            raise RuntimeError("无法识别的模型格式")

        if symbols is None:
            symbols = [f"s{i}" for i in range(len(raw_pred))]

        output_type = self._config.output_type
        if output_type == "rank":
            from scipy.stats import rankdata
            predictions = rankdata(raw_pred) / len(raw_pred)
        elif output_type == "zscore":
            mean, std = raw_pred.mean(), raw_pred.std()
            predictions = (raw_pred - mean) / std if std > 0 else raw_pred * 0
        else:
            predictions = raw_pred

        return PredictionResult(
            predictions=predictions,
            symbols=symbols,
            model_id=self._config.model_path,
        )

    @staticmethod
    def save_model_hash(path: str) -> str:
        """生成并保存模型 SHA-256 摘要。"""
        p = Path(path)
        h = Predictor._compute_hash(p)
        hash_path = p.with_suffix(p.suffix + ".sha256")
        hash_path.write_text(h)
        logger.info("模型 hash 已保存: %s", hash_path)
        return h

    @staticmethod
    def _compute_hash(path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
