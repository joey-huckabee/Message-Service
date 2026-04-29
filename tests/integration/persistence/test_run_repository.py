"""Unit tests for :class:`SqliteRunRepository`.

Uses a real in-memory SQLite DB so the tests exercise the actual SQL
and JSON round-tripping — this is the "integration-ish" layer that
proves our port contract implementation matches what use cases expect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import PersistenceError, RunNotFoundError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 21, 13, 0, 0, tzinfo=UTC)


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
async def repo(conn: aiosqlite.Connection) -> SqliteRunRepository:
    return SqliteRunRepository(conn)


def _make_run(
    *,
    run_id: str = "00000000-0000-4000-8000-000000000001",
    state: RunState = RunState.INITIATED,
    attachment_mode: AttachmentMode = AttachmentMode.SINGLE_AGGREGATED,
    tags: frozenset[str] = frozenset({"prod", "critical"}),
    declared_stages: tuple[DeclaredStage, ...] | None = None,
    created_at: datetime = _T0,
    updated_at: datetime | None = None,
) -> Run:
    if declared_stages is None:
        declared_stages = (
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="r", version="1.0"),
            ),
            DeclaredStage(
                stage_id=StageId("transform"),
                stage_order=1,
                report_template_ref=TemplateRef(name="r", version="1.0"),
            ),
        )
    agg_ref = (
        TemplateRef(name="agg", version="2.0")
        if attachment_mode is AttachmentMode.SINGLE_AGGREGATED
        else None
    )
    return Run(
        run_id=RunId(run_id),
        pipeline_type="etl-nightly",
        tags=tags,
        declared_stages=declared_stages,
        state=state,
        attachment_mode=attachment_mode,
        aggregation_template_ref=agg_ref,
        subscription_predicate_tags=tags,
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )


# -----------------------------------------------------------------------------
# Happy-path round-trip
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-PERS-003")
async def test_save_then_get_returns_identical_run(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run()
    await repo.save(run)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded == run


@pytest.mark.asyncio
async def test_per_stage_mode_persists_null_aggregation_template(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run(attachment_mode=AttachmentMode.PER_STAGE)
    await repo.save(run)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded.aggregation_template_ref is None
    assert loaded.attachment_mode is AttachmentMode.PER_STAGE


@pytest.mark.asyncio
async def test_empty_tags_persist_and_round_trip(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run(tags=frozenset())
    await repo.save(run)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded.tags == frozenset()


@pytest.mark.asyncio
async def test_empty_declared_stages_round_trip(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run(declared_stages=())
    await repo.save(run)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded.declared_stages == ()


@pytest.mark.asyncio
async def test_declared_stages_order_preserved(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L1-AGGR-003: stable ordering across persistence."""
    # Z before A by stage_id but Z has stage_order=0, A has stage_order=1.
    # The tuple-order in declared_stages is what we expect back.
    declared = (
        DeclaredStage(
            stage_id=StageId("z_stage"),
            stage_order=0,
            report_template_ref=TemplateRef(name="r", version="1.0"),
        ),
        DeclaredStage(
            stage_id=StageId("a_stage"),
            stage_order=1,
            report_template_ref=TemplateRef(name="r", version="1.0"),
        ),
    )
    run = _make_run(declared_stages=declared)
    await repo.save(run)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded.declared_stages == declared  # identity, not just set equality


# -----------------------------------------------------------------------------
# get() misses
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_on_missing_run_raises_run_not_found(
    repo: SqliteRunRepository,
) -> None:
    with pytest.raises(RunNotFoundError) as exc_info:
        await repo.get(RunId("00000000-0000-4000-8000-000000000999"))
    assert exc_info.value.details["run_id"].endswith("999")


# -----------------------------------------------------------------------------
# Upsert (save idempotent)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_same_run_twice_is_upsert(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run()
    await repo.save(run)
    await repo.save(run)  # must not raise
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded == run


@pytest.mark.asyncio
async def test_save_overwrites_prior_fields(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    original = _make_run(tags=frozenset({"old"}))
    await repo.save(original)
    updated = _make_run(
        tags=frozenset({"new1", "new2"}),
        state=RunState.AGGREGATING,
        updated_at=_T1,
    )
    await repo.save(updated)
    await conn.commit()

    loaded = await repo.get(original.run_id)
    assert loaded.tags == frozenset({"new1", "new2"})
    assert loaded.state is RunState.AGGREGATING
    assert loaded.updated_at == _T1


# -----------------------------------------------------------------------------
# update_state
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_state_changes_state_and_updated_at(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    run = _make_run()
    await repo.save(run)
    await conn.commit()

    await repo.update_state(run.run_id, RunState.AGGREGATING, _T1)
    await conn.commit()

    loaded = await repo.get(run.run_id)
    assert loaded.state is RunState.AGGREGATING
    assert loaded.updated_at == _T1
    assert loaded.created_at == _T0  # untouched


@pytest.mark.asyncio
async def test_update_state_on_missing_run_raises_not_found(
    repo: SqliteRunRepository,
) -> None:
    with pytest.raises(RunNotFoundError):
        await repo.update_state(
            RunId("00000000-0000-4000-8000-000000000999"),
            RunState.AGGREGATING,
            _T1,
        )


# -----------------------------------------------------------------------------
# list_in_states
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_in_states_filters_correctly(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    await repo.save(_make_run(run_id="00000000-0000-4000-8000-000000000001"))
    await repo.save(
        _make_run(
            run_id="00000000-0000-4000-8000-000000000002",
            state=RunState.AGGREGATING,
        )
    )
    await repo.save(
        _make_run(
            run_id="00000000-0000-4000-8000-000000000003",
            state=RunState.SENT,
        )
    )
    await conn.commit()

    initiated = await repo.list_in_states(frozenset({RunState.INITIATED}))
    assert {r.run_id for r in initiated} == {"00000000-0000-4000-8000-000000000001"}

    active = await repo.list_in_states(frozenset({RunState.INITIATED, RunState.AGGREGATING}))
    assert {r.run_id for r in active} == {
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    }


@pytest.mark.asyncio
async def test_list_in_states_empty_input_returns_empty(
    repo: SqliteRunRepository,
) -> None:
    result = await repo.list_in_states(frozenset())
    assert list(result) == []


# -----------------------------------------------------------------------------
# list_expired
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-004")
@pytest.mark.requirement("L3-SWEEP-006")
async def test_list_expired_returns_runs_older_than_cutoff_in_active_states(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    old_initiated = _make_run(
        run_id="00000000-0000-4000-8000-000000000001",
        created_at=_T0,
        state=RunState.INITIATED,
    )
    recent_aggregating = _make_run(
        run_id="00000000-0000-4000-8000-000000000002",
        created_at=_T0 + timedelta(hours=2),
        state=RunState.AGGREGATING,
    )
    old_but_sent = _make_run(
        run_id="00000000-0000-4000-8000-000000000003",
        created_at=_T0,
        state=RunState.SENT,
    )
    for r in (old_initiated, recent_aggregating, old_but_sent):
        await repo.save(r)
    await conn.commit()

    cutoff = _T0 + timedelta(hours=1)
    expired = await repo.list_expired(
        cutoff=cutoff,
        active_states=frozenset(
            {RunState.INITIATED, RunState.AGGREGATING, RunState.READY, RunState.SENDING}
        ),
        limit=100,
    )
    expired_ids = [r.run_id for r in expired]
    # Only the one both older than cutoff and in an active state.
    assert expired_ids == ["00000000-0000-4000-8000-000000000001"]


@pytest.mark.asyncio
@pytest.mark.requirement("L2-SWEEP-004")
@pytest.mark.requirement("L3-SWEEP-006")
async def test_list_expired_uses_updated_at_not_created_at(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """A run created long ago but with a recent transition SHALL NOT be
    treated as expired.

    L2-SWEEP-004 mandates the comparison is against ``updated_at``, not
    ``created_at``. A long-lived run that just transitioned is healthy.
    """
    # Created 3 hours ago, but transitioned 30 minutes ago.
    long_running = _make_run(
        run_id="00000000-0000-4000-8000-0000000000aa",
        created_at=_T0 - timedelta(hours=3),
        updated_at=_T0 - timedelta(minutes=30),
        state=RunState.AGGREGATING,
    )
    await repo.save(long_running)
    await conn.commit()

    # Cutoff is 1 hour ago — older than created_at but younger than
    # updated_at. If the SQL used created_at, this run would match.
    cutoff = _T0 - timedelta(hours=1)
    expired = await repo.list_expired(
        cutoff=cutoff,
        active_states=frozenset({RunState.AGGREGATING}),
        limit=100,
    )
    assert list(expired) == []


@pytest.mark.asyncio
async def test_list_expired_empty_active_states_returns_empty(
    repo: SqliteRunRepository,
) -> None:
    result = await repo.list_expired(cutoff=_T0, active_states=frozenset(), limit=100)
    assert list(result) == []


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-017")
async def test_list_expired_inclusive_boundary(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L3-SWEEP-017 / L1-SWEEP-002: a run whose ``updated_at`` is
    *exactly* the cutoff SHALL be classified as orphaned (inclusive
    boundary, not strict ``<``). The off-by-one team finding from
    Increment 14f.
    """
    boundary = _T0 - timedelta(hours=1)  # cutoff
    # Three runs: one strictly newer than cutoff (younger), one EXACTLY
    # at the cutoff (boundary case), one strictly older.
    await repo.save(
        _make_run(
            run_id="00000000-0000-4000-8000-0000000000a1",
            created_at=boundary + timedelta(seconds=1),
            updated_at=boundary + timedelta(seconds=1),  # 1s newer than cutoff
            state=RunState.AGGREGATING,
        )
    )
    await repo.save(
        _make_run(
            run_id="00000000-0000-4000-8000-0000000000a2",
            created_at=boundary,
            updated_at=boundary,  # EXACTLY at cutoff
            state=RunState.AGGREGATING,
        )
    )
    await repo.save(
        _make_run(
            run_id="00000000-0000-4000-8000-0000000000a3",
            created_at=boundary - timedelta(seconds=1),
            updated_at=boundary - timedelta(seconds=1),  # 1s older
            state=RunState.AGGREGATING,
        )
    )
    await conn.commit()

    expired = await repo.list_expired(
        cutoff=boundary,
        active_states=frozenset({RunState.AGGREGATING}),
        limit=100,
    )
    expired_ids = sorted(str(r.run_id) for r in expired)
    # Boundary case (a2) AND strictly older (a3) both surface; the
    # newer one (a1) does not.
    assert expired_ids == [
        "00000000-0000-4000-8000-0000000000a2",
        "00000000-0000-4000-8000-0000000000a3",
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-007")
async def test_list_expired_state_filter_includes_only_active_states(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L3-SWEEP-007: the SQL state-IN clause SHALL match exactly the
    four active states (INITIATED, AGGREGATING, READY, SENDING).
    Verified by seeding one expired run per state across the full
    state machine and confirming only the four active ones surface."""
    cutoff = _T0 + timedelta(hours=1)  # everything older than this is expired
    older = _T0 - timedelta(hours=2)

    states_seeded = [
        RunState.INITIATED,
        RunState.AGGREGATING,
        RunState.READY,
        RunState.SENDING,
        RunState.SENT,
        RunState.FAILED,
        RunState.ORPHANED,
    ]
    for i, state in enumerate(states_seeded):
        await repo.save(
            _make_run(
                run_id=f"00000000-0000-4000-8000-0000000000{i:02d}",
                created_at=older,
                updated_at=older,
                state=state,
            )
        )
    await conn.commit()

    expired = await repo.list_expired(
        cutoff=cutoff,
        active_states=frozenset(
            {RunState.INITIATED, RunState.AGGREGATING, RunState.READY, RunState.SENDING}
        ),
        limit=100,
    )
    surfaced_states = sorted(r.state.value for r in expired)
    assert surfaced_states == sorted(["AGGREGATING", "INITIATED", "READY", "SENDING"]), (
        f"unexpected states surfaced: {surfaced_states}"
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-SWEEP-008")
async def test_list_expired_honors_limit_parameter(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L3-SWEEP-008: the SQL LIMIT clause SHALL bound the row count.
    Seed N+1 expired runs, request limit=N, expect exactly N rows."""
    cutoff = _T0 + timedelta(hours=1)
    older = _T0 - timedelta(hours=2)
    n_seed = 5
    for i in range(n_seed + 1):
        await repo.save(
            _make_run(
                run_id=f"00000000-0000-4000-8000-0000000001{i:02d}",
                created_at=older,
                updated_at=older,
                state=RunState.AGGREGATING,
            )
        )
    await conn.commit()

    expired = await repo.list_expired(
        cutoff=cutoff,
        active_states=frozenset({RunState.AGGREGATING}),
        limit=n_seed,
    )
    assert len(list(expired)) == n_seed


@pytest.mark.asyncio
async def test_list_expired_rejects_zero_limit(repo: SqliteRunRepository) -> None:
    """``limit < 1`` is a programming error; SHALL raise ValueError."""
    with pytest.raises(ValueError, match="limit"):
        await repo.list_expired(
            cutoff=_T0, active_states=frozenset({RunState.AGGREGATING}), limit=0
        )


# -----------------------------------------------------------------------------
# Corrupt-data handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_with_unknown_state_raises_persistence_error(
    conn: aiosqlite.Connection,
) -> None:
    """If the state column holds an unknown enum value (CHECK bypassed
    in testing), the row-to-aggregate mapper SHALL raise."""
    # Insert a row bypassing CHECK by temporarily dropping the CHECK
    # via direct table insertion with a valid state, then corrupt it
    # via UPDATE which is not re-checked in SQLite for CHECK
    # constraints on existing rows by default — actually it IS. So we
    # disable FK and CHECK via PRAGMA ignore_check_constraints.
    await conn.execute("PRAGMA ignore_check_constraints = 1")
    await conn.execute(
        """
        INSERT INTO runs (
            run_id, pipeline_type, state, attachment_mode,
            aggregation_template_name, aggregation_template_version,
            tags_json, declared_stages_json, subscription_predicate_tags_json,
            created_at, updated_at
        ) VALUES (
            '00000000-0000-4000-8000-00000000bad1', 'etl', 'NOT_A_STATE', 'PER_STAGE',
            NULL, NULL,
            '[]', '[]', '[]',
            '2026-04-21T00:00:00Z', '2026-04-21T00:00:00Z'
        )
        """
    )
    await conn.commit()
    await conn.execute("PRAGMA ignore_check_constraints = 0")

    repo = SqliteRunRepository(conn)
    with pytest.raises(PersistenceError, match="unknown state"):
        await repo.get(RunId("00000000-0000-4000-8000-00000000bad1"))


@pytest.mark.asyncio
async def test_row_with_malformed_json_raises_persistence_error(
    conn: aiosqlite.Connection,
) -> None:
    await conn.execute(
        """
        INSERT INTO runs (
            run_id, pipeline_type, state, attachment_mode,
            aggregation_template_name, aggregation_template_version,
            tags_json, declared_stages_json, subscription_predicate_tags_json,
            created_at, updated_at
        ) VALUES (
            '00000000-0000-4000-8000-00000000bad2', 'etl', 'INITIATED', 'PER_STAGE',
            NULL, NULL,
            'not-valid-json', '[]', '[]',
            '2026-04-21T00:00:00Z', '2026-04-21T00:00:00Z'
        )
        """
    )
    await conn.commit()

    repo = SqliteRunRepository(conn)
    with pytest.raises(PersistenceError, match="decode persisted JSON"):
        await repo.get(RunId("00000000-0000-4000-8000-00000000bad2"))


# -----------------------------------------------------------------------------
# list_paginated (Increment 19a)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-024")
async def test_list_paginated_orders_most_recent_first(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L3-DASH-024: results SHALL be ordered by created_at DESC."""
    runs = [
        _make_run(
            run_id=f"00000000-0000-4000-8000-{i:012d}",
            state=RunState.SENT,
            created_at=_T0 + timedelta(minutes=i),
        )
        for i in range(3)
    ]
    for r in runs:
        await repo.save(r)
    await conn.commit()

    result = await repo.list_paginated(frozenset({RunState.SENT}), limit=10, offset=0)
    # Latest run (i=2) appears first.
    assert [r.run_id for r in result] == [
        runs[2].run_id,
        runs[1].run_id,
        runs[0].run_id,
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-024")
async def test_list_paginated_uses_run_id_tiebreaker(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """L3-DASH-024: identical created_at rows SHALL be ordered by run_id DESC."""
    same_time = _T0
    runs = [
        _make_run(
            run_id=f"00000000-0000-4000-8000-{i:012x}",
            state=RunState.SENT,
            created_at=same_time,
        )
        for i in range(3)
    ]
    for r in runs:
        await repo.save(r)
    await conn.commit()

    result = await repo.list_paginated(frozenset({RunState.SENT}), limit=10, offset=0)
    # run_ids sorted DESC by their string form.
    expected = sorted([r.run_id for r in runs], reverse=True)
    assert [r.run_id for r in result] == expected


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-024")
async def test_list_paginated_respects_limit_and_offset(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """LIMIT and OFFSET SHALL slice the result window."""
    runs = [
        _make_run(
            run_id=f"00000000-0000-4000-8000-{i:012d}",
            state=RunState.SENT,
            created_at=_T0 + timedelta(minutes=i),
        )
        for i in range(5)
    ]
    for r in runs:
        await repo.save(r)
    await conn.commit()

    page1 = await repo.list_paginated(frozenset({RunState.SENT}), limit=2, offset=0)
    page2 = await repo.list_paginated(frozenset({RunState.SENT}), limit=2, offset=2)
    page3 = await repo.list_paginated(frozenset({RunState.SENT}), limit=2, offset=4)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # Pages are non-overlapping in run_id.
    seen = {r.run_id for r in (*page1, *page2, *page3)}
    assert len(seen) == 5


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-023")
async def test_list_paginated_filters_by_state_set(
    repo: SqliteRunRepository, conn: aiosqlite.Connection
) -> None:
    """The state filter SHALL exclude runs in any other state."""
    sent = _make_run(
        run_id="00000000-0000-4000-8000-00000000aaa1",
        state=RunState.SENT,
    )
    failed = _make_run(
        run_id="00000000-0000-4000-8000-00000000aaa2",
        state=RunState.FAILED,
    )
    initiated = _make_run(
        run_id="00000000-0000-4000-8000-00000000aaa3",
        state=RunState.INITIATED,
    )
    for r in (sent, failed, initiated):
        await repo.save(r)
    await conn.commit()

    result = await repo.list_paginated(
        frozenset({RunState.SENT, RunState.FAILED}), limit=10, offset=0
    )
    ids = {r.run_id for r in result}
    assert ids == {sent.run_id, failed.run_id}


@pytest.mark.asyncio
async def test_list_paginated_empty_states_returns_empty(
    repo: SqliteRunRepository,
) -> None:
    """An empty state set SHALL yield an empty result without hitting SQL."""
    assert await repo.list_paginated(frozenset(), limit=10, offset=0) == ()


@pytest.mark.asyncio
async def test_list_paginated_rejects_invalid_args(
    repo: SqliteRunRepository,
) -> None:
    """Limit must be positive, offset must be non-negative."""
    with pytest.raises(ValueError, match="limit"):
        await repo.list_paginated(frozenset({RunState.SENT}), limit=0, offset=0)
    with pytest.raises(ValueError, match="offset"):
        await repo.list_paginated(frozenset({RunState.SENT}), limit=10, offset=-1)
