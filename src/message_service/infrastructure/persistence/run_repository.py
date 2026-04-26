"""Concrete :class:`RunRepository` backed by SQLite.

Every method uses the :class:`aiosqlite.Connection` provided at
construction. Writes participate in whatever transaction the enclosing
:class:`SqliteUnitOfWork` has opened; the repo never issues ``BEGIN``/
``COMMIT`` itself.

Serialization conventions
-------------------------

* Timestamps: :func:`iso_z` on write, :func:`parse_iso_z` on read.
* Tags: sorted JSON array (the frozenset is serialized via ``sorted()``
  before :func:`dumps_json`).
* Declared stages: JSON array of ``{stage_id, stage_order,
  report_template_name, report_template_version}`` dicts; order
  preserved from :attr:`Run.declared_stages` (L1-AGGR-003 requires
  stable ordering).
* Aggregation template: split across two columns
  ``aggregation_template_name`` and ``aggregation_template_version``;
  both NULL when ``attachment_mode is PER_STAGE``. The DB-level CHECK
  constraint in ``001_initial_schema.sql`` enforces this invariant.

Upsert semantics
----------------

:meth:`save` uses ``INSERT ... ON CONFLICT(run_id) DO UPDATE`` so
re-delivery of the same logical state is idempotent per the port
contract.

Requirement references
----------------------
L1-PERS-003
L2-RUN-003
L3-RUN-004, L3-RUN-005, L3-RUN-025
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.run_repository import RunRepository
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import PersistenceError, RunNotFoundError
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.infrastructure.persistence._helpers import (
    dumps_json,
    loads_json,
    parse_iso_z,
)

# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------

_SQL_UPSERT = """
INSERT INTO runs (
    run_id, pipeline_type, state, attachment_mode,
    aggregation_template_name, aggregation_template_version,
    tags_json, declared_stages_json, subscription_predicate_tags_json,
    created_at, updated_at
) VALUES (
    :run_id, :pipeline_type, :state, :attachment_mode,
    :aggregation_template_name, :aggregation_template_version,
    :tags_json, :declared_stages_json, :subscription_predicate_tags_json,
    :created_at, :updated_at
)
ON CONFLICT(run_id) DO UPDATE SET
    pipeline_type = excluded.pipeline_type,
    state = excluded.state,
    attachment_mode = excluded.attachment_mode,
    aggregation_template_name = excluded.aggregation_template_name,
    aggregation_template_version = excluded.aggregation_template_version,
    tags_json = excluded.tags_json,
    declared_stages_json = excluded.declared_stages_json,
    subscription_predicate_tags_json = excluded.subscription_predicate_tags_json,
    updated_at = excluded.updated_at
"""

_SQL_SELECT_BY_ID = """
SELECT
    run_id, pipeline_type, state, attachment_mode,
    aggregation_template_name, aggregation_template_version,
    tags_json, declared_stages_json, subscription_predicate_tags_json,
    created_at, updated_at
FROM runs
WHERE run_id = ?
"""

_SQL_UPDATE_STATE = """
UPDATE runs
SET state = ?, updated_at = ?
WHERE run_id = ?
"""

# _SQL_LIST_IN_STATES is built dynamically; see method.

_SQL_LIST_EXPIRED_BASE = """
SELECT
    run_id, pipeline_type, state, attachment_mode,
    aggregation_template_name, aggregation_template_version,
    tags_json, declared_stages_json, subscription_predicate_tags_json,
    created_at, updated_at
FROM runs
WHERE updated_at <= ? AND state IN ({placeholders})
ORDER BY updated_at ASC
LIMIT ?
"""


class SqliteRunRepository(RunRepository):
    """SQLite-backed :class:`RunRepository`.

    Constructed per UoW by :class:`SqliteUnitOfWorkFactory`; callers
    outside a UoW scope do not interact with this class directly.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to a connection that is already inside a transaction."""
        self._conn = conn

    # -- save ------------------------------------------------------------

    async def save(self, run: Run) -> None:  # noqa: D102
        await self._conn.execute(_SQL_UPSERT, _run_to_params(run))

    # -- get -------------------------------------------------------------

    async def get(self, run_id: RunId) -> Run:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BY_ID, (run_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RunNotFoundError(
                f"run {run_id!r} not found",
                details={"run_id": run_id},
            )
        return _row_to_run(row)

    # -- update_state ----------------------------------------------------

    async def update_state(self, run_id: RunId, new_state: RunState, now: datetime) -> None:  # noqa: D102
        cur = await self._conn.execute(_SQL_UPDATE_STATE, (new_state.value, iso_z(now), run_id))
        # cur.rowcount is 0 for a missing run_id — surface as NotFound so
        # use cases don't silently no-op.
        if cur.rowcount == 0:
            raise RunNotFoundError(
                f"run {run_id!r} not found for state update",
                details={"run_id": run_id, "new_state": new_state.value},
            )

    # -- list_in_states --------------------------------------------------

    async def list_in_states(self, states: frozenset[RunState]) -> Sequence[Run]:  # noqa: D102
        if not states:
            return ()

        placeholders = ", ".join("?" * len(states))
        sql = f"""
            SELECT
                run_id, pipeline_type, state, attachment_mode,
                aggregation_template_name, aggregation_template_version,
                tags_json, declared_stages_json, subscription_predicate_tags_json,
                created_at, updated_at
            FROM runs
            WHERE state IN ({placeholders})
            ORDER BY updated_at ASC
        """
        # Sort states for deterministic parameter order (irrelevant to
        # SQL semantics but useful for log diffing).
        params = tuple(sorted(s.value for s in states))
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]

    # -- list_expired ----------------------------------------------------

    async def list_expired(
        self,
        cutoff: datetime,
        active_states: frozenset[RunState],
        *,
        limit: int,
    ) -> Sequence[Run]:
        """List up to ``limit`` runs whose last transition is older than ``cutoff``.

        Per L2-SWEEP-004 the comparison is against ``updated_at``
        (the "last transition" timestamp), not ``created_at``, so a
        run that transitions briskly but stalls mid-lifecycle is
        correctly excluded until its latest transition itself ages out.
        ``limit`` enforces L3-SWEEP-008's per-tick bound.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")
        if not active_states:
            return ()

        placeholders = ", ".join("?" * len(active_states))
        sql = _SQL_LIST_EXPIRED_BASE.format(placeholders=placeholders)
        params = (iso_z(cutoff), *sorted(s.value for s in active_states), limit)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]

    # -- list_paginated --------------------------------------------------

    async def list_paginated(  # noqa: D102
        self,
        states: frozenset[RunState],
        *,
        limit: int,
        offset: int,
    ) -> Sequence[Run]:
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")
        if offset < 0:
            raise ValueError(f"offset must be non-negative; got {offset}")
        if not states:
            return ()

        # L3-DASH-024: ORDER BY runs.created_at DESC, runs.run_id DESC
        # then LIMIT ? OFFSET ?. The state IN clause uses sorted state
        # values for deterministic parameter binding (consistent with
        # list_in_states / list_expired above).
        placeholders = ", ".join("?" * len(states))
        sql = f"""
            SELECT
                run_id, pipeline_type, state, attachment_mode,
                aggregation_template_name, aggregation_template_version,
                tags_json, declared_stages_json, subscription_predicate_tags_json,
                created_at, updated_at
            FROM runs
            WHERE state IN ({placeholders})
            ORDER BY created_at DESC, run_id DESC
            LIMIT ? OFFSET ?
        """
        params = (*sorted(s.value for s in states), limit, offset)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]


# -----------------------------------------------------------------------------
# Row <-> aggregate mapping
# -----------------------------------------------------------------------------


def _run_to_params(run: Run) -> dict[str, Any]:
    """Flatten a :class:`Run` into a :mod:`sqlite3` parameter dict."""
    if run.attachment_mode is AttachmentMode.SINGLE_AGGREGATED:
        assert run.aggregation_template_ref is not None  # invariant
        agg_name: str | None = run.aggregation_template_ref.name
        agg_version: str | None = run.aggregation_template_ref.version
    else:
        agg_name = None
        agg_version = None

    declared_stages_payload = [
        {
            "stage_id": ds.stage_id,
            "stage_order": ds.stage_order,
            "report_template_name": ds.report_template_ref.name,
            "report_template_version": ds.report_template_ref.version,
        }
        for ds in run.declared_stages
    ]

    return {
        "run_id": run.run_id,
        "pipeline_type": run.pipeline_type,
        "state": run.state.value,
        "attachment_mode": run.attachment_mode.value,
        "aggregation_template_name": agg_name,
        "aggregation_template_version": agg_version,
        "tags_json": dumps_json(sorted(run.tags)),
        "declared_stages_json": dumps_json(declared_stages_payload),
        "subscription_predicate_tags_json": dumps_json(sorted(run.subscription_predicate_tags)),
        "created_at": iso_z(run.created_at),
        "updated_at": iso_z(run.updated_at),
    }


def _row_to_run(row: aiosqlite.Row) -> Run:
    """Build a :class:`Run` from a ``runs`` row.

    Raises:
        PersistenceError: Data corruption (unparseable JSON, unknown
            enum value, invariant violation).
    """
    try:
        state = RunState(row["state"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted run has unknown state {row['state']!r}",
            details={"run_id": row["run_id"], "state": row["state"]},
        ) from exc

    try:
        attachment_mode = AttachmentMode(row["attachment_mode"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted run has unknown attachment_mode {row['attachment_mode']!r}",
            details={
                "run_id": row["run_id"],
                "attachment_mode": row["attachment_mode"],
            },
        ) from exc

    aggregation_template_ref: TemplateRef | None = None
    if (
        row["aggregation_template_name"] is not None
        and row["aggregation_template_version"] is not None
    ):
        aggregation_template_ref = TemplateRef(
            name=row["aggregation_template_name"],
            version=row["aggregation_template_version"],
        )

    tags_list = loads_json(row["tags_json"], field="tags_json")
    sub_tags_list = loads_json(
        row["subscription_predicate_tags_json"],
        field="subscription_predicate_tags_json",
    )
    declared_stages_list = loads_json(row["declared_stages_json"], field="declared_stages_json")

    declared_stages = tuple(
        DeclaredStage(
            stage_id=StageId(item["stage_id"]),
            stage_order=int(item["stage_order"]),
            report_template_ref=TemplateRef(
                name=item["report_template_name"],
                version=item["report_template_version"],
            ),
        )
        for item in declared_stages_list
    )

    try:
        return Run(
            run_id=RunId(row["run_id"]),
            pipeline_type=row["pipeline_type"],
            tags=frozenset(tags_list),
            declared_stages=declared_stages,
            state=state,
            attachment_mode=attachment_mode,
            aggregation_template_ref=aggregation_template_ref,
            subscription_predicate_tags=frozenset(sub_tags_list),
            created_at=parse_iso_z(row["created_at"]),
            updated_at=parse_iso_z(row["updated_at"]),
        )
    except ValueError as exc:
        # Aggregate invariants (e.g., updated_at < created_at) violated
        # by corrupt data.
        raise PersistenceError(
            f"persisted run {row['run_id']!r} violates aggregate invariants: {exc}",
            details={"run_id": row["run_id"], "reason": str(exc)},
        ) from exc


__all__ = ["SqliteRunRepository"]
