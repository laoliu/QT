"""告警日志处理器 — 自动拦截 WARNING+ 日志并推送飞书告警。

根本性解决方案：通过 Python logging.Handler 机制，使所有 logger.warning()
和 logger.error() 自动触发飞书告警推送，无需在每个调用点手动调用
send_feishu_alert()。

用法::

    from aipha.monitor.alert_handler import setup_alert_logging

    # 在入口点调用一次即可
    setup_alert_logging()

    # 此后所有 aipha.* 下的 WARNING+ 日志自动推送飞书
    logger = logging.getLogger("aipha.config")
    logger.warning("risk 节缺失")  # → 自动推送飞书告警

架构::

    logger.warning(msg)
        ↓
    FeishuAlertHandler.emit(record)
        ↓
    _category_from_logger(name)  → AlertCategory (RISK/TRADE/CONFIG/SYSTEM)
        ↓
    send_feishu_alert(title, content, level=...)
        ↓
    [飞书 lark-cli / Webhook] + [审计日志 JSONL]
        （内置 1h 去重 + 超时保护 + 降级通道）

.. seealso::
    - :func:`aipha.monitor.feishu_alert.send_feishu_alert` 底层发送
    - §13.18 Silent Pass 禁令
    - §14.2 告警即生命线
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── 日志级别 → 飞书告警级别映射 ──
_LEVEL_MAP: dict[int, str] = {
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}

# ── logger name → 告警类别映射（按前缀匹配） ──
_CATEGORY_PREFIXES: list[tuple[str, str]] = [
    ("aipha.risk", "RISK"),
    ("aipha.execution", "TRADE"),
    ("aipha.market", "TRADE"),
    ("aipha.runner", "SYSTEM"),
    ("aipha.data", "DATA"),
    ("aipha.monitor", "MONITOR"),
    ("aipha.combo_config", "CONFIG"),
    ("aipha.config", "CONFIG"),
    ("aipha.strategy", "STRATEGY"),
    ("aipha.backtest", "BACKTEST"),
]


def _category_from_logger(name: str) -> str:
    """根据 logger 名称推导告警类别。"""
    for prefix, category in _CATEGORY_PREFIXES:
        if name.startswith(prefix):
            return category
    if name.startswith("aipha."):
        return "SYSTEM"
    return "OTHER"


class FeishuAlertHandler(logging.Handler):
    """日志处理器：拦截 WARNING+ 日志并推送飞书告警。

    特性：
    - 非阻塞：告警发送在独立线程中执行，不阻塞日志写入
    - 去重：底层 send_feishu_alert 内置 1h 去重窗口
    - 失败安全：告警发送失败不影响主流程
    - 可配置：支持命名空间过滤、级别过滤
    """

    def __init__(
        self,
        level: int = logging.WARNING,
        namespace_prefixes: tuple[str, ...] = ("aipha.",),
        async_send: bool = True,
    ) -> None:
        super().__init__(level)
        self._namespace_prefixes = namespace_prefixes
        self._async_send = async_send

    def filter(self, record: logging.LogRecord) -> bool:
        """只拦截指定命名空间的日志。"""
        return any(
            record.name.startswith(p) for p in self._namespace_prefixes
        )

    def emit(self, record: logging.LogRecord) -> None:
        """发送告警。"""
        try:
            level_str = _LEVEL_MAP.get(record.levelno, "WARNING")
            category = _category_from_logger(record.name)
            title = f"[{category}] {record.name}"
            content = self.format(record) if self.formatter else record.getMessage()

            if self._async_send:
                t = threading.Thread(
                    target=self._do_send,
                    args=(title, content, level_str),
                    daemon=True,
                )
                t.start()
            else:
                self._do_send(title, content, level_str)
        except Exception:
            self.handleError(record)

    @staticmethod
    def _do_send(title: str, content: str, level: str) -> None:
        """实际发送（可在线程中执行）。"""
        try:
            from aipha.monitor.feishu_alert import send_feishu_alert
            send_feishu_alert(
                title=title,
                content=content,
                level=level,
                dedup=True,
            )
        except Exception:
            pass  # 告警发送失败不得影响主流程


# ── 模块级单例 ──
_handler_instance: FeishuAlertHandler | None = None
_setup_lock = threading.Lock()


def setup_alert_logging(
    *,
    level: int = logging.WARNING,
    namespace_prefixes: tuple[str, ...] = ("aipha.",),
    async_send: bool = True,
    target_logger: str | None = None,
) -> FeishuAlertHandler:
    """一次性配置告警日志处理器（幂等）。

    在应用入口点调用一次即可。重复调用返回同一个 handler 实例。

    Parameters
    ----------
    level:
        触发告警的最低日志级别，默认 WARNING。
    namespace_prefixes:
        只拦截这些前缀的 logger，默认 ``("aipha.",)``。
    async_send:
        是否在独立线程中发送告警（避免阻塞），默认 True。
    target_logger:
        挂载到哪个 logger。None = root logger（推荐），
        也可指定如 ``"aipha"`` 只拦截子树。

    Returns
    -------
    FeishuAlertHandler
        已挂载的 handler 实例。
    """
    global _handler_instance
    with _setup_lock:
        if _handler_instance is not None:
            return _handler_instance

        handler = FeishuAlertHandler(
            level=level,
            namespace_prefixes=namespace_prefixes,
            async_send=async_send,
        )
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)

        target = logging.getLogger(target_logger)
        target.addHandler(handler)

        _handler_instance = handler
        logger.info(
            "告警日志处理器已启用: level=%s, namespaces=%s, target=%s",
            logging.getLevelName(level),
            namespace_prefixes,
            target_logger or "root",
        )
        return handler


def teardown_alert_logging() -> None:
    """移除告警日志处理器（主要用于测试）。"""
    global _handler_instance
    with _setup_lock:
        if _handler_instance is None:
            return
        for lg_name in (None, "aipha"):
            lg = logging.getLogger(lg_name)
            if _handler_instance in lg.handlers:
                lg.removeHandler(_handler_instance)
        _handler_instance = None