"""Standalone app (mock demo) and an OpenAPI-only app for schema generation.

The standalone app does NOT import or start anything from the Supervisor
(no LiveKit, no camera, no robot services).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from table_tennis.config import Settings, load_settings

from .router import build_router, install_error_handlers
from .runtime import Runtime, build_runtime


def create_app(settings: Optional[Settings] = None, runtime: Optional[Runtime] = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else load_settings())
    rt = runtime or build_runtime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt.start()
        try:
            yield
        finally:
            rt.stop()

    app = FastAPI(
        title="A2 Table Tennis Referee (mock)" if settings.simulated else "A2 Table Tennis Referee",
        version="1.0",
        lifespan=lifespan,
    )
    app.state.tt_runtime = rt
    app.include_router(build_router(lambda: rt))
    install_error_handlers(app)
    return app


def create_openapi_app() -> FastAPI:
    """Router without a runtime: only for ``app.openapi()`` in the generator."""

    def _no_runtime():  # pragma: no cover - never called when generating schema
        raise RuntimeError("schema-only app")

    app = FastAPI(title="A2 Table Tennis Referee API", version="1.0")
    app.include_router(build_router(_no_runtime))
    return app
