"""监控面板 — 控制台/HTML 格式化输出"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aipha.monitor.metrics_collector import MetricsCollector


def format_console_dashboard(summary: dict[str, Any]) -> str:
    """格式化控制台面板"""
    nav = summary.get("nav", {})
    risk = summary.get("risk", {})
    trading = summary.get("trading", {})

    lines = [
        "=" * 60,
        f"  策略: {summary.get('strategy_name', '')}",
        f"  时间: {summary.get('timestamp', '')}",
        "-" * 60,
        f"  NAV: {nav.get('current', 0):,.2f}  (起始: {nav.get('initial', 0):,.2f})",
        f"  总收益: {nav.get('total_return_pct', 0):+.2f}%  日收益: {nav.get('daily_return_pct', 0):+.2f}%",
        "-" * 60,
        f"  当前回撤: {risk.get('current_drawdown_pct', 0):.2f}%  最大回撤: {risk.get('max_drawdown_pct', 0):.2f}%",
        f"  滚动Sharpe: {risk.get('rolling_sharpe', 0):.3f}  波动率: {risk.get('rolling_volatility_pct', 0):.2f}%",
        "-" * 60,
        f"  成交: {trading.get('total_fills', 0)}  订单: {trading.get('total_orders', 0)}  Bar: {trading.get('total_bars', 0)}",
        "=" * 60,
    ]
    return "\n".join(lines)


def format_html_dashboard(summary: dict[str, Any]) -> str:
    """生成简单 HTML 面板"""
    nav = summary.get("nav", {})
    risk = summary.get("risk", {})

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>策略监控</title>
<style>body{{font-family:monospace;margin:20px;}}
table{{border-collapse:collapse;}} td,th{{border:1px solid #ccc;padding:6px 12px;}}
.green{{color:green;}} .red{{color:red;}}</style></head>
<body>
<h2>{summary.get('strategy_name', 'Strategy')} 监控面板</h2>
<p>更新时间: {summary.get('timestamp', '')}</p>
<table>
<tr><th>指标</th><th>值</th></tr>
<tr><td>NAV</td><td>{nav.get('current', 0):,.2f}</td></tr>
<tr><td>总收益</td><td class="{'green' if nav.get('total_return_pct', 0) >= 0 else 'red'}">{nav.get('total_return_pct', 0):+.2f}%</td></tr>
<tr><td>最大回撤</td><td class="red">{risk.get('max_drawdown_pct', 0):.2f}%</td></tr>
<tr><td>滚动Sharpe</td><td>{risk.get('rolling_sharpe', 0):.3f}</td></tr>
</table>
</body></html>"""
