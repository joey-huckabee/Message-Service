"""Unit tests for the gRPC servicer's EmailBodyPosition resolution (L3-AGGR-004).

The servicer resolves the proto ``EmailBodyPosition`` enum to the domain
enum. The proto3 default ``UNSPECIFIED`` — indistinguishable on the wire
from an unset field — resolves to ``AFTER_STAGES_SUMMARY`` and emits a
DEBUG log; explicit ``BEFORE``/``AFTER`` pass through verbatim with no log.

The DEBUG-log assertion patches the servicer's module logger rather than
using ``structlog.testing.capture_logs``: ``logging_setup`` configures
structlog with ``cache_logger_on_first_use=True``, so once any prior test
configures logging the module proxy is cached and ``capture_logs`` can no
longer intercept it. Patching the ``_log`` attribute is independent of
global structlog state and therefore deterministic under any test order.
"""

from __future__ import annotations

from typing import Any

import pytest
from message_service_proto.v1 import message_service_pb2 as pb

from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.interfaces.grpc import servicer as servicer_mod
from message_service.interfaces.grpc.servicer import _email_body_position_to_domain


class _CapturingLogger:
    """Minimal structlog stand-in that records ``debug`` calls; other levels no-op."""

    def __init__(self) -> None:
        self.debug_calls: list[dict[str, Any]] = []

    def debug(self, event: str, **kwargs: Any) -> None:
        self.debug_calls.append({"event": event, **kwargs})

    def __getattr__(self, _name: str) -> Any:
        return lambda *_a, **_k: None


@pytest.fixture
def cap_log(monkeypatch: pytest.MonkeyPatch) -> _CapturingLogger:
    logger = _CapturingLogger()
    monkeypatch.setattr(servicer_mod, "_log", logger)
    return logger


@pytest.mark.requirement("L3-AGGR-004")
def test_unspecified_position_resolves_to_after_with_debug_log(
    cap_log: _CapturingLogger,
) -> None:
    """UNSPECIFIED SHALL resolve to AFTER_STAGES_SUMMARY and emit a DEBUG log."""
    result = _email_body_position_to_domain(
        pb.EMAIL_BODY_POSITION_UNSPECIFIED, run_id="run-1", stage_id="extract"
    )

    assert result is EmailBodyPosition.AFTER_STAGES_SUMMARY
    assert cap_log.debug_calls == [
        {
            "event": "email_body_position_defaulted",
            "run_id": "run-1",
            "stage_id": "extract",
            "resolved_position": "AFTER_STAGES_SUMMARY",
        }
    ]


@pytest.mark.requirement("L3-AGGR-004")
def test_explicit_before_resolves_verbatim_without_log(cap_log: _CapturingLogger) -> None:
    """An explicit BEFORE SHALL pass through unchanged and emit no defaulting log."""
    result = _email_body_position_to_domain(
        pb.EMAIL_BODY_POSITION_BEFORE_STAGES_SUMMARY, run_id="run-1", stage_id="extract"
    )

    assert result is EmailBodyPosition.BEFORE_STAGES_SUMMARY
    assert cap_log.debug_calls == []


@pytest.mark.requirement("L3-AGGR-004")
def test_explicit_after_resolves_verbatim_without_log(cap_log: _CapturingLogger) -> None:
    """An explicit AFTER SHALL pass through unchanged and emit no defaulting log."""
    result = _email_body_position_to_domain(
        pb.EMAIL_BODY_POSITION_AFTER_STAGES_SUMMARY, run_id="run-1", stage_id="extract"
    )

    assert result is EmailBodyPosition.AFTER_STAGES_SUMMARY
    assert cap_log.debug_calls == []


@pytest.mark.requirement("L2-AGGR-003")
def test_email_body_contribution_has_position_field() -> None:
    """L2-AGGR-003: ``EmailBodyContribution`` SHALL carry a ``position`` enum field."""
    field = pb.EmailBodyContribution.DESCRIPTOR.fields_by_name.get("position")
    assert field is not None, "EmailBodyContribution SHALL have a `position` field"
    assert field.enum_type is pb.EmailBodyPosition.DESCRIPTOR
