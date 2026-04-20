"""The :class:`Subscription` aggregate: one user's interest in receiving reports.

A :class:`Subscription` binds a :class:`~message_service.domain.ids.UserId`
to a predicate that matches runs. At report-delivery time the
:class:`~message_service.application.ports.subscription_repository.SubscriptionRepository`
returns the set of distinct email addresses matching the finalized run.

Three granularities (v1):

- ``GLOBAL``     — every finalized run. ``target_value`` must be
  ``None``.
- ``PIPELINE``   — finalized runs whose ``pipeline_type`` equals
  ``target_value``.
- ``TAG``        — finalized runs whose ``tags`` contain
  ``target_value``.

Requirement references
----------------------
L2-SUB-001, L2-SUB-002, L2-SUB-003
L3-SUB-001, L3-SUB-002, L3-SUB-003, L3-SUB-004
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from message_service.domain.ids import SubscriptionId, UserId


class SubscriptionGranularity(StrEnum):
    """The scope of a subscription's run-matching predicate."""

    GLOBAL = "GLOBAL"
    PIPELINE = "PIPELINE"
    TAG = "TAG"


@dataclass(frozen=True, slots=True)
class Subscription:
    """A single user's subscription.

    Attributes:
        subscription_id: Surrogate key minted by persistence.
        user_id: Owning user.
        granularity: Which predicate applies (L2-SUB-002).
        target_value: Predicate argument. ``None`` iff
            ``granularity is GLOBAL``. Pipeline type name for
            ``PIPELINE``; tag name for ``TAG``.
        created_at: UTC timestamp captured at insert time (L3-SUB-002);
            immutable once set.
    """

    subscription_id: SubscriptionId
    user_id: UserId
    granularity: SubscriptionGranularity
    created_at: datetime
    target_value: str | None = None

    def __post_init__(self) -> None:
        """Validate granularity/target_value consistency and timestamp.

        Raises:
            ValueError: If the combination of ``granularity`` and
                ``target_value`` is inconsistent, or if ``created_at``
                is naive.
        """
        if self.created_at.tzinfo is None:
            raise ValueError("Subscription.created_at must be timezone-aware")

        if self.granularity is SubscriptionGranularity.GLOBAL:
            if self.target_value is not None:
                raise ValueError(
                    "Subscription with GLOBAL granularity must have target_value=None "
                    f"(got {self.target_value!r})"
                )
        else:  # PIPELINE or TAG
            if self.target_value is None:
                raise ValueError(
                    f"Subscription with {self.granularity} granularity requires target_value"
                )
            if not self.target_value:
                raise ValueError(
                    f"Subscription with {self.granularity} granularity requires "
                    "non-empty target_value"
                )


__all__ = ["Subscription", "SubscriptionGranularity"]
