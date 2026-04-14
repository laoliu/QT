from __future__ import annotations

import pandas as pd

from scripts import _combo_enhanced_backtest as combo


def test_has_reusable_output_accepts_valid_nav_file(tmp_path):
    out_dir = tmp_path / "ml_csi2000_fund"
    out_dir.mkdir()
    pd.DataFrame(
        {"date": ["2020-01-02", "2020-01-03"], "nav": [1_000_000, 1_010_000]}
    ).to_csv(out_dir / "daily_nav.csv", index=False)

    assert combo._has_reusable_output(out_dir) is True


def test_prepare_output_dir_cleans_stale_output_by_default(tmp_path):
    out_dir = tmp_path / "cb_stock_quality"
    out_dir.mkdir()
    (out_dir / "stale.txt").write_text("old", encoding="utf-8")

    reused = combo._prepare_output_dir(out_dir, reuse_existing=False)

    assert reused is False
    assert out_dir.exists() is False


def test_prepare_output_dir_reuses_only_valid_output(tmp_path):
    out_dir = tmp_path / "etf_cross_v3"
    out_dir.mkdir()
    pd.DataFrame(
        {"date": ["2020-01-02", "2020-01-03"], "nav": [1_000_000, 1_005_000]}
    ).to_csv(out_dir / "daily_snapshots.csv", index=False)

    reused = combo._prepare_output_dir(out_dir, reuse_existing=True)

    assert reused is True
    assert out_dir.exists() is True


def test_write_run_manifest_contains_reuse_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(combo, "OUTPUT_BASE", tmp_path)

    combo._write_run_manifest(reuse_existing=False)

    manifest = (tmp_path / "run_manifest.json").read_text(encoding="utf-8")
    assert '"reuse_existing": false' in manifest
    assert '"ml_index": "csi2000_lgb_fund"' in manifest
