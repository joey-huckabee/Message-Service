"""Concrete :class:`SweeperActionRepository` backed by SQLite.

For 14b.2 only the write-side (``enqueue``) is implemented; the
dispatcher's claim/complete/fail SQL arrives in 14b.3 and will share
this module.

Requirement references
----------------------
L2-SWEEP-006 (atomic transition + enqueue)
L3-SWEEP-010 (sweeper_actions outbox)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.sweeper_action_repository import (
    SweeperActionRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from message_service.config.schema import DispositionAction
    from message_service.domain.ids import RunId


_SQL_INSERT = """
INSERT INTO sweeper_actions (run_id, action_name, enqueued_at)
VALUES (?, ?, ?)
"""


class SqliteSweeperActionRepository(SweeperActionRepository):
    """SQLite-backed write-side outbox for sweeper actions."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to a connection inside a UoW transaction."""
        self._conn = conn

    async def enqueue(
        self,
        *,
        run_id: RunId,
        action_name: DispositionAction,
        enqueued_at: datetime,
    ) -> None:
        """Insert one pending row.

        The DB enforces the action_name CHECK constraint (must be one
        of the four valid :data:`DispositionAction` values) and the FK
        to ``runs(run_id)``; failures bubble up as
        ``aiosqlite.IntegrityError`` and the enclosing UoW rolls back.
        """
        await self._conn.execute(
            _SQL_INSERT,
            (str(run_id), action_name, iso_z(enqueued_at)),
        )


__all__ = ["SqliteSweeperActionRepository"]
