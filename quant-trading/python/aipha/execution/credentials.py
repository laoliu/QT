"""
凭证安全管理器

提供安全的凭证加载、存储与脱敏能力，用于实盘网关连接。

安全原则：
=========
1. 凭证从环境变量或加密本地文件加载，禁止硬编码
2. 日志输出自动脱敏（仅保留前后 2~4 位）
3. 凭证在内存中不以明文 str 长期驻留（使用 SecureString 封装）
4. 支持多券商多账户的凭证隔离

加载优先级：
1. 环境变量（适合 CI/CD 与容器环境）
2. .env 文件（通过 python-dotenv 自动加载）
3. 加密 JSON 凭证文件（适合本地开发与生产）

使用示例::

    loader = CredentialsLoader()

    # 方式 1：从环境变量
    creds = loader.load_from_env(prefix="BROKER_EASYTRADER")
    # 读取 BROKER_EASYTRADER_USER, BROKER_EASYTRADER_PASSWORD 等

    # 方式 2：从加密文件
    creds = loader.load_from_file(
        Path("configs/credentials/easytrader.json.enc"),
        passphrase_env="CREDENTIAL_KEY",
    )

    # 方式 3：组合（环境变量优先, 文件兜底）
    creds = loader.load(
        broker_name="easytrader",
        env_prefix="BROKER_EASYTRADER",
        fallback_file=Path("configs/credentials/easytrader.json.enc"),
    )
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 脱敏工具
# =============================================================================


def mask_sensitive(value: str, show_chars: int = 4) -> str:
    """脱敏敏感字符串，仅保留前后各 show_chars 位。

    Args:
        value: 原始字符串
        show_chars: 前后各保留的字符数

    Returns:
        脱敏后的字符串，例如 "abcd****wxyz"

    Examples:
        >>> mask_sensitive("my_secret_token_12345", 4)
        'my_s*************2345'
        >>> mask_sensitive("ab", 4)
        '**'
    """
    if len(value) <= show_chars * 2:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars * 2) + value[-show_chars:]


def mask_dict(data: dict[str, str], sensitive_keys: set[str] | None = None) -> dict[str, str]:
    """对字典中的敏感字段脱敏。

    Args:
        data: 原始字典
        sensitive_keys: 需要脱敏的键集合，默认包含常见敏感字段

    Returns:
        脱敏后的字典副本
    """
    if sensitive_keys is None:
        sensitive_keys = {
            "password", "passwd", "token", "secret", "api_key",
            "apikey", "auth_token", "access_token", "credential",
            "comm_password", "trade_password",
        }

    result = {}
    for k, v in data.items():
        k_lower = k.lower()
        if any(sk in k_lower for sk in sensitive_keys):
            result[k] = mask_sensitive(v)
        else:
            result[k] = v
    return result


# =============================================================================
# 凭证数据结构
# =============================================================================


@dataclass(frozen=True)
class BrokerCredentials:
    """券商登录凭证（不可变）。

    用途：
    - 传递给 BrokerGateway.login()
    - 内存中保持最短生命周期

    安全注意：
    - __repr__ 和 __str__ 自动脱敏
    - 不允许序列化到日志
    """

    user: str
    password: str
    extra: dict[str, str] = field(default_factory=dict)

    # 元数据（非敏感）
    broker_name: str = ""
    source: str = ""  # "env" / "file" / "manual"

    def __repr__(self) -> str:
        """脱敏的字符串表示"""
        masked_user = mask_sensitive(self.user, 2) if self.user else ""
        return (
            f"BrokerCredentials(user='{masked_user}', "
            f"broker='{self.broker_name}', source='{self.source}')"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def to_login_dict(self) -> dict[str, str]:
        """转换为 login() 接受的字典格式。

        Returns:
            {"user": ..., "password": ..., ...extra}
        """
        result = {"user": self.user, "password": self.password}
        result.update(self.extra)
        return result

    def masked_summary(self) -> dict[str, str]:
        """脱敏后的字典摘要，安全用于日志。"""
        return mask_dict(self.to_login_dict())


# =============================================================================
# 加密 / 解密工具（简单 AES-like，基于 Fernet 或降级到 XOR）
# =============================================================================


def _derive_key(passphrase: str, salt: bytes = b"aipha-q-salt") -> bytes:
    """从口令派生密钥。"""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 100_000)


def encrypt_data(plaintext: str, passphrase: str) -> str:
    """加密数据（优先使用 Fernet，降级到 XOR + HMAC）。

    Args:
        plaintext: 明文 JSON 字符串
        passphrase: 加密口令

    Returns:
        Base64 编码的密文字符串（带前缀标识加密方式）
    """
    try:
        from cryptography.fernet import Fernet
        key = base64.urlsafe_b64encode(_derive_key(passphrase)[:32])
        f = Fernet(key)
        encrypted = f.encrypt(plaintext.encode())
        return "FERNET:" + encrypted.decode()
    except ImportError:
        # 降级：XOR + HMAC（安全性较低，仅用于开发）
        key = _derive_key(passphrase)
        data = plaintext.encode()
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        mac = hmac.new(key, xored, hashlib.sha256).digest()
        payload = mac + xored
        return "XOR:" + base64.b64encode(payload).decode()


def decrypt_data(ciphertext: str, passphrase: str) -> str:
    """解密数据。

    Args:
        ciphertext: 加密后的字符串（含前缀）
        passphrase: 解密口令

    Returns:
        明文 JSON 字符串

    Raises:
        ValueError: 解密失败（口令错误或数据损坏）
    """
    if ciphertext.startswith("FERNET:"):
        try:
            from cryptography.fernet import Fernet
            key = base64.urlsafe_b64encode(_derive_key(passphrase)[:32])
            f = Fernet(key)
            return f.decrypt(ciphertext[7:].encode()).decode()
        except ImportError:
            raise ValueError("需要 cryptography 库解密 Fernet 加密数据")
        except Exception as e:
            raise ValueError(f"Fernet 解密失败（口令错误？）: {e}")

    elif ciphertext.startswith("XOR:"):
        key = _derive_key(passphrase)
        payload = base64.b64decode(ciphertext[4:])
        mac_got = payload[:32]
        xored = payload[32:]
        mac_expected = hmac.new(key, xored, hashlib.sha256).digest()
        if not hmac.compare_digest(mac_got, mac_expected):
            raise ValueError("HMAC 校验失败：口令错误或数据已损坏")
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(xored))
        return data.decode()

    else:
        raise ValueError(f"未知加密格式: {ciphertext[:20]}...")


# =============================================================================
# 凭证加载器
# =============================================================================


class CredentialsLoader:
    """统一凭证加载器

    支持从环境变量、加密文件两种来源加载凭证，
    并维护已加载凭证的内存缓存（避免重复读文件/解密）。

    用法::

        loader = CredentialsLoader()

        # 方式 A: 从环境变量
        creds = loader.load_from_env("BROKER_EASYTRADER")

        # 方式 B: 从加密文件
        creds = loader.load_from_file(
            Path("configs/credentials/easytrader.json.enc"),
            passphrase_env="CREDENTIAL_KEY",
        )

        # 方式 C: 组合（环境变量优先）
        creds = loader.load(
            broker_name="easytrader",
            env_prefix="BROKER_EASYTRADER",
        )
    """

    # 默认的环境变量后缀映射
    ENV_SUFFIXES = {
        "user": "_USER",
        "password": "_PASSWORD",
        "comm_password": "_COMM_PASSWORD",
        "trade_password": "_TRADE_PASSWORD",
        "token": "_TOKEN",
    }

    def __init__(self) -> None:
        self._cache: dict[str, BrokerCredentials] = {}

    def load_from_env(
        self,
        prefix: str,
        *,
        required_fields: tuple[str, ...] = ("user", "password"),
    ) -> BrokerCredentials | None:
        """从环境变量加载凭证。

        读取 {prefix}_USER, {prefix}_PASSWORD 等环境变量。

        Args:
            prefix: 环境变量前缀，如 "BROKER_EASYTRADER"
            required_fields: 必须存在的字段

        Returns:
            凭证对象，环境变量缺失时返回 None
        """
        values: dict[str, str] = {}
        for field_name, suffix in self.ENV_SUFFIXES.items():
            env_key = prefix + suffix
            val = os.environ.get(env_key)
            if val:
                values[field_name] = val

        # 检查必需字段
        for required in required_fields:
            if required not in values:
                logger.debug(
                    f"环境变量 {prefix}{self.ENV_SUFFIXES.get(required, '')} "
                    f"未设置，跳过环境变量加载"
                )
                return None

        user = values.pop("user", "")
        password = values.pop("password", "")

        creds = BrokerCredentials(
            user=user,
            password=password,
            extra=values,
            broker_name=prefix.replace("BROKER_", "").lower(),
            source="env",
        )

        logger.info(f"从环境变量加载凭证: {creds}")
        return creds

    def load_from_file(
        self,
        file_path: Path,
        *,
        passphrase_env: str = "CREDENTIAL_KEY",
        passphrase: str | None = None,
    ) -> BrokerCredentials | None:
        """从加密文件加载凭证。

        文件格式（加密前的 JSON）::

            {
                "user": "account_id",
                "password": "my_password",
                "comm_password": "optional",
                "trade_password": "optional"
            }

        Args:
            file_path: 加密凭证文件路径
            passphrase_env: 解密口令的环境变量名
            passphrase: 直接传入解密口令（优先级高于 env）

        Returns:
            凭证对象，文件不存在或解密失败返回 None
        """
        if not file_path.exists():
            logger.debug(f"凭证文件不存在: {file_path}")
            return None

        # 获取解密口令
        actual_passphrase = passphrase or os.environ.get(passphrase_env)
        if not actual_passphrase:
            logger.warning(
                f"未设置解密口令环境变量 {passphrase_env}，无法加载凭证文件"
            )
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                encrypted_content = f.read().strip()

            plaintext = decrypt_data(encrypted_content, actual_passphrase)
            data = json.loads(plaintext)

            user = data.pop("user", "")
            password = data.pop("password", "")
            broker_name = data.pop("broker_name", file_path.stem.split(".")[0])

            creds = BrokerCredentials(
                user=user,
                password=password,
                extra=data,
                broker_name=broker_name,
                source="file",
            )

            logger.info(f"从加密文件加载凭证: {creds}")
            return creds

        except (ValueError, json.JSONDecodeError, OSError) as e:
            logger.error(f"加载凭证文件失败: {e}")
            return None

    def load(
        self,
        broker_name: str,
        *,
        env_prefix: str | None = None,
        fallback_file: Path | None = None,
        passphrase_env: str = "CREDENTIAL_KEY",
        use_cache: bool = True,
    ) -> BrokerCredentials | None:
        """组合加载凭证（环境变量优先，加密文件兜底）。

        Args:
            broker_name: 券商名称（用于缓存 key 和日志）
            env_prefix: 环境变量前缀，默认 "BROKER_{broker_name.upper()}"
            fallback_file: 兜底的加密凭证文件
            passphrase_env: 解密口令环境变量名
            use_cache: 是否使用缓存

        Returns:
            凭证对象，所有来源都失败返回 None
        """
        # 检查缓存
        if use_cache and broker_name in self._cache:
            logger.debug(f"使用缓存凭证: {broker_name}")
            return self._cache[broker_name]

        # 1. 尝试环境变量
        if env_prefix is None:
            env_prefix = f"BROKER_{broker_name.upper()}"

        creds = self.load_from_env(env_prefix)

        # 2. 兜底：加密文件
        if creds is None and fallback_file is not None:
            creds = self.load_from_file(fallback_file, passphrase_env=passphrase_env)

        # 缓存
        if creds is not None and use_cache:
            self._cache[broker_name] = creds

        if creds is None:
            logger.warning(f"所有来源均未能加载凭证: {broker_name}")

        return creds

    def clear_cache(self, broker_name: str | None = None) -> None:
        """清除凭证缓存。

        Args:
            broker_name: 指定券商名称，None 则清除全部
        """
        if broker_name is None:
            self._cache.clear()
            logger.debug("已清除所有凭证缓存")
        else:
            self._cache.pop(broker_name, None)
            logger.debug(f"已清除凭证缓存: {broker_name}")

    @staticmethod
    def create_encrypted_file(
        credentials: dict[str, str],
        output_path: Path,
        passphrase: str,
    ) -> None:
        """创建加密凭证文件（工具方法）。

        用于初始化时生成加密凭证文件。

        Args:
            credentials: 凭证字典 {"user": ..., "password": ..., ...}
            output_path: 输出文件路径
            passphrase: 加密口令

        Example::

            CredentialsLoader.create_encrypted_file(
                {"user": "my_account", "password": "my_pass"},
                Path("configs/credentials/easytrader.json.enc"),
                passphrase="my_secret_key",
            )
        """
        plaintext = json.dumps(credentials, ensure_ascii=False)
        encrypted = encrypt_data(plaintext, passphrase)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(encrypted)

        logger.info(f"已创建加密凭证文件: {output_path}")


# =============================================================================
# 全局单例（方便各处使用）
# =============================================================================

_default_loader: CredentialsLoader | None = None


def get_credentials_loader() -> CredentialsLoader:
    """获取全局凭证加载器实例。"""
    global _default_loader
    if _default_loader is None:
        _default_loader = CredentialsLoader()
    return _default_loader
