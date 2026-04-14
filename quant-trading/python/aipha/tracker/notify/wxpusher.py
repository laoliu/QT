"""WxPusher notification integration.

WxPusher: https://wxpusher.zjiecode.com/docs
- Free: 2000 messages / day / user
- Supports HTML (contentType=2)
- HTTP POST API with appToken + UID

Configuration via configs/tracker/settings.yml:
    notification:
      wxpusher:
        app_token: "AT_xxxxx"
        uids: ["UID_xxxxx"]
        enabled: true
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..models import PortfolioDiff, TradePlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"


# ---------------------------------------------------------------------------
# WxPusherNotifier
# ---------------------------------------------------------------------------


class WxPusherNotifier:
    """Send WeChat notifications via WxPusher.

    Usage:
        notifier = WxPusherNotifier(app_token="AT_xxx", uids=["UID_xxx"])
        notifier.notify_diff(diff)
    """

    def __init__(
        self,
        app_token: str,
        uids: list[str],
        enabled: bool = True,
    ) -> None:
        if not app_token:
            raise ValueError("app_token must not be empty")
        if not uids:
            raise ValueError("uids must not be empty")
        self._app_token = app_token
        self._uids = uids
        self._enabled = enabled

    def send(
        self,
        content: str,
        summary: str | None = None,
        content_type: int = 2,
        url: str | None = None,
    ) -> bool:
        """Send a message via WxPusher.

        Args:
            content: Message content (HTML if content_type=2).
            summary: Short summary shown in notification bar (max 100 chars).
            content_type: 1=text, 2=HTML, 3=markdown.
            url: Optional click-through URL.

        Returns:
            True if all UIDs received the message.
        """
        if not self._enabled:
            logger.info("WxPusher disabled, skipping notification")
            return True

        if summary and len(summary) > 100:
            summary = summary[:97] + "..."

        payload: dict[str, Any] = {
            "appToken": self._app_token,
            "content": content,
            "summary": summary or content[:80],
            "contentType": content_type,
            "uids": self._uids,
        }
        if url:
            payload["url"] = url

        try:
            req = Request(
                WXPUSHER_API,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if result.get("code") == 1000:
                logger.info("WxPusher message sent successfully")
                return True
            else:
                logger.error("WxPusher error: %s", result.get("msg", "unknown"))
                return False

        except URLError as e:
            logger.error("WxPusher request failed: %s", e)
            return False
        except Exception as e:
            logger.error("WxPusher unexpected error: %s", e)
            return False

    # -- Convenience methods --------------------------------------------------

    def notify_diff(self, diff: PortfolioDiff) -> bool:
        """Send a portfolio diff notification."""
        if not diff.has_changes:
            logger.debug("No changes to notify for %s", diff.author_name)
            return True

        html = _diff_to_html(diff)
        summary = f"【{diff.author_name}】持仓变化: " + _diff_one_liner(diff)
        return self.send(content=html, summary=summary)

    def notify_plan_reminder(self, plan: TradePlan) -> bool:
        """Send a trade plan notification."""
        html = _plan_to_html(plan)
        summary = f"📋 交易计划: {plan.direction.value} {plan.name}"
        return self.send(content=html, summary=summary)

    def notify_text(self, title: str, body: str) -> bool:
        """Send a simple text notification."""
        html = f"<h3>{title}</h3><p>{body}</p>"
        return self.send(content=html, summary=title)


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------


def _diff_to_html(diff: PortfolioDiff) -> str:
    """Format PortfolioDiff as an HTML message."""
    parts: list[str] = []
    parts.append(f"<h3>📊 【{diff.author_name}】持仓变化</h3>")
    parts.append(f"<p>时间: {diff.from_time:%Y-%m-%d} → {diff.to_time:%Y-%m-%d}</p>")

    if diff.added:
        parts.append("<h4 style='color:red'>🔴 新增</h4><ul>")
        for c in diff.added:
            parts.append(f"<li><b>{c.name}</b>（{c.symbol}）权重 {c.new_weight}</li>")
        parts.append("</ul>")

    if diff.removed:
        parts.append("<h4 style='color:green'>🟢 清仓</h4><ul>")
        for c in diff.removed:
            parts.append(f"<li><b>{c.name}</b>（{c.symbol}）原权重 {c.old_weight}</li>")
        parts.append("</ul>")

    if diff.increased:
        parts.append("<h4 style='color:orange'>🔼 加仓</h4><ul>")
        for c in diff.increased:
            parts.append(
                f"<li><b>{c.name}</b>（{c.symbol}）{c.old_weight} → {c.new_weight}</li>"
            )
        parts.append("</ul>")

    if diff.decreased:
        parts.append("<h4 style='color:blue'>🔽 减仓</h4><ul>")
        for c in diff.decreased:
            parts.append(
                f"<li><b>{c.name}</b>（{c.symbol}）{c.old_weight} → {c.new_weight}</li>"
            )
        parts.append("</ul>")

    parts.append("<hr><p style='color:gray;font-size:12px'>Aipha-Q Tracker</p>")
    return "\n".join(parts)


def _diff_one_liner(diff: PortfolioDiff) -> str:
    """One-line summary of a diff for notification bar."""
    items: list[str] = []
    if diff.added:
        items.append(f"+{len(diff.added)}")
    if diff.removed:
        items.append(f"-{len(diff.removed)}")
    if diff.increased:
        items.append(f"↑{len(diff.increased)}")
    if diff.decreased:
        items.append(f"↓{len(diff.decreased)}")
    return " ".join(items) if items else "无变化"


def _plan_to_html(plan: TradePlan) -> str:
    """Format TradePlan as an HTML message."""
    direction_emoji = "🔴 买入" if plan.direction.value == "BUY" else "🟢 卖出"
    parts: list[str] = [
        f"<h3>📋 交易计划: {direction_emoji} {plan.name}</h3>",
        f"<p><b>代码:</b> {plan.symbol}</p>",
        f"<p><b>信心:</b> {plan.confidence.value}</p>",
        f"<p><b>止损价:</b> {plan.stop_loss_price} ({plan.stop_loss_pct:.1%})</p>",
        f"<p><b>目标仓位:</b> {plan.target_position_pct:.1%}</p>",
    ]

    if plan.vip_references:
        parts.append(f"<p><b>参考大V:</b> {', '.join(plan.vip_references)}</p>")

    parts.append("<h4>交易理由:</h4><ol>")
    for reason in plan.reasons:
        parts.append(f"<li>{reason}</li>")
    parts.append("</ol>")

    parts.append(f"<p><b>状态:</b> {plan.status.value}</p>")
    parts.append("<hr><p style='color:gray;font-size:12px'>Aipha-Q Tracker</p>")
    return "\n".join(parts)
