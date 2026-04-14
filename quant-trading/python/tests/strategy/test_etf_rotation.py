"""ETF轮动策略单元测试

R111审计修复: 验证rank-normalize因子评分(A1)、on_fill逻辑(A2)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.etf_rotation import ETFRotationConfig, ETFRotationStrategy


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------

def _bar(symbol: str, close: float, volume: float = 1e6,
         is_suspended: bool = False) -> Bar:
    """构造最小 Bar 实例"""
    dt = datetime(2024, 6, 1, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=dt,
        end_ts=dt,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=volume,
        is_suspended=is_suspended,
    )


def _make_strategy(**overrides) -> ETFRotationStrategy:
    """创建策略实例，支持参数覆盖"""
    defaults = dict(
        top_k=3,
        rebalance_days=1,
        momentum_lookback=5,
        volatility_lookback=5,
        reversion_lookback=3,
        trend_ma_period=0,
        min_amount=0,
        weight_momentum=0.4,
        weight_volatility=0.3,
        weight_reversion=0.2,
        weight_volume=0.1,
        stop_loss_pct=0.0,       # 禁用止损
        trailing_stop_pct=0.0,   # 禁用追踪止损
    )
    defaults.update(overrides)
    cfg = ETFRotationConfig(**defaults)
    strategy = ETFRotationStrategy(config=cfg)
    return strategy


def _inject_history(strategy: ETFRotationStrategy,
                    prices: dict[str, list[float]],
                    volumes: dict[str, list[float]] | None = None):
    """注入价格和成交量历史"""
    strategy._price_history = {s: list(p) for s, p in prices.items()}
    if volumes:
        strategy._volume_history = {s: list(v) for s, v in volumes.items()}
    else:
        strategy._volume_history = {
            s: [1e6] * len(p) for s, p in prices.items()
        }


# ---------------------------------------------------------------------------
# A1: rank-normalize 因子评分测试
# ---------------------------------------------------------------------------

class TestRankNormalize:
    """验证 _rank_normalize_list 静态方法"""

    def test_basic_ascending(self):
        """升序输入 → 归一化后保持升序 [0, 0.5, 1.0]"""
        result = ETFRotationStrategy._rank_normalize_list([1.0, 2.0, 3.0])
        assert result == pytest.approx([0.0, 0.5, 1.0])

    def test_basic_descending(self):
        """降序输入 → 归一化后降序 [1.0, 0.5, 0.0]"""
        result = ETFRotationStrategy._rank_normalize_list([3.0, 2.0, 1.0])
        assert result == pytest.approx([1.0, 0.5, 0.0])

    def test_tie_handling(self):
        """相同值取平均排名"""
        result = ETFRotationStrategy._rank_normalize_list([1.0, 2.0, 2.0])
        # ranks: 0, (1+2)/2=1.5, (1+2)/2=1.5 → [0/2, 1.5/2, 1.5/2]
        assert result == pytest.approx([0.0, 0.75, 0.75])

    def test_single_element(self):
        """单元素 → [0.5]"""
        result = ETFRotationStrategy._rank_normalize_list([42.0])
        assert result == [0.5]

    def test_empty(self):
        """空列表 → []"""
        result = ETFRotationStrategy._rank_normalize_list([])
        assert result == []

    def test_all_same(self):
        """全部相同 → 平均排名"""
        result = ETFRotationStrategy._rank_normalize_list([5.0, 5.0, 5.0])
        assert result == pytest.approx([0.5, 0.5, 0.5])


class TestCalcRawFactors:
    """验证 _calc_raw_factors 原始因子方向正确"""

    def test_momentum_direction(self):
        """价格上涨 → 动量为正"""
        s = _make_strategy(momentum_lookback=5)
        hist = [1.0, 1.01, 1.02, 1.03, 1.04, 1.10]  # 涨10%
        s._volume_history = {"A": [1e6] * 6}
        mom, _, _, _ = s._calc_raw_factors("A", hist)
        assert mom > 0

    def test_volatility_direction(self):
        """高波动 → neg_vol 更负"""
        s = _make_strategy(volatility_lookback=5)
        # 低波动
        lo = [1.0, 1.001, 1.002, 1.003, 1.004, 1.005]
        # 高波动
        hi = [1.0, 1.05, 0.95, 1.10, 0.90, 1.0]
        s._volume_history = {"LO": [1e6] * 6, "HI": [1e6] * 6}
        _, neg_vol_lo, _, _ = s._calc_raw_factors("LO", lo)
        _, neg_vol_hi, _, _ = s._calc_raw_factors("HI", hi)
        # 低波动 → neg_vol 更接近 0 → 得分更高
        assert neg_vol_lo > neg_vol_hi

    def test_reversion_direction(self):
        """近期下跌 → 反转因子为正"""
        s = _make_strategy(reversion_lookback=3)
        # 近3天下跌
        hist = [1.0, 1.05, 1.10, 1.08, 1.00, 0.90]
        s._volume_history = {"A": [1e6] * 6}
        _, _, rev, _ = s._calc_raw_factors("A", hist)
        assert rev > 0  # -(0.90/1.08-1) = +0.167


class TestScoreETFs:
    """验证 _score_etfs 整体评分逻辑"""

    def test_rank_normalize_eliminates_scale_bias(self):
        """rank-normalize后各因子权重能有效发挥作用"""
        s = _make_strategy(
            weight_momentum=0.0,
            weight_volatility=1.0,  # 仅用波动率
            weight_reversion=0.0,
            weight_volume=0.0,
        )
        # A: 低波动高收益, B: 高波动低收益
        _inject_history(s, {
            "A": [1.0] * 4 + [1.001, 1.002, 1.003, 1.004, 1.005, 1.006],
            "B": [1.0] * 4 + [1.05, 0.95, 1.08, 0.92, 1.10, 0.88],
        })
        bars = {"A": _bar("A", 1.006), "B": _bar("B", 0.88)}
        ctx = StrategyContext(
            cash=1e6, positions={}, current_time=datetime(2024, 6, 1),
            nav=1e6,
        )
        scored = s._score_etfs(bars, ctx)
        # 低波动 A 应该排名更高
        assert scored[0][0] == "A"

    def test_score_respects_all_weights(self):
        """多因子权重都能影响排名结果"""
        s = _make_strategy(
            weight_momentum=0.5,
            weight_volatility=0.5,
            weight_reversion=0.0,
            weight_volume=0.0,
        )
        # A: 高动量+高波动, B: 低动量+低波动
        _inject_history(s, {
            "A": [1.0] * 4 + [1.01, 1.02, 1.03, 1.04, 1.05, 1.20],
            "B": [1.0] * 4 + [1.001, 1.002, 1.003, 1.004, 1.005, 1.01],
        })
        bars = {"A": _bar("A", 1.20), "B": _bar("B", 1.01)}
        ctx = StrategyContext(
            cash=1e6, positions={}, current_time=datetime(2024, 6, 1),
            nav=1e6,
        )
        scored = s._score_etfs(bars, ctx)
        # 两ETF各赢一个因子，rank均为0.5 → 分数相同
        assert len(scored) == 2
        assert abs(scored[0][1] - scored[1][1]) < 0.01


# ---------------------------------------------------------------------------
# A2: on_fill 逻辑测试
# ---------------------------------------------------------------------------

class TestOnFill:
    """验证 on_fill 不再误比较 qty 与 price"""

    def test_buy_sets_entry_price(self):
        s = _make_strategy()
        s.on_fill("510300", Side.BUY, 1000, 4.5)
        assert s._entry_prices["510300"] == 4.5
        assert s._high_since_entry["510300"] == 4.5

    def test_sell_does_not_immediately_clear(self):
        """卖出后 entry_price 不应立即清除（延迟到 on_bar 清理）"""
        s = _make_strategy()
        s.on_fill("510300", Side.BUY, 1000, 4.5)
        s.on_fill("510300", Side.SELL, 1000, 4.8)
        # 延迟清理：entry_price 仍存在
        assert "510300" in s._entry_prices
        #但 pending_sell_qty 已记录
        assert s._pending_sell_qty["510300"] == 1000

    def test_sell_cleared_in_on_bar_when_no_position(self):
        """on_bar 中检测到无持仓时清除 entry"""
        s = _make_strategy()
        s._bar_count = 0
        s.on_fill("510300", Side.BUY, 1000, 4.5)
        s.on_fill("510300", Side.SELL, 1000, 4.8)
        # 模拟 on_bar: positions 为空
        bars = {"510300": _bar("510300", 4.8)}
        ctx = StrategyContext(
            cash=1e6, positions={}, current_time=datetime(2024, 6, 1),
            nav=1e6,
        )
        s._update_history(bars)
        s.on_bar(bars, ctx)
        # on_bar 中检测到无持仓，应已清除
        assert "510300" not in s._entry_prices
        assert "510300" not in s._high_since_entry

    def test_partial_sell_keeps_entry(self):
        """部分卖出不清除 entry"""
        s = _make_strategy()
        s._bar_count = 0
        s.on_fill("510300", Side.BUY, 1000, 4.5)
        s.on_fill("510300", Side.SELL, 500, 4.8)
        # 仍持有 500 股
        bars = {"510300": _bar("510300", 4.8)}
        ctx = StrategyContext(
            cash=1e6, positions={"510300": 500},
            current_time=datetime(2024, 6, 1), nav=1e6,
        )
        s._update_history(bars)
        s.on_bar(bars, ctx)
        # 仍有持仓，不清除
        assert "510300" in s._entry_prices


# ---------------------------------------------------------------------------
# C-factor: 相关性因子测试 (R115 FAA)
# ---------------------------------------------------------------------------

class TestCorrelationFactor:
    """验证 C-factor (相关性因子) 的核心逻辑"""

    def test_load_benchmark_returns(self, tmp_path):
        """基准收益率 CSV 正确加载"""
        csv_f = tmp_path / "bench.csv"
        csv_f.write_text(
            "date,return\n"
            "2024-01-02,0.01\n"
            "2024-01-03,-0.005\n"
            "2024-01-04,0.003\n"
        )
        s = _make_strategy(
            weight_correlation=0.30,
            benchmark_returns_path=str(csv_f),
        )
        assert len(s._benchmark_returns) == 3
        assert s._benchmark_dates == [
            "2024-01-02", "2024-01-03", "2024-01-04"
        ]
        assert s._benchmark_returns["2024-01-02"] == pytest.approx(0.01)

    def test_no_benchmark_path_disables_cfactor(self):
        """未配置基准路径时，C 因子自动禁用"""
        s = _make_strategy(
            weight_correlation=0.30,
            benchmark_returns_path="",
        )
        assert len(s._benchmark_returns) == 0

    def test_correlation_computation(self, tmp_path):
        """相关性计算：完全正相关 ETF 应得低分"""
        import random
        random.seed(42)
        # 构造基准: 60天日收益率
        dates = [f"2024-{m:02d}-{d:02d}"
                 for m in range(1, 4) for d in range(1, 22)][:60]
        bm_rets = [random.gauss(0, 0.01) for _ in range(60)]
        csv_f = tmp_path / "bench.csv"
        lines = ["date,return"]
        for d, r in zip(dates, bm_rets):
            lines.append(f"{d},{r}")
        csv_f.write_text("\n".join(lines))

        s = _make_strategy(
            weight_correlation=0.30,
            benchmark_returns_path=str(csv_f),
            correlation_lookback=30,
        )
        # 注入完全正相关的 ETF 价格历史
        # (价格递增, 日收益与基准同向)
        prices = [100.0]
        for r in bm_rets:
            prices.append(prices[-1] * (1 + r))
        s._price_history["ETF_A"] = prices

        corr = s._calc_correlation_with_benchmark("ETF_A", dates[-1])
        # 自相关应接近 1.0
        assert corr is not None
        assert corr > 0.8

    def test_hard_exclude_filters_high_corr(self, tmp_path):
        """硬排除: corr > threshold 的 ETF 不进入评分"""
        import random
        random.seed(123)
        dates = [f"2024-{m:02d}-{d:02d}"
                 for m in range(1, 4) for d in range(1, 22)][:60]
        bm_rets = [random.gauss(0, 0.01) for _ in range(60)]
        csv_f = tmp_path / "bench.csv"
        lines = ["date,return"]
        for d, r in zip(dates, bm_rets):
            lines.append(f"{d},{r}")
        csv_f.write_text("\n".join(lines))

        s = _make_strategy(
            weight_correlation=0.30,
            benchmark_returns_path=str(csv_f),
            correlation_lookback=30,
            correlation_hard_exclude=0.50,
            momentum_lookback=5,
            volatility_lookback=5,
        )
        # ETF_HIGH: 与基准高相关 (同向收益)
        prices_high = [100.0]
        for r in bm_rets:
            prices_high.append(prices_high[-1] * (1 + r))

        # ETF_LOW: 与基准负相关 (反向收益)
        prices_low = [100.0]
        for r in bm_rets:
            prices_low.append(prices_low[-1] * (1 - r * 0.5))

        s._price_history = {
            "ETF_HIGH": prices_high,
            "ETF_LOW": prices_low,
        }
        s._volume_history = {
            "ETF_HIGH": [1e6] * 20,
            "ETF_LOW": [1e6] * 20,
        }

        bars = {
            "ETF_HIGH": _bar("ETF_HIGH", prices_high[-1]),
            "ETF_LOW": _bar("ETF_LOW", prices_low[-1]),
        }
        # 修改 end_ts 使其匹配基准的最后日期，确保日期窗口对齐
        last_date = dates[-1]  # "2024-03-18"
        for b in bars.values():
            object.__setattr__(b, 'end_ts',
                               datetime(2024, 3, 18, 15, 0))

        ctx = StrategyContext(
            cash=1e6, positions={},
            current_time=datetime(2024, 3, 18), nav=1e6,
        )
        scored = s._score_etfs(bars, ctx)
        syms = [sym for sym, _ in scored]
        # 高相关 ETF 应被硬排除
        assert "ETF_HIGH" not in syms
        # 低相关 ETF 应保留
        assert "ETF_LOW" in syms

    def test_cfactor_weight_zero_backward_compat(self):
        """weight_correlation=0 时退化为原始四因子评分"""
        s = _make_strategy(
            weight_correlation=0.0,
            weight_momentum=0.6,
            weight_volatility=0.4,
            weight_reversion=0.0,
            weight_volume=0.0,
        )
        # 注入简单历史
        _inject_history(s, {
            "A": [100 + i * 0.5 for i in range(20)],
            "B": [100 - i * 0.3 for i in range(20)],
        })
        bars = {
            "A": _bar("A", 109.5),
            "B": _bar("B", 94.3),
        }
        ctx = StrategyContext(
            cash=1e6, positions={},
            current_time=datetime(2024, 6, 1), nav=1e6,
        )
        scored = s._score_etfs(bars, ctx)
        # 应该正常返回评分（无崩溃）
        assert len(scored) == 2
        # A 有正动量，应排名更高
        assert scored[0][0] == "A"

    def test_config_new_fields_defaults(self):
        """新增配置字段有正确的默认值"""
        cfg = ETFRotationConfig()
        assert cfg.weight_correlation == 0.30
        assert cfg.correlation_lookback == 60
        assert cfg.correlation_hard_exclude == 0.60
        assert cfg.benchmark_returns_path == ""
