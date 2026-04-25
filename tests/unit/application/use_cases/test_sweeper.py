"""Tests for :class:`SweeperUseCase`.

These exercise the use case through a real SQLite UoW so the
transition + audit atomicity is genuine, not mocked. Disposition
handlers are recording mocks so we can assert invocation order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.ports.disposition_handler import DispositionHandler
from message_service.application.use_cases.sweeper import SweeperUseCase, TickResult
from message_service.config.schema import DispositionAction
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import ConfigurationError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState
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


# -----------------------------------------------------------------------------
# Fixtures + helpers
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    """Manually-advanceable clock for deterministic age calculations."""

    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += timedelta(seconds=seconds)


class _RecordingHandler(DispositionHandler):
    """Records (action_id, run_id) on every call for assertion.

    Tests construct multiple recording handlers with different ids.
    ``DispositionHandler.action_id`` is a :class:`ClassVar`, so we
    stash the test-configured id on a plain instance attribute
    (``_id``) rather than shadowing the class variable — mypy's
    "cannot assign to class variable via instance" rule applies to
    the latter.
    """

    def __init__(
        self,
        action_id_value: str,
        call_log: list[tuple[str, str]],
        *,
        should_raise: bool = False,
    ) -> None:
        self._id = action_id_value
        self._log = call_log
        self._raise = should_raise

    async def handle(self, run: Run) -> None:
        self._log.append((self._id, str(run.run_id)))
        if self._raise:
            raise RuntimeError("handler failed on purpose")


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
    return _FixedClock(_T0)


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


def _make_run(
    *,
    run_id: str,
    state: RunState,
    created_at: datetime,
    updated_at: datetime | None = None,
) -> Run:
    """Build a Run aggregate with sensible defaults."""
    return Run(
        run_id=RunId(run_id),
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="frag", version="1.0"),
            ),
        ),
        state=state,
        attachment_mode=AttachmentMode.PER_STAGE,
        aggregation_template_ref=None,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )


async def _seed_run(uow_factory: SqliteUnitOfWorkFactory, run: Run) -> None:
    async with uow_factory() as uow:
        await uow.run_repo.save(run)


# -----------------------------------------------------------------------------
# No-orphans case
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SWEEP-001")
async def test_tick_with_empty_repo_returns_zero_counts(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()
    assert result == TickResult(orphaned_count=0, enqueued_actions=0)


@pytest.mark.asyncio
async def test_tick_ignores_non_expired_runs(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    # Created a minute ago, still well within the 1-hour timeout.
    run = _make_run(
        run_id="00000000-0000-4000-8000-000000000001",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(minutes=1),
        updated_at=_T0 - timedelta(seconds=30),
    )
    await _seed_run(uow_factory, run)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()
    assert result.orphaned_count == 0


@pytest.mark.asyncio
async def test_tick_ignores_terminal_runs(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """A SENT run older than the cutoff SHALL NOT be swept."""
    old_but_sent = _make_run(
        run_id="00000000-0000-4000-8000-000000000099",
        state=RunState.SENT,
        created_at=_T0 - timedelta(hours=3),
        updated_at=_T0 - timedelta(hours=2),
    )
    await _seed_run(uow_factory, old_but_sent)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()
    assert result.orphaned_count == 0


# -----------------------------------------------------------------------------
# Orphan detection + transition + audit
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
async def test_tick_transitions_expired_run_to_orphaned(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """An expired, active run SHALL be transitioned to ORPHANED."""
    run = _make_run(
        run_id="00000000-0000-4000-8000-0000000000aa",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(hours=5),
        updated_at=_T0 - timedelta(hours=2),  # 2h old > 1h timeout
    )
    await _seed_run(uow_factory, run)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()
    assert result.orphaned_count == 1

    # Verify persisted state + updated_at is the clock's "now".
    async with uow_factory() as uow:
        reloaded = await uow.run_repo.get(run.run_id)
    assert reloaded.state is RunState.ORPHANED
    assert reloaded.updated_at == _T0


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
@pytest.mark.requirement("L3-OBS-030")
async def test_tick_records_sweep_orphan_audit_event(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Every ORPHANED transition SHALL be accompanied by a SWEEP_ORPHAN audit."""
    run = _make_run(
        run_id="00000000-0000-4000-8000-0000000000bb",
        state=RunState.INITIATED,
        created_at=_T0 - timedelta(hours=5),
        updated_at=_T0 - timedelta(hours=3),
    )
    await _seed_run(uow_factory, run)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    await sweeper.tick()

    async with uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.SWEEP_ORPHAN)
    assert len(events) == 1
    evt = events[0]
    assert evt.actor == "system:sweeper"
    assert evt.resource == f"run:{run.run_id}"
    assert evt.details["prior_state"] == "INITIATED"
    assert evt.details["new_state"] == "ORPHANED"
    # L3-STAGE-013: audit details include pending_stage_ids list
    # (empty here — no stage rows seeded for this run).
    assert evt.details["pending_stage_ids"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-013")
async def test_audit_records_pending_stage_ids_when_present(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """When the orphaning run has PENDING stages, their ids SHALL appear
    sorted in the SWEEP_ORPHAN audit record's pending_stage_ids field
    (per L3-STAGE-013 / L2-STAGE-007). Operators investigating an
    orphan can then identify which stages were the cause."""
    run = _make_run(
        run_id="00000000-0000-4000-8000-0000000000ee",
        state=RunState.INITIATED,
        created_at=_T0 - timedelta(hours=5),
        updated_at=_T0 - timedelta(hours=3),
    )
    await _seed_run(uow_factory, run)

    # Seed three PENDING stages (deliberately out of alphabetical order
    # to exercise the sorted-output guarantee).
    pending_ids = ["transform", "extract", "load"]
    async with uow_factory() as uow:
        for sid in pending_ids:
            await uow.stage_repo.save(
                Stage(
                    run_id=run.run_id,
                    stage_id=StageId(sid),
                    state=StageState.PENDING,
                    report_template_ref=TemplateRef(name="frag", version="1.0"),
                )
            )

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    await sweeper.tick()

    async with uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.SWEEP_ORPHAN)
    assert len(events) == 1
    assert events[0].details["pending_stage_ids"] == sorted(pending_ids)


@pytest.mark.asyncio
async def test_tick_sweeps_multiple_runs_independently(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    for i, state in enumerate([RunState.INITIATED, RunState.AGGREGATING, RunState.READY]):
        await _seed_run(
            uow_factory,
            _make_run(
                run_id=f"00000000-0000-4000-8000-00000000100{i}",
                state=state,
                created_at=_T0 - timedelta(hours=3),
                updated_at=_T0 - timedelta(hours=2),
            ),
        )

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()
    assert result.orphaned_count == 3


# -----------------------------------------------------------------------------
# Outbox enqueue (L2-SWEEP-006, L3-SWEEP-010)
# -----------------------------------------------------------------------------


async def _outbox_rows(
    sqlite_conn: aiosqlite.Connection, run_id: str
) -> list[tuple[str, str | None, str | None]]:
    """Return ``(action_name, claimed_at, completed_at)`` for the run's rows
    in insert order."""
    async with sqlite_conn.execute(
        "SELECT action_name, claimed_at, completed_at "
        "FROM sweeper_actions WHERE run_id = ? ORDER BY action_id",
        (run_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-010")
async def test_actions_enqueued_in_config_order(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """One outbox row per configured action, inserted in configured order
    (L2-SWEEP-009 / L3-SWEEP-015), all in the pending state (claimed_at /
    completed_at NULL)."""
    run_id = "00000000-0000-4000-8000-00000000000a"
    await _seed_run(
        uow_factory,
        _make_run(
            run_id=run_id,
            state=RunState.AGGREGATING,
            created_at=_T0 - timedelta(hours=3),
            updated_at=_T0 - timedelta(hours=2),
        ),
    )

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id={
            "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
            "DISCARD_SILENTLY": _RecordingHandler("DISCARD_SILENTLY", []),
        },
    )
    result = await sweeper.tick()

    assert result.orphaned_count == 1
    assert result.enqueued_actions == 2

    rows = await _outbox_rows(sqlite_conn, run_id)
    assert [r[0] for r in rows] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]
    # All pending: claimed_at and completed_at NULL.
    for _, claimed_at, completed_at in rows:
        assert claimed_at is None
        assert completed_at is None


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
async def test_handlers_are_not_invoked_in_tick(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """The tick path SHALL NOT invoke disposition handlers — that work is
    deferred to the outbox dispatcher (14b.3). A handler instance passed
    into the constructor MUST never have ``handle`` called from a tick."""
    await _seed_run(
        uow_factory,
        _make_run(
            run_id="00000000-0000-4000-8000-00000000000b",
            state=RunState.AGGREGATING,
            created_at=_T0 - timedelta(hours=3),
            updated_at=_T0 - timedelta(hours=2),
        ),
    )

    call_log: list[tuple[str, str]] = []
    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id={
            "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log),
            "DISCARD_SILENTLY": _RecordingHandler("DISCARD_SILENTLY", call_log),
        },
    )
    await sweeper.tick()

    assert call_log == []


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
async def test_failed_enqueue_rolls_back_orphan_transition(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """If an outbox insert fails inside the orphan UoW, the entire
    transaction (transition + audit + earlier action rows) SHALL roll back.

    Simulated by passing an action_name that the DB CHECK constraint
    rejects. The constructor's startup validation passes because the
    handler is registered; the failure happens at INSERT time inside the
    UoW. Exactly-once requires this to leave NO partial state behind.
    """
    run = _make_run(
        run_id="00000000-0000-4000-8000-00000000000c",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(hours=3),
        updated_at=_T0 - timedelta(hours=2),
    )
    await _seed_run(uow_factory, run)

    bogus: DispositionAction = "DEFINITELY_NOT_AN_ACTION"  # type: ignore[assignment]
    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[bogus],
        handlers_by_id={bogus: _RecordingHandler("ignored", [])},
    )
    with pytest.raises(Exception):  # noqa: B017 — aiosqlite.IntegrityError or wrapped variant
        await sweeper.tick()

    # ORPHANED transition rolled back: run is still in its pre-sweep state.
    async with uow_factory() as uow:
        reloaded = await uow.run_repo.get(run.run_id)
    assert reloaded.state is RunState.AGGREGATING
    # No audit row written.
    async with uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.SWEEP_ORPHAN)
    assert events == []
    # No outbox rows persisted.
    rows = await _outbox_rows(sqlite_conn, str(run.run_id))
    assert rows == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-011")
async def test_empty_disposition_actions_still_transitions_to_orphaned(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """Empty ``disposition_actions`` is permitted (L3-SWEEP-011).

    The orphaned run still gets the state transition and audit; no
    outbox rows are written. Equivalent in effect to a single
    ``DISCARD_SILENTLY``.
    """
    run = _make_run(
        run_id="00000000-0000-4000-8000-00000000000d",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(hours=3),
        updated_at=_T0 - timedelta(hours=2),
    )
    await _seed_run(uow_factory, run)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()

    assert result.orphaned_count == 1
    assert result.enqueued_actions == 0

    async with uow_factory() as uow:
        reloaded = await uow.run_repo.get(run.run_id)
    assert reloaded.state is RunState.ORPHANED

    rows = await _outbox_rows(sqlite_conn, str(run.run_id))
    assert rows == []


# -----------------------------------------------------------------------------
# Configuration validation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SWEEP-019")
def test_constructor_rejects_action_without_registered_handler(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Known DispositionAction id without registered handler → startup ConfigurationError.

    Distinct from L3-SWEEP-012 which covers ids unknown to the type
    system (caught by Pydantic). This pins the case where the id IS
    valid in the Literal but bootstrap doesn't ship a handler — the
    v1 boundary established by Increment 14a for SEND_PARTIAL_FLAGGED
    and NOTIFY_SUBSCRIBERS.
    """
    with pytest.raises(ConfigurationError, match="no handler registered") as exc_info:
        SweeperUseCase(
            uow_factory=uow_factory,
            clock=clock,
            run_timeout_seconds=3600,
            disposition_actions=["SEND_PARTIAL_FLAGGED"],
            handlers_by_id={},  # no handler for SEND_PARTIAL_FLAGGED
        )
    assert exc_info.value.details["missing_actions"] == ["SEND_PARTIAL_FLAGGED"]
    assert exc_info.value.details["registered_actions"] == []


def test_constructor_rejects_zero_max_candidates(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """``max_candidates_per_iteration < 1`` is a programming error;
    SHALL raise ValueError."""
    with pytest.raises(ValueError, match="max_candidates_per_iteration"):
        SweeperUseCase(
            uow_factory=uow_factory,
            clock=clock,
            run_timeout_seconds=3600,
            disposition_actions=[],
            handlers_by_id={},
            max_candidates_per_iteration=0,
        )


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-010")
async def test_backlog_drains_across_multiple_ticks(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L2-SWEEP-010: a backlog larger than ``max_candidates_per_iteration``
    SHALL drain across multiple ticks, not in one. Three orphan candidates
    + cap of 2 → tick 1 sweeps 2, tick 2 sweeps the third, tick 3 sees zero."""
    older = _T0 - timedelta(hours=2)
    for i in range(3):
        await _seed_run(
            uow_factory,
            _make_run(
                run_id=f"00000000-0000-4000-8000-0000000002{i:02d}",
                state=RunState.AGGREGATING,
                created_at=older,
                updated_at=older,
            ),
        )

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
        max_candidates_per_iteration=2,  # cap is 2 < 3 candidates seeded
    )

    first = await sweeper.tick()
    assert first.orphaned_count == 2  # capped

    second = await sweeper.tick()
    assert second.orphaned_count == 1  # remaining one drains

    third = await sweeper.tick()
    assert third.orphaned_count == 0  # backlog cleared


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-017")
async def test_tick_classifies_run_at_exact_timeout_as_orphan(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L3-SWEEP-017 / L1-SWEEP-002 inclusive boundary: a run whose
    elapsed time is exactly ``run_timeout_seconds`` SHALL orphan on
    the very next tick — not after one additional polling interval.

    Use-case-level mirror of the repository-level
    ``test_list_expired_inclusive_boundary``. Catches any future
    regression that re-introduces the pre-14f off-by-one in the SQL
    or any client-side cutoff arithmetic.
    """
    timeout_seconds = 3600
    # Run last transitioned EXACTLY run_timeout_seconds ago relative
    # to the clock's "now" — the inclusive-boundary case.
    run = _make_run(
        run_id="00000000-0000-4000-8000-0000000000bb",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(seconds=timeout_seconds),
        updated_at=_T0 - timedelta(seconds=timeout_seconds),
    )
    await _seed_run(uow_factory, run)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=timeout_seconds,
        disposition_actions=[],
        handlers_by_id={},
    )
    result = await sweeper.tick()

    assert result.orphaned_count == 1  # boundary run swept; no extra tick needed
    async with uow_factory() as uow:
        reloaded = await uow.run_repo.get(run.run_id)
    assert reloaded.state is RunState.ORPHANED
