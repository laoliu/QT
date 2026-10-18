from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aipha.research import (
    ResearchConfig,
    ResearchPipeline,
    build_forward_labels,
    build_target_positions,
    load_research_config,
    validate_feature_frame,
    validate_market_frame,
    visible_financial_snapshots,
    SnapshotSpec,
    assemble_tushare_snapshot,
    join_pit_financials,
    point_in_time_adjust_prices,
    build_cn_equity_features,
    historical_st_status_from_provider,
    migrate_legacy_model,
)
from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import FinancialSnapshot, TargetPosition
from aipha.research.pipeline import _filter_historical_universe
from aipha.research.artifacts import load_verified_selector
from aipha.research.execution_plan import build_execution_plan, execute_target_plan, read_execution_plan
from aipha.research.snapshots import (
    SNAPSHOT_GENERATOR_VERSION,
    attest_snapshot_metadata,
    financial_snapshots_from_provider,
    frame_content_sha256,
)


def _panel(n_days: int = 360, n_symbols: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    rows = []
    for symbol_idx in range(n_symbols):
        symbol = f"{600000 + symbol_idx:06d}.SH"
        price = 10.0 + symbol_idx
        for dt in dates:
            signal = rng.normal()
            ret = 0.0005 + 0.004 * signal + rng.normal(0, 0.005)
            price = max(1.0, price * (1.0 + ret))
            rows.append({
                "trade_date": dt,
                "symbol": symbol,
                "open": price * 0.999,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 100000.0,
                "amount": price * 100000.0,
                "adj_factor": 1.0,
                "price_adjustment": "point_in_time_factor",
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_index_member": True,
                "is_st": False,
                "listing_days": 1000,
                "listing_days_basis": "exchange_open_sessions",
                "feature_version": "cn_equity_v1",
                "momentum_signal": signal,
                "momentum_20": signal,
                "volatility_20": abs(signal),
                "benchmark_close": 100.0 + dates.get_loc(dt),
            })
    return pd.DataFrame(rows)


def test_default_yaml_is_canonical_and_safe() -> None:
    path = Path(__file__).parents[2] / "aipha" / "configs" / "research" / "a_stock_ml.yaml"
    config = load_research_config(path)
    assert config.data_provider == "tushare_pit"
    assert config.adjust == "point_in_time"
    assert config.feature_version == "cn_equity_v1"
    assert config.rebalance == "monthly"
    assert config.forward_days == config.purge_days == 20
    assert config.label_skip_days == 1
    app_config = json.loads((Path(__file__).parents[3] / "config.json").read_text(encoding="utf-8"))
    assert app_config["tradingAccount"]["mode"] == "paper"
    assert app_config["tradingAccount"]["broker"] == "paper"
    assert app_config["tradingAccount"]["autoStart"] is False
    assert app_config["tradingAccount"]["confirmationRequired"] is True


def test_config_rejects_non_paper_and_non_pit_modes(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("data:\n  provider: akshare\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tushare_pit"):
        load_research_config(cfg)
    cfg.write_text("data:\n  provider: tushare_pit\n  adjust: qfq\n", encoding="utf-8")
    with pytest.raises(ValueError, match="point_in_time"):
        load_research_config(cfg)


def test_canonical_config_rejects_same_day_label_entry() -> None:
    with pytest.raises(ValueError, match="label_skip_days"):
        ResearchConfig(label_skip_days=0)


def test_parquet_bridge_requires_provenance_sidecar(tmp_path: Path) -> None:
    from aipha.engines.aipha_ml_engine import _read_parquet, run

    assert run({"action": "list_models"})["data"]["models"] == ["lgbm", "linear"]
    path = tmp_path / "fixture.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(path)
    with pytest.raises(ValueError, match="sidecar required"):
        _read_parquet(str(path))
    path.with_suffix(".metadata.json").write_text(json.dumps({
        "provider": "akshare", "data_snapshot_id": "bad", "feature_version": "v1",
        "start_date": "2024-01-01", "end_date": "2024-01-02",
    }), encoding="utf-8")
    assert _read_parquet(str(path)).attrs["snapshot_metadata"]["provider"] == "akshare"


def test_market_frame_rejects_duplicate_rows_and_normalizes_dates() -> None:
    frame = _panel(n_days=1, n_symbols=1)
    normalized = validate_market_frame(frame)
    assert normalized.loc[0, "trade_date"].isoformat() == "2022-01-03"
    with pytest.raises(ValueError, match="duplicate"):
        validate_market_frame(pd.concat([frame, frame], ignore_index=True))


def test_feature_frame_requires_version_and_unique_symbol_date() -> None:
    frame = pd.DataFrame({
        "trade_date": ["2024-01-02"], "symbol": ["600000.SH"],
        "feature_version": ["cn_equity_v1"], "momentum": [0.2],
    })
    assert validate_feature_frame(frame).loc[0, "feature_version"] == "cn_equity_v1"
    with pytest.raises(ValueError, match="feature_version"):
        validate_feature_frame(frame.drop(columns="feature_version"))


def test_universe_is_filtered_using_row_level_historical_membership() -> None:
    panel = _panel(n_days=2, n_symbols=2)
    panel.loc[panel.index[0], "is_index_member"] = False
    panel.loc[panel.index[1], "is_st"] = True
    filtered = _filter_historical_universe(panel)
    assert len(filtered) == len(panel) - 2
    assert filtered["is_index_member"].all()
    assert not filtered["is_st"].any()


def test_forward_labels_respect_t_plus_one_skip() -> None:
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-01", periods=4),
        "symbol": ["600000.SH"] * 4,
        "open": [10, 11, 12, 13], "high": [10, 11, 12, 13],
        "low": [10, 11, 12, 13], "close": [10, 12, 15, 20],
        "volume": [1, 1, 1, 1], "amount": [10, 12, 15, 20],
    })
    labelled = build_forward_labels(frame, horizon_days=1, skip_days=1)
    # Signal after day 0 close enters day 1 open and exits day 2 close.
    assert labelled.iloc[0]["fwd_return"] == pytest.approx(15 / 11 - 1)
    assert len(labelled) == 2
    assert "fwd_return" not in frame


def test_training_labels_use_complete_tape_before_signal_universe_filter(monkeypatch) -> None:
    from aipha.models.ml_stock_selector import MLStockSelector

    frame = _panel(n_days=8, n_symbols=3)
    selected = frame["symbol"] == "600000.SH"
    dates = sorted(frame["trade_date"].unique())
    frame.loc[selected, "open"] = np.arange(10.0, 18.0)
    frame.loc[selected, "close"] = np.arange(20.0, 28.0)
    # A later status change must not alter an earlier signal's holding dates.
    frame.loc[selected & frame["trade_date"].isin(dates[1:3]), "is_st"] = True
    frame["open_raw"] = 999.0
    frame["up_limit"] = 1000.0
    frame["down_limit"] = 0.01
    observed = {}

    def capture(selector, labelled):
        observed["labelled"] = labelled
        observed["features"] = selector.config.feature_cols
        return labelled[["datetime", "symbol", "fwd_return"]].assign(pred_score=0.5)

    monkeypatch.setattr(MLStockSelector, "walk_forward_train", capture)
    ResearchPipeline(ResearchConfig(forward_days=1)).train_oos(frame)
    labelled = observed["labelled"]
    first_signal = labelled.loc[(labelled["symbol"] == "600000.SH") & (labelled["datetime"] == dates[0])]
    assert first_signal.iloc[0]["fwd_return"] == pytest.approx(22 / 11 - 1)
    assert not labelled["is_st"].any()
    assert "open_raw" not in observed["features"]
    assert "up_limit" not in observed["features"]


def test_financial_facts_are_filtered_by_announcement_date() -> None:
    rows = [
        FinancialSnapshot("600000.SH", pd.Timestamp("2023-12-31").date(), pd.Timestamp("2024-03-20").date(), "roe", 0.1),
        FinancialSnapshot("600000.SH", pd.Timestamp("2024-03-31").date(), pd.Timestamp("2024-05-01").date(), "roe", 0.2),
    ]
    visible = visible_financial_snapshots(rows, pd.Timestamp("2024-04-01").date())
    assert [row.value for row in visible] == [0.1]


def test_tushare_snapshot_materializes_announced_indicator_and_statement_history() -> None:
    from datetime import date
    from types import SimpleNamespace

    class PITFixture:
        def get_indicator_history(self, symbol: str):
            return [SimpleNamespace(
                report_date=SimpleNamespace(to_date=lambda: date(2023, 12, 31)),
                announce_date=date(2024, 3, 15),
                indicators={"roe": 0.12},
            )]

        def get_statement_history(self, symbol, start_date, end_date, statement_type):
            values = {
                "income": {"revenue": 1200.0},
                "balance": {"total_assets": 3500.0},
                "cashflow": {"cash_from_operations": 450.0},
            }
            announced_before_window = SimpleNamespace(
                report_date=SimpleNamespace(to_date=lambda: date(2022, 12, 31)),
                announce_date=date(2023, 3, 20),
                data={"revenue": 900.0} if statement_type == "income" else {},
            )
            announced_in_window = SimpleNamespace(
                report_date=SimpleNamespace(to_date=lambda: date(2023, 12, 31)),
                announce_date=date(2024, 3, 20),
                data=values[statement_type],
            )
            assert start_date == date.min
            return [announced_before_window, announced_in_window]

    facts = financial_snapshots_from_provider(
        PITFixture(), ["600000.SH"],
        start_date=date(2024, 1, 1), end_date=date(2024, 3, 31),
    )
    assert set(facts["metric_name"]) == {
        "roe", "revenue", "total_assets", "cash_from_operations",
    }
    panel = join_pit_financials(pd.DataFrame({
        "trade_date": ["2024-03-14", "2024-03-15", "2024-03-19", "2024-03-20"],
        "symbol": ["600000.SH"] * 4,
    }), facts)
    assert pd.isna(panel.loc[0, "roe"])
    assert panel.loc[1, "roe"] == pytest.approx(0.12)
    assert panel.loc[0, "revenue"] == pytest.approx(900.0)
    assert panel.loc[2, "revenue"] == pytest.approx(900.0)
    assert panel.loc[3, "revenue"] == pytest.approx(1200.0)
    assert panel.loc[3, "total_assets"] == pytest.approx(3500.0)
    assert panel.loc[3, "cash_from_operations"] == pytest.approx(450.0)


def test_tushare_snapshot_uses_historical_membership_pit_and_same_day_factor() -> None:
    market = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH", "600001.SH", "600001.SH"],
        "trade_date": ["20240102", "20240103", "20240102", "20240103"],
        "open": [10, 11, 20, 21], "high": [10, 11, 20, 21],
        "low": [10, 11, 20, 21], "close": [10, 11, 20, 21],
        "vol": [100, 100, 100, 100], "amount": [1000, 1100, 2000, 2100],
        "adj_factor": [1.0, 2.0, 1.0, 1.0],
    })
    stock = pd.DataFrame({"ts_code": ["600000.SH", "600001.SH"], "list_date": ["20200101", "20200101"]})
    status = pd.DataFrame({
        "ts_code": ["600000.SH"] * 2 + ["600001.SH"] * 2,
        "trade_date": ["20240102", "20240103", "20240102", "20240103"],
        "is_st": [False] * 4,
    })
    limits = pd.DataFrame({
        "ts_code": ["600000.SH"] * 2 + ["600001.SH"] * 2,
        "trade_date": ["20240102", "20240103", "20240102", "20240103"],
        "up_limit": [10, 11, 22, 22], "down_limit": [8, 9, 18, 19],
    })
    facts = pd.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "report_date": ["2023-12-31", "2023-12-31"],
        "announce_date": ["2024-01-02", "2024-01-04"],
        "metric_name": ["roe", "roe"], "value": [0.1, 0.2],
    })
    industries = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH", "600001.SH"],
        "industry_code": ["801010.SI", "801020.SI", "801010.SI"],
        "industry_name": ["农林牧渔", "采掘", "农林牧渔"],
        "in_date": ["20200101", "20240103", "20200101"],
        "out_date": ["20240102", None, None],
    })
    snapshot = assemble_tushare_snapshot(
        market, daily_basic=None, stock_basic=stock,
        index_snapshots={"20240101": ["600000.SH"], "20240103": ["600001.SH"]},
        st_status=status, limit_prices=limits, financial_snapshots=facts,
        industry_history=industries,
        trade_calendar=pd.DataFrame({
            "cal_date": pd.date_range("2020-01-01", "2024-01-03"),
            "is_open": pd.date_range("2020-01-01", "2024-01-03").dayofweek < 5,
        }),
        spec=SnapshotSpec("20240102", "20240103"),
    )
    panel = snapshot.panel.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    assert panel.loc[0, "close"] == pytest.approx(10.0)
    assert panel.loc[1, "close"] == pytest.approx(20.0)
    assert bool(panel.loc[0, "is_index_member"]) is True
    assert bool(panel.loc[2, "is_index_member"]) is False
    assert panel.loc[0, "roe"] == pytest.approx(0.1)
    assert pd.isna(panel.loc[1, "roe"])
    assert bool(panel.loc[2, "is_limit_up"]) is True
    assert panel.loc[0, "industry"] == "农林牧渔"
    assert panel.loc[2, "industry"] == "采掘"
    assert snapshot.metadata["provider"] == "tushare_pit"
    assert snapshot.metadata["listing_calendar"]["start_date"] == "2020-01-01"
    assert snapshot.metadata["listing_calendar"]["listing_days_basis"] == "exchange_open_sessions"
    assert panel.listing_days_basis.eq("exchange_open_sessions").all()
    assert panel.loc[0, "listing_days"] == len(pd.bdate_range("2020-01-01", "2024-01-02"))
    from copy import deepcopy
    from aipha.research.snapshots import verify_snapshot_signature

    tampered = deepcopy(snapshot.metadata)
    tampered["listing_calendar"]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="signature"):
        verify_snapshot_signature(tampered)


def test_historical_industry_rejects_overlapping_classifications() -> None:
    from aipha.research.snapshots import _historical_industry

    with pytest.raises(ValueError, match="ambiguous historical industry"):
        _historical_industry(
            pd.Series(["2024-01-03"]), pd.Series(["600000.SH"]),
            pd.DataFrame({
                "ts_code": ["600000.SH", "600000.SH"],
                "industry_name": ["农林牧渔", "采掘"],
                "in_date": ["20200101", "20240101"],
                "out_date": [None, None],
            }),
        )


def test_point_in_time_adjustment_rejects_missing_future_factor() -> None:
    frame = pd.DataFrame({"open": [10], "high": [10], "low": [10], "close": [10], "adj_factor": [None]})
    with pytest.raises(ValueError, match="adj_factor"):
        point_in_time_adjust_prices(frame)


def test_causal_features_do_not_change_when_future_prices_change() -> None:
    panel = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=30),
        "symbol": ["600000.SH"] * 30,
        "close": np.arange(10.0, 40.0),
        "volume": [100.0] * 30, "amount": [1000.0] * 30,
    })
    base = build_cn_equity_features(panel)
    changed = panel.copy()
    changed.loc[changed.index >= 25, "close"] *= 5
    revised = build_cn_equity_features(changed)
    pd.testing.assert_series_equal(base.loc[:24, "momentum_5"], revised.loc[:24, "momentum_5"])
    pd.testing.assert_series_equal(base.loc[:24, "volatility_20"], revised.loc[:24, "volatility_20"])


def test_historical_st_status_uses_namechange_intervals() -> None:
    class Provider:
        def load_namechange(self, ts_code: str, use_cache: bool = True) -> pd.DataFrame:
            assert ts_code == "600000.SH"
            return pd.DataFrame({
                "ts_code": [ts_code], "name": ["ST示例"],
                "ann_date": ["20240104"],
                "start_date": [pd.Timestamp("2024-01-03")],
                "end_date": [pd.Timestamp("2024-01-04")],
            })

    market = pd.DataFrame({
        "ts_code": ["600000.SH"] * 4,
        "trade_date": ["20240102", "20240103", "20240104", "20240105"],
    })
    result = historical_st_status_from_provider(Provider(), market)
    # Jan 3 is inside the effective name interval, but the announcement
    # arrived Jan 4, so the historical panel must not mark it ST beforehand.
    assert result["is_st"].tolist() == [False, False, True, False]


def test_historical_st_status_rejects_history_without_announcement_date() -> None:
    class Provider:
        def load_namechange(self, ts_code: str, use_cache: bool = True) -> pd.DataFrame:
            return pd.DataFrame({
                "ts_code": [ts_code], "name": ["ST示例"],
                "start_date": ["20240103"], "end_date": ["20240104"],
            })

    market = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20240103"]})
    with pytest.raises(ValueError, match="not PIT-complete.*ann_date"):
        historical_st_status_from_provider(Provider(), market)


def test_tushare_pipeline_entry_builds_snapshot_before_training(tmp_path: Path, monkeypatch) -> None:
    dates = ["20240102", "20240103"]
    symbols = ["600000.SH", "600001.SH"]
    market = pd.DataFrame([
        {
            "ts_code": symbol, "trade_date": day,
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "vol": 1000.0, "amount": 10000.0,
        }
        for day in dates for symbol in symbols
    ])

    class EquityProvider:
        def load_trade_calendar(self, start_date, end_date, **kwargs):
            days = pd.date_range(start_date, end_date)
            return pd.DataFrame({"cal_date": days, "is_open": days.dayofweek < 5})
        def load_daily_bars_range(self, *args, **kwargs): return market.copy()
        def load_daily_basic_range(self, *args, **kwargs): return pd.DataFrame()
        def load_stock_basic(self, **kwargs):
            return pd.DataFrame({"ts_code": symbols, "list_date": ["20200101"] * 2})
        def load_adj_factor_bulk(self, *args, **kwargs):
            return pd.DataFrame([{"ts_code": s, "trade_date": d, "adj_factor": 1.0} for d in dates for s in symbols])
        def load_index_members_snapshots(self, *args, **kwargs): return {"20240101": symbols}
        def load_index_bars_range(self, *args, **kwargs):
            return pd.DataFrame({"trade_date": dates, "close": [100.0, 101.0]})
        def load_limit_prices_range(self, *args, **kwargs):
            return pd.DataFrame([{"ts_code": s, "trade_date": d, "up_limit": 11.0, "down_limit": 9.0} for d in dates for s in symbols])
        def load_namechange(self, ts_code: str, **kwargs): return pd.DataFrame()
        def load_dividend_history(self, symbols, use_cache=True):
            return pd.DataFrame([{
                "ts_code": "600000.SH", "end_date": "20231231", "ann_date": "20240101",
                "div_proc": "实施", "record_date": "20240102", "ex_date": "20240103",
                "cash_div_tax": 0.1, "stk_div": 0.0, "pay_date": "20240105",
            }])

    class PitProvider:
        def get_indicator_history(self, symbol: str): return []

    pipeline = ResearchPipeline()
    observed = {}
    def capture(frame, output_root, *, data_snapshot_id=None):
        observed["frame"] = frame
        observed["data_snapshot_id"] = data_snapshot_id
        return {"run_id": "captured"}
    monkeypatch.setattr(pipeline, "run", capture)
    result = pipeline.run_tushare(
        EquityProvider(), tmp_path / "runs", start_date="20240102", end_date="20240103",
        pit_provider=PitProvider(), use_cache=False,
    )
    assert result["run_id"] == "captured"
    assert observed["data_snapshot_id"].startswith("tushare_pit:")
    assert {"momentum_5", "volatility_20", "feature_version"} <= set(observed["frame"].columns)
    assert "benchmark_close" in observed["frame"]
    assert observed["frame"]["is_index_member"].all()
    from aipha.research.corporate_actions import read_corporate_actions
    action, = read_corporate_actions(observed["frame"])
    assert action.pay_date.isoformat() == "2024-01-05"


def test_portfolio_replay_executes_next_open_and_obeys_limit_up() -> None:
    market = pd.DataFrame({
        "trade_date": pd.date_range("2024-01-30", periods=4),
        "symbol": ["600000.SH"] * 4,
        "open": [10.0, 10.0, 11.0, 12.0], "high": [10.2, 10.2, 11.2, 12.2],
        "low": [9.8, 9.8, 10.8, 11.8], "close": [10.0, 10.0, 11.0, 12.0],
        "volume": [100000] * 4, "amount": [1_000_000] * 4,
        "is_suspended": [False] * 4, "is_limit_up": [False, False, True, False],
        "is_limit_down": [False] * 4,
    })
    config = ResearchConfig(initial_capital=100_000, top_k=1, max_position_weight=0.5,
                            max_daily_turnover=1.0, commission_min=0, slippage_bps=0)
    target = TargetPosition(pd.Timestamp("2024-01-30").date(), "600000.SH", 0.5, "rank")
    result = run_portfolio_backtest(market, pd.DataFrame(), [target], config)
    assert result["trades"][0]["trade_date"] == "2024-01-31"
    assert result["trades"][0]["signal_date"] == "2024-01-30"
    assert result["trades"][0]["side"] == "buy"
    assert result["metrics"]["portfolio_costs"] > 0
    assert result["metrics"]["cost_attribution"]["total"] == pytest.approx(
        result["metrics"]["portfolio_total_costs"], rel=1e-12,
    )
    assert result["metrics"]["execution_attempts"] >= 1
    assert "slippage_cost" in result["trades"][0]


def test_targets_are_monthly_top_k_and_weight_capped() -> None:
    rows = []
    for day, score in [("2024-01-10", 1), ("2024-01-31", 3), ("2024-02-29", 2)]:
        for i in range(5):
            rows.append({"trade_date": day, "symbol": str(i), "score": score + i})
    targets = build_target_positions(pd.DataFrame(rows), top_k=4, max_position_weight=0.05)
    assert sorted({t.rebalance_date.isoformat() for t in targets}) == ["2024-01-31", "2024-02-29"]
    assert len(targets) == 8
    assert all(t.target_weight <= 0.05 for t in targets)
    assert sum(t.target_weight for t in targets[:4]) == pytest.approx(0.2)


def test_targets_apply_industry_cap_when_snapshot_has_industry() -> None:
    rows = [
        {"trade_date": "2024-01-31", "symbol": "A", "score": 5.0, "industry": "bank"},
        {"trade_date": "2024-01-31", "symbol": "B", "score": 4.0, "industry": "bank"},
        {"trade_date": "2024-01-31", "symbol": "C", "score": 3.0, "industry": "bank"},
        {"trade_date": "2024-01-31", "symbol": "D", "score": 2.0, "industry": "tech"},
    ]
    targets = build_target_positions(
        pd.DataFrame(rows), top_k=4, max_position_weight=0.25,
        max_industry_weight=0.50,
    )
    assert [target.symbol for target in targets] == ["A", "B", "D"]
    assert sum(target.target_weight for target in targets if target.symbol in {"A", "B"}) == pytest.approx(0.50)


def test_execution_plan_is_paper_only_and_provenance_checked(tmp_path: Path) -> None:
    targets = build_target_positions(pd.DataFrame([
        {"trade_date": "2024-01-31", "symbol": "600000.SH", "score": 1.0},
        {"trade_date": "2024-01-31", "symbol": "600001.SH", "score": 0.5},
    ]), top_k=2, max_position_weight=0.5, run_id="run-1", model_id="model-1")
    plan = build_execution_plan(targets, run_id="run-1", model_id="model-1")
    path = plan.write(tmp_path / "plan.json")
    restored = read_execution_plan(path)
    assert restored.mode == "paper"
    assert all(item.run_id == "run-1" and item.model_id == "model-1" and item.signal_id for item in restored.targets)
    with pytest.raises(ValueError, match="explicit allow_live"):
        build_execution_plan(targets, mode="live")


def test_live_plan_executor_attaches_provenance_to_every_order() -> None:
    targets = build_target_positions(pd.DataFrame([
        {"trade_date": "2024-01-31", "symbol": "600000.SH", "score": 1.0},
    ]), top_k=1, max_position_weight=0.5, run_id="run-1", model_id="model-1")
    plan = build_execution_plan(targets, mode="live", allow_live=True)
    class Gateway:
        def __init__(self): self.orders = []
        def submit_order(self, order):
            self.orders.append(order)
            return order.order_id
    gateway = Gateway()
    ids = execute_target_plan(
        gateway, plan, prices={"600000.SH": 10.0}, account_value=100_000,
        allow_live=True,
    )
    assert ids and gateway.orders[0].metadata["run_id"] == "run-1"
    assert gateway.orders[0].metadata["model_id"] == "model-1"
    assert gateway.orders[0].metadata["signal_id"]


def test_paper_plan_executor_rejects_live_plan_and_preserves_provenance() -> None:
    from aipha.research.execution_plan import execute_paper_target_plan
    from aipha.execution.gateway import GatewayError

    targets = build_target_positions(pd.DataFrame([{
        "trade_date": "2024-01-31", "symbol": "600000.SH", "score": 1.0,
    }]), top_k=1, max_position_weight=0.5, run_id="run-paper", model_id="model-paper")
    plan = build_execution_plan(targets, run_id="run-paper", model_id="model-paper")

    class Gateway:
        def __init__(self): self.orders = []
        def submit_order(self, order):
            self.orders.append(order)
            return order.order_id

    gateway = Gateway()
    ids = execute_paper_target_plan(
        gateway, plan, prices={"600000.SH": 10.0}, account_value=100_000,
        current_positions={},
    )
    assert ids and gateway.orders[0].metadata["execution_plan_id"] == plan.plan_id
    live_plan = build_execution_plan(targets, mode="live", allow_live=True)
    with pytest.raises(GatewayError, match="mode=paper"):
        execute_paper_target_plan(
            gateway, live_plan, prices={"600000.SH": 10.0}, account_value=100_000,
            current_positions={},
        )


def test_legacy_model_without_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest missing"):
        load_verified_selector(tmp_path, expected_feature_version="cn_equity_v1")


def test_legacy_model_migration_requires_explicit_attestation(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "meta.pkl").write_bytes(b"placeholder")
    with pytest.raises(ValueError, match="acknowledge_legacy"):
        migrate_legacy_model(
            source, tmp_path / "migrated", feature_version="cn_equity_v1",
            feature_columns=["momentum_signal"], model_type="lightgbm",
            data_snapshot_id="attested-snapshot",
        )


def test_end_to_end_oos_run_writes_reproducible_artifacts(tmp_path: Path) -> None:
    pytest.importorskip("lightgbm")
    cfg = ResearchConfig(
        train_years=1,
        test_months=1,
        forward_days=5,
        label_skip_days=1,
        purge_days=10,
        top_k=4,
        max_position_weight=0.25,
        max_industry_weight=0.5,
        n_estimators=5,
    )
    pipeline = ResearchPipeline(cfg)
    fixture = _panel(n_symbols=20)
    fixture["industry"] = fixture["symbol"].map(
        lambda symbol: "bank" if int(symbol[:6]) % 2 == 0 else "tech",
    )
    # Market rows remain available after a stock exits the signal universe.
    fixture.loc[fixture.index[-1], "is_index_member"] = False
    fixture.attrs["snapshot_metadata"] = attest_snapshot_metadata(fixture, {
        "provider": "tushare_pit", "data_snapshot_id": "fixture-v1",
        "index_code": "000905.SH",
        "feature_version": "cn_equity_v1",
        "start_date": "2022-01-03", "end_date": "2023-05-19",
    })
    result = pipeline.run(fixture, tmp_path / "runs", data_snapshot_id="fixture-v1")
    artifact_dir = Path(result["artifact_dir"])
    assert result["data_snapshot_id"] == "fixture-v1"
    with pytest.raises(ValueError, match="metadata"):
        pipeline.run(_panel(n_symbols=20), tmp_path / "missing-metadata", data_snapshot_id="fixture-v1")
    unversioned = fixture.drop(columns="feature_version").copy()
    unversioned.attrs["snapshot_metadata"] = attest_snapshot_metadata(
        unversioned,
        {key: value for key, value in fixture.attrs["snapshot_metadata"].items()
         if key not in {"snapshot_schema_version", "generator_version", "snapshot_content_sha256",
                        "row_count", "column_count", "source_signature_algorithm", "source_signature"}},
    )
    with pytest.raises(ValueError, match="feature_version column"):
        pipeline.run(unversioned, tmp_path / "unversioned", data_snapshot_id="fixture-v1")
    spoofed = fixture.copy()
    spoofed.attrs["snapshot_metadata"] = dict(fixture.attrs["snapshot_metadata"])
    spoofed.attrs["snapshot_metadata"]["generator_version"] = "external/fake"
    from aipha.research.snapshots import sign_snapshot_metadata
    spoofed.attrs["snapshot_metadata"] = sign_snapshot_metadata(spoofed.attrs["snapshot_metadata"])
    with pytest.raises(ValueError, match="canonical snapshot builder"):
        pipeline.run(spoofed, tmp_path / "spoofed", data_snapshot_id="fixture-v1")
    assert (artifact_dir / "model" / "model_manifest.json").is_file()
    assert (artifact_dir / "input_snapshot.parquet").is_file()
    frozen = pd.read_parquet(artifact_dir / "input_snapshot.parquet")
    assert len(frozen) == len(fixture)
    assert not frozen["is_index_member"].all()
    assert (artifact_dir / "input_snapshot.metadata.json").is_file()
    assert (artifact_dir / "oos_predictions.parquet").is_file()
    predictions = pd.read_parquet(artifact_dir / "oos_predictions.parquet")
    assert predictions["industry"].notna().all()
    assert (artifact_dir / "target_positions.json").is_file()
    plan_paths = result["execution_plans"]
    assert plan_paths
    plan = read_execution_plan(artifact_dir / plan_paths[0])
    assert plan.mode == "paper"
    assert plan.run_id == result["run_id"]
    industries = fixture.drop_duplicates("symbol").set_index("symbol")["industry"].to_dict()
    for industry in {industries[target.symbol] for target in plan.targets}:
        assert sum(t.target_weight for t in plan.targets if industries[t.symbol] == industry) <= 0.5
    # The same frozen plan is consumable by the PaperGateway boundary.  This
    # closes the offline chain after target generation and verifies that order
    # provenance survives the broker-neutral handoff.
    from aipha.execution.paper_gateway import PaperGateway
    paper = PaperGateway.from_cn_equity(initial_capital=cfg.initial_capital)
    prices = {target.symbol: 10.0 for target in plan.targets}
    paper_orders = paper.submit_target_plan(
        plan,
        prices=prices,
        account_value=cfg.initial_capital,
        max_position_weight=cfg.max_position_weight,
    )
    assert paper_orders
    first_order = paper.get_order(paper_orders[0])
    assert first_order is not None
    assert first_order.metadata["run_id"] == result["run_id"]
    assert first_order.metadata["model_id"] == result["run_id"]
    assert first_order.metadata["signal_id"]
    manifest = json.loads((artifact_dir / "run_manifest.json").read_text(encoding="utf-8"))
    report = json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))
    assert manifest["data_snapshot_id"] == "fixture-v1"
    assert manifest["feature_version"] == "cn_equity_v1"
    assert manifest["evaluation_window"] == report["evaluation_window"]
    assert manifest["train_end"] == report["training_period"]["end"]
    assert manifest["train_end"] < str(fixture.trade_date.max().date())
    for fold in report["walk_forward"]["folds"]:
        assert pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["purge_start"])
        assert pd.Timestamp(fold["purge_end"]) < pd.Timestamp(fold["test_start"])
        assert fold["purge_sessions"] >= cfg.forward_days + cfg.label_skip_days
        assert fold["train_dates"][-1] == str(pd.Timestamp(fold["train_end"]).date())
    assert np.isfinite(report["metrics"]["rank_ic"])
    assert 0.0 <= report["metrics"]["pbo_proxy"] <= 1.0
    assert np.isfinite(report["metrics"]["portfolio_total_return"])
    assert (artifact_dir / "portfolio_returns.parquet").is_file()
    returns = pd.read_parquet(artifact_dir / "portfolio_returns.parquet")
    assert returns.trade_date.min() == manifest["oos_start"]
    assert report["evaluation_window"]["return_observations"] == len(returns) - 1
    assert (artifact_dir / "trades.json").is_file()
    assert (artifact_dir / "corporate_actions.json").is_file()
    assert report["accounting"]["price_basis"] == "raw"
    assert report["walk_forward"]["n_folds"] >= 1
    assert report["diagnostics"]["benchmark"]["available"] is True
    assert report["diagnostics"]["benchmark"]["relative"]["available"] is True
    assert report["diagnostics"]["benchmark"]["relative"]["aligned_days"] >= 2
    assert np.isfinite(report["diagnostics"]["benchmark"]["relative"]["excess_return"])
    assert report["diagnostics"]["style_exposures"]["available"] is True
    long_short = report["diagnostics"]["long_short"]
    assert long_short["available"] is True
    assert long_short["rebalance_frequency"] == "monthly"
    assert long_short["gross_exposure"] == 2.0
    assert np.isfinite(long_short["mean_net_return"])
    assert long_short["estimated_cost"] >= 0
    assert report["diagnostics"]["pbo_method"] == "cscv_net_top_k_portfolios"
    assert report["diagnostics"]["selection_pbo_method"] == "cscv_net_top_k_portfolios"
    assert report["pbo_analysis"]["return_basis"] == "net_portfolio_replay"
    assert report["pbo_analysis"]["overlapping_return_intervals"] is False
    from aipha.research.snapshots import verify_attested_parquet
    from aipha.reporting.pbo import PBOAnalyzer, PBOConfig
    cscv_path = artifact_dir / "cscv_returns.parquet"
    cscv_metadata = verify_attested_parquet(cscv_path, require_artifact_hash=True)
    cscv_returns = pd.read_parquet(cscv_path).set_index("trade_date")
    cscv_analysis = json.loads((artifact_dir / "cscv_analysis.json").read_text(encoding="utf-8"))
    assert cscv_analysis["candidate_returns_content_sha256"] == cscv_metadata["snapshot_content_sha256"]
    assert cscv_metadata["start_date"] == cscv_returns.index.min().date().isoformat()
    assert cscv_metadata["end_date"] == cscv_returns.index.max().date().isoformat()
    assert cscv_analysis["result"]["num_observations"] == len(returns) - 1
    assert sum(cscv_analysis["result"]["subsample_sizes"]) == len(cscv_returns)
    expected_returns = returns.set_index(pd.to_datetime(returns.trade_date)).daily_return.iloc[1:]
    np.testing.assert_allclose(cscv_returns[f"top_k_{cfg.top_k}"], expected_returns)
    reproduced_pbo = PBOAnalyzer(PBOConfig(**cscv_analysis["config"])).compute(cscv_returns.to_numpy())
    assert reproduced_pbo.pbo == report["metrics"]["pbo"]
    assert report["diagnostics"]["selection_pbo_candidates"]
    model_manifest = json.loads((artifact_dir / "model" / "model_manifest.json").read_text(encoding="utf-8"))
    assert set(report["feature_missing_rates"]["columns"]) == set(model_manifest["feature_columns"])
    assert "momentum_signal" in model_manifest["feature_columns"]
    assert "is_limit_up" not in model_manifest["feature_columns"]
    assert "fwd_return" not in model_manifest["feature_columns"]
    selector = load_verified_selector(
        artifact_dir / "model", expected_feature_version="cn_equity_v1",
        expected_feature_columns=model_manifest["feature_columns"], expected_model_type="lightgbm",
    )
    assert np.isfinite(selector.predict(fixture.head(20)).to_numpy()).all()


def test_saved_selector_rejects_feature_version_or_column_order_mismatch(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "model_type": "lightgbm",
        "feature_version": "cn_equity_v1",
        "feature_columns": ["a", "b"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="version mismatch"):
        load_verified_selector(model_dir, expected_feature_version="cn_equity_v2")
    with pytest.raises(ValueError, match="columns/order"):
        load_verified_selector(
            model_dir,
            expected_feature_version="cn_equity_v1",
            expected_feature_columns=["b", "a"],
        )


def test_linear_elasticnet_baseline_is_a_real_walk_forward_model() -> None:
    pytest.importorskip("sklearn")
    cfg = ResearchConfig(train_years=1, test_months=1, forward_days=5,
                         label_skip_days=1, purge_days=10, model_type="elasticnet")
    oos, summary, selector = ResearchPipeline(cfg).train_oos(_panel(n_days=360, n_symbols=20))
    assert not oos.empty
    assert selector.config.model_type == "elasticnet"
    assert summary["model_type"] == "elasticnet"


def test_canonical_loader_rejects_experimental_manifest_before_loading(tmp_path: Path) -> None:
    (tmp_path / "model_manifest.json").write_text(json.dumps({
        "schema_version": 1, "model_type": "double_ensemble",
        "feature_version": "cn_equity_v1", "feature_columns": ["a"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="lightgbm or elasticnet only"):
        load_verified_selector(tmp_path, expected_feature_version="cn_equity_v1")


def test_canonical_loader_rejects_model_type_mismatch(tmp_path: Path) -> None:
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    MLStockSelector(MLSelectorConfig(model_type="elasticnet", feature_cols=["a"])).save(tmp_path)
    (tmp_path / "model_manifest.json").write_text(json.dumps({
        "schema_version": 1, "model_type": "lightgbm",
        "feature_version": "cn_equity_v1", "feature_columns": ["a"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="saved model type does not match"):
        load_verified_selector(tmp_path, expected_feature_version="cn_equity_v1")
