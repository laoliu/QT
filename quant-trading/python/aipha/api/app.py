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
        existing = {
            r.get("portfolio_id") if isinstance(r, dict) else r["portfolio_id"]
            for r in store.list_paper_portfolios()
        }
        for combo_id in discover_state_dirs():
            if combo_id not in existing:
                store.upsert_paper_portfolio({
                    "portfolio_id": combo_id,
                    "display_name": combo_id,
                    "config_path": "",
                    "paper_mode": "auto_discovered",
                    "paper_status": "running",
                    "paper_category": "research",
                    "strategies": [],
                    "weights": {},
                    "initial_capital": 1_000_000.0,
                    "state_dir": f"data/paper_state/{combo_id}",
                    "notes": "auto-discovered from state directory",
                })
                logger.info("Auto-discovered paper portfolio: %s", combo_id)

        # 3) Activate portfolios that have real state data on disk
        #    + sync paper_category from registry
        activated = 0
        for row in store.list_paper_portfolios():
            pid = row.get("portfolio_id") if isinstance(row, dict) else row["portfolio_id"]
            status = row.get("paper_status") if isinstance(row, dict) else row.get("paper_status", "")
            updates = []
            params = []
            if status not in ("running",) and has_state_data(pid):
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan: startup / shutdown."""
    _bootstrap_paper_registry(app)
    yield


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
            "http://localhost:3000",
            "http://127.0.0.1:5173",
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
    from aipha.api.factors.router import router as factor_router
    from aipha.api.factors.mining_router import router as mining_router
    from aipha.api.portfolios.router import router as port_router
    from aipha.api.trading.router import router as trade_router
    from aipha.api.system.router import router as sys_router
    from aipha.api.data_hub.router import router as data_router
    from aipha.api.audit.router import router as audit_router

    prefix = "/api/v1"
    app.include_router(auth_router, prefix=prefix)
    app.include_router(strat_router, prefix=prefix)
    app.include_router(bt_list_router, prefix=prefix)
    app.include_router(bt_detail_router, prefix=prefix)
    app.include_router(factor_router, prefix=prefix)
    app.include_router(mining_router, prefix=prefix)
    app.include_router(port_router, prefix=prefix)
    app.include_router(trade_router, prefix=prefix)
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
