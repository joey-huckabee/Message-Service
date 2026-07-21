"""SQLite adapter for :class:`SessionRepository`."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.session_repository import SessionRepository
from message_service.domain.aggregates.session import Session
from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence._helpers import parse_iso_z

_SQL_INSERT = """
INSERT INTO sessions (token_hash, user_id, created_at, last_activity_at)
VALUES (?, ?, ?, ?)
"""

_SQL_SELECT = """
SELECT token_hash, user_id, created_at, last_activity_at
FROM sessions
WHERE token_hash = ?
"""

_SQL_TOUCH = """
UPDATE sessions
SET last_activity_at = ?
WHERE token_hash = ?
"""

_SQL_DELETE = """
DELETE FROM sessions WHERE token_hash = ?
"""

_SQL_DELETE_EXPIRED = """
DELETE FROM sessions WHERE last_activity_at < ?
"""

_SQL_DELETE_BY_USER = """
DELETE FROM sessions WHERE user_id = ?
"""


class SqliteSessionRepository(SessionRepository):
    """SQLite-backed :class:`SessionRepository`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to a UoW-scoped connection."""
        self._conn = conn

    async def save(self, session: Session) -> None:  # noqa: D102
        # Wrap IntegrityError (duplicate token_hash PK, or the
        # last_activity_at >= created_at CHECK) in PersistenceError so the
        # port's declared contract holds — a raw aiosqlite.IntegrityError must
        # not leak past the adapter boundary (matches SqliteUserRepository).
        try:
            await self._conn.execute(
                _SQL_INSERT,
                (
                    session.token_hash,
                    session.user_id,
                    iso_z(session.created_at),
                    iso_z(session.last_activity_at),
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise PersistenceError(
                f"failed to insert session for user {session.user_id}: {exc}",
                details={"user_id": session.user_id, "reason": str(exc)},
            ) from exc

    async def get_by_token_hash(self, token_hash: str) -> Session | None:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT, (token_hash,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        token_hash_value, user_id, created_at, last_activity_at = row
        return Session(
            token_hash=str(token_hash_value),
            user_id=int(user_id),
            created_at=parse_iso_z(str(created_at)),
            last_activity_at=parse_iso_z(str(last_activity_at)),
        )

    async def touch(self, token_hash: str, now: datetime) -> None:  # noqa: D102
        # Wrap IntegrityError (the ``last_activity_at >= created_at`` CHECK can
        # fail if the wall clock steps backward, e.g. an NTP correction) so the
        # port's PersistenceError contract holds, as in ``save``.
        try:
            await self._conn.execute(_SQL_TOUCH, (iso_z(now), token_hash))
        except aiosqlite.IntegrityError as exc:
            raise PersistenceError(
                f"failed to touch session: {exc}",
                details={"reason": str(exc)},
            ) from exc

    async def delete_by_token_hash(self, token_hash: str) -> None:  # noqa: D102
        await self._conn.execute(_SQL_DELETE, (token_hash,))

    async def delete_expired(self, *, idle_threshold: datetime) -> int:  # noqa: D102
        cur = await self._conn.execute(_SQL_DELETE_EXPIRED, (iso_z(idle_threshold),))
        return cur.rowcount

    async def delete_by_user_id(self, user_id: int) -> int:  # noqa: D102
        cur = await self._conn.execute(_SQL_DELETE_BY_USER, (user_id,))
        return cur.rowcount


__all__ = ["SqliteSessionRepository"]
