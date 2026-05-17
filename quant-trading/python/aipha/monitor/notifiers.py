"""
告警通知渠道

提供多渠道告警通知能力：Webhook、邮件、企业微信等。
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any
from urllib.parse import urljoin


logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    """告警级别"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    """告警类别"""

    TRADE = "trade"  # 交易相关
    RISK = "risk"  # 风险相关
    SYSTEM = "system"  # 系统相关
    MARKET = "market"  # 行情相关
    PERFORMANCE = "performance"  # 绩效相关


@dataclass
class AlertMessage:
    """告警消息"""

    title: str
    content: str
    level: AlertLevel = AlertLevel.INFO
    category: AlertCategory = AlertCategory.SYSTEM
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
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
        """格式化为纯文本"""
        lines = [
            f"[{self.level.value.upper()}] {self.title}",
            f"时间: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"类别: {self.category.value}",
            "",
            self.content,
        ]
        if self.source:
            lines.insert(1, f"来源: {self.source}")
        return "\n".join(lines)

    def format_markdown(self) -> str:
        """格式化为Markdown"""
        level_emoji = {
            AlertLevel.DEBUG: "🔍",
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(self.level, "📢")

        lines = [
            f"# {emoji} {self.title}",
            "",
            f"**时间**: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**级别**: {self.level.value.upper()}",
            f"**类别**: {self.category.value}",
        ]
        if self.source:
            lines.append(f"**来源**: {self.source}")
        lines.extend(["", "---", "", self.content])
        return "\n".join(lines)


class NotificationChannel(ABC):
    """通知渠道抽象基类"""

    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        """发送通知

        Args:
            message: 告警消息

        Returns:
            是否发送成功
        """
        ...

    @abstractmethod
    def get_name(self) -> str:
        """获取渠道名称"""
        ...

    def filter_message(self, message: AlertMessage) -> bool:
        """过滤消息（可覆盖）

        返回True表示应该发送这条消息
        """
        return True


@dataclass
class WebhookConfig:
    """Webhook配置"""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    retry_count: int = 3
    retry_delay: float = 1.0


class WebhookChannel(NotificationChannel):
    """Webhook通知渠道"""

    def __init__(self, config: WebhookConfig):
        self.config = config
        self._session = None

    async def send(self, message: AlertMessage) -> bool:
        """通过 Webhook 发送告警消息，支持重试。"""
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed. Run: pip install aiohttp")
            return False

        headers = {"Content-Type": "application/json", **self.config.headers}
        payload = message.to_dict()

        for attempt in range(self.config.retry_count):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        self.config.method,
                        self.config.url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    ) as response:
                        if response.status < 300:
                            logger.info(f"Webhook sent successfully: {message.title}")
                            return True
                        else:
                            logger.warning(
                                f"Webhook failed with status {response.status}"
                            )
            except Exception as e:
                logger.error(f"Webhook attempt {attempt + 1} failed: {e}")

            if attempt < self.config.retry_count - 1:
                await asyncio.sleep(self.config.retry_delay)

        return False

    def get_name(self) -> str:
        """返回渠道名称 'webhook'。"""
        return "webhook"


@dataclass
class EmailConfig:
    """邮件配置"""

    smtp_host: str
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    use_tls: bool = True
    timeout: float = 30.0


class EmailChannel(NotificationChannel):
    """邮件通知渠道"""

    def __init__(self, config: EmailConfig):
        self.config = config

    async def send(self, message: AlertMessage) -> bool:
        """通过线程池异步发送邮件告警。"""
        try:
            # 在线程池中执行同步SMTP操作
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._send_sync, message)
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    def _send_sync(self, message: AlertMessage) -> bool:
        """同步发送邮件"""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{message.level.value.upper()}] {message.title}"
            msg["From"] = self.config.sender or self.config.username
            msg["To"] = ", ".join(self.config.recipients)

            # 纯文本版本
            text_part = MIMEText(message.format_text(), "plain", "utf-8")
            msg.attach(text_part)

            # HTML版本
            html_content = self._render_html(message)
            html_part = MIMEText(html_content, "html", "utf-8")
            msg.attach(html_part)

            # 发送
            context = ssl.create_default_context() if self.config.use_tls else None

            with smtplib.SMTP(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=int(self.config.timeout),
            ) as server:
                if self.config.use_tls:
                    server.starttls(context=context)
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.sendmail(
                    msg["From"],
                    self.config.recipients,
                    msg.as_string(),
                )

            logger.info(f"Email sent successfully: {message.title}")
            return True

        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    def _render_html(self, message: AlertMessage) -> str:
        """渲染HTML邮件"""
        level_colors = {
            AlertLevel.DEBUG: "#6c757d",
            AlertLevel.INFO: "#17a2b8",
            AlertLevel.WARNING: "#ffc107",
            AlertLevel.ERROR: "#dc3545",
            AlertLevel.CRITICAL: "#721c24",
        }
        color = level_colors.get(message.level, "#333")

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
                .header {{ background: {color}; color: white; padding: 15px; border-radius: 5px 5px 0 0; }}
                .content {{ background: #f8f9fa; padding: 20px; border: 1px solid #ddd; border-top: none; }}
                .meta {{ color: #666; font-size: 12px; margin-bottom: 15px; }}
                .body {{ white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2 style="margin: 0;">{message.title}</h2>
            </div>
            <div class="content">
                <div class="meta">
                    <strong>时间:</strong> {message.timestamp.strftime('%Y-%m-%d %H:%M:%S')} |
                    <strong>级别:</strong> {message.level.value.upper()} |
                    <strong>类别:</strong> {message.category.value}
                    {f' | <strong>来源:</strong> {message.source}' if message.source else ''}
                </div>
                <div class="body">{message.content}</div>
            </div>
        </body>
        </html>
        """

    def get_name(self) -> str:
        """返回渠道名称 'email'。"""
        return "email"


@dataclass
class WeChatWorkConfig:
    """企业微信配置"""

    webhook_url: str
    mentioned_list: list[str] = field(default_factory=list)  # @用户列表
    mentioned_mobile_list: list[str] = field(default_factory=list)  # @手机号列表


class WeChatWorkChannel(NotificationChannel):
    """企业微信通知渠道"""

    def __init__(self, config: WeChatWorkConfig):
        self.config = config

    async def send(self, message: AlertMessage) -> bool:
        """通过企业微信 Webhook 发送 Markdown 格式告警。"""
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed. Run: pip install aiohttp")
            return False

        # 格式化消息
        content = self._format_message(message)

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10.0),
                ) as response:
                    result = await response.json()
                    if result.get("errcode") == 0:
                        logger.info(f"WeChat Work sent successfully: {message.title}")
                        return True
                    else:
                        logger.error(f"WeChat Work failed: {result}")
                        return False
        except Exception as e:
            logger.error(f"WeChat Work send failed: {e}")
            return False

    def _format_message(self, message: AlertMessage) -> str:
        """格式化企业微信消息"""
        level_emoji = {
            AlertLevel.DEBUG: "🔍",
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(message.level, "📢")

        lines = [
            f"## {emoji} {message.title}",
            f"> **时间**: {message.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"> **级别**: <font color=\"warning\">{message.level.value.upper()}</font>",
            f"> **类别**: {message.category.value}",
            "",
            message.content,
        ]

        # 添加@提醒
        if self.config.mentioned_list:
            mentions = " ".join(f"<@{uid}>" for uid in self.config.mentioned_list)
            lines.append("")
            lines.append(mentions)

        return "\n".join(lines)

    def get_name(self) -> str:
        """返回渠道名称 'wechat_work'。"""
        return "wechat_work"


@dataclass
class DingTalkConfig:
    """钉钉配置"""

    webhook_url: str
    secret: str = ""  # 加签密钥
    at_mobiles: list[str] = field(default_factory=list)
    at_all: bool = False


class DingTalkChannel(NotificationChannel):
    """钉钉通知渠道"""

    def __init__(self, config: DingTalkConfig):
        self.config = config

    async def send(self, message: AlertMessage) -> bool:
        """通过钉钉机器人 Webhook 发送 Markdown 告警。"""
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed. Run: pip install aiohttp")
            return False

        # 构造签名URL（如果配置了secret）
        url = self._get_signed_url()

        # 格式化消息
        content = message.format_markdown()

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": message.title,
                "text": content,
            },
            "at": {
                "atMobiles": self.config.at_mobiles,
                "isAtAll": self.config.at_all,
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10.0),
                ) as response:
                    result = await response.json()
                    if result.get("errcode") == 0:
                        logger.info(f"DingTalk sent successfully: {message.title}")
                        return True
                    else:
                        logger.error(f"DingTalk failed: {result}")
                        return False
        except Exception as e:
            logger.error(f"DingTalk send failed: {e}")
            return False

    def _get_signed_url(self) -> str:
        """获取签名URL"""
        if not self.config.secret:
            return self.config.webhook_url

        import base64
        import hashlib
        import hmac
        import time
        import urllib.parse

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        hmac_code = hmac.new(
            self.config.secret.encode(),
            string_to_sign.encode(),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode())

        return f"{self.config.webhook_url}&timestamp={timestamp}&sign={sign}"

    def get_name(self) -> str:
        """返回渠道名称 'dingtalk'。"""
        return "dingtalk"


@dataclass
class FeishuConfig:
    """飞书机器人配置"""

    webhook_url: str
    secret: str = ""  # 签名校验密钥（可选）


class FeishuChannel(NotificationChannel):
    """飞书机器人通知渠道

    通过飞书自定义机器人 Webhook 发送富文本告警消息。
    飞书 Webhook 文档: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """

    def __init__(self, config: FeishuConfig):
        self.config = config

    async def send(self, message: AlertMessage) -> bool:
        """通过飞书机器人 Webhook 发送 Interactive Card 格式告警。"""
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not installed. Run: pip install aiohttp")
            return False

        url = self._get_signed_url()
        payload = self._build_payload(message)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10.0),
                ) as response:
                    result = await response.json()
                    if result.get("code") == 0 or result.get("StatusCode") == 0:
                        logger.info(f"Feishu sent successfully: {message.title}")
                        return True
                    else:
                        logger.error(f"Feishu failed: {result}")
                        return False
        except Exception as e:
            logger.error(f"Feishu send failed: {e}")
            return False

    def _build_payload(self, message: AlertMessage) -> dict:
        """构造飞书富文本消息体。"""
        level_emoji = {
            AlertLevel.DEBUG: "🔍",
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(message.level, "📢")
        ts = message.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        content = (
            f"{emoji} **{message.title}**\n"
            f"时间: {ts}\n"
            f"级别: {message.level.value.upper()}\n"
            f"类别: {message.category.value}\n\n"
            f"{message.content}"
        )

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{emoji} {message.title}",
                    },
                    "template": self._level_color(message.level),
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content,
                    }
                ],
            },
        }

    @staticmethod
    def _level_color(level: AlertLevel) -> str:
        """映射告警级别到飞书卡片颜色模板。"""
        mapping = {
            AlertLevel.DEBUG: "grey",
            AlertLevel.INFO: "blue",
            AlertLevel.WARNING: "orange",
            AlertLevel.ERROR: "red",
            AlertLevel.CRITICAL: "red",
        }
        return mapping.get(level, "blue")

    def _get_signed_url(self) -> str:
        """获取签名 URL（如配置了 secret）。"""
        if not self.config.secret:
            return self.config.webhook_url

        import hashlib
        import hmac
        import base64
        import time

        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode(),
            digestmod=hashlib.sha256,
        ).digest()
        sign = base64.b64encode(hmac_code).decode()

        sep = "&" if "?" in self.config.webhook_url else "?"
        return f"{self.config.webhook_url}{sep}timestamp={timestamp}&sign={sign}"

    def get_name(self) -> str:
        """返回渠道名称 'feishu'。"""
        return "feishu"


class NotificationManager:
    """通知管理器

    管理多个通知渠道，支持按级别和类别路由。
    """

    def __init__(self):
        self._channels: dict[str, NotificationChannel] = {}
        self._routing_rules: list[tuple[AlertLevel, AlertCategory | None, list[str]]] = []
        self._default_channels: list[str] = []

    def add_channel(self, channel: NotificationChannel) -> None:
        """添加通知渠道"""
        self._channels[channel.get_name()] = channel
        logger.info(f"Added notification channel: {channel.get_name()}")

    def remove_channel(self, name: str) -> bool:
        """移除通知渠道"""
        if name in self._channels:
            del self._channels[name]
            return True
        return False

    def set_routing(
        self,
        level: AlertLevel,
        category: AlertCategory | None,
        channels: list[str],
    ) -> None:
        """设置路由规则

        Args:
            level: 最低告警级别
            category: 告警类别（None表示所有类别）
            channels: 目标渠道列表
        """
        self._routing_rules.append((level, category, channels))

    def set_default_channels(self, channels: list[str]) -> None:
        """设置默认渠道"""
        self._default_channels = channels

    def get_target_channels(self, message: AlertMessage) -> list[str]:
        """获取消息应发送的渠道"""
        level_order = list(AlertLevel)
        message_level_idx = level_order.index(message.level)

        matched_channels = set()

        for rule_level, rule_category, channels in self._routing_rules:
            rule_level_idx = level_order.index(rule_level)

            # 级别匹配（消息级别 >= 规则级别）
            if message_level_idx < rule_level_idx:
                continue

            # 类别匹配
            if rule_category is not None and message.category != rule_category:
                continue

            matched_channels.update(channels)

        if not matched_channels:
            matched_channels.update(self._default_channels)

        return list(matched_channels)

    async def send(self, message: AlertMessage) -> dict[str, bool]:
        """发送通知

        Returns:
            渠道名 -> 是否成功
        """
        target_channels = self.get_target_channels(message)
        results = {}

        tasks = []
        for channel_name in target_channels:
            channel = self._channels.get(channel_name)
            if channel is None:
                logger.warning(f"Channel not found: {channel_name}")
                results[channel_name] = False
                continue

            if not channel.filter_message(message):
                logger.debug(f"Message filtered by {channel_name}")
                results[channel_name] = False
                continue

            tasks.append((channel_name, channel.send(message)))

        # 并行发送
        for channel_name, task in tasks:
            try:
                results[channel_name] = await task
            except Exception as e:
                logger.error(f"Channel {channel_name} failed: {e}")
                results[channel_name] = False

        return results

    def send_sync(self, message: AlertMessage) -> dict[str, bool]:
        """同步发送通知"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.send(message))
        finally:
            loop.close()


class AlertBuilder:
    """告警构建器（便捷API）"""

    def __init__(self, manager: NotificationManager):
        self._manager = manager

    def info(
        self,
        title: str,
        content: str,
        category: AlertCategory = AlertCategory.SYSTEM,
        **kwargs: Any,
    ) -> dict[str, bool]:
        """发送INFO级别告警"""
        return self._send(AlertLevel.INFO, title, content, category, **kwargs)

    def warning(
        self,
        title: str,
        content: str,
        category: AlertCategory = AlertCategory.SYSTEM,
        **kwargs: Any,
    ) -> dict[str, bool]:
        """发送WARNING级别告警"""
        return self._send(AlertLevel.WARNING, title, content, category, **kwargs)

    def error(
        self,
        title: str,
        content: str,
        category: AlertCategory = AlertCategory.SYSTEM,
        **kwargs: Any,
    ) -> dict[str, bool]:
        """发送ERROR级别告警"""
        return self._send(AlertLevel.ERROR, title, content, category, **kwargs)

    def critical(
        self,
        title: str,
        content: str,
        category: AlertCategory = AlertCategory.SYSTEM,
        **kwargs: Any,
    ) -> dict[str, bool]:
        """发送CRITICAL级别告警"""
        return self._send(AlertLevel.CRITICAL, title, content, category, **kwargs)

    def trade_alert(self, title: str, content: str, **kwargs: Any) -> dict[str, bool]:
        """发送交易告警"""
        return self._send(AlertLevel.WARNING, title, content, AlertCategory.TRADE, **kwargs)

    def risk_alert(self, title: str, content: str, **kwargs: Any) -> dict[str, bool]:
        """发送风险告警"""
        return self._send(AlertLevel.ERROR, title, content, AlertCategory.RISK, **kwargs)

    def _send(
        self,
        level: AlertLevel,
        title: str,
        content: str,
        category: AlertCategory,
        **kwargs,
    ) -> dict[str, bool]:
        message = AlertMessage(
            title=title,
            content=content,
            level=level,
            category=category,
            **kwargs,
        )
        return self._manager.send_sync(message)
