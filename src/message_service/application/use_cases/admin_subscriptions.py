"""Use cases: admin-on-behalf-of subscription management (L3-DASH-044).

An administrator manages another recipient's notification subscriptions. Unlike
the self-service :class:`SubscribeUseCase` / :class:`UnsubscribeUseCase` — whose
audit actor is the subscriber and whose delete enforces *self*-ownership — these
record the acting administrator as the audit actor and the target recipient as
the resource, and they scope the delete to the target recipient (an admin cannot
remove a third user's subscription through a target's path).

Target validation for ``PIPELINE`` / ``TAG`` reuses the same rule as
self-service subscription creation (L3-SUB-004), so there is one correctness
rule for what a subscription may point at.

Requirement references
----------------------
L1-DASH-009 (admin manages any recipient's subscriptions)
L2-DASH-022 (on-behalf-of admin subscription API)
L3-DASH-044 (admin subscribe/unsubscribe use cases + audit)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.errors import (
    SubscriptionNotFoundError,
    UnknownPipelineTypeError,
    UnknownTagError,
    UserNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.tag_vocabulary import TagVocabulary
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.subscription import Subscription
    from message_service.domain.ids import SubscriptionId, UserId

_log = structlog.get_logger(__name__)


class AdminSubscribeUseCase:
    """Create a subscription for a target recipient, audited to the admin."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        tag_vocabulary: TagVocabulary,
        registered_pipelines: frozenset[str],
    ) -> None:
        """Bind to UoW factory + clock + validation registries."""
        self._uow_factory = uow_factory
        self._clock = clock
        self._tag_vocabulary = tag_vocabulary
        self._registered_pipelines = registered_pipelines

    async def execute(
        self,
        *,
        admin_id: int,
        target_user_id: UserId,
        granularity: SubscriptionGranularity,
        target_value: str | None,
    ) -> Subscription:
        """Create the subscription for ``target_user_id`` and audit it.

        Args:
            admin_id: The acting administrator (recorded as the audit actor).
            target_user_id: The recipient the subscription is created for.
            granularity: ``GLOBAL`` / ``PIPELINE`` / ``TAG``.
            target_value: ``None`` for ``GLOBAL``; pipeline-type / tag name
                otherwise (validated against the registry / vocabulary).

        Returns:
            The newly-created :class:`Subscription`.

        Raises:
            UnknownPipelineTypeError: PIPELINE target not registered (422).
            UnknownTagError: TAG target not in the vocabulary (422).
            UserNotFoundError: ``target_user_id`` does not exist (404).
            PersistenceError: Duplicate subscription, or other failure (409).
        """
        # L3-SUB-004: validate the target before any persistence work.
        if granularity is SubscriptionGranularity.PIPELINE:
            if target_value not in self._registered_pipelines:
                raise UnknownPipelineTypeError(
                    f"unknown pipeline_type {target_value!r}",
                    details={
                        "target_value": target_value,
                        "registered_pipelines": sorted(self._registered_pipelines),
                    },
                )
        elif (
            granularity is SubscriptionGranularity.TAG
            and target_value is not None
            and not self._tag_vocabulary.contains(target_value)
        ):
            raise UnknownTagError(
                f"unknown tag {target_value!r}",
                details={"target_value": target_value},
            )

        async with self._uow_factory() as uow:
            target = await uow.user_repo.get_by_id(int(target_user_id))
            if target is None:
                raise UserNotFoundError(
                    f"user {target_user_id} does not exist",
                    details={"target_user_id": int(target_user_id)},
                )
            saved = await uow.subscription_repo.add(
                user_id=target_user_id,
                granularity=granularity,
                target_value=target_value,
            )
            now = self._clock.now()
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.SUBSCRIBE,
                    actor=f"user:{admin_id}",
                    resource=f"user:{target_user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "target_user_id": int(target_user_id),
                        "granularity": granularity.value,
                        "target_value": target_value,
                    },
                ),
            )
        _log.info(
            "admin_subscription_created",
            admin_id=admin_id,
            target_user_id=int(target_user_id),
            subscription_id=saved.subscription_id,
            granularity=granularity.value,
        )
        return saved


class AdminUnsubscribeUseCase:
    """Delete a target recipient's subscription, audited to the admin."""

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
        admin_id: int,
        target_user_id: UserId,
        subscription_id: SubscriptionId,
    ) -> None:
        """Delete ``subscription_id`` iff it belongs to ``target_user_id``.

        Args:
            admin_id: The acting administrator (recorded as the audit actor).
            target_user_id: The recipient whose subscription is being removed.
            subscription_id: The subscription to delete.

        Raises:
            SubscriptionNotFoundError: No subscription with that id belongs to
                ``target_user_id`` (maps to HTTP 404). Note this is returned
                even when the id exists but is owned by a *different* user, so
                the admin cannot probe another recipient's rows via this path.
        """
        async with self._uow_factory() as uow:
            existing = await uow.subscription_repo.get_by_id(subscription_id)
            if existing is None or existing.user_id != target_user_id:
                raise SubscriptionNotFoundError(
                    f"subscription {subscription_id} is not owned by user {target_user_id}",
                    details={
                        "subscription_id": subscription_id,
                        "target_user_id": int(target_user_id),
                    },
                )
            now = self._clock.now()
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.UNSUBSCRIBE,
                    actor=f"user:{admin_id}",
                    resource=f"user:{target_user_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "target_user_id": int(target_user_id),
                        "subscription_id": subscription_id,
                        "granularity": existing.granularity.value,
                        "target_value": existing.target_value,
                    },
                ),
            )
            await uow.subscription_repo.remove(subscription_id)
        _log.info(
            "admin_subscription_removed",
            admin_id=admin_id,
            target_user_id=int(target_user_id),
            subscription_id=subscription_id,
        )


__all__ = ["AdminSubscribeUseCase", "AdminUnsubscribeUseCase"]
