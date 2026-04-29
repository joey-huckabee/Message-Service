"""Unit tests for :class:`SqliteSessionRepository`.

Drive the adapter against a real :memory: SQLite with all migrations
applied. Sessions FK to users(user_id) — every test seeds at least one
parent user via the repo (not raw SQL) so the path matches production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.aggregates.session import Session
from message_service.domain.aggregates.user import User
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.session_repository import (
    SqliteSessionRepository,
)
from message_service.infrastructure.persistence.user_repository import (
    SqliteUserRepository,
)

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)
_HASH_A = "a" * 64
_HASH_B = "b" * 64


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def session_repo(conn: aiosqlite.Connection) -> SqliteSessionRepository:
    return SqliteSessionRepository(conn)


@pytest.fixture
async def user_id(conn: aiosqlite.Connection) -> int:
    repo = SqliteUserRepository(conn)
    saved = await repo.save(
        User(
            email="alice@example.com",
            display_name="Alice",
            password_hash="h",
            created_at=_T0,
        )
    )
    assert saved.user_id is not None
    return saved.user_id


def _session(token_hash: str, user_id: int, *, last: datetime | None = None) -> Session:
    return Session(
        token_hash=token_hash,
        user_id=user_id,
        created_at=_T0,
        last_activity_at=last if last is not None else _T0,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-002")
async def test_save_then_get_round_trip(
    session_repo: SqliteSessionRepository, user_id: int
) -> None:
    await session_repo.save(_session(_HASH_A, user_id))
    fetched = await session_repo.get_by_token_hash(_HASH_A)
    assert fetched is not None
    assert fetched.token_hash == _HASH_A
    assert fetched.user_id == user_id
    assert fetched.created_at == _T0
    assert fetched.last_activity_at == _T0


@pytest.mark.asyncio
async def test_get_by_token_hash_returns_none_when_absent(
    session_repo: SqliteSessionRepository,
) -> None:
    assert await session_repo.get_by_token_hash(_HASH_A) is None


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AUTH-006")
@pytest.mark.requirement("L3-AUTH-010")
async def test_touch_updates_last_activity_at(
    session_repo: SqliteSessionRepository, user_id: int
) -> None:
    await session_repo.save(_session(_HASH_A, user_id))
    later = _T0 + timedelta(minutes=5)
    await session_repo.touch(_HASH_A, later)
    fetched = await session_repo.get_by_token_hash(_HASH_A)
    assert fetched is not None
    assert fetched.last_activity_at == later


@pytest.mark.asyncio
async def test_touch_unknown_token_is_noop(
    session_repo: SqliteSessionRepository,
) -> None:
    """No-op if the row was already deleted (race with logout)."""
    later = _T0 + timedelta(minutes=5)
    # Should not raise.
    await session_repo.touch(_HASH_A, later)


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-002")
async def test_delete_by_token_hash_removes_row(
    session_repo: SqliteSessionRepository, user_id: int
) -> None:
    await session_repo.save(_session(_HASH_A, user_id))
    await session_repo.delete_by_token_hash(_HASH_A)
    assert await session_repo.get_by_token_hash(_HASH_A) is None


@pytest.mark.asyncio
async def test_delete_by_token_hash_is_idempotent(
    session_repo: SqliteSessionRepository,
) -> None:
    """Logout from an already-deleted session SHALL NOT raise."""
    await session_repo.delete_by_token_hash(_HASH_A)
    await session_repo.delete_by_token_hash(_HASH_A)


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AUTH-006")
@pytest.mark.requirement("L3-AUTH-011")
async def test_delete_expired_only_removes_below_threshold(
    session_repo: SqliteSessionRepository, user_id: int
) -> None:
    fresh_activity = _T0 + timedelta(hours=2)
    stale_activity = _T0 + timedelta(minutes=1)
    await session_repo.save(_session(_HASH_A, user_id, last=fresh_activity))
    await session_repo.save(_session(_HASH_B, user_id, last=stale_activity))

    threshold = _T0 + timedelta(hours=1)
    deleted = await session_repo.delete_expired(idle_threshold=threshold)

    assert deleted == 1
    assert await session_repo.get_by_token_hash(_HASH_A) is not None
    assert await session_repo.get_by_token_hash(_HASH_B) is None
