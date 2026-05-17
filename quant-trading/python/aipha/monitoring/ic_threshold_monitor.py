"""ML 模型 IC 阈值监控 — 滚动 IC 绝对值检查。

对生产 ML 模型的 OOS 预测质量进行月度监控：
- 滚动 3 个月 IC < 0.10 → 降权预警 (WARNING)
- 滚动 3 个月 IC < 0.05 → 强制审查 (CRITICAL)
- 滚动 6 个月 IC < 0.08 → 考虑暂停 (CRITICAL)

理论基础：
- Gu, Kelly, Xiu (2020): 金融 ML R² 通常 < 5%，IC=0.10 对应 R²≈1%
- 原则 B Alpha 衰减检验：近 3 年 SR < 全量 × 40% 为衰减
- DR-AR-004: 近 3 年滚动 SR < 全量 SR × 40% 触发降权预警

用法::

    from aipha.monitoring.ic_threshold_monitor import ICThresholdMonitor
    monitor = ICThresholdMonitor(model_dir="models/ml_csi2000_lgb_20d_r40")
    result = monitor.check(lookback_months=3)
    if result.alert_level != "healthy":
        send_feishu_alert(...)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ICCheckResult:
    """IC 阈值检查结果。"""

    model_name: str
    check_date: str
    lookback_months: int
    rolling_ic: float
    rolling_icir: float
    n_days: int
    alert_level: str  # "healthy" | "warning" | "critical"
    message: str
    monthly_ic: dict[str, float] = field(default_factory=dict)
    full_period_ic: Optional[float] = None
    decay_ratio: Optional[float] = None


# ---------------------------------------------------------------------------
# 阈值配置
# ---------------------------------------------------------------------------

IC_THRESHOLD_WARNING = 0.10  # 3 个月 IC < 此值 → WARNING
IC_THRESHOLD_CRITICAL = 0.05  # 3 个月 IC < 此值 → CRITICAL
IC_THRESHOLD_6M_CRITICAL = 0.08  # 6 个月 IC < 此值 → CRITICAL
DECAY_RATIO_WARNING = 0.40  # 近期 IC / 全量 IC < 此值 → WARNING (原则B)


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class ICThresholdMonitor:
    """ML 模型 IC 绝对阈值监控器。

    从 OOS 预测文件加载历史预测，计算滚动 IC 并与阈值比较。
    支持两种数据源：
    1. OOS predictions parquet（离线历史验证）
    2. Daily prediction logs（在线日常监控，需 scorer 记录分数）
    """

    def __init__(
        self,
        model_dir: str | Path,
        *,
        ic_warning: float = IC_THRESHOLD_WARNING,
        ic_critical: float = IC_THRESHOLD_CRITICAL,
        ic_6m_critical: float = IC_THRESHOLD_6M_CRITICAL,
        decay_ratio_warning: float = DECAY_RATIO_WARNING,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._ic_warning = ic_warning
        self._ic_critical = ic_critical
        self._ic_6m_critical = ic_6m_critical
        self._decay_ratio_warning = decay_ratio_warning
        self._oos_df: Optional[pd.DataFrame] = None

    @property
    def model_name(self) -> str:
        return self._model_dir.name

    def _load_oos_predictions(self) -> pd.DataFrame:
        """加载 OOS 预测文件。"""
        if self._oos_df is not None:
            return self._oos_df

        oos_path = self._model_dir / "oos_predictions.parquet"
        if not oos_path.exists():
            raise FileNotFoundError(
                f"OOS predictions not found: {oos_path}"
            )
        df = pd.read_parquet(oos_path)
        # 标准化列名
        if "datetime" in df.columns and "date" not in df.columns:
            df = df.rename(columns={"datetime": "date"})
        df["date"] = pd.to_datetime(df["date"])
        self._oos_df = df
        return df

    def compute_daily_ic(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.Series:
        """计算逐日截面 IC（Spearman rank correlation）。

        Returns:
            pd.Series, index=date, values=IC
        """
        df = self._load_oos_predictions()

        if start_date:
            df = df[df["date"] >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df["date"] <= pd.Timestamp(end_date)]

        if df.empty:
            return pd.Series(dtype=float)

        # 逐日计算 Spearman IC
        ic_series = df.groupby("date").apply(
            lambda g: g["pred_score"].corr(g["fwd_return"], method="spearman")
            if len(g) >= 10 else np.nan,
            include_groups=False,
        )
        ic_series = ic_series.dropna()
        ic_series.name = "ic"
        return ic_series

    def check(
        self,
        lookback_months: int = 3,
        reference_date: Optional[str] = None,
    ) -> ICCheckResult:
        """执行 IC 阈值检查。

        Args:
            lookback_months: 滚动窗口月数（默认 3）
            reference_date: 检查截止日期（默认 OOS 最后日期）

        Returns:
            ICCheckResult 含 alert_level 和诊断信息
        """
        df = self._load_oos_predictions()

        if reference_date:
            ref_dt = pd.Timestamp(reference_date)
        else:
            ref_dt = df["date"].max()

        # 计算全量 IC
        full_ic_series = self.compute_daily_ic()
        full_ic = float(full_ic_series.mean()) if len(full_ic_series) > 0 else 0.0

        # 计算滚动窗口 IC
        start_dt = ref_dt - pd.DateOffset(months=lookback_months)
        window_ic_series = full_ic_series[
            (full_ic_series.index >= start_dt) & (full_ic_series.index <= ref_dt)
        ]

        if len(window_ic_series) < 10:
            return ICCheckResult(
                model_name=self.model_name,
                check_date=ref_dt.strftime("%Y-%m-%d"),
                lookback_months=lookback_months,
                rolling_ic=0.0,
                rolling_icir=0.0,
                n_days=len(window_ic_series),
                alert_level="insufficient_data",
                message=f"仅 {len(window_ic_series)} 天数据，不足以评估",
                full_period_ic=full_ic,
            )

        rolling_ic = float(window_ic_series.mean())
        rolling_std = float(window_ic_series.std())
        rolling_icir = rolling_ic / rolling_std if rolling_std > 1e-9 else 0.0

        # 计算月度 IC 分解
        monthly_ic: dict[str, float] = {}
        for month_key, month_group in window_ic_series.groupby(
            window_ic_series.index.to_period("M")
        ):
            monthly_ic[str(month_key)] = float(month_group.mean())

        # 计算衰减比
        decay_ratio = (
            rolling_ic / full_ic if abs(full_ic) > 1e-9 else None
        )

        # 确定告警级别
        alert_level = "healthy"
        message = f"滚动{lookback_months}个月 IC={rolling_ic:.4f}, 状态正常"

        if rolling_ic < self._ic_critical:
            alert_level = "critical"
            message = (
                f"滚动{lookback_months}个月 IC={rolling_ic:.4f} < "
                f"{self._ic_critical}，建议强制审查/暂停"
            )
        elif rolling_ic < self._ic_warning:
            alert_level = "warning"
            message = (
                f"滚动{lookback_months}个月 IC={rolling_ic:.4f} < "
                f"{self._ic_warning}，建议降权"
            )
        elif decay_ratio is not None and decay_ratio < self._decay_ratio_warning:
            alert_level = "warning"
            message = (
                f"IC 衰减比={decay_ratio:.2f} < {self._decay_ratio_warning}"
                f"（近期 IC={rolling_ic:.4f} vs 全量 IC={full_ic:.4f}）"
            )

        # 6 个月窗口附加检查
        if lookback_months <= 3 and alert_level == "healthy":
            start_6m = ref_dt - pd.DateOffset(months=6)
            ic_6m = full_ic_series[
                (full_ic_series.index >= start_6m)
                & (full_ic_series.index <= ref_dt)
            ]
            if len(ic_6m) >= 20:
                ic_6m_mean = float(ic_6m.mean())
                if ic_6m_mean < self._ic_6m_critical:
                    alert_level = "warning"
                    message = (
                        f"滚动6个月 IC={ic_6m_mean:.4f} < "
                        f"{self._ic_6m_critical}，趋势恶化"
                    )

        return ICCheckResult(
            model_name=self.model_name,
            check_date=ref_dt.strftime("%Y-%m-%d"),
            lookback_months=lookback_months,
            rolling_ic=rolling_ic,
            rolling_icir=rolling_icir,
            n_days=len(window_ic_series),
            alert_level=alert_level,
            message=message,
            monthly_ic=monthly_ic,
            full_period_ic=full_ic,
            decay_ratio=decay_ratio,
        )

    def check_with_realized_returns(
        self,
        predictions_log: Path,
        market_data_provider,
        lookback_months: int = 3,
    ) -> ICCheckResult:
        """使用日常预测日志 + 实际收益计算 IC（在线模式）。"""
        raise NotImplementedError(
            "Online IC check requires prediction logging. "
            "Use check() with OOS predictions for now."
        )
