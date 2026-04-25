"""Use case: ``Logout`` — invalidate a session.

Trivial: hash the inbound cookie, delete the row, audit ``LOGOUT``.
The delete is no-op-safe — concurrent logout from another tab races
fine.

Requirement references
----------------------
L1-AUTH-002 (server-side sessions; immediate revocation per L2-AUTH-004)
L2-AUTH-003 (no plaintext token in audit/log/db)
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.unit_of_work import UnitOfWork

_log = structlog.get_logger(__name__)


class LogoutUseCase:
    """Delete a session and audit it."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        """Bind to UoW factory + clock."""
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, *, plaintext_token: str, user_id: int) -> None:
        """Invalidate the session for ``plaintext_token``.

        Args:
            plaintext_token: The cookie value the FastAPI route layer
                passed through. Hashed locally; never stored.
            user_id: Authenticated user id from the request context;
                used for the audit ``actor``. The route layer SHALL
                already have authenticated; this use case trusts the
                value.
        """
        token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
        async with self._uow_factory() as uow:
            now = self._clock.now()
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.LOGOUT,
                    actor=f"user:{user_id}",
                    resource=f"user:{user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={"user_id": user_id},
                )
            )
            await uow.session_repo.delete_by_token_hash(token_hash)
        _log.info("logout_success", user_id=user_id)


__all__ = ["LogoutUseCase"]
