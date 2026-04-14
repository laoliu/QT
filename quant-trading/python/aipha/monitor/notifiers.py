"""通知渠道 — 支持 Webhook / Email / 企业微信 / 钉钉 / 飞书"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

_thread_pool = ThreadPoolExecutor(max_workers=2)


# ── 枚举 & 消息 ──────────────────────────────────────────────

class AlertLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    TRADE = "trade"
    RISK = "risk"
    SYSTEM = "system"
    MARKET = "market"
    PERFORMANCE = "performance"


@dataclass
class AlertMessage:
    title: str
    content: str
    level: AlertLevel = AlertLevel.INFO
    category: AlertCategory = AlertCategory.SYSTEM
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "level": self.level.value,
            "category": self.category.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "metadata": self.metadata,
        }

    def format_text(self) -> str:
        return (
            f"[{self.level.value.upper()}][{self.category.value}] {self.title}\n"
            f"{self.content}\n"
            f"时间: {self.timestamp:%Y-%m-%d %H:%M:%S}"
        )

    def format_markdown(self) -> str:
        emoji = {"debug": "🔍", "info": "ℹ️", "warning": "⚠️",
                 "error": "❌", "critical": "🔥"}.get(self.level.value, "📋")
        return (
            f"{emoji} **{self.title}**\n\n"
            f"> 级别: {self.level.value} | 分类: {self.category.value}\n\n"
            f"{self.content}\n\n"
            f"*{self.timestamp:%Y-%m-%d %H:%M:%S}*"
        )


# ── 渠道配置 ──────────────────────────────────────────────

@dataclass
class WebhookConfig:
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    retry_count: int = 3
    retry_delay: float = 1.0


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    use_tls: bool = True
    timeout: float = 30.0


@dataclass
class WeChatWorkConfig:
    webhook_url: str
    mentioned_list: list[str] = field(default_factory=list)
    mentioned_mobile_list: list[str] = field(default_factory=list)


@dataclass
class DingTalkConfig:
    webhook_url: str
    secret: str = ""
    at_mobiles: list[str] = field(default_factory=list)
    at_all: bool = False


@dataclass
class FeishuConfig:
    webhook_url: str
    secret: str = ""


# ── 渠道抽象 ──────────────────────────────────────────────

class NotificationChannel(ABC):
    """通知渠道抽象基类"""

    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        ...

    @abstractmethod
    def get_name(self) -> str:
        ...

    def filter_message(self, message: AlertMessage) -> bool:
        """返回 True 表示此消息应该被过滤（不发送）"""
        return False


class WebhookChannel(NotificationChannel):
    def __init__(self, name: str, config: WebhookConfig):
        self._name = name
        self._config = config

    def get_name(self) -> str:
        return self._name

    async def send(self, message: AlertMessage) -> bool:
        import urllib.request
        payload = json.dumps(message.to_dict()).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._config.headers}
        for attempt in range(self._config.retry_count):
            try:
                req = urllib.request.Request(
                    self._config.url, data=payload,
                    headers=headers, method=self._config.method,
                )
                with urllib.request.urlopen(req, timeout=self._config.timeout) as resp:
                    if resp.status < 400:
                        return True
            except Exception as e:
                logger.warning("Webhook %s attempt %d failed: %s", self._name, attempt + 1, e)
                if attempt < self._config.retry_count - 1:
                    await asyncio.sleep(self._config.retry_delay)
        return False


class EmailChannel(NotificationChannel):
    def __init__(self, name: str, config: EmailConfig):
        self._name = name
        self._config = config

    def get_name(self) -> str:
        return self._name

    async def send(self, message: AlertMessage) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_thread_pool, self._send_sync, message)

    def _send_sync(self, message: AlertMessage) -> bool:
        cfg = self._config
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{message.level.value.upper()}] {message.title}"
            msg["From"] = cfg.sender or cfg.username
            msg["To"] = ", ".join(cfg.recipients)
            msg.attach(MIMEText(message.format_text(), "plain", "utf-8"))
            msg.attach(MIMEText(self._render_html(message), "html", "utf-8"))

            if cfg.use_tls:
                ctx = ssl.create_default_context()
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as s:
                    s.starttls(context=ctx)
                    if cfg.username:
                        s.login(cfg.username, cfg.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as s:
                    if cfg.username:
                        s.login(cfg.username, cfg.password)
                    s.send_message(msg)
            return True
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return False

    @staticmethod
    def _render_html(message: AlertMessage) -> str:
        color = {"debug": "#888", "info": "#17a", "warning": "#f80",
                 "error": "#d00", "critical": "#900"}.get(message.level.value, "#333")
        return (
            f'<div style="font-family:sans-serif">'
            f'<h2 style="color:{color}">{message.title}</h2>'
            f'<p>{message.content}</p>'
            f'<hr><small>{message.timestamp:%Y-%m-%d %H:%M:%S} | '
            f'{message.level.value} | {message.category.value}</small></div>'
        )


class WeChatWorkChannel(NotificationChannel):
    def __init__(self, name: str, config: WeChatWorkConfig):
        self._name = name
        self._config = config

    def get_name(self) -> str:
        return self._name

    async def send(self, message: AlertMessage) -> bool:
        import urllib.request
        body = {
            "msgtype": "markdown",
            "markdown": {"content": self._format_message(message)},
        }
        try:
            req = urllib.request.Request(
                self._config.webhook_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception as e:
            logger.error("WeChatWork send failed: %s", e)
            return False

    def _format_message(self, message: AlertMessage) -> str:
        mentions = " ".join(f"<@{u}>" for u in self._config.mentioned_list)
        return f"{message.format_markdown()}\n{mentions}"


class DingTalkChannel(NotificationChannel):
    def __init__(self, name: str, config: DingTalkConfig):
        self._name = name
        self._config = config

    def get_name(self) -> str:
        return self._name

    async def send(self, message: AlertMessage) -> bool:
        import urllib.request
        url = self._get_signed_url() if self._config.secret else self._config.webhook_url
        body = {
            "msgtype": "markdown",
            "markdown": {"title": message.title, "text": message.format_markdown()},
            "at": {"atMobiles": self._config.at_mobiles, "isAtAll": self._config.at_all},
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception as e:
            logger.error("DingTalk send failed: %s", e)
            return False

    def _get_signed_url(self) -> str:
        ts = str(int(time.time() * 1000))
        sign_str = f"{ts}\n{self._config.secret}"
        hmac_code = hmac.new(
            self._config.secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        import base64
        sign = quote_plus(base64.b64encode(hmac_code))
        return f"{self._config.webhook_url}&timestamp={ts}&sign={sign}"


class FeishuChannel(NotificationChannel):
    def __init__(self, name: str, config: FeishuConfig):
        self._name = name
        self._config = config

    def get_name(self) -> str:
        return self._name

    async def send(self, message: AlertMessage) -> bool:
        import urllib.request
        color = self._level_color(message.level)
        payload = self._build_payload(message, color)
        url = self._get_signed_url() if self._config.secret else self._config.webhook_url
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception as e:
            logger.error("Feishu send failed: %s", e)
            return False

    @staticmethod
    def _level_color(level: AlertLevel) -> str:
        return {"debug": "grey", "info": "blue", "warning": "orange",
                "error": "red", "critical": "red"}.get(level.value, "grey")

    @staticmethod
    def _build_payload(message: AlertMessage, color: str) -> dict:
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": message.title},
                           "template": color},
                "elements": [
                    {"tag": "markdown", "content": message.format_markdown()},
                ],
            },
        }

    def _get_signed_url(self) -> str:
        ts = str(int(time.time()))
        sign_str = f"{ts}\n{self._config.secret}"
        hmac_code = hmac.new(sign_str.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
        import base64
        sign = quote_plus(base64.b64encode(hmac_code))
        return f"{self._config.webhook_url}&timestamp={ts}&sign={sign}"


# ── 通知管理器 ─────────────────────────────────────────────

class NotificationManager:
    """统一管理多渠道通知路由"""

    def __init__(self) -> None:
        self._channels: dict[str, NotificationChannel] = {}
        self._routing: dict[tuple[AlertLevel, AlertCategory], list[str]] = {}
        self._default_channels: list[str] = []

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels[channel.get_name()] = channel

    def remove_channel(self, name: str) -> bool:
        return self._channels.pop(name, None) is not None

    def set_routing(self, level: AlertLevel, category: AlertCategory,
                    channels: list[str]) -> None:
        self._routing[(level, category)] = channels

    def set_default_channels(self, channels: list[str]) -> None:
        self._default_channels = channels

    def get_target_channels(self, message: AlertMessage) -> list[str]:
        key = (message.level, message.category)
        return self._routing.get(key, self._default_channels)

    async def send(self, message: AlertMessage) -> dict[str, bool]:
        targets = self.get_target_channels(message)
        results: dict[str, bool] = {}
        for name in targets:
            ch = self._channels.get(name)
            if ch is None:
                results[name] = False
                continue
            if ch.filter_message(message):
                results[name] = False
                continue
            try:
                results[name] = await ch.send(message)
            except Exception as e:
                logger.error("Channel %s error: %s", name, e)
                results[name] = False
        return results

    def send_sync(self, message: AlertMessage) -> dict[str, bool]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                return pool.submit(asyncio.run, self.send(message)).result()
        return asyncio.run(self.send(message))


# ── 便捷构建器 ─────────────────────────────────────────────

class AlertBuilder:
    """链式告警构建器"""

    def __init__(self, manager: NotificationManager):
        self._manager = manager

    async def _send(self, level: AlertLevel, title: str, content: str,
                    category: AlertCategory, **kwargs: Any) -> dict[str, bool]:
        msg = AlertMessage(title=title, content=content, level=level,
                           category=category, **kwargs)
        return await self._manager.send(msg)

    async def info(self, title: str, content: str,
                   category: AlertCategory = AlertCategory.SYSTEM, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.INFO, title, content, category, **kw)

    async def warning(self, title: str, content: str,
                      category: AlertCategory = AlertCategory.SYSTEM, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.WARNING, title, content, category, **kw)

    async def error(self, title: str, content: str,
                    category: AlertCategory = AlertCategory.SYSTEM, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.ERROR, title, content, category, **kw)

    async def critical(self, title: str, content: str,
                       category: AlertCategory = AlertCategory.SYSTEM, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.CRITICAL, title, content, category, **kw)

    async def trade_alert(self, title: str, content: str, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.INFO, title, content, AlertCategory.TRADE, **kw)

    async def risk_alert(self, title: str, content: str, **kw: Any) -> dict[str, bool]:
        return await self._send(AlertLevel.WARNING, title, content, AlertCategory.RISK, **kw)
