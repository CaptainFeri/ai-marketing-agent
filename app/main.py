"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import dispose_engines

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("starting", extra={"version": __version__, "environment": settings.environment})
    yield
    dispose_engines()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.project_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    # The Next.js panel is served from a separate origin. CORS_ALLOWED_ORIGINS
    # (app/core/config.py) lets an operator name where it actually is; unset,
    # local dev still gets its usual localhost:3000 and everything else
    # stays closed rather than guessing at an origin nobody configured.
    default_origins = ["http://localhost:3000"] if settings.environment == "local" else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins or default_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Tag every request so a panel error can be found in the logs."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # Expected, client-safe failures: a 4xx with a stable machine code.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # ``loc`` and ``msg`` only: pydantic also carries the offending value
        # and an exception object in ``input``/``ctx``, and echoing those back
        # would put a submitted password into the response and the logs.
        errors = [
            {"loc": [str(part) for part in error.get("loc", ())], "msg": error.get("msg", "")}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "the request body did not validate",
                    "details": {"errors": errors},
                },
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals to a customer; the request id ties the response
        # to the full traceback in the logs.
        logger.exception(
            "unhandled error",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "internal server error"},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
