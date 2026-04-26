"""Unit tests for the subscription routes' pure / non-I/O surface.

Covers the request/response Pydantic models. Behavioural tests
(routes, use cases, audit) live in
``tests/integration/rest/test_subscriptions.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.interfaces.rest.routes.subscriptions import (
    CreateSubscriptionRequest,
    SubscriptionResponse,
)

# -----------------------------------------------------------------------------
# CreateSubscriptionRequest
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DASH-009")
def test_create_request_accepts_global_with_null_target() -> None:
    """GLOBAL with ``target_value=null`` SHALL validate."""
    body = CreateSubscriptionRequest.model_validate(
        {"granularity": "GLOBAL", "target_value": None},
    )
    assert body.granularity is SubscriptionGranularity.GLOBAL
    assert body.target_value is None


@pytest.mark.requirement("L3-DASH-009")
def test_create_request_accepts_pipeline_with_target() -> None:
    """PIPELINE with a target SHALL validate."""
    body = CreateSubscriptionRequest.model_validate(
        {"granularity": "PIPELINE", "target_value": "etl-nightly"},
    )
    assert body.granularity is SubscriptionGranularity.PIPELINE
    assert body.target_value == "etl-nightly"


@pytest.mark.requirement("L3-DASH-009")
@pytest.mark.requirement("L2-DASH-005")
def test_create_request_rejects_user_id_in_body() -> None:
    """L2-DASH-005: extra ``user_id`` SHALL be rejected to prevent tampering."""
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(
            {
                "granularity": "GLOBAL",
                "target_value": None,
                "user_id": 999,  # parameter-tampering attempt
            },
        )


@pytest.mark.requirement("L3-DASH-009")
def test_create_request_rejects_unknown_granularity() -> None:
    """Granularity outside the enum SHALL fail validation."""
    with pytest.raises(ValidationError):
        CreateSubscriptionRequest.model_validate(
            {"granularity": "GLOBAL_OR_SOMETHING", "target_value": None},
        )


# -----------------------------------------------------------------------------
# SubscriptionResponse
# -----------------------------------------------------------------------------


def test_response_round_trips_minimal_global() -> None:
    """Response model accepts the minimal GLOBAL-shape projection."""
    resp = SubscriptionResponse(
        subscription_id=42,
        granularity=SubscriptionGranularity.GLOBAL,
        target_value=None,
        created_at="2026-04-25T12:00:00Z",
    )
    assert resp.model_dump()["subscription_id"] == 42
    assert resp.model_dump()["target_value"] is None
