"""Unit tests for :mod:`message_service.infrastructure.persistence.unit_of_work`.

Tests exercise the UoW's transaction lifecycle (commit on clean exit,
rollback on exception, explicit commit/rollback). The injected repo
factories are plain :class:`MagicMock` instances; these tests do not
exercise concrete repo behavior, which is covered in Increment 11b's
per-repo test suites.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWork,
    SqliteUnitOfWorkFactory,
)

# -----------------------------------------------------------------------------
# Fixture — real connection, mocked repos
# -----------------------------------------------------------------------------


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[SqliteUnitOfWorkFactory]:
    """Build a factory against a fresh migrated DB; close it on teardown."""
    db = tmp_path / "test.db"
    conn = await open_connection(db)
    await apply_migrations(conn)
    uow_factory = SqliteUnitOfWorkFactory(
        conn=conn,
        run_repo_factory=lambda c: MagicMock(),
        stage_repo_factory=lambda c: MagicMock(),
        subscription_repo_factory=lambda c: MagicMock(),
        audit_log_factory=lambda c: MagicMock(),
        sweeper_action_repo_factory=lambda c: MagicMock(),
        user_repo_factory=lambda c: MagicMock(),
        session_repo_factory=lambda c: MagicMock(),
    )
    try:
        yield uow_factory
    finally:
        await uow_factory.close()


async def _user_count(factory: SqliteUnitOfWorkFactory) -> int:
    async with factory._conn.execute("SELECT COUNT(*) FROM users") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _insert_user(uow: SqliteUnitOfWork, email: str = "test@example.com") -> None:
    await uow._conn.execute(
        "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
        (email, "Test User", "2026-04-21T00:00:00Z"),
    )


# -----------------------------------------------------------------------------
# Commit on clean exit
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-003")
async def test_clean_exit_commits(factory: SqliteUnitOfWorkFactory) -> None:
    async with factory() as uow:
        await _insert_user(uow)
    assert await _user_count(factory) == 1
    await factory.close()


@pytest.mark.asyncio
async def test_multiple_inserts_in_same_uow_commit_together(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        await _insert_user(uow, email="a@x")
        await _insert_user(uow, email="b@x")
        await _insert_user(uow, email="c@x")
    assert await _user_count(factory) == 3
    await factory.close()


# -----------------------------------------------------------------------------
# Rollback on exception (L3-RUN-004)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_exception_rolls_back(factory: SqliteUnitOfWorkFactory) -> None:
    class BoomError(Exception):
        pass

    with pytest.raises(BoomError):
        async with factory() as uow:
            await _insert_user(uow)
            raise BoomError("force rollback")
    assert await _user_count(factory) == 0
    await factory.close()


@pytest.mark.asyncio
async def test_rollback_preserves_prior_committed_state(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """A rolled-back UoW SHALL NOT undo prior committed UoWs."""
    async with factory() as uow:
        await _insert_user(uow, email="keep@x")
    assert await _user_count(factory) == 1

    with pytest.raises(RuntimeError):
        async with factory() as uow:
            await _insert_user(uow, email="discard@x")
            raise RuntimeError("rollback")
    assert await _user_count(factory) == 1
    await factory.close()


# -----------------------------------------------------------------------------
# Explicit commit / rollback
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_commit_persists(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        await _insert_user(uow)
        await uow.commit()
    assert await _user_count(factory) == 1
    await factory.close()


@pytest.mark.asyncio
async def test_explicit_rollback_discards(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        await _insert_user(uow)
        await uow.rollback()
    assert await _user_count(factory) == 0
    await factory.close()


@pytest.mark.asyncio
async def test_explicit_commit_followed_by_aexit_does_not_double_commit(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """After explicit commit, __aexit__ SHALL be a no-op."""
    async with factory() as uow:
        await _insert_user(uow)
        await uow.commit()
        # Exit happens here; no second commit should fire.
    assert await _user_count(factory) == 1
    await factory.close()


@pytest.mark.asyncio
async def test_double_commit_raises(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        await uow.commit()
        with pytest.raises(PersistenceError, match="finalized"):
            await uow.commit()
    await factory.close()


@pytest.mark.asyncio
async def test_commit_after_rollback_raises(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        await uow.rollback()
        with pytest.raises(PersistenceError, match="finalized"):
            await uow.commit()
    await factory.close()


# -----------------------------------------------------------------------------
# Repository binding
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repos_bound_inside_context_manager(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        assert uow.run_repo is not None
        assert uow.stage_repo is not None
        assert uow.subscription_repo is not None
        assert uow.audit_log is not None
        assert uow.sweeper_action_repo is not None
        assert uow.user_repo is not None
        assert uow.session_repo is not None
    await factory.close()


@pytest.mark.asyncio
async def test_fresh_repos_per_uow_instance(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """Each UoW invocation SHALL get fresh repo instances."""
    async with factory() as uow1:
        repo1 = uow1.run_repo
    async with factory() as uow2:
        repo2 = uow2.run_repo
    assert repo1 is not repo2
    await factory.close()


@pytest.mark.asyncio
async def test_non_reentrant(factory: SqliteUnitOfWorkFactory) -> None:
    """Calling __aenter__ twice on the same UoW instance SHALL raise."""
    uow = factory()
    await uow.__aenter__()
    try:
        with pytest.raises(PersistenceError, match="re-entrant"):
            await uow.__aenter__()
    finally:
        await uow.rollback()
    await factory.close()


# -----------------------------------------------------------------------------
# Factory close
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factory_close_is_idempotent(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    await factory.close()
    # Second close should not raise.
    await factory.close()


# -----------------------------------------------------------------------------
# FK constraint enforcement (confirms the PRAGMA actually works across UoWs)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-002")
async def test_foreign_key_violation_triggers_rollback(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """Inserting a subscription with a bogus user_id SHALL fail and roll back."""
    # Pydantic-free minimal rejection: insert a subscription FK'd to
    # a user_id that doesn't exist.
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        async with factory() as uow:
            await uow._conn.execute(
                "INSERT INTO subscriptions (user_id, granularity, target_value, created_at) "
                "VALUES (?, ?, ?, ?)",
                (999, "GLOBAL", None, "2026-04-21T00:00:00Z"),
            )
    # No subscription leaked through.
    async with factory._conn.execute("SELECT COUNT(*) FROM subscriptions") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0
    await factory.close()
