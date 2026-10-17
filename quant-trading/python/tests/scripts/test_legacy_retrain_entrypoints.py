from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(f"{path.stem}_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "script_name",
    ["retrain_csi1000_rolling.py", "retrain_csi2000_rolling.py"],
)
def test_legacy_retrain_requires_explicit_compatibility(script_name: str) -> None:
    module = load_script(script_name)
    args = module.parse_args([])
    with pytest.raises(SystemExit, match="disabled by default"):
        module.validate_retrain_mode(args)


@pytest.mark.parametrize(
    "script_name",
    ["retrain_csi1000_rolling.py", "retrain_csi2000_rolling.py"],
)
def test_legacy_retrain_compatibility_is_explicit(script_name: str) -> None:
    module = load_script(script_name)
    args = module.parse_args([])
    args.legacy_compat = True
    module.validate_retrain_mode(args)


@pytest.mark.parametrize(
    "script_name",
    ["retrain_csi1000_rolling.py", "retrain_csi2000_rolling.py"],
)
def test_legacy_retrain_check_only_remains_read_only(script_name: str) -> None:
    module = load_script(script_name)
    args = module.parse_args(["--check-only"])
    module.validate_retrain_mode(args)
