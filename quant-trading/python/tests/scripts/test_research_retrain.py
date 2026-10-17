from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "research_retrain.py"
SPEC = importlib.util.spec_from_file_location("research_retrain_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_canonical_retrain_parser_requires_snapshot() -> None:
    args = MODULE.parse_args(["--data-path", "snapshot.parquet"])
    assert args.data_path == "snapshot.parquet"
    assert args.output_dir == "output/research/runs"


def test_canonical_retrain_parser_accepts_snapshot_identity() -> None:
    args = MODULE.parse_args([
        "--data-path", "snapshot.parquet",
        "--output-dir", "runs",
        "--data-snapshot-id", "tushare_pit:fixture",
    ])
    assert args.data_snapshot_id == "tushare_pit:fixture"
