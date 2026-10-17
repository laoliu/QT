from __future__ import annotations

from pathlib import Path

import yaml

from scripts.audit_ml_migration import audit_config, audit_tree, main


def test_audit_marks_manifest_backed_snapshot_as_canonical(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "model_manifest.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "canonical.yml"
    config.write_text(yaml.safe_dump({
        "feature_snapshot_path": "snapshot.parquet",
        "model_dir": "model",
    }), encoding="utf-8")
    result = audit_config(config, project_root=tmp_path)
    assert result["status"] == "canonical"
    assert result["model_manifests"][0]["manifest"] is True


def test_audit_marks_explicit_legacy_config(tmp_path: Path) -> None:
    config = tmp_path / "legacy.yml"
    config.write_text(yaml.safe_dump({
        "legacy_ml_compat": True,
        "strategy": {"model_dir": "models/old"},
    }), encoding="utf-8")
    result = audit_config(config, project_root=tmp_path)
    assert result["status"] == "legacy_compat"
    assert result["legacy_references"]


def test_strict_audit_fails_when_legacy_configs_remain(tmp_path: Path, capsys) -> None:
    (tmp_path / "legacy.yml").write_text(
        yaml.safe_dump({"legacy_ml_compat": True}), encoding="utf-8",
    )
    assert main(["--config-dir", str(tmp_path), "--strict"]) == 2
    assert '"legacy_compat": 1' in capsys.readouterr().out


def test_audit_tree_reports_empty_directory(tmp_path: Path) -> None:
    result = audit_tree(tmp_path, project_root=tmp_path)
    assert result["total"] == 0
    assert result["counts"] == {}


def test_repository_paper_ml_configs_are_marked_or_canonical() -> None:
    project_root = Path(__file__).resolve().parents[2]
    report = audit_tree(project_root / "configs" / "paper", project_root=project_root)
    assert report["counts"].get("unmigrated", 0) == 0
    assert report["counts"].get("legacy_compat", 0) == 20
    assert report["counts"].get("not_applicable", 0) == 16
