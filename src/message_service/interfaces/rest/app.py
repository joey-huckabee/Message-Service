"""FastAPI dashboard application factory and chassis (Increment 17).

The factory :func:`create_app` constructs a :class:`fastapi.FastAPI`
instance from a fully-built :class:`~message_service.bootstrap.Service`.
This increment delivers the chassis only:

* lifespan startup / shutdown handlers (L3-DASH-002),
* session-cookie middleware that authenticates each request, enforces
  the configured idle-timeout, and refreshes ``last_activity_at`` on
  every successful authenticated request
  (L2-AUTH-005, L2-AUTH-006, L3-AUTH-008..L3-AUTH-012),
* a CSRF double-submit guard on POST/PATCH/DELETE/PUT
  (L3-DASH-018),
* an unauthenticated health endpoint at ``GET /healthz``,
* a ``POST /login`` route that delegates to
  :class:`~message_service.application.use_cases.login.LoginUseCase`
  and sets the session cookie on success,
* a ``POST /logout`` route that delegates to
  :class:`~message_service.application.use_cases.logout.LogoutUseCase`
  and clears the cookie.

Domain routes (subscriptions, runs, admin) are deliberately out of
scope for this increment; they land in 18..20.

Requirement references
----------------------
L1-AUTH-001, L1-AUTH-002, L1-DASH-001, L1-DEP-001
L2-AUTH-005, L2-AUTH-006, L2-DASH-001, L2-DASH-002
L3-AUTH-008..L3-AUTH-012
L3-DASH-001, L3-DASH-002, L3-DASH-004, L3-DASH-018
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware

from message_service.application.use_cases.login import AuthenticationError
from message_service.domain.aggregates.password import Password

if TYPE_CHECKING:
    from starlette.responses import Response as StarletteResponse

    from message_service.bootstrap import Service

_log = structlog.get_logger(__name__)

SESSION_COOKIE_NAME = "msp_session"
"""L3-AUTH-008: name of the session cookie."""

CSRF_COOKIE_NAME = "msp_csrf"
"""Companion CSRF cookie for the double-submit guard (L3-DASH-018)."""

CSRF_HEADER_NAME = "X-CSRF-Token"
"""Header that POST/PATCH/DELETE/PUT requests echo for CSRF validation."""

_STATE_CHANGING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

_REALM = 'Session realm="Message-Service"'
"""L3-AUTH-012: the ``WWW-Authenticate`` value on session-auth 401s."""


# -----------------------------------------------------------------------------
# Request models
# -----------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Body of ``POST /login``."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1)


# -----------------------------------------------------------------------------
# Cookie helpers
# -----------------------------------------------------------------------------


def _hash_token(plaintext: str) -> str:
    """L3-AUTH-007: store ``SHA-256(plaintext)`` rather than the token."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _set_session_cookie(response: Response, token: str, *, https_only: bool) -> None:
    """Set the session cookie with the L3-AUTH-008 attributes."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=https_only,
        path="/",
    )


def _set_csrf_cookie(response: Response, token: str, *, https_only: bool) -> None:
    """Set the CSRF cookie. Readable by JS so SPAs can echo it in the header."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # the header echoes this value, so JS must read it
        samesite="lax",
        secure=https_only,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Clear both the session and CSRF cookies (no-args path on logout)."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


# -----------------------------------------------------------------------------
# Middleware: session authentication + idle-timeout enforcement
# -----------------------------------------------------------------------------


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate the request by session cookie; enforce idle-timeout.

    Every request flows through this middleware. The middleware:

    1. Reads the session cookie (if any) and looks up the session row
       by ``SHA-256(token)``.
    2. If the session is missing or its ``last_activity_at`` is older
       than the configured idle-timeout, the cookie is treated as
       invalid: the row (if any) is deleted in the same request that
       rejects authentication (L3-AUTH-011), and the request continues
       *unauthenticated*. Authenticated routes will then surface 401
       via :func:`require_session`.
    3. On a valid session, the middleware updates ``last_activity_at``
       to ``now`` (L3-AUTH-010) and binds ``request.state.user_id`` /
       ``request.state.session_token`` for downstream handlers.

    Public (unauthenticated) endpoints — ``/healthz``, ``/login`` —
    pass through without consulting the session.
    """

    def __init__(self, app: object, *, service: Service) -> None:
        """Bind to the service so we can reach the session repo + clock.

        Args:
            app: The ASGI app the middleware wraps.
            service: The constructed service (carries clock, UoW
                factory, config).
        """
        super().__init__(app)  # type: ignore[arg-type]
        self._service = service
        self._idle_timeout = timedelta(
            seconds=service.config.auth.session_idle_timeout_seconds,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        """Authenticate the request, then dispatch to the next handler."""
        request.state.user_id = None
        request.state.session_token = None

        plaintext_token = request.cookies.get(SESSION_COOKIE_NAME)
        if plaintext_token is None:
            return await call_next(request)

        token_hash = _hash_token(plaintext_token)
        now = self._service.clock.now()

        async with self._service.uow_factory() as uow:
            session = await uow.session_repo.get_by_token_hash(token_hash)
            if session is None:
                # Cookie present but no row — stale or forged. Continue
                # unauthenticated; the response handler can clear the
                # cookie if it cares to.
                return await call_next(request)

            elapsed = now - session.last_activity_at
            if elapsed >= self._idle_timeout:
                # L3-AUTH-011: delete the row in the same request that
                # rejects it. Continue unauthenticated.
                await uow.session_repo.delete_by_token_hash(token_hash)
                await uow.commit()
                _log.info(
                    "session_expired",
                    user_id=session.user_id,
                    idle_seconds=elapsed.total_seconds(),
                )
                return await call_next(request)

            # L3-AUTH-010: refresh activity on every authenticated request.
            await uow.session_repo.touch(token_hash, now)
            await uow.commit()

        request.state.user_id = session.user_id
        request.state.session_token = plaintext_token
        return await call_next(request)


def require_session(request: Request) -> int:
    """FastAPI dependency: 401 if the request is not authenticated.

    Returns the authenticated ``user_id``. Routes that need the user
    declare this dependency; routes that do not (login, health) skip
    it.

    Per L3-AUTH-012 the ``WWW-Authenticate`` header is set.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": _REALM},
        )
    assert isinstance(user_id, int)
    return user_id


def require_admin_factory(service: Service) -> Callable[[Request], Awaitable[int]]:
    """Build a `require_admin` dependency bound to ``service``.

    The dependency layers on top of :func:`require_session`:

    1. Require an authenticated session (401 if absent).
    2. Re-check ``is_admin`` per request by re-reading the user row
       (L3-DASH-021 — no session cache, role changes take effect
       immediately).
    3. 403 if the user is not an administrator (L3-DASH-011).

    The closure captures ``service`` so the dependency has access to
    the UoW factory without going through globals; this matches the
    factory pattern used elsewhere in the rest layer.

    Args:
        service: The composed ``Service`` from
            :func:`message_service.bootstrap.build_service`.

    Returns:
        An async FastAPI dependency that returns the admin user's
        ``user_id``. Routes declare it via ``Depends(require_admin)``
        on the router-builder side (see ``build_templates_router``).
    """

    async def _require_admin(request: Request) -> int:
        user_id = require_session(request)
        async with service.uow_factory() as uow:
            user = await uow.user_repo.get_by_id(user_id)
        if user is None:
            # Session referenced a user that no longer exists. Treat
            # like an expired session: 401 with the realm header.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": _REALM},
            )
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="administrator privilege required",
            )
        return user_id

    return _require_admin


# -----------------------------------------------------------------------------
# Middleware: CSRF double-submit guard
# -----------------------------------------------------------------------------


class CsrfMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF guard on state-changing requests (L3-DASH-018).

    The login flow issues a random CSRF token in a non-HttpOnly cookie
    on successful authentication. Subsequent state-changing requests
    (POST / PATCH / PUT / DELETE) MUST echo the token in the
    ``X-CSRF-Token`` header. Mismatch or absence → HTTP 403.

    The login route itself is exempt — it has no prior cookie context
    and is the issuance point for the CSRF token. Health / GETs are
    inherently safe and are not checked.
    """

    _EXEMPT_PATHS: frozenset[str] = frozenset({"/login"})

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        """Enforce CSRF on state-changing methods outside the exempt set."""
        if request.method in _STATE_CHANGING_METHODS and request.url.path not in self._EXEMPT_PATHS:
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)
            if (
                cookie_token is None
                or header_token is None
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                # ``BaseHTTPMiddleware`` does not translate raised
                # :class:`HTTPException` into responses (FastAPI only
                # does that for route-level handlers), so we return a
                # response directly.
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "CSRF token missing or invalid"},
                )
        return await call_next(request)


# -----------------------------------------------------------------------------
# App factory
# -----------------------------------------------------------------------------


def create_app(service: Service) -> FastAPI:
    """Build the FastAPI dashboard application from a constructed service.

    The factory wires middleware first (in outer-to-inner registration
    order: CSRF → session-auth), then the chassis routes. Domain
    routers attach in subsequent increments via ``app.include_router``.

    Args:
        service: The fully-constructed :class:`Service` from
            :func:`message_service.bootstrap.build_service`.

    Returns:
        A new :class:`FastAPI` instance. No module-level ``app`` global
        is created (L3-DASH-001).
    """

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        """L3-DASH-002: lifespan startup/shutdown.

        Service-wide startup (migrations, scheduler, gRPC server)
        already happened in ``build_service``; the lifespan is a hook
        for FastAPI-only one-time work and runs no-op for now.
        """
        _log.info(
            "rest_app_starting",
            host=service.config.dashboard.host,
            port=service.config.dashboard.port,
        )
        yield
        _log.info("rest_app_stopped")

    app = FastAPI(
        title="Message-Service",
        version="0.1",
        lifespan=_lifespan,
    )

    # Middleware registration: Starlette runs middleware in REVERSE
    # of registration order. We want:
    #     request  ─▶ CsrfMiddleware ─▶ SessionAuthMiddleware ─▶ route
    # so register session FIRST then CSRF (CSRF wraps session, runs
    # outermost). CSRF needs no service state, so it's the simpler
    # outer guard.
    app.add_middleware(SessionAuthMiddleware, service=service)
    app.add_middleware(CsrfMiddleware)

    https_only = service.config.dashboard.https_only

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Unauthenticated liveness probe."""
        return {"status": "ok"}

    # -------------------------------------------------------------------------
    # Prometheus scrape endpoint (L2-OBS-004, L3-OBS-007)
    # -------------------------------------------------------------------------

    @app.get("/metrics")
    async def metrics() -> Response:
        """Unauthenticated Prometheus scrape endpoint.

        Per L3-OBS-007, returns ``prometheus_client.generate_latest()``
        with content type ``text/plain; version=0.0.4; charset=utf-8``
        — the standard Prometheus exposition format the
        ``prometheus_client`` library emits and the format every
        Prometheus server understands.

        The endpoint is deliberately unauthenticated. Per the v1
        ISOLAN deployment model, the dashboard's network is trusted;
        Prometheus scrapers run on the same network and need
        unauthenticated access to scrape on a configured interval.
        Future deployment models (mTLS, internet-exposed) will gate
        this route via the same `require_admin` dependency the
        dashboard's admin routes use — see ROADMAP `R-DASH-004` for
        the path forward.
        """
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            generate_latest,
        )

        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # -------------------------------------------------------------------------
    # Login / Logout
    # -------------------------------------------------------------------------

    @app.post("/login")
    async def login(body: LoginRequest, response: Response) -> dict[str, str]:
        """L1-AUTH-001/L1-AUTH-002: authenticate + mint session.

        On success, sets the session cookie + a fresh CSRF token; on
        failure (any reason) returns the generic 401 per L3-AUTH-013.
        """
        try:
            result = await service.login.execute(
                email=body.email,
                password=Password(body.password),
            )
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": _REALM},
            ) from exc

        _set_session_cookie(response, result.plaintext_token, https_only=https_only)
        _set_csrf_cookie(
            response,
            secrets.token_urlsafe(32),
            https_only=https_only,
        )
        return {"status": "ok"}

    @app.post("/logout")
    async def logout(request: Request, response: Response) -> dict[str, str]:
        """Idempotent logout. CSRF-checked by the middleware.

        Sessions that have already been deleted (concurrent logout from
        another tab) SHALL NOT raise; the use case is itself idempotent.
        """
        token = request.cookies.get(SESSION_COOKIE_NAME)
        user_id = getattr(request.state, "user_id", None)
        if token is not None and user_id is not None:
            await service.logout.execute(plaintext_token=token, user_id=user_id)
        _clear_auth_cookies(response)
        return {"status": "ok"}

    # -------------------------------------------------------------------------
    # Domain routers
    # -------------------------------------------------------------------------

    # Imported here rather than at module top to avoid a circular import:
    # the routes modules import ``require_session`` / ``require_admin_factory``
    # from this module.
    from message_service.interfaces.rest.routes.admin_audit import (
        build_admin_audit_router,
    )
    from message_service.interfaces.rest.routes.admin_users import (
        build_admin_users_router,
    )
    from message_service.interfaces.rest.routes.runs import (
        build_runs_router,
    )
    from message_service.interfaces.rest.routes.subscriptions import (
        build_subscriptions_router,
    )
    from message_service.interfaces.rest.routes.templates import (
        build_templates_router,
    )

    app.include_router(build_subscriptions_router(service))
    app.include_router(build_runs_router(service))
    app.include_router(build_templates_router(service))
    app.include_router(build_admin_users_router(service))
    app.include_router(build_admin_audit_router(service))

    return app


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "SESSION_COOKIE_NAME",
    "create_app",
    "require_admin_factory",
    "require_session",
]
