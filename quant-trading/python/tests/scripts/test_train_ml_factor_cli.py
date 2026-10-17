from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "train_ml_factor.py"
SPEC = importlib.util.spec_from_file_location("train_ml_factor_cli_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_legacy_ml_script_defaults_to_canonical_lightgbm() -> None:
    args = MODULE.parse_args([])
    assert args.model == "lightgbm"
    assert args.legacy_model is None


def test_legacy_model_requires_explicit_legacy_option(caplog) -> None:
    args = MODULE.parse_args(["--legacy-model", "gru"])
    assert args.model == "gru"
    assert args.legacy_model == "gru"
    assert "legacy experimental model gru" in caplog.text


def test_legacy_training_cli_requires_explicit_compatibility() -> None:
    args = MODULE.parse_args([])
    try:
        MODULE.validate_cli_mode(args)
    except SystemExit as exc:
        assert "disabled by default" in str(exc)
    else:
        raise AssertionError("legacy cache-backed training must be opt-in")


def test_legacy_training_cli_accepts_explicit_compatibility() -> None:
    args = MODULE.parse_args(["--legacy-compat"])
    MODULE.validate_cli_mode(args)
