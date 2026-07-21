"""Use case: ``Login`` — authenticate a user and mint a session token.

The login workflow:

1. Look the user up by email.
2. If absent → audit ``LOGIN_FAILED`` and raise
   :class:`AuthenticationError`. Do **not** distinguish unknown-user
   from wrong-password externally (L3-AUTH-013); the audit detail
   carries the distinction for operator forensics.
3. If disabled → audit ``LOGIN_FAILED`` (with reason) and raise.
4. Verify the password via :class:`PasswordHasher`.
5. On miss → audit ``LOGIN_FAILED`` and raise.
6. On hit → mint a fresh token (256 bits via ``secrets.token_urlsafe``
   per L3-AUTH-006), persist the SHA-256 of it as a :class:`Session`
   row, audit ``LOGIN`` (success), and return the **plaintext** token
   to the caller. The plaintext token never enters persistent storage.

The plaintext token is the cookie value that the FastAPI route layer
(Increment 17) writes to the response. This use case has no knowledge
of HTTP — it only mints and persists.

Requirement references
----------------------
L1-AUTH-001 (local accounts, password verification)
L1-AUTH-002 (server-side sessions)
L2-AUTH-001 (Argon2id verification)
L2-AUTH-003 (no plaintext in audit log)
L2-AUTH-004 (≥128-bit tokens; SHA-256 of token persisted)
L3-AUTH-006 (token_urlsafe(32) → 256 bits of entropy)
L3-AUTH-013 (generic-failure surface, no user-vs-password discrimination)
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.session import Session

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.password_hasher import PasswordHasher
    from message_service.application.ports.unit_of_work import UnitOfWork

_log = structlog.get_logger(__name__)


class AuthenticationError(Exception):
    """Raised when login fails for any reason.

    Bad email, disabled account, or wrong password all surface as the
    same exception. Carries no detail externally per L3-AUTH-013; the
    audit log records the *real* reason via
    ``LOGIN_FAILED.details["reason"]``.
    """


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Returned to the FastAPI route layer on successful login.

    Attributes:
        plaintext_token: The session token to set as a cookie. Lives
            in memory exactly once — the SHA-256 is what's stored.
        user_id: For audit logging on subsequent requests / the
            ``user_id`` contextvar binding (L2-OBS-002).
    """

    plaintext_token: str
    user_id: int


class LoginUseCase:
    """Authenticate + mint session."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        password_hasher: PasswordHasher,
    ) -> None:
        """Bind to its ports.

        Args:
            uow_factory: UoW factory (one transaction per login).
            clock: For session timestamps + audit timestamp.
            password_hasher: Argon2 verifier.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._hasher = password_hasher
        # L3-AUTH-022: a decoy hash used to equalize login timing. The
        # unknown-email path performs a throwaway verify against this so
        # its response time matches the valid-email path (which pays the
        # Argon2 cost); otherwise the absence of that cost is a timing
        # oracle for account enumeration. Generated with the injected
        # hasher so the decoy's cost parameters match live users'. The
        # plaintext is random and discarded, so the decoy never verifies.
        self._decoy_hash = password_hasher.hash(Password(secrets.token_urlsafe(32)))

    async def execute(self, *, email: str, password: Password) -> LoginResult:
        """Authenticate ``email`` + ``password`` and return a fresh session.

        Args:
            email: The login email.
            password: Plaintext-wrapper for the password.

        Returns:
            :class:`LoginResult` with the plaintext token + user_id.

        Raises:
            AuthenticationError: Authentication failed for any reason.
                The audit log carries the real cause; the exception
                deliberately does not.
        """
        # Failure paths must commit the LOGIN_FAILED audit row before
        # surfacing the AuthenticationError; raising inside the UoW
        # would roll the audit back. We use an explicit per-branch
        # commit + raise outside the with-block (the with-block
        # auto-commits the success path).
        async with self._uow_factory() as uow:
            user = await uow.user_repo.get_by_email(email)
            now = self._clock.now()

            if user is None:
                # L3-AUTH-022: pay the Argon2 cost against a decoy so the
                # unknown-email response time matches the valid-email path.
                self._hasher.verify(password, self._decoy_hash)
                await self._audit_failure(uow, now, email, reason="unknown_email")
                await uow.commit()
                raise AuthenticationError("invalid credentials")
            if user.disabled:
                # L3-AUTH-022: still verify (against the account's real
                # hash) so a disabled account is timing-indistinguishable
                # from an enabled one; the result is discarded.
                self._hasher.verify(password, user.password_hash)
                await self._audit_failure(
                    uow, now, email, reason="account_disabled", user_id=user.user_id
                )
                await uow.commit()
                raise AuthenticationError("invalid credentials")
            if not self._hasher.verify(password, user.password_hash):
                await self._audit_failure(
                    uow, now, email, reason="bad_password", user_id=user.user_id
                )
                await uow.commit()
                raise AuthenticationError("invalid credentials")

            # All clear — mint and persist the session.
            assert user.user_id is not None  # invariant: persisted users have ids
            plaintext_token = secrets.token_urlsafe(32)  # L3-AUTH-006
            token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
            session = Session(
                token_hash=token_hash,
                user_id=user.user_id,
                created_at=now,
                last_activity_at=now,
            )
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.LOGIN,
                    actor=f"user:{user.user_id}",
                    resource=f"user:{user.user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={"email": email},
                )
            )
            await uow.session_repo.save(session)

        _log.info("login_success", user_id=user.user_id, email=email)
        return LoginResult(plaintext_token=plaintext_token, user_id=user.user_id)

    async def _audit_failure(
        self,
        uow: UnitOfWork,
        now: datetime,
        email: str,
        *,
        reason: str,
        user_id: int | None = None,
    ) -> None:
        """Record a LOGIN_FAILED audit event before raising.

        Per L2-OBS-017's audit-content rule for ``LOGIN_FAILED``: actor
        is ``username:<email>`` (no user_id, since auth was rejected).
        The reason field carries operator-only context (unknown email
        vs. bad password vs. disabled).
        """
        await uow.audit_log.record(
            AuditEvent(
                timestamp=now,
                action=AuditAction.LOGIN_FAILED,
                actor=f"username:{email}",
                resource=f"username:{email}" if user_id is None else f"user:{user_id}",
                outcome=AuditOutcome.FAILURE,
                details={"email": email, "reason": reason},
            )
        )
        _log.warning("login_failed", email=email, reason=reason, user_id=user_id)


__all__ = ["AuthenticationError", "LoginResult", "LoginUseCase"]
