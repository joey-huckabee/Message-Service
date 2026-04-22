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
    )


__all__ = ["SqliteAuditLog"]
