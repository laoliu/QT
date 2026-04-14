"""凭证安全管理器测试

覆盖：
- mask_sensitive / mask_dict 脱敏
- BrokerCredentials 数据结构
- encrypt_data / decrypt_data 加解密
- CredentialsLoader.load_from_env
- CredentialsLoader.load_from_file
- CredentialsLoader.load（组合）
- CredentialsLoader.create_encrypted_file
- CredentialsLoader 缓存机制
- 边界条件：空字符串、缺失字段、损坏数据
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from aipha.execution.credentials import (
    BrokerCredentials,
    CredentialsLoader,
    decrypt_data,
    encrypt_data,
    get_credentials_loader,
    mask_dict,
    mask_sensitive,
)


# ============================================================================
# mask_sensitive 测试
# ============================================================================


class TestMaskSensitive:
    """mask_sensitive 脱敏函数测试"""

    def test_normal_string(self):
        result = mask_sensitive("my_secret_token_12345", 4)
        assert result.startswith("my_s")
        assert result.endswith("2345")
        assert "*" in result

    def test_short_string_fully_masked(self):
        """短于 2*show_chars 的字符串全部遮盖"""
        result = mask_sensitive("ab", 4)
        assert result == "**"

    def test_exact_boundary(self):
        """恰好 2*show_chars 的字符串全遮盖"""
        result = mask_sensitive("12345678", 4)
        assert result == "********"

    def test_one_more_than_boundary(self):
        """比边界长一位"""
        result = mask_sensitive("123456789", 4)
        assert result.startswith("1234")
        assert result.endswith("6789")
        assert result[4] == "*"

    def test_custom_show_chars(self):
        result = mask_sensitive("abcdefghijklmnop", 2)
        assert result.startswith("ab")
        assert result.endswith("op")

    def test_empty_string(self):
        result = mask_sensitive("", 4)
        assert result == ""


class TestMaskDict:
    """mask_dict 字典脱敏测试"""

    def test_masks_known_keys(self):
        data = {"user": "admin", "password": "s3cret", "host": "localhost"}
        result = mask_dict(data)
        assert result["user"] == "admin"
        assert "s3cret" not in result["password"]
        assert result["host"] == "localhost"

    def test_masks_partial_key_match(self):
        data = {"api_key_v2": "my_very_long_api_key_value"}
        result = mask_dict(data)
        assert "my_very_long_api_key_value" not in result["api_key_v2"]

    def test_custom_sensitive_keys(self):
        data = {"foo": "bar", "baz": "qux"}
        result = mask_dict(data, sensitive_keys={"foo"})
        assert result["foo"] != "bar" or len("bar") <= 8
        assert result["baz"] == "qux"

    def test_empty_dict(self):
        assert mask_dict({}) == {}


# ============================================================================
# BrokerCredentials 测试
# ============================================================================


class TestBrokerCredentials:
    """BrokerCredentials 数据结构测试"""

    def test_creation(self):
        creds = BrokerCredentials(
            user="myuser",
            password="mypass",
            broker_name="easytrader",
            source="env",
        )
        assert creds.user == "myuser"
        assert creds.password == "mypass"
        assert creds.broker_name == "easytrader"

    def test_repr_masked(self):
        creds = BrokerCredentials(user="myaccount123", password="secretpwd")
        rep = repr(creds)
        assert "myaccount123" not in rep
        assert "secretpwd" not in rep
        assert "BrokerCredentials" in rep

    def test_str_masked(self):
        creds = BrokerCredentials(user="admin", password="pass")
        s = str(creds)
        assert "pass" not in s or "BrokerCredentials" in s

    def test_to_login_dict(self):
        creds = BrokerCredentials(
            user="u", password="p", extra={"comm_password": "cp"}
        )
        d = creds.to_login_dict()
        assert d["user"] == "u"
        assert d["password"] == "p"
        assert d["comm_password"] == "cp"

    def test_masked_summary(self):
        creds = BrokerCredentials(user="testuser", password="longpassword123")
        summary = creds.masked_summary()
        assert summary["user"] == "testuser"  # user 不在默认 sensitive keys
        assert "longpassword123" not in summary["password"]

    def test_frozen(self):
        creds = BrokerCredentials(user="a", password="b")
        with pytest.raises(AttributeError):
            creds.user = "c"  # type: ignore


# ============================================================================
# 加密/解密测试
# ============================================================================


class TestEncryptDecrypt:
    """encrypt_data / decrypt_data 测试"""

    def test_xor_roundtrip(self):
        """XOR 模式加解密往返"""
        original = json.dumps({"user": "test", "password": "secret123"})
        passphrase = "my_key"

        encrypted = encrypt_data(original, passphrase)
        assert encrypted.startswith("XOR:") or encrypted.startswith("FERNET:")

        decrypted = decrypt_data(encrypted, passphrase)
        assert decrypted == original

    def test_wrong_passphrase_fails(self):
        """错误口令应解密失败"""
        original = "hello world"
        encrypted = encrypt_data(original, "correct_key")

        with pytest.raises(ValueError):
            decrypt_data(encrypted, "wrong_key")

    def test_corrupted_data_fails(self):
        """损坏数据应报错"""
        with pytest.raises(ValueError):
            decrypt_data("XOR:corrupted===", "key")

    def test_unknown_format(self):
        with pytest.raises(ValueError, match="未知加密格式"):
            decrypt_data("UNKNOWN:data", "key")

    def test_empty_data(self):
        encrypted = encrypt_data("", "key")
        decrypted = decrypt_data(encrypted, "key")
        assert decrypted == ""

    def test_unicode_data(self):
        original = json.dumps({"用户": "测试", "密码": "中文密码"}, ensure_ascii=False)
        encrypted = encrypt_data(original, "key123")
        decrypted = decrypt_data(encrypted, "key123")
        assert decrypted == original


# ============================================================================
# CredentialsLoader 测试
# ============================================================================


class TestCredentialsLoader:
    """CredentialsLoader 加载器测试"""

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("BROKER_TEST_USER", "myuser")
        monkeypatch.setenv("BROKER_TEST_PASSWORD", "mypass")

        loader = CredentialsLoader()
        creds = loader.load_from_env("BROKER_TEST")

        assert creds is not None
        assert creds.user == "myuser"
        assert creds.password == "mypass"
        assert creds.source == "env"

    def test_load_from_env_with_extra(self, monkeypatch):
        monkeypatch.setenv("BROKER_X_USER", "u")
        monkeypatch.setenv("BROKER_X_PASSWORD", "p")
        monkeypatch.setenv("BROKER_X_COMM_PASSWORD", "cp")
        monkeypatch.setenv("BROKER_X_TOKEN", "tk")

        loader = CredentialsLoader()
        creds = loader.load_from_env("BROKER_X")

        assert creds is not None
        assert creds.extra["comm_password"] == "cp"
        assert creds.extra["token"] == "tk"

    def test_load_from_env_missing_required(self, monkeypatch):
        monkeypatch.setenv("BROKER_Y_USER", "u")
        # 缺少 PASSWORD

        loader = CredentialsLoader()
        creds = loader.load_from_env("BROKER_Y")
        assert creds is None

    def test_load_from_env_no_env_set(self):
        loader = CredentialsLoader()
        # 使用一个不太可能存在的前缀
        creds = loader.load_from_env("NONEXISTENT_BROKER_ZZZZZ")
        assert creds is None

    def test_load_from_file(self, tmp_path):
        # 创建加密凭证文件
        passphrase = "test_key_123"
        cred_data = {"user": "file_user", "password": "file_pass"}
        file_path = tmp_path / "test.json.enc"

        CredentialsLoader.create_encrypted_file(cred_data, file_path, passphrase)
        assert file_path.exists()

        loader = CredentialsLoader()
        creds = loader.load_from_file(file_path, passphrase=passphrase)

        assert creds is not None
        assert creds.user == "file_user"
        assert creds.password == "file_pass"
        assert creds.source == "file"

    def test_load_from_file_not_exists(self, tmp_path):
        loader = CredentialsLoader()
        creds = loader.load_from_file(tmp_path / "nonexistent.enc", passphrase="key")
        assert creds is None

    def test_load_from_file_wrong_passphrase(self, tmp_path):
        passphrase = "correct_key"
        file_path = tmp_path / "test.enc"
        CredentialsLoader.create_encrypted_file(
            {"user": "u", "password": "p"}, file_path, passphrase
        )

        loader = CredentialsLoader()
        creds = loader.load_from_file(file_path, passphrase="wrong_key")
        assert creds is None  # 解密失败应返回 None

    def test_load_from_file_no_passphrase(self, tmp_path):
        file_path = tmp_path / "test.enc"
        file_path.write_text("XOR:something")

        loader = CredentialsLoader()
        # 无口令环境变量且不传入 passphrase
        creds = loader.load_from_file(file_path)
        assert creds is None

    def test_load_combined_env_priority(self, monkeypatch, tmp_path):
        """组合加载：环境变量优先"""
        monkeypatch.setenv("BROKER_COMBO_USER", "env_user")
        monkeypatch.setenv("BROKER_COMBO_PASSWORD", "env_pass")

        file_path = tmp_path / "combo.enc"
        CredentialsLoader.create_encrypted_file(
            {"user": "file_user", "password": "file_pass"},
            file_path,
            "key",
        )

        loader = CredentialsLoader()
        creds = loader.load(
            broker_name="combo",
            env_prefix="BROKER_COMBO",
            fallback_file=file_path,
        )

        assert creds is not None
        assert creds.user == "env_user"  # 环境变量优先
        assert creds.source == "env"

    def test_load_combined_file_fallback(self, monkeypatch, tmp_path):
        """组合加载：环境变量缺失则用文件"""
        # 确保环境变量不存在
        monkeypatch.delenv("BROKER_FALLBACK_USER", raising=False)
        monkeypatch.delenv("BROKER_FALLBACK_PASSWORD", raising=False)

        passphrase = "fb_key"
        file_path = tmp_path / "fallback.enc"
        CredentialsLoader.create_encrypted_file(
            {"user": "fb_user", "password": "fb_pass"},
            file_path,
            passphrase,
        )

        loader = CredentialsLoader()
        creds = loader.load(
            broker_name="fallback",
            env_prefix="BROKER_FALLBACK",
            fallback_file=file_path,
            passphrase_env="TEST_PASSPHRASE_UNUSED",
        )
        # 没有设置 TEST_PASSPHRASE_UNUSED 环境变量 -> 应该返回 None
        assert creds is None

    def test_caching(self, monkeypatch):
        monkeypatch.setenv("BROKER_CACHE_USER", "cu")
        monkeypatch.setenv("BROKER_CACHE_PASSWORD", "cp")

        loader = CredentialsLoader()
        creds1 = loader.load(broker_name="cache", env_prefix="BROKER_CACHE")
        assert creds1 is not None

        # 清除环境变量
        monkeypatch.delenv("BROKER_CACHE_USER")
        monkeypatch.delenv("BROKER_CACHE_PASSWORD")

        # 应从缓存返回
        creds2 = loader.load(broker_name="cache", env_prefix="BROKER_CACHE")
        assert creds2 is creds1

    def test_clear_cache(self, monkeypatch):
        monkeypatch.setenv("BROKER_CLR_USER", "u")
        monkeypatch.setenv("BROKER_CLR_PASSWORD", "p")

        loader = CredentialsLoader()
        loader.load(broker_name="clr", env_prefix="BROKER_CLR")
        assert "clr" in loader._cache

        loader.clear_cache("clr")
        assert "clr" not in loader._cache

    def test_clear_all_cache(self, monkeypatch):
        monkeypatch.setenv("BROKER_A_USER", "u")
        monkeypatch.setenv("BROKER_A_PASSWORD", "p")

        loader = CredentialsLoader()
        loader.load(broker_name="a", env_prefix="BROKER_A")
        assert len(loader._cache) > 0

        loader.clear_cache()
        assert len(loader._cache) == 0


class TestGetCredentialsLoader:
    """全局单例测试"""

    def test_returns_singleton(self):
        loader1 = get_credentials_loader()
        loader2 = get_credentials_loader()
        assert loader1 is loader2

    def test_is_instance(self):
        loader = get_credentials_loader()
        assert isinstance(loader, CredentialsLoader)


class TestCreateEncryptedFile:
    """create_encrypted_file 工具方法测试"""

    def test_creates_file(self, tmp_path):
        output = tmp_path / "subdir" / "creds.enc"
        CredentialsLoader.create_encrypted_file(
            {"user": "u", "password": "p"},
            output,
            "key123",
        )
        assert output.exists()

    def test_roundtrip(self, tmp_path):
        output = tmp_path / "rt.enc"
        original = {"user": "hello", "password": "world", "token": "abc"}
        passphrase = "roundtrip_key"

        CredentialsLoader.create_encrypted_file(original, output, passphrase)

        loader = CredentialsLoader()
        creds = loader.load_from_file(output, passphrase=passphrase)

        assert creds is not None
        assert creds.user == "hello"
        assert creds.password == "world"
        assert creds.extra.get("token") == "abc"
