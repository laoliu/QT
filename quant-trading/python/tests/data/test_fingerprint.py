"""测试：数据指纹和可复现性"""

import pytest
from datetime import date
from aipha.data.tushare_provider import TuShareProBarProvider
from aipha.data.calendar import CNStockCalendar
from aipha.data.fingerprint_utils import (
    generate_experiment_fingerprint,
    validate_fingerprint,
    FingerprintMismatchError,
)


class TestDataFingerprint:
    """测试：数据指纹生成"""

    def test_tushare_provider_fingerprint(self, mock_tushare_api):
        """测试：TuShare Provider 生成指纹"""
        provider = TuShareProBarProvider(token="test_token", cache_dir=None)
        fingerprint = provider.fingerprint()

        assert fingerprint.provider == "TuSharePro"
        assert fingerprint.snapshot  # 应有版本信息
        assert fingerprint.calendar == "cn_stock_v1"
        assert fingerprint.adjust == "qfq"
        assert fingerprint.schema == "bar_v1"

    def test_calendar_fingerprint(self):
        """测试：交易日历生成指纹"""
        calendar = CNStockCalendar()
        fingerprint = calendar.fingerprint()

        assert "cn_stock_v1" in fingerprint
        assert "range" in fingerprint or "fallback" in fingerprint
        assert len(fingerprint) > 10  # 应该是有意义的字符串

    def test_fingerprint_consistency(self, mock_tushare_api):
        """测试：相同配置生成相同指纹"""
        provider1 = TuShareProBarProvider(token="test_token", adjust="qfq")
        provider2 = TuShareProBarProvider(token="test_token", adjust="qfq")

        fp1 = provider1.fingerprint()
        fp2 = provider2.fingerprint()

        assert fp1 == fp2

    def test_fingerprint_different_adjust(self, mock_tushare_api):
        """测试：不同复权方式生成不同指纹"""
        provider1 = TuShareProBarProvider(token="test_token", adjust="qfq")
        provider2 = TuShareProBarProvider(token="test_token", adjust="hfq")

        fp1 = provider1.fingerprint()
        fp2 = provider2.fingerprint()

        assert fp1.adjust != fp2.adjust


class TestExperimentFingerprint:
    """测试：实验指纹（完整可复现性）"""

    def test_generate_experiment_fingerprint(self, mock_tushare_api, tmp_path):
        """测试：生成实验指纹"""
        provider = TuShareProBarProvider(token="test_token", cache_dir=str(tmp_path))
        calendar = CNStockCalendar()

        # 生成完整实验指纹
        exp_fp = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={
                "symbols": ["000001.SZ", "000002.SZ"],
                "start": "2024-01-01",
                "end": "2024-12-31",
                "frequency": "1min",
            },
            git_commit="abc123def456",
        )

        # 验证字段
        assert exp_fp["provider"] == "TuSharePro"
        assert exp_fp["calendar"].startswith("cn_stock_v1")
        assert exp_fp["config"]["symbols"] == ["000001.SZ", "000002.SZ"]
        assert exp_fp["git_commit"] == "abc123def456"
        assert "timestamp" in exp_fp

    def test_validate_fingerprint_match(self, mock_tushare_api):
        """测试：指纹验证通过"""
        provider = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        config = {"symbols": ["000001.SZ"], "start": "2024-01-01", "end": "2024-01-31"}

        # 生成指纹
        fp1 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config=config,
            git_commit="abc123",
        )

        # 验证相同配置
        fp2 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config=config,
            git_commit="abc123",
        )

        # 核心字段应匹配（忽略timestamp）
        assert fp1["provider"] == fp2["provider"]
        assert fp1["calendar"] == fp2["calendar"]
        assert fp1["config"] == fp2["config"]
        assert fp1["git_commit"] == fp2["git_commit"]

    def test_validate_fingerprint_mismatch_config(self, mock_tushare_api):
        """测试：配置不同导致指纹不匹配"""
        provider = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        fp1 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"symbols": ["000001.SZ"]},
            git_commit="abc123",
        )

        fp2 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"symbols": ["000002.SZ"]},  # 不同股票池
            git_commit="abc123",
        )

        assert fp1["config"] != fp2["config"]

    def test_fingerprint_serialization(self, mock_tushare_api, tmp_path):
        """测试：指纹序列化与反序列化"""
        import json

        provider = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        fp = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"test": "data"},
            git_commit="abc123",
        )

        # 序列化
        fp_file = tmp_path / "fingerprint.json"
        with open(fp_file, "w") as f:
            json.dump(fp, f, indent=2, default=str)

        # 反序列化
        with open(fp_file, "r") as f:
            loaded_fp = json.load(f)

        assert loaded_fp["provider"] == fp["provider"]
        assert loaded_fp["git_commit"] == fp["git_commit"]

    def test_compare_fingerprints(self, mock_tushare_api):
        """测试：比较两个指纹的差异"""
        from aipha.data.fingerprint_utils import compare_fingerprints

        provider1 = TuShareProBarProvider(token="test_token")
        provider2 = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        fp1 = generate_experiment_fingerprint(
            provider=provider1,
            calendar=calendar,
            config={"symbols": ["000001.SZ"]},
            git_commit="abc123",
        )

        fp2 = generate_experiment_fingerprint(
            provider=provider2,
            calendar=calendar,
            config={"symbols": ["000002.SZ"]},
            git_commit="def456",
        )

        diff = compare_fingerprints(fp1, fp2)
        assert "config" in diff
        assert "git_commit" in diff
        assert diff["git_commit"] == ("abc123", "def456")

    def test_validate_fingerprint_raises(self, mock_tushare_api):
        """测试：指纹验证不通过抛出异常"""
        provider = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        fp1 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"symbols": ["000001.SZ"]},
            git_commit="abc123",
        )

        fp2 = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"symbols": ["000002.SZ"]},
            git_commit="abc123",
        )

        with pytest.raises(FingerprintMismatchError, match="指纹不匹配"):
            validate_fingerprint(fp1, fp2)

    def test_save_and_load_fingerprint(self, mock_tushare_api, tmp_path):
        """测试：保存和加载指纹"""
        from aipha.data.fingerprint_utils import save_fingerprint, load_fingerprint

        provider = TuShareProBarProvider(token="test_token")
        calendar = CNStockCalendar()

        fp = generate_experiment_fingerprint(
            provider=provider,
            calendar=calendar,
            config={"test": "data"},
            git_commit="abc123",
        )

        # 保存
        fp_file = tmp_path / "fingerprint.json"
        save_fingerprint(fp, str(fp_file))

        # 加载
        loaded = load_fingerprint(str(fp_file))

        assert loaded["provider"] == fp["provider"]
        assert loaded["git_commit"] == fp["git_commit"]
        assert loaded["config"] == fp["config"]
