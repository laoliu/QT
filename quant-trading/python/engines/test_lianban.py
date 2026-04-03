"""
Tests for engines/lianban.py — limit-up detection, consecutive board counting,
board-type classification, and quality scoring.
"""

from __future__ import annotations

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engines.lianban import (
    board_quality_score,
    classify_board_type,
    count_consecutive_boards,
    detect_limit_down,
    detect_limit_up,
    get_board_history,
    is_one_word_board,
    _limit_up_ratio,
)


# ── _limit_up_ratio ─────────────────────────────────────────────


class TestLimitUpRatio:
    def test_main_board_sh(self):
        assert _limit_up_ratio("600519") == 0.10
        assert _limit_up_ratio("601398.SH") == 0.10

    def test_main_board_sz(self):
        assert _limit_up_ratio("000001") == 0.10
        assert _limit_up_ratio("000858.SZ") == 0.10

    def test_gem_board(self):
        """创业板 30xxxx → 20%"""
        assert _limit_up_ratio("300750") == 0.20
        assert _limit_up_ratio("301234.SZ") == 0.20

    def test_star_board(self):
        """科创板 688xxx → 20%"""
        assert _limit_up_ratio("688001") == 0.20
        assert _limit_up_ratio("688981.SH") == 0.20

    def test_bse_board(self):
        """北交所 4xxxxx / 8xxxxx (非688) → 30%"""
        assert _limit_up_ratio("430047") == 0.30
        assert _limit_up_ratio("830946") == 0.30

    def test_empty(self):
        assert _limit_up_ratio("") == 0.10
        assert _limit_up_ratio(None) == 0.10  # type: ignore[arg-type]


# ── detect_limit_up ──────────────────────────────────────────────


class TestDetectLimitUp:
    def test_exact_10pct_limit(self):
        row = {"close": 11.0, "open": 10.5, "high": 11.0, "low": 10.2}
        assert detect_limit_up(row, prev_close=10.0, symbol="600519") is True

    def test_just_below_threshold(self):
        row = {"close": 10.8, "open": 10.5, "high": 10.9, "low": 10.2}
        assert detect_limit_up(row, prev_close=10.0, symbol="600519") is False

    def test_20pct_gem_limit(self):
        row = {"close": 12.0, "open": 11.5, "high": 12.0, "low": 11.0}
        assert detect_limit_up(row, prev_close=10.0, symbol="300750") is True

    def test_20pct_not_reached(self):
        row = {"close": 11.5, "open": 11.0, "high": 11.6, "low": 10.8}
        assert detect_limit_up(row, prev_close=10.0, symbol="300750") is False

    def test_30pct_bse_limit(self):
        row = {"close": 13.0, "open": 12.0, "high": 13.0, "low": 11.5}
        assert detect_limit_up(row, prev_close=10.0, symbol="430047") is True

    def test_st_stock_5pct(self):
        row = {"close": 10.5, "open": 10.2, "high": 10.5, "low": 10.1}
        assert detect_limit_up(row, prev_close=10.0, symbol="600123", is_st=True) is True

    def test_st_stock_not_reached(self):
        row = {"close": 10.3, "open": 10.1, "high": 10.4, "low": 10.0}
        assert detect_limit_up(row, prev_close=10.0, symbol="600123", is_st=True) is False

    def test_prev_close_from_row(self):
        row = {"close": 11.0, "prev_close": 10.0}
        assert detect_limit_up(row, symbol="600519") is True

    def test_zero_close(self):
        row = {"close": 0, "open": 10}
        assert detect_limit_up(row, prev_close=10.0) is False

    def test_zero_prev_close(self):
        row = {"close": 11.0}
        assert detect_limit_up(row, prev_close=0) is False


# ── detect_limit_down ────────────────────────────────────────────


class TestDetectLimitDown:
    def test_10pct_limit_down(self):
        row = {"close": 9.0, "open": 9.5, "high": 9.6, "low": 9.0}
        assert detect_limit_down(row, prev_close=10.0, symbol="600519") is True

    def test_not_limit_down(self):
        row = {"close": 9.5, "open": 9.8, "high": 10.0, "low": 9.4}
        assert detect_limit_down(row, prev_close=10.0, symbol="600519") is False


# ── is_one_word_board ────────────────────────────────────────────


class TestIsOneWordBoard:
    def test_one_word(self):
        row = {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0}
        assert is_one_word_board(row) is True

    def test_normal_bar(self):
        row = {"open": 10.5, "high": 11.0, "low": 10.2, "close": 10.8}
        assert is_one_word_board(row) is False


# ── classify_board_type ──────────────────────────────────────────


class TestClassifyBoardType:
    def test_one_word_board(self):
        row = {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0}
        assert classify_board_type(row, prev_close=10.0, symbol="600519") == "一字板"

    def test_t_board(self):
        """T字板: open == low, but high > open"""
        row = {"open": 11.0, "high": 11.2, "low": 11.0, "close": 11.0}
        assert classify_board_type(row, prev_close=10.0, symbol="600519") == "T字板"

    def test_huanshou_board(self):
        """换手板: normal traded range"""
        row = {"open": 10.5, "high": 11.0, "low": 10.2, "close": 11.0}
        assert classify_board_type(row, prev_close=10.0, symbol="600519") == "换手板"

    def test_not_limit_up(self):
        row = {"open": 10.5, "high": 10.8, "low": 10.2, "close": 10.6}
        assert classify_board_type(row, prev_close=10.0, symbol="600519") is None


# ── count_consecutive_boards ─────────────────────────────────────


class TestCountConsecutiveBoards:
    def _make_bar(self, close, prev_close, **kwargs):
        """Helper to make a K-line bar."""
        return {
            "close": close,
            "open": kwargs.get("open", close - 0.3),
            "high": kwargs.get("high", close),
            "low": kwargs.get("low", close - 0.5),
            "volume": kwargs.get("volume", 10000),
        }

    def test_three_consecutive(self):
        bars = [
            self._make_bar(10.0, 9.5),   # base
            self._make_bar(11.0, 10.0),   # +10% → board 1
            self._make_bar(12.1, 11.0),   # +10% → board 2
            self._make_bar(13.31, 12.1),  # +10% → board 3
        ]
        assert count_consecutive_boards(bars, symbol="600519") == 3

    def test_two_then_break(self):
        bars = [
            self._make_bar(10.0, 9.5),
            self._make_bar(11.0, 10.0),   # board 1
            self._make_bar(12.1, 11.0),   # board 2
            self._make_bar(12.5, 12.1),   # NOT board (only +3%)
        ]
        assert count_consecutive_boards(bars, symbol="600519") == 0

    def test_single_bar(self):
        bars = [self._make_bar(10.0, 9.5)]
        assert count_consecutive_boards(bars) == 0

    def test_empty(self):
        assert count_consecutive_boards([]) == 0


# ── get_board_history ────────────────────────────────────────────


class TestGetBoardHistory:
    def test_returns_newest_first(self):
        bars = [
            {"close": 10.0, "open": 9.8, "high": 10.0, "low": 9.7, "date": "2026-03-10", "volume": 1000, "amount": 10000, "turnover_rate": 5.0},
            {"close": 11.0, "open": 10.5, "high": 11.0, "low": 10.2, "date": "2026-03-11", "volume": 2000, "amount": 22000, "turnover_rate": 8.0},
            {"close": 12.1, "open": 11.2, "high": 12.1, "low": 11.0, "date": "2026-03-12", "volume": 3000, "amount": 36000, "turnover_rate": 10.0},
        ]
        history = get_board_history(bars, symbol="600519")
        assert len(history) == 2
        assert history[0]["date"] == "2026-03-12"  # newest first
        assert history[1]["date"] == "2026-03-11"


# ── board_quality_score ──────────────────────────────────────────


class TestBoardQualityScore:
    def test_no_boards_gives_zero(self):
        bars = [
            {"close": 10.0, "open": 9.8, "high": 10.0, "low": 9.7, "volume": 1000, "turnover_rate": 5.0},
            {"close": 10.2, "open": 10.0, "high": 10.3, "low": 9.9, "volume": 1100, "turnover_rate": 5.0},
        ]
        result = board_quality_score(bars, "600519")
        assert result["total_score"] == 0
        assert result["board_count"] == 0

    def test_two_huanshou_boards(self):
        bars = [
            {"close": 10.0, "open": 9.8, "high": 10.1, "low": 9.7, "volume": 1000, "turnover_rate": 8.0},
            {"close": 11.0, "open": 10.5, "high": 11.0, "low": 10.2, "volume": 1500, "turnover_rate": 10.0},
            {"close": 12.1, "open": 11.2, "high": 12.1, "low": 11.0, "volume": 2000, "turnover_rate": 12.0},
        ]
        result = board_quality_score(bars, "600519")
        assert result["board_count"] == 2
        assert result["total_score"] > 50  # Should be high quality

    def test_all_one_word_penalty(self):
        bars = [
            {"close": 10.0, "open": 10.0, "high": 10.0, "low": 10.0, "volume": 100, "turnover_rate": 0.5},
            {"close": 11.0, "open": 11.0, "high": 11.0, "low": 11.0, "volume": 50, "turnover_rate": 0.2},
            {"close": 12.1, "open": 12.1, "high": 12.1, "low": 12.1, "volume": 30, "turnover_rate": 0.1},
        ]
        result = board_quality_score(bars, "600519")
        assert result["board_count"] == 2
        # All 一字板 → heavy penalty, low total
        assert result["total_score"] < 40
        assert "全部一字板" in str(result.get("warnings", []))
