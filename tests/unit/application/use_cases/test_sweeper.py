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
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)

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
    assert result == TickResult(0, 0, 0)


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
# Handler dispatch order + invocation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-009")
async def test_handlers_dispatched_in_config_order(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Handlers SHALL be invoked in the order they appear in the configured list."""
    await _seed_run(
        uow_factory,
        _make_run(
            run_id="00000000-0000-4000-8000-00000000000a",
            state=RunState.AGGREGATING,
            created_at=_T0 - timedelta(hours=3),
            updated_at=_T0 - timedelta(hours=2),
        ),
    )

    call_log: list[tuple[str, str]] = []
    h_a = _RecordingHandler("NOTIFY_ADMINS", call_log)
    h_b = _RecordingHandler("DISCARD_SILENTLY", call_log)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id={"NOTIFY_ADMINS": h_a, "DISCARD_SILENTLY": h_b},
    )
    result = await sweeper.tick()

    assert result.dispatched_actions == 2
    assert [action for action, _ in call_log] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]


@pytest.mark.asyncio
async def test_handler_failure_does_not_stop_later_handlers(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """If one handler raises, subsequent handlers SHALL still be invoked."""
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
    h_fail = _RecordingHandler("NOTIFY_ADMINS", call_log, should_raise=True)
    h_ok = _RecordingHandler("DISCARD_SILENTLY", call_log)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id={"NOTIFY_ADMINS": h_fail, "DISCARD_SILENTLY": h_ok},
    )
    result = await sweeper.tick()

    assert result.orphaned_count == 1
    assert result.dispatched_actions == 1
    assert result.handler_failures == 1
    # Both handlers were called, even though the first raised.
    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_handler_failure_does_not_roll_back_orphan_transition(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Even when a handler raises, the ORPHANED transition SHALL remain committed."""
    run = _make_run(
        run_id="00000000-0000-4000-8000-00000000000c",
        state=RunState.AGGREGATING,
        created_at=_T0 - timedelta(hours=3),
        updated_at=_T0 - timedelta(hours=2),
    )
    await _seed_run(uow_factory, run)

    call_log: list[tuple[str, str]] = []
    h_fail = _RecordingHandler("NOTIFY_ADMINS", call_log, should_raise=True)
    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=["NOTIFY_ADMINS"],
        handlers_by_id={"NOTIFY_ADMINS": h_fail},
    )
    await sweeper.tick()

    async with uow_factory() as uow:
        reloaded = await uow.run_repo.get(run.run_id)
    assert reloaded.state is RunState.ORPHANED


# -----------------------------------------------------------------------------
# Configuration validation
# -----------------------------------------------------------------------------


def test_constructor_rejects_action_without_registered_handler(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    with pytest.raises(ValueError, match="no handler registered"):
        SweeperUseCase(
            uow_factory=uow_factory,
            clock=clock,
            run_timeout_seconds=3600,
            disposition_actions=["SEND_PARTIAL_FLAGGED"],
            handlers_by_id={},  # no handler for SEND_PARTIAL_FLAGGED
        )
