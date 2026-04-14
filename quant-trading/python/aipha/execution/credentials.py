"""凭证管理 — 安全加载券商 API 凭证"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def mask_sensitive(value: str, visible: int = 4) -> str:
    """掩码敏感字段, 只保留尾部可见字符"""
    if not value or len(value) <= visible:
        return "****"
    return "*" * (len(value) - visible) + value[-visible:]


@dataclass
class BrokerCredentials:
    broker: str
    account: str
    password: str = field(repr=False)
    app_key: str = field(default="", repr=False)
    app_secret: str = field(default="", repr=False)
    extra: dict = field(default_factory=dict, repr=False)

    def masked(self) -> dict:
        return {
            "broker": self.broker,
            "account": mask_sensitive(self.account),
            "password": mask_sensitive(self.password),
            "app_key": mask_sensitive(self.app_key) if self.app_key else "",
        }


class CredentialsLoader:
    """凭证加载器 — 支持环境变量、JSON 文件、dotenv"""

    @staticmethod
    def from_env(prefix: str = "BROKER_") -> Optional[BrokerCredentials]:
        broker = os.environ.get(f"{prefix}NAME")
        account = os.environ.get(f"{prefix}ACCOUNT")
        password = os.environ.get(f"{prefix}PASSWORD")
        if not all([broker, account, password]):
            return None
        return BrokerCredentials(
            broker=broker,  # type: ignore
            account=account,  # type: ignore
            password=password,  # type: ignore
            app_key=os.environ.get(f"{prefix}APP_KEY", ""),
            app_secret=os.environ.get(f"{prefix}APP_SECRET", ""),
        )

    @staticmethod
    def from_json(path: str | Path) -> BrokerCredentials:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"凭证文件不存在: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return BrokerCredentials(
            broker=data["broker"],
            account=data["account"],
            password=data["password"],
            app_key=data.get("app_key", ""),
            app_secret=data.get("app_secret", ""),
            extra=data.get("extra", {}),
        )

    @staticmethod
    def from_dotenv(path: str | Path = ".env", prefix: str = "BROKER_") -> Optional[BrokerCredentials]:
        p = Path(path)
        if not p.exists():
            return None
        env_vars = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env_vars[key.strip()] = val.strip().strip('"').strip("'")

        broker = env_vars.get(f"{prefix}NAME")
        account = env_vars.get(f"{prefix}ACCOUNT")
        password = env_vars.get(f"{prefix}PASSWORD")
        if not all([broker, account, password]):
            return None
        return BrokerCredentials(
            broker=broker,  # type: ignore
            account=account,  # type: ignore
            password=password,  # type: ignore
            app_key=env_vars.get(f"{prefix}APP_KEY", ""),
            app_secret=env_vars.get(f"{prefix}APP_SECRET", ""),
        )

    @classmethod
    def load(cls, json_path: str | None = None, env_prefix: str = "BROKER_") -> BrokerCredentials:
        """优先级: 环境变量 > JSON 文件 > .env"""
        cred = cls.from_env(env_prefix)
        if cred:
            logger.info("凭证来源: 环境变量")
            return cred

        if json_path:
            cred = cls.from_json(json_path)
            logger.info("凭证来源: JSON文件")
            return cred

        cred = cls.from_dotenv(prefix=env_prefix)
        if cred:
            logger.info("凭证来源: .env")
            return cred

        raise RuntimeError("未找到券商凭证, 请设置环境变量或提供凭证文件")
