"""因子去冗余脚本测试"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from factor_dedup_analysis import (
    compute_correlation_clusters,
    load_feature_importance,
    load_feature_names_from_folds,
    select_best_per_cluster,
)


class TestCorrelationClusters:
    """相关性簇的 union-find 逻辑测试"""

    def test_no_clusters_when_low_corr(self):
        """低相关系数应无冗余簇。"""
        corr = np.eye(3)
        names = ["a", "b", "c"]
        clusters = compute_correlation_clusters(corr, names, 0.70)
        assert len(clusters) == 0

    def test_two_correlated_factor_cluster(self):
        """两个高相关因子应归入同一簇。"""
        corr = np.array([
            [1.0, 0.85, 0.1],
            [0.85, 1.0, 0.1],
            [0.1, 0.1, 1.0],
        ])
        names = ["a", "b", "c"]
        clusters = compute_correlation_clusters(corr, names, 0.70)
        assert len(clusters) == 1
        assert set(clusters[0]) == {"a", "b"}

    def test_chain_clustering(self):
        """链式关联 a-b-c 应归入同一簇。"""
        corr = np.array([
            [1.0, 0.80, 0.3],
            [0.80, 1.0, 0.75],
            [0.3, 0.75, 1.0],
        ])
        names = ["a", "b", "c"]
        clusters = compute_correlation_clusters(corr, names, 0.70)
        assert len(clusters) == 1
        assert set(clusters[0]) == {"a", "b", "c"}

    def test_negative_correlation(self):
        """负相关 (|ρ| >= 0.70) 也应归入簇。"""
        corr = np.array([
            [1.0, -0.80],
            [-0.80, 1.0],
        ])
        names = ["a", "b"]
        clusters = compute_correlation_clusters(corr, names, 0.70)
        assert len(clusters) == 1


class TestSelectBestPerCluster:
    """簇内因子选择测试"""

    def test_keeps_highest_importance(self):
        clusters = [["a", "b", "c"]]
        importance = {"a": 10, "b": 50, "c": 30}
        keep, drop = select_best_per_cluster(clusters, importance)
        assert keep == ["b"]
        assert set(drop) == {"a", "c"}

    def test_missing_importance_treated_as_zero(self):
        clusters = [["a", "b"]]
        importance = {"a": 5}  # b 无重要性
        keep, drop = select_best_per_cluster(clusters, importance)
        assert keep == ["a"]
        assert drop == ["b"]

    def test_multiple_clusters(self):
        clusters = [["a", "b"], ["c", "d"]]
        importance = {"a": 10, "b": 20, "c": 5, "d": 15}
        keep, drop = select_best_per_cluster(clusters, importance)
        assert set(keep) == {"b", "d"}
        assert set(drop) == {"a", "c"}


class TestLoadFeatureNames:
    """模型文件加载测试"""

    def test_load_from_fold(self, tmp_path):
        fold = tmp_path / "fold_0.txt"
        fold.write_text(
            "tree\nversion=v4\nnum_class=1\n"
            "feature_names=alpha beta gamma\n"
        )
        names = load_feature_names_from_folds(tmp_path)
        assert names == ["alpha", "beta", "gamma"]

    def test_missing_fold(self, tmp_path):
        names = load_feature_names_from_folds(tmp_path)
        assert names == []
