"""Session aggregate — server-side session record (L1-AUTH-002).

The session row stored in the ``sessions`` table holds the SHA-256
*hash* of the token, never the plaintext (L3-AUTH-007). This
aggregate carries only the fields the use cases need to read or
write; the plaintext token is returned exactly once at login by the
``LoginUseCase`` to be set as a cookie by the FastAPI route layer
(Increment 17).

Idle-timeout semantics (L2-AUTH-006 / L3-AUTH-010): each
authenticated request updates ``last_activity_at`` to ``now``;
sessions where ``now - last_activity_at >= idle_timeout`` are
treated as expired and rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Session:
    """One live login session.

    Attributes:
        token_hash: SHA-256 hex digest (64 chars) of the plaintext
            session token. The plaintext lives on the cookie, not in
            the DB; lookup compares the hash of the inbound cookie to
            this column.
        user_id: FK to ``users.user_id``.
        created_at: UTC timestamp at session mint.
        last_activity_at: UTC timestamp updated on every authenticated
            request. Drives the idle-timeout check per L2-AUTH-006.
    """

    token_hash: str
    user_id: int
    created_at: datetime
    last_activity_at: datetime

    def __post_init__(self) -> None:
        """Validate the hash shape and timestamp tz-awareness.

        Raises:
            ValueError: ``token_hash`` is not 64 hex chars, or either
                timestamp is naive, or ``last_activity_at`` precedes
                ``created_at``.
        """
        if len(self.token_hash) != 64:
            raise ValueError(
                f"Session.token_hash must be 64 hex chars (SHA-256); got {len(self.token_hash)}"
            )
        try:
            int(self.token_hash, 16)
        except ValueError as exc:
            raise ValueError("Session.token_hash must be hex") from exc
        if self.created_at.tzinfo is None:
            raise ValueError("Session.created_at must be timezone-aware")
        if self.last_activity_at.tzinfo is None:
            raise ValueError("Session.last_activity_at must be timezone-aware")
        if self.last_activity_at < self.created_at:
            raise ValueError("Session.last_activity_at SHALL NOT precede created_at")


__all__ = ["Session"]
