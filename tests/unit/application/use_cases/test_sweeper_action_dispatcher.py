"""Tests for :class:`SweeperActionDispatcherUseCase`.

Drives the dispatcher through a real SQLite UoW with the migrations
applied, so claim-and-settle SQL participates in real transactions.
Disposition handlers are recording instances so we can assert
invocation counts, order, and the post-transition aggregate they
receive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.ports.disposition_handler import DispositionHandler
from message_service.application.use_cases.sweeper import SweeperUseCase
from message_service.application.use_cases.sweeper_action_dispatcher import (
    DispatchResult,
    SweeperActionDispatcherUseCase,
)
from message_service.config.schema import DispositionAction
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
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)

# -----------------------------------------------------------------------------
# Fixtures + helpers
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += timedelta(seconds=seconds)


class _RecordingHandler(DispositionHandler):
    """Captures the runs handed in. Optional ``raise_on`` toggles the
    L3-SWEEP-013 failure path so the dispatcher's error handling can be
    asserted."""

    def __init__(
        self,
        action_id_value: str,
        call_log: list[tuple[str, RunState]],
        *,
        should_raise: bool = False,
    ) -> None:
        self._id = action_id_value
        self._log = call_log
        self._raise = should_raise

    async def handle(self, run: Run) -> None:
        self._log.append((self._id, run.state))
        if self._raise:
            raise RuntimeError("handler boom")


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
    )


def _make_run(*, run_id: str, state: RunState, updated_at: datetime) -> Run:
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
        created_at=updated_at,
        updated_at=updated_at,
    )


async def _seed_orphan_with_actions(
    *,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    run_id: str,
    actions: list[DispositionAction],
    handlers_by_id: dict[DispositionAction, DispositionHandler],
) -> None:
    """Seed an active run, then run the sweeper to drive the orphan
    transition + outbox enqueue. Deliberately reuses the production
    SweeperUseCase so the dispatcher tests exercise the same shape of
    rows the sweeper actually produces."""
    stale = _make_run(
        run_id=run_id,
        state=RunState.AGGREGATING,
        updated_at=_T0 - timedelta(hours=2),
    )
    async with uow_factory() as uow:
        await uow.run_repo.save(stale)

    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=actions,
        handlers_by_id=handlers_by_id,
    )
    await sweeper.tick()


async def _row_state(
    sqlite_conn: aiosqlite.Connection, action_id: int
) -> tuple[str | None, str | None, int, str | None]:
    async with sqlite_conn.execute(
        "SELECT claimed_at, completed_at, attempts, last_error "
        "FROM sweeper_actions WHERE action_id = ?",
        (action_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1], int(row[2]), row[3]


# -----------------------------------------------------------------------------
# Empty-queue path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-013")
async def test_dispatch_empty_outbox_returns_zero_counts(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id={"NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", [])},
    )
    result = await dispatcher.dispatch_pending()
    assert result == DispatchResult(claimed=0, succeeded=0, failed=0)


# -----------------------------------------------------------------------------
# Happy path: claim → invoke → mark_completed
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-006")
async def test_dispatch_invokes_handler_and_stamps_completed(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """One pending action drains cleanly: handler runs, row stamped
    completed_at."""
    call_log: list[tuple[str, RunState]] = []
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000010",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
    )
    result = await dispatcher.dispatch_pending()

    assert result == DispatchResult(claimed=1, succeeded=1, failed=0)
    # Handler saw the run AFTER the ORPHANED transition (per L3-SWEEP-013
    # contract that the post-transition aggregate is what flows in).
    assert call_log == [("NOTIFY_ADMINS", RunState.ORPHANED)]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-015")
async def test_dispatch_invokes_handlers_in_enqueue_order(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Per L3-SWEEP-015 the configured action order is preserved at
    dispatch — ensured by the claim query's ``enqueued_at, action_id``
    ordering, since 14b.2 inserts in configured order."""
    call_log: list[tuple[str, RunState]] = []
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log),
        "DISCARD_SILENTLY": _RecordingHandler("DISCARD_SILENTLY", call_log),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000011",
        actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id=handlers,
    )

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
    )
    await dispatcher.dispatch_pending()

    assert [a for a, _ in call_log] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]


# -----------------------------------------------------------------------------
# Failure path: handler raises
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-013")
async def test_handler_failure_is_swallowed_and_recorded(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """L3-SWEEP-013: a raising handler SHALL NOT propagate. The
    dispatcher catches, logs, and stamps mark_failed (attempts++,
    last_error)."""
    call_log: list[tuple[str, RunState]] = []
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log, should_raise=True),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000012",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
    )
    result = await dispatcher.dispatch_pending()

    assert result == DispatchResult(claimed=1, succeeded=0, failed=1)

    async with sqlite_conn.execute("SELECT action_id FROM sweeper_actions") as cur:
        row = await cur.fetchone()
    assert row is not None
    claimed_at, completed_at, attempts, last_error = await _row_state(sqlite_conn, int(row[0]))
    assert claimed_at is not None
    assert completed_at is not None
    assert attempts == 1
    assert last_error == "handler boom"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-013")
async def test_one_handler_failure_does_not_block_siblings(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """Two actions enqueued; the first raises. The second SHALL still
    be invoked and stamped completed."""
    call_log: list[tuple[str, RunState]] = []
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log, should_raise=True),
        "DISCARD_SILENTLY": _RecordingHandler("DISCARD_SILENTLY", call_log),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000013",
        actions=["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
        handlers_by_id=handlers,
    )

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
    )
    result = await dispatcher.dispatch_pending()

    assert result == DispatchResult(claimed=2, succeeded=1, failed=1)
    assert [a for a, _ in call_log] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]


# -----------------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unregistered_handler_settles_as_failed(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """If an outbox row references an action whose handler has been
    removed since enqueue (e.g., bootstrap registry shrunk between
    runs), the row SHALL be stamped failed with a clear message rather
    than crash the dispatcher."""
    enqueue_handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000014",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=enqueue_handlers,
    )

    # Dispatcher built with an EMPTY handler registry — simulates the
    # post-config-change drift case.
    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id={},
    )
    result = await dispatcher.dispatch_pending()
    assert result == DispatchResult(claimed=1, succeeded=0, failed=1)

    async with sqlite_conn.execute("SELECT action_id, last_error FROM sweeper_actions") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "no handler registered" in str(row[1])


@pytest.mark.asyncio
async def test_dispatch_does_not_re_claim_settled_rows(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """A second tick with no new enqueues SHALL return zero — settled
    rows have ``claimed_at IS NOT NULL`` so the partial index does not
    surface them again. This is the no-double-dispatch contract that
    makes the outbox exactly-once."""
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000015",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
    )
    first = await dispatcher.dispatch_pending()
    assert first == DispatchResult(claimed=1, succeeded=1, failed=0)

    second = await dispatcher.dispatch_pending()
    assert second == DispatchResult(claimed=0, succeeded=0, failed=0)


# -----------------------------------------------------------------------------
# Constructor validation
# -----------------------------------------------------------------------------


def test_constructor_rejects_zero_batch_limit(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    with pytest.raises(ValueError, match="batch_limit"):
        SweeperActionDispatcherUseCase(
            uow_factory=uow_factory,
            clock=clock,
            handlers_by_id={},
            batch_limit=0,
        )


def test_constructor_rejects_zero_stale_threshold(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    with pytest.raises(ValueError, match="stale_claim_threshold_seconds"):
        SweeperActionDispatcherUseCase(
            uow_factory=uow_factory,
            clock=clock,
            handlers_by_id={},
            stale_claim_threshold_seconds=0,
        )


def test_constructor_rejects_zero_max_dispatch_attempts(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    with pytest.raises(ValueError, match="max_dispatch_attempts"):
        SweeperActionDispatcherUseCase(
            uow_factory=uow_factory,
            clock=clock,
            handlers_by_id={},
            max_dispatch_attempts=0,
        )


# -----------------------------------------------------------------------------
# Stuck-claim recovery (L3-SWEEP-020)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-020")
async def test_dispatcher_reclaims_stuck_rows_on_next_tick(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """L3-SWEEP-020: a row whose claim ages past stale_threshold gets
    reclaimed and the handler runs again. Simulates the
    crash-mid-handler scenario by manually claiming a row and then
    advancing the clock past the threshold."""
    call_log: list[tuple[str, RunState]] = []
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", call_log),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000020",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    # Simulate a crashed dispatcher: claim the row but DON'T settle.
    async with uow_factory() as uow:
        claimed = await uow.sweeper_action_repo.claim_pending(now=clock.now(), limit=10)
    assert len(claimed) == 1

    # Time passes. Clock advances 10 minutes — past 300s threshold.
    clock.advance(600)

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
        stale_claim_threshold_seconds=300,
        max_dispatch_attempts=3,
    )
    result = await dispatcher.dispatch_pending()

    # Reclaimed and dispatched cleanly.
    assert result.claimed == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert result.abandoned == 0
    # Handler did run on the reclaim attempt.
    assert call_log == [("NOTIFY_ADMINS", RunState.ORPHANED)]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-020")
async def test_dispatcher_does_not_reclaim_recently_claimed_rows(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """A row claimed under the stale threshold SHALL stay claimed —
    the dispatcher might still be processing it."""
    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000021",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    # Claim the row.
    async with uow_factory() as uow:
        await uow.sweeper_action_repo.claim_pending(now=clock.now(), limit=10)

    # Only 60 seconds advance (under the 300s threshold).
    clock.advance(60)

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
        stale_claim_threshold_seconds=300,
        max_dispatch_attempts=3,
    )
    result = await dispatcher.dispatch_pending()
    assert result.claimed == 0  # nothing reclaimed
    assert result.abandoned == 0


# -----------------------------------------------------------------------------
# Abandonment after retry exhaustion (L3-SWEEP-021)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-021")
async def test_dispatcher_abandons_rows_past_max_attempts(
    uow_factory: SqliteUnitOfWorkFactory,
    sqlite_conn: aiosqlite.Connection,
    clock: _FixedClock,
) -> None:
    """L3-SWEEP-021: a stuck row whose attempts has reached the cap
    SHALL be marked terminal and audited as abandoned, NOT retried
    again. Operators can find it via SWEEP_ORPHAN's sibling event."""
    from message_service.domain.aggregates.audit_event import AuditAction

    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000022",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    # Simulate a row that's already been retried 3 times: claim it,
    # then bump attempts to 3 manually.
    async with uow_factory() as uow:
        claimed = await uow.sweeper_action_repo.claim_pending(now=clock.now(), limit=10)
    aid = claimed[0].action_id
    await sqlite_conn.execute("UPDATE sweeper_actions SET attempts = 3 WHERE action_id = ?", (aid,))
    await sqlite_conn.commit()

    clock.advance(600)  # past stale threshold

    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
        stale_claim_threshold_seconds=300,
        max_dispatch_attempts=3,
    )
    result = await dispatcher.dispatch_pending()

    assert result.abandoned == 1
    assert result.claimed == 0  # not re-claimed; abandoned outright

    # Audit event was emitted.
    async with uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.DISPATCHER_ACTION_ABANDONED)
    assert len(events) == 1
    evt = events[0]
    assert evt.actor == "system:sweeper_action_dispatcher"
    assert evt.resource == f"sweeper_action:{aid}"
    assert evt.outcome.value == "FAILURE"
    assert evt.details["attempts"] == 3
    assert evt.details["max_attempts"] == 3

    # Row marked terminal.
    async with sqlite_conn.execute(
        "SELECT completed_at, attempts, last_error FROM sweeper_actions WHERE action_id = ?",
        (aid,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None  # completed_at set
    assert row[1] == 3  # attempts NOT bumped (mark_abandoned doesn't bump)
    assert "abandoned after 3 attempts" in str(row[2])


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-021")
async def test_abandoned_rows_are_not_re_abandoned(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """A row that's already abandoned (completed_at set) SHALL NOT
    surface as a new abandonment on subsequent ticks — the
    abandonment audit fires exactly once per action_id."""
    from message_service.domain.aggregates.audit_event import AuditAction

    handlers: dict[DispositionAction, DispositionHandler] = {
        "NOTIFY_ADMINS": _RecordingHandler("NOTIFY_ADMINS", []),
    }
    await _seed_orphan_with_actions(
        uow_factory=uow_factory,
        clock=clock,
        run_id="00000000-0000-4000-8000-000000000023",
        actions=["NOTIFY_ADMINS"],
        handlers_by_id=handlers,
    )

    # Push to abandoned state.
    async with uow_factory() as uow:
        claimed = await uow.sweeper_action_repo.claim_pending(now=clock.now(), limit=10)
    aid = claimed[0].action_id
    async with uow_factory() as uow:
        await uow.sweeper_action_repo.mark_abandoned(
            action_id=aid, completed_at=clock.now(), error_message="exhausted"
        )

    clock.advance(600)
    dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers,
        stale_claim_threshold_seconds=300,
        max_dispatch_attempts=3,
    )

    # Two ticks back to back.
    r1 = await dispatcher.dispatch_pending()
    r2 = await dispatcher.dispatch_pending()
    assert r1.abandoned == 0  # already abandoned; not re-abandoned
    assert r2.abandoned == 0
    async with uow_factory() as uow:
        events = await uow.audit_log.query(action=AuditAction.DISPATCHER_ACTION_ABANDONED)
    assert events == []  # no abandonment audit fired
