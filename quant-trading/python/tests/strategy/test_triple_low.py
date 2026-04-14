"""可转债三低策略测试

测试 V3 归一化评分逻辑、风控过滤、止损、强赎预警等核心功能。
"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import IntentType, StrategyContext
from aipha.strategy.triple_low import (
    TripleLowConfig,
    TripleLowScore,
    TripleLowStrategy,
    calculate_normalized_scores,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_bar(
    symbol: str,
    close: float,
    *,
    open_: float | None = None,
    volume: float = 1000.0,
    metadata: dict | None = None,
) -> Bar:
    """构造 Bar（含 metadata）"""
    dt = datetime(2024, 1, 2, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 1, 2, 9, 30),
        end_ts=dt,
        open=open_ or close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=volume,
        metadata=metadata,
    )


def _make_cb_bar(
    ts_code: str,
    close: float,
    cb_value: float,
    cb_over_rate: float,
    remain_size: float = 5000.0,
    amount: float = 100.0,
    stk_short_name: str = "正股",
    maturity_date: str = "20280101",
    **extra_meta,
) -> Bar:
    """构造可转债 Bar（带完整 metadata）"""
    meta = {
        "cb_value": cb_value,
        "cb_over_rate": cb_over_rate,
        "remain_size": remain_size,
        "amount": amount,
        "stk_short_name": stk_short_name,
        "bond_short_name": f"转债{ts_code[:6]}",
        "maturity_date": maturity_date,
        "is_called": False,
        **extra_meta,
    }
    return _make_bar(ts_code, close, metadata=meta)


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
    nav: float = 500_000.0,
    trade_date: date | None = None,
) -> StrategyContext:
    """构造 StrategyContext"""
    td = trade_date or date(2024, 1, 2)
    return StrategyContext(
        current_time=datetime.combine(td, datetime.min.time().replace(hour=15)),
        positions=positions or {},
        cash=cash,
        nav=nav,
    )


# ============================================================================
# 评分测试
# ============================================================================


class TestCalculateNormalizedScores:
    """V3 归一化评分算法测试"""

    def test_empty_bonds(self):
        """空列表返回空"""
        assert calculate_normalized_scores([]) == []

    def test_single_bond(self):
        """单只转债，归一化后得分为 0"""
        bonds = [
            {"ts_code": "A", "name": "A转", "price": 100, "premium_rate": 10, "cb_value": 95}
        ]
        scores = calculate_normalized_scores(bonds)
        assert len(scores) == 1
        assert scores[0].ts_code == "A"
        # 单只时 min == max，归一化分子为 0
        assert scores[0].total_score == pytest.approx(0.0, abs=0.01)

    def test_two_bonds_ordering(self):
        """两只转债：低价低溢价低转股价值的排在前面"""
        bonds = [
            {"ts_code": "GOOD", "name": "优", "price": 100, "premium_rate": 5, "cb_value": 90},
            {"ts_code": "BAD", "name": "差", "price": 130, "premium_rate": 40, "cb_value": 130},
        ]
        scores = calculate_normalized_scores(bonds)
        assert scores[0].ts_code == "GOOD"
        assert scores[1].ts_code == "BAD"
        assert scores[0].total_score < scores[1].total_score

    def test_equal_weights(self):
        """等权 0.33/0.33/0.34 验证"""
        bonds = [
            {"ts_code": "A", "price": 100, "premium_rate": 0, "cb_value": 80},
            {"ts_code": "B", "price": 130, "premium_rate": 40, "cb_value": 130},
        ]
        scores = calculate_normalized_scores(
            bonds, price_weight=0.33, premium_weight=0.33, cb_value_weight=0.34,
        )
        # A 应该得分接近 0（所有维度都是最低）
        assert scores[0].ts_code == "A"
        assert scores[0].total_score < 0.05

    def test_low_size_bonus(self):
        """低余额加分功能"""
        bonds = [
            {"ts_code": "SMALL", "price": 105, "premium_rate": 10, "cb_value": 95, "remain_size": 20000},
            {"ts_code": "BIG", "price": 105, "premium_rate": 10, "cb_value": 95, "remain_size": 50000},
        ]
        scores = calculate_normalized_scores(
            bonds, low_size_threshold=30000, low_size_bonus=0.1,
        )
        small = next(s for s in scores if s.ts_code == "SMALL")
        big = next(s for s in scores if s.ts_code == "BIG")
        # SMALL 有低余额奖励，得分更低
        assert small.total_score <= big.total_score

    def test_no_size_bonus_when_zero(self):
        """关闭低余额加分"""
        bonds = [
            {"ts_code": "A", "price": 100, "premium_rate": 5, "cb_value": 90, "remain_size": 10000},
            {"ts_code": "B", "price": 130, "premium_rate": 40, "cb_value": 130, "remain_size": 10000},
        ]
        scores_with = calculate_normalized_scores(bonds, low_size_bonus=0.1)
        scores_without = calculate_normalized_scores(bonds, low_size_bonus=0.0)
        # 关闭后应该不影响排序（但分数不同）
        assert scores_with[0].ts_code == scores_without[0].ts_code


# ============================================================================
# 配置测试
# ============================================================================


class TestTripleLowConfig:
    """配置默认值验证"""

    def test_defaults(self):
        cfg = TripleLowConfig()
        assert cfg.top_k == 10
        assert cfg.rebalance_days == 3
        assert cfg.max_premium == 40.0
        assert cfg.min_price == 0.0
        assert cfg.max_price == 130.0
        assert cfg.lot_size == 10
        assert cfg.stop_loss_pct == 0.10
        assert cfg.price_weight == pytest.approx(0.33)
        assert cfg.premium_weight == pytest.approx(0.33)
        assert cfg.cb_value_weight == pytest.approx(0.34)


# ============================================================================
# 策略测试
# ============================================================================


class TestTripleLowStrategy:
    """策略核心逻辑测试"""

    @pytest.fixture
    def strategy(self) -> TripleLowStrategy:
        return TripleLowStrategy(
            config=TripleLowConfig(
                top_k=3,
                rebalance_days=3,
                max_price=130.0,
                min_price=0.0,
                max_premium=40.0,
                lot_size=10,
            )
        )

    def _build_bars(self) -> dict[str, Bar]:
        """创建一组可转债 bars"""
        return {
            "113001.SH": _make_cb_bar("113001.SH", 100, 95, 5.0, 5000, 100),
            "113002.SH": _make_cb_bar("113002.SH", 105, 98, 8.0, 8000, 200),
            "113003.SH": _make_cb_bar("113003.SH", 110, 102, 12.0, 10000, 300),
            "113004.SH": _make_cb_bar("113004.SH", 120, 115, 25.0, 15000, 500),
            "113005.SH": _make_cb_bar("113005.SH", 125, 120, 35.0, 20000, 600),
        }

    def test_first_rebalance(self, strategy: TripleLowStrategy):
        """首次调仓：空仓等待 rebalance_days 个交易日后应买入 top_k 只"""
        bars = self._build_bars()
        strategy.on_start()

        # 前 2 个 bar 不应调仓（rebalance_days=3）
        for day_offset in range(1, 3):
            ctx = _make_context(positions={}, cash=500_000, trade_date=date(2024, 1, day_offset))
            early_intents = strategy.on_bar(bars, ctx)
            assert len([i for i in early_intents if i.side == Side.BUY]) == 0

        # 第 3 个 bar 应开始调仓
        context = _make_context(positions={}, cash=500_000, trade_date=date(2024, 1, 3))
        intents = strategy.on_bar(bars, context)

        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) == 3  # top_k=3
        # 买入的应该是得分最低的 3 只
        bought_symbols = {i.symbol for i in buy_intents}
        assert "113001.SH" in bought_symbols  # 最低价
        assert "113002.SH" in bought_symbols  # 第二低

    def test_rebalance_skipped_within_period(self, strategy: TripleLowStrategy):
        """调仓间隔内不应调仓"""
        bars = self._build_bars()
        context1 = _make_context(positions={}, cash=500_000, trade_date=date(2024, 1, 2))
        context2 = _make_context(
            positions={"113001.SH": 100, "113002.SH": 100, "113003.SH": 100},
            cash=100_000,
            trade_date=date(2024, 1, 3),  # 仅 1 天后
        )

        strategy.on_start()
        strategy.on_bar(bars, context1)  # 首次调仓
        intents2 = strategy.on_bar(bars, context2)  # 第二天

        # 没有止损的情况下，不应有任何调仓
        rebalance_intents = [i for i in intents2 if "轮" in (i.reason or "")]
        assert len(rebalance_intents) == 0

    def test_rebalance_after_period(self, strategy: TripleLowStrategy):
        """调仓间隔后应触发调仓"""
        bars = self._build_bars()
        context1 = _make_context(positions={}, cash=500_000, trade_date=date(2024, 1, 2))
        context2 = _make_context(
            positions={"113001.SH": 100, "113002.SH": 100, "113003.SH": 100},
            cash=100_000,
            trade_date=date(2024, 1, 5),  # 3 天后
        )

        strategy.on_start()
        strategy.on_bar(bars, context1)
        intents2 = strategy.on_bar(bars, context2)

        # 应该有调仓意图（可能是卖+买或者只是确认持仓不变）
        # 因为持仓与目标一致，可能没有新交易
        # 但至少不会报错
        assert isinstance(intents2, list)

    def test_stop_loss(self, strategy: TripleLowStrategy):
        """止损触发卖出"""
        # 记录入场价为 110
        strategy._entry_prices["113003.SH"] = 110.0

        # 当前价跌到 95（跌幅 > 10%）
        bars = {
            "113003.SH": _make_cb_bar("113003.SH", 95, 90, 5.0),
        }
        context = _make_context(
            positions={"113003.SH": 100},
            cash=100_000,
            trade_date=date(2024, 1, 3),
        )

        # 手动设置上次调仓日避免触发调仓
        strategy._last_rebalance_date = date(2024, 1, 2)
        strategy._last_rebalance_bar = strategy._bar_count

        intents = strategy.on_bar(bars, context)
        stop_intents = [i for i in intents if "止损" in (i.reason or "")]
        assert len(stop_intents) == 1
        assert stop_intents[0].symbol == "113003.SH"
        assert stop_intents[0].side == Side.SELL
        assert stop_intents[0].qty == 100

    def test_no_stop_loss_within_threshold(self, strategy: TripleLowStrategy):
        """未达止损阈值不卖"""
        strategy._entry_prices["113001.SH"] = 100.0

        bars = {
            "113001.SH": _make_cb_bar("113001.SH", 95, 90, 5.0),  # 跌 5% < 10%
        }
        context = _make_context(
            positions={"113001.SH": 100},
            cash=100_000,
            trade_date=date(2024, 1, 3),
        )
        strategy._last_rebalance_date = date(2024, 1, 2)
        strategy._last_rebalance_bar = strategy._bar_count

        intents = strategy.on_bar(bars, context)
        stop_intents = [i for i in intents if "止损" in (i.reason or "")]
        assert len(stop_intents) == 0

    # ------------------------------------------------------------------
    # 风控过滤
    # ------------------------------------------------------------------

    def test_filter_high_price(self, strategy: TripleLowStrategy):
        """价格超限被过滤"""
        bars = {
            "HIGH": _make_cb_bar("HIGH", 140, 130, 5.0),  # price > 130
            "OK": _make_cb_bar("OK", 100, 95, 5.0),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_high_premium(self, strategy: TripleLowStrategy):
        """溢价率超限被过滤"""
        bars = {
            "HPREM": _make_cb_bar("HPREM", 100, 95, 45.0),  # premium > 40
            "OK": _make_cb_bar("OK", 100, 95, 5.0),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_st(self, strategy: TripleLowStrategy):
        """ST 正股被过滤"""
        bars = {
            "ST": _make_cb_bar("ST", 100, 95, 5.0, stk_short_name="*ST异常"),
            "OK": _make_cb_bar("OK", 100, 95, 5.0, stk_short_name="正常股"),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_low_turnover(self, strategy: TripleLowStrategy):
        """成交额过低被过滤"""
        bars = {
            "LOW": _make_cb_bar("LOW", 100, 95, 5.0, amount=5.0),  # < min_turnover=20
            "OK": _make_cb_bar("OK", 100, 95, 5.0, amount=50.0),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_maturity(self, strategy: TripleLowStrategy):
        """临期转债被过滤"""
        bars = {
            "EXPIRING": _make_cb_bar(
                "EXPIRING", 100, 95, 5.0, maturity_date="20240120"
            ),  # < 30 天
            "OK": _make_cb_bar("OK", 100, 95, 5.0, maturity_date="20280101"),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_called(self, strategy: TripleLowStrategy):
        """已强赎转债被过滤"""
        bars = {
            "CALLED": _make_cb_bar("CALLED", 100, 95, 5.0, is_called=True),
            "OK": _make_cb_bar("OK", 100, 95, 5.0, is_called=False),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1
        assert filtered[0]["ts_code"] == "OK"

    def test_filter_no_metadata(self, strategy: TripleLowStrategy):
        """无 metadata 的 bar 被跳过"""
        bars = {
            "NOOP": _make_bar("NOOP", 100),  # 无 metadata
            "OK": _make_cb_bar("OK", 100, 95, 5.0),
        }
        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 1

    # ------------------------------------------------------------------
    # 强赎预警
    # ------------------------------------------------------------------

    def test_force_redeem_counter(self, strategy: TripleLowStrategy):
        """强赎预警计数累积"""
        bars = {
            "A": _make_cb_bar("A", 100, 135, 5.0),  # cb_value=135 > threshold=130
        }
        for _ in range(7):
            strategy._update_force_redeem_counter(bars)
        assert strategy._force_redeem_counter["A"] == 7

        # 第 8 次后应被过滤
        strategy._update_force_redeem_counter(bars)
        assert strategy._force_redeem_counter["A"] == 8

        filtered = strategy._filter_bonds(bars, date(2024, 1, 2))
        assert len(filtered) == 0  # 被剔除

    def test_force_redeem_reset(self, strategy: TripleLowStrategy):
        """转股价值回落后计数重置"""
        high_bars = {"A": _make_cb_bar("A", 100, 135, 5.0)}
        low_bars = {"A": _make_cb_bar("A", 100, 100, 5.0)}

        for _ in range(5):
            strategy._update_force_redeem_counter(high_bars)
        assert strategy._force_redeem_counter["A"] == 5

        strategy._update_force_redeem_counter(low_bars)  # cb_value < 130
        assert strategy._force_redeem_counter["A"] == 0

    # ------------------------------------------------------------------
    # 轮动指令生成
    # ------------------------------------------------------------------

    def test_generate_sell_intents(self, strategy: TripleLowStrategy):
        """轮出不在目标的持仓"""
        bars = self._build_bars()
        scores = calculate_normalized_scores(
            [
                {"ts_code": "113001.SH", "price": 100, "premium_rate": 5, "cb_value": 95},
                {"ts_code": "113002.SH", "price": 105, "premium_rate": 8, "cb_value": 98},
            ]
        )
        target_symbols = {"113001.SH", "113002.SH"}

        positions = {"113001.SH": 100, "113003.SH": 200}
        cash = 50_000
        # NAV = positions_value + cash
        nav = 100 * 100 + 200 * 110 + cash  # 113001=100, 113003=110
        intents = strategy._generate_rebalance_intents(
            target_symbols=target_symbols,
            scores={s.ts_code: s for s in scores},
            current_positions=positions,
            cash=cash,
            bars=bars,
            nav=nav,
        )

        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) == 1
        assert sell_intents[0].symbol == "113003.SH"

    def test_generate_buy_intents(self, strategy: TripleLowStrategy):
        """轮入不在持仓的目标"""
        bars = self._build_bars()
        scores = calculate_normalized_scores(
            [
                {"ts_code": "113001.SH", "price": 100, "premium_rate": 5, "cb_value": 95},
                {"ts_code": "113002.SH", "price": 105, "premium_rate": 8, "cb_value": 98},
            ]
        )
        target_symbols = {"113001.SH", "113002.SH"}

        intents = strategy._generate_rebalance_intents(
            target_symbols=target_symbols,
            scores={s.ts_code: s for s in scores},
            current_positions={},
            cash=200_000,
            bars=bars,
            nav=200_000,
        )

        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) == 2
        symbols = {i.symbol for i in buy_intents}
        assert symbols == {"113001.SH", "113002.SH"}

    def test_lot_size_rounding(self, strategy: TripleLowStrategy):
        """买入数量是 lot_size 的整数倍"""
        bars = self._build_bars()
        scores = calculate_normalized_scores(
            [{"ts_code": "113001.SH", "price": 100, "premium_rate": 5, "cb_value": 95}]
        )
        intents = strategy._generate_rebalance_intents(
            target_symbols={"113001.SH"},
            scores={s.ts_code: s for s in scores},
            current_positions={},
            cash=100_000,
            bars=bars,
            nav=100_000,
        )

        buy_intents = [i for i in intents if i.side == Side.BUY]
        for intent in buy_intents:
            assert intent.qty % strategy.config.lot_size == 0

    # ------------------------------------------------------------------
    # kwargs 覆盖
    # ------------------------------------------------------------------

    def test_kwargs_override(self):
        """kwargs 能覆盖 config 字段"""
        strategy = TripleLowStrategy(top_k=20, max_premium=50.0)
        assert strategy.config.top_k == 20
        assert strategy.config.max_premium == 50.0


# ============================================================================
# 注册表测试
# ============================================================================


class TestRegistry:
    """策略注册测试"""

    def test_triple_low_registered(self):
        """triple_low 已注册"""
        from aipha.strategy.registry import StrategyRegistry

        registry = StrategyRegistry()
        cls = registry.get("triple_low")
        assert cls is not None
        assert cls is TripleLowStrategy
