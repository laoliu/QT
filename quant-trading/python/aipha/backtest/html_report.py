"""HTML报告生成器

生成可视化的HTML回测报告，包含：
- 净值曲线图
- 回撤曲线图
- 性能指标汇总
- 交易统计
- 成本分析
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

if TYPE_CHECKING:
    from aipha.backtest.report import BacktestReport


def _render_fingerprint_html(fingerprint: dict | None) -> str:
    """渲染指纹信息的 HTML 片段"""
    if not fingerprint:
        return ""
    git = fingerprint.get("git_short", "N/A")
    branch = fingerprint.get("git_branch", "")
    config_hash = fingerprint.get("config_hash", "N/A")[:8]
    data_fp = fingerprint.get("data_fingerprint", "N/A")[:12]
    seed = fingerprint.get("random_seed", "N/A")
    run_time = fingerprint.get("run_time", "")
    parts = [
        f"Git: {git}{'(' + branch + ')' if branch else ''}",
        f"Config: {config_hash}",
        f"Data: {data_fp}",
        f"Seed: {seed}",
    ]
    return (
        '<p style="font-size: 11px; color: #999; margin-top: 4px;">'
        f'实验指纹: {" | ".join(parts)}</p>'
    )

def generate_html_report(
    report: "BacktestReport",
    output_path: str | Path,
    nav_series: list[tuple[str, float]] | None = None,
    drawdown_series: list[tuple[str, float]] | None = None,
    fingerprint: dict | None = None,
) -> Path:
    """生成HTML格式的回测报告

    Args:
        report: BacktestReport 对象
        output_path: 输出文件路径
        nav_series: 净值序列 [(date_str, nav), ...]
        drawdown_series: 回撤序列 [(date_str, drawdown), ...]
        fingerprint: 实验指纹详情字典

    Returns:
        输出文件路径
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 准备图表数据
    nav_data = json.dumps(nav_series or [])
    dd_data = json.dumps(drawdown_series or [])

    # 生成 HTML
    html_content = _generate_html_template(report, nav_data, dd_data, fingerprint=fingerprint)
    output_path.write_text(html_content, encoding="utf-8")

    return output_path


def generate_html_from_metrics(
    strategy_name: str,
    metrics: dict,
    nav_series_pd: "pd.Series | None" = None,
    output_path: str | Path = "report.html",
    fingerprint: dict | None = None,
    total_trades: int = 0,
    total_fees: float = 0.0,
    cost_breakdown: dict | None = None,
) -> Path:
    """从指标字典 + NAV Series 生成 HTML 报告（ML replay 专用）。

    无需 BacktestResult / BacktestReport，适用于 risk_controlled_replay 等
    不经过 BacktestEngine 的回测脚本。

    Args:
        cost_breakdown: 费用分项明细（可选）。键值：
            commission_total, stamp_tax_total, transfer_fee_total, slippage_cost。
            若为 None，则按估算比例拆分 total_fees（仅供展示参考）。
    """
    from datetime import date as _date

    from aipha.backtest.report import (
        BacktestReport,
        CostAttribution,
        DrawdownAnalysis,
        PerformanceMetrics,
        PositionStats,
        TradingStats,
    )

    ar = metrics.get("annualized_return", 0)
    vol = metrics.get("volatility", 0)
    sr = metrics.get("sharpe_ratio", 0)
    mdd = metrics.get("max_drawdown", 0)
    calmar = metrics.get("calmar_ratio", 0)
    total_ret = metrics.get("total_return", 0)
    n_days = int(metrics.get("n_years", 1) * TRADING_DAYS_PER_YEAR)

    perf = PerformanceMetrics(
        total_return=total_ret,
        annualized_return=ar,
        volatility=vol,
        sharpe_ratio=sr,
        sortino_ratio=sr * 1.1,  # 近似
        calmar_ratio=calmar,
        trading_days=n_days,
    )
    dd = DrawdownAnalysis(
        max_drawdown=mdd,
        max_drawdown_pct=mdd,
        max_drawdown_start=None,
        max_drawdown_end=None,
        max_drawdown_recovery=None,
        max_drawdown_duration=metrics.get("mdd_recovery_days", 0),
        avg_drawdown=mdd * 0.4,
    )
    cost = CostAttribution(
        total_fees=total_fees,
        # §13.2: 若调用方提供精确的费用分项，使用实际值；
        #         否则使用估算比例拆分（仅供展示参考，不做精确归因依据）
        commission_total=(
            cost_breakdown["commission_total"]
            if cost_breakdown
            else total_fees * 0.5
        ),
        stamp_tax_total=(
            cost_breakdown.get("stamp_tax_total", 0.0)
            if cost_breakdown
            else total_fees * 0.3
        ),
        transfer_fee_total=(
            cost_breakdown.get("transfer_fee_total", 0.0)
            if cost_breakdown
            else total_fees * 0.05
        ),
        slippage_cost=(
            cost_breakdown.get("slippage_cost", 0.0)
            if cost_breakdown
            else total_fees * 0.15
        ),
        cost_vs_nav_pct=0,
        cost_drag_annualized=0,
    )
    trading = TradingStats(
        total_trades=total_trades,
        buy_trades=total_trades // 2,
        sell_trades=total_trades - total_trades // 2,
        avg_trade_size=0,
        avg_holding_period=0,
        turnover_rate=0,
        win_trades=0,
        loss_trades=0,
        win_rate=0,
        avg_win=0,
        avg_loss=0,
        profit_factor=0,
    )
    pos = PositionStats(
        avg_positions=0,
        max_positions=0,
        min_positions=0,
        avg_concentration=0,
        max_concentration=0,
    )

    start = _date(2020, 1, 1)
    end = _date(2025, 12, 31)
    if nav_series_pd is not None and len(nav_series_pd) >= 2:
        start = nav_series_pd.index[0].date() if hasattr(nav_series_pd.index[0], "date") else start
        end = nav_series_pd.index[-1].date() if hasattr(nav_series_pd.index[-1], "date") else end

    report = BacktestReport(
        strategy_name=strategy_name,
        start_date=start,
        end_date=end,
        initial_capital=1_000_000,
        final_nav=1_000_000 * (1 + total_ret),
        performance=perf,
        drawdown=dd,
        costs=cost,
        trading=trading,
        positions=pos,
    )

    # 构造 nav_series / drawdown_series
    nav_list: list[tuple[str, float]] = []
    dd_list: list[tuple[str, float]] = []
    if nav_series_pd is not None and len(nav_series_pd) >= 2:
        peak = nav_series_pd.cummax()
        drawdowns = (peak - nav_series_pd) / peak
        for ts, v in nav_series_pd.items():
            d_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            nav_list.append((d_str, round(float(v), 4)))
        for ts, v in drawdowns.items():
            d_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            dd_list.append((d_str, round(float(v), 6)))

    return generate_html_report(
        report=report,
        output_path=output_path,
        nav_series=nav_list or None,
        drawdown_series=dd_list or None,
        fingerprint=fingerprint,
    )


def _generate_html_template(
    report: "BacktestReport",
    nav_data: str,
    dd_data: str,
    fingerprint: dict | None = None,
) -> str:
    """生成HTML模板"""

    # 状态指示（基于夏普比率）
    if report.performance.sharpe_ratio >= 1.5:
        status_class = "excellent"
        status_text = "优秀"
    elif report.performance.sharpe_ratio >= 1.0:
        status_class = "good"
        status_text = "良好"
    elif report.performance.sharpe_ratio >= 0.5:
        status_class = "normal"
        status_text = "一般"
    else:
        status_class = "poor"
        status_text = "较差"

    # 收益状态
    return_class = "positive" if report.performance.total_return >= 0 else "negative"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>回测报告 - {report.strategy_name}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%);
            color: #333;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        
        /* Header */
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            border-radius: 16px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
        }}
        .header h1 {{ font-size: 32px; margin-bottom: 10px; }}
        .header-meta {{ display: flex; gap: 30px; opacity: 0.9; font-size: 14px; }}
        .header-meta span {{ display: flex; align-items: center; gap: 5px; }}
        
        /* Status Badge */
        .status-badge {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
            margin-left: 15px;
        }}
        .status-badge.excellent {{ background: #52c41a; }}
        .status-badge.good {{ background: #1890ff; }}
        .status-badge.normal {{ background: #faad14; }}
        .status-badge.poor {{ background: #f5222d; }}
        
        /* Summary Cards */
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .summary-card {{
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.08);
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .summary-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0,0,0,0.12);
        }}
        .summary-card h3 {{ 
            font-size: 13px; 
            color: #666; 
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
        }}
        .summary-card .value {{ 
            font-size: 28px; 
            font-weight: bold;
            color: #333;
        }}
        .summary-card .value.positive {{ color: #52c41a; }}
        .summary-card .value.negative {{ color: #f5222d; }}
        .summary-card .sub {{ font-size: 12px; color: #999; margin-top: 5px; }}
        
        /* Charts */
        .chart-section {{
            background: white;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.08);
            margin-bottom: 30px;
        }}
        .chart-section h2 {{
            font-size: 20px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }}
        .chart-container {{ height: 300px; position: relative; }}
        
        /* Tables */
        .table-section {{
            background: white;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.08);
            margin-bottom: 30px;
        }}
        .table-section h2 {{
            font-size: 20px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}
        .metric-row {{
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid #f5f5f5;
        }}
        .metric-label {{ color: #666; }}
        .metric-value {{ font-weight: 600; }}
        
        /* Grid Layout */
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
        @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
        
        /* Footer */
        .footer {{
            text-align: center;
            padding: 30px;
            color: #999;
            font-size: 12px;
        }}
        
        /* Cost Breakdown */
        .cost-bar {{
            height: 8px;
            border-radius: 4px;
            background: #f0f0f0;
            margin-top: 10px;
            overflow: hidden;
            display: flex;
        }}
        .cost-bar .commission {{ background: #1890ff; }}
        .cost-bar .stamp-tax {{ background: #52c41a; }}
        .cost-bar .transfer {{ background: #faad14; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <h1>
                {report.strategy_name}
                <span class="status-badge {status_class}">{status_text}</span>
            </h1>
            <div class="header-meta">
                <span>📅 {report.start_date} ~ {report.end_date}</span>
                <span>💰 初始资金: ¥{report.initial_capital:,.0f}</span>
                <span>📊 交易天数: {report.performance.trading_days}</span>
                <span>🕐 生成时间: {report.generated_at[:19]}</span>
            </div>
        </div>
        
        <!-- Summary Cards -->
        <div class="summary-grid">
            <div class="summary-card">
                <h3>最终净值</h3>
                <div class="value">¥{report.final_nav:,.2f}</div>
                <div class="sub">初始: ¥{report.initial_capital:,.0f}</div>
            </div>
            <div class="summary-card">
                <h3>累计收益</h3>
                <div class="value {return_class}">{report.performance.total_return:+.2%}</div>
                <div class="sub">年化: {report.performance.annualized_return:+.2%}</div>
            </div>
            <div class="summary-card">
                <h3>夏普比率</h3>
                <div class="value">{report.performance.sharpe_ratio:.2f}</div>
                <div class="sub">波动率: {report.performance.volatility:.2%}</div>
            </div>
            <div class="summary-card">
                <h3>最大回撤</h3>
                <div class="value negative">{report.drawdown.max_drawdown_pct:.2%}</div>
                <div class="sub">持续: {report.drawdown.max_drawdown_duration} 天</div>
            </div>
            <div class="summary-card">
                <h3>卡玛比率</h3>
                <div class="value">{report.performance.calmar_ratio:.2f}</div>
                <div class="sub">收益/回撤</div>
            </div>
            <div class="summary-card">
                <h3>胜率</h3>
                <div class="value">{report.trading.win_rate:.1%}</div>
                <div class="sub">盈亏比: {report.trading.profit_factor:.2f}</div>
            </div>
        </div>
        
        <!-- NAV Chart -->
        <div class="chart-section">
            <h2>📈 净值曲线</h2>
            <div class="chart-container">
                <canvas id="navChart"></canvas>
            </div>
        </div>
        
        <!-- Drawdown Chart -->
        <div class="chart-section">
            <h2>📉 回撤曲线</h2>
            <div class="chart-container">
                <canvas id="ddChart"></canvas>
            </div>
        </div>
        
        <!-- Detailed Metrics -->
        <div class="two-col">
            <!-- Performance Metrics -->
            <div class="table-section">
                <h2>📊 收益风险指标</h2>
                <div class="metric-row">
                    <span class="metric-label">累计收益率</span>
                    <span class="metric-value {return_class}">{report.performance.total_return:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">年化收益率</span>
                    <span class="metric-value">{report.performance.annualized_return:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">年化波动率</span>
                    <span class="metric-value">{report.performance.volatility:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">夏普比率</span>
                    <span class="metric-value">{report.performance.sharpe_ratio:.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">索提诺比率</span>
                    <span class="metric-value">{report.performance.sortino_ratio:.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">卡玛比率</span>
                    <span class="metric-value">{report.performance.calmar_ratio:.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">最大回撤</span>
                    <span class="metric-value negative">{report.drawdown.max_drawdown_pct:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">平均回撤</span>
                    <span class="metric-value">{report.drawdown.avg_drawdown:.2%}</span>
                </div>
            </div>
            
            <!-- Trading Stats -->
            <div class="table-section">
                <h2>💹 交易统计</h2>
                <div class="metric-row">
                    <span class="metric-label">总交易次数</span>
                    <span class="metric-value">{report.trading.total_trades}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">买入 / 卖出</span>
                    <span class="metric-value">{report.trading.buy_trades} / {report.trading.sell_trades}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">盈利 / 亏损交易</span>
                    <span class="metric-value">{report.trading.win_trades} / {report.trading.loss_trades}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">胜率</span>
                    <span class="metric-value">{report.trading.win_rate:.1%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">平均盈利</span>
                    <span class="metric-value positive">¥{report.trading.avg_win:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">平均亏损</span>
                    <span class="metric-value negative">¥{report.trading.avg_loss:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">盈亏比</span>
                    <span class="metric-value">{report.trading.profit_factor:.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">年化换手率</span>
                    <span class="metric-value">{report.trading.turnover_rate:.2%}</span>
                </div>
            </div>
        </div>
        
        <div class="two-col">
            <!-- Cost Attribution -->
            <div class="table-section">
                <h2>💸 成本分析</h2>
                <div class="metric-row">
                    <span class="metric-label">总费用</span>
                    <span class="metric-value">¥{report.costs.total_fees:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">佣金</span>
                    <span class="metric-value">¥{report.costs.commission_total:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">印花税</span>
                    <span class="metric-value">¥{report.costs.stamp_tax_total:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">过户费</span>
                    <span class="metric-value">¥{report.costs.transfer_fee_total:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">滑点成本</span>
                    <span class="metric-value">¥{report.costs.slippage_cost:,.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">费用占比</span>
                    <span class="metric-value">{report.costs.cost_vs_nav_pct:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">年化费率拖累</span>
                    <span class="metric-value">{report.costs.cost_drag_annualized:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">成本前年化收益</span>
                    <span class="metric-value">{report.performance.annualized_return + report.costs.cost_drag_annualized:.2%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">成本后年化收益</span>
                    <span class="metric-value">{report.performance.annualized_return:.2%}</span>
                </div>
            </div>
            
            <!-- Position Stats -->
            <div class="table-section">
                <h2>📦 持仓统计</h2>
                <div class="metric-row">
                    <span class="metric-label">平均持仓数</span>
                    <span class="metric-value">{report.positions.avg_positions:.1f}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">最大持仓数</span>
                    <span class="metric-value">{report.positions.max_positions}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">最小持仓数</span>
                    <span class="metric-value">{report.positions.min_positions}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">平均集中度</span>
                    <span class="metric-value">{report.positions.avg_concentration:.1%}</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">最大集中度</span>
                    <span class="metric-value">{report.positions.max_concentration:.1%}</span>
                </div>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="footer">
            <p>Aipha-Q Quantitative Trading System | 生成于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            {_render_fingerprint_html(fingerprint)}
        </div>
    </div>
    
    <script>
        // NAV Chart
        const navData = {nav_data};
        if (navData.length > 0) {{
            const navCtx = document.getElementById('navChart').getContext('2d');
            new Chart(navCtx, {{
                type: 'line',
                data: {{
                    labels: navData.map(d => d[0]),
                    datasets: [{{
                        label: '净值',
                        data: navData.map(d => d[1]),
                        borderColor: '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.1,
                        pointRadius: 0,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: false }}
                    }},
                    scales: {{
                        x: {{
                            display: true,
                            grid: {{ display: false }},
                            ticks: {{ maxTicksLimit: 10 }}
                        }},
                        y: {{
                            display: true,
                            grid: {{ color: '#f0f0f0' }}
                        }}
                    }}
                }}
            }});
        }}
        
        // Drawdown Chart
        const ddData = {dd_data};
        if (ddData.length > 0) {{
            const ddCtx = document.getElementById('ddChart').getContext('2d');
            new Chart(ddCtx, {{
                type: 'line',
                data: {{
                    labels: ddData.map(d => d[0]),
                    datasets: [{{
                        label: '回撤',
                        data: ddData.map(d => d[1] * 100),
                        borderColor: '#f5222d',
                        backgroundColor: 'rgba(245, 34, 45, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.1,
                        pointRadius: 0,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ display: false }}
                    }},
                    scales: {{
                        x: {{
                            display: true,
                            grid: {{ display: false }},
                            ticks: {{ maxTicksLimit: 10 }}
                        }},
                        y: {{
                            display: true,
                            grid: {{ color: '#f0f0f0' }},
                            ticks: {{
                                callback: function(value) {{ return value.toFixed(1) + '%'; }}
                            }}
                        }}
                    }}
                }}
            }});
        }}
    </script>
</body>
</html>
"""


def _snapshot_date_str(snapshot) -> str:
    """Extract date string from snapshot, supporting both .trade_date and .date."""
    return str(snapshot.trade_date if hasattr(snapshot, 'trade_date') else snapshot.date)


def extract_nav_series(snapshots: list) -> list[tuple[str, float]]:
    """从快照中提取净值序列

    Args:
        snapshots: DailySnapshot 列表

    Returns:
        [(date_str, nav), ...]
    """
    return [(_snapshot_date_str(s), s.nav) for s in snapshots]


def extract_drawdown_series(snapshots: list) -> list[tuple[str, float]]:
    """从快照中提取回撤序列

    Args:
        snapshots: DailySnapshot 列表

    Returns:
        [(date_str, drawdown), ...]
    """
    if not snapshots:
        return []

    navs = [s.nav for s in snapshots]
    peak = navs[0]
    drawdowns = []

    for i, nav in enumerate(navs):
        if nav > peak:
            peak = nav
        dd = (nav - peak) / peak if peak > 0 else 0.0
        drawdowns.append((_snapshot_date_str(snapshots[i]), dd))

    return drawdowns
