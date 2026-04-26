"""SQLite adapter for :class:`UserRepository`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.user_repository import UserRepository
from message_service.domain.aggregates.user import User
from message_service.domain.errors import PersistenceError
from message_service.infrastructure.persistence._helpers import parse_iso_z

if TYPE_CHECKING:
    pass


_SQL_INSERT = """
INSERT INTO users (email, display_name, password_hash, is_admin, disabled, created_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

_SQL_SELECT_BASE = """
SELECT user_id, email, display_name, password_hash, is_admin, disabled, created_at
FROM users
"""


class SqliteUserRepository(UserRepository):
    """SQLite-backed :class:`UserRepository`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        """Bind to a UoW-scoped connection."""
        self._conn = conn

    async def save(self, user: User) -> User:  # noqa: D102
        if user.user_id is not None:
            raise ValueError(
                f"SqliteUserRepository.save expects new users (user_id=None); "
                f"got user_id={user.user_id}"
            )
        try:
            cur = await self._conn.execute(
                _SQL_INSERT,
                (
                    user.email,
                    user.display_name,
                    user.password_hash,
                    1 if user.is_admin else 0,
                    1 if user.disabled else 0,
                    iso_z(user.created_at),
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise PersistenceError(
                f"failed to insert user {user.email!r}: {exc}",
                details={"email": user.email, "reason": str(exc)},
            ) from exc
        new_id = cur.lastrowid
        if new_id is None:
            raise PersistenceError(
                "user insert succeeded but lastrowid is None",
                details={"email": user.email},
            )
        # Return the same aggregate with user_id populated. Frozen
        # dataclass — construct a fresh instance.
        return User(
            email=user.email,
            display_name=user.display_name,
            password_hash=user.password_hash,
            created_at=user.created_at,
            user_id=int(new_id),
            is_admin=user.is_admin,
            disabled=user.disabled,
        )

    async def get_by_email(self, email: str) -> User | None:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BASE + "WHERE email = ?", (email,)) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def get_by_id(self, user_id: int) -> User | None:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BASE + "WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def update(  # noqa: D102
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        is_admin: bool | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> User | None:
        # Build the SET clause from the supplied (non-None) fields.
        # Empty-PATCH case: skip the UPDATE entirely and just re-read.
        sets: list[str] = []
        params: list[object] = []
        if display_name is not None:
            sets.append("display_name = ?")
            params.append(display_name)
        if is_admin is not None:
            sets.append("is_admin = ?")
            params.append(1 if is_admin else 0)
        if disabled is not None:
            sets.append("disabled = ?")
            params.append(1 if disabled else 0)
        if password_hash is not None:
            sets.append("password_hash = ?")
            params.append(password_hash)

        if sets:
            params.append(user_id)
            sql = f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?"
            try:
                cur = await self._conn.execute(sql, tuple(params))
            except aiosqlite.IntegrityError as exc:
                raise PersistenceError(
                    f"failed to update user {user_id}: {exc}",
                    details={"user_id": user_id, "reason": str(exc)},
                ) from exc
            if cur.rowcount == 0:
                # No row updated → user_id did not exist.
                return None

        return await self.get_by_id(user_id)


def _row_to_user(row: aiosqlite.Row) -> User:
    """Map a SELECT row tuple back into a :class:`User`."""
    user_id, email, display_name, password_hash, is_admin, disabled, created_at = row
    return User(
        user_id=int(user_id),
        email=str(email),
        display_name=str(display_name),
        password_hash=str(password_hash),
        is_admin=bool(int(is_admin)),
        disabled=bool(int(disabled)),
        created_at=parse_iso_z(str(created_at)),
    )


__all__ = ["SqliteUserRepository"]
