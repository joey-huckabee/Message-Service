"""User aggregate — local-account authentication target (L1-AUTH-001).

The aggregate carries the persistent fields stored in the ``users``
table (extended by migration 003 with ``password_hash`` and
``is_admin``). Authentication flows look users up by email; the use
case then uses the :class:`PasswordHasher` port to verify the
provided plaintext against the stored hash.

The aggregate intentionally does not hold a :class:`Password` —
plaintext exists only on the request path, never on the persistent
record. ``password_hash`` is opaque from this layer's perspective;
its format is owned by the hasher adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class User:
    """A local user account.

    Attributes:
        user_id: Auto-incrementing primary key from the ``users``
            table. ``None`` for unsaved instances; the SQLite repo
            stamps the value on insert.
        email: Login identifier. Unique across the table (enforced at
            the SQL level by ``UNIQUE`` on ``users.email``).
        display_name: Human-readable name, shown in the dashboard.
            Required, non-empty.
        password_hash: Argon2id hash string in the standard PHC format
            (`$argon2id$v=19$...$...$...`). Empty string for accounts
            that have not yet set a password (only legitimately the
            case for migrated rows; see migration 003 default).
        is_admin: True for accounts with administrative dashboard
            access (L1-DASH-004). Defaults to False.
        disabled: True if the account is locked out — login attempts
            return failure regardless of password validity.
        created_at: UTC timestamp at row insert.
    """

    email: str
    display_name: str
    password_hash: str
    created_at: datetime
    user_id: int | None = None
    is_admin: bool = False
    disabled: bool = False

    def __post_init__(self) -> None:
        """Validate required-non-empty fields and timestamp tz-awareness.

        Raises:
            ValueError: empty ``email`` / ``display_name``, or naive
                ``created_at``.
        """
        if not self.email:
            raise ValueError("User.email must be non-empty")
        if not self.display_name:
            raise ValueError("User.display_name must be non-empty")
        if self.created_at.tzinfo is None:
            raise ValueError("User.created_at must be timezone-aware")


__all__ = ["User"]
