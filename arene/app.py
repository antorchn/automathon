"""Point d'entrée ASGI de l'application web de l'arène Automathon."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from arene import scheduler
from arene.auth import create_auth_router
from arene.config import Settings
from arene.db import create_db_engine, create_session_factory
from arene.middleware import TrustedProxyMiddleware
from arene.migrations import apply_migrations
from arene.storage import LocalFileStore
from arene.web import STATIC_DIR, UnityStaticFiles, create_web_router
from arene.worker_api import create_worker_router


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    engine = create_db_engine(cfg.database_url)
    apply_migrations(engine)
    session_factory = create_session_factory(engine)
    store = LocalFileStore(cfg.data_dir)

    with session_factory() as session:
        scheduler.seed_reference_agents(session, store, cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    fastapi_app = FastAPI(
        title="Arène Automathon",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    fastapi_app.state.settings = cfg
    fastapi_app.state.engine = engine
    fastapi_app.state.session_factory = session_factory
    fastapi_app.state.store = store

    fastapi_app.include_router(create_auth_router(cfg))
    fastapi_app.include_router(create_worker_router(cfg))
    fastapi_app.include_router(create_web_router(cfg))

    fastapi_app.mount(
        cfg.url("/static"),
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    webgl_dir = Path(cfg.webgl_dir)
    if (webgl_dir / "index.html").is_file():
        fastapi_app.mount(
            cfg.url("/webgl"),
            UnityStaticFiles(directory=str(webgl_dir), html=True),
            name="webgl",
        )
    else:
        @fastapi_app.get(cfg.url("/webgl/index.html"), response_class=HTMLResponse)
        def webgl_missing():
            return HTMLResponse(
                "<p>Build WebGL 1.0.0 absent. Exécuter <code>python scripts/fetch_sources.py --extract</code>.</p>",
                status_code=404,
            )

    if cfg.base_path:
        @fastapi_app.get("/", include_in_schema=False)
        def root_redirect():
            return RedirectResponse(url=cfg.url("/"), status_code=302)

    fastapi_app.add_middleware(TrustedProxyMiddleware, trusted_proxies=cfg.trusted_proxies)
    return fastapi_app


def get_app() -> FastAPI:
    """Factory ASGI sans effet de bord à l'import du module."""
    return create_app()

