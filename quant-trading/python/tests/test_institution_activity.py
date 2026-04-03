"""Tests for institution_activity helpers — quarterly dates, cache, event filtering (no network required)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

from scripts.institution_activity import (
    _amount_score,
    _clean_text,
    _latest_report_quarter,
    _load_fund_hold_cached,
    _make_event,
    _prev_report_quarter,
    _HOLDER_TYPES,
)


# ── Quarterly Date Helpers ──────────────────────────────────────


class TestLatestReportQuarter:
    def test_january(self):
        assert _latest_report_quarter(datetime(2026, 1, 15)) == "20250930"

    def test_february(self):
        assert _latest_report_quarter(datetime(2026, 2, 28)) == "20250930"

    def test_march(self):
        assert _latest_report_quarter(datetime(2026, 3, 10)) == "20251231"

    def test_june(self):
        assert _latest_report_quarter(datetime(2026, 6, 5)) == "20260331"

    def test_september(self):
        assert _latest_report_quarter(datetime(2026, 9, 20)) == "20260630"

    def test_december(self):
        assert _latest_report_quarter(datetime(2026, 12, 25)) == "20260930"


class TestPrevReportQuarter:
    def test_q1_to_prev_q4(self):
        assert _prev_report_quarter("20260331") == "20251231"

    def test_q2_to_prev_q1(self):
        assert _prev_report_quarter("20260630") == "20260331"

    def test_q3_to_prev_q2(self):
        assert _prev_report_quarter("20260930") == "20260630"

    def test_q4_to_prev_q3(self):
        assert _prev_report_quarter("20261231") == "20260930"

    def test_roundtrip(self):
        q = "20260630"
        prev = _prev_report_quarter(q)
        assert prev == "20260331"
        prev_prev = _prev_report_quarter(prev)
        assert prev_prev == "20251231"


# ── Holder Types Config ─────────────────────────────────────────


class TestHolderTypes:
    def test_all_six_types_present(self):
        assert len(_HOLDER_TYPES) == 6

    def test_labels(self):
        labels = [label for label, _ in _HOLDER_TYPES]
        assert "基金持仓" in labels
        assert "QFII持仓" in labels
        assert "社保持仓" in labels
        assert "券商持仓" in labels
        assert "保险持仓" in labels
        assert "信托持仓" in labels

    def test_tags_unique(self):
        tags = [tag for _, tag in _HOLDER_TYPES]
        assert len(tags) == len(set(tags))


# ── Cache Layer ─────────────────────────────────────────────────


class TestFundHoldCache:
    def test_cache_hit(self, tmp_path):
        cache_dir = str(tmp_path)
        data = {
            "600519": {"name": "贵州茅台", "holders": 120, "totalShares": 5000000,
                       "totalValue": 3e10, "change": "增仓", "changePct": 15.0, "changeQty": 1000},
        }
        cache_file = os.path.join(cache_dir, "fund_hold_基金持仓_20250930.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        warnings: list[str] = []
        with patch("scripts.institution_activity._FUND_HOLD_CACHE_DIR", cache_dir):
            result = _load_fund_hold_cached("基金持仓", "20250930", warnings)

        assert result is not None
        assert "600519" in result
        assert result["600519"]["name"] == "贵州茅台"
        assert len(warnings) == 0

    def test_cache_miss_no_akshare(self, tmp_path):
        cache_dir = str(tmp_path)
        warnings: list[str] = []
        with patch("scripts.institution_activity._FUND_HOLD_CACHE_DIR", cache_dir):
            with patch.dict("sys.modules", {"akshare": None}):
                result = _load_fund_hold_cached("基金持仓", "20250930", warnings)
        # Without akshare, returns None
        assert result is None


# ── Scoring ─────────────────────────────────────────────────────


class TestAmountScore:
    def test_high_tier(self):
        assert _amount_score(250, [(200, 7.5), (100, 6.0), (50, 4.5)], 4.0) == 7.5

    def test_mid_tier(self):
        assert _amount_score(150, [(200, 7.5), (100, 6.0), (50, 4.5)], 4.0) == 6.0

    def test_low_tier(self):
        assert _amount_score(60, [(200, 7.5), (100, 6.0), (50, 4.5)], 4.0) == 4.5

    def test_fallback(self):
        assert _amount_score(10, [(200, 7.5), (100, 6.0), (50, 4.5)], 4.0) == 4.0

    def test_negative_uses_abs(self):
        assert _amount_score(-250, [(200, 7.5), (100, 6.0)], 3.0) == 7.5


# ── Clean Text ──────────────────────────────────────────────────


class TestCleanText:
    def test_normal_string(self):
        assert _clean_text("hello") == "hello"

    def test_nan_string(self):
        assert _clean_text("nan") == ""

    def test_none_value(self):
        assert _clean_text(None) == ""

    def test_float_nan(self):
        assert _clean_text(float("nan")) == ""


# ── Make Event ──────────────────────────────────────────────────


class TestMakeEvent:
    def test_basic_event(self):
        event = _make_event(
            symbol="600519",
            trade_date="2026-03-12",
            source="institutional_hold",
            source_label="基金持仓",
            event_type="fund_new_entry",
            evidence_type="build",
            direction="buy",
            title="基金持仓新进",
            detail="基金持仓 25家持仓",
            score_impact=5.5,
        )
        assert event["symbol"] == "600519"
        assert event["tradeDate"] == "2026-03-12"
        assert event["source"] == "institutional_hold"
        assert event["evidenceType"] == "build"
        assert event["direction"] == "buy"
        assert event["scoreImpact"] == 5.5

    def test_score_rounded(self):
        event = _make_event(
            symbol="000001",
            trade_date="2026-03-12",
            source="test",
            source_label="t",
            event_type="t",
            evidence_type="build",
            direction="buy",
            title="t",
            detail="t",
            score_impact=5.567,
        )
        assert event["scoreImpact"] == 5.6

    def test_raw_source_fields_default(self):
        event = _make_event(
            symbol="000001",
            trade_date="2026-03-12",
            source="test",
            source_label="t",
            event_type="t",
            evidence_type="build",
            direction="buy",
            title="t",
            detail="t",
            score_impact=5.0,
        )
        assert event["rawSourceFields"] == {}
