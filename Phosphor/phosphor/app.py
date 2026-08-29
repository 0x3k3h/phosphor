"""FastAPI application factory. Serves the JSON API, the public tracking
endpoints, and the single-page control panel."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import ROUTERS
from .config import settings
from .database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phosphor")

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"
INDEX_HTML = WEB_DIR / "templates" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Phosphor %s — data dir: %s", __version__, settings.data_dir)

    controllers = []
    scheduler = None
    if app.state.run_services:
        from .smtp.server import start_smtp_servers, stop_smtp_servers
        from .worker import start_worker, stop_worker

        controllers = start_smtp_servers()
        scheduler = start_worker()
        app.state._stop_smtp = stop_smtp_servers
        app.state._stop_worker = stop_worker

    yield

    if app.state.run_services:
        app.state._stop_worker()
        app.state._stop_smtp()
    log.info("Phosphor shut down")


def create_app(run_services: bool = True) -> FastAPI:
    settings.ensure_dirs()
    init_db()

    app = FastAPI(
        title="Phosphor",
        version=__version__,
        description="Self-hosted mail server & cold-outreach engine.",
        lifespan=lifespan,
    )
    app.state.run_services = run_services

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ROUTERS:
        app.include_router(router)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "app": "phosphor",
            "version": __version__,
            "primary_domain": settings.primary_domain,
            "server_hostname": settings.server_hostname,
            "outbound_enabled": settings.outbound_enabled,
        }

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX_HTML)

    # SPA fallback: any unknown non-API path returns the shell.
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "static/", "t/", "u/")):
            from fastapi import HTTPException
            raise HTTPException(404)
        return FileResponse(INDEX_HTML)

    return app


app = create_app()
