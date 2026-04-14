"""dashboard 模块单元测试

覆盖：
- get_dashboard_html 返回有效 HTML
- create_dashboard_app 在无 fastapi 时 raise ImportError
- create_dashboard_app 在有 fastapi 时创建 app
"""

from __future__ import annotations

import pytest


class TestGetDashboardHtml:
    """get_dashboard_html 返回一段完整的 HTML。"""

    def test_returns_html_string(self):
        from aipha.monitor.dashboard import get_dashboard_html

        html = get_dashboard_html()
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html
        assert "Aipha-Q" in html

    def test_contains_key_elements(self):
        from aipha.monitor.dashboard import get_dashboard_html

        html = get_dashboard_html()
        assert "账户概览" in html
        assert "持仓列表" in html
        assert "ws/stream" in html  # WebSocket 端点

    def test_non_empty(self):
        from aipha.monitor.dashboard import get_dashboard_html

        html = get_dashboard_html()
        assert len(html) > 500


class TestCreateDashboardApp:
    """create_dashboard_app 工厂函数。"""

    def test_creates_app_with_fastapi(self):
        """若 fastapi 已安装则返回可用 app。"""
        try:
            import fastapi  # noqa: F401
        except ImportError:
            pytest.skip("fastapi not installed")

        from aipha.monitor.dashboard import create_dashboard_app

        app = create_dashboard_app(runner_provider=lambda: None)
        assert app is not None
        # FastAPI app 应有 routes
        assert len(app.routes) > 0

    def test_app_has_expected_routes(self):
        """app 包含 /api/status 等关键路由。"""
        try:
            import fastapi  # noqa: F401
        except ImportError:
            pytest.skip("fastapi not installed")

        from aipha.monitor.dashboard import create_dashboard_app

        app = create_dashboard_app(runner_provider=lambda: None)
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/" in route_paths
        assert "/api/status" in route_paths
        assert "/api/account" in route_paths
        assert "/api/positions" in route_paths
        assert "/api/orders" in route_paths
        assert "/api/alerts" in route_paths


class TestRunDashboardServer:
    """run_dashboard_server 入口函数。"""

    def test_import_error_without_uvicorn(self, monkeypatch):
        """缺少 uvicorn 时 raise ImportError。"""
        import aipha.monitor.dashboard as dash_mod

        # 暂存原始引用
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def fake_import(name, *args, **kwargs):
            if name == "uvicorn":
                raise ImportError("no uvicorn")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)

        with pytest.raises(ImportError, match="uvicorn"):
            dash_mod.run_dashboard_server(runner_provider=lambda: None)
