"""Unit tests for :mod:`message_service.domain.aggregates.subscription`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from message_service.domain.aggregates.subscription import Subscription, SubscriptionGranularity
from message_service.domain.ids import SubscriptionId, UserId

_SID = SubscriptionId(42)
_UID = UserId(7)
_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _sub(**overrides: object) -> Subscription:
    fields: dict[str, object] = {
        "subscription_id": _SID,
        "user_id": _UID,
        "granularity": SubscriptionGranularity.GLOBAL,
        "target_value": None,
        "created_at": _T0,
    }
    fields.update(overrides)
    return Subscription(**fields)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-SUB-002")
def test_global_subscription_constructs_with_null_target() -> None:
    s = _sub(granularity=SubscriptionGranularity.GLOBAL, target_value=None)
    assert s.granularity == SubscriptionGranularity.GLOBAL
    assert s.target_value is None


@pytest.mark.requirement("L2-SUB-002")
def test_pipeline_subscription_requires_target() -> None:
    s = _sub(granularity=SubscriptionGranularity.PIPELINE, target_value="etl-nightly")
    assert s.target_value == "etl-nightly"


@pytest.mark.requirement("L2-SUB-002")
def test_tag_subscription_requires_target() -> None:
    s = _sub(granularity=SubscriptionGranularity.TAG, target_value="production")
    assert s.target_value == "production"


@pytest.mark.requirement("L2-SUB-002")
def test_subscription_is_frozen() -> None:
    s = _sub()
    with pytest.raises((AttributeError, TypeError)):
        s.target_value = "changed"  # type: ignore[misc]


# -----------------------------------------------------------------------------
# Invariants (L3-SUB-003)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SUB-003")
def test_global_rejects_non_null_target() -> None:
    with pytest.raises(ValueError, match="GLOBAL"):
        _sub(granularity=SubscriptionGranularity.GLOBAL, target_value="should-be-null")


@pytest.mark.requirement("L2-SUB-002")
@pytest.mark.parametrize(
    "granularity",
    [SubscriptionGranularity.PIPELINE, SubscriptionGranularity.TAG],
)
def test_non_global_rejects_null_target(granularity: SubscriptionGranularity) -> None:
    with pytest.raises(ValueError, match=str(granularity)):
        _sub(granularity=granularity, target_value=None)


@pytest.mark.requirement("L2-SUB-002")
@pytest.mark.parametrize(
    "granularity",
    [SubscriptionGranularity.PIPELINE, SubscriptionGranularity.TAG],
)
def test_non_global_rejects_empty_target(granularity: SubscriptionGranularity) -> None:
    with pytest.raises(ValueError):
        _sub(granularity=granularity, target_value="")


@pytest.mark.requirement("L3-SUB-002")
def test_rejects_naive_created_at() -> None:
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="created_at"):
        _sub(created_at=naive)
