"""Concrete :class:`AuditLog` backed by SQLite.

Append-only: :meth:`record` does one ``INSERT`` into ``audit_log``.
No ``UPDATE``/``DELETE`` path through this class — retention is
handled by a separate background pruner driven by
``observability.audit.retention_days``.

:meth:`query` supports optional filters on ``action``, ``resource``,
``actor``, and a ``[since, until]`` timestamp range, ANDed together.
Results are ordered ``timestamp DESC`` to surface recent events first.

Because :attr:`AuditEvent.details` is arbitrary structured data, it
serializes to a JSON TEXT column with our deterministic encoder
(``sort_keys=True, separators=(",", ":")``). Round-trip preserves
key order for diffing.

Requirement references
----------------------
L1-OBS-003 (append-only governance)
L2-OBS-002 (audit every state change)
L3-RUN-026, L3-RUN-027 (audit-first ordering; enforced at the call
site, not here)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.clock import iso_z
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence._helpers import (
    dumps_json,
    loads_json,
    parse_iso_z,
)

# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------

_SQL_INSERT = """
INSERT INTO audit_log (
    timestamp, action, actor, resource, outcome, details_json
) VALUES (?, ?, ?, ?, ?, ?)
"""

_SQL_SELECT_BASE = """
SELECT audit_id, timestamp, action, actor, resource, outcome, details_json
FROM audit_log
"""

# Bounded DELETE for the L3-OBS-014..016 retention pruner. Stdlib sqlite3
# lacks SQLITE_ENABLE_UPDATE_DELETE_LIMIT, so the standard sub-select
# pattern is used. The timestamp index makes the inner query efficient.
# See L3-OBS-039 for the sole-deleter conformance constraint that
# limits callers of delete_older_than to the audit_log_pruner module.
_SQL_DELETE_OLDER_THAN = """
DELETE FROM audit_log
WHERE audit_id IN (
    SELECT audit_id FROM audit_log
    WHERE timestamp < ?
    ORDER BY timestamp ASC
    LIMIT ?
)
"""


class SqliteAuditLog(AuditLog):
    """SQLite-backed append-only :class:`AuditLog`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to a connection inside a UoW transaction."""
        self._conn = conn

    async def record(self, event: AuditEvent) -> None:
        """Append a single audit event."""
        await self._conn.execute(
            _SQL_INSERT,
            (
                iso_z(event.timestamp),
                event.action.value,
                event.actor,
                event.resource,
                event.outcome.value,
                dumps_json(event.details),
            ),
        )

    async def query(
        self,
        *,
        action: AuditAction | None = None,
        resource: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> Sequence[AuditEvent]:
        """Retrieve matching events ordered timestamp DESC."""
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")

        where_clauses: list[str] = []
        params: list[str | int] = []
        if action is not None:
            where_clauses.append("action = ?")
            params.append(action.value)
        if resource is not None:
            where_clauses.append("resource = ?")
            params.append(resource)
        if actor is not None:
            where_clauses.append("actor = ?")
            params.append(actor)
        if since is not None:
            where_clauses.append("timestamp >= ?")
            params.append(iso_z(since))
        if until is not None:
            where_clauses.append("timestamp <= ?")
            params.append(iso_z(until))

        sql = _SQL_SELECT_BASE
        if where_clauses:
            sql += "WHERE " + " AND ".join(where_clauses) + " "
        sql += "ORDER BY timestamp DESC, audit_id DESC LIMIT ?"
        params.append(limit)

        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]

    async def delete_older_than(
        self,
        cutoff: datetime,
        *,
        batch_size: int,
    ) -> int:
        """Delete up to ``batch_size`` rows whose ``timestamp`` < ``cutoff``.

        Reserved for the audit-log retention pruner per L3-OBS-039.
        Implements the sub-select pattern documented in
        ``_SQL_DELETE_OLDER_THAN`` because stdlib sqlite3 lacks
        ``DELETE ... LIMIT`` support.
        """
        if cutoff.tzinfo is None:
            raise ValueError(
                f"delete_older_than requires a timezone-aware cutoff; got naive {cutoff!r}"
            )
        if batch_size < 1:
            raise ValueError(f"batch_size must be positive; got {batch_size}")
        cursor = await self._conn.execute(
            _SQL_DELETE_OLDER_THAN,
            (iso_z(cutoff), batch_size),
        )
        return int(cursor.rowcount)

    async def list_paginated(  # noqa: D102
        self,
        *,
        actions: frozenset[AuditAction] | None = None,
        actor: str | None = None,
        resource: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[AuditEvent]:
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")
        if offset < 0:
            raise ValueError(f"offset must be non-negative; got {offset}")

        where_clauses: list[str] = []
        params: list[str | int] = []
        if actions:
            placeholders = ", ".join("?" for _ in actions)
            where_clauses.append(f"action IN ({placeholders})")
            params.extend(a.value for a in actions)
        if actor is not None:
            where_clauses.append("actor = ?")
            params.append(actor)
        if resource is not None:
            where_clauses.append("resource = ?")
            params.append(resource)
        if since is not None:
            where_clauses.append("timestamp >= ?")
            params.append(iso_z(since))
        if until is not None:
            where_clauses.append("timestamp <= ?")
            params.append(iso_z(until))

        sql = _SQL_SELECT_BASE
        if where_clauses:
            sql += "WHERE " + " AND ".join(where_clauses) + " "
        # L3-DASH-034: order by audit_id DESC for stability across
        # same-timestamp ties (two events from the same UoW share a
        # timestamp because the use case captures clock.now() once).
        sql += "ORDER BY audit_id DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))

        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]


# -----------------------------------------------------------------------------
# Row -> aggregate
# -----------------------------------------------------------------------------


def _row_to_event(row: aiosqlite.Row) -> AuditEvent:
    """Build an :class:`AuditEvent` from an ``audit_log`` row."""
    try:
        action = AuditAction(row["action"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted audit row has unknown action {row['action']!r}",
            details={"audit_id": row["audit_id"], "action": row["action"]},
        ) from exc

    try:
        outcome = AuditOutcome(row["outcome"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted audit row has unknown outcome {row['outcome']!r}",
            details={"audit_id": row["audit_id"], "outcome": row["outcome"]},
        ) from exc

    details = loads_json(row["details_json"], field="details_json")

    return AuditEvent(
        timestamp=parse_iso_z(row["timestamp"]),
        action=action,
        actor=row["actor"],
        resource=row["resource"],
        outcome=outcome,
        details=details,
        audit_id=int(row["audit_id"]),
    )


__all__ = ["SqliteAuditLog"]
