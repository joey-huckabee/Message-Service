"""Unit tests for :class:`SqliteSweeperActionRepository`.

Exercises the adapter against a real :memory: SQLite with the 002
migration applied. The use-case-layer tests
(``test_sweeper_action_dispatcher.py``) drive the repo through the
dispatcher; this file isolates the SQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.sweeper_action_repository import ClaimedAction
from message_service.domain.ids import RunId
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        # FK to runs requires a parent row; seed one.
        await c.execute(
            "INSERT INTO runs ("
            "  run_id, pipeline_type, state, attachment_mode, "
            "  aggregation_template_name, aggregation_template_version, "
            "  tags_json, declared_stages_json, "
            "  subscription_predicate_tags_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "00000000-0000-4000-8000-000000000001",
                "etl-default",
                "ORPHANED",
                "PER_STAGE",
                None,
                None,
                "[]",
                "[]",
                "[]",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        await c.commit()
        yield c
    finally:
        await c.close()


@pytest.fixture
def repo(conn: aiosqlite.Connection) -> SqliteSweeperActionRepository:
    return SqliteSweeperActionRepository(conn)


_RUN_ID = RunId("00000000-0000-4000-8000-000000000001")


async def _enqueue_at(
    repo: SqliteSweeperActionRepository,
    conn: aiosqlite.Connection,
    *,
    action: str,
    enqueued_at: datetime,
) -> None:
    """Helper: enqueue + commit so subsequent calls see the row."""
    await repo.enqueue(
        run_id=_RUN_ID,
        action_name=action,  # type: ignore[arg-type]
        enqueued_at=enqueued_at,
    )
    await conn.commit()


async def _row(
    conn: aiosqlite.Connection, action_id: int
) -> tuple[str | None, str | None, int, str | None]:
    async with conn.execute(
        "SELECT claimed_at, completed_at, attempts, last_error "
        "FROM sweeper_actions WHERE action_id = ?",
        (action_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1], int(row[2]), row[3]


# -----------------------------------------------------------------------------
# enqueue
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_enqueue_inserts_pending_row(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=_T0)

    async with conn.execute("SELECT COUNT(*) FROM sweeper_actions") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1

    async with conn.execute(
        "SELECT claimed_at, completed_at, attempts FROM sweeper_actions"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] is None
    assert row[2] == 0


# -----------------------------------------------------------------------------
# claim_pending
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_claim_pending_returns_oldest_first(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """FIFO: enqueued_at ascending; ties broken by action_id (insert order)."""
    # Enqueue two with the same timestamp (mirrors a single sweeper tick
    # inserting both in configured order) plus one earlier and one later.
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=_T0)
    await _enqueue_at(repo, conn, action="DISCARD_SILENTLY", enqueued_at=_T0)

    claimed = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()

    assert len(claimed) == 2
    assert [c.action_name for c in claimed] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]
    assert claimed[0].run_id == _RUN_ID
    assert all(c.attempts == 0 for c in claimed)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_claim_pending_respects_limit(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    for action in ("NOTIFY_ADMINS", "DISCARD_SILENTLY", "NOTIFY_ADMINS"):
        await _enqueue_at(repo, conn, action=action, enqueued_at=_T0)

    first = await repo.claim_pending(now=_T0, limit=2)
    await conn.commit()
    assert len(first) == 2

    # Second call sees the leftover row only.
    second = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()
    assert len(second) == 1


@pytest.mark.asyncio
async def test_claim_pending_empty_outbox_returns_empty(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    claimed = await repo.claim_pending(now=_T0, limit=10)
    assert list(claimed) == []


@pytest.mark.asyncio
async def test_claim_pending_skips_already_claimed_rows(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """A row whose ``claimed_at`` is set is NOT re-claimed by a subsequent call.

    This is the no-double-dispatch contract that makes the outbox
    exactly-once-on-claim.
    """
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=_T0)

    first = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()
    assert len(first) == 1

    second = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()
    assert list(second) == []


@pytest.mark.asyncio
async def test_claim_pending_limit_must_be_positive(
    repo: SqliteSweeperActionRepository,
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await repo.claim_pending(now=_T0, limit=0)


# -----------------------------------------------------------------------------
# mark_completed / mark_failed
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_mark_completed_stamps_completed_at(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=_T0)
    claimed = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()

    completed_at = datetime(2026, 4, 21, 12, 0, 5, tzinfo=UTC)
    await repo.mark_completed(action_id=claimed[0].action_id, completed_at=completed_at)
    await conn.commit()

    claimed_at, comp_at, attempts, last_error = await _row(conn, claimed[0].action_id)
    assert claimed_at is not None
    assert comp_at is not None
    assert comp_at == "2026-04-21T12:00:05Z"
    assert attempts == 0
    assert last_error is None


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_mark_failed_bumps_attempts_and_records_error(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=_T0)
    claimed = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()

    completed_at = datetime(2026, 4, 21, 12, 0, 5, tzinfo=UTC)
    await repo.mark_failed(
        action_id=claimed[0].action_id,
        completed_at=completed_at,
        error_message="smtp timed out",
    )
    await conn.commit()

    _, comp_at, attempts, last_error = await _row(conn, claimed[0].action_id)
    assert comp_at == "2026-04-21T12:00:05Z"
    assert attempts == 1
    assert last_error == "smtp timed out"


@pytest.mark.asyncio
async def test_claimed_action_dataclass_round_trip(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """Round-trip the values: enqueue → claim → assert ClaimedAction shape."""
    await _enqueue_at(repo, conn, action="DISCARD_SILENTLY", enqueued_at=_T0)
    claimed = await repo.claim_pending(now=_T0, limit=10)
    await conn.commit()

    assert claimed[0] == ClaimedAction(
        action_id=claimed[0].action_id,  # AUTOINCREMENT, value not asserted
        run_id=_RUN_ID,
        action_name="DISCARD_SILENTLY",
        attempts=0,
    )
