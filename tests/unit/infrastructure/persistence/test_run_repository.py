"""Unit tests for :class:`SqliteRunRepository`.

Uses a real in-memory SQLite DB so the tests exercise the actual SQL
and JSON round-tripping — this is the "integration-ish" layer that
proves our port contract implementation matches what use cases expect.
"""

from __future__ import annotations

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
async def conn() -> aiosqlite.Connection:
    c = await open_connection(Path(":memory:"))
    await apply_migrations(c)
    return c


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
@pytest.mark.requirement("L3-RUN-025")
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
    )
    expired_ids = [r.run_id for r in expired]
    # Only the one both older than cutoff and in an active state.
    assert expired_ids == ["00000000-0000-4000-8000-000000000001"]


@pytest.mark.asyncio
async def test_list_expired_empty_active_states_returns_empty(
    repo: SqliteRunRepository,
) -> None:
    result = await repo.list_expired(cutoff=_T0, active_states=frozenset())
    assert list(result) == []


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
