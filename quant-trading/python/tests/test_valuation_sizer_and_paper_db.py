"""估值仓位控制 + paper_portfolios DB 表 的测试"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest


# ============================================================================
# ValuationPositionSizer 测试
# ============================================================================


class TestValuationConfig:
    """测试 ValuationConfig 默认值"""

    def test_default_disabled(self):
        from aipha.risk.position_sizing import ValuationConfig
        cfg = ValuationConfig()
        assert cfg.enabled is False
        assert cfg.metric == "premium_percentile"
        assert cfg.lookback_days == 500

    def test_custom_config(self):
        from aipha.risk.position_sizing import ValuationConfig
        cfg = ValuationConfig(
            enabled=True, high_threshold=0.75, low_threshold=0.25,
            high_scale=0.40, low_scale=1.0,
        )
        assert cfg.high_threshold == 0.75
        assert cfg.high_scale == 0.40


class TestValuationPositionSizer:
    """测试 ValuationPositionSizer"""

    def _make_sizer(self, **kwargs):
        from aipha.risk.position_sizing import ValuationPositionSizer, ValuationConfig
        defaults = {"enabled": True, "lookback_days": 50, "smooth_days": 1}
        defaults.update(kwargs)
        cfg = ValuationConfig(**defaults)
        return ValuationPositionSizer(cfg)

    def test_disabled_returns_one(self):
        from aipha.risk.position_sizing import ValuationPositionSizer, ValuationConfig
        sizer = ValuationPositionSizer(ValuationConfig(enabled=False))
        assert sizer.compute_scale([10.0, 20.0, 30.0]) == 1.0

    def test_empty_market_values(self):
        sizer = self._make_sizer()
        assert sizer.compute_scale([]) == 0.80  # mid_scale

    def test_too_few_values(self):
        sizer = self._make_sizer()
        assert sizer.compute_scale([10.0]) == 0.80

    def test_insufficient_history_returns_mid(self):
        sizer = self._make_sizer()
        # 只喂一天数据，历史不足
        scale = sizer.compute_scale([10.0, 20.0, 30.0, 40.0, 50.0])
        assert scale == 0.80  # mid_scale

    def test_low_valuation_full_position(self):
        """低估值时应接近满仓"""
        sizer = self._make_sizer(lookback_days=30)
        # 先喂入30天高估值数据建立历史
        for _ in range(30):
            sizer.compute_scale(list(np.random.uniform(50, 80, 20)))
        # 然后喂入低估值数据
        scale = sizer.compute_scale(list(np.random.uniform(5, 15, 20)))
        # 应该接近 low_scale (1.0)
        assert scale >= 0.85

    def test_high_valuation_reduced_position(self):
        """高估值时应减仓"""
        sizer = self._make_sizer(lookback_days=30)
        # 先喂入30天低估值数据建立历史
        for _ in range(30):
            sizer.compute_scale(list(np.random.uniform(5, 15, 20)))
        # 然后喂入极高估值数据
        scale = sizer.compute_scale(list(np.random.uniform(80, 120, 20)))
        # 应该接近 high_scale (0.50)
        assert scale <= 0.65

    def test_reset_clears_history(self):
        sizer = self._make_sizer()
        for _ in range(25):
            sizer.compute_scale(list(np.random.uniform(10, 50, 20)))
        assert len(sizer._history) == 25
        sizer.reset()
        assert len(sizer._history) == 0

    def test_current_percentile(self):
        sizer = self._make_sizer(lookback_days=20)
        # 初始无历史
        assert sizer.current_percentile is None
        # 喂入数据
        for i in range(20):
            sizer.compute_scale(list(np.full(10, float(i * 5))))
        pct = sizer.current_percentile
        assert pct is not None
        assert 0.0 <= pct <= 1.0


class TestPositionSizingOverlayWithValuation:
    """测试 PositionSizingOverlay 集成估值"""

    def test_overlay_without_valuation(self):
        from aipha.risk.position_sizing import PositionSizingOverlay
        overlay = PositionSizingOverlay()
        scale = overlay.compute_scale([0.01, -0.005, 0.008], 1000.0)
        assert 0.0 < scale <= 1.0

    def test_overlay_with_valuation(self):
        from aipha.risk.position_sizing import (
            PositionSizingOverlay, ValuationConfig,
        )
        overlay = PositionSizingOverlay(
            val_config=ValuationConfig(enabled=True, lookback_days=30, smooth_days=1),
        )
        overlay.reset()
        # 无估值数据时 val_scale 应为 1.0（不影响）
        scale_no_val = overlay.compute_scale([0.01], 1000.0)
        scale_with_val = overlay.compute_scale(
            [0.01], 1000.0, market_values=[10, 20, 30, 40, 50],
        )
        # 两者都应该在合理范围
        assert 0.0 < scale_no_val <= 1.0
        assert 0.0 < scale_with_val <= 1.0


# ============================================================================
# paper_portfolios DB 表测试
# ============================================================================


class TestPaperPortfolioDB:
    """测试 ResultStore paper_portfolios CRUD"""

    def _make_store(self, tmp_path):
        from aipha.backtest.result_store import ResultStore
        return ResultStore(db_path=tmp_path / "test.db")

    def test_upsert_and_get(self, tmp_path):
        store = self._make_store(tmp_path)
        store.upsert_paper_portfolio({
            "portfolio_id": "test_p1",
            "display_name": "Test Portfolio 1",
            "strategies": ["s1", "s2"],
            "weights": {"s1": 0.6, "s2": 0.4},
            "initial_capital": 500000.0,
        })
        p = store.get_paper_portfolio("test_p1")
        assert p is not None
        assert p["display_name"] == "Test Portfolio 1"
        assert p["strategies"] == ["s1", "s2"]
        assert p["weights"]["s1"] == 0.6
        assert p["initial_capital"] == 500000.0
        store.close()

    def test_list_portfolios(self, tmp_path):
        store = self._make_store(tmp_path)
        for i in range(3):
            store.upsert_paper_portfolio({
                "portfolio_id": f"p{i}",
                "display_name": f"Portfolio {i}",
                "strategies": [f"s{i}"],
                "weights": {f"s{i}": 1.0},
            })
        plist = store.list_paper_portfolios()
        assert len(plist) == 3
        store.close()

    def test_update_status(self, tmp_path):
        store = self._make_store(tmp_path)
        store.upsert_paper_portfolio({
            "portfolio_id": "p_status",
            "display_name": "Status Test",
            "strategies": ["s1"],
            "weights": {"s1": 1.0},
        })
        assert store.update_paper_portfolio_status("p_status", "running")
        p = store.get_paper_portfolio("p_status")
        assert p["paper_status"] == "running"
        store.close()

    def test_invalid_status_raises(self, tmp_path):
        store = self._make_store(tmp_path)
        store.upsert_paper_portfolio({
            "portfolio_id": "p_bad",
            "display_name": "Bad Status",
            "strategies": ["s1"],
            "weights": {"s1": 1.0},
        })
        with pytest.raises(ValueError):
            store.update_paper_portfolio_status("p_bad", "invalid_status")
        store.close()

    def test_soft_delete(self, tmp_path):
        store = self._make_store(tmp_path)
        store.upsert_paper_portfolio({
            "portfolio_id": "p_del",
            "display_name": "To Delete",
            "strategies": ["s1"],
            "weights": {"s1": 1.0},
        })
        assert store.count_paper_portfolios() == 1
        store.soft_delete_paper_portfolio("p_del")
        assert store.count_paper_portfolios() == 0
        # 包含非活跃
        assert store.count_paper_portfolios(active_only=False) == 1
        store.close()

    def test_upsert_idempotent(self, tmp_path):
        store = self._make_store(tmp_path)
        portfolio = {
            "portfolio_id": "p_idem",
            "display_name": "Idempotent Test",
            "strategies": ["s1"],
            "weights": {"s1": 1.0},
        }
        store.upsert_paper_portfolio(portfolio)
        store.upsert_paper_portfolio(portfolio)
        assert store.count_paper_portfolios() == 1
        store.close()

    def test_import_from_json(self, tmp_path):
        import json
        registry = {
            "version": "2.0",
            "entries": [
                {
                    "entry_id": "j_test",
                    "display_name": "JSON Import Test",
                    "strategies": ["s1", "s2"],
                    "weights": {"s1": 0.5, "s2": 0.5},
                    "initial_capital": 100000,
                    "registered_at": "2026-01-01T00:00:00",
                }
            ],
        }
        json_path = tmp_path / "registry.json"
        json_path.write_text(json.dumps(registry), encoding="utf-8")

        from aipha.backtest.result_store import ResultStore, import_paper_registry_from_json
        store = ResultStore(db_path=tmp_path / "test.db")
        count = import_paper_registry_from_json(json_path, store)
        assert count == 1
        p = store.get_paper_portfolio("j_test")
        assert p is not None
        assert p["strategies"] == ["s1", "s2"]
        store.close()

    def test_schema_v4_migration(self, tmp_path):
        """确认 schema 版本已升级到 4"""
        store = self._make_store(tmp_path)
        ver = store.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(ver) >= 4
        store.close()
