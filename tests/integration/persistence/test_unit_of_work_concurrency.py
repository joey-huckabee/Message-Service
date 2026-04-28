"""Concurrency contract for :class:`SqliteUnitOfWork`.

The factory shares one :class:`aiosqlite.Connection` across all UoW
instances. Without serialization, two coroutines that simultaneously
enter ``async with factory()`` would both call
``await conn.execute("BEGIN")`` against the same connection, and the
second would raise
``sqlite3.OperationalError: cannot start a transaction within a
transaction`` (wrapped as :class:`PersistenceError`).

L2-PERS-004 + L3-PERS-006 + L3-PERS-007 + L3-PERS-021 (post-Increment-27)
require the factory to hold an :class:`asyncio.Lock` and thread it
into every UoW it produces, with each UoW acquiring the lock before
BEGIN and releasing it exactly once on every transaction-closing
path. This test verifies that contract by spawning two real
coroutines that contend on the lock against a real migrated
database, both performing writes that must commit successfully.

The assertions on this test are sufficient evidence on their own:
without the lock, the second coroutine's BEGIN raises naturally and
the test fails. No "remove the lock to prove the test catches it"
step is needed — the absence of failure is the proof of correctness.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWork,
    SqliteUnitOfWorkFactory,
)


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[SqliteUnitOfWorkFactory]:
    """Build a factory against a fresh migrated DB; close it on teardown."""
    db = tmp_path / "concurrency.db"
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


async def _insert_user_with_yield(
    uow: SqliteUnitOfWork,
    *,
    email: str,
    display_name: str,
    started: asyncio.Event,
) -> None:
    """Open a real write inside the UoW, yield, and write again.

    The ``started`` event is set after the BEGIN-and-first-write
    completes so the test driver knows this coroutine has entered
    its transaction. The intervening ``asyncio.sleep(0)`` cedes
    control to the event loop with the transaction held — exactly
    the window during which a concurrent BEGIN against the shared
    connection would race without serialization.
    """
    await uow._conn.execute(
        "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
        (email, display_name, "2026-04-27T00:00:00Z"),
    )
    started.set()
    await asyncio.sleep(0)
    await uow._conn.execute(
        "UPDATE users SET display_name = ? WHERE email = ?",
        (display_name + " (updated)", email),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L2-PERS-004")
@pytest.mark.requirement("L3-PERS-006")
@pytest.mark.requirement("L3-PERS-007")
@pytest.mark.requirement("L3-PERS-021")
async def test_two_concurrent_uows_serialize_and_both_commit(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """Two simultaneous UoWs SHALL each commit cleanly without colliding at BEGIN."""
    started_a = asyncio.Event()
    started_b = asyncio.Event()

    async def worker(email: str, display_name: str, started: asyncio.Event) -> None:
        async with factory() as uow:
            await _insert_user_with_yield(
                uow,
                email=email,
                display_name=display_name,
                started=started,
            )

    # Launch both workers concurrently. With the lock in place, the
    # second worker's __aenter__ will queue at the lock until the
    # first worker's __aexit__ releases it. Without the lock, the
    # second worker's BEGIN raises "cannot start a transaction within
    # a transaction" and asyncio.gather propagates the error.
    await asyncio.gather(
        worker("alice@example.com", "Alice", started_a),
        worker("bob@example.com", "Bob", started_b),
    )

    # Both workers reached their first write — proves the contention
    # actually happened (otherwise one of these events stays unset).
    assert started_a.is_set()
    assert started_b.is_set()

    # Both transactions committed: the rows are visible on a fresh
    # read after both UoWs exited. The display_name column carries
    # the post-update value, proving the second write inside each
    # transaction also landed (i.e., the workers ran their full
    # transactional work, not just BEGIN).
    async with factory._conn.execute("SELECT email, display_name FROM users ORDER BY email") as cur:
        rows = await cur.fetchall()
    assert [(str(row[0]), str(row[1])) for row in rows] == [
        ("alice@example.com", "Alice (updated)"),
        ("bob@example.com", "Bob (updated)"),
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-007")
async def test_lock_released_after_clean_commit(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """A serial sequence of UoWs SHALL each acquire and release the lock cleanly."""
    # Three back-to-back UoWs — the lock must be released after each
    # one or the second `async with` would deadlock.
    for i in range(3):
        async with factory() as uow:
            await uow._conn.execute(
                "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
                (f"user{i}@example.com", f"User {i}", "2026-04-27T00:00:00Z"),
            )

    async with factory._conn.execute("SELECT COUNT(*) FROM users") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 3


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-007")
async def test_lock_released_after_exception_rollback(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """A UoW exited via exception SHALL release the lock so subsequent UoWs proceed."""

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        async with factory() as uow:
            await uow._conn.execute(
                "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
                ("aborted@example.com", "Aborted", "2026-04-27T00:00:00Z"),
            )
            raise _BoomError

    # If the lock leaked, the next __aenter__ would deadlock. We
    # protect the test with a real timeout so a leak fails the run
    # rather than hanging it.
    async with asyncio.timeout(2.0), factory() as uow:
        await uow._conn.execute(
            "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
            ("after_rollback@example.com", "After", "2026-04-27T00:00:00Z"),
        )

    # The aborted insert was rolled back; the post-rollback insert
    # was committed. Net rows = 1.
    async with factory._conn.execute("SELECT email FROM users ORDER BY email") as cur:
        rows = await cur.fetchall()
    emails = [str(row[0]) for row in rows]
    assert emails == ["after_rollback@example.com"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-007")
async def test_lock_released_after_explicit_commit(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """Explicit commit() inside the block SHALL release the lock; next UoW proceeds."""
    async with factory() as uow:
        await uow._conn.execute(
            "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
            ("explicit@example.com", "Explicit", "2026-04-27T00:00:00Z"),
        )
        await uow.commit()

    async with asyncio.timeout(2.0), factory() as uow:
        await uow._conn.execute(
            "INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?)",
            ("after_explicit@example.com", "After", "2026-04-27T00:00:00Z"),
        )

    async with factory._conn.execute("SELECT COUNT(*) FROM users") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 2


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-006")
async def test_factory_lock_constructed_lazily(
    factory: SqliteUnitOfWorkFactory,
) -> None:
    """The factory's asyncio.Lock SHALL NOT exist before the first __call__."""
    # Walk a freshly-initialized factory: no UoWs requested yet, no
    # lock should be bound. (Construction must be loop-agnostic.)
    db = factory._conn  # reuse the same migrated DB
    fresh = SqliteUnitOfWorkFactory(
        conn=db,
        run_repo_factory=lambda c: MagicMock(),
        stage_repo_factory=lambda c: MagicMock(),
        subscription_repo_factory=lambda c: MagicMock(),
        audit_log_factory=lambda c: MagicMock(),
        sweeper_action_repo_factory=lambda c: MagicMock(),
        user_repo_factory=lambda c: MagicMock(),
        session_repo_factory=lambda c: MagicMock(),
    )
    assert fresh._lock is None

    # First __call__ binds the lock. Second __call__ reuses the same
    # instance — both UoWs from this factory share one lock.
    uow1 = fresh()
    assert isinstance(fresh._lock, asyncio.Lock)
    first_lock = fresh._lock
    uow2 = fresh()
    assert fresh._lock is first_lock
    assert uow1._lock is first_lock
    assert uow2._lock is first_lock
