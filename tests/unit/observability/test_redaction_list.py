"""Inspection tests for the structlog redaction list.

Covers L3-AUTH-005 (the required sensitive-key set is registered) and
L3-OBS-006 (single-source-of-truth pattern).
"""

from __future__ import annotations

import pytest

from message_service.observability.logging_setup import (
    REDACTED_PLACEHOLDER,
    SENSITIVE_FIELD_NAMES,
    redact_sensitive_keys,
)


@pytest.mark.requirement("L3-AUTH-005")
@pytest.mark.requirement("L3-OBS-005")
@pytest.mark.requirement("L3-OBS-018")
def test_redaction_list_includes_required_keys() -> None:
    """L3-AUTH-005 + L3-OBS-005: the redaction list SHALL include the full
    sensitive-field set: ``password``, ``passwd``, ``password_hash``,
    ``pwd``, ``secret``, ``smtp_password``, ``session_token``,
    ``cookie``, ``authorization``, ``email_body``, ``rendered_output``,
    ``template_context``.
    """
    required = frozenset(
        {
            "password",
            "passwd",
            "password_hash",
            "pwd",
            "secret",
            "smtp_password",
            "session_token",
            "cookie",
            "authorization",
            "email_body",
            "rendered_output",
            "template_context",
        }
    )
    missing = required - SENSITIVE_FIELD_NAMES
    assert missing == set(), f"redaction list missing required keys: {missing}"


@pytest.mark.requirement("L3-AUTH-005")
def test_redact_replaces_password_value_with_placeholder() -> None:
    """L3-AUTH-005: a payload with ``password`` key SHALL emit
    ``<redacted>`` in the redacted copy, not the original value.
    """
    redacted = redact_sensitive_keys({"password": "hunter2", "user": "alice"})
    assert redacted["password"] == REDACTED_PLACEHOLDER
    assert redacted["user"] == "alice"


@pytest.mark.requirement("L3-AUTH-005")
def test_redact_does_not_mutate_input() -> None:
    """L3-AUTH-005 / L3-ERR-016: the original payload SHALL be left intact;
    redaction operates on a copy.
    """
    original: dict[str, object] = {"password": "hunter2"}
    _ = redact_sensitive_keys(original)
    assert original["password"] == "hunter2"


@pytest.mark.requirement("L3-AUTH-005")
def test_redact_handles_each_required_key() -> None:
    """L3-AUTH-005: each of the four named keys SHALL produce a redacted
    copy regardless of value type.
    """
    payload: dict[str, object] = {
        "password": "p",
        "passwd": "q",
        "password_hash": "$argon2id$...",
        "pwd": "r",
        "user": "alice",
    }
    redacted = redact_sensitive_keys(payload)
    for key in ("password", "passwd", "password_hash", "pwd"):
        assert redacted[key] == REDACTED_PLACEHOLDER, (
            f"key {key!r} was not redacted: {redacted[key]!r}"
        )
    assert redacted["user"] == "alice"


@pytest.mark.requirement("L3-OBS-044")
def test_instance_value_is_redacted() -> None:
    """``instance_value`` (raw offending value in a schema violation) SHALL redact.

    It carries the same class of arbitrary pipeline data as ``template_context``.
    """
    redacted = redact_sensitive_keys({"instance_value": {"ssn": "123"}, "validator": "required"})
    assert redacted["instance_value"] == REDACTED_PLACEHOLDER
    assert redacted["validator"] == "required"


@pytest.mark.requirement("L3-OBS-044")
def test_redaction_recurses_into_nested_dicts_and_lists() -> None:
    """A sensitive key nested inside a value SHALL be redacted, not just top-level.

    Regression: the pass was shallow, so a ``password`` (or ``instance_value``)
    inside a ``details`` payload leaked to logs / the error envelope.
    """
    payload: dict[str, object] = {
        "details": {
            "password": "hunter2",
            "instance_value": "raw-client-data",
            "run_id": "r-1",
        },
        "items": [{"secret": "s"}, {"ok": 1}],
        "user": "alice",
    }
    redacted = redact_sensitive_keys(payload)

    nested = redacted["details"]
    assert isinstance(nested, dict)
    assert nested["password"] == REDACTED_PLACEHOLDER
    assert nested["instance_value"] == REDACTED_PLACEHOLDER
    assert nested["run_id"] == "r-1"  # non-sensitive nested key survives
    items = redacted["items"]
    assert isinstance(items, list)
    assert items[0]["secret"] == REDACTED_PLACEHOLDER
    assert items[1]["ok"] == 1
    assert redacted["user"] == "alice"
    # Original is untouched (deep).
    assert payload["details"] == {
        "password": "hunter2",
        "instance_value": "raw-client-data",
        "run_id": "r-1",
    }
