"""
FastAPI application entry point for the Unified Enterprise Causal Platform.

Registers:
  - CORS middleware
  - Firebase auth middleware
  - Structured logging with structlog
  - Routes: /api/semantic/, /api/governance/, /api/agents/
  - /health endpoint

Run locally:
    uvicorn src.api.main:app --reload --port 8080

Cloud Run:
    CMD uvicorn src.api.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# .env must be loaded before the modules that read it are imported -- notably the
# auth middleware, which decides on DEV_AUTH_BYPASS and ENV. Import order is
# execution order, so this import states that constraint instead of a loop wedged
# between the imports below (which is what made every one of them an E402).
import src.env_bootstrap  # noqa: F401  # imported for its side effect, first
from src.api import decisions as decisions_router_module
from src.api import governance as governance_router_module
from src.api import incidents as incidents_router_module
from src.api import interventions as interventions_router_module
from src.api import models as models_router_module
from src.api import platform as platform_router_module
from src.api import provenance as provenance_router_module
from src.api import semantic as semantic_router_module
from src.api import session as session_router_module
from src.db.factory import CloudSqlFactory
from src.middleware.auth import FirebaseAuthMiddleware
from src.semantic_layer.verdict import require_signing_key

# ── Logging setup ─────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

log = structlog.get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup / shutdown lifecycle."""
    env = os.getenv("ENV", "local")
    log.info("app.startup", env=env, version=app.version)

    # Outside local development, no signing key means no start: a verdict signed
    # with a per-process key would be refused by every other worker and after a
    # restart. Raising here makes uvicorn exit before it serves a request.
    require_signing_key()

    # Local dev: auto-create SQLite tables via ORM
    if env == "local":
        await CloudSqlFactory.init_db()
        log.info("app.db.local_tables_created")

    yield  # ← Application runs here

    log.info("app.shutdown")
    engine = CloudSqlFactory.get_engine()
    await engine.dispose()


# ── App factory ───────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="Unified Enterprise Causal Platform",
        description=(
            "Governed causal AI platform. "
            "All writes require Guardian APPROVED verdicts. "
            "Audit trail is append-only and immutable."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # ── Firebase auth middleware ───────────────────────────────────────────────
    app.add_middleware(FirebaseAuthMiddleware)

    # ── Request logging middleware ────────────────────────────────────────────
    @app.middleware("http")
    async def request_logging_middleware(
        request: Request, call_next: object
    ) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID", "")
        start = time.perf_counter()

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        log.info("http.request.started")

        # call_next is typed as Any by starlette but is callable
        response = await call_next(request)  # type: ignore[operator]

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info(
            "http.request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response  # type: ignore[return-value]

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(
        semantic_router_module.router,
        prefix="/api/semantic",
        tags=["Semantic Layer"],
    )
    app.include_router(
        governance_router_module.router,
        prefix="/api/governance",
        tags=["Governance"],
    )
    app.include_router(
        session_router_module.router,
        prefix="/api/v1/session",
        tags=["Session Orchestration"],
    )
    app.include_router(
        platform_router_module.router,
        prefix="/api/v1/platform",
        tags=["Platform Ops"],
    )
    app.include_router(
        provenance_router_module.router,
        tags=["Provenance"],
    )
    app.include_router(
        interventions_router_module.router,
        tags=["Interventions"],
    )
    app.include_router(
        incidents_router_module.router,
        tags=["Incidents"],
    )
    app.include_router(
        decisions_router_module.router,
        tags=["Decision Assurance"],
    )
    app.include_router(
        models_router_module.router,
        tags=["Assumption Registry"],
    )

    # ── Health endpoint ───────────────────────────────────────────────────────
    @app.get("/health", tags=["Platform"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "version": app.version,
            "env": os.getenv("ENV", "unknown"),
        }

    # ── Exception handlers ────────────────────────────────────────────────────
    from src.exceptions import (
        AuthenticationError,
        AuthorizationError,
        CircuitBreakerError,
        GuardianBlockedError,
        ObjectNotFoundError,
    )

    @app.exception_handler(GuardianBlockedError)
    async def guardian_blocked_handler(
        request: Request, exc: GuardianBlockedError
    ) -> JSONResponse:
        log.warning(
            "api.guardian_blocked",
            reason=exc.reason,
            verdict_status=exc.verdict_status,
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "guardian_blocked",
                "reason": exc.reason,
                "verdict_status": exc.verdict_status,
                "verdict_id": exc.verdict_id,
            },
        )

    @app.exception_handler(CircuitBreakerError)
    async def circuit_breaker_handler(
        request: Request, exc: CircuitBreakerError
    ) -> JSONResponse:
        log.error(
            "api.circuit_breaker_open",
            cycle_count=exc.cycle_count,
            session_id=exc.session_id,
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": "circuit_breaker_open",
                "cycle_count": exc.cycle_count,
                "message": "Maximum reasoning cycles reached. Escalating to human review.",
            },
        )

    @app.exception_handler(ObjectNotFoundError)
    async def not_found_handler(
        request: Request, exc: ObjectNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": str(exc)},
        )

    @app.exception_handler(AuthorizationError)
    async def authz_handler(
        request: Request, exc: AuthorizationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": str(exc)},
        )

    @app.exception_handler(AuthenticationError)
    async def authn_handler(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": str(exc)},
        )

    return app


# ── Module-level app instance (used by uvicorn) ───────────────────────────────
app = create_app()
