"""Use case: ``Subscribe`` -- create a subscription on behalf of a user.

The route layer takes the authenticated ``user_id`` from the session
context and the ``granularity`` + ``target_value`` from the validated
request body. The use case validates ``target_value`` against the
configured tag vocabulary (TAG) or registered pipelines (PIPELINE)
per L3-SUB-004 before calling the repo, then adds the audit row in
the same UoW so the SUBSCRIBE event commits atomically with the
persisted row.

Order of operations
-------------------
The repo's ``add()`` mints the ``subscription_id`` (SQLite
``AUTOINCREMENT``); we cannot reference the id in the audit ``resource``
until after the insert. So this use case calls ``add()`` first, then
records the audit. Both operations live in one UoW: if the audit
write fails the transaction rolls back and the row is not visible.
This mirrors :class:`LoginUseCase`, which mints the session token
before auditing ``LOGIN``.

Requirement references
----------------------
L1-SUB-001, L1-SUB-002 (subscription model + per-user CRUD)
L2-SUB-001, L2-SUB-002 (persistence shape + target validation)
L3-SUB-002, L3-SUB-004 (timestamp + target validation)
L3-OBS-031 (SUBSCRIBE audit format)
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
from message_service.domain.errors import UnknownPipelineTypeError, UnknownTagError

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.tag_vocabulary import TagVocabulary
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.subscription import Subscription
    from message_service.domain.ids import UserId

_log = structlog.get_logger(__name__)


class SubscribeUseCase:
    """Create a subscription and audit it in one UoW."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        tag_vocabulary: TagVocabulary,
        registered_pipelines: frozenset[str],
    ) -> None:
        """Bind to UoW factory + clock + validation registries.

        Args:
            uow_factory: UoW factory.
            clock: Source of audit timestamps.
            tag_vocabulary: For TAG-granularity target_value validation
                (L3-SUB-004).
            registered_pipelines: Set of registered pipeline-type
                names for PIPELINE-granularity validation (L3-SUB-004).
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._tag_vocabulary = tag_vocabulary
        self._registered_pipelines = registered_pipelines

    async def execute(
        self,
        *,
        user_id: UserId,
        granularity: SubscriptionGranularity,
        target_value: str | None,
    ) -> Subscription:
        """Create the subscription, audit, return the saved aggregate.

        Args:
            user_id: Authenticated user the route layer pulled from the
                session context. The use case trusts this value.
            granularity: ``GLOBAL`` / ``PIPELINE`` / ``TAG``.
            target_value: ``None`` for ``GLOBAL``; pipeline-type name
                for ``PIPELINE``; tag name for ``TAG``. The repo
                validates against the registry / vocabulary.

        Returns:
            The newly-created :class:`Subscription` with its minted
            ``subscription_id`` populated.

        Raises:
            UnknownPipelineTypeError: ``target_value`` is not a
                registered pipeline-type (PIPELINE granularity).
            UnknownTagError: ``target_value`` is not in the configured
                tag vocabulary (TAG granularity).
            PersistenceError: Duplicate subscription, or other
                infrastructure failure.
        """
        # L3-SUB-004: validate target_value against the configured
        # vocabulary / registry before any persistence work. Raise the
        # specific domain exception so the route layer can surface the
        # right HTTP status (422 for both via the route's translator).
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
            saved = await uow.subscription_repo.add(
                user_id=user_id,
                granularity=granularity,
                target_value=target_value,
            )
            now = self._clock.now()
            await uow.audit_log.record(
                AuditEvent(
                    timestamp=now,
                    action=AuditAction.SUBSCRIBE,
                    actor=f"user:{user_id}",
                    resource=f"subscription:{saved.subscription_id}",
                    outcome=AuditOutcome.SUCCESS,
                    details={
                        "granularity": granularity.value,
                        "target_value": target_value,
                    },
                ),
            )
        _log.info(
            "subscription_created",
            user_id=user_id,
            subscription_id=saved.subscription_id,
            granularity=granularity.value,
        )
        return saved


__all__ = ["SubscribeUseCase"]
