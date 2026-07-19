"""Unit tests for :class:`SqliteAuditLog`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations

# -----------------------------------------------------------------------------
# Fixtures + helpers
# -----------------------------------------------------------------------------

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
async def log(conn: aiosqlite.Connection) -> SqliteAuditLog:
    return SqliteAuditLog(conn)


def _event(
    *,
    ts: datetime = _T0,
    action: AuditAction = AuditAction.BEGIN_RUN,
    actor: str = "user:alice",
    resource: str = "run:001",
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    details: Mapping[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=ts,
        action=action,
        actor=actor,
        resource=resource,
        outcome=outcome,
        details=dict(details) if details else {},
    )


# -----------------------------------------------------------------------------
# record
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-OBS-002")
async def test_record_persists_event(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event())
    await conn.commit()

    events = await log.query()
    assert len(events) == 1
    assert events[0].action is AuditAction.BEGIN_RUN
    assert events[0].resource == "run:001"


@pytest.mark.asyncio
async def test_record_preserves_details_structure(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    """Nested details SHALL round-trip identically."""
    original = {
        "run_id": "abc",
        "prior_state": "INITIATED",
        "new_state": "AGGREGATING",
        "nested": {"list": [1, 2, 3], "map": {"k": "v"}},
    }
    await log.record(_event(details=original))
    await conn.commit()

    events = await log.query()
    assert events[0].details == original


@pytest.mark.asyncio
async def test_record_preserves_insertion_order_within_transaction(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    """Multiple events at the same timestamp preserve INSERT order via audit_id."""
    for i in range(3):
        await log.record(_event(resource=f"run:{i:03d}"))
    await conn.commit()

    # Query returns newest-first; within a timestamp group, newest audit_id first.
    events = await log.query()
    resources = [e.resource for e in events]
    # Insert order was run:000, run:001, run:002; DESC by audit_id returns
    # run:002, run:001, run:000.
    assert resources == ["run:002", "run:001", "run:000"]


@pytest.mark.asyncio
async def test_record_failure_outcome(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event(outcome=AuditOutcome.FAILURE, details={"reason": "EMAIL_DELIVERY"}))
    await conn.commit()

    events = await log.query()
    assert events[0].outcome is AuditOutcome.FAILURE
    assert events[0].details["reason"] == "EMAIL_DELIVERY"


# -----------------------------------------------------------------------------
# query — filtering
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_returns_most_recent_first(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    await log.record(_event(ts=_T0, resource="run:old"))
    await log.record(_event(ts=_T0 + timedelta(hours=1), resource="run:mid"))
    await log.record(_event(ts=_T0 + timedelta(hours=2), resource="run:new"))
    await conn.commit()

    events = await log.query()
    assert [e.resource for e in events] == ["run:new", "run:mid", "run:old"]


@pytest.mark.asyncio
async def test_query_filter_by_action(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event(action=AuditAction.BEGIN_RUN))
    await log.record(_event(action=AuditAction.FINALIZE_RUN))
    await log.record(_event(action=AuditAction.SEND_REPORT))
    await conn.commit()

    finalizes = await log.query(action=AuditAction.FINALIZE_RUN)
    assert len(finalizes) == 1
    assert finalizes[0].action is AuditAction.FINALIZE_RUN


@pytest.mark.asyncio
async def test_query_filter_by_resource(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event(resource="run:a"))
    await log.record(_event(resource="run:b"))
    await log.record(_event(resource="run:a"))
    await conn.commit()

    a_events = await log.query(resource="run:a")
    assert len(a_events) == 2


@pytest.mark.asyncio
async def test_query_filter_by_actor(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event(actor="user:alice"))
    await log.record(_event(actor="user:bob"))
    await log.record(_event(actor="system:sweeper"))
    await conn.commit()

    bob = await log.query(actor="user:bob")
    assert len(bob) == 1
    assert bob[0].actor == "user:bob"


@pytest.mark.asyncio
async def test_query_filter_by_time_range(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    await log.record(_event(ts=_T0 - timedelta(hours=2), resource="run:before"))
    await log.record(_event(ts=_T0, resource="run:at"))
    await log.record(_event(ts=_T0 + timedelta(hours=2), resource="run:after"))
    await conn.commit()

    # Inclusive bounds.
    within = await log.query(
        since=_T0 - timedelta(hours=1),
        until=_T0 + timedelta(hours=1),
    )
    assert [e.resource for e in within] == ["run:at"]


@pytest.mark.asyncio
async def test_query_filters_are_anded(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    """Multiple filters SHALL compose via AND, not OR."""
    await log.record(_event(action=AuditAction.BEGIN_RUN, resource="run:a"))
    await log.record(_event(action=AuditAction.FINALIZE_RUN, resource="run:a"))
    await log.record(_event(action=AuditAction.BEGIN_RUN, resource="run:b"))
    await conn.commit()

    matches = await log.query(action=AuditAction.BEGIN_RUN, resource="run:a")
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_query_limit_caps_result_count(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    for i in range(10):
        await log.record(_event(ts=_T0 + timedelta(minutes=i)))
    await conn.commit()

    events = await log.query(limit=3)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_query_default_limit_is_1000(log: SqliteAuditLog, conn: aiosqlite.Connection) -> None:
    for i in range(1005):
        await log.record(_event(ts=_T0 + timedelta(seconds=i)))
    await conn.commit()

    events = await log.query()
    assert len(events) == 1000


@pytest.mark.asyncio
async def test_query_zero_limit_rejected(log: SqliteAuditLog) -> None:
    with pytest.raises(ValueError, match="limit"):
        await log.query(limit=0)


@pytest.mark.asyncio
async def test_query_empty_log_returns_empty(log: SqliteAuditLog) -> None:
    events = await log.query()
    assert list(events) == []


# -----------------------------------------------------------------------------
# Corrupt-data handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_with_unknown_action_raises_persistence_error(
    conn: aiosqlite.Connection,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_log (
            timestamp, action, actor, resource, outcome, details_json
        ) VALUES ('2026-04-21T00:00:00Z', 'NOT_A_REAL_ACTION',
                  'actor', 'resource', 'SUCCESS', '{}')
        """
    )
    await conn.commit()

    log = SqliteAuditLog(conn)
    with pytest.raises(PersistenceError, match="unknown action"):
        await log.query()


@pytest.mark.asyncio
async def test_row_with_malformed_details_json_raises(
    conn: aiosqlite.Connection,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_log (
            timestamp, action, actor, resource, outcome, details_json
        ) VALUES ('2026-04-21T00:00:00Z', 'BEGIN_RUN',
                  'actor', 'resource', 'SUCCESS', 'not json')
        """
    )
    await conn.commit()

    log = SqliteAuditLog(conn)
    with pytest.raises(PersistenceError, match="decode persisted JSON"):
        await log.query()


# -----------------------------------------------------------------------------
# fetch_older_than — the archival read that mirrors delete_older_than (L3-OBS-042)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-042")
async def test_fetch_older_than_returns_exactly_what_delete_removes(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    """For a dataset larger than batch_size, fetch returns exactly what delete removes."""
    cutoff = _T0 + timedelta(days=10)
    for i in range(5):  # old rows, strictly increasing timestamps
        await log.record(_event(ts=_T0 + timedelta(hours=i), resource=f"old:{i}"))
    for i in range(2):  # recent rows, after the cutoff
        await log.record(_event(ts=cutoff + timedelta(hours=i), resource=f"new:{i}"))
    await conn.commit()

    batch = 3
    fetched = await log.fetch_older_than(cutoff, batch_size=batch)
    assert [e.resource for e in fetched] == ["old:0", "old:1", "old:2"]  # oldest-first
    fetched_ids = {e.audit_id for e in fetched}

    deleted = await log.delete_older_than(cutoff, batch_size=batch)
    await conn.commit()
    assert deleted == batch

    remaining = await log.query(limit=100)
    remaining_ids = {e.audit_id for e in remaining}
    assert fetched_ids.isdisjoint(remaining_ids)  # every fetched row was deleted
    assert {e.resource for e in remaining} == {"old:3", "old:4", "new:0", "new:1"}


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-042")
async def test_fetch_and_delete_agree_on_tied_timestamps_at_batch_boundary(
    log: SqliteAuditLog, conn: aiosqlite.Connection
) -> None:
    """With tied timestamps at the batch cap, the audit_id tiebreak keeps fetch==delete."""
    cutoff = _T0 + timedelta(days=1)
    for i in range(4):  # four rows sharing the SAME old timestamp
        await log.record(_event(ts=_T0, resource=f"tie:{i}"))
    await conn.commit()

    batch = 2
    fetched_ids = {e.audit_id for e in await log.fetch_older_than(cutoff, batch_size=batch)}
    deleted = await log.delete_older_than(cutoff, batch_size=batch)
    await conn.commit()
    assert deleted == 2

    remaining_ids = {e.audit_id for e in await log.query(limit=100)}
    assert len(remaining_ids) == 2
    assert fetched_ids.isdisjoint(remaining_ids)  # the exact fetched rows were deleted


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-042")
async def test_fetch_older_than_rejects_naive_cutoff_and_bad_batch(
    log: SqliteAuditLog,
) -> None:
    """fetch_older_than validates its arguments like delete_older_than."""
    with pytest.raises(ValueError, match="timezone-aware"):
        await log.fetch_older_than(datetime(2026, 1, 1), batch_size=10)
    with pytest.raises(ValueError, match="positive"):
        await log.fetch_older_than(_T0, batch_size=0)
