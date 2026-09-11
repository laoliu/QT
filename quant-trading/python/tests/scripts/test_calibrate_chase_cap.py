"""calibrate_chase_cap 脚本的读取/构建层测试。

只测不依赖行情源的部分: 状态文件解析、可用性统计、记录构建、
退化数据告警、按状态切分、signal_price 重建（行情源用替身）。
校准算法本身在 tests/advisory/test_chase_calibration.py 覆盖。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.calibrate_chase_cap import (
    SOURCE_RECONSTRUCTED,
    backfill_signal_prices,
    build_records,
    format_backfill,
    load_trades,
    main,
    split_by_regime,
    summarize_coverage,
    warn_if_degenerate,
)


def _trade(
    code: str = "600000.SH",
    side: str = "buy",
    price: float | None = 10.3,
    signal_price: float | None = 10.0,
    date: str = "20250106",
    **extra,
) -> dict:
    t = {
        "code": code, "side": side, "price": price, "shares": 100,
        "amount": 1030.0, "cost": 5.0, "date": date, "component": "c1",
    }
    if signal_price is not None:
        t["signal_price"] = signal_price
    t.update(extra)
    return t


def _write_state(tmp_path: Path, trades: list[dict], key: str = "trades") -> Path:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({key: trades}, ensure_ascii=False), encoding="utf-8",
    )
    return path


class TestLoadTrades:
    """状态文件解析"""

    def test_reads_trades_key(self, tmp_path) -> None:
        path = _write_state(tmp_path, [_trade(), _trade(code="000001.SZ")])

        assert len(load_trades(path)) == 2

    def test_reads_bare_list(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps([_trade()]), encoding="utf-8")

        assert len(load_trades(path)) == 1

    def test_accepts_ts_code_and_trade_date(self, tmp_path) -> None:
        """历史上两套命名都出现过"""
        path = _write_state(tmp_path, [{
            "ts_code": "600519.SH", "side": "BUY", "price": 1700.0,
            "trade_date": "20250106", "signal_price": 1680.0,
        }])

        trade = load_trades(path)[0]

        assert trade["code"] == "600519.SH"
        assert trade["date"] == "20250106"
        assert trade["side"] == "buy", "side 应归一为小写"

    def test_missing_signal_price_is_none(self, tmp_path) -> None:
        path = _write_state(tmp_path, [_trade(signal_price=None)])

        assert load_trades(path)[0]["signal_price"] is None

    def test_skips_non_dict_entries(self, tmp_path) -> None:
        path = _write_state(tmp_path, [_trade(), "garbage", None])  # type: ignore[list-item]

        assert len(load_trades(path)) == 1

    def test_rejects_non_list_trades(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"trades": {"a": 1}}), encoding="utf-8")

        with pytest.raises(ValueError, match="未找到交易记录列表"):
            load_trades(path)

    def test_empty_state(self, tmp_path) -> None:
        assert load_trades(_write_state(tmp_path, [])) == []


class TestCoverage:
    """可用性统计 —— 先把「数据够不够」讲清楚"""

    def test_counts_each_stage(self) -> None:
        trades = [
            _trade(),                                   # 可用
            _trade(side="sell"),                        # 非买入
            _trade(signal_price=None),                  # 无信号价
            _trade(price=None),                         # 无成交价
            _trade(signal_price=0.0),                   # 信号价非正
        ]

        cov = summarize_coverage(trades)

        assert cov == {
            "total": 5, "buys": 4, "with_signal_price": 2, "usable": 1,
            "recorded": 1, "reconstructed": 0,
        }

    def test_empty(self) -> None:
        assert summarize_coverage([])["usable"] == 0


class TestBuildRecords:
    """ExecutionRecord 构建"""

    def test_arrival_equals_fill(self) -> None:
        """两阶段模型下可下单价与成交价同一个数，追高全在 delay_bps"""
        rec = build_records([_trade()], {})[0]

        assert rec.arrival_price == rec.fill_price == pytest.approx(10.3)
        assert rec.execution_bps == pytest.approx(0.0)
        assert rec.delay_bps == pytest.approx(300.0, rel=1e-6)

    def test_forward_prices_joined_by_code_and_date(self) -> None:
        forward = {("600000.SH", "20250106"): [10.5, 10.1]}

        rec = build_records([_trade()], forward)[0]

        assert rec.forward_prices == [10.5, 10.1]

    def test_missing_forward_leaves_empty(self) -> None:
        rec = build_records([_trade()], {})[0]

        assert rec.forward_prices == []

    def test_sells_dropped(self) -> None:
        assert build_records([_trade(side="sell")], {}) == []

    def test_records_without_signal_price_dropped(self) -> None:
        assert build_records([_trade(signal_price=None)], {}) == []

    def test_fees_and_qty_carried(self) -> None:
        rec = build_records([_trade()], {})[0]

        assert rec.qty == 100
        assert rec.fees == pytest.approx(5.0)


class TestDegenerateWarning:
    """成交价==信号价的退化数据"""

    def test_warns_when_all_identical(self, caplog) -> None:
        records = build_records(
            [_trade(price=10.0, signal_price=10.0) for _ in range(3)], {},
        )

        with caplog.at_level(logging.WARNING):
            warn_if_degenerate(records)

        assert "追高幅度恒为 0" in caplog.text
        assert "traded_price" in caplog.text, "应指出正确的数据来源"

    def test_warns_when_majority_identical(self, caplog) -> None:
        records = build_records(
            [_trade(price=10.0, signal_price=10.0) for _ in range(3)]
            + [_trade()],
            {},
        )

        with caplog.at_level(logging.WARNING):
            warn_if_degenerate(records)

        assert "偏向 0" in caplog.text

    def test_quiet_on_healthy_data(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            warn_if_degenerate(build_records([_trade()], {}))

        assert caplog.text == ""

    def test_empty_is_quiet(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            warn_if_degenerate([])

        assert caplog.text == ""


class TestSplitByRegime:
    """按市场状态切分"""

    @pytest.fixture
    def regime_file(self, tmp_path) -> Path:
        path = tmp_path / "regimes.json"
        path.write_text(json.dumps({
            "20250106": "BULL", "20250107": "bear",
        }), encoding="utf-8")
        return path

    def test_groups_by_regime(self, regime_file) -> None:
        trades = [_trade(date="20250106"), _trade(date="20250107")]

        groups = split_by_regime(trades, {}, regime_file)

        assert set(groups) == {"BULL", "BEAR"}
        assert len(groups["BULL"]) == len(groups["BEAR"]) == 1

    def test_case_insensitive(self, regime_file) -> None:
        """小写状态名也要归到 BEAR"""
        groups = split_by_regime([_trade(date="20250107")], {}, regime_file)

        assert "BEAR" in groups

    def test_unmapped_dates_skipped(self, regime_file, caplog) -> None:
        trades = [_trade(date="20250106"), _trade(date="20991231")]

        with caplog.at_level(logging.WARNING):
            groups = split_by_regime(trades, {}, regime_file)

        assert sum(len(v) for v in groups.values()) == 1
        assert "状态映射中缺失" in caplog.text

    def test_nested_regimes_key(self, tmp_path) -> None:
        path = tmp_path / "regimes.json"
        path.write_text(
            json.dumps({"regimes": {"20250106": "NEUTRAL"}}), encoding="utf-8",
        )

        groups = split_by_regime([_trade()], {}, path)

        assert "NEUTRAL" in groups

    def test_rejects_bad_mapping(self, tmp_path) -> None:
        path = tmp_path / "regimes.json"
        path.write_text(json.dumps(["BULL"]), encoding="utf-8")

        with pytest.raises(ValueError, match="日期→状态"):
            split_by_regime([_trade()], {}, path)


class _FakeCalendar:
    """把前一交易日定为前一天，跳过周末 —— 够用且可预测。"""

    def get_prev_trading_day(self, d: date, skip_days: int = 1) -> date:
        cur = d
        found = 0
        while found < skip_days:
            cur -= timedelta(days=1)
            if cur.weekday() < 5:
                found += 1
        return cur


def _patch_backfill_sources(prices_by_date: dict[str, dict[str, float]]):
    """替换交易日历与取价函数。

    prices_by_date: 信号日 → {code: close}。缺失的日期视为取价抛错。
    """
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_fetch(gw, codes, trade_date, **kwargs):
        calls.append((trade_date, tuple(codes)))
        if trade_date not in prices_by_date:
            raise RuntimeError(f"no data for {trade_date}")
        return dict(prices_by_date[trade_date])

    return (
        patch("aipha.data.calendar.CNStockCalendar", _FakeCalendar),
        patch("aipha.execution.trading_ops.fetch_prices", fake_fetch),
        calls,
    )


class TestBackfillSignalPrice:
    """从历史日线重建 signal_price。

    引擎落盘的 signal_price 就是信号日收盘价（fetch_prices 的 close），
    这里调同一个函数取同一个字段，所以重建值应与原生落盘一致。
    """

    def _run(self, trades, prices_by_date):
        cal_patch, fetch_patch, calls = _patch_backfill_sources(prices_by_date)
        with cal_patch, fetch_patch:
            stats = backfill_signal_prices(object(), trades)
        return stats, calls

    def test_fills_from_recorded_signal_date(self) -> None:
        trades = load_trades_inline([
            _trade(signal_price=None, date="20250107", signal_date="20250106"),
        ])

        stats, calls = self._run(
            trades, {"20250106": {"600000.SH": 9.87}},
        )

        assert stats.filled == 1
        assert stats.exact_date == 1
        assert stats.inferred_date == 0
        assert trades[0]["signal_price"] == 9.87
        assert trades[0]["signal_price_source"] == SOURCE_RECONSTRUCTED
        assert calls == [("20250106", ("600000.SH",))]

    def test_infers_prev_trading_day_when_signal_date_absent(self) -> None:
        """没有 signal_date 时按「T 出信号、T+1 开盘成交」倒推。"""
        trades = load_trades_inline([_trade(signal_price=None, date="20250107")])

        stats, calls = self._run(trades, {"20250106": {"600000.SH": 9.5}})

        assert stats.filled == 1
        assert stats.inferred_date == 1
        assert stats.exact_date == 0
        assert trades[0]["signal_date"] == "20250106"
        assert calls == [("20250106", ("600000.SH",))]

    def test_weekend_skipped_when_inferring(self) -> None:
        # 20250106 是周一，前一交易日应为上周五 20250103
        trades = load_trades_inline([_trade(signal_price=None, date="20250106")])

        stats, calls = self._run(trades, {"20250103": {"600000.SH": 9.1}})

        assert stats.filled == 1
        assert calls == [("20250103", ("600000.SH",))]

    def test_recorded_signal_price_never_overwritten(self) -> None:
        """原生落盘值是基准 —— 重建不能覆盖它。"""
        trades = load_trades_inline([
            _trade(signal_price=10.0, date="20250107"),
        ])

        stats, calls = self._run(trades, {"20250106": {"600000.SH": 99.0}})

        assert stats.considered == 0
        assert stats.filled == 0
        assert trades[0]["signal_price"] == 10.0
        assert calls == []  # 完全没必要发请求

    def test_sells_ignored(self) -> None:
        """追高校准只用买入样本，卖出不必重建。"""
        trades = load_trades_inline([
            _trade(side="sell", signal_price=None, date="20250107"),
        ])

        stats, calls = self._run(trades, {"20250106": {"600000.SH": 9.0}})

        assert stats.considered == 0
        assert calls == []

    def test_same_signal_date_batched_into_one_call(self) -> None:
        trades = load_trades_inline([
            _trade(code="600000.SH", signal_price=None, date="20250107"),
            _trade(code="000001.SZ", signal_price=None, date="20250107"),
        ])

        stats, calls = self._run(
            trades,
            {"20250106": {"600000.SH": 9.0, "000001.SZ": 12.0}},
        )

        assert stats.filled == 2
        assert len(calls) == 1
        assert calls[0][1] == ("000001.SZ", "600000.SH")

    def test_missing_close_skipped_with_reason(self) -> None:
        """停牌/未上市 → 该笔补不上，但要说明原因而不是静默丢弃。"""
        trades = load_trades_inline([
            _trade(code="600000.SH", signal_price=None, date="20250107"),
            _trade(code="999999.SZ", signal_price=None, date="20250107"),
        ])

        stats, _ = self._run(trades, {"20250106": {"600000.SH": 9.0}})

        assert stats.considered == 2
        assert stats.filled == 1
        assert sum(stats.skipped.values()) == 1
        assert trades[1].get("signal_price") in (None, "")

    def test_zero_price_treated_as_missing(self) -> None:
        trades = load_trades_inline([_trade(signal_price=None, date="20250107")])

        stats, _ = self._run(trades, {"20250106": {"600000.SH": 0.0}})

        assert stats.filled == 0
        assert sum(stats.skipped.values()) == 1

    def test_fetch_failure_does_not_abort_other_dates(self) -> None:
        """某个信号日取不到行情，其他日期照常重建。"""
        trades = load_trades_inline([
            _trade(signal_price=None, date="20250107"),
            _trade(signal_price=None, date="20250110"),
        ])

        stats, _ = self._run(trades, {"20250109": {"600000.SH": 9.0}})

        assert stats.filled == 1
        assert stats.skipped.get("信号日行情获取失败") == 1
        assert stats.dates_fetched == 1

    def test_unparseable_fill_date_skipped(self) -> None:
        trades = load_trades_inline([_trade(signal_price=None, date="")])

        stats, calls = self._run(trades, {})

        assert stats.filled == 0
        assert stats.skipped.get("成交日缺失或无法解析") == 1
        assert calls == []

    def test_no_work_needed_returns_empty_stats(self) -> None:
        stats, calls = self._run(load_trades_inline([_trade()]), {})

        assert stats.considered == 0
        assert calls == []

    def test_internal_marker_not_leaked(self) -> None:
        """临时字段不能留在记录里 —— 这些 dict 会被写进 JSON 报告。"""
        trades = load_trades_inline([
            _trade(signal_price=None, date="20250107"),
            _trade(code="X.SZ", signal_price=None, date=""),
        ])

        self._run(trades, {"20250106": {"600000.SH": 9.0}})

        for t in trades:
            assert "_signal_date_exact" not in t

    def test_coverage_splits_recorded_and_reconstructed(self) -> None:
        trades = load_trades_inline([
            _trade(code="600000.SH", signal_price=10.0, date="20250107"),
            _trade(code="000001.SZ", signal_price=None, date="20250107"),
        ])

        self._run(trades, {"20250106": {"000001.SZ": 12.0}})
        cov = summarize_coverage(trades)

        assert cov["usable"] == 2
        assert cov["recorded"] == 1
        assert cov["reconstructed"] == 1

    def test_reconstructed_records_feed_calibration(self) -> None:
        """重建完必须真能进 build_records —— 否则等于没补。"""
        trades = load_trades_inline([
            _trade(signal_price=None, date="20250107", price=10.3),
        ])

        self._run(trades, {"20250106": {"600000.SH": 10.0}})
        records = build_records(trades, {})

        assert len(records) == 1
        assert records[0].signal_price == 10.0
        assert records[0].fill_price == 10.3


class TestFormatBackfill:
    """报告文本"""

    def test_nothing_to_do(self) -> None:
        from scripts.calibrate_chase_cap import BackfillStats

        assert "无需重建" in format_backfill(BackfillStats())

    def test_inferred_dates_get_a_caveat(self) -> None:
        from scripts.calibrate_chase_cap import BackfillStats

        text = format_backfill(BackfillStats(
            considered=3, filled=3, exact_date=1, inferred_date=2,
        ))

        assert "倒推" in text
        assert "参考值" in text

    def test_exact_only_has_no_caveat(self) -> None:
        from scripts.calibrate_chase_cap import BackfillStats

        text = format_backfill(BackfillStats(
            considered=2, filled=2, exact_date=2,
        ))

        assert "参考值" not in text

    def test_skip_reasons_listed(self) -> None:
        from scripts.calibrate_chase_cap import BackfillStats

        text = format_backfill(BackfillStats(
            considered=2, filled=0, skipped={"停牌": 2},
        ))

        assert "停牌" in text
        assert "未能重建" in text


def load_trades_inline(trades: list[dict]) -> list[dict]:
    """走一遍 load_trades 的字段归一化，不落盘。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.json"
        path.write_text(json.dumps({"trades": trades}), encoding="utf-8")
        return load_trades(path)


class TestMain:
    """入口的早退路径（不触发行情拉取）"""

    def test_missing_state_file(self, tmp_path, capsys) -> None:
        assert main(["--state", str(tmp_path / "nope.json")]) == 2

    def test_legacy_records_without_signal_price(self, tmp_path, capsys) -> None:
        """旧记录没有 signal_price —— 要说清怎么办，而不是报错退出"""
        path = _write_state(tmp_path, [_trade(signal_price=None)])

        code = main(["--state", str(path)])
        out = capsys.readouterr().out

        assert code == 1
        assert "旧格式的记录" in out
        assert "继续运行即可积累" in out

    def test_coverage_printed(self, tmp_path, capsys) -> None:
        path = _write_state(tmp_path, [_trade(signal_price=None)])

        main(["--state", str(path)])

        assert "带信号价 0 笔" in capsys.readouterr().out

    def test_no_trades_reports_zero(self, tmp_path, capsys) -> None:
        """空记录不该走到拉行情 —— 没有日期可拉"""
        path = _write_state(tmp_path, [])

        code = main(["--state", str(path)])

        assert code == 1
        assert "交易记录 0 笔" in capsys.readouterr().out

    def test_legacy_hint_mentions_backfill_flag(self, tmp_path, capsys) -> None:
        """旧记录的提示要给出「现在就能用」的出路，而不只是让人等。"""
        path = _write_state(tmp_path, [_trade(signal_price=None)])

        main(["--state", str(path)])

        assert "--backfill-signal-price" in capsys.readouterr().out

    def test_backfill_needs_token(self, tmp_path, capsys, monkeypatch) -> None:
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        path = _write_state(tmp_path, [_trade(signal_price=None)])

        code = main(["--state", str(path), "--backfill-signal-price"])

        assert code == 2

    def test_backfill_failure_message_differs(
        self, tmp_path, capsys, monkeypatch,
    ) -> None:
        """重建后仍无数据 → 提示改成「看未能重建的原因」，别再叫人等积累。"""
        monkeypatch.setenv("TUSHARE_TOKEN", "x")
        path = _write_state(tmp_path, [_trade(signal_price=None)])
        cal_patch, fetch_patch, _ = _patch_backfill_sources({})

        with patch(
            "aipha.data.tushare_gateway.TuShareGateway", lambda **kw: object(),
        ), cal_patch, fetch_patch:
            code = main(["--state", str(path), "--backfill-signal-price"])

        out = capsys.readouterr().out
        assert code == 1
        assert "仍没有可用的 signal_price" in out
        assert "继续运行即可积累" not in out

    def test_reconstructed_warning_printed(
        self, tmp_path, capsys, monkeypatch,
    ) -> None:
        """混了重建样本必须显式告警 —— 这个数会被拿去改风控参数。"""
        monkeypatch.setenv("TUSHARE_TOKEN", "x")
        path = _write_state(
            tmp_path, [_trade(signal_price=None, date="20250107")],
        )
        cal_patch, fetch_patch, _ = _patch_backfill_sources(
            {"20250106": {"600000.SH": 10.0}},
        )

        with patch(
            "aipha.data.tushare_gateway.TuShareGateway", lambda **kw: object(),
        ), cal_patch, fetch_patch, patch(
            "scripts.calibrate_chase_cap.fetch_forward_prices", return_value={},
        ):
            main(["--state", str(path), "--backfill-signal-price"])

        out = capsys.readouterr().out
        assert "事后重建的" in out
        assert "重建 1" in out
