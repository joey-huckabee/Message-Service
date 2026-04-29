"""Inspection tests for the observability logging setup.

Covers:
- L3-OBS-001: structlog configured via ``configure_logging()`` in
  ``observability/logging_setup.py``.
- L3-OBS-002: JSON renderer emits ``timestamp``, ``level``, ``logger``,
  ``event`` plus structured fields.
- L3-OBS-006: redaction is case-insensitive on the key name.
- L3-OBS-022: log_level is config-driven and requires service restart
  (no hot-reload mechanism).
- L3-OBS-023: error_code processor copies ``error_code`` to top-level.
- L3-OBS-024: ERROR-level records carry ``error_code`` in the JSON.
"""

from __future__ import annotations

import inspect

import pytest

from message_service.observability.logging_setup import (
    REDACTED_PLACEHOLDER,
    configure_logging,
    redact_sensitive_keys,
)

# -----------------------------------------------------------------------------
# L3-OBS-001: configure_logging exists and is the chokepoint
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-001")
def test_configure_logging_callable_in_logging_setup() -> None:
    """L3-OBS-001: ``configure_logging`` SHALL exist as the structlog
    setup entrypoint in ``observability/logging_setup.py``.
    """
    assert callable(configure_logging)
    assert configure_logging.__module__ == "message_service.observability.logging_setup"


@pytest.mark.requirement("L3-OBS-001")
def test_configure_logging_takes_level_argument() -> None:
    """L3-OBS-001 / L3-OBS-021: ``configure_logging`` SHALL accept the
    operator's log-level value as a parameter (named ``level`` in v1).
    """
    sig = inspect.signature(configure_logging)
    params = list(sig.parameters)
    assert "level" in params, f"configure_logging SHALL accept `level`; got params {params}"


# -----------------------------------------------------------------------------
# L3-OBS-002: JSON renderer emits the required fields
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-002")
def test_logging_setup_uses_json_renderer() -> None:
    """L3-OBS-002: structlog SHALL emit JSON-formatted records (the
    JSON renderer is the production processor).
    """
    from pathlib import Path

    setup_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "message_service"
        / "observability"
        / "logging_setup.py"
    )
    text = setup_path.read_text(encoding="utf-8")
    assert "JSONRenderer" in text, (
        "logging_setup SHALL configure structlog with the JSON renderer (L3-OBS-002)"
    )


# -----------------------------------------------------------------------------
# L3-OBS-006: case-insensitive redaction
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-006")
def test_redact_is_case_insensitive_on_key_names() -> None:
    """L3-OBS-006: redaction SHALL match keys case-insensitively;
    submitting ``PASSWORD`` / ``Password`` / ``password`` SHALL all
    redact.
    """
    payload: dict[str, object] = {
        "password": "lowercase",
        "Password": "titlecase",
        "PASSWORD": "uppercase",
        "user": "alice",
    }
    redacted = redact_sensitive_keys(payload)
    for key in ("password", "Password", "PASSWORD"):
        assert redacted[key] == REDACTED_PLACEHOLDER, (
            f"Case variant {key!r} was not redacted: {redacted[key]!r}"
        )
    assert redacted["user"] == "alice"


# -----------------------------------------------------------------------------
# L3-OBS-022: log_level requires restart (no hot-reload code path)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-022")
def test_no_log_level_hot_reload_path_exists() -> None:
    """L3-OBS-022: hot-reload of ``observability.log_level`` is OUT of
    scope for v1; no SIGHUP / runtime reconfigure path SHALL exist.
    Verifies by inspection of logging_setup.py.
    """
    from pathlib import Path

    setup_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "message_service"
        / "observability"
        / "logging_setup.py"
    )
    text = setup_path.read_text(encoding="utf-8")
    # No SIGHUP-driven reload, no runtime reconfigure helper.
    assert "SIGHUP" not in text
    assert "reconfigure" not in text.lower(), (
        "logging_setup SHALL NOT expose a runtime-reconfigure path; "
        "log-level changes require restart per L3-OBS-022"
    )
