"""OOS 预测文件 universe 守门测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

_MOD_PATH = _SCRIPTS_DIR / "enhanced_oos_replay.py"
_spec = importlib.util.spec_from_file_location("enhanced_oos_replay", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

load_predictions = _mod.load_predictions


def _write_predictions(path: Path, symbols: list[str]) -> Path:
    df = pd.DataFrame(
        {
            "date": ["2024-01-02"] * len(symbols),
            "symbol": symbols,
            "score": list(range(len(symbols), 0, -1)),
        }
    )
    df.to_parquet(path)
    return path


def test_csi2000_predictions_raise_on_bj_symbols(tmp_path: Path) -> None:
    pred_path = tmp_path / "ml_csi2000_lgb_fund_oos_predictions.parquet"
    _write_predictions(pred_path, ["000001.SZ", "920190.BJ"])

    with pytest.raises(ValueError, match="csi2000"):
        load_predictions(
            pred_path,
            universe_name="csi2000_lgb_fund",
            strict_universe=True,
        )


def test_csi2000_predictions_filter_bj_symbols_when_not_strict(tmp_path: Path) -> None:
    pred_path = tmp_path / "ml_csi2000_lgb_fund_oos_predictions.parquet"
    _write_predictions(pred_path, ["000001.SZ", "600000.SH", "920190.BJ"])

    result = load_predictions(
        pred_path,
        universe_name="csi2000_lgb_fund",
        strict_universe=False,
    )

    assert set(result["symbol"]) == {"000001.SZ", "600000.SH"}
    assert not result["symbol"].str.endswith(".BJ").any()


def test_all_universe_allows_bj_symbols(tmp_path: Path) -> None:
    pred_path = tmp_path / "ml_all_market_oos_predictions.parquet"
    _write_predictions(pred_path, ["000001.SZ", "920190.BJ"])

    result = load_predictions(
        pred_path,
        universe_name="all",
        strict_universe=True,
    )

    assert set(result["symbol"]) == {"000001.SZ", "920190.BJ"}
