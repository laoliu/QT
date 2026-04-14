"""估值过热风控层（ValuationGuard）测试

覆盖 R28-A3 实施的四层风控机制：
- 预警/减仓/清仓/回补 各级别判定
- adjust_weights 权重调整逻辑
- CrossAssetAllocator 集成
"""

import math
import pytest
from aipha.risk.valuation_guard import (
    SectorValuationConfig,
    ValuationAlert,
    ValuationAlertLevel,
    ValuationGuard,
    _SectorHistory,
)


# ====================================================================
# 辅助函数
# ====================================================================

def _make_default_config(
    sector_id: str = "csi1000", **overrides
) -> SectorValuationConfig:
    """构建默认配置，可覆盖任意字段."""
    defaults = dict(
        sector_id=sector_id,
        metric="pe_ttm",
        lookback_days=250,
        warn_percentile=0.80,
        reduce_percentile=0.90,
        clear_percentile=0.95,
        clear_pb_multiplier=3.0,
        recover_percentile=0.70,
        reduce_weight_scale=0.50,
        clear_weight_floor=0.05,
        smooth_days=1,
    )
    defaults.update(overrides)
    return SectorValuationConfig(**defaults)


def _feed_linear(hist: _SectorHistory, n: int, start: float = 10.0, step: float = 0.1):
    """向 SectorHistory 喂入线性递增数据."""
    for i in range(n):
        hist.update(start + i * step)


# ====================================================================
# _SectorHistory 基础测试
# ====================================================================

class TestSectorHistory:
    """_SectorHistory 单元测试."""

    def test_empty_has_no_data(self):
        cfg = _make_default_config()
        hist = _SectorHistory(cfg)
        assert not hist.has_enough_data
        assert hist.current_percentile is None
        assert hist.pb_median is None

    def test_percentile_after_enough_data(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        # 喂入100个递增值: 10.0, 10.1, ..., 19.9
        _feed_linear(hist, 100, start=10.0, step=0.1)
        assert hist.has_enough_data
        pct = hist.current_percentile
        assert pct is not None
        assert pct == 1.0  # 最后一个值是最大值 → 100%分位

    def test_percentile_at_max_is_one(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        _feed_linear(hist, 100, start=10.0, step=0.1)
        assert hist.current_percentile == 1.0

    def test_percentile_at_min_is_near_zero(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        # 喂入递减值：最后一个最小
        for i in range(100):
            hist.update(100.0 - i * 0.1)
        pct = hist.current_percentile
        assert pct is not None
        assert pct == pytest.approx(0.01, abs=0.02)

    def test_level_normal_when_low(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        # 喂入100个值，最后一个处于50%分位
        for i in range(100):
            hist.update(float(i))
        # 现在追加一个中间值
        hist.update(50.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.NORMAL

    def test_level_warning_at_80pct(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        for i in range(100):
            hist.update(float(i))
        # 追加 85 → 约86%分位（>80%，<90%）
        hist.update(85.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.WARNING

    def test_level_reduce_at_90pct(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        for i in range(100):
            hist.update(float(i))
        # 追加 95 → 约95%分位，但PB条件不满足 → REDUCE
        hist.update(95.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.REDUCE

    def test_level_clear_with_pb(self):
        cfg = _make_default_config(lookback_days=100, clear_pb_multiplier=3.0)
        hist = _SectorHistory(cfg)
        # 喂入PB=1.0 共100天
        for i in range(100):
            hist.update(float(i), pb_value=1.0)
        # 追加极端值：PE 99%分位 + PB=5.0（>3x中位数1.0）
        hist.update(99.0, pb_value=5.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.CLEAR

    def test_clear_without_pb_falls_to_reduce(self):
        cfg = _make_default_config(lookback_days=100)
        hist = _SectorHistory(cfg)
        for i in range(100):
            hist.update(float(i))
        # 99%分位但无PB数据 → 不满足清仓条件 → REDUCE
        hist.update(99.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.REDUCE

    def test_recover_hysteresis(self):
        """从 REDUCE 回落到 NORMAL 需低于 recover_percentile."""
        cfg = _make_default_config(lookback_days=100, recover_percentile=0.70)
        hist = _SectorHistory(cfg)
        for i in range(100):
            hist.update(float(i))
        # 进入 REDUCE
        hist.update(95.0)
        assert hist.determine_level() == ValuationAlertLevel.REDUCE
        # 回落到 75%分位，仍在 70%以上 → 保持 REDUCE
        hist.update(75.0)
        level = hist.determine_level()
        assert level != ValuationAlertLevel.NORMAL
        # 回落到 50%分位（<70%） → 恢复 NORMAL
        hist.update(50.0)
        level = hist.determine_level()
        assert level == ValuationAlertLevel.NORMAL

    def test_weight_scale_mapping(self):
        cfg = _make_default_config(
            lookback_days=100,
            reduce_weight_scale=0.50,
            clear_weight_floor=0.05,
        )
        hist = _SectorHistory(cfg)
        for i in range(100):
            hist.update(float(i), pb_value=1.0)
        # 正常时 scale=1.0
        hist.update(50.0, pb_value=1.0)
        assert hist.weight_scale() == 1.0
        # REDUCE 时 scale=0.50
        hist.update(95.0, pb_value=1.0)
        assert hist.weight_scale() == 0.50
        # CLEAR 时 scale=0.05
        hist.update(99.0, pb_value=5.0)
        assert hist.weight_scale() == 0.05

    def test_reset_clears_all(self):
        cfg = _make_default_config()
        hist = _SectorHistory(cfg)
        _feed_linear(hist, 100)
        assert hist.has_enough_data
        hist.reset()
        assert not hist.has_enough_data
        assert hist.current_percentile is None


# ====================================================================
# ValuationGuard 核心测试
# ====================================================================

class TestValuationGuard:
    """ValuationGuard 集成测试."""

    def test_disabled_returns_base_weights(self):
        guard = ValuationGuard(
            [_make_default_config("csi1000")], enabled=False,
        )
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        assert adj == base
        assert alerts == []

    def test_no_alerts_in_normal(self):
        guard = ValuationGuard([_make_default_config("csi1000")])
        # 喂入正常数据
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
        guard.update("csi1000", metric_value=50.0)  # 50% 分位
        adj, alerts = guard.adjust_weights(
            {"cb": 0.4, "csi1000": 0.3, "etf": 0.3},
        )
        assert alerts == []
        assert adj["csi1000"] == pytest.approx(0.3, abs=0.01)

    def test_reduce_halves_weight(self):
        guard = ValuationGuard([_make_default_config("csi1000")])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
        guard.update("csi1000", metric_value=95.0)  # >90% → REDUCE
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        assert len(alerts) == 1
        assert alerts[0].level == ValuationAlertLevel.REDUCE
        # csi1000 权重减半: 0.3 * 0.5 = 0.15
        assert adj["csi1000"] == pytest.approx(0.15, abs=0.01)

    def test_released_weight_distributed(self):
        guard = ValuationGuard([_make_default_config("csi1000")])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
        guard.update("csi1000", metric_value=95.0)
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        # 释放 0.15，按比例分配给 cb 和 etf
        total = sum(adj.values())
        assert total == pytest.approx(1.0, abs=0.01)
        # cb 和 etf 各获得释放权重的一半
        assert adj["cb"] > 0.4
        assert adj["etf"] > 0.3

    def test_clear_severe_scenario(self):
        guard = ValuationGuard([
            _make_default_config("csi1000", clear_weight_floor=0.05),
        ])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i), pb_value=1.0)
        guard.update("csi1000", metric_value=99.0, pb_value=5.0)
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        assert alerts[0].level == ValuationAlertLevel.CLEAR
        assert adj["csi1000"] == pytest.approx(0.05, abs=0.01)

    def test_multi_sector_mixed_levels(self):
        guard = ValuationGuard([
            _make_default_config("csi1000"),
            _make_default_config("etf"),
        ])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
            guard.update("etf", metric_value=float(i))
        # csi1000 过热，etf 正常
        guard.update("csi1000", metric_value=95.0)
        guard.update("etf", metric_value=50.0)
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        assert len(alerts) == 1
        assert adj["csi1000"] < 0.3
        assert adj["etf"] > 0.3  # etf 吸收释放权重

    def test_all_sectors_overheated(self):
        guard = ValuationGuard([
            _make_default_config("csi1000"),
            _make_default_config("etf"),
        ])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
            guard.update("etf", metric_value=float(i))
        guard.update("csi1000", metric_value=95.0)
        guard.update("etf", metric_value=95.0)
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj, alerts = guard.adjust_weights(base)
        assert len(alerts) == 2
        # cb 不被监控 → 吸收释放权重
        assert adj["cb"] > 0.4

    def test_recovery_restores_weight(self):
        guard = ValuationGuard([_make_default_config("csi1000")])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
        # 进入 REDUCE
        guard.update("csi1000", metric_value=95.0)
        base = {"cb": 0.4, "csi1000": 0.3, "etf": 0.3}
        adj1, _ = guard.adjust_weights(base)
        assert adj1["csi1000"] < 0.3
        # 回落到安全区间
        guard.update("csi1000", metric_value=50.0)
        adj2, alerts2 = guard.adjust_weights(base)
        assert adj2["csi1000"] == pytest.approx(0.3, abs=0.01)
        assert alerts2 == []

    def test_summary_dict(self):
        guard = ValuationGuard([_make_default_config("csi1000")])
        s = guard.summary()
        assert s["enabled"] is True
        assert "csi1000" in s["sectors"]

    def test_get_all_alerts(self):
        guard = ValuationGuard([
            _make_default_config("csi1000"),
            _make_default_config("etf"),
        ])
        for i in range(100):
            guard.update("csi1000", metric_value=float(i))
            guard.update("etf", metric_value=float(i))
        guard.update("csi1000", metric_value=95.0)
        guard.update("etf", metric_value=50.0)
        alerts = guard.get_all_alerts()
        assert len(alerts) == 1
        assert alerts[0].sector_id == "csi1000"
