"""Unit tests for :class:`LoginUseCase`.

The login flow is exercised against a real SQLite UoW so the
audit-then-state ordering and the L3-AUTH-013 generic-failure surface
are observable in the audit log alongside the session row's
presence/absence.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.use_cases.login import (
    AuthenticationError,
    LoginUseCase,
)
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
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

pytestmark = pytest.mark.allow_io

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> _FixedClock:
    return _FixedClock()


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
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: _FixedClock) -> SqliteUnitOfWorkFactory:
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


@pytest.fixture
def login_uc(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    hasher: Argon2PasswordHasher,
) -> LoginUseCase:
    return LoginUseCase(
        uow_factory=uow_factory,
        clock=clock,
        password_hasher=hasher,
    )


async def _seed_user(
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
    *,
    email: str = "alice@example.com",
    plaintext: str = "hunter2",
    disabled: bool = False,
) -> User:
    """Hash + persist a user; return the saved aggregate."""
    pw_hash = hasher.hash(Password(plaintext))
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@")[0],
                password_hash=pw_hash,
                created_at=_T0,
                disabled=disabled,
            )
        )
    return saved


# -----------------------------------------------------------------------------
# Success
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_login_success_returns_plaintext_token_and_user_id(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    seeded = await _seed_user(uow_factory, hasher)
    result = await login_uc.execute(email="alice@example.com", password=Password("hunter2"))
    assert result.user_id == seeded.user_id
    assert result.plaintext_token  # non-empty


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-006")
async def test_login_success_token_is_high_entropy(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """``secrets.token_urlsafe(32)`` produces 256-bit URL-safe tokens (~43 chars)."""
    await _seed_user(uow_factory, hasher)
    result = await login_uc.execute(email="alice@example.com", password=Password("hunter2"))
    # token_urlsafe(32) emits 43 chars (256 bits, base64url, no padding).
    assert len(result.plaintext_token) >= 40


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-007")
async def test_login_success_persists_sha256_hash_not_plaintext(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """The session row SHALL persist SHA-256(token), never the plaintext."""
    await _seed_user(uow_factory, hasher)
    result = await login_uc.execute(email="alice@example.com", password=Password("hunter2"))
    expected_hash = hashlib.sha256(result.plaintext_token.encode("utf-8")).hexdigest()
    async with uow_factory() as uow:
        session = await uow.session_repo.get_by_token_hash(expected_hash)
    assert session is not None
    assert session.user_id == result.user_id


@pytest.mark.asyncio
@pytest.mark.requirement("L2-OBS-017")
@pytest.mark.requirement("L3-OBS-033")
async def test_login_success_audits_login(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    seeded = await _seed_user(uow_factory, hasher)
    await login_uc.execute(email="alice@example.com", password=Password("hunter2"))
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    assert any(
        e.action == AuditAction.LOGIN
        and e.outcome == AuditOutcome.SUCCESS
        and e.actor == f"user:{seeded.user_id}"
        for e in events
    )


# -----------------------------------------------------------------------------
# Failure variants — all SHALL raise AuthenticationError + audit LOGIN_FAILED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-013")
@pytest.mark.requirement("L3-OBS-034")
async def test_login_unknown_email_raises_and_audits_failure(
    login_uc: LoginUseCase, uow_factory: SqliteUnitOfWorkFactory
) -> None:
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        await login_uc.execute(email="nobody@example.com", password=Password("hunter2"))
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    failures = [e for e in events if e.action == AuditAction.LOGIN_FAILED]
    assert len(failures) == 1
    assert failures[0].outcome == AuditOutcome.FAILURE
    assert failures[0].details["reason"] == "unknown_email"
    assert failures[0].details["email"] == "nobody@example.com"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-013")
@pytest.mark.requirement("L3-OBS-034")
async def test_login_disabled_account_raises_and_audits_failure(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    seeded = await _seed_user(uow_factory, hasher, disabled=True)
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        await login_uc.execute(email="alice@example.com", password=Password("hunter2"))
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    failures = [e for e in events if e.action == AuditAction.LOGIN_FAILED]
    assert len(failures) == 1
    assert failures[0].details["reason"] == "account_disabled"
    assert failures[0].resource == f"user:{seeded.user_id}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-013")
@pytest.mark.requirement("L3-OBS-034")
async def test_login_bad_password_raises_and_audits_failure(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    seeded = await _seed_user(uow_factory, hasher)
    with pytest.raises(AuthenticationError, match="invalid credentials"):
        await login_uc.execute(email="alice@example.com", password=Password("wrong"))
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    failures = [e for e in events if e.action == AuditAction.LOGIN_FAILED]
    assert len(failures) == 1
    assert failures[0].details["reason"] == "bad_password"
    assert failures[0].resource == f"user:{seeded.user_id}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AUTH-013")
async def test_login_failure_does_not_persist_session(
    login_uc: LoginUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    hasher: Argon2PasswordHasher,
) -> None:
    """A failed login SHALL NOT leave any session row behind."""
    await _seed_user(uow_factory, hasher)
    with pytest.raises(AuthenticationError):
        await login_uc.execute(email="alice@example.com", password=Password("wrong"))
    async with uow_factory() as uow, uow._conn.execute("SELECT COUNT(*) FROM sessions") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0
