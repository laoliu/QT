"""建议格式化器 — 文本/结构化/微信推送格式。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from aipha.advisory.models import Recommendation
from aipha.advisory.operation_advisor import OperationAdvice

ACTION_CN = {
    "strong_buy": "强烈买入",
    "buy": "买入",
    "hold": "持有",
    "reduce": "减仓",
    "sell": "卖出",
    "add": "加仓",
}


def format_text(recs: list[Recommendation]) -> str:
    """纯文本格式化推荐列表。"""
    lines: list[str] = [f"=== 荐股报告 {datetime.now():%Y-%m-%d %H:%M} ===\n"]
    for i, r in enumerate(recs, 1):
        action_cn = ACTION_CN.get(r.action.value if hasattr(r.action, "value") else r.action, r.action)
        lines.append(
            f"{i}. [{action_cn}] {r.ts_code} {r.name} "
            f"置信度={r.confidence_level} "
            f"目标价={r.target_price or '—'}"
        )
        if r.reasons:
            lines.append(f"   理由: {', '.join(r.reasons[:3])}")
    return "\n".join(lines)


def format_operations_text(ops: list[OperationAdvice]) -> str:
    """纯文本格式化操作建议。"""
    lines: list[str] = [f"=== 操作建议 {datetime.now():%Y-%m-%d %H:%M} ===\n"]
    for o in ops:
        action_cn = ACTION_CN.get(o.action, o.action)
        lines.append(
            f"[{action_cn}] {o.ts_code} {o.name} "
            f"仓位={o.quantity_pct:.1%} 紧急={o.urgency}"
        )
    return "\n".join(lines)


def format_json(recs: list[Recommendation]) -> str:
    """JSON 格式化。"""
    data = []
    for r in recs:
        d = {
            "ts_code": r.ts_code,
            "name": r.name,
            "action": r.action.value if hasattr(r.action, "value") else r.action,
            "horizon": r.horizon.value if hasattr(r.horizon, "value") else r.horizon,
            "confidence": r.confidence if isinstance(r.confidence, float) else 0.5,
            "target_price": r.target_price,
            "reasons": r.reasons[:5],
        }
        data.append(d)
    return json.dumps(data, ensure_ascii=False, indent=2)


def format_wechat_markdown(recs: list[Recommendation]) -> str:
    """企业微信 Markdown 格式。"""
    lines: list[str] = [f"### 荐股报告 {datetime.now():%m-%d %H:%M}\n"]
    for r in recs[:10]:
        action_cn = ACTION_CN.get(
            r.action.value if hasattr(r.action, "value") else r.action, "—",
        )
        conf = r.confidence if isinstance(r.confidence, float) else 0.5
        emoji = "🔴" if "buy" in str(r.action) else ("🟢" if "sell" in str(r.action) else "⚪")
        lines.append(
            f"{emoji} **{r.ts_code}** {r.name} — {action_cn} "
            f"(置信={conf:.0%})"
        )
    return "\n".join(lines)
