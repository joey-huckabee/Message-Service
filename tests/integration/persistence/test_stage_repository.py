"""Unit tests for :class:`SqliteStageRepository`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import PersistenceError, UnknownStageError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.stage_states import StageState
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)

# -----------------------------------------------------------------------------
# Fixtures + test helpers
# -----------------------------------------------------------------------------

_RID = RunId("00000000-0000-4000-8000-000000000001")
_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 21, 13, 0, 0, tzinfo=UTC)
_TPL = TemplateRef(name="r", version="1.0")


async def _seed_run(conn: aiosqlite.Connection, run_id: RunId = _RID) -> None:
    """Insert a minimal runs row so FK'd stages can land."""
    await conn.execute(
        """
        INSERT INTO runs (
            run_id, pipeline_type, state, attachment_mode,
            aggregation_template_name, aggregation_template_version,
            tags_json, declared_stages_json, subscription_predicate_tags_json,
            created_at, updated_at
        ) VALUES (?, 'etl', 'INITIATED', 'PER_STAGE',
                  NULL, NULL, '[]', '[]', '[]',
                  '2026-04-21T12:00:00Z', '2026-04-21T12:00:00Z')
        """,
        (run_id,),
    )


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        await _seed_run(c)
        await c.commit()
        yield c
    finally:
        await c.close()


@pytest.fixture
async def repo(conn: aiosqlite.Connection) -> SqliteStageRepository:
    return SqliteStageRepository(conn)


def _make_stage(
    *,
    stage_id: str = "extract",
    state: StageState = StageState.PENDING,
    report_context_json: str | None = None,
    email_body_context_json: str | None = None,
    email_body_position: EmailBodyPosition | None = None,
    submitted_at: datetime | None = None,
) -> Stage:
    # PENDING must have submitted_at=None; non-PENDING must have it set.
    if state is not StageState.PENDING and submitted_at is None:
        submitted_at = _T0
    # L3-AGGR-018: position is set iff an email body contribution is present.
    if email_body_position is None and email_body_context_json is not None:
        email_body_position = EmailBodyPosition.AFTER_STAGES_SUMMARY
    return Stage(
        run_id=_RID,
        stage_id=StageId(stage_id),
        state=state,
        report_template_ref=_TPL,
        report_context_json=report_context_json,
        email_body_context_json=email_body_context_json,
        email_body_position=email_body_position,
        submitted_at=submitted_at,
    )


# -----------------------------------------------------------------------------
# Happy-path round-trip
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-PERS-003")
async def test_save_then_get_returns_identical_stage(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    stage = _make_stage(
        state=StageState.SUBMITTED,
        report_context_json='{"metric":42}',
        submitted_at=_T0,
    )
    await repo.save(stage)
    await conn.commit()

    loaded = await repo.get(stage.run_id, stage.stage_id)
    assert loaded == stage


@pytest.mark.asyncio
async def test_pending_stage_has_null_submitted_at(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    stage = _make_stage(state=StageState.PENDING, submitted_at=None)
    await repo.save(stage)
    await conn.commit()

    loaded = await repo.get(stage.run_id, stage.stage_id)
    assert loaded.submitted_at is None


@pytest.mark.asyncio
async def test_empty_json_contexts_round_trip(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    """L3-STAGE-010: empty dict ``{}`` preserved; not coerced to NULL."""
    stage = _make_stage(
        state=StageState.SUBMITTED,
        report_context_json="{}",
        email_body_context_json=None,
        submitted_at=_T0,
    )
    await repo.save(stage)
    await conn.commit()

    loaded = await repo.get(stage.run_id, stage.stage_id)
    assert loaded.report_context_json == "{}"
    assert loaded.email_body_context_json is None


# -----------------------------------------------------------------------------
# get() misses
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_on_missing_stage_raises(
    repo: SqliteStageRepository,
) -> None:
    with pytest.raises(UnknownStageError):
        await repo.get(_RID, StageId("never-existed"))


@pytest.mark.asyncio
async def test_get_on_wrong_run_id_raises(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    stage = _make_stage()
    await repo.save(stage)
    await conn.commit()

    wrong_rid = RunId("00000000-0000-4000-8000-000000000999")
    with pytest.raises(UnknownStageError):
        await repo.get(wrong_rid, stage.stage_id)


# -----------------------------------------------------------------------------
# Upsert
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-005")
@pytest.mark.requirement("L3-STAGE-006")
async def test_retry_overwrites_prior_content_in_place(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    first = _make_stage(
        state=StageState.SUBMITTED,
        report_context_json='{"version":1}',
        submitted_at=_T0,
    )
    await repo.save(first)

    retried = _make_stage(
        state=StageState.RETRIED,
        report_context_json='{"version":2}',
        submitted_at=_T1,
    )
    await repo.save(retried)
    await conn.commit()

    loaded = await repo.get(_RID, StageId("extract"))
    assert loaded.report_context_json == '{"version":2}'
    assert loaded.state is StageState.RETRIED
    assert loaded.submitted_at == _T1


# -----------------------------------------------------------------------------
# list_by_run
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_run_returns_all_stages(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    for name in ("extract", "transform", "load"):
        await repo.save(_make_stage(stage_id=name))
    await conn.commit()

    stages = await repo.list_by_run(_RID)
    assert {s.stage_id for s in stages} == {"extract", "transform", "load"}


@pytest.mark.asyncio
async def test_list_by_run_empty_for_unknown_run(
    repo: SqliteStageRepository,
) -> None:
    stages = await repo.list_by_run(RunId("00000000-0000-4000-8000-000000000999"))
    assert list(stages) == []


# -----------------------------------------------------------------------------
# update_state
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_state_changes_state_and_submitted_at(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    stage = _make_stage(state=StageState.PENDING, submitted_at=None)
    await repo.save(stage)
    await conn.commit()

    await repo.update_state(_RID, StageId("extract"), StageState.SUBMITTED, _T1)
    await conn.commit()

    loaded = await repo.get(_RID, StageId("extract"))
    assert loaded.state is StageState.SUBMITTED
    assert loaded.submitted_at == _T1


@pytest.mark.asyncio
async def test_update_state_on_missing_stage_raises(
    repo: SqliteStageRepository,
) -> None:
    with pytest.raises(UnknownStageError):
        await repo.update_state(_RID, StageId("never-existed"), StageState.SUBMITTED, _T1)


# -----------------------------------------------------------------------------
# list_pending_by_run
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_by_run_returns_only_pending_ids(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    await repo.save(_make_stage(stage_id="extract", state=StageState.PENDING))
    await repo.save(
        _make_stage(
            stage_id="transform",
            state=StageState.SUBMITTED,
            submitted_at=_T0,
        )
    )
    await repo.save(_make_stage(stage_id="load", state=StageState.PENDING))
    await conn.commit()

    pending_ids = await repo.list_pending_by_run(_RID)
    assert set(pending_ids) == {"extract", "load"}


@pytest.mark.asyncio
async def test_list_pending_empty_when_all_submitted(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    await repo.save(_make_stage(stage_id="extract", state=StageState.SUBMITTED, submitted_at=_T0))
    await conn.commit()

    pending = await repo.list_pending_by_run(_RID)
    assert list(pending) == []


# -----------------------------------------------------------------------------
# FK cascade
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_run_cascades_to_stages(
    repo: SqliteStageRepository, conn: aiosqlite.Connection
) -> None:
    await repo.save(_make_stage(stage_id="extract"))
    await conn.commit()

    await conn.execute("DELETE FROM runs WHERE run_id = ?", (_RID,))
    await conn.commit()

    stages = await repo.list_by_run(_RID)
    assert list(stages) == []


# -----------------------------------------------------------------------------
# Corrupt-data handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_with_unknown_state_raises_persistence_error(
    conn: aiosqlite.Connection,
) -> None:
    await conn.execute("PRAGMA ignore_check_constraints = 1")
    await conn.execute(
        """
        INSERT INTO stages (
            run_id, stage_id, state,
            report_template_name, report_template_version,
            report_context_json, email_body_context_json, submitted_at
        ) VALUES (?, 'bad', 'NOT_A_STATE', 'r', '1.0', NULL, NULL, NULL)
        """,
        (_RID,),
    )
    await conn.commit()
    await conn.execute("PRAGMA ignore_check_constraints = 0")

    repo = SqliteStageRepository(conn)
    with pytest.raises(PersistenceError, match="unknown state"):
        await repo.get(_RID, StageId("bad"))
