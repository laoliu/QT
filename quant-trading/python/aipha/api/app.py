"""FastAPI application factory — assembles all routers.

Production deployment serves React static files from frontend/dist/.
Development: React dev server (Vite) on :5173 proxies /api to FastAPI on :8000.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def _bootstrap_paper_registry(app: FastAPI) -> None:
    """Import paper_registry.json into DB and auto-discover state dirs."""
    try:
        from aipha.api.deps import get_result_store
        from aipha.backtest.result_store import import_paper_registry_from_json
        from aipha.api.trading.paper_state_reader import (
            discover_state_dirs, has_state_data,
        )

        store = get_result_store()

        # 1) Import from paper_registry.json
        registry_path = Path(
            os.environ.get("AIPHA_PAPER_REGISTRY", "configs/paper/paper_registry.json")
        )
        if registry_path.exists():
            count = import_paper_registry_from_json(registry_path, store)
            logger.info("Imported %d entries from paper_registry.json", count)
        else:
            logger.warning("paper_registry.json not found: %s", registry_path)

        # 2) Auto-discover state dirs not in registry
        #    Use active_only=False to include archived combos in the known set,
        #    preventing re-discovery of intentionally archived portfolios.
        existing = {
            r.get("portfolio_id") if isinstance(r, dict) else r["portfolio_id"]
            for r in store.list_paper_portfolios(active_only=False)
        }
        for combo_id in discover_state_dirs():
            if combo_id not in existing:
                store.upsert_paper_portfolio({
                    "portfolio_id": combo_id,
                    "display_name": combo_id,
                    "config_path": "",
                    "paper_mode": "auto_discovered",
                    "paper_status": "registered",
                    "paper_category": "research",
                    "strategies": [],
                    "weights": {},
                    "is_active": False,
                    "initial_capital": 1_000_000.0,
                    "state_dir": f"data/paper_state/{combo_id}",
                    "notes": "auto-discovered from state directory (inactive until promoted)",
                })
                logger.info("Auto-discovered paper portfolio (inactive): %s", combo_id)

        # 3) Activate portfolios that have real state data on disk
        #    + sync paper_category from registry
        #    NOTE: skip archived combos and disabled portfolios
        #    Load disabled set from system_settings.json
        disabled_ids: set = set()
        try:
            settings_path = Path(
                os.environ.get("AIPHA_PAPER_REGISTRY", "configs/paper")
            ).parent / "paper" / "system_settings.json"
            if not settings_path.exists():
                settings_path = Path("configs/paper/system_settings.json")
            if settings_path.exists():
                import json as _json
                _settings = _json.loads(settings_path.read_text(encoding="utf-8"))
                _dp = _settings.get("disabled_portfolios", {})
                disabled_ids = set(_dp.keys()) if isinstance(_dp, dict) else set(_dp)
        except Exception:
            pass

        activated = 0
        for row in store.list_paper_portfolios():
            pid = row.get("portfolio_id") if isinstance(row, dict) else row["portfolio_id"]
            status = row.get("paper_status") if isinstance(row, dict) else row.get("paper_status", "")
            updates = []
            # Skip archived, already running, and disabled portfolios
            if pid in disabled_ids:
                continue
            if status not in ("running", "archived", "s4_active") and has_state_data(pid):
                updates.append("paper_status='running'")
                activated += 1
                logger.info("Activated portfolio %s (state data found on disk)", pid)
            if updates:
                store.conn.execute(
                    f"UPDATE paper_portfolios SET {', '.join(updates)} WHERE portfolio_id=?",
                    (pid,),
                )
        if activated:
            store.conn.commit()
            logger.info("Activated %d portfolios with state data on disk", activated)

        total = len(store.list_paper_portfolios())
        logger.info("Paper portfolios in DB: %d", total)
    except Exception as e:
        logger.error("Failed to bootstrap paper registry: %s", e)


def _sync_backtest_index(app: FastAPI) -> None:
    """Sync filesystem backtest results into SQLite index on startup."""
    try:
        from aipha.backtest.result_store import import_existing_results

        stats = import_existing_results()
        logger.info(
            "Backtest index sync: imported=%d, skipped=%d, wf=%d, sweep=%d",
            stats["imported"], stats["skipped"],
            stats["wf_imported"], stats["sweep_imported"],
        )
    except Exception as e:
        logger.error("Failed to sync backtest index: %s", e)


def _bootstrap_users(app: FastAPI) -> None:
    """Load users from configs/auth/users.yml into DB if users table is empty."""
    try:
        import yaml
        from aipha.api.deps import get_result_store

        auth_path = Path(
            os.environ.get("AIPHA_AUTH_CONFIG", "configs/auth/users.yml")
        )
        if not auth_path.exists():
            logger.warning("Auth config not found: %s, skipping user bootstrap", auth_path)
            return

        store = get_result_store()

        with open(auth_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        users = cfg.get("credentials", {}).get("usernames", {})
        imported = 0
        for username, info in users.items():
            store.upsert_user({
                "username": username,
                "name": info.get("name", username),
                "password_hash": info.get("password", ""),
                "role": info.get("role", "viewer"),
                "email": info.get("email", ""),
                "is_active": True,
            })
            imported += 1
        logger.info("Bootstrapped %d users from %s", imported, auth_path)
    except Exception as e:
        logger.error("Failed to bootstrap users: %s", e)


def _cleanup_zombie_tasks() -> None:
    """Mark any 'running' async tasks as failed after server restart.

    Tasks persisted with status='running' cannot actually be running after
    a process restart — the background threads and subprocesses are gone.
    """
    try:
        from datetime import datetime
        from aipha.api.deps import get_result_store

        store = get_result_store()
        rows = store.get_system_setting("async_tasks", [])
        if not isinstance(rows, list):
            return

        cleaned = 0
        for row in rows:
            if row.get("status") == "running":
                row["status"] = "failed"
                row["error_message"] = "服务重启后任务中断 (orphaned after restart)"
                row["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cleaned += 1

        if cleaned:
            store.set_system_setting("async_tasks", rows, updated_by="system_startup")
            logger.info("Cleaned %d zombie running tasks after restart", cleaned)
    except Exception as e:
        logger.error("Failed to cleanup zombie tasks: %s", e)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan: startup / shutdown."""
    _bootstrap_paper_registry(app)
    _sync_backtest_index(app)
    _bootstrap_users(app)
    _cleanup_zombie_tasks()

    # Pre-import C-extension modules that are NOT thread-safe.
    # highspy (used by cvxpy HiGHS solver) crashes with access violation
    # if first imported in a non-main thread.  Importing it here ensures
    # the shared library is loaded before any background thread needs it.
    try:
        import highspy  # noqa: F401
    except ImportError:
        pass

    # Initialize live engine manager (does NOT auto-start engine;
    # engine is started via POST /api/v1/live/engine/start)
    try:
        from aipha.api.live.engine_manager import get_engine_manager
        _engine_mgr = get_engine_manager()
        logger.info("Live engine manager initialized (engine idle, awaiting start)")
    except Exception as e:
        logger.warning("Live engine manager init skipped: %s", e)
        _engine_mgr = None

    yield

    # Shutdown: stop engine if running
    if _engine_mgr is not None and _engine_mgr.is_running:
        logger.info("Shutting down live engine on API exit")
        _engine_mgr.stop(reason="api_shutdown")


def create_app() -> FastAPI:
    """Create and configure the Aipha-Q API application."""
    app = FastAPI(
        title="Aipha-Q 量化研发平台 API",
        description="一站式量化策略研发、回测、交易管理平台",
        version="0.2.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    # CORS — allow React dev server (development only)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite dev
            "http://localhost:5174",  # Vite dev (alt port)
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Register REST routers ───────────────────────────
    from aipha.api.auth.router import router as auth_router
    from aipha.api.strategies.router import router as strat_router
    from aipha.api.backtests.router import router as bt_list_router
    from aipha.api.backtests.detail_router import router as bt_detail_router
    from aipha.api.backtests.compare_router import router as bt_compare_router
    from aipha.api.factors.router import router as factor_router
    from aipha.api.factors.mining_router import router as mining_router
    from aipha.api.portfolios.router import router as port_router
    from aipha.api.trading.router import router as trade_router
    from aipha.api.system.router import router as sys_router
    from aipha.api.data_hub.router import router as data_router
    from aipha.api.audit.router import router as audit_router

    # aipha.api.live 整个包目前不在树里（engine_manager + router 都缺）。
    # 之前这里是无条件 import，于是 `import aipha.api.app` 直接
    # ModuleNotFoundError —— 模块末尾还有一句 `app = create_app()`，
    # 结果**整个 API 服务连启动都做不到**，其余十几个 router 全被一个
    # 缺失模块拖死。上面 lifespan 里对 engine_manager 用的就是可选导入
    # （`:214`），这里跟它保持一致。
    #
    # 缺的是「HTTP 启停实盘引擎」这条控制面（app.py:213 提到
    # POST /api/v1/live/engine/start）。没有测试规格、也没有调用方
    # （前端的 /quant/api/live/status 由 Node 侧自己实现），凭空补一个
    # 能下真钱的启停接口不合适，留给人决定。
    try:
        from aipha.api.live.router import router as live_router
    except ImportError as e:
        live_router = None
        logger.warning("Live router 不可用，实盘启停接口未注册: %s", e)

    prefix = "/api/v1"
    app.include_router(auth_router, prefix=prefix)
    app.include_router(strat_router, prefix=prefix)
    app.include_router(bt_list_router, prefix=prefix)
    app.include_router(bt_compare_router, prefix=prefix)
    app.include_router(bt_detail_router, prefix=prefix)
    app.include_router(factor_router, prefix=prefix)
    app.include_router(mining_router, prefix=prefix)
    app.include_router(port_router, prefix=prefix)
    app.include_router(trade_router, prefix=prefix)
    if live_router is not None:
        app.include_router(live_router, prefix=prefix)
    app.include_router(sys_router, prefix=prefix)
    app.include_router(data_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)

    # ── Register WebSocket routes ───────────────────────
    from aipha.api.ws.router import router as ws_router

    app.include_router(ws_router)

    # ── Health check ────────────────────────────────────
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.2.0"}

    # ── Serve React static files (production) ───────────
    # In production the React app is pre-built into frontend/dist/
    # StaticFiles serves JS/CSS/images; 404 handler returns index.html for SPA routing
    _dist_dir = Path(
        os.environ.get("AIPHA_FRONTEND_DIST", "frontend/dist")
    )
    if _dist_dir.is_dir():
        # Serve /assets/* (Vite hashed bundles)
        app.mount(
            "/assets",
            StaticFiles(directory=_dist_dir / "assets"),
            name="static-assets",
        )

        _index_html = _dist_dir / "index.html"

        @app.exception_handler(404)
        async def spa_not_found(request: Request, exc: StarletteHTTPException):
            """SPA fallback — serve index.html for non-API 404s."""
            path = request.url.path
            if path.startswith(("/api/", "/ws/")):
                return JSONResponse(
                    {"detail": "Not Found"}, status_code=404
                )
            # Try exact static file first
            rel = path.lstrip("/")
            static_file = _dist_dir / rel
            if rel and static_file.is_file():
                return FileResponse(static_file)
            return FileResponse(_index_html)

    return app


# Module-level app instance for `uvicorn aipha.api.app:app`
app = create_app()
