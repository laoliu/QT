"""
Tests for engines/dragon_master.py — 真龙战法核心引擎.

Tests cover:
  - identify_major_theme (大主线识别)
  - rank_dragons (龙一/龙二/龙三排名)
  - evaluate_buy_timing (买入时机 1板/2板/3板/4板+)
  - generate_board_buy_signal (封单打板信号)
  - get_fallback_dragons (龙一→龙二→龙三降级推荐)
  - run_full_analysis (完整流程)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engines.dragon_master import (
    _calc_theme_score,
    _check_volume_signal,
    _classify_seal_strength,
    _is_early_board,
    _score_board_height,
    _score_board_time,
    _score_seal_amount,
    _score_stability,
    evaluate_buy_timing,
    generate_board_buy_signal,
    get_fallback_dragons,
    identify_major_theme,
    rank_dragons,
    run_full_analysis,
)


# ── Helper: mock pool data ──

def _make_stock(
    code: str = "600001",
    name: str = "测试股",
    industry: str = "电力设备",
    lian_ban_count: int = 1,
    first_board_time: str = "09:30:05",
    seal_amount: float = 3e8,
    open_count: int = 0,
    turnover_rate: float = 8.0,
    amount: float = 10e8,
    price: float = 20.0,
    flow_value: float = 80e8,
    **extra,
) -> dict:
    d = {
        "code": code,
        "name": name,
        "industry": industry,
        "lian_ban_count": lian_ban_count,
        "first_board_time": first_board_time,
        "last_board_time": first_board_time,
        "seal_amount": seal_amount,
        "open_count": open_count,
        "turnover_rate": turnover_rate,
        "amount": amount,
        "price": price,
        "flow_value": flow_value,
        "change_pct": 10.0,
    }
    d.update(extra)
    return d


def _make_pool(stocks: list[dict], date: str = "20260328") -> dict:
    return {"stocks": stocks, "date": date}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scoring helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestScoreBoardTime:
    def test_early_open(self):
        assert _score_board_time("09:25:03") == 30

    def test_first_5min(self):
        assert _score_board_time("09:33:00") == 30

    def test_mid_morning(self):
        assert _score_board_time("10:15:00") == 20

    def test_afternoon(self):
        assert _score_board_time("14:00:00") == 5

    def test_tail_end(self):
        assert _score_board_time("14:55:00") == 2

    def test_unknown(self):
        assert _score_board_time("") == 15


class TestScoreSealAmount:
    def test_huge_seal(self):
        assert _score_seal_amount(10e8) == 25

    def test_large_seal(self):
        assert _score_seal_amount(5e8) == 20

    def test_medium_seal(self):
        assert _score_seal_amount(2e8) == 15

    def test_small_seal(self):
        assert _score_seal_amount(1e8) == 10

    def test_tiny_seal(self):
        assert _score_seal_amount(3000e4) == 4

    def test_no_data(self):
        assert _score_seal_amount(0) == 2


class TestScoreStability:
    def test_no_open(self):
        assert _score_stability(0) == 20

    def test_one_open(self):
        assert _score_stability(1) == 14

    def test_many_opens(self):
        assert _score_stability(4) == 2


class TestScoreBoardHeight:
    def test_5_board(self):
        assert _score_board_height(5) == 15

    def test_3_board(self):
        assert _score_board_height(3) == 12

    def test_2_board(self):
        assert _score_board_height(2) == 10

    def test_1_board(self):
        assert _score_board_height(1) == 5


class TestClassifySealStrength:
    def test_extreme(self):
        assert _classify_seal_strength(12e8) == "极强"

    def test_strong(self):
        assert _classify_seal_strength(6e8) == "强"

    def test_medium(self):
        assert _classify_seal_strength(3e8) == "中"

    def test_weak(self):
        assert _classify_seal_strength(1e8) == "弱"

    def test_very_weak(self):
        assert _classify_seal_strength(5000e4) == "极弱"


class TestIsEarlyBoard:
    def test_early(self):
        assert _is_early_board("09:30:00") is True

    def test_boundary(self):
        assert _is_early_board("10:00:00") is True

    def test_late(self):
        assert _is_early_board("10:01:00") is False


class TestCheckVolumeSignal:
    def test_breakout(self):
        klines = [
            {"volume": 1000}, {"volume": 1100}, {"volume": 1050}, {"volume": 2000},
        ]
        assert _check_volume_signal(klines) == "放量"

    def test_shrink(self):
        klines = [
            {"volume": 2000}, {"volume": 1800}, {"volume": 2100}, {"volume": 600},
        ]
        assert _check_volume_signal(klines) == "缩量"

    def test_flat(self):
        klines = [
            {"volume": 1000}, {"volume": 1100}, {"volume": 1050}, {"volume": 1100},
        ]
        assert _check_volume_signal(klines) == "平量"

    def test_insufficient_data(self):
        assert _check_volume_signal([{"volume": 100}]) == "未知"
        assert _check_volume_signal(None) == "未知"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第一步: 大主线识别
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestIdentifyMajorTheme:
    def test_super_theme_found(self):
        """板块有>=10只涨停、连续2天、有2连板 → 超级主线."""
        sector = "电力设备"
        today_stocks = [
            _make_stock(f"6000{i:02d}", f"电力{i}", sector, lian_ban_count=(3 if i == 0 else 1))
            for i in range(12)
        ]
        yesterday_stocks = [
            _make_stock(f"6000{i:02d}", f"电力{i}", sector)
            for i in range(11)
        ]
        result = identify_major_theme(
            _make_pool(today_stocks),
            _make_pool(yesterday_stocks),
        )
        themes = result.get("themes", [])
        assert len(themes) >= 1
        top = themes[0]
        assert top["sector"] == sector
        assert top["is_super_theme"] is True
        assert top["today_zt_count"] == 12
        assert top["max_board_height"] >= 2
        assert result["market_summary"]["super_theme_count"] >= 1

    def test_no_super_theme(self):
        """Only 3 limit-ups → not super theme."""
        stocks = [_make_stock(f"6000{i:02d}", f"Test{i}", "银行") for i in range(3)]
        result = identify_major_theme(
            _make_pool(stocks),
            _make_pool([]),
        )
        themes = result.get("themes", [])
        assert len(themes) >= 1
        assert themes[0]["is_super_theme"] is False

    def test_empty_pool(self):
        result = identify_major_theme(_make_pool([]), _make_pool([]))
        assert result["market_summary"]["total_zt_today"] == 0

    def test_theme_score_calculation(self):
        # 15+ ZT today + 10+ yesterday + 5-board height + 2 consecutive days
        score = _calc_theme_score(15, 12, 5, 2, 10)
        assert score == 100  # Maximum


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第二步: 龙一/龙二/龙三排名
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRankDragons:
    def test_rank_by_board_time(self):
        """Earliest board time should rank #1."""
        stocks = [
            _make_stock("600001", "晚封", first_board_time="10:30:00", seal_amount=5e8),
            _make_stock("600002", "早封", first_board_time="09:25:00", seal_amount=5e8),
        ]
        dragons = rank_dragons(stocks)
        assert dragons[0]["code"] == "600002"
        assert dragons[0]["dragon_rank"] == 1

    def test_rank_by_seal_amount(self):
        """Larger seal should rank higher when time is similar."""
        stocks = [
            _make_stock("600001", "小封", first_board_time="09:30:00", seal_amount=1e8),
            _make_stock("600002", "大封", first_board_time="09:30:00", seal_amount=10e8),
        ]
        dragons = rank_dragons(stocks)
        assert dragons[0]["code"] == "600002"

    def test_stability_matters(self):
        """Zero open_count beats multiple opens."""
        stocks = [
            _make_stock("600001", "不稳", first_board_time="09:30:00",
                        seal_amount=5e8, open_count=4),
            _make_stock("600002", "稳", first_board_time="09:30:00",
                        seal_amount=5e8, open_count=0),
        ]
        dragons = rank_dragons(stocks)
        assert dragons[0]["code"] == "600002"

    def test_day2_confirmation(self):
        """2-board stock with yesterday's early first should get day2 bonus."""
        today = [
            _make_stock("600001", "龙一", lian_ban_count=2,
                        first_board_time="09:30:00", seal_amount=5e8),
        ]
        yesterday = [
            _make_stock("600001", "龙一", lian_ban_count=1,
                        first_board_time="09:25:00"),
        ]
        dragons = rank_dragons(today, yesterday)
        assert dragons[0]["factors"]["day2_confirmed"] is True

    def test_empty_input(self):
        assert rank_dragons([]) == []

    def test_dragon_rank_assigned(self):
        stocks = [
            _make_stock(f"6000{i}", f"股{i}", first_board_time=f"09:{30+i}:00")
            for i in range(5)
        ]
        dragons = rank_dragons(stocks)
        assert len(dragons) == 5
        for i, d in enumerate(dragons):
            assert d["dragon_rank"] == i + 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第三步: 买入时机评估
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEvaluateBuyTiming:
    def test_1board_wait(self):
        """1板 → 不买."""
        stock = _make_stock(lian_ban_count=1)
        result = evaluate_buy_timing(stock)
        assert result["action"] == "wait"
        assert result["board_number"] == 1

    def test_2board_wait(self):
        """2板 → 等3板."""
        stock = _make_stock(lian_ban_count=2)
        result = evaluate_buy_timing(stock)
        assert result["action"] == "wait"

    def test_3board_buy_best(self):
        """3板 → 最佳买点!"""
        stock = _make_stock(lian_ban_count=3)
        result = evaluate_buy_timing(stock)
        assert result["action"] == "buy"
        assert result["is_best_timing"] is True
        assert result["board_number"] == 3
        assert result["confidence"] >= 60

    def test_3board_with_volume(self):
        """3板+放量 → 最高置信买入."""
        stock = _make_stock(lian_ban_count=3)
        klines = [
            {"volume": 1000}, {"volume": 1100}, {"volume": 1050}, {"volume": 2000},
        ]
        result = evaluate_buy_timing(stock, klines)
        assert result["action"] == "buy"
        assert result["is_best_timing"] is True
        assert result["volume_signal"] == "放量"
        assert result["confidence"] >= 80
        assert result["risk_level"] == "low"

    def test_4board_risky(self):
        """4板 → 可做但风险高."""
        stock = _make_stock(lian_ban_count=4)
        result = evaluate_buy_timing(stock)
        assert result["action"] == "buy"
        assert result["is_best_timing"] is False
        assert result["risk_level"] == "high"

    def test_5board_skip(self):
        """5板+ → 不建议."""
        stock = _make_stock(lian_ban_count=5)
        result = evaluate_buy_timing(stock)
        assert result["action"] == "skip"

    def test_2board_yizi_wait(self):
        """2板一字板 → 99%买不进."""
        stock = _make_stock(
            lian_ban_count=2,
            first_board_time="09:25:00",
            open_count=0,
            turnover_rate=0.5,
        )
        result = evaluate_buy_timing(stock)
        assert result["action"] == "wait"
        assert "99%" in result["reason"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第四步: 封单打板信号
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBoardBuySignal:
    def test_no_realtime_data(self):
        """Without realtime, use pool seal as fallback."""
        stock = _make_stock(seal_amount=5e8)
        result = generate_board_buy_signal(stock, None)
        assert result["ready"] is False
        assert result["seal_amount_yi"] > 0
        assert "盘后" in result["signal"]

    def test_realtime_at_limit_sealed(self):
        """At limit-up, strong seal → ready to buy."""
        stock = _make_stock(code="600519", price=200.0)
        rt_quote = {
            "price": 220.0,
            "prevClose": 200.0,
            "bids": [{"price": 220.0, "volume": 500000}],  # 500000手
            "asks": [],
        }
        result = generate_board_buy_signal(stock, rt_quote)
        assert result["signal"] == "板上封稳"
        assert result["ready"] is True

    def test_realtime_not_at_limit(self):
        """Price not at limit → not ready."""
        stock = _make_stock(code="600519", price=200.0)
        rt_quote = {
            "price": 210.0,
            "prevClose": 200.0,
            "bids": [],
            "asks": [],
        }
        result = generate_board_buy_signal(stock, rt_quote)
        assert result["signal"] in ("冲板中", "未涨停")
        assert result["ready"] is False

    def test_realtime_board_open(self):
        """At limit but has asks → 开板."""
        stock = _make_stock(code="600519", price=200.0)
        rt_quote = {
            "price": 220.0,
            "prevClose": 200.0,
            "bids": [{"price": 220.0, "volume": 10000}],
            "asks": [{"price": 220.0, "volume": 5000}],
        }
        result = generate_board_buy_signal(stock, rt_quote)
        assert result["signal"] == "开板"
        assert result["ready"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第五步: 降级推荐
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFallbackDragons:
    def test_dragon_1_buyable(self):
        dragons = [
            {"code": "600001", "name": "龙一", "dragon_rank": 1, "dragon_score": 90,
             "factors": {"turnover_rate": 8.0}},
            {"code": "600002", "name": "龙二", "dragon_rank": 2, "dragon_score": 70,
             "factors": {"turnover_rate": 6.0}},
        ]
        result = get_fallback_dragons(dragons)
        assert result["dragon_1"]["code"] == "600001"
        assert result["dragon_2"]["code"] == "600002"
        assert "龙一" in result["recommendation"]
        assert len(result["buyable_dragons"]) == 2

    def test_dragon_1_unbuyable(self):
        """龙一一字板(turnover < 1) → recommend 龙二."""
        dragons = [
            {"code": "600001", "name": "一字龙", "dragon_rank": 1, "dragon_score": 90,
             "factors": {"turnover_rate": 0.3}},
            {"code": "600002", "name": "龙二", "dragon_rank": 2, "dragon_score": 70,
             "factors": {"turnover_rate": 8.0}},
        ]
        result = get_fallback_dragons(dragons)
        assert result["buyable_dragons"][0]["code"] == "600002"
        assert "龙二" in result["recommendation"]

    def test_all_unbuyable(self):
        """All 一字板 → give up."""
        dragons = [
            {"code": f"6000{i}", "name": f"一字{i}", "dragon_rank": i + 1,
             "dragon_score": 90 - i * 10,
             "factors": {"turnover_rate": 0.2}}
            for i in range(3)
        ]
        result = get_fallback_dragons(dragons)
        assert len(result["buyable_dragons"]) == 0
        assert "放弃" in result["recommendation"]

    def test_max_rank_3(self):
        """Only consider up to dragon 3."""
        dragons = [
            {"code": f"6000{i}", "name": f"龙{i+1}", "dragon_rank": i + 1,
             "dragon_score": 90 - i * 10,
             "factors": {"turnover_rate": 8.0}}
            for i in range(5)
        ]
        result = get_fallback_dragons(dragons, max_rank=3)
        assert len(result["buyable_dragons"]) == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第六步: 完整流程
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRunFullAnalysis:
    def test_full_with_mock_pools(self):
        """Full analysis with mocked pool data."""
        sector = "电力设备"
        today_stocks = [
            _make_stock(
                f"6000{i:02d}", f"电力{i}", sector,
                lian_ban_count=(3 if i == 0 else (2 if i == 1 else 1)),
                first_board_time=f"09:{25+i}:00",
                seal_amount=(8e8 if i == 0 else (3e8 if i == 1 else 1e8)),
            )
            for i in range(12)
        ]
        yest_stocks = [
            _make_stock(f"6000{i:02d}", f"电力{i}", sector)
            for i in range(11)
        ]

        result = run_full_analysis(
            today_pool=_make_pool(today_stocks),
            yesterday_pool=_make_pool(yest_stocks),
        )

        assert "timestamp" in result
        assert "summary" in result
        assert "themes" in result
        assert "top_picks" in result

        themes = result["themes"]
        assert len(themes) >= 1
        top_theme = themes[0]
        assert top_theme["sector"] == sector
        assert len(top_theme["dragons"]) >= 1
        assert top_theme["dragons"][0]["dragon_rank"] == 1
        assert "fallback" in top_theme
        assert "buy_signals" in top_theme
        assert "board_signals" in top_theme

    def test_empty_market(self):
        """No limit-ups → graceful empty result."""
        result = run_full_analysis(
            today_pool=_make_pool([]),
            yesterday_pool=_make_pool([]),
        )
        assert result["summary"]
        assert result["market_summary"]["total_zt_today"] == 0

    def test_summary_format(self):
        """Summary should be readable."""
        today_stocks = [
            _make_stock(f"6000{i:02d}", f"股{i}", "银行",
                        lian_ban_count=3, first_board_time="09:30:00")
            for i in range(5)
        ]
        result = run_full_analysis(
            today_pool=_make_pool(today_stocks),
            yesterday_pool=_make_pool([]),
        )
        assert "涨停" in result["summary"]
