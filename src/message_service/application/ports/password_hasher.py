"""Port: password hashing + verification (L1-AUTH-001).

Use cases call :meth:`hash` once at user creation and :meth:`verify`
on every login attempt. The plaintext :class:`Password` is wrapped
to avoid leaking via repr; the adapter calls ``.reveal()`` exactly
once per call.

Implementations
---------------
* :class:`~message_service.infrastructure.auth.argon2_hasher.Argon2PasswordHasher`
  — production adapter using ``argon2-cffi`` per L2-AUTH-001.

Requirement references
----------------------
L1-AUTH-001 (local-account auth, Argon2)
L2-AUTH-001 (argon2-cffi, Argon2id variant)
L2-AUTH-002 (configurable cost parameters)
L2-AUTH-003 (no plaintext in logs / audit / DB)
L3-AUTH-001 (PasswordHasher singleton)
L3-AUTH-013 (verify catches VerifyMismatchError → generic invalid)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from message_service.domain.aggregates.password import Password


class PasswordHasher(ABC):
    """Abstract password-hashing port."""

    @abstractmethod
    def hash(self, password: Password) -> str:
        """Compute the Argon2id PHC string for ``password``.

        Args:
            password: Plaintext wrapper. Adapter calls ``.reveal()``
                exactly once.

        Returns:
            The encoded hash string (PHC format, e.g.,
            ``"$argon2id$v=19$m=65536,t=3,p=4$..."``). Suitable for
            direct storage in ``users.password_hash``.

        Raises:
            ValueError: Hashing parameters reject the input (e.g.,
                empty plaintext slipped past validation).
        """

    @abstractmethod
    def verify(self, password: Password, encoded_hash: str) -> bool:
        """Constant-time compare ``password`` against the stored hash.

        Per L3-AUTH-013, the adapter SHALL catch
        ``argon2.exceptions.VerifyMismatchError`` and translate to a
        plain ``False`` return — call sites surface a generic "invalid
        credentials" message that doesn't distinguish unknown-user
        from wrong-password.

        Args:
            password: Plaintext wrapper.
            encoded_hash: PHC string produced by :meth:`hash`.

        Returns:
            ``True`` if the plaintext matches the stored hash;
            ``False`` otherwise. NEVER raises on mismatch — only on
            malformed-hash structural errors.

        Raises:
            ValueError: ``encoded_hash`` is not a valid PHC string.
        """


__all__ = ["PasswordHasher"]
