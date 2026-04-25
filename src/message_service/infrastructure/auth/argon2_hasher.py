"""Argon2id PasswordHasher adapter (L1-AUTH-001 / L2-AUTH-001).

Wraps ``argon2.PasswordHasher`` so cost parameters come from config
rather than the library's defaults. The adapter is a service-scoped
singleton (per L3-AUTH-001) — bootstrap constructs one and shares it
across every use case that needs hashing.

Cost parameter rationale (defaults pinned by L3-AUTH-002):

* ``memory_cost=65536`` (64 MiB)
* ``time_cost=3`` iterations
* ``parallelism=4``
* ``hash_len=32`` bytes
* ``salt_len=16`` bytes

These values comfortably exceed OWASP minimums for password hashing
on modern server hardware. L3-AUTH-003 prescribes a benchmark window
of 50-500 ms per hash on CI hardware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from argon2 import PasswordHasher as _Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from message_service.application.ports.password_hasher import PasswordHasher

if TYPE_CHECKING:
    from message_service.domain.aggregates.password import Password


class Argon2PasswordHasher(PasswordHasher):
    """Argon2id-backed :class:`PasswordHasher`."""

    def __init__(
        self,
        *,
        memory_cost: int = 65_536,
        time_cost: int = 3,
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16,
    ) -> None:
        """Construct with cost parameters; defaults match L3-AUTH-002."""
        self._impl = _Argon2PasswordHasher(
            memory_cost=memory_cost,
            time_cost=time_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
        )

    def hash(self, password: Password) -> str:  # noqa: D102
        # argon2.PasswordHasher.hash returns the encoded PHC string.
        return self._impl.hash(password.reveal())

    def verify(self, password: Password, encoded_hash: str) -> bool:  # noqa: D102
        if not encoded_hash:
            # Empty hash — never matches. Migration 003 leaves
            # password_hash='' on existing rows; this guard is the
            # explicit "no-password accounts cannot authenticate"
            # defense.
            return False
        try:
            return self._impl.verify(encoded_hash, password.reveal())
        except VerifyMismatchError:
            # L3-AUTH-013: translate to generic False; call sites
            # surface a generic "invalid credentials" message.
            return False
        except InvalidHashError as exc:
            # Malformed stored hash — structural error, not a bad
            # password. Bubble up as ValueError per port contract.
            raise ValueError(f"malformed password hash: {exc}") from exc


__all__ = ["Argon2PasswordHasher"]
