"""Port: persistence for :class:`User` records.

Login looks users up by email; admin flows look them up by id. There
is no ``list_all`` method in v1 — listings happen via direct SQL on
the dashboard's "user management" page (Increment 19) where pagination
matters.

Requirement references
----------------------
L1-AUTH-001 (local accounts)
L1-PERS-003 (repository pattern)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

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


__all__ = ["UserRepository"]
