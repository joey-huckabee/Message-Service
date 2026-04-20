"""Unit tests for :mod:`message_service.domain.aggregates.audit_event`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from message_service.domain.aggregates.audit_event import AuditAction, AuditEvent, AuditOutcome

_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> AuditEvent:
    fields: dict[str, object] = {
        "timestamp": _T0,
        "action": AuditAction.BEGIN_RUN,
        "actor": "pipeline:etl-nightly",
        "resource": "run:00000000-0000-4000-8000-000000000001",
        "outcome": AuditOutcome.SUCCESS,
    }
    fields.update(overrides)
    return AuditEvent(**fields)  # type: ignore[arg-type]


@pytest.mark.requirement("L1-OBS-003")
def test_audit_event_constructs_with_defaults() -> None:
    e = _event()
    assert e.details == {}
    assert e.outcome == AuditOutcome.SUCCESS


@pytest.mark.requirement("L1-OBS-003")
def test_audit_event_is_frozen() -> None:
    e = _event()
    with pytest.raises((AttributeError, TypeError)):
        e.outcome = AuditOutcome.FAILURE  # type: ignore[misc]


@pytest.mark.requirement("L1-OBS-003")
def test_audit_event_details_supports_nested_structure() -> None:
    e = _event(details={"pipeline_type": "etl", "declared_stages": ["extract", "load"]})
    assert e.details["pipeline_type"] == "etl"


@pytest.mark.requirement("L3-RUN-025")
def test_audit_event_rejects_naive_timestamp() -> None:
    naive = datetime(2026, 4, 19, 12, 0, 0)
    with pytest.raises(ValueError, match="timestamp"):
        _event(timestamp=naive)


@pytest.mark.requirement("L1-OBS-003")
@pytest.mark.parametrize("field_name", ["actor", "resource"])
def test_audit_event_rejects_empty_string_fields(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        _event(**{field_name: ""})


@pytest.mark.requirement("L1-OBS-003")
@pytest.mark.parametrize("action", list(AuditAction))
def test_all_audit_actions_construct(action: AuditAction) -> None:
    e = _event(action=action)
    assert action == e.action
