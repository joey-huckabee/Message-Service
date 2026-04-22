"""Concrete :class:`StageRepository` backed by SQLite.

Stages have a composite primary key ``(run_id, stage_id)`` and a
foreign-key cascade from ``runs`` so deleting a run removes its
stages automatically.

The ``report_template`` reference is split across two columns
(``report_template_name`` / ``report_template_version``); a single
column with composite JSON would have been simpler but would block
future indexing of "all stages using template X".

Upsert semantics
----------------

:meth:`save` uses ``INSERT ... ON CONFLICT(run_id, stage_id) DO UPDATE``
per L3-STAGE-006. Retry submissions overwrite ``report_context_json``,
``email_body_context_json``, ``state``, and ``submitted_at`` in place.

Requirement references
----------------------
L1-PERS-003
L3-STAGE-002, L3-STAGE-006, L3-STAGE-007, L3-STAGE-009
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.stage_repository import StageRepository
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import PersistenceError, UnknownStageError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.stage_states import StageState
from message_service.infrastructure.persistence._helpers import parse_iso_z

# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------

_SQL_UPSERT = """
INSERT INTO stages (
    run_id, stage_id, state,
    report_template_name, report_template_version,
    report_context_json, email_body_context_json,
    submitted_at
) VALUES (
    :run_id, :stage_id, :state,
    :report_template_name, :report_template_version,
    :report_context_json, :email_body_context_json,
    :submitted_at
)
ON CONFLICT(run_id, stage_id) DO UPDATE SET
    state = excluded.state,
    report_template_name = excluded.report_template_name,
    report_template_version = excluded.report_template_version,
    report_context_json = excluded.report_context_json,
    email_body_context_json = excluded.email_body_context_json,
    submitted_at = excluded.submitted_at
"""

_SQL_SELECT_BY_KEY = """
SELECT
    run_id, stage_id, state,
    report_template_name, report_template_version,
    report_context_json, email_body_context_json,
    submitted_at
FROM stages
WHERE run_id = ? AND stage_id = ?
"""

_SQL_SELECT_BY_RUN = """
SELECT
    run_id, stage_id, state,
    report_template_name, report_template_version,
    report_context_json, email_body_context_json,
    submitted_at
FROM stages
WHERE run_id = ?
"""

_SQL_UPDATE_STATE = """
UPDATE stages
SET state = ?, submitted_at = ?
WHERE run_id = ? AND stage_id = ?
"""

_SQL_LIST_PENDING_IDS = """
SELECT stage_id
FROM stages
WHERE run_id = ? AND state = ?
"""


class SqliteStageRepository(StageRepository):
    """SQLite-backed :class:`StageRepository`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to an open connection."""
        self._conn = conn

    async def save(self, stage: Stage) -> None:  # noqa: D102
        await self._conn.execute(_SQL_UPSERT, _stage_to_params(stage))

    async def get(self, run_id: RunId, stage_id: StageId) -> Stage:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BY_KEY, (run_id, stage_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise UnknownStageError(
                f"stage {stage_id!r} not found in run {run_id!r}",
                details={"run_id": run_id, "stage_id": stage_id},
            )
        return _row_to_stage(row)

    async def list_by_run(self, run_id: RunId) -> Sequence[Stage]:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BY_RUN, (run_id,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_stage(r) for r in rows]

    async def update_state(
        self,
        run_id: RunId,
        stage_id: StageId,
        new_state: StageState,
        now: datetime,
    ) -> None:
        """Update a stage's state and ``submitted_at``."""
        # PENDING stages have NULL submitted_at by CHECK constraint;
        # any non-PENDING state must have a submitted_at. The caller
        # (use case) supplies ``now`` for the non-PENDING transitions;
        # for PENDING we force NULL.
        submitted_at_value = None if new_state is StageState.PENDING else iso_z(now)
        cur = await self._conn.execute(
            _SQL_UPDATE_STATE,
            (new_state.value, submitted_at_value, run_id, stage_id),
        )
        if cur.rowcount == 0:
            raise UnknownStageError(
                f"stage {stage_id!r} not found in run {run_id!r} for state update",
                details={
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "new_state": new_state.value,
                },
            )

    async def list_pending_by_run(self, run_id: RunId) -> Sequence[StageId]:  # noqa: D102
        async with self._conn.execute(
            _SQL_LIST_PENDING_IDS, (run_id, StageState.PENDING.value)
        ) as cur:
            rows = await cur.fetchall()
        return [StageId(r["stage_id"]) for r in rows]


# -----------------------------------------------------------------------------
# Row <-> aggregate mapping
# -----------------------------------------------------------------------------


def _stage_to_params(stage: Stage) -> dict[str, Any]:
    """Flatten a :class:`Stage` into a :mod:`sqlite3` parameter dict."""
    return {
        "run_id": stage.run_id,
        "stage_id": stage.stage_id,
        "state": stage.state.value,
        "report_template_name": stage.report_template_ref.name,
        "report_template_version": stage.report_template_ref.version,
        "report_context_json": stage.report_context_json,
        "email_body_context_json": stage.email_body_context_json,
        "submitted_at": iso_z(stage.submitted_at) if stage.submitted_at is not None else None,
    }


def _row_to_stage(row: aiosqlite.Row) -> Stage:
    """Build a :class:`Stage` from a ``stages`` row."""
    try:
        state = StageState(row["state"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted stage has unknown state {row['state']!r}",
            details={
                "run_id": row["run_id"],
                "stage_id": row["stage_id"],
                "state": row["state"],
            },
        ) from exc

    submitted_at = parse_iso_z(row["submitted_at"]) if row["submitted_at"] is not None else None

    try:
        return Stage(
            run_id=RunId(row["run_id"]),
            stage_id=StageId(row["stage_id"]),
            state=state,
            report_template_ref=TemplateRef(
                name=row["report_template_name"],
                version=row["report_template_version"],
            ),
            report_context_json=row["report_context_json"],
            email_body_context_json=row["email_body_context_json"],
            submitted_at=submitted_at,
        )
    except ValueError as exc:
        raise PersistenceError(
            f"persisted stage violates aggregate invariants: {exc}",
            details={
                "run_id": row["run_id"],
                "stage_id": row["stage_id"],
                "reason": str(exc),
            },
        ) from exc


__all__ = ["SqliteStageRepository"]
