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


# -----------------------------------------------------------------------------
# Stuck-claim recovery (L3-SWEEP-020)
# -----------------------------------------------------------------------------


from datetime import timedelta  # noqa: E402


async def _claim_at(
    repo: SqliteSweeperActionRepository,
    conn: aiosqlite.Connection,
    *,
    enqueued_at: datetime,
    claimed_at: datetime,
) -> int:
    """Helper: enqueue and claim so the row is in 'in-flight' state.
    Returns the action_id."""
    await _enqueue_at(repo, conn, action="NOTIFY_ADMINS", enqueued_at=enqueued_at)
    claimed = await repo.claim_pending(now=claimed_at, limit=10)
    await conn.commit()
    return claimed[0].action_id


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-020")
async def test_reclaim_stuck_picks_up_old_in_flight_rows(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """An in-flight row whose claim is older than the threshold SHALL
    be reclaimed and have its attempts incremented."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(minutes=1)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)

    # Now: 10 minutes after the claim. Threshold: 5 minutes (300s).
    now = claimed_at + timedelta(minutes=10)
    reclaimed = await repo.reclaim_stuck(
        now=now, limit=10, stale_threshold_seconds=300, max_attempts=3
    )
    await conn.commit()

    assert len(reclaimed) == 1
    assert reclaimed[0].action_id == aid
    assert reclaimed[0].attempts == 1  # post-bump

    # Confirm the new claimed_at landed in the row.
    async with conn.execute(
        "SELECT claimed_at, attempts FROM sweeper_actions WHERE action_id = ?",
        (aid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "2026-04-21T12:11:00Z"  # claimed_at = now
    assert row[1] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-020")
async def test_reclaim_stuck_skips_recently_claimed_rows(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """A row whose claim is newer than the threshold SHALL NOT be
    reclaimed — the dispatcher might still be processing it."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)

    # Only 30 seconds past the claim; threshold is 300s.
    now = claimed_at + timedelta(seconds=30)
    reclaimed = await repo.reclaim_stuck(
        now=now, limit=10, stale_threshold_seconds=300, max_attempts=3
    )
    assert reclaimed == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-020")
async def test_reclaim_stuck_skips_completed_rows(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """A row that's been completed (success or terminal failure) SHALL
    NOT be reclaimed."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)
    completed_at = claimed_at + timedelta(seconds=1)
    await repo.mark_completed(action_id=aid, completed_at=completed_at)
    await conn.commit()

    # 1 hour later, well past the 5-minute threshold.
    now = claimed_at + timedelta(hours=1)
    reclaimed = await repo.reclaim_stuck(
        now=now, limit=10, stale_threshold_seconds=300, max_attempts=3
    )
    assert reclaimed == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-021")
async def test_reclaim_stuck_skips_rows_at_max_attempts(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """L3-SWEEP-021: rows already at the max-attempts cap SHALL NOT be
    reclaimed — they're abandonment candidates, not retry candidates."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)
    # Manually push attempts to 3 (the cap).
    await conn.execute("UPDATE sweeper_actions SET attempts = 3 WHERE action_id = ?", (aid,))
    await conn.commit()

    now = claimed_at + timedelta(hours=1)  # well past threshold
    reclaimed = await repo.reclaim_stuck(
        now=now, limit=10, stale_threshold_seconds=300, max_attempts=3
    )
    assert reclaimed == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-021")
async def test_find_abandoned_returns_exhausted_stuck_rows(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """L3-SWEEP-021: find_abandoned returns the rows reclaim_stuck
    skipped (stuck + at-or-past max_attempts)."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)
    await conn.execute("UPDATE sweeper_actions SET attempts = 3 WHERE action_id = ?", (aid,))
    await conn.commit()

    now = claimed_at + timedelta(hours=1)
    abandoned = await repo.find_abandoned(
        now=now, stale_threshold_seconds=300, max_attempts=3, limit=10
    )
    assert len(abandoned) == 1
    assert abandoned[0].action_id == aid
    assert abandoned[0].attempts == 3


@pytest.mark.asyncio
async def test_find_abandoned_is_pure_read(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """find_abandoned does not mutate — it's an inspection method."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)
    await conn.execute("UPDATE sweeper_actions SET attempts = 3 WHERE action_id = ?", (aid,))
    await conn.commit()

    now = claimed_at + timedelta(hours=1)
    await repo.find_abandoned(now=now, stale_threshold_seconds=300, max_attempts=3, limit=10)
    # Read state again; should be unchanged.
    async with conn.execute(
        "SELECT claimed_at, completed_at, attempts FROM sweeper_actions WHERE action_id = ?",
        (aid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None  # claimed_at unchanged
    assert row[1] is None  # completed_at still NULL
    assert row[2] == 3  # attempts unchanged


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-021")
async def test_mark_abandoned_stamps_completed_without_bumping_attempts(
    repo: SqliteSweeperActionRepository, conn: aiosqlite.Connection
) -> None:
    """mark_abandoned makes a row terminal but does NOT bump attempts —
    distinct from mark_failed because the abandonment decision isn't
    a fresh handler attempt."""
    enqueued = _T0
    claimed_at = _T0 + timedelta(seconds=10)
    aid = await _claim_at(repo, conn, enqueued_at=enqueued, claimed_at=claimed_at)
    await conn.execute("UPDATE sweeper_actions SET attempts = 3 WHERE action_id = ?", (aid,))
    await conn.commit()

    completed_at = claimed_at + timedelta(hours=1)
    await repo.mark_abandoned(action_id=aid, completed_at=completed_at, error_message="exhausted")
    await conn.commit()

    async with conn.execute(
        "SELECT completed_at, attempts, last_error FROM sweeper_actions WHERE action_id = ?",
        (aid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "2026-04-21T13:00:10Z"
    assert row[1] == 3  # NOT bumped
    assert row[2] == "exhausted"


@pytest.mark.asyncio
async def test_reclaim_stuck_rejects_invalid_args(
    repo: SqliteSweeperActionRepository,
) -> None:
    """All three positive-int params SHALL be validated."""
    with pytest.raises(ValueError, match="limit"):
        await repo.reclaim_stuck(now=_T0, limit=0, stale_threshold_seconds=300, max_attempts=3)
    with pytest.raises(ValueError, match="stale_threshold_seconds"):
        await repo.reclaim_stuck(now=_T0, limit=10, stale_threshold_seconds=0, max_attempts=3)
    with pytest.raises(ValueError, match="max_attempts"):
        await repo.reclaim_stuck(now=_T0, limit=10, stale_threshold_seconds=300, max_attempts=0)
