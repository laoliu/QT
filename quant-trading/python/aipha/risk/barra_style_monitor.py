"""Barra 风格暴露监控器 — 简化 5 因子模型 (O6)

计算组合级风格暴露度，用于月度监控与风险报告。

5 个核心风格因子:
- SIZE:      ln(market_cap)，截面 z-score
- BM:        book-to-market (EP 代替)，截面 z-score
- MOM:       12-1 月动量，截面 z-score
- VOL:       20 日收益波动率，截面 z-score
- LIQUIDITY: 20 日平均换手率，截面 z-score

组合暴露 = Σ(w_i × z_i)，其中 w_i 为持仓权重，z_i 为股票的因子 z 分。

参考:
- Barra CNE5: SIZE, BM, MOMENTUM, RESIDUAL_VOL, LIQUIDITY, ...
- 本模块为简化版，后续可扩展至 CNE5/CNE6 完整因子集
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd


# ── 数据结构 ──────────────────────────────────────────────────

STYLE_FACTORS = ["SIZE", "BM", "MOM", "VOL", "LIQUIDITY"]


def _zscore(series: pd.Series) -> pd.Series:
    """截面 z-score 标准化（MAD robust）"""
    median = series.median()
    mad = (series - median).abs().median()
    if mad < 1e-10:
        return pd.Series(0.0, index=series.index)
    # MAD to σ conversion: σ ≈ 1.4826 × MAD
    sigma = 1.4826 * mad
    z = (series - median) / sigma
    return z.clip(-3.0, 3.0)


@dataclass
class StyleExposure:
    """单日组合风格暴露"""

    trade_date: date
    exposures: dict[str, float]  # {factor_name: exposure_value}
    n_holdings: int = 0
    total_weight: float = 0.0


@dataclass
class ExposureAlert:
    """暴露超限告警"""

    factor: str
    exposure: float
    threshold: float
    direction: str  # "long" or "short"


@dataclass
class StyleMonitorConfig:
    """监控配置"""

    alert_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "SIZE": 1.5,
            "BM": 1.5,
            "MOM": 2.0,
            "VOL": 2.0,
            "LIQUIDITY": 1.5,
        }
    )


class BarraStyleMonitor:
    """Barra 风格暴露监控器"""

    def __init__(self, config: StyleMonitorConfig | None = None):
        self._config = config or StyleMonitorConfig()
        self._history: list[StyleExposure] = []

    def compute_style_scores(
        self, df: pd.DataFrame,
    ) -> pd.DataFrame:
        """计算截面 z-score 风格因子

        Args:
            df: 必须包含列:
                - ts_code: 股票代码
                - close: 收盘价
                - total_mv: 总市值（万元）
                - pe_ttm: 滚动市盈率
                - turnover_rate: 换手率
                须提供足够的历史行（≥250 天）供动量/波动率计算

        Returns:
            DataFrame with columns: ts_code + STYLE_FACTORS (z-scores)
        """
        result = df[["ts_code"]].copy()

        # SIZE: ln(total_mv)
        if "total_mv" in df.columns:
            raw = np.log(df["total_mv"].clip(lower=1))
            result["SIZE"] = _zscore(raw)
        else:
            result["SIZE"] = 0.0

        # BM: 1/PE (book-to-market proxy)
        if "pe_ttm" in df.columns:
            pe = df["pe_ttm"].replace(0, np.nan)
            raw = 1.0 / pe
            result["BM"] = _zscore(raw)
        else:
            result["BM"] = 0.0

        # MOM: 过去 12 个月累计收益（需外部预计算或用 pct_change_252）
        if "mom_12m" in df.columns:
            result["MOM"] = _zscore(df["mom_12m"])
        elif "pct_chg" in df.columns:
            # 用当日收益变化的截面排名代替（简化）
            result["MOM"] = _zscore(df["pct_chg"])
        else:
            result["MOM"] = 0.0

        # VOL: 20 日收益波动率
        if "vol_20d" in df.columns:
            result["VOL"] = _zscore(df["vol_20d"])
        else:
            result["VOL"] = 0.0

        # LIQUIDITY: 20 日平均换手率
        if "turnover_rate" in df.columns:
            result["LIQUIDITY"] = _zscore(df["turnover_rate"])
        else:
            result["LIQUIDITY"] = 0.0

        return result

    def compute_portfolio_exposure(
        self,
        weights: dict[str, float],
        style_scores: pd.DataFrame,
        trade_date: date | None = None,
    ) -> StyleExposure:
        """计算组合风格暴露

        Args:
            weights: {ts_code: weight}，权重之和应为 1.0
            style_scores: compute_style_scores 的输出
            trade_date: 交易日期

        Returns:
            StyleExposure 暴露快照
        """
        scores = style_scores.set_index("ts_code")
        total_w = sum(weights.values())
        exposures: dict[str, float] = {}
        for factor in STYLE_FACTORS:
            if factor not in scores.columns:
                exposures[factor] = 0.0
                continue
            exp = 0.0
            for code, w in weights.items():
                if code in scores.index:
                    val = scores.loc[code, factor]
                    if pd.notna(val):
                        exp += (w / total_w) * val
            exposures[factor] = round(exp, 4)

        result = StyleExposure(
            trade_date=trade_date or date.today(),
            exposures=exposures,
            n_holdings=len(weights),
            total_weight=round(total_w, 4),
        )
        self._history.append(result)
        return result

    def check_alerts(
        self, exposure: StyleExposure,
    ) -> list[ExposureAlert]:
        """检查暴露是否超限"""
        alerts: list[ExposureAlert] = []
        for factor, threshold in self._config.alert_thresholds.items():
            exp = exposure.exposures.get(factor, 0.0)
            if abs(exp) > threshold:
                direction = "long" if exp > 0 else "short"
                alerts.append(
                    ExposureAlert(
                        factor=factor,
                        exposure=exp,
                        threshold=threshold,
                        direction=direction,
                    )
                )
        return alerts

    def get_history_df(self) -> pd.DataFrame:
        """暴露历史转 DataFrame"""
        if not self._history:
            return pd.DataFrame()
        rows = []
        for snap in self._history:
            row = {"trade_date": snap.trade_date, "n_holdings": snap.n_holdings}
            row.update(snap.exposures)
            rows.append(row)
        return pd.DataFrame(rows)

    def generate_report(self) -> dict[str, Any]:
        """生成监控报告摘要"""
        if not self._history:
            return {"status": "no_data", "n_snapshots": 0}
        latest = self._history[-1]
        alerts = self.check_alerts(latest)
        hist_df = self.get_history_df()
        report: dict[str, Any] = {
            "status": "alert" if alerts else "normal",
            "n_snapshots": len(self._history),
            "latest_date": str(latest.trade_date),
            "latest_exposures": latest.exposures,
            "alerts": [
                {"factor": a.factor, "exposure": a.exposure,
                 "threshold": a.threshold, "direction": a.direction}
                for a in alerts
            ],
        }
        # 历史统计
        stats: dict[str, dict[str, float]] = {}
        for f in STYLE_FACTORS:
            if f in hist_df.columns:
                col = hist_df[f].dropna()
                stats[f] = {
                    "mean": round(float(col.mean()), 4),
                    "std": round(float(col.std()), 4),
                    "max_abs": round(float(col.abs().max()), 4),
                }
        report["history_stats"] = stats
        return report
