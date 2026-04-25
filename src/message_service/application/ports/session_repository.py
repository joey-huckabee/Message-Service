"""Port: persistence for :class:`Session` records.

Session rows are looked up by token-hash on every authenticated
request, updated (``last_activity_at``) on every request, and deleted
on logout / expiry / mass-logout flows.

The plaintext token never enters the repository. Callers SHALL hash
the inbound cookie (SHA-256) and pass only the hash to
:meth:`get_by_token_hash` and :meth:`delete_by_token_hash`.

Requirement references
----------------------
L1-AUTH-002 (server-side sessions, idle-timeout)
L2-AUTH-004 (server-side; ≥128 bits entropy)
L2-AUTH-006 (per-request idle-timeout check)
L3-AUTH-007 (token-hash storage, never plaintext)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from message_service.domain.aggregates.session import Session


class SessionRepository(ABC):
    """Abstract repository for :class:`Session`."""

    @abstractmethod
    async def save(self, session: Session) -> None:
        """Insert a fresh session row.

        Raises:
            PersistenceError: Insert failed; typically a duplicate
                ``token_hash`` (statistically improbable but treated
                as an error rather than a no-op).
        """

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session for this token hash, or ``None`` if absent.

        The caller has already hashed the plaintext cookie. The repo
        does NOT update ``last_activity_at`` on read — that's
        :meth:`touch`'s job, and it's an explicit decision because not
        every read advances activity (e.g., the FastAPI dependency
        layer reads on every request, but a separate "list my
        sessions" endpoint should not advance activity).
        """

    @abstractmethod
    async def touch(self, token_hash: str, now: datetime) -> None:
        """Update ``last_activity_at`` to ``now`` for the given session.

        Idempotent — calling twice with the same ``now`` produces the
        same end state. No-op if the token has been deleted in the
        meantime (race with logout); callers do not need to check
        existence first.
        """

    @abstractmethod
    async def delete_by_token_hash(self, token_hash: str) -> None:
        """Remove a session row. Logout uses this; expiry sweeps too.

        No-op if the session is already absent (race-tolerant).
        """

    @abstractmethod
    async def delete_expired(self, *, idle_threshold: datetime) -> int:
        """Delete every session whose ``last_activity_at < idle_threshold``.

        Returns the number of rows deleted (useful for metrics +
        log lines from the cleanup task).
        """


__all__ = ["SessionRepository"]
