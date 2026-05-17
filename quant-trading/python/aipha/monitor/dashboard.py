"""
监控仪表盘 Web 服务

提供 HTTP API 和 WebSocket 接口：
- GET /api/status - 运行器状态
- GET /api/account - 账户信息
- GET /api/positions - 持仓列表
- GET /api/orders - 订单列表
- GET /api/alerts - 告警列表
- WS /ws/stream - 实时数据流

依赖：
    pip install fastapi uvicorn websockets
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
except ImportError:
    FastAPI = None  # type: ignore

logger = logging.getLogger(__name__)


def create_dashboard_app(runner_provider: callable) -> "FastAPI":
    """创建仪表盘 FastAPI 应用

    Args:
        runner_provider: 返回 LiveRunner 实例的函数

    Returns:
        FastAPI 应用
    """
    if FastAPI is None:
        raise ImportError("请安装 fastapi: pip install fastapi uvicorn")

    app = FastAPI(
        title="Aipha-Q 监控仪表盘",
        description="量化交易系统实时监控",
        version="1.0.0",
    )

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # WebSocket 连接管理
    active_connections: list[WebSocket] = []

    @app.get("/", response_class=HTMLResponse)
    async def root() -> str:
        """仪表盘首页"""
        return get_dashboard_html()

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        """获取运行器状态"""
        runner = runner_provider()
        if runner is None:
            return {"error": "Runner not initialized"}
        return runner.get_state_dict()

    @app.get("/api/account")
    async def get_account() -> dict[str, Any]:
        """获取账户信息"""
        runner = runner_provider()
        if runner is None:
            return {"error": "Runner not initialized"}

        snapshot = runner.get_account_snapshot()
        return {
            "cash": float(snapshot.cash),
            "frozen_cash": float(snapshot.frozen_cash),
            "market_value": float(snapshot.market_value),
            "total_value": float(snapshot.total_value),
            "timestamp": datetime.now().isoformat(),
        }

    @app.get("/api/positions")
    async def get_positions() -> list[dict[str, Any]]:
        """获取持仓列表"""
        runner = runner_provider()
        if runner is None:
            return []

        snapshot = runner.get_account_snapshot()
        positions = []

        for symbol, pos_data in snapshot.positions.items():
            positions.append({
                "symbol": symbol,
                "quantity": pos_data.get("qty", 0),
                "frozen": pos_data.get("frozen", 0),
                "avg_cost": float(pos_data.get("avg_cost", 0)),
                "market_value": float(pos_data.get("market_value", 0)),
                "unrealized_pnl": float(pos_data.get("unrealized_pnl", 0)),
            })

        return positions

    @app.get("/api/orders")
    async def get_orders() -> list[dict[str, Any]]:
        """获取订单列表"""
        runner = runner_provider()
        if runner is None:
            return []

        active_orders = runner.get_active_orders()
        orders = []

        for order in active_orders:
            orders.append({
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.qty,
                "filled_qty": order.filled_qty,
                "order_type": order.order_type,
                "limit_price": order.limit_price,
                "status": order.status.value,
                "created_at": (
                    order.created_at.isoformat() if order.created_at else None
                ),
            })

        return orders

    @app.get("/api/alerts")
    async def get_alerts() -> list[dict[str, Any]]:
        """获取告警列表"""
        runner = runner_provider()
        if runner is None:
            return []

        # 从运行器状态获取错误信息
        state = runner.get_state_dict()
        alerts = []

        for i, error in enumerate(state.get("errors", [])):
            alerts.append({
                "id": f"err_{i}",
                "level": "warning",
                "message": error,
                "timestamp": datetime.now().isoformat(),
            })

        return alerts

    @app.websocket("/ws/stream")
    async def websocket_stream(websocket: WebSocket) -> None:
        """WebSocket 实时数据流"""
        await websocket.accept()
        active_connections.append(websocket)

        try:
            while True:
                runner = runner_provider()
                if runner is not None:
                    state = runner.get_state_dict()
                    await websocket.send_json(state)

                await asyncio.sleep(1)  # 1 秒更新一次

        except WebSocketDisconnect:
            active_connections.remove(websocket)

    return app


def get_dashboard_html() -> str:
    """获取仪表盘 HTML 页面"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aipha-Q 监控仪表盘</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid #333;
        }
        .header h1 { font-size: 24px; color: #4CAF50; }
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #f44336;
        }
        .status-dot.running { background: #4CAF50; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .card {
            background: #1a1a1a;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #333;
        }
        .card h2 {
            font-size: 14px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 16px;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
        }
        .metric-label { color: #888; }
        .metric-value { font-weight: 600; font-size: 18px; }
        .metric-value.positive { color: #4CAF50; }
        .metric-value.negative { color: #f44336; }
        .positions-table {
            width: 100%;
            border-collapse: collapse;
        }
        .positions-table th, .positions-table td {
            text-align: left;
            padding: 8px;
            border-bottom: 1px solid #333;
        }
        .positions-table th {
            color: #888;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Aipha-Q 监控仪表盘</h1>
        <div class="status-indicator">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">连接中...</span>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>账户概览</h2>
            <div class="metric">
                <span class="metric-label">总资产</span>
                <span class="metric-value" id="totalValue">--</span>
            </div>
            <div class="metric">
                <span class="metric-label">可用资金</span>
                <span class="metric-value" id="cash">--</span>
            </div>
            <div class="metric">
                <span class="metric-label">冻结资金</span>
                <span class="metric-value" id="frozenCash">--</span>
            </div>
        </div>

        <div class="card">
            <h2>运行统计</h2>
            <div class="metric">
                <span class="metric-label">策略名称</span>
                <span class="metric-value" id="strategyName">--</span>
            </div>
            <div class="metric">
                <span class="metric-label">已处理 Bar</span>
                <span class="metric-value" id="totalBars">--</span>
            </div>
            <div class="metric">
                <span class="metric-label">订单数</span>
                <span class="metric-value" id="totalOrders">--</span>
            </div>
        </div>

        <div class="card" style="grid-column: span 2;">
            <h2>持仓列表</h2>
            <table class="positions-table">
                <thead>
                    <tr>
                        <th>代码</th>
                        <th>数量</th>
                        <th>成本</th>
                        <th>市值</th>
                        <th>盈亏</th>
                    </tr>
                </thead>
                <tbody id="positionsBody">
                    <tr><td colspan="5">暂无持仓</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function formatNumber(n) {
            if (n === undefined || n === null) return '--';
            return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
        }

        function updateDashboard(data) {
            document.getElementById('statusDot').classList.toggle('running', data.is_running);
            document.getElementById('statusText').textContent = data.is_running ? '运行中' : '已停止';
            document.getElementById('strategyName').textContent = data.strategy_name || '--';
            document.getElementById('totalBars').textContent = formatNumber(data.total_bars);
            document.getElementById('totalOrders').textContent = formatNumber(data.total_orders);

            if (data.account) {
                document.getElementById('totalValue').textContent = '¥' + formatNumber(data.account.total_value);
                document.getElementById('cash').textContent = '¥' + formatNumber(data.account.cash);
                document.getElementById('frozenCash').textContent = '¥' + formatNumber(data.account.frozen_cash);

                const positionsBody = document.getElementById('positionsBody');
                const positions = Object.entries(data.account.positions || {});
                if (positions.length > 0) {
                    positionsBody.innerHTML = positions.map(([symbol, qty]) =>
                        `<tr>
                            <td>${symbol}</td>
                            <td>${qty}</td>
                            <td>--</td>
                            <td>--</td>
                            <td>--</td>
                        </tr>`
                    ).join('');
                } else {
                    positionsBody.innerHTML = '<tr><td colspan="5">暂无持仓</td></tr>';
                }
            }
        }

        // WebSocket 连接
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/stream`);

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        };

        ws.onerror = () => {
            document.getElementById('statusText').textContent = '连接失败';
        };

        ws.onclose = () => {
            document.getElementById('statusText').textContent = '连接断开';
            document.getElementById('statusDot').classList.remove('running');
        };
    </script>
</body>
</html>
"""


def run_dashboard_server(
    runner_provider: callable,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """运行仪表盘服务器

    Args:
        runner_provider: 返回 LiveRunner 实例的函数
        host: 监听地址
        port: 监听端口
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError("请安装 uvicorn: pip install uvicorn")

    app = create_dashboard_app(runner_provider)
    uvicorn.run(app, host=host, port=port)
