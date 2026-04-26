"""Use case: ``Unsubscribe`` -- delete a subscription on behalf of its owner.

Per L2-DASH-004 / L3-DASH-007, dashboard CRUD routes scope all
operations to the authenticated session user. This use case
implements that contract by fetching the subscription, comparing its
``user_id`` against the session-user, and surfacing distinct
exceptions for the two failure modes:

* :class:`SubscriptionNotFoundError` -- no row with that id (HTTP 404).
* :class:`SubscriptionForbiddenError` -- row exists but is owned by
  another user (HTTP 403, per L3-DASH-007).

On success, the audit insert and the row delete commit together in
one UoW; the ``UNSUBSCRIBE`` audit event matches the L3-OBS-032
format.

Requirement references
----------------------
L2-DASH-004 (per-user route scoping)
L3-DASH-007 (cross-user attempts return HTTP 403)
L3-OBS-032 (UNSUBSCRIBE audit format)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import (
    SubscriptionForbiddenError,
    SubscriptionNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.ids import SubscriptionId, UserId

_log = structlog.get_logger(__name__)


class UnsubscribeUseCase:
    """Delete an owned subscription, audit, return."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
    ) -> None:
        """Bind to UoW factory + clock."""
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self,
        *,
        subscription_id: SubscriptionId,
        user_id: UserId,
    ) -> None:
        """Delete the subscription if it belongs to ``user_id``.

        Args:
            subscription_id: Path-parameter id from the route.
            user_id: Authenticated user from the session context.

        Raises:
            SubscriptionNotFoundError: No row with ``subscription_id``
                exists -- maps to HTTP 404.
            SubscriptionForbiddenError: Row exists but belongs to a
                different user -- maps to HTTP 403 per L3-DASH-007.
        """
        async with self._uow_factory() as uow:
            existing = await uow.subscription_repo.get_by_id(subscription_id)
            if existing is None:
                raise SubscriptionNotFoundError(
                    f"subscription {subscription_id} does not exist",
                    details={"subscription_id": subscription_id},
                )
            if existing.user_id != user_id:
                raise SubscriptionForbiddenError(
                    f"subscription {subscription_id} belongs to another user",
                    details={"subscription_id": subscription_id},
                )

            now = self._clock.now()
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.UNSUBSCRIBE,
                    actor=f"user:{user_id}",
                    resource=f"subscription:{subscription_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "granularity": existing.granularity.value,
                        "target_value": existing.target_value,
                    },
                ),
            )
            await uow.subscription_repo.remove(subscription_id)
        _log.info(
            "subscription_removed",
            user_id=user_id,
            subscription_id=subscription_id,
        )


__all__ = ["UnsubscribeUseCase"]
