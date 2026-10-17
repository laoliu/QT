from __future__ import annotations

import pytest

from aipha.screeners.ml_ranking_screener import MLRankingScreener


def test_legacy_ml_ranking_screener_is_identified_as_heuristic() -> None:
    screener = MLRankingScreener()
    assert "不加载训练模型" in screener.screener_description
    assert "GRU" not in screener.screener_description

    result = screener.screen({
        "600000.SH": {
            "name": "示例股份",
            "listing_days": 1000,
            "return_20d": 0.12,
            "return_60d": 0.08,
            "pe": 12,
            "pb": 1.2,
            "roe": 0.15,
            "net_margin": 0.12,
            "volatility_20d": 0.02,
            "turnover_rate": 3.0,
            "price_vs_ma20": 0.04,
            "price_vs_ma60": 0.08,
        },
    })
    assert len(result) == 1
    assert result[0].stock_id == "600000.SH"


def test_legacy_ml_ranking_screener_rejects_model_type_claims() -> None:
    screener = MLRankingScreener({"model_type": "gru_ranker"})
    with pytest.raises(ValueError, match="heuristic-only"):
        screener.screen({"600000.SH": {"listing_days": 1000}})
