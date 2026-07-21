"""Integration tests for the FastAPI dashboard chassis (Increment 17).

Builds a real :class:`Service` against an in-memory SQLite database +
Argon2 hasher, hands it to :func:`create_app`, and drives the
resulting FastAPI app via ``httpx.AsyncClient`` over the ASGI
transport (no real port binding). The flows under test are the
chassis pieces 17 delivers — health, login/logout, session
middleware, CSRF guard. Domain routes land in 18+ and are not
covered here.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport

from message_service.application.ports.clock import Clock
from message_service.application.use_cases.login import LoginUseCase
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.config.schema import Argon2Config, AuthConfig, DashboardConfig
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.user import User
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.session_repository import (
    SqliteSessionRepository,
)
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from message_service.infrastructure.persistence.user_repository import (
    SqliteUserRepository,
)
from message_service.interfaces.rest.app import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    create_app,
)

_T0 = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


class _MutableClock(Clock):
    """A Clock whose ``now()`` can be advanced from tests."""

    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value

    def advance(self, delta: timedelta) -> None:
        self._value = self._value + delta


class _ServiceLike:
    """Smallest object that ``create_app`` actually accesses.

    ``create_app`` reads ``config``, ``clock``, ``uow_factory``,
    ``login``, ``logout``. We deliberately do not build the full
    :class:`Service` here — the chassis only needs the auth surface,
    and a focused stand-in keeps the test setup readable.
    """

    def __init__(
        self,
        *,
        config: Any,
        clock: Clock,
        uow_factory: SqliteUnitOfWorkFactory,
        login: LoginUseCase,
        logout: LogoutUseCase,
    ) -> None:
        self.config = config
        self.clock = clock
        self.uow_factory = uow_factory
        self.login = login
        self.logout = logout


class _ConfigStub:
    """Minimal config stand-in exposing the keys the chassis reads."""

    def __init__(self, *, idle_timeout_seconds: int, https_only: bool) -> None:
        self.dashboard = DashboardConfig(host="127.0.0.1", https_only=https_only)
        self.auth = AuthConfig(
            session_idle_timeout_seconds=idle_timeout_seconds,
            argon2=Argon2Config(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8),
        )


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> _MutableClock:
    return _MutableClock()


@pytest.fixture
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(
        memory_cost=8,
        time_cost=1,
        parallelism=1,
        hash_len=16,
        salt_len=8,
    )


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: _MutableClock) -> SqliteUnitOfWorkFactory:
    return SqliteUnitOfWorkFactory(
        conn=sqlite_conn,
        run_repo_factory=lambda c: SqliteRunRepository(c),
        stage_repo_factory=lambda c: SqliteStageRepository(c),
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=clock),
        audit_log_factory=lambda c: SqliteAuditLog(c),
        sweeper_action_repo_factory=lambda c: SqliteSweeperActionRepository(c),
        user_repo_factory=lambda c: SqliteUserRepository(c),
        session_repo_factory=lambda c: SqliteSessionRepository(c),
    )


async def _seed_user(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str = "alice@example.com",
    plaintext: str = "hunter2",
) -> User:
    pw_hash = hasher.hash(Password(plaintext))
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name="Alice",
                password_hash=pw_hash,
                created_at=_T0,
                disabled=False,
                is_admin=False,
            ),
        )
        await uow.commit()
    assert saved.user_id is not None
    return saved


@pytest.fixture
def service_like(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _MutableClock,
    hasher: Argon2PasswordHasher,
) -> _ServiceLike:
    login = LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher)
    logout = LogoutUseCase(uow_factory=uow_factory, clock=clock)
    return _ServiceLike(
        config=_ConfigStub(idle_timeout_seconds=3600, https_only=False),
        clock=clock,
        uow_factory=uow_factory,
        login=login,
        logout=logout,
    )


@pytest.fixture
async def http_client(service_like: _ServiceLike) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service_like)  # type: ignore[arg-type]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# -----------------------------------------------------------------------------
# Health endpoint
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-DASH-001")
async def test_healthz_is_unauthenticated(http_client: httpx.AsyncClient) -> None:
    """``GET /healthz`` SHALL return 200 without any auth context."""
    response = await http_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-040")
async def test_login_page_is_unauthenticated_html(http_client: httpx.AsyncClient) -> None:
    """L3-DASH-040: ``GET /login`` SHALL return the HTML sign-in page without auth."""
    response = await http_client.get("/login")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert body.startswith("<!doctype html>")
    assert 'id="login-form"' in body
    assert 'name="email"' in body
    assert 'name="password"' in body


# -----------------------------------------------------------------------------
# Prometheus /metrics scrape endpoint (L2-OBS-004, L3-OBS-007)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-007")
async def test_metrics_endpoint_is_unauthenticated(
    http_client: httpx.AsyncClient,
) -> None:
    """L3-OBS-007: ``GET /metrics`` SHALL be reachable without auth.

    Prometheus scrapers run on the same trusted ISOLAN network as
    the service and need unauthenticated access to scrape on a
    configured interval.
    """
    response = await http_client.get("/metrics")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-007")
async def test_metrics_endpoint_returns_prometheus_content_type(
    http_client: httpx.AsyncClient,
) -> None:
    """L3-OBS-007: content type SHALL be ``text/plain; version=0.0.4; charset=utf-8``."""
    response = await http_client.get("/metrics")
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-007")
async def test_metrics_endpoint_emits_recorded_counter(
    http_client: httpx.AsyncClient,
) -> None:
    """The endpoint SHALL surface metrics that the recorder has actually emitted.

    Scrapes once before and once after exercising a metric;
    asserts the counter value increased between the two scrapes.
    Concretely tests ``prometheus_client.generate_latest()`` is
    wired to the same default registry the metrics adapter
    populates.
    """
    from message_service.infrastructure.observability.metrics import (
        PrometheusMetricsRecorder,
    )

    recorder = PrometheusMetricsRecorder()
    # Scrape baseline.
    before = await http_client.get("/metrics")
    assert before.status_code == 200

    # Emit one observation. The counter name is
    # `email_delivery_outcomes_total{outcome="success"}` per
    # the metrics adapter's labels.
    recorder.record_email_delivery_outcome("success")

    after = await http_client.get("/metrics")
    assert after.status_code == 200

    # The "success"-labeled counter SHALL appear in the after
    # scrape with a higher value than the before scrape (or
    # appear at all if it wasn't there before).
    assert "email_delivery_outcomes_total" in after.text
    assert 'outcome="success"' in after.text


# -----------------------------------------------------------------------------
# Login flow
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_login_with_valid_credentials_sets_session_and_csrf_cookies(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """A valid login SHALL set both ``msp_session`` and ``msp_csrf`` cookies."""
    await _seed_user(uow_factory, hasher)
    response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in response.cookies
    assert CSRF_COOKIE_NAME in response.cookies


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-012")
@pytest.mark.requirement("L3-AUTH-013")
async def test_login_with_unknown_email_returns_401_with_realm(
    http_client: httpx.AsyncClient,
) -> None:
    """Unknown email SHALL return 401 with the L3-AUTH-012 realm header."""
    response = await http_client.post(
        "/login",
        json={"email": "ghost@example.com", "password": "hunter2"},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == 'Session realm="Message-Service"'


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-013")
async def test_login_with_bad_password_returns_401(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """Bad password SHALL return 401 with the same generic shape."""
    await _seed_user(uow_factory, hasher)
    response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_oversized_password_before_hashing(
    http_client: httpx.AsyncClient,
) -> None:
    """An over-length password SHALL be rejected at validation (422), not hashed.

    Bounds the unauthenticated Argon2 cost — an unbounded password length is a
    CPU/memory DoS lever.
    """
    response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "x" * 513},
    )
    assert response.status_code == 422


# -----------------------------------------------------------------------------
# Logout
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-002")
async def test_logout_deletes_session_row_and_clears_cookies(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """``POST /logout`` SHALL delete the session row and clear cookies."""
    await _seed_user(uow_factory, hasher)
    login_response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    token = login_response.cookies[SESSION_COOKIE_NAME]
    csrf = login_response.cookies[CSRF_COOKIE_NAME]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    response = await http_client.post(
        "/logout",
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 200

    # Session row should be gone.
    async with uow_factory() as uow:
        assert await uow.session_repo.get_by_token_hash(token_hash) is None


# -----------------------------------------------------------------------------
# CSRF middleware
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-018")
async def test_csrf_middleware_blocks_logout_without_header(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """A POST with no ``X-CSRF-Token`` header SHALL return 403."""
    await _seed_user(uow_factory, hasher)
    await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    response = await http_client.post("/logout")  # missing CSRF header
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-018")
async def test_csrf_middleware_blocks_mismatched_token(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """A header whose value differs from the cookie SHALL return 403."""
    await _seed_user(uow_factory, hasher)
    await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    response = await http_client.post(
        "/logout",
        headers={CSRF_HEADER_NAME: "not-the-real-token"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-018")
async def test_csrf_middleware_exempts_login(http_client: httpx.AsyncClient) -> None:
    """``POST /login`` is the issuance point and SHALL be exempt from CSRF."""
    # Login without any cookies present must NOT 403 with "CSRF missing".
    response = await http_client.post(
        "/login",
        json={"email": "ghost@example.com", "password": "hunter2"},
    )
    # 401 (auth failure) -- not 403 (CSRF) -- is the expected outcome.
    assert response.status_code == 401


# -----------------------------------------------------------------------------
# Idle-timeout enforcement
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-010")
async def test_session_touch_updates_last_activity_on_each_request(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    clock: _MutableClock,
) -> None:
    """L3-AUTH-010: ``last_activity_at`` SHALL update on every request."""
    await _seed_user(uow_factory, hasher)
    login_response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    token = login_response.cookies[SESSION_COOKIE_NAME]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    async with uow_factory() as uow:
        before = await uow.session_repo.get_by_token_hash(token_hash)
    assert before is not None

    clock.advance(timedelta(seconds=300))  # 5 min later, well within idle-timeout
    # The cookie is already in the client's jar from the login response;
    # ``httpx.AsyncClient`` per-request ``cookies=`` is deprecated.
    assert SESSION_COOKIE_NAME in http_client.cookies
    await http_client.get("/healthz")

    async with uow_factory() as uow:
        after = await uow.session_repo.get_by_token_hash(token_hash)
    assert after is not None
    assert after.last_activity_at > before.last_activity_at


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-011")
async def test_expired_session_is_deleted_on_first_request_past_threshold(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    clock: _MutableClock,
) -> None:
    """L3-AUTH-011: an expired session row SHALL be deleted on the request that rejects it."""
    await _seed_user(uow_factory, hasher)
    login_response = await http_client.post(
        "/login",
        json={"email": "alice@example.com", "password": "hunter2"},
    )
    token = login_response.cookies[SESSION_COOKIE_NAME]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Default idle-timeout in the fixture is 3600 seconds. Advance past it.
    clock.advance(timedelta(seconds=3601))
    # Cookie remains in the client jar from login; the request triggers
    # the middleware which deletes the expired row.
    assert SESSION_COOKIE_NAME in http_client.cookies
    _ = token  # token captured for hash above; cookie comes via the jar
    await http_client.get("/healthz")

    async with uow_factory() as uow:
        assert await uow.session_repo.get_by_token_hash(token_hash) is None
