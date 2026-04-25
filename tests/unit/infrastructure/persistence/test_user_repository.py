"""Unit tests for :class:`SqliteUserRepository`.

Drive the adapter against a real :memory: SQLite with all migrations
applied so the SQL exercises the real schema (including 003's
``password_hash`` and ``is_admin`` columns).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.aggregates.user import User
from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.user_repository import SqliteUserRepository

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> SqliteUserRepository:
    return SqliteUserRepository(conn)


def _new_user(email: str = "alice@example.com", **overrides: object) -> User:
    base: dict[str, object] = {
        "email": email,
        "display_name": "Alice",
        "password_hash": "$argon2id$v=19$m=8,t=1,p=1$YWFhYWFhYWE$ZHVtbXk",
        "created_at": _T0,
    }
    base.update(overrides)
    return User(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_save_returns_user_with_user_id_set(repo: SqliteUserRepository) -> None:
    saved = await repo.save(_new_user())
    assert saved.user_id is not None
    assert saved.user_id > 0
    assert saved.email == "alice@example.com"


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_save_rejects_user_with_user_id_already_set(
    repo: SqliteUserRepository,
) -> None:
    user_with_id = User(
        email="b@x",
        display_name="B",
        password_hash="h",
        created_at=_T0,
        user_id=42,
    )
    with pytest.raises(ValueError, match="user_id"):
        await repo.save(user_with_id)


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_save_duplicate_email_raises_persistence_error(
    repo: SqliteUserRepository,
) -> None:
    await repo.save(_new_user(email="dup@x"))
    with pytest.raises(PersistenceError, match="dup@x"):
        await repo.save(_new_user(email="dup@x"))


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_get_by_email_returns_saved_user(repo: SqliteUserRepository) -> None:
    saved = await repo.save(_new_user(email="bob@x"))
    fetched = await repo.get_by_email("bob@x")
    assert fetched is not None
    assert fetched.user_id == saved.user_id
    assert fetched.email == "bob@x"
    assert fetched.password_hash == saved.password_hash


@pytest.mark.asyncio
async def test_get_by_email_returns_none_when_absent(
    repo: SqliteUserRepository,
) -> None:
    assert await repo.get_by_email("nobody@x") is None


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_get_by_id_returns_saved_user(repo: SqliteUserRepository) -> None:
    saved = await repo.save(_new_user(email="carol@x"))
    assert saved.user_id is not None
    fetched = await repo.get_by_id(saved.user_id)
    assert fetched is not None
    assert fetched.email == "carol@x"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_absent(
    repo: SqliteUserRepository,
) -> None:
    assert await repo.get_by_id(999_999) is None


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_admin_and_disabled_round_trip(repo: SqliteUserRepository) -> None:
    saved = await repo.save(_new_user(email="admin@x", is_admin=True, disabled=True))
    fetched = await repo.get_by_email("admin@x")
    assert fetched is not None
    assert fetched.is_admin is True
    assert fetched.disabled is True
    assert fetched.user_id == saved.user_id
