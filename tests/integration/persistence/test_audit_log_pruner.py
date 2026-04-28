"""Integration tests for :class:`AuditLogPrunerUseCase`.

Real migrated SQLite + real ``SqliteAuditLog`` adapter + injected
:class:`FakeClock`. Each test seeds audit rows at varied timestamps
via ``uow.audit_log.record(...)``, drives ``pruner.run_once()``
synchronously, then verifies the post-state via real
``uow.audit_log.query(...)`` reads.

Structural-sequencing pattern from Increment 27h applies: the
pruner is driven directly rather than via the
:class:`AuditLogPrunerLoop` background task, so post-condition
assertions read state that is structurally guaranteed to be
settled.

Requirement references
----------------------
L1-OBS-003 (append-only audit log + configurable retention)
L2-OBS-008, L2-OBS-009 (retention enforcement + asyncio scheduling)
L3-OBS-014 (24h cadence default)
L3-OBS-015 (DELETE FROM audit_log WHERE timestamp < cutoff)
L3-OBS-016 (per-tick batch ceiling)
L3-OBS-040 (anti-recursion: no audit row for the prune action)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.use_cases.audit_log_pruner import (
    AuditLogPrunerUseCase,
    AuditPruneResult,
)
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import (
    SqliteRunRepository,
)
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
from message_service.infrastructure.persistence.user_repository import SqliteUserRepository
from tests.fixtures.clocks import FakeClock

# Wall-clock anchor; tests advance from here.
_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
# Default retention used in most tests.
_RETENTION_DAYS = 30


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def db_conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Open + migrate a fresh SQLite DB in ``tmp_path``."""
    conn = await open_connection(tmp_path / "audit_pruner.db")
    try:
        await apply_migrations(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def fake_clock_now() -> FakeClock:
    """``FakeClock`` set to ``_NOW`` so cutoff arithmetic is anchored."""
    return FakeClock(_NOW)


@pytest.fixture
async def uow_factory(
    db_conn: aiosqlite.Connection,
    fake_clock_now: FakeClock,
) -> SqliteUnitOfWorkFactory:
    """UoW factory with real adapters bound to ``db_conn``."""
    return SqliteUnitOfWorkFactory(
        conn=db_conn,
        run_repo_factory=SqliteRunRepository,
        stage_repo_factory=SqliteStageRepository,
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=fake_clock_now),
        audit_log_factory=SqliteAuditLog,
        sweeper_action_repo_factory=SqliteSweeperActionRepository,
        user_repo_factory=SqliteUserRepository,
        session_repo_factory=SqliteSessionRepository,
    )


@pytest.fixture
def pruner(
    uow_factory: SqliteUnitOfWorkFactory,
    fake_clock_now: FakeClock,
) -> AuditLogPrunerUseCase:
    """``AuditLogPrunerUseCase`` with retention=30 days, batch=10000."""
    return AuditLogPrunerUseCase(
        uow_factory=uow_factory,
        clock=fake_clock_now,
        retention_days=_RETENTION_DAYS,
        cleanup_batch_size=10_000,
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _seed_event(
    factory: SqliteUnitOfWorkFactory,
    *,
    when: datetime,
    action: AuditAction = AuditAction.LOGIN,
    actor: str = "user:1",
    resource: str = "session:test",
) -> None:
    """Insert a single audit row at the given timestamp via the real adapter."""
    event = AuditEvent(
        timestamp=when,
        action=action,
        actor=actor,
        resource=resource,
        outcome=AuditOutcome.SUCCESS,
        details={"seeded_at": when.isoformat()},
    )
    async with factory() as uow:
        await uow.audit_log.record(event)


async def _row_count(conn: aiosqlite.Connection) -> int:
    """Total rows currently in audit_log."""
    async with conn.execute("SELECT COUNT(*) FROM audit_log") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# -----------------------------------------------------------------------------
# Eligibility predicate (L3-OBS-015 strict-less-than boundary)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-OBS-003")
@pytest.mark.requirement("L2-OBS-008")
@pytest.mark.requirement("L3-OBS-015")
async def test_old_rows_are_deleted_recent_rows_preserved(
    pruner: AuditLogPrunerUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    db_conn: aiosqlite.Connection,
) -> None:
    """Rows older than (now - retention_days) SHALL be deleted; newer rows preserved."""
    # 2 rows clearly past cutoff, 2 rows clearly inside the window.
    await _seed_event(
        uow_factory, when=_NOW - timedelta(days=_RETENTION_DAYS + 5), actor="user:old1"
    )
    await _seed_event(
        uow_factory, when=_NOW - timedelta(days=_RETENTION_DAYS + 2), actor="user:old2"
    )
    await _seed_event(
        uow_factory, when=_NOW - timedelta(days=_RETENTION_DAYS - 1), actor="user:keep1"
    )
    await _seed_event(uow_factory, when=_NOW - timedelta(days=1), actor="user:keep2")

    assert await _row_count(db_conn) == 4

    result = await pruner.run_once()

    assert result == AuditPruneResult(rows_deleted=2)
    assert await _row_count(db_conn) == 2

    async with uow_factory() as uow:
        survivors = await uow.audit_log.query(limit=10)
    survivor_actors = sorted(e.actor for e in survivors)
    assert survivor_actors == ["user:keep1", "user:keep2"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-015")
async def test_strict_less_than_boundary_preserves_row_at_exact_cutoff(
    pruner: AuditLogPrunerUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    db_conn: aiosqlite.Connection,
) -> None:
    """L3-OBS-015 boundary: a row whose timestamp equals cutoff SHALL be preserved."""
    cutoff = _NOW - timedelta(days=_RETENTION_DAYS)
    await _seed_event(uow_factory, when=cutoff, actor="user:boundary")
    # One unambiguously old row to confirm the pruner did run.
    await _seed_event(
        uow_factory, when=_NOW - timedelta(days=_RETENTION_DAYS + 1), actor="user:old"
    )

    result = await pruner.run_once()

    assert result.rows_deleted == 1
    # The boundary row survives.
    async with uow_factory() as uow:
        survivors = await uow.audit_log.query(limit=10)
    survivor_actors = [e.actor for e in survivors]
    assert "user:boundary" in survivor_actors
    assert "user:old" not in survivor_actors


@pytest.mark.asyncio
async def test_no_eligible_rows_returns_zero_and_writes_no_audit(
    pruner: AuditLogPrunerUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    db_conn: aiosqlite.Connection,
) -> None:
    """A pruner tick over an empty DB SHALL be a clean no-op."""
    result = await pruner.run_once()
    assert result == AuditPruneResult(rows_deleted=0)
    assert await _row_count(db_conn) == 0


# -----------------------------------------------------------------------------
# Per-tick batch ceiling + multi-tick draining (L3-OBS-016)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-016")
async def test_batch_size_caps_rows_deleted_per_tick_and_drains_across_ticks(
    uow_factory: SqliteUnitOfWorkFactory,
    fake_clock_now: FakeClock,
    db_conn: aiosqlite.Connection,
) -> None:
    """cap=2 + 5 eligible rows SHALL drain 2-2-1 across three ticks."""
    capped_pruner = AuditLogPrunerUseCase(
        uow_factory=uow_factory,
        clock=fake_clock_now,
        retention_days=_RETENTION_DAYS,
        cleanup_batch_size=2,
    )
    base = _NOW - timedelta(days=_RETENTION_DAYS + 1)
    for i in range(5):
        await _seed_event(uow_factory, when=base - timedelta(seconds=i), actor=f"user:old{i}")
    assert await _row_count(db_conn) == 5

    tick1 = await capped_pruner.run_once()
    assert tick1.rows_deleted == 2
    assert await _row_count(db_conn) == 3

    tick2 = await capped_pruner.run_once()
    assert tick2.rows_deleted == 2
    assert await _row_count(db_conn) == 1

    tick3 = await capped_pruner.run_once()
    assert tick3.rows_deleted == 1
    assert await _row_count(db_conn) == 0

    tick4 = await capped_pruner.run_once()
    assert tick4.rows_deleted == 0


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-016")
async def test_oldest_first_ordering_within_batch(
    uow_factory: SqliteUnitOfWorkFactory,
    fake_clock_now: FakeClock,
    db_conn: aiosqlite.Connection,
) -> None:
    """The pruner SHALL evict the oldest rows first (per the inner ORDER BY)."""
    capped_pruner = AuditLogPrunerUseCase(
        uow_factory=uow_factory,
        clock=fake_clock_now,
        retention_days=_RETENTION_DAYS,
        cleanup_batch_size=2,
    )
    # Three eligible rows at different ages; only the two oldest SHALL go.
    await _seed_event(
        uow_factory,
        when=_NOW - timedelta(days=_RETENTION_DAYS + 10),
        actor="user:oldest",
    )
    await _seed_event(
        uow_factory,
        when=_NOW - timedelta(days=_RETENTION_DAYS + 5),
        actor="user:middle",
    )
    await _seed_event(
        uow_factory,
        when=_NOW - timedelta(days=_RETENTION_DAYS + 1),
        actor="user:youngest_eligible",
    )

    await capped_pruner.run_once()

    async with uow_factory() as uow:
        survivors = await uow.audit_log.query(limit=10)
    survivor_actors = [e.actor for e in survivors]
    assert survivor_actors == ["user:youngest_eligible"]


# -----------------------------------------------------------------------------
# Anti-recursion (L3-OBS-040)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-040")
async def test_pruner_emits_no_audit_row_for_its_own_delete(
    pruner: AuditLogPrunerUseCase,
    uow_factory: SqliteUnitOfWorkFactory,
    db_conn: aiosqlite.Connection,
) -> None:
    """The pruner SHALL NOT emit any audit row for its own delete activity.

    A self-referential audit (PRUNE_AUDIT_LOG action) would mean every
    prune tick adds a row that itself eventually needs pruning. Per
    L3-OBS-040 the pruner relies on its structured INFO log + the
    L3-OBS-039 sole-deleter conformance for forensic visibility.
    """
    # Seed an old row + a recent row so the prune actually runs.
    await _seed_event(
        uow_factory,
        when=_NOW - timedelta(days=_RETENTION_DAYS + 1),
        action=AuditAction.SWEEP_ORPHAN,
        actor="system:sweeper",
        resource="run:00000000-0000-4000-8000-000000000001",
    )
    await _seed_event(
        uow_factory,
        when=_NOW - timedelta(days=1),
        action=AuditAction.LOGIN,
        actor="user:1",
        resource="session:abc",
    )
    await pruner.run_once()

    # Surviving row should be the recent LOGIN; no PRUNE_AUDIT_LOG-style
    # row should exist (and the AuditAction enum has no such value).
    async with uow_factory() as uow:
        all_events = await uow.audit_log.query(limit=100)
    actor_set = {e.actor for e in all_events}
    assert "system:audit_log_pruner" not in actor_set
    assert "system:audit_pruner" not in actor_set
    # All actions on surviving rows are pre-existing categories.
    permitted_actions = {a.value for a in AuditAction}
    for event in all_events:
        assert event.action.value in permitted_actions
    assert all(e.action != AuditAction.LOGIN_FAILED for e in all_events)  # sanity
    # Row count: original LOGIN row plus no extras.
    assert await _row_count(db_conn) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-040")
async def test_no_prune_audit_log_action_in_audit_action_enum() -> None:
    """The AuditAction enum SHALL NOT include a PRUNE_AUDIT_LOG value.

    A static guarantee that L3-OBS-040's anti-recursion rule cannot
    be violated by a future caller: with no enum value, no record()
    call against the audit log can name the action.
    """
    forbidden = {"PRUNE_AUDIT_LOG", "AUDIT_PRUNED", "PRUNE_AUDIT", "AUDIT_LOG_PRUNED"}
    actual_values = {a.value for a in AuditAction}
    assert actual_values & forbidden == set(), (
        f"AuditAction must not include any of {forbidden}; found {actual_values & forbidden}"
    )
