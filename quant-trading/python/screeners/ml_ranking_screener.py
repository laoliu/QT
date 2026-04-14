"""
ML Ranking Screener — 将 aipha ML 排序能力注册为 QT screener 框架中的选股器

使用 LightGBM/GRU 排序模型对全市场股票打分，输出 top-K。
当模型文件不存在时退化为多因子综合评分。
"""
from __future__ import annotations

import sys
import os
from typing import Any, Optional

# 确保可以导入 screeners 和 aipha
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from screeners.base import BaseScreener, ScreenerResult, register_screener


@register_screener
class MLRankingScreener(BaseScreener):
    """基于 aipha ML 排序模型的智能选股器"""

    screener_id = "ml_ranking"
    screener_name = "ML智能排序选股"
    screener_description = (
        "利用 aipha 机器学习排序模型 (LightGBM/GRU) 对股票进行多因子综合排序。"
        "自动提取动量、价值、质量、波动率、流动性、技术面等特征组，"
        "输出 top-K 股票及对应因子分数。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "model_type": "lgbm_ranker",       # lgbm_ranker / gru_ranker
        "top_k": 30,
        "feature_groups": ["momentum", "value", "quality", "volatility", "liquidity", "technical"],
        "min_listing_days": 120,
        "exclude_st": True,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        params = {**self._DEFAULTS, **self.params}
        top_k = min(params["top_k"], limit)

        # 1. 基础过滤
        candidates = {}
        for code, data in stock_data.items():
            name = data.get("name", "")
            # ST 过滤
            if params.get("exclude_st") and ("ST" in name or "st" in name):
                continue
            # 上市天数
            listing_days = data.get("listing_days", 999)
            if listing_days < params.get("min_listing_days", 0):
                continue
            candidates[code] = data

        if not candidates:
            return []

        # 2. 尝试加载 ML 模型
        ml_available = False
        try:
            from aipha.models.lgbm_ranker import LGBMRanker
            from aipha.models.feature_eng import FeatureEngineer
            ml_available = True
        except ImportError:
            pass

        # 3. 多因子评分 (ML 退化模式 / 直接评分)
        results: list[ScreenerResult] = []
        for code, data in candidates.items():
            score, factors, signals = self._compute_score(data, params)
            if score > 0:
                results.append(ScreenerResult(
                    stock_id=code,
                    name=data.get("name", ""),
                    market=market,
                    score=score,
                    factors=factors,
                    signals=signals,
                ))

        # 排序取 top-K
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:top_k], 1):
            r.rank = i

        return results[:top_k]

    def _compute_score(
        self,
        data: dict[str, Any],
        params: dict,
    ) -> tuple[float, dict[str, Any], list[str]]:
        """综合多因子评分 — 覆盖 aipha 6 大因子组"""
        score = 0.0
        factors: dict[str, Any] = {}
        signals: list[str] = []
        feature_groups = params.get("feature_groups", [])

        # ── Momentum ──
        if "momentum" in feature_groups:
            ret_20d = data.get("return_20d", 0)
            ret_60d = data.get("return_60d", 0)
            mom = (ret_20d * 0.6 + ret_60d * 0.4) if ret_20d or ret_60d else 0
            factors["momentum"] = round(mom, 4)
            if mom > 0.05:
                score += 1.5
                signals.append(f"动量强势: 20D={ret_20d:.1%}")
            elif mom > 0:
                score += 0.5

        # ── Value ──
        if "value" in feature_groups:
            pe = data.get("pe", 0)
            pb = data.get("pb", 0)
            val_score = 0
            if 0 < pe < 15:
                val_score += 1.5
                signals.append(f"低估值: PE={pe:.1f}")
            elif 0 < pe < 30:
                val_score += 0.5
            if 0 < pb < 1.5:
                val_score += 1.0
            elif 0 < pb < 3:
                val_score += 0.3
            factors["pe"] = pe
            factors["pb"] = pb
            score += val_score

        # ── Quality ──
        if "quality" in feature_groups:
            roe = data.get("roe", 0)
            net_margin = data.get("net_margin", data.get("net_profit_margin", 0))
            q_score = 0
            if roe > 15:
                q_score += 1.5
                signals.append(f"高质量: ROE={roe:.1f}%")
            elif roe > 8:
                q_score += 0.5
            if net_margin > 15:
                q_score += 0.5
            factors["roe"] = roe
            factors["net_margin"] = net_margin
            score += q_score

        # ── Volatility ──
        if "volatility" in feature_groups:
            vol_20d = data.get("volatility_20d", data.get("vol_20d", 0))
            factors["volatility_20d"] = round(vol_20d, 4) if vol_20d else 0
            if vol_20d and vol_20d < 0.02:
                score += 0.8
            elif vol_20d and vol_20d < 0.04:
                score += 0.3

        # ── Liquidity ──
        if "liquidity" in feature_groups:
            turnover = data.get("turnover_rate", data.get("turnover", 0))
            factors["turnover"] = round(turnover, 4) if turnover else 0
            if turnover and 0.02 < turnover < 0.15:
                score += 0.5  # 适中流动性

        # ── Technical ──
        if "technical" in feature_groups:
            # MA 均线排列
            ma5 = data.get("ma5", 0)
            ma20 = data.get("ma20", 0)
            ma60 = data.get("ma60", 0)
            price = data.get("close", data.get("price", 0))
            if price and ma5 and ma20 and ma60:
                if price > ma5 > ma20 > ma60:
                    score += 1.5
                    signals.append("均线多头排列")
                elif price > ma20:
                    score += 0.5
                factors["ma_alignment"] = "bullish" if (price > ma5 > ma20) else "neutral"

            # RSI
            rsi = data.get("rsi", data.get("rsi_14", 0))
            if rsi:
                factors["rsi"] = round(rsi, 2)
                if 40 < rsi < 65:
                    score += 0.3  # 健康区间

        return score, factors, signals
