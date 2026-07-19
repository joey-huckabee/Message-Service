"""Port: persistence for :class:`User` records.

Login looks users up by email; admin flows look them up by id;
``list_paginated`` backs the admin recipient console (L1-DASH-008 /
L2-DASH-021).

Requirement references
----------------------
L1-AUTH-001 (local accounts)
L1-PERS-003 (repository pattern)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from message_service.domain.aggregates.user import User


class UserRepository(ABC):
    """Abstract repository for :class:`User`."""

    @abstractmethod
    async def save(self, user: User) -> User:
        """Insert a new user; return the persisted aggregate with ``user_id`` set.

        Args:
            user: A :class:`User` whose ``user_id`` is ``None``. Other
                fields are persisted as-is. Email uniqueness is
                enforced at the SQL layer; a duplicate raises
                ``PersistenceError`` (with the underlying constraint
                violation in ``details``).

        Returns:
            The same aggregate with ``user_id`` populated.

        Raises:
            ValueError: ``user.user_id`` is not None.
            PersistenceError: Insert failed (typically duplicate email).
        """

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None:
        """Return the user with this email, or ``None`` if not found.

        Email comparison is case-sensitive in v1 (no
        normalization). Future increments may add a normalized
        lower-case lookup column if operators report duplicate-on-case
        confusion.
        """

    @abstractmethod
    async def get_by_id(self, user_id: int) -> User | None:
        """Return the user with this primary key, or ``None`` if not found."""

    @abstractmethod
    async def update(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        is_admin: bool | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> User | None:
        """Update specified columns; ``None`` arguments leave them unchanged.

        Used by Increment 20b's admin user-management routes
        (L1-AUTH-003 / L2-AUTH-007). The ``email`` and ``created_at``
        columns are deliberately omitted from this signature — both
        are immutable in v1 (per L2-AUTH-007's "email is not mutable"
        clause; ``created_at`` is set once at insertion).

        When every keyword argument is ``None`` (the empty-PATCH
        case per L3-AUTH-015) the implementation SHALL skip the
        UPDATE statement and just return the existing row.

        Args:
            user_id: The user's primary key.
            display_name: Replacement display name, or ``None`` to keep.
            is_admin: Replacement admin flag, or ``None`` to keep.
            disabled: Replacement disabled flag, or ``None`` to keep.
            password_hash: Replacement Argon2id PHC string, or ``None``
                to keep. Callers SHALL produce this hash via the
                ``PasswordHasher`` chokepoint per L3-AUTH-016 — the
                repo does NOT hash; it only persists the bytes the
                caller hands it.

        Returns:
            The updated :class:`User` aggregate if ``user_id`` exists;
            ``None`` if the user was not found (caller surfaces 404).

        Raises:
            PersistenceError: SQL execution failed for any reason
                other than user-not-found.
        """

    @abstractmethod
    async def list_paginated(self, *, limit: int, offset: int) -> Sequence[User]:
        """Return a page of accounts ordered by ``user_id`` ascending (L3-DASH-042).

        Backs the admin recipient console's roster view. The returned
        aggregates carry ``password_hash`` like every other repo read;
        the no-hash-on-the-wire guarantee is enforced by the list
        endpoint's response projection (L3-DASH-043), not here.

        Args:
            limit: Maximum accounts to return (the route constrains this
                to ``[1, 200]`` per L3-DASH-043).
            offset: Number of rows to skip (the route constrains this to
                ``>= 0``).

        Returns:
            At most ``limit`` :class:`User` aggregates, ``user_id`` ascending.
        """


__all__ = ["UserRepository"]
