from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from aipha.research.config import (
    load_research_config,
    migrate_legacy_ml_config,
)
from aipha.research.execution_plan import (
    build_execution_plan,
)
from aipha.research.snapshots import (
    verify_attested_parquet,
    write_attested_parquet,
)
from aipha.research import build_target_positions
from aipha.research import ResearchConfig, ResearchPipeline
from aipha.research.execution_plan import read_execution_plan


def _plan(weight: float = 0.5):
    targets = build_target_positions(
        pd.DataFrame([{
            "trade_date": "2024-01-31",
            "symbol": "600000.SH",
            "score": 1.0,
        }]),
        top_k=1,
        max_position_weight=weight,
        run_id="run-completion",
        model_id="model-completion",
    )
    return build_execution_plan(targets, run_id="run-completion", model_id="model-completion")


def test_attested_tushare_snapshot_rejects_byte_tampering(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.parquet"
    frame = pd.DataFrame({
        "trade_date": ["2024-01-02"],
        "symbol": ["600000.SH"],
        "close": [10.0],
    })
    write_attested_parquet(frame, path, {
        "provider": "tushare_pit",
        "data_snapshot_id": "fixture",
        "feature_version": "cn_equity_v1",
        "start_date": "2024-01-02",
        "end_date": "2024-01-02",
    })
    assert verify_attested_parquet(path, require_artifact_hash=True)["generator_version"]
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="artifact_sha256"):
        verify_attested_parquet(path, require_artifact_hash=True)


def test_legacy_ml_config_migration_is_explicitly_pit_safe() -> None:
    # Unsupported legacy ensembles do not silently become a canonical model.
    with pytest.raises(ValueError, match="no canonical migration"):
        migrate_legacy_ml_config({"ml_model": {"type": "double_ensemble"}})

    migrated, warnings = migrate_legacy_ml_config({
        "data": {"source": "akshare"},
        "universe": {"preset": "zz500"},
        "ml_model": {"type": "lgbm", "top_n": 20},
        "strategy": {"params": {"top_n": 20}},
    })
    assert migrated["data"]["provider"] == "tushare_pit"
    assert migrated["execution"]["mode"] == "paper"
    assert migrated["portfolio"]["top_k"] == 20
    assert any("akshare" in item for item in warnings)


def test_paper_gateway_boundary_applies_plan_risk_gate() -> None:
    from aipha.execution.paper_gateway import PaperGateway

    gateway = PaperGateway.from_cn_equity(initial_capital=100_000)
    plan = _plan(0.5)
    order_ids = gateway.submit_target_plan(
        plan,
        prices={"600000.SH": 10.0},
        account_value=100_000,
        max_position_weight=0.5,
    )
    assert order_ids
    order = gateway.get_order(order_ids[0])
    assert order is not None
    assert order.metadata["run_id"] == "run-completion"
    assert order.metadata["model_id"] == "model-completion"
    assert order.metadata["signal_id"]

    with pytest.raises(ValueError, match="max_position_weight"):
        gateway.submit_target_plan(
            _plan(0.5),
            prices={"600000.SH": 10.0},
            account_value=100_000,
            max_position_weight=0.1,
        )


def test_research_config_migration_output_is_loadable(tmp_path: Path) -> None:
    from aipha.research.config import migrate_legacy_ml_config_file

    source = tmp_path / "legacy.yaml"
    destination = tmp_path / "canonical.yaml"
    source.write_text(
        "data:\n  source: akshare\nml_model:\n  type: lgbm\n",
        encoding="utf-8",
    )
    migrate_legacy_ml_config_file(source, destination)
    config = load_research_config(destination)
    assert config.data_provider == "tushare_pit"
    assert config.model_type == "lightgbm"


def test_legacy_ml_engine_only_forwards_canonical_actions() -> None:
    from engines.aipha_ml_engine import run

    result = run({"action": "list_models"})
    assert result["data"]["models"] == ["lgbm", "linear"]
    rejected = run({"action": "legacy_array_train", "trainFeatures": []})
    assert rejected["ok"] is False
    assert "versioned Parquet" in rejected["error"]


def test_tushare_fixture_runs_to_paper_plan(tmp_path: Path) -> None:
    """Exercise the complete offline TuShare-shaped research contract."""
    pytest.importorskip("lightgbm")
    dates = pd.bdate_range("2022-01-03", periods=300)
    symbols = [f"60000{i}.SH" for i in range(6)]
    market_rows: list[dict[str, object]] = []
    for idx, symbol in enumerate(symbols):
        for day_idx, day in enumerate(dates):
            close = 10.0 + idx + day_idx * 0.01
            market_rows.append({
                "ts_code": symbol,
                "trade_date": day.strftime("%Y%m%d"),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "vol": 100_000.0,
                "amount": close * 100_000.0,
            })
    market = pd.DataFrame(market_rows)

    class EquityFixture:
        def load_daily_bars_range(self, *args, **kwargs):
            return market.copy()

        def load_daily_basic_range(self, *args, **kwargs):
            return pd.DataFrame({
                "ts_code": market["ts_code"],
                "trade_date": market["trade_date"],
                "circ_mv": 1_000_000.0,
            })

        def load_stock_basic(self, **kwargs):
            return pd.DataFrame({"ts_code": symbols, "list_date": ["20180101"] * len(symbols)})

        def load_adj_factor_bulk(self, *args, **kwargs):
            return market[["ts_code", "trade_date"]].assign(adj_factor=1.0)

        def load_index_members_snapshots(self, *args, **kwargs):
            return {"20200101": symbols}

        def load_index_bars_range(self, *args, **kwargs):
            return pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "close": [100.0 + i * 0.1 for i in range(len(dates))],
            })

        def load_limit_prices_range(self, *args, **kwargs):
            return market[["ts_code", "trade_date"]].assign(up_limit=1000.0, down_limit=0.01)

        def load_namechange(self, ts_code: str, **kwargs):
            return pd.DataFrame()

    class PitFixture:
        def get_indicator_history(self, symbol: str):
            return []

    cfg = ResearchConfig(
        train_years=1, test_months=1, forward_days=5,
        label_skip_days=1, purge_days=10, top_k=3,
        max_position_weight=0.25, n_estimators=3,
    )
    result = ResearchPipeline(cfg).run_tushare(
        EquityFixture(), tmp_path / "runs",
        start_date="20220103", end_date=dates[-1].strftime("%Y%m%d"),
        pit_provider=PitFixture(), use_cache=False,
    )
    artifact = Path(result["artifact_dir"])
    plan_path = artifact / result["execution_plans"][0]
    plan = read_execution_plan(plan_path)
    assert plan.mode == "paper"
    assert (artifact / "input_snapshot.metadata.json").is_file()
    assert (artifact / "oos_predictions.metadata.json").is_file()
