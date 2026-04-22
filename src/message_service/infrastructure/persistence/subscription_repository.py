"""Concrete :class:`SubscriptionRepository` backed by SQLite.

The interesting method is :meth:`list_recipients_for_run`, which
resolves a run's subscriber email list via a single
``SELECT DISTINCT`` joining ``users`` and ``subscriptions`` with three
OR'd predicates over ``granularity`` (L3-SUB-005).

Edge cases in that query:

* ``tags`` is an empty frozenset — drop the TAG branch from the
  ``WHERE`` clause entirely. An empty ``IN ()`` is a SQL syntax error,
  so we must short-circuit in Python, not try to pass zero parameters.
* A user matches multiple subscription rules for the same run — return
  one row thanks to ``SELECT DISTINCT``.
* Disabled users — excluded by ``u.disabled = 0`` (L3-SUB-017).

The ``add`` method uses a straight ``INSERT`` and relies on the
partial unique indexes in ``001_initial_schema.sql`` to reject
duplicates. :class:`sqlite3.IntegrityError` is translated to
:class:`PersistenceError` with the offending values in ``details``.

Requirement references
----------------------
L1-SUB-004 (recipient resolution)
L2-SUB-003 (predicate-based selection)
L3-SUB-001 (uniqueness)
L3-SUB-003 (created_at captured by persistence)
L3-SUB-005 (single-query resolution with disabled-user exclusion)
L3-SUB-017 (users.disabled column)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

import aiosqlite

from message_service.application.ports.clock import iso_z
from message_service.application.ports.subscription_repository import (
    SubscriptionRepository,
)
from message_service.domain.aggregates.subscription import (
    Subscription,
    SubscriptionGranularity,
)
from message_service.domain.errors import PersistenceError
from message_service.domain.ids import SubscriptionId, UserId
from message_service.infrastructure.persistence._helpers import parse_iso_z

# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------

_SQL_INSERT = """
INSERT INTO subscriptions (user_id, granularity, target_value, created_at)
VALUES (?, ?, ?, ?)
"""

_SQL_DELETE_BY_ID = """
DELETE FROM subscriptions WHERE subscription_id = ?
"""

_SQL_SELECT_BY_USER = """
SELECT subscription_id, user_id, granularity, target_value, created_at
FROM subscriptions
WHERE user_id = ?
ORDER BY subscription_id ASC
"""

_SQL_SELECT_BY_ID = """
SELECT subscription_id, user_id, granularity, target_value, created_at
FROM subscriptions
WHERE subscription_id = ?
"""


class SqliteSubscriptionRepository(SubscriptionRepository):
    """SQLite-backed :class:`SubscriptionRepository`."""

    def __init__(self, conn: aiosqlite.Connection, *, clock: object) -> None:
        """Bind to a connection.

        Args:
            conn: Open, PRAGMA-configured connection inside a UoW
                transaction.
            clock: :class:`Clock` port used to stamp ``created_at`` on
                :meth:`add`. Declared as ``object`` to avoid a
                runtime-cycle import; the bootstrap wires the concrete
                ``SystemClock``.
        """
        self._conn = conn
        self._clock = clock

    # -- list_recipients_for_run ----------------------------------------

    async def list_recipients_for_run(
        self, pipeline_type: str, tags: frozenset[str]
    ) -> frozenset[str]:
        """Resolve distinct recipient emails matching any subscription predicate."""
        # Build the WHERE clause dynamically. GLOBAL and PIPELINE
        # branches are always present; TAG branch only when ``tags``
        # is non-empty.
        branches: list[str] = [
            "s.granularity = 'GLOBAL'",
            "(s.granularity = 'PIPELINE' AND s.target_value = ?)",
        ]
        params: list[str | int] = [pipeline_type]
        if tags:
            # Deterministic parameter order for log-diffing.
            sorted_tags = sorted(tags)
            placeholders = ", ".join("?" * len(sorted_tags))
            branches.append(f"(s.granularity = 'TAG' AND s.target_value IN ({placeholders}))")
            params.extend(sorted_tags)

        where_or = " OR ".join(branches)
        sql = f"""
            SELECT DISTINCT u.email
            FROM users u
            JOIN subscriptions s ON s.user_id = u.user_id
            WHERE u.disabled = 0
              AND ({where_or})
        """
        async with self._conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return frozenset(row["email"] for row in rows)

    # -- list_for_user --------------------------------------------------

    async def list_for_user(self, user_id: UserId) -> Sequence[Subscription]:  # noqa: D102
        async with self._conn.execute(_SQL_SELECT_BY_USER, (user_id,)) as cur:
            rows = await cur.fetchall()
        return [_row_to_subscription(r) for r in rows]

    # -- add -------------------------------------------------------------

    async def add(
        self,
        user_id: UserId,
        granularity: SubscriptionGranularity,
        target_value: str | None,
    ) -> Subscription:
        """Insert a subscription; stamp ``created_at`` from the injected clock."""
        # Defense-in-depth: the Subscription aggregate validates
        # granularity/target_value consistency. We pre-check so a bad
        # combo doesn't reach SQL at all.
        if granularity is SubscriptionGranularity.GLOBAL:
            if target_value is not None:
                raise PersistenceError(
                    "GLOBAL subscription cannot have a target_value",
                    details={"granularity": granularity.value, "target_value": target_value},
                )
        elif not target_value:
            raise PersistenceError(
                f"{granularity.value} subscription requires non-empty target_value",
                details={"granularity": granularity.value, "target_value": target_value},
            )

        # Clock-port call. Avoid importing the concrete Clock ABC
        # because this module is in the adapter layer; the bootstrap
        # supplies a real Clock instance and we just call .now().
        now = self._clock.now()  # type: ignore[attr-defined]
        created_at_iso = iso_z(now)

        try:
            cur = await self._conn.execute(
                _SQL_INSERT,
                (user_id, granularity.value, target_value, created_at_iso),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(
                f"duplicate subscription for user {user_id} "
                f"({granularity.value}, {target_value!r})",
                details={
                    "user_id": user_id,
                    "granularity": granularity.value,
                    "target_value": target_value,
                    "reason": str(exc),
                },
            ) from exc

        sub_id = cur.lastrowid
        if sub_id is None:
            # lastrowid should always be set after an INSERT on a
            # table with AUTOINCREMENT; if not, something is badly
            # wrong.
            raise PersistenceError(
                "INSERT did not return a subscription_id",
                details={"user_id": user_id, "granularity": granularity.value},
            )

        return Subscription(
            subscription_id=SubscriptionId(sub_id),
            user_id=user_id,
            granularity=granularity,
            target_value=target_value,
            created_at=now,
        )

    # -- remove ----------------------------------------------------------

    async def remove(self, subscription_id: SubscriptionId) -> None:  # noqa: D102
        # Idempotent per port contract: deleting a missing subscription
        # is not an error.
        await self._conn.execute(_SQL_DELETE_BY_ID, (subscription_id,))


# -----------------------------------------------------------------------------
# Row -> aggregate
# -----------------------------------------------------------------------------


def _row_to_subscription(row: aiosqlite.Row) -> Subscription:
    """Build a :class:`Subscription` from a ``subscriptions`` row."""
    try:
        granularity = SubscriptionGranularity(row["granularity"])
    except ValueError as exc:
        raise PersistenceError(
            f"persisted subscription has unknown granularity {row['granularity']!r}",
            details={
                "subscription_id": row["subscription_id"],
                "granularity": row["granularity"],
            },
        ) from exc

    try:
        return Subscription(
            subscription_id=SubscriptionId(row["subscription_id"]),
            user_id=UserId(row["user_id"]),
            granularity=granularity,
            target_value=row["target_value"],
            created_at=parse_iso_z(row["created_at"]),
        )
    except ValueError as exc:
        raise PersistenceError(
            f"persisted subscription violates aggregate invariants: {exc}",
            details={
                "subscription_id": row["subscription_id"],
                "reason": str(exc),
            },
        ) from exc


__all__ = ["SqliteSubscriptionRepository"]
