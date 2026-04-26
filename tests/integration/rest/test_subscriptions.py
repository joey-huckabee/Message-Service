"""Integration tests for the subscription CRUD routes (Increment 18).

Builds a real :class:`SubscribeUseCase` / :class:`UnsubscribeUseCase`
against an in-memory SQLite database with the tag vocabulary and
pipeline registry seeded, hands the assembly to the FastAPI app via
``httpx.AsyncClient`` over ``ASGITransport``, and asserts:

* GET / POST / DELETE shape and status codes (L3-DASH-008,
  L3-DASH-019).
* Per-user scoping: GET returns only the session user's rows;
  cross-user DELETE returns 403; non-existent id returns 404
  (L3-DASH-007).
* POST body model rejects ``user_id`` (L3-DASH-009).
* CSRF middleware blocks state-changing requests without the header
  (L3-DASH-018).
* SUBSCRIBE / UNSUBSCRIBE audit records match L3-OBS-031 / L3-OBS-032.
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
from message_service.application.use_cases.subscribe import SubscribeUseCase
from message_service.application.use_cases.unsubscribe import UnsubscribeUseCase
from message_service.config.schema import Argon2Config, AuthConfig, DashboardConfig
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.user import User
from message_service.domain.ids import UserId
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
from message_service.infrastructure.tags.vocabulary_loader import InMemoryTagVocabulary
from message_service.interfaces.rest.app import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    create_app,
)

_T0 = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


class _MutableClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value

    def advance(self, delta: timedelta) -> None:
        self._value = self._value + delta


class _ConfigStub:
    """Minimal config stand-in exposing the keys the chassis + repos read."""

    def __init__(self) -> None:
        self.dashboard = DashboardConfig(host="127.0.0.1", https_only=False)
        self.auth = AuthConfig(
            session_idle_timeout_seconds=3600,
            argon2=Argon2Config(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8),
        )


class _ServiceLike:
    """Smallest object that ``create_app`` plus the subscription router access."""

    def __init__(
        self,
        *,
        config: Any,
        clock: Clock,
        uow_factory: SqliteUnitOfWorkFactory,
        login: LoginUseCase,
        logout: LogoutUseCase,
        subscribe: SubscribeUseCase,
        unsubscribe: UnsubscribeUseCase,
    ) -> None:
        self.config = config
        self.clock = clock
        self.uow_factory = uow_factory
        self.login = login
        self.logout = logout
        self.subscribe = subscribe
        self.unsubscribe = unsubscribe


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
    return Argon2PasswordHasher(memory_cost=8, time_cost=1, parallelism=1, hash_len=16, salt_len=8)


@pytest.fixture
def vocabulary() -> InMemoryTagVocabulary:
    return InMemoryTagVocabulary(frozenset({"production", "nightly"}))


@pytest.fixture
def uow_factory(
    sqlite_conn: aiosqlite.Connection,
    clock: _MutableClock,
) -> SqliteUnitOfWorkFactory:
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
    email: str,
    plaintext: str = "hunter2",
) -> User:
    pw_hash = hasher.hash(Password(plaintext))
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@", 1)[0],
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
    vocabulary: InMemoryTagVocabulary,
) -> _ServiceLike:
    return _ServiceLike(
        config=_ConfigStub(),
        clock=clock,
        uow_factory=uow_factory,
        login=LoginUseCase(uow_factory=uow_factory, clock=clock, password_hasher=hasher),
        logout=LogoutUseCase(uow_factory=uow_factory, clock=clock),
        subscribe=SubscribeUseCase(
            uow_factory=uow_factory,
            clock=clock,
            tag_vocabulary=vocabulary,
            registered_pipelines=frozenset({"etl-nightly"}),
        ),
        unsubscribe=UnsubscribeUseCase(uow_factory=uow_factory, clock=clock),
    )


@pytest.fixture
async def http_client(service_like: _ServiceLike) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(service_like)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


async def _login_as(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str,
) -> tuple[int, str]:
    """Seed the user, log in via the route, return (user_id, csrf_token)."""
    seeded = await _seed_user(uow_factory, hasher, email=email)
    response = await http_client.post(
        "/login",
        json={"email": email, "password": "hunter2"},
    )
    assert response.status_code == 200
    csrf = response.cookies[CSRF_COOKIE_NAME]
    assert seeded.user_id is not None
    return int(seeded.user_id), csrf


# -----------------------------------------------------------------------------
# GET /subscriptions
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-008")
async def test_list_subscriptions_returns_only_session_user_rows(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-007 / L3-DASH-008: listing scopes to the authenticated user."""
    user_a, csrf_a = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    # Seed a subscription owned by another user directly via the repo.
    user_b = await _seed_user(uow_factory, hasher, email="bob@example.com")
    async with uow_factory() as uow:
        await uow.subscription_repo.add(
            user_id=UserId(user_b.user_id),  # type: ignore[arg-type]
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )
        await uow.commit()
    # Alice creates one of her own.
    await http_client.post(
        "/subscriptions",
        json={"granularity": "GLOBAL", "target_value": None},
        headers={CSRF_HEADER_NAME: csrf_a},
    )

    response = await http_client.get("/subscriptions")
    assert response.status_code == 200
    body = response.json()
    # Only Alice's row is returned.
    assert len(body) == 1
    assert body[0]["granularity"] == "GLOBAL"
    _ = user_a  # captured for symmetry with user_b setup above


@pytest.mark.asyncio
async def test_list_subscriptions_requires_session(
    http_client: httpx.AsyncClient,
) -> None:
    """Unauthenticated GET /subscriptions SHALL return 401."""
    response = await http_client.get("/subscriptions")
    assert response.status_code == 401


# -----------------------------------------------------------------------------
# POST /subscriptions
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-008")
@pytest.mark.requirement("L3-OBS-031")
async def test_post_creates_subscription_and_audits(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """POST creates a row and the SUBSCRIBE audit matches L3-OBS-031."""
    user_id, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.post(
        "/subscriptions",
        json={"granularity": "TAG", "target_value": "production"},
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["granularity"] == "TAG"
    assert body["target_value"] == "production"
    new_id = body["subscription_id"]

    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.SUBSCRIBE))
    matching = [e for e in events if e.resource == f"subscription:{new_id}"]
    assert len(matching) == 1
    audit = matching[0]
    assert audit.actor == f"user:{user_id}"
    assert audit.details["granularity"] == "TAG"
    assert audit.details["target_value"] == "production"


@pytest.mark.asyncio
@pytest.mark.requirement("L2-DASH-005")
async def test_post_rejects_extra_user_id_field(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L2-DASH-005: a POST body carrying ``user_id`` SHALL be rejected."""
    _user, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.post(
        "/subscriptions",
        json={"granularity": "GLOBAL", "target_value": None, "user_id": 999},
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_unknown_tag_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """An unknown TAG target SHALL bubble up as 422."""
    _user, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.post(
        "/subscriptions",
        json={"granularity": "TAG", "target_value": "not-a-real-tag"},
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-018")
async def test_post_without_csrf_token_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-018: state-changing POST without CSRF header SHALL 403."""
    await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.post(
        "/subscriptions",
        json={"granularity": "GLOBAL", "target_value": None},
        # no CSRF header
    )
    assert response.status_code == 403


# -----------------------------------------------------------------------------
# DELETE /subscriptions/{id}
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-008")
@pytest.mark.requirement("L3-OBS-032")
async def test_delete_removes_owned_subscription_and_audits(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """DELETE on an owned id SHALL succeed and emit L3-OBS-032 audit."""
    user_id, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    create = await http_client.post(
        "/subscriptions",
        json={"granularity": "GLOBAL", "target_value": None},
        headers={CSRF_HEADER_NAME: csrf},
    )
    sub_id = create.json()["subscription_id"]

    response = await http_client.delete(
        f"/subscriptions/{sub_id}",
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 204

    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.UNSUBSCRIBE))
    matching = [e for e in events if e.resource == f"subscription:{sub_id}"]
    assert len(matching) == 1
    audit = matching[0]
    assert audit.actor == f"user:{user_id}"
    assert audit.details["granularity"] == "GLOBAL"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-007")
async def test_delete_on_other_users_subscription_returns_403(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-007: cross-user DELETE SHALL return 403."""
    # Seed a subscription owned by Bob.
    user_b = await _seed_user(uow_factory, hasher, email="bob@example.com")
    async with uow_factory() as uow:
        b_sub = await uow.subscription_repo.add(
            user_id=UserId(user_b.user_id),  # type: ignore[arg-type]
            granularity=SubscriptionGranularity.GLOBAL,
            target_value=None,
        )
        await uow.commit()

    # Authenticate as Alice and try to delete Bob's subscription.
    _user_a, csrf_a = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.delete(
        f"/subscriptions/{b_sub.subscription_id}",
        headers={CSRF_HEADER_NAME: csrf_a},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_on_unknown_id_returns_404(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """A DELETE for a non-existent id SHALL return 404."""
    _user, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.delete(
        "/subscriptions/999",
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-019")
async def test_delete_with_non_integer_path_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-019: non-integer path values SHALL return 422."""
    _user, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.delete(
        "/subscriptions/not-a-number",
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-019")
async def test_delete_with_zero_path_returns_422(
    http_client: httpx.AsyncClient,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """L3-DASH-019: zero (non-positive integer) SHALL return 422."""
    _user, csrf = await _login_as(http_client, uow_factory, hasher, email="alice@example.com")
    response = await http_client.delete(
        "/subscriptions/0",
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 422


# Hash a token, just to keep the import quiet on platforms that
# don't surface unused-import warnings -- we use it transitively
# through the login flow. Used only for documentation; the test
# above already exercises hashing via the login route.
del hashlib
