"""Password value object — a redacted-`__repr__` wrapper for plaintext.

Defense-in-depth against accidental disclosure via logging or `repr()`
(L2-AUTH-003 / L3-AUTH-004). The object holds plaintext for exactly
the duration of the verify-or-hash call; persistent storage is always
the Argon2id hash, never the plaintext.

Construction is intentionally cheap (no validation beyond non-empty)
so this type can wrap untrusted input early in the request path
without forcing every call site to handle a domain error.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Password:
    """Wraps plaintext password with redacted ``__repr__`` and ``__str__``.

    Use this type at every boundary where a plaintext password crosses
    layers — request DTO → use case → password hasher port. The
    plaintext is exposed only via :meth:`reveal`, which is the explicit
    "I know what I'm doing" escape hatch the hasher adapter calls.

    Attributes:
        _plaintext: The plaintext password. Underscored to discourage
            direct attribute access; use :meth:`reveal` instead.
    """

    _plaintext: str

    def __post_init__(self) -> None:
        """Reject empty passwords at construction.

        Raises:
            ValueError: ``_plaintext`` is empty.
        """
        if not self._plaintext:
            raise ValueError("Password plaintext must be non-empty")

    def __repr__(self) -> str:
        """L3-AUTH-004: hide value from default repr."""
        return "<Password>"

    def __str__(self) -> str:
        """L3-AUTH-004: hide value from default str."""
        return "<Password>"

    def reveal(self) -> str:
        """Return the plaintext.

        Call sites SHALL be limited to the Argon2 hash/verify boundary
        in the password hasher adapter.
        """
        return self._plaintext

    def constant_time_equals(self, other: Password) -> bool:
        """Constant-time comparison of two Password instances.

        Per L3-AUTH-004's pairing with ``secrets.compare_digest``. Used
        for direct password comparison in tests and rare equality
        checks; production auth flows go through the hasher's
        ``verify`` method instead (which is itself constant-time).
        """
        return secrets.compare_digest(self._plaintext, other._plaintext)


__all__ = ["Password"]
