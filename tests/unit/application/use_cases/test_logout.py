"""Unit tests for :class:`LogoutUseCase`.

Logout hashes the inbound cookie, deletes the matching session row,
and audits LOGOUT. The delete is idempotent — a concurrent logout
from another tab SHALL NOT raise.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.session import Session
from message_service.domain.aggregates.user import User
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
def logout_uc(uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock) -> LogoutUseCase:
    return LogoutUseCase(uow_factory=uow_factory, clock=clock)


async def _seed_session(
    uow_factory: SqliteUnitOfWorkFactory,
    *,
    plaintext_token: str = "test-cookie-value",
) -> tuple[int, str]:
    """Seed a user + a session whose token_hash matches ``plaintext_token``.

    Returns the user_id and the SHA-256 token_hash for assertions.
    """
    token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email="alice@example.com",
                display_name="Alice",
                password_hash="h",
                created_at=_T0,
            )
        )
        assert saved.user_id is not None
        await uow.session_repo.save(
            Session(
                token_hash=token_hash,
                user_id=saved.user_id,
                created_at=_T0,
                last_activity_at=_T0,
            )
        )
    return saved.user_id, token_hash


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AUTH-004")
async def test_logout_deletes_session_row(
    logout_uc: LogoutUseCase, uow_factory: SqliteUnitOfWorkFactory
) -> None:
    user_id, token_hash = await _seed_session(uow_factory)
    await logout_uc.execute(plaintext_token="test-cookie-value", user_id=user_id)
    async with uow_factory() as uow:
        assert await uow.session_repo.get_by_token_hash(token_hash) is None


@pytest.mark.asyncio
@pytest.mark.requirement("L2-OBS-017")
@pytest.mark.requirement("L3-OBS-033")
async def test_logout_audits_logout(
    logout_uc: LogoutUseCase, uow_factory: SqliteUnitOfWorkFactory
) -> None:
    user_id, _ = await _seed_session(uow_factory)
    await logout_uc.execute(plaintext_token="test-cookie-value", user_id=user_id)
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    logouts = [e for e in events if e.action == AuditAction.LOGOUT]
    assert len(logouts) == 1
    assert logouts[0].outcome == AuditOutcome.SUCCESS
    assert logouts[0].actor == f"user:{user_id}"


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AUTH-003")
@pytest.mark.requirement("L3-OBS-036")
async def test_logout_audit_does_not_contain_plaintext_token(
    logout_uc: LogoutUseCase, uow_factory: SqliteUnitOfWorkFactory
) -> None:
    """L2-AUTH-003: plaintext token SHALL NOT appear in any audit detail."""
    user_id, _ = await _seed_session(uow_factory)
    await logout_uc.execute(plaintext_token="test-cookie-value", user_id=user_id)
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query())
    for ev in events:
        for value in ev.details.values():
            assert "test-cookie-value" not in str(value)


@pytest.mark.asyncio
async def test_logout_unknown_token_is_noop(
    logout_uc: LogoutUseCase, uow_factory: SqliteUnitOfWorkFactory
) -> None:
    """Concurrent logout from another tab SHALL NOT raise."""
    user_id, _ = await _seed_session(uow_factory)
    # First logout removes the row.
    await logout_uc.execute(plaintext_token="test-cookie-value", user_id=user_id)
    # Second logout SHALL succeed silently.
    await logout_uc.execute(plaintext_token="test-cookie-value", user_id=user_id)
