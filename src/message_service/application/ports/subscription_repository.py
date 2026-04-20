"""Port: subscription persistence and recipient resolution.

This port serves two clients:

* **Dashboard use cases** use :meth:`list_for_user`, :meth:`add`, and
  :meth:`remove` for subscription management.
* **Delivery use cases** call :meth:`list_recipients_for_run` once per
  finalized run to compute the distinct email addresses to mail.

The two shapes are deliberately distinct: the dashboard wants rich
:class:`Subscription` aggregates for UI rendering; the delivery path
wants just ``frozenset[str]`` of email addresses because that is the
only thing the :class:`~message_service.application.ports.mailer.Mailer`
consumes.

Requirement references
----------------------
L2-SUB-001, L2-SUB-002, L2-SUB-003
L3-SUB-001, L3-SUB-005, L3-SUB-006
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from message_service.domain.aggregates.subscription import Subscription, SubscriptionGranularity
from message_service.domain.ids import SubscriptionId, UserId


class SubscriptionRepository(ABC):
    """Abstract repository for :class:`Subscription` rows plus recipient resolution.

    Implementations MUST:

    * Enforce the unique index on ``(user_id, granularity,
      target_value)`` (L3-SUB-001). Attempts to insert a duplicate
      raise
      :class:`~message_service.domain.errors.PersistenceError` (or a
      subclass) carrying the conflicting values in ``details``.
    * For :meth:`list_recipients_for_run`: skip disabled users (the
      adapter joins on ``users.disabled = 0`` per L3-SUB-005) and
      return a ``frozenset[str]`` of distinct email addresses.
    """

    @abstractmethod
    async def list_recipients_for_run(
        self,
        pipeline_type: str,
        tags: frozenset[str],
    ) -> frozenset[str]:
        """Resolve the distinct email recipients for a finalized run.

        Matches any subscription whose predicate is true for this run:

        * GLOBAL subscriptions match every run.
        * PIPELINE subscriptions match iff ``target_value ==
          pipeline_type``.
        * TAG subscriptions match iff ``target_value in tags``.

        Disabled users are excluded (L3-SUB-005). Multiple matching
        subscriptions for the same user yield one email address — the
        result is a set.

        Args:
            pipeline_type: The run's pipeline type.
            tags: The run's tag set. Empty frozenset matches no TAG
                subscriptions; GLOBAL/PIPELINE still apply.

        Returns:
            Frozen set of email addresses. Empty if no subscribers
            match.

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_for_user(self, user_id: UserId) -> Sequence[Subscription]:
        """Return every subscription owned by ``user_id``.

        Args:
            user_id: The owner whose subscriptions to list.

        Returns:
            Sequence of :class:`Subscription` aggregates. Empty if the
            user has no subscriptions.

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def add(
        self,
        user_id: UserId,
        granularity: SubscriptionGranularity,
        target_value: str | None,
    ) -> Subscription:
        """Create a new subscription.

        Args:
            user_id: The owner.
            granularity: Scope of the subscription.
            target_value: Predicate argument. MUST be ``None`` for
                ``GLOBAL`` and MUST be non-empty for ``PIPELINE`` or
                ``TAG`` (invariant enforced in :class:`Subscription`).
                Implementations validate the value against the
                pipeline registry or tag vocabulary as appropriate
                (L3-SUB-004); unknown targets raise
                :class:`~message_service.domain.errors.UnknownPipelineTypeError`
                or
                :class:`~message_service.domain.errors.UnknownTagError`.

        Returns:
            The newly-created :class:`Subscription`, with its minted
            ``subscription_id`` populated.

        Raises:
            UnknownPipelineTypeError: PIPELINE target_value is not in
                the pipeline registry.
            UnknownTagError: TAG target_value is not in the tag
                vocabulary.
            PersistenceError: Duplicate subscription (same user_id,
                granularity, target_value) or other infrastructure
                failure.
        """

    @abstractmethod
    async def remove(self, subscription_id: SubscriptionId) -> None:
        """Delete a subscription by id.

        Deleting a non-existent id is NOT an error (idempotent), but
        successful removal of an existing subscription emits an audit
        event from the calling use case.

        Args:
            subscription_id: Identifier to remove.

        Raises:
            PersistenceError: Infrastructure failure.
        """


__all__ = ["SubscriptionRepository"]
