"""Reproducibility & Archiver edge-case tests.

Covers untested branches in:
- reproducibility.py: create_fingerprint (file_not_found), content/shape
  fingerprint fallbacks, verify_fingerprint branches, load_fingerprint
  python_version fallback, summary with config_path
- archiver.py: _json_serial branches, empty snapshots/trades, single-day,
  get_nav_series missing, load_experiment missing dir
- fingerprint_utils.py: validate_fingerprint custom ignore_fields
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aipha.utils.reproducibility import (
    ExperimentFingerprint,
    _compute_content_fingerprint,
    _compute_shape_fingerprint,
    compute_data_fingerprint,
    create_fingerprint,
    hash_config_file,
    load_fingerprint,
    save_fingerprint,
    verify_fingerprint,
)


# ============================================================================
# create_fingerprint – config_path not found
# ============================================================================


class TestCreateFingerprintEdge:
    """create_fingerprint edge cases"""

    def test_config_path_not_found_returns_file_not_found_hash(self, tmp_path: Path):
        """config_path 指向不存在的文件时 config_hash == 'file_not_found'"""
        fp = create_fingerprint(
            config_path=tmp_path / "does_not_exist.yml",
            set_seed=False,
        )
        assert fp.config_hash == "file_not_found"
        assert fp.config_path is not None

    def test_no_config_no_data(self):
        """都不提供时 config_hash='none', data_fingerprint='none'"""
        fp = create_fingerprint(set_seed=False)
        assert fp.config_hash == "none"
        assert fp.data_fingerprint == "none"

    def test_config_path_sets_config_path_str(self, tmp_path: Path):
        """config_path 应转为 str 存储"""
        cfg = tmp_path / "c.yml"
        cfg.write_text("a: 1\n")
        fp = create_fingerprint(config_path=cfg, set_seed=False)
        assert fp.config_path == str(cfg)
        assert fp.config_hash != "none"


# ============================================================================
# Content fingerprint – repr fallback
# ============================================================================


class TestContentFingerprintFallback:
    """_compute_content_fingerprint repr fallback (非 JSON 可序列化)"""

    def test_set_triggers_repr_fallback(self):
        """set 不能 JSON 序列化，走 repr 分支"""
        data = {1, 2, 3}
        fp = _compute_content_fingerprint(data)
        assert isinstance(fp, str) and len(fp) == 16

    def test_custom_object_falls_back(self):
        """自定义对象走 repr"""

        class X:
            pass

        fp = _compute_content_fingerprint(X())
        assert len(fp) == 16


# ============================================================================
# Shape fingerprint branches
# ============================================================================


class TestShapeFingerprintBranches:
    """_compute_shape_fingerprint 分支"""

    def test_dict_keys_branch(self):
        """dict 应走 keys 分支"""
        d = {f"k{i}": i for i in range(150)}
        fp = _compute_shape_fingerprint(d)
        assert len(fp) == 16

    def test_non_numeric_array(self):
        """字符串 ndarray 不应添加 mean/std/min/max"""
        arr = np.array(["a", "b", "c"])
        fp = _compute_shape_fingerprint(arr)
        assert len(fp) == 16

    def test_plain_list(self):
        """list => length 分支"""
        fp = _compute_shape_fingerprint([10, 20, 30])
        assert len(fp) == 16

    def test_numeric_array_stats(self):
        """数值 array 应返回稳定指纹"""
        arr = np.array([1.0, 2.0, 3.0])
        fp1 = _compute_shape_fingerprint(arr)
        fp2 = _compute_shape_fingerprint(arr)
        assert fp1 == fp2


# ============================================================================
# verify_fingerprint edge paths
# ============================================================================


class TestVerifyFingerprintEdge:
    """verify_fingerprint 边界路径"""

    def test_config_path_file_missing(self, tmp_path: Path):
        """config_path 已删除 → config_hash = False"""
        cfg = tmp_path / "c.yml"
        cfg.write_text("x: 1\n")
        fp = create_fingerprint(config_path=cfg, set_seed=False)

        cfg.unlink()  # 删除
        results = verify_fingerprint(fp, config_path=cfg)
        assert results["config_hash"] is False

    def test_data_fingerprint_match(self):
        """data 一致 → data_fingerprint = True"""
        data = [1, 2, 3]
        fp = create_fingerprint(data=data, set_seed=False)
        results = verify_fingerprint(fp, data=data)
        assert results["data_fingerprint"] is True

    def test_data_fingerprint_mismatch(self):
        """data 不一致 → data_fingerprint = False"""
        fp = create_fingerprint(data=[1, 2, 3], set_seed=False)
        results = verify_fingerprint(fp, data=[4, 5, 6])
        assert results["data_fingerprint"] is False


# ============================================================================
# load_fingerprint – python_version fallback
# ============================================================================


class TestLoadFingerprintEdge:
    """load_fingerprint 边界"""

    def test_missing_python_version_fallback(self, tmp_path: Path):
        """旧格式 JSON 缺少 python_version → 'unknown'"""
        fp = ExperimentFingerprint(
            git_commit="abc",
            git_short="abc",
            git_dirty=False,
            git_branch="main",
            config_path=None,
            config_hash="h",
            data_fingerprint="d",
            random_seed=0,
            run_time=datetime(2025, 1, 1),
            python_version="3.12.5",
        )
        path = tmp_path / "fp.json"
        d = fp.to_dict()
        del d["python_version"]
        path.write_text(json.dumps(d), encoding="utf-8")

        loaded = load_fingerprint(path)
        assert loaded.python_version == "unknown"

    def test_round_trip_with_config_path_none(self, tmp_path: Path):
        """config_path=None 时 round-trip 正常"""
        fp = ExperimentFingerprint(
            git_commit="x", git_short="x", git_dirty=True,
            git_branch="b", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=7,
            run_time=datetime(2025, 6, 1),
        )
        path = tmp_path / "fp2.json"
        save_fingerprint(fp, path)
        loaded = load_fingerprint(path)
        assert loaded.config_path is None
        assert loaded.git_dirty is True


# ============================================================================
# ExperimentFingerprint.summary – config_path branch
# ============================================================================


class TestFingerprintSummary:
    """summary() 分支"""

    def test_summary_with_config_path(self):
        """config_path 非 None 时应出现在 summary 中"""
        fp = ExperimentFingerprint(
            git_commit="abc", git_short="abc", git_dirty=False,
            git_branch="main", config_path="/my/config.yml",
            config_hash="h", data_fingerprint="d",
            random_seed=42, run_time=datetime(2025, 1, 1),
        )
        s = fp.summary()
        assert "配置文件" in s
        assert "/my/config.yml" in s

    def test_summary_without_config_path(self):
        """config_path=None 时 summary 不含配置文件行"""
        fp = ExperimentFingerprint(
            git_commit="abc", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None,
            config_hash="h", data_fingerprint="d",
            random_seed=42, run_time=datetime(2025, 1, 1),
        )
        s = fp.summary()
        assert "配置文件" not in s


# ============================================================================
# Archiver: _json_serial branches
# ============================================================================


from aipha.reporting.archiver import ExperimentArchiver, _json_serial


class TestJsonSerial:
    """_json_serial 各分支"""

    def test_date_object(self):
        assert _json_serial(date(2025, 1, 1)) == "2025-01-01"

    def test_datetime_object(self):
        dt = datetime(2025, 1, 1, 12, 30)
        assert _json_serial(dt) == "2025-01-01T12:30:00"

    def test_to_dict_object(self):
        class HasToDict:
            def to_dict(self):
                return {"key": "val"}

        assert _json_serial(HasToDict()) == {"key": "val"}

    def test_dunder_dict_object(self):
        class Plain:
            def __init__(self):
                self.x = 1

        assert _json_serial(Plain()) == {"x": 1}

    def test_non_serializable_raises(self):
        with pytest.raises(TypeError, match="not serializable"):
            _json_serial(object())


# ============================================================================
# Archiver: empty snapshots / trades / single-day
# ============================================================================


from aipha.execution.fees import FeeBreakdown


@dataclass
class _Snap:
    trade_date: date
    nav: float
    cash: float
    positions: dict = field(default_factory=dict)
    position_values: dict = field(default_factory=dict)
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class _Trade:
    trade_id: str
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    notional: float
    fees: FeeBreakdown
    trade_time: datetime = field(default_factory=datetime.now)
    trade_date: date = field(default_factory=date.today)


@dataclass
class _Result:
    strategy_name: str = "t"
    start_date: date = date(2024, 1, 2)
    end_date: date = date(2024, 1, 2)
    initial_capital: float = 1_000_000
    final_nav: float = 1_000_000
    final_cash: float = 1_000_000
    final_positions: dict = field(default_factory=dict)
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    total_trades: int = 0
    total_fees: FeeBreakdown = field(
        default_factory=lambda: FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0)
    )
    exclusion_stats: dict = field(default_factory=dict)
    config: dict = field(default_factory=lambda: {"fill_model_name": "nbo"})


class TestArchiverEmptyEdge:
    """Archiver 空数据边界"""

    def test_empty_snapshots_no_nav_file(self, tmp_path: Path):
        """daily_snapshots=[] → nav_series.csv 不生成"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        r = _Result(daily_snapshots=[], trades=[])
        run_id = arch.archive(r, {"name": "t"})
        run_dir = tmp_path / "t" / run_id
        assert not (run_dir / "nav_series.csv").exists()

    def test_empty_trades_no_trades_file(self, tmp_path: Path):
        """trades=[] → trades.csv 不生成"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        snap = _Snap(trade_date=date(2024, 1, 2), nav=1_000_000, cash=1_000_000)
        r = _Result(daily_snapshots=[snap], trades=[])
        run_id = arch.archive(r, {"name": "t"})
        run_dir = tmp_path / "t" / run_id
        assert not (run_dir / "trades.csv").exists()

    def test_empty_snapshots_metrics_empty(self, tmp_path: Path):
        """daily_snapshots=[] → metrics == {}"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        r = _Result(daily_snapshots=[])
        run_id = arch.archive(r, {"name": "t"})
        meta = json.loads(
            (tmp_path / "t" / run_id / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["metrics"] == {}

    def test_single_day_snapshot_sharpe_zero(self, tmp_path: Path):
        """单日快照 → sharpe=0"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        snap = _Snap(
            trade_date=date(2024, 1, 2),
            nav=1_010_000, cash=100_000,
            daily_return=0.01, cumulative_return=0.01,
        )
        r = _Result(
            daily_snapshots=[snap], final_nav=1_010_000,
            end_date=date(2024, 1, 2),
        )
        run_id = arch.archive(r, {"name": "t"})
        meta = json.loads(
            (tmp_path / "t" / run_id / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["metrics"]["sharpe_ratio"] == 0


class TestArchiverLoadEdge:
    """Archiver load / get_nav_series 边界"""

    def test_load_experiment_dir_not_found(self, tmp_path: Path):
        """目录不存在 → FileNotFoundError"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            arch.load_experiment("no_strategy", "no_run")

    def test_get_nav_series_file_not_found(self, tmp_path: Path):
        """nav_series.csv 不存在 → 空列表"""
        arch = ExperimentArchiver(base_dir=str(tmp_path))
        result = arch.get_nav_series("missing", "missing_run")
        assert result == []


# ============================================================================
# fingerprint_utils – validate with custom ignore_fields
# ============================================================================

from aipha.data.fingerprint_utils import (
    FingerprintMismatchError,
    compare_fingerprints,
    validate_fingerprint as validate_fp_dict,
)


class TestFingerprintUtilsEdge:
    """fingerprint_utils 边界"""

    def test_validate_custom_ignore_fields(self):
        """自定义 ignore_fields 应忽略对应差异"""
        fp1 = {"a": 1, "b": 2, "timestamp": "t1", "x": 10}
        fp2 = {"a": 1, "b": 2, "timestamp": "t2", "x": 99}
        # 默认只忽略 timestamp → x 不同导致异常
        with pytest.raises(FingerprintMismatchError):
            validate_fp_dict(fp1, fp2)
        # 忽略 timestamp + x → 通过
        assert validate_fp_dict(fp1, fp2, ignore_fields=["timestamp", "x"]) is True

    def test_validate_identical_fingerprints(self):
        fp = {"a": 1, "timestamp": "2025"}
        assert validate_fp_dict(fp, fp) is True

    def test_compare_finds_differences(self):
        fp1 = {"a": 1, "b": 2}
        fp2 = {"a": 1, "b": 3, "c": 4}
        diff = compare_fingerprints(fp1, fp2)
        assert "b" in diff and diff["b"] == (2, 3)
        assert "c" in diff and diff["c"] == (None, 4)

    def test_compare_identical(self):
        fp = {"a": 1}
        assert compare_fingerprints(fp, fp) == {}
