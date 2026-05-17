"""工具模块

提供通用工具函数：
- reproducibility: 可复现性与实验指纹
- vectorized: 向量化计算函数
"""

from aipha.utils.reproducibility import (
    ExperimentFingerprint,
    create_fingerprint,
    load_fingerprint,
    save_fingerprint,
    set_global_seed,
    verify_fingerprint,
)
from aipha.utils.vectorized import (
    batch_compute_correlation,
    batch_compute_covariance,
    batch_compute_returns,
    batch_compute_volatility,
    compute_bollinger_bands,
    compute_exponential_ma,
    compute_macd,
    compute_max_drawdown,
    compute_momentum,
    compute_moving_average,
    compute_returns,
    compute_rolling_std,
    compute_rsi,
    compute_sharpe_ratio,
    compute_volatility,
)

__all__ = [
    # reproducibility
    "ExperimentFingerprint",
    "set_global_seed",
    "create_fingerprint",
    "verify_fingerprint",
    "save_fingerprint",
    "load_fingerprint",
    # vectorized
    "compute_returns",
    "compute_volatility",
    "compute_moving_average",
    "compute_exponential_ma",
    "compute_rolling_std",
    "compute_momentum",
    "compute_rsi",
    "compute_macd",
    "compute_bollinger_bands",
    "compute_sharpe_ratio",
    "compute_max_drawdown",
    "batch_compute_returns",
    "batch_compute_volatility",
    "batch_compute_correlation",
    "batch_compute_covariance",
]
