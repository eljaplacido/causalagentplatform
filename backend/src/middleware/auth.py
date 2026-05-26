"""
Firebase JWT authentication middleware for FastAPI.

Pattern from projectcarfcynepic / carfdevops (CYBERSECURITY.md §3.1):
  - Lazy Firebase SDK initialisation (avoids startup failures in CI/CD or
    restricted environments where service account credentials are not present)
  - Extracts Bearer token from Authorization header
  - Attaches decoded user dict to request.state.user
  - Returns 401 on invalid/missing tokens for protected routes
  - Unprotected routes (e.g. /health) are excluded via PUBLIC_PATHS

Usage in FastAPI:
    app.add_middleware(BaseHTTPMiddleware, dispatch=firebase_jwt_middleware)

Or as a dependency on individual routes:
    async def require_auth(request: Request) -> dict:
        return await verify_request_user(request)
"""

from __future__ import annotations

import os
from typing import Any, Callable

import structlog
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

log = structlog.get_logger(__name__)

# Paths that never require authentication
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
})

# ── Local-dev auth bypass ─────────────────────────────────────────────────────
# Active only when ENV=local AND DEV_AUTH_BYPASS=true.
# The bypass token is a fixed sentinel; it is never a real Firebase JWT and
# cannot work in any other environment configuration.
_DEV_BYPASS_TOKEN = "dev-local-token"
_DEV_MOCK_USER: dict[str, object] = {
    "uid": "dev-user-001",
    "email": "dev@causalplatform.local",
    "name": "Dev User",
    "roles": ["governance_officer", "platform_engineer", "analyst", "sre"],
}

# ── Lazy Firebase initialisation ──────────────────────────────────────────────

_firebase_app: Any = None
_firebase_init_attempted: bool = False


def _get_firebase_app() -> Any:
    """
    Lazily initialise the Firebase Admin SDK.

    Raises RuntimeError if initialisation fails (e.g. missing credentials).
    In CI or test environments, set FIREBASE_EMULATOR_HOST and
    FIREBASE_AUTH_EMULATOR_HOST to use the local emulator.

    Credentials are read from GOOGLE_APPLICATION_CREDENTIALS env var
    or the default GCP ADC chain — never from hard-coded values.
    """
    global _firebase_app, _firebase_init_attempted  # noqa: PLW0603

    if _firebase_app is not None:
        return _firebase_app

    if _firebase_init_attempted:
        raise RuntimeError(
            "Firebase SDK initialisation previously failed. "
            "Check GOOGLE_APPLICATION_CREDENTIALS or ADC configuration."
        )

    _firebase_init_attempted = True

    try:
        import firebase_admin
        from firebase_admin import credentials, auth as firebase_auth  # noqa: F401

        if not firebase_admin._apps:  # type: ignore[attr-defined]
            # Try explicit credentials file first
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if cred_path:
                cred = credentials.Certificate(cred_path)
                _firebase_app = firebase_admin.initialize_app(cred)
            else:
                # Fall back to Application Default Credentials (Cloud Run, GKE)
                _firebase_app = firebase_admin.initialize_app()
        else:
            _firebase_app = firebase_admin.get_app()

        log.info("firebase.sdk.initialized")
    except Exception as exc:
        _firebase_init_attempted = True
        log.error("firebase.sdk.init_failed", error=str(exc))
        raise RuntimeError(f"Firebase SDK init failed: {exc}") from exc

    return _firebase_app


async def _verify_firebase_token(token: str) -> dict[str, Any]:
    """
    Verify a Firebase ID token and return the decoded claims dict.

    Raises:
        RuntimeError: If Firebase SDK is not available.
        firebase_admin.auth.InvalidIdTokenError: If token is invalid.
        firebase_admin.auth.ExpiredIdTokenError: If token is expired.
    """
    from firebase_admin import auth as firebase_auth

    _get_firebase_app()  # Ensure SDK is initialised
    # check_revoked=True adds a network call to verify token revocation
    decoded = firebase_auth.verify_id_token(token, check_revoked=True)
    return dict(decoded)


def _extract_bearer_token(request: Request) -> str | None:
    """Extract the Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):].strip()
        return token if token else None
    return None


# ── Middleware class ──────────────────────────────────────────────────────────


class FirebaseAuthMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that validates Firebase JWT tokens.

    For protected routes (not in PUBLIC_PATHS):
      - Missing token → 401
      - Invalid/expired token → 401
      - Valid token → request.state.user = decoded claims dict

    For public routes:
      - request.state.user is set to None
      - Request proceeds without auth
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        # CORS preflight (OPTIONS) never requires auth
        if request.method == "OPTIONS":
            request.state.user = None
            return await call_next(request)

        # Always allow public paths
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PATHS):
            request.state.user = None
            return await call_next(request)

        token = _extract_bearer_token(request)

        # Local-dev bypass — never runs unless both env vars are explicitly set
        # Strip whitespace to handle Windows CRLF-corrupted env vars ("true\r")
        if (
            os.getenv("ENV", "").strip() == "local"
            and os.getenv("DEV_AUTH_BYPASS", "").strip() == "true"
            and token == _DEV_BYPASS_TOKEN
        ):
            request.state.user = _DEV_MOCK_USER
            log.debug("auth.dev_bypass", path=path)
            return await call_next(request)

        if not token:
            log.warning(
                "auth.missing_token",
                path=path,
                method=request.method,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required: Bearer token missing."},
            )

        try:
            decoded_user = await _verify_firebase_token(token)
            request.state.user = decoded_user
            log.debug(
                "auth.token_verified",
                uid=decoded_user.get("uid"),
                email=decoded_user.get("email"),
                path=path,
            )
        except Exception as exc:
            log.warning(
                "auth.token_invalid",
                path=path,
                error=str(exc),
            )
            return JSONResponse(
                status_code=401,
                content={"detail": f"Authentication failed: {exc}"},
            )

        return await call_next(request)


# ── Route-level dependency ────────────────────────────────────────────────────


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency: return the authenticated user or raise 401.

    Usage::

        @router.get("/protected")
        async def my_route(user: dict = Depends(get_current_user)):
            uid = user["uid"]
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user  # type: ignore[return-value]


def require_role(*roles: str) -> Callable[[Request], dict[str, Any]]:
    """
    FastAPI dependency factory: assert the authenticated user has one of the
    specified roles.

    Custom claims are expected in the Firebase token under the key "roles"
    (a list of strings) or "role" (a single string).

    Usage::

        @router.post("/approve/{id}")
        async def approve(
            id: UUID,
            user: dict = Depends(require_role("governance_officer", "platform_engineer")),
        ):
            ...
    """
    async def _checker(request: Request) -> dict[str, Any]:
        user = await get_current_user(request)
        user_roles: list[str] = []

        claims = user.get("roles") or []
        if isinstance(claims, list):
            user_roles = claims
        elif isinstance(claims, str):
            user_roles = [claims]

        # Also check single "role" claim
        single_role = user.get("role")
        if single_role and isinstance(single_role, str):
            user_roles.append(single_role)

        if not any(r in user_roles for r in roles):
            log.warning(
                "auth.insufficient_role",
                uid=user.get("uid"),
                required_roles=list(roles),
                user_roles=user_roles,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Insufficient permissions. Required: {list(roles)}. "
                    f"User has: {user_roles or ['none']}."
                ),
            )
        return user

    return _checker
