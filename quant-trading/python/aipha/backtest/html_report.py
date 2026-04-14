"""回测 HTML 交互式报告生成器"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>回测报告 — {strategy_id}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f8f9fa; }}
.container {{ max-width: 1100px; margin: 0 auto; }}
h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0; }}
.metric {{ background: white; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
.metric .value {{ font-size: 24px; font-weight: bold; color: #2c3e50; }}
.metric .label {{ font-size: 13px; color: #7f8c8d; margin-top: 4px; }}
.positive {{ color: #e74c3c !important; }}
.negative {{ color: #27ae60 !important; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 20px 0; }}
th, td {{ padding: 10px 14px; text-align: right; border-bottom: 1px solid #ecf0f1; }}
th {{ background: #34495e; color: white; font-weight: 500; }}
tr:hover {{ background: #f0f0f0; }}
.chart {{ background: white; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 20px 0; }}
canvas {{ width: 100%; height: 300px; }}
</style>
</head>
<body>
<div class="container">
<h1>📈 回测报告 — {strategy_id}</h1>
<p>区间: {start_date} → {end_date} &nbsp; 初始资金: ¥{initial_capital:,.0f}</p>

<div class="metrics">
  <div class="metric"><div class="value {return_class}">{total_return}%</div><div class="label">总收益率</div></div>
  <div class="metric"><div class="value">{final_value}</div><div class="label">最终净值</div></div>
  <div class="metric"><div class="value">{sharpe_ratio}</div><div class="label">夏普比率</div></div>
  <div class="metric"><div class="value negative">{max_drawdown}%</div><div class="label">最大回撤</div></div>
  <div class="metric"><div class="value">{total_trades}</div><div class="label">总交易次数</div></div>
  <div class="metric"><div class="value">¥{total_fees:,.2f}</div><div class="label">总费用</div></div>
  <div class="metric"><div class="value">{n_days}</div><div class="label">交易日数</div></div>
  <div class="metric"><div class="value">{win_rate}</div><div class="label">胜率</div></div>
</div>

<div class="chart">
  <h3>净值曲线</h3>
  <canvas id="navChart"></canvas>
</div>

<h3>最近交易记录 (最近50笔)</h3>
<table>
<tr><th>日期</th><th>标的</th><th>方向</th><th>数量</th><th>成交价</th><th>费用</th></tr>
{trades_html}
</table>

</div>
<script>
const navData = {nav_json};
const ctx = document.getElementById('navChart').getContext('2d');
const w = ctx.canvas.width = ctx.canvas.parentElement.clientWidth - 32;
const h = ctx.canvas.height = 300;
if (navData.length > 1) {{
  const minNav = Math.min(...navData.map(d => d.nav)) * 0.98;
  const maxNav = Math.max(...navData.map(d => d.nav)) * 1.02;
  const range = maxNav - minNav || 1;
  ctx.strokeStyle = '#3498db'; ctx.lineWidth = 1.5; ctx.beginPath();
  navData.forEach((d, i) => {{
    const x = (i / (navData.length - 1)) * w;
    const y = h - ((d.nav - minNav) / range) * h;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }});
  ctx.stroke();
  ctx.fillStyle = '#7f8c8d'; ctx.font = '11px sans-serif';
  ctx.fillText(navData[0].date, 0, h - 5);
  ctx.fillText(navData[navData.length-1].date, w - 80, h - 5);
  ctx.fillText('¥' + maxNav.toFixed(0), 2, 15);
  ctx.fillText('¥' + minNav.toFixed(0), 2, h - 15);
}}
</script>
</body>
</html>"""


def generate_html_report(result: dict[str, Any], output_path: str | None = None) -> str:
    """从回测结果 dict 生成 HTML 报告。

    Args:
        result: 回测结果字典 (来自 BacktestEngine 或 bridge)
        output_path: 输出文件路径 (None=只返回字符串)

    Returns:
        HTML 字符串
    """
    data = result.get("data", result)

    snapshots = data.get("dailySnapshots", [])
    trades = data.get("trades", [])
    total_return = data.get("totalReturn", 0)

    trades_html = "\n".join(
        f"<tr><td>{t.get('date','')}</td><td>{t.get('symbol','')}</td>"
        f"<td>{'买入' if t.get('side')=='BUY' else '卖出'}</td>"
        f"<td>{t.get('qty',0)}</td><td>{t.get('price',0):.4f}</td>"
        f"<td>{t.get('fees',0):.2f}</td></tr>"
        for t in trades[-50:]
    )

    nav_json = json.dumps(
        [{"date": s.get("date", ""), "nav": s.get("nav", 0)} for s in snapshots],
        ensure_ascii=False
    )

    # 计算胜率 (正收益交易日)
    positive_days = sum(1 for s in snapshots if s.get("dailyReturn", 0) > 0)
    win_rate = f"{positive_days / len(snapshots) * 100:.1f}%" if snapshots else "N/A"

    html = _HTML_TEMPLATE.format(
        strategy_id=data.get("strategyId", "unknown"),
        start_date=data.get("startDate", ""),
        end_date=data.get("endDate", ""),
        initial_capital=data.get("initialCapital", 0),
        total_return=total_return,
        return_class="positive" if total_return > 0 else "negative",
        final_value=f"¥{data.get('finalValue', 0):,.2f}",
        sharpe_ratio=f"{data.get('sharpeRatio', 0):.4f}",
        max_drawdown=f"{data.get('maxDrawdown', 0):.2f}",
        total_trades=data.get("totalTrades", 0),
        total_fees=data.get("totalFees", 0),
        n_days=len(snapshots),
        win_rate=win_rate,
        trades_html=trades_html,
        nav_json=nav_json,
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")

    return html
