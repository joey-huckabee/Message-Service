"""Concrete :class:`SweeperActionRepository` backed by SQLite.

Two halves: ``enqueue`` is called by the orphan transaction
(:class:`~message_service.application.use_cases.sweeper.SweeperUseCase`);
``claim_pending`` / ``mark_completed`` / ``mark_failed`` are called by
the dispatcher use case.

The claim query uses ``UPDATE … WHERE action_id IN (SELECT … LIMIT N)``
to atomically stamp ``claimed_at`` on the oldest pending rows; a follow
``SELECT`` returns the freshly claimed rows for handler dispatch. We do
not rely on ``UPDATE … RETURNING`` (added in SQLite 3.35) so the code
runs on older system SQLite installs without surprise.

The SqliteUnitOfWork serializes both halves on a single connection, so
"two queries inside one transaction" is racially safe for the claim.

Requirement references
----------------------
L2-SWEEP-006 (atomic transition + enqueue)
L3-SWEEP-010 (sweeper_actions outbox)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.sweeper_action_repository import (
    ClaimedAction,
    SweeperActionRepository,
)
from message_service.domain.ids import RunId

if TYPE_CHECKING:
    from datetime import datetime

    from message_service.config.schema import DispositionAction


_SQL_INSERT = """
INSERT INTO sweeper_actions (run_id, action_name, enqueued_at)
VALUES (?, ?, ?)
"""

# Two-step claim: SELECT the oldest N pending rows (capturing every
# field the dispatcher needs), then UPDATE those specific action_ids
# to stamp claimed_at. We do NOT key the post-claim read on
# claimed_at, since two ticks at the same clock value would re-read
# each other's claims; we key on the captured action_id list instead.
# Both statements run inside the caller's UoW transaction.
_SQL_CLAIM_SELECT_PENDING = """
SELECT action_id, run_id, action_name, attempts
FROM sweeper_actions
WHERE claimed_at IS NULL
ORDER BY enqueued_at, action_id
LIMIT ?
"""

_SQL_MARK_COMPLETED = """
UPDATE sweeper_actions
SET completed_at = ?
WHERE action_id = ?
"""

_SQL_MARK_FAILED = """
UPDATE sweeper_actions
SET completed_at = ?,
    attempts = attempts + 1,
    last_error = ?
WHERE action_id = ?
"""

# Stuck-claim recovery (L3-SWEEP-020). Two-step like claim_pending:
# SELECT the candidate ids first (so we know exactly what we own),
# then UPDATE-with-bumped-attempts on those specific ids.
_SQL_SELECT_STUCK = """
SELECT action_id, run_id, action_name, attempts
FROM sweeper_actions
WHERE completed_at IS NULL
  AND claimed_at IS NOT NULL
  AND claimed_at <= ?
  AND attempts < ?
ORDER BY claimed_at, action_id
LIMIT ?
"""

# Abandonment detection (L3-SWEEP-021). Same shape as the stuck
# select but flipped on the attempts cap. Pure read — caller settles
# via mark_abandoned + audit.
_SQL_SELECT_ABANDONED = """
SELECT action_id, run_id, action_name, attempts
FROM sweeper_actions
WHERE completed_at IS NULL
  AND claimed_at IS NOT NULL
  AND claimed_at <= ?
  AND attempts >= ?
ORDER BY claimed_at, action_id
LIMIT ?
"""

_SQL_MARK_ABANDONED = """
UPDATE sweeper_actions
SET completed_at = ?,
    last_error = ?
WHERE action_id = ?
"""


class SqliteSweeperActionRepository(SweeperActionRepository):
    """SQLite-backed outbox for sweeper actions."""

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

    async def claim_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ClaimedAction]:
        """Stamp ``claimed_at`` on the oldest ``limit`` pending rows; return them."""
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")

        async with self._conn.execute(_SQL_CLAIM_SELECT_PENDING, (limit,)) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []

        action_ids = [int(r[0]) for r in rows]
        placeholders = ",".join("?" * len(action_ids))
        await self._conn.execute(
            f"UPDATE sweeper_actions SET claimed_at = ? WHERE action_id IN ({placeholders})",
            (iso_z(now), *action_ids),
        )
        return [
            ClaimedAction(
                action_id=int(r[0]),
                run_id=RunId(str(r[1])),
                action_name=cast("DispositionAction", str(r[2])),
                attempts=int(r[3]),
            )
            for r in rows
        ]

    async def mark_completed(
        self,
        *,
        action_id: int,
        completed_at: datetime,
    ) -> None:
        """Stamp ``completed_at`` on a successful row."""
        await self._conn.execute(
            _SQL_MARK_COMPLETED,
            (iso_z(completed_at), action_id),
        )

    async def mark_failed(
        self,
        *,
        action_id: int,
        completed_at: datetime,
        error_message: str,
    ) -> None:
        """Stamp completed_at + attempts + last_error on a failed row."""
        await self._conn.execute(
            _SQL_MARK_FAILED,
            (iso_z(completed_at), error_message, action_id),
        )

    async def reclaim_stuck(
        self,
        *,
        now: datetime,
        limit: int,
        stale_threshold_seconds: int,
        max_attempts: int,
    ) -> Sequence[ClaimedAction]:
        """SELECT-then-UPDATE on stuck rows; bumps attempts."""
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")
        if stale_threshold_seconds < 1:
            raise ValueError(
                f"stale_threshold_seconds must be positive; got {stale_threshold_seconds}"
            )
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be positive; got {max_attempts}")

        from datetime import timedelta

        cutoff = now - timedelta(seconds=stale_threshold_seconds)
        async with self._conn.execute(
            _SQL_SELECT_STUCK, (iso_z(cutoff), max_attempts, limit)
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []

        action_ids = [int(r[0]) for r in rows]
        placeholders = ",".join("?" * len(action_ids))
        # Bump attempts AND set claimed_at in one UPDATE.
        await self._conn.execute(
            f"UPDATE sweeper_actions "
            f"SET claimed_at = ?, attempts = attempts + 1 "
            f"WHERE action_id IN ({placeholders})",
            (iso_z(now), *action_ids),
        )
        # Return the post-bump attempts (selected_attempts + 1).
        return [
            ClaimedAction(
                action_id=int(r[0]),
                run_id=RunId(str(r[1])),
                action_name=cast("DispositionAction", str(r[2])),
                attempts=int(r[3]) + 1,
            )
            for r in rows
        ]

    async def find_abandoned(
        self,
        *,
        now: datetime,
        stale_threshold_seconds: int,
        max_attempts: int,
        limit: int,
    ) -> Sequence[ClaimedAction]:
        """Pure-read scan for stuck rows that have exhausted retries."""
        if limit < 1:
            raise ValueError(f"limit must be positive; got {limit}")
        if stale_threshold_seconds < 1:
            raise ValueError(
                f"stale_threshold_seconds must be positive; got {stale_threshold_seconds}"
            )
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be positive; got {max_attempts}")

        from datetime import timedelta

        cutoff = now - timedelta(seconds=stale_threshold_seconds)
        async with self._conn.execute(
            _SQL_SELECT_ABANDONED, (iso_z(cutoff), max_attempts, limit)
        ) as cur:
            rows = await cur.fetchall()
        return [
            ClaimedAction(
                action_id=int(r[0]),
                run_id=RunId(str(r[1])),
                action_name=cast("DispositionAction", str(r[2])),
                attempts=int(r[3]),
            )
            for r in rows
        ]

    async def mark_abandoned(
        self,
        *,
        action_id: int,
        completed_at: datetime,
        error_message: str,
    ) -> None:
        """Stamp completed_at + last_error WITHOUT bumping attempts.

        Distinct from :meth:`mark_failed` because the abandonment
        decision is the dispatcher's, not a fresh handler attempt —
        attempts already reflects the full retry history.
        """
        await self._conn.execute(
            _SQL_MARK_ABANDONED,
            (iso_z(completed_at), error_message, action_id),
        )


__all__ = ["SqliteSweeperActionRepository"]
