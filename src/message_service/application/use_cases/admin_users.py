"""Use cases: admin user management (Increment 20b).

Three closely-related use cases share this module — they all consume
the same ports (``UnitOfWork``, ``Clock``, ``PasswordHasher``), produce
the same shape of audit record, and serve the same dashboard router
(``interfaces/rest/routes/admin_users.py``). Splitting them across
three files would multiply boilerplate without aiding readability.

* :class:`CreateUserUseCase` — admin-driven user creation. Validates
  the email syntactic format, hashes the password through the shared
  :class:`PasswordHasher` chokepoint, persists, and emits a
  ``CREATE_USER`` audit record.
* :class:`UpdateUserUseCase` — admin-driven mutation of
  ``display_name``, ``is_admin``, or ``disabled`` (every field
  optional; empty PATCH is a no-op success). Enforces the
  self-protection guardrail from L2-AUTH-009: an admin cannot
  remove their own ``is_admin`` or set ``disabled=True`` on their
  own account; both attempts raise :class:`SelfProtectionError` and
  emit no audit record.
* :class:`ResetPasswordUseCase` — admin-driven password set. Hashes
  through the same chokepoint as login and create, persists the new
  hash, and emits an ``UPDATE_USER`` audit record with
  ``mutated_fields=['password_hash']``.

Common contract:

* ``actor`` is ``user:<admin_id>`` (the requesting administrator).
* ``resource`` is ``user:<target_user_id>``.
* ``outcome`` is ``SUCCESS`` (the L2-AUTH-009 self-protection rejection
  raises before reaching the audit code path).
* ``details`` carries at minimum ``target_user_id``; ``CREATE_USER``
  also carries ``target_email`` for human-scannability of audit logs;
  ``UPDATE_USER`` carries ``mutated_fields`` (sorted list of column
  names actually changed).
* The plaintext password and its hash NEVER appear in ``details`` (per
  L2-AUTH-008 / L3-AUTH-016 / L3-OBS-036).

Requirement references
----------------------
L1-AUTH-003 (admin user management)
L2-AUTH-007, L2-AUTH-008, L2-AUTH-009
L3-AUTH-014, L3-AUTH-015, L3-AUTH-016, L3-AUTH-017
L3-OBS-035 (audit-record format inherited)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.user import User
from message_service.domain.errors import (
    DuplicateEmailError,
    InvalidEmailError,
    PersistenceError,
    SelfProtectionError,
    UserNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.password_hasher import PasswordHasher
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.password import Password

_log = structlog.get_logger(__name__)


# Minimal RFC-5322-flavored email check. Same shape used by the
# existing config validator for ``mail.from_address``; v1 deliberately
# does not attempt full RFC compliance (which is impractical) — it
# rejects the obviously-malformed and accepts the rest, leaving
# deliverability concerns to the SMTP relay.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _validate_email(email: str) -> None:
    """Raise ``InvalidEmailError`` if ``email`` is syntactically malformed."""
    if not _EMAIL_PATTERN.match(email):
        raise InvalidEmailError(
            f"email is not a valid address: {email!r}",
            details={"email": email},
        )


# -----------------------------------------------------------------------------
# Result records
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateUserResult:
    """Returned to the route layer on a successful create.

    Attributes:
        user_id: The newly-minted primary key.
        email: Echoed for the response body (no password material).
        display_name: Echoed.
        is_admin: Echoed.
        disabled: Echoed.
    """

    user_id: int
    email: str
    display_name: str
    is_admin: bool
    disabled: bool


@dataclass(frozen=True, slots=True)
class UpdateUserResult:
    """Returned on a successful update.

    Attributes:
        user_id: The updated user's primary key.
        email: Echoed for the response body (immutable; informational).
        display_name: Current value.
        is_admin: Current value.
        disabled: Current value.
        mutated_fields: Sorted list of column names actually changed.
            Empty list for an empty-PATCH no-op success.
    """

    user_id: int
    email: str
    display_name: str
    is_admin: bool
    disabled: bool
    mutated_fields: tuple[str, ...]


# -----------------------------------------------------------------------------
# CreateUserUseCase
# -----------------------------------------------------------------------------


class CreateUserUseCase:
    """Admin-driven user creation per L1-AUTH-003 / L2-AUTH-007."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        password_hasher: PasswordHasher,
    ) -> None:
        """Bind ports.

        Args:
            uow_factory: One UoW per create (insert + audit).
            clock: For the audit timestamp.
            password_hasher: The shared Argon2id hasher (L1-AUTH-001).
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._hasher = password_hasher

    async def execute(
        self,
        *,
        admin_id: int,
        email: str,
        display_name: str,
        password: Password,
        is_admin: bool,
        disabled: bool,
    ) -> CreateUserResult:
        """Create a user, hash + persist the password, audit, return.

        Args:
            admin_id: The requesting administrator's user_id (audit
                ``actor``).
            email: Target email; rejected if syntactically invalid or
                already in use.
            display_name: Target display name.
            password: Plaintext-wrapper; hashed before persistence.
            is_admin: Whether the new user is an admin.
            disabled: Whether the new user is disabled.

        Returns:
            :class:`CreateUserResult` with the assigned ``user_id``.

        Raises:
            InvalidEmailError: ``email`` failed the syntactic check.
            DuplicateEmailError: ``email`` is already in use.
        """
        _validate_email(email)
        password_hash = self._hasher.hash(password)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            try:
                saved = await uow.user_repo.save(
                    User(
                        email=email,
                        display_name=display_name,
                        password_hash=password_hash,
                        created_at=now,
                        is_admin=is_admin,
                        disabled=disabled,
                    ),
                )
            except PersistenceError as exc:
                # SqliteUserRepository wraps the UNIQUE-constraint
                # IntegrityError as PersistenceError. Retranslate the
                # email-uniqueness case to the dedicated 409 error;
                # other persistence failures propagate.
                reason = exc.details.get("reason", "")
                if "UNIQUE" in reason and "email" in reason:
                    raise DuplicateEmailError(
                        f"email already in use: {email!r}",
                        details={"email": email},
                    ) from exc
                raise

            if saved.user_id is None:  # invariant: save returns a persisted id
                raise RuntimeError("user_repo.save must return a persisted user_id")
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.CREATE_USER,
                    actor=f"user:{admin_id}",
                    resource=f"user:{saved.user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "target_user_id": saved.user_id,
                        "target_email": saved.email,
                    },
                )
            )

        _log.info(
            "admin_user_created",
            admin_id=admin_id,
            target_user_id=saved.user_id,
            target_email=email,
        )
        return CreateUserResult(
            user_id=saved.user_id,
            email=saved.email,
            display_name=saved.display_name,
            is_admin=saved.is_admin,
            disabled=saved.disabled,
        )


# -----------------------------------------------------------------------------
# UpdateUserUseCase
# -----------------------------------------------------------------------------


class UpdateUserUseCase:
    """Admin-driven update of display_name / is_admin / disabled."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        """Bind ports."""
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self,
        *,
        admin_id: int,
        target_user_id: int,
        display_name: str | None = None,
        is_admin: bool | None = None,
        disabled: bool | None = None,
    ) -> UpdateUserResult:
        """Apply an optional set of mutations and audit.

        Args:
            admin_id: The requesting administrator's user_id.
            target_user_id: The user being updated.
            display_name: New value, or ``None`` to keep.
            is_admin: New value, or ``None`` to keep.
            disabled: New value, or ``None`` to keep.

        Returns:
            :class:`UpdateUserResult` with the post-update user state
            and the list of fields actually mutated.

        Raises:
            UserNotFoundError: ``target_user_id`` does not exist.
            SelfProtectionError: Admin tried to remove their own
                ``is_admin`` or disable their own account.
        """
        # Self-protection check — runs BEFORE any DB mutation so we
        # can return 409 cleanly. The check applies only when the
        # admin is targeting themselves AND the requested mutation
        # would lose access.
        if admin_id == target_user_id:
            attempted: str | None = None
            if is_admin is False:
                attempted = "is_admin"
            elif disabled is True:
                attempted = "disabled"
            if attempted is not None:
                _log.warning(
                    "admin_self_protection_rejected",
                    admin_id=admin_id,
                    target_user_id=target_user_id,
                    attempted_field=attempted,
                )
                raise SelfProtectionError(
                    "administrators cannot remove their own admin privilege"
                    " or disable their own account",
                    details={
                        "admin_id": admin_id,
                        "target_user_id": target_user_id,
                        "attempted_field": attempted,
                    },
                )

        # Build the mutated_fields list from the (non-None) request
        # arguments — NOT from value-equality with the existing row.
        # An admin who PATCHes with the same value the user already
        # has has still expressed intent to mutate that field; the
        # audit log preserves that intent.
        mutated: list[str] = []
        if display_name is not None:
            mutated.append("display_name")
        if is_admin is not None:
            mutated.append("is_admin")
        if disabled is not None:
            mutated.append("disabled")
        mutated.sort()

        now = self._clock.now()
        async with self._uow_factory() as uow:
            updated = await uow.user_repo.update(
                target_user_id,
                display_name=display_name,
                is_admin=is_admin,
                disabled=disabled,
            )
            if updated is None:
                raise UserNotFoundError(
                    f"user not found: {target_user_id}",
                    details={"user_id": target_user_id},
                )

            # Empty-PATCH case: no audit record (nothing changed).
            # Non-empty: emit a single UPDATE_USER record carrying
            # mutated_fields per L3-AUTH-017.
            if mutated:
                await uow.audit_log.record(
                    AuditEvent(
                        timestamp=now,
                        action=AuditAction.UPDATE_USER,
                        actor=f"user:{admin_id}",
                        resource=f"user:{target_user_id}",
                        outcome=AuditOutcome.SUCCESS,
                        details={
                            "target_user_id": target_user_id,
                            "mutated_fields": mutated,
                        },
                    )
                )

            # Disabling an account SHALL revoke its live sessions in the same
            # transaction, so the disable takes effect immediately rather than
            # only at idle-timeout (a compromised/departed account is otherwise
            # still authenticated by its existing cookie).
            if disabled is True:
                await uow.session_repo.delete_by_user_id(target_user_id)

        _log.info(
            "admin_user_updated",
            admin_id=admin_id,
            target_user_id=target_user_id,
            mutated_fields=mutated,
        )
        return UpdateUserResult(
            user_id=updated.user_id or target_user_id,
            email=updated.email,
            display_name=updated.display_name,
            is_admin=updated.is_admin,
            disabled=updated.disabled,
            mutated_fields=tuple(mutated),
        )


# -----------------------------------------------------------------------------
# ResetPasswordUseCase
# -----------------------------------------------------------------------------


class ResetPasswordUseCase:
    """Admin-driven password reset per L2-AUTH-008 / L3-AUTH-016."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        password_hasher: PasswordHasher,
    ) -> None:
        """Bind ports."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._hasher = password_hasher

    async def execute(
        self,
        *,
        admin_id: int,
        target_user_id: int,
        new_password: Password,
    ) -> None:
        """Hash the new password, persist, audit.

        Args:
            admin_id: The requesting administrator's user_id.
            target_user_id: The user whose password is being reset.
            new_password: Plaintext-wrapper; hashed before persistence.

        Raises:
            UserNotFoundError: ``target_user_id`` does not exist.
        """
        password_hash = self._hasher.hash(new_password)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            updated = await uow.user_repo.update(
                target_user_id,
                password_hash=password_hash,
            )
            if updated is None:
                raise UserNotFoundError(
                    f"user not found: {target_user_id}",
                    details={"user_id": target_user_id},
                )

            # Audit per L3-AUTH-017: UPDATE_USER with
            # mutated_fields=['password_hash']. The hash value itself
            # is NOT in details (L3-OBS-036 redaction).
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.UPDATE_USER,
                    actor=f"user:{admin_id}",
                    resource=f"user:{target_user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "target_user_id": target_user_id,
                        "mutated_fields": ["password_hash"],
                    },
                )
            )

            # A password reset SHALL revoke the target's live sessions in the
            # same transaction: a reset is typically done *because* the old
            # credential is compromised, so any session established with it must
            # not survive the change.
            await uow.session_repo.delete_by_user_id(target_user_id)

        _log.info(
            "admin_password_reset",
            admin_id=admin_id,
            target_user_id=target_user_id,
        )


__all__ = [
    "CreateUserResult",
    "CreateUserUseCase",
    "ResetPasswordUseCase",
    "UpdateUserResult",
    "UpdateUserUseCase",
]
