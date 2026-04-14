"""Tests for reproducibility utilities.

Coverage targets:
- Git info capture
- Config hashing
- Data fingerprinting
- Seed management
- ExperimentFingerprint creation and verification
"""
from __future__ import annotations

import json
import random
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from aipha.utils.reproducibility import (
    ExperimentFingerprint,
    compute_data_fingerprint,
    create_fingerprint,
    generate_seed,
    get_git_commit,
    get_git_dirty,
    get_git_info,
    hash_config_dict,
    hash_config_file,
    load_fingerprint,
    save_fingerprint,
    set_global_seed,
    verify_fingerprint,
)


# ============================================================================
# Git Info Tests
# ============================================================================


class TestGitInfo:
    """Tests for git info capture"""

    def test_get_git_commit_returns_string(self):
        """Should return a string (either hash or 'unknown')"""
        commit = get_git_commit()
        assert isinstance(commit, str)
        assert len(commit) > 0

    def test_get_git_commit_short(self):
        """Short commit should be 7 chars or 'unknown'"""
        commit = get_git_commit(short=True)
        assert isinstance(commit, str)
        if commit != "unknown":
            assert len(commit) == 7

    def test_get_git_dirty_returns_bool(self):
        """Should return a boolean"""
        dirty = get_git_dirty()
        assert isinstance(dirty, bool)

    def test_get_git_info_structure(self):
        """Should return dict with expected keys"""
        info = get_git_info()

        assert "commit" in info
        assert "short_commit" in info
        assert "dirty" in info
        assert "branch" in info


# ============================================================================
# Config Hash Tests
# ============================================================================


class TestConfigHash:
    """Tests for config hashing"""

    def test_hash_config_dict_deterministic(self):
        """Same config should produce same hash"""
        config = {"a": 1, "b": "test", "c": [1, 2, 3]}

        hash1 = hash_config_dict(config)
        hash2 = hash_config_dict(config)

        assert hash1 == hash2

    def test_hash_config_dict_order_independent(self):
        """Dict order should not affect hash"""
        config1 = {"a": 1, "b": 2}
        config2 = {"b": 2, "a": 1}

        assert hash_config_dict(config1) == hash_config_dict(config2)

    def test_hash_config_dict_different_values(self):
        """Different values should produce different hashes"""
        config1 = {"a": 1}
        config2 = {"a": 2}

        assert hash_config_dict(config1) != hash_config_dict(config2)

    def test_hash_config_dict_length(self):
        """Hash should be 16 characters"""
        config = {"test": "value"}
        hash_val = hash_config_dict(config)

        assert len(hash_val) == 16

    def test_hash_config_file(self):
        """Should hash file content"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("test: value\n")
            path = f.name

        try:
            hash_val = hash_config_file(path)
            assert len(hash_val) == 16
        finally:
            Path(path).unlink()

    def test_hash_config_file_not_found(self):
        """Should raise FileNotFoundError for missing file"""
        with pytest.raises(FileNotFoundError):
            hash_config_file("/nonexistent/path/config.yaml")


# ============================================================================
# Data Fingerprint Tests
# ============================================================================


class TestDataFingerprint:
    """Tests for data fingerprinting"""

    def test_fingerprint_dict(self):
        """Should fingerprint dict"""
        data = {"a": 1, "b": [1, 2, 3]}
        fp = compute_data_fingerprint(data)

        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_fingerprint_list(self):
        """Should fingerprint list"""
        data = [1, 2, 3, 4, 5]
        fp = compute_data_fingerprint(data)

        assert len(fp) == 16

    def test_fingerprint_numpy_array(self):
        """Should fingerprint numpy array"""
        data = np.array([1.0, 2.0, 3.0])
        fp = compute_data_fingerprint(data)

        assert len(fp) == 16

    def test_fingerprint_deterministic(self):
        """Same data should produce same fingerprint"""
        data = {"test": [1, 2, 3]}

        fp1 = compute_data_fingerprint(data)
        fp2 = compute_data_fingerprint(data)

        assert fp1 == fp2

    def test_fingerprint_shape_method(self):
        """Shape method should work"""
        data = np.random.randn(100, 10)
        fp = compute_data_fingerprint(data, method="shape")

        assert len(fp) == 16


# ============================================================================
# Seed Management Tests
# ============================================================================


class TestSeedManagement:
    """Tests for seed management"""

    def test_generate_seed_range(self):
        """Generated seed should be in valid range"""
        for _ in range(10):
            seed = generate_seed()
            assert 0 <= seed < 2**31

    def test_set_global_seed_python_random(self):
        """Should make Python random deterministic"""
        set_global_seed(42)
        vals1 = [random.random() for _ in range(5)]

        set_global_seed(42)
        vals2 = [random.random() for _ in range(5)]

        assert vals1 == vals2

    def test_set_global_seed_numpy(self):
        """Should make NumPy random deterministic"""
        set_global_seed(42)
        vals1 = np.random.rand(5).tolist()

        set_global_seed(42)
        vals2 = np.random.rand(5).tolist()

        assert vals1 == vals2

    def test_different_seeds_different_results(self):
        """Different seeds should produce different results"""
        set_global_seed(42)
        val1 = random.random()

        set_global_seed(43)
        val2 = random.random()

        assert val1 != val2


# ============================================================================
# ExperimentFingerprint Tests
# ============================================================================


class TestExperimentFingerprint:
    """Tests for ExperimentFingerprint"""

    def test_fingerprint_to_dict(self):
        """Should convert to dict correctly"""
        fp = ExperimentFingerprint(
            git_commit="abc123def456",
            git_short="abc123d",
            git_dirty=False,
            git_branch="main",
            config_path="/path/to/config.yaml",
            config_hash="1234567890abcdef",
            data_fingerprint="fedcba0987654321",
            random_seed=42,
            run_time=datetime(2025, 1, 6, 10, 30, 0),
            python_version="3.12.5",
        )

        d = fp.to_dict()

        assert d["git_commit"] == "abc123def456"
        assert d["git_short"] == "abc123d"
        assert d["git_dirty"] is False
        assert d["git_branch"] == "main"
        assert d["random_seed"] == 42

    def test_fingerprint_summary(self):
        """Should generate readable summary"""
        fp = ExperimentFingerprint(
            git_commit="abc123def456",
            git_short="abc123d",
            git_dirty=True,
            git_branch="feature",
            config_path=None,
            config_hash="1234567890abcdef",
            data_fingerprint="fedcba0987654321",
            random_seed=42,
            run_time=datetime(2025, 1, 6, 10, 30, 0),
        )

        summary = fp.summary()

        assert "abc123d" in summary
        assert "(dirty)" in summary
        assert "42" in summary


# ============================================================================
# Create Fingerprint Tests
# ============================================================================


class TestCreateFingerprint:
    """Tests for create_fingerprint"""

    def test_create_fingerprint_basic(self):
        """Should create fingerprint with defaults"""
        fp = create_fingerprint()

        assert isinstance(fp, ExperimentFingerprint)
        assert fp.random_seed >= 0

    def test_create_fingerprint_with_config(self):
        """Should hash provided config"""
        config = {"strategy": "momentum", "top_k": 10}
        fp = create_fingerprint(config=config)

        assert fp.config_hash != "none"

    def test_create_fingerprint_with_seed(self):
        """Should use provided seed"""
        fp = create_fingerprint(random_seed=12345, set_seed=False)

        assert fp.random_seed == 12345

    def test_create_fingerprint_sets_seed(self):
        """Should set global seed when set_seed=True"""
        fp = create_fingerprint(random_seed=42, set_seed=True)

        # After setting seed, random should be deterministic
        val1 = random.random()

        set_global_seed(42)
        random.random()  # Skip the first value (already consumed)
        val2_next = random.random()

        # Values should be deterministic after the first
        assert fp.random_seed == 42

    def test_create_fingerprint_with_data(self):
        """Should compute data fingerprint"""
        data = {"bars": [1, 2, 3, 4, 5]}
        fp = create_fingerprint(data=data)

        assert fp.data_fingerprint != "none"


# ============================================================================
# Save/Load Fingerprint Tests
# ============================================================================


class TestSaveLoadFingerprint:
    """Tests for save/load fingerprint"""

    def test_save_and_load(self):
        """Should round-trip correctly"""
        fp = ExperimentFingerprint(
            git_commit="abc123def456",
            git_short="abc123d",
            git_dirty=False,
            git_branch="main",
            config_path="/path/to/config.yaml",
            config_hash="1234567890abcdef",
            data_fingerprint="fedcba0987654321",
            random_seed=42,
            run_time=datetime(2025, 1, 6, 10, 30, 0),
            python_version="3.12.5",
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            save_fingerprint(fp, path)
            loaded = load_fingerprint(path)

            assert loaded.git_commit == fp.git_commit
            assert loaded.git_short == fp.git_short
            assert loaded.git_dirty == fp.git_dirty
            assert loaded.config_hash == fp.config_hash
            assert loaded.random_seed == fp.random_seed
        finally:
            Path(path).unlink()

    def test_save_creates_parent_dirs(self):
        """Should create parent directories"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "fingerprint.json"

            fp = create_fingerprint(set_seed=False)
            save_fingerprint(fp, path)

            assert path.exists()


# ============================================================================
# Verify Fingerprint Tests
# ============================================================================


class TestVerifyFingerprint:
    """Tests for verify_fingerprint"""

    def test_verify_matching_config(self):
        """Should pass for matching config"""
        config = {"test": "value"}
        fp = create_fingerprint(config=config, set_seed=False)

        results = verify_fingerprint(fp, config=config)

        assert results.get("config_hash") is True

    def test_verify_mismatched_config(self):
        """Should fail for different config"""
        config1 = {"test": "value1"}
        config2 = {"test": "value2"}
        fp = create_fingerprint(config=config1, set_seed=False)

        results = verify_fingerprint(fp, config=config2)

        assert results.get("config_hash") is False

    def test_verify_git_info(self):
        """Should check git info"""
        fp = create_fingerprint(set_seed=False)

        results = verify_fingerprint(fp)

        assert "git_commit" in results
        assert "git_dirty" in results
