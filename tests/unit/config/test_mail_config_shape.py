"""Schema-shape inspection tests for the mail configuration.

Covers L3-MAIL-002 (host/port/username/password types), L3-MAIL-011
(retry defaults: max_retries=5, initial_interval=2, max_interval=300),
L3-MAIL-020 (timeout: aiosmtplib library default — no v1 config knob),
and L3-MAIL-022 (empty username skips login; missing password with
non-empty username surfaces at SMTP layer, not at config validation).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from message_service.config.schema import MailRetryConfig, SmtpConfig

# -----------------------------------------------------------------------------
# L3-MAIL-002: SmtpConfig field types
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-002")
def test_smtp_config_minimum_construction() -> None:
    """L3-MAIL-002: host (str, non-empty), port (int [1, 65535])."""
    cfg = SmtpConfig(host="smtp.example.com", port=587)
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 587


@pytest.mark.requirement("L3-MAIL-002")
def test_smtp_config_username_password_default_empty() -> None:
    """L3-MAIL-002: username/password are optional (default empty string)."""
    cfg = SmtpConfig(host="x", port=587)
    assert cfg.username == ""
    assert cfg.password == ""


@pytest.mark.requirement("L3-MAIL-002")
@pytest.mark.parametrize("bad_port", [0, -1, 65_536, 100_000])
def test_smtp_config_rejects_out_of_range_port(bad_port: int) -> None:
    """L3-MAIL-002: port outside [1, 65535] SHALL be rejected."""
    with pytest.raises(ValidationError):
        SmtpConfig(host="x", port=bad_port)


@pytest.mark.requirement("L3-MAIL-002")
def test_smtp_config_rejects_empty_host() -> None:
    """L3-MAIL-002: host SHALL be a non-empty string."""
    with pytest.raises(ValidationError):
        SmtpConfig(host="", port=587)


# -----------------------------------------------------------------------------
# L3-MAIL-011: retry defaults
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-011")
def test_mail_retry_defaults_match_spec() -> None:
    """L3-MAIL-011: defaults SHALL be ``max_retries=5``,
    ``initial_interval_seconds=2``, ``max_interval_seconds=300``.
    """
    cfg = MailRetryConfig()
    assert cfg.max_retries == 5
    assert cfg.initial_interval_seconds == 2
    assert cfg.max_interval_seconds == 300


# -----------------------------------------------------------------------------
# L3-MAIL-020: no smtp.timeout_seconds field (v1 uses library default)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-020")
def test_smtp_config_has_no_timeout_seconds_field() -> None:
    """L3-MAIL-020: v1 does NOT expose a ``mail.smtp.timeout_seconds``
    config knob; the underlying ``aiosmtplib.SMTP`` client uses its
    library default. Pinning the absence of the field here so that
    accidental re-introduction would surface in code review.
    """
    fields = SmtpConfig.model_fields
    assert "timeout_seconds" not in fields, (
        "v1 SHALL NOT have a `mail.smtp.timeout_seconds` field (see L3-MAIL-020 for rationale)"
    )


# -----------------------------------------------------------------------------
# L3-MAIL-022: username/password validation at config level
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-022")
def test_smtp_config_accepts_empty_username_and_password() -> None:
    """L3-MAIL-022: empty username/password is permitted at config load
    (v1 does not gate "auth required" at the config layer; SMTP server
    surfaces auth failures at first send instead).
    """
    cfg = SmtpConfig(host="x", port=587, username="", password="")
    assert cfg.username == ""
    assert cfg.password == ""


@pytest.mark.requirement("L3-MAIL-022")
def test_smtp_config_accepts_username_without_password() -> None:
    """L3-MAIL-022: a non-empty username paired with an empty password
    is NOT detected at config load — the SMTP server surfaces the
    auth failure on first send (classified permanent per L3-MAIL-007).
    """
    # SHALL NOT raise ValidationError.
    cfg = SmtpConfig(host="x", port=587, username="user", password="")
    assert cfg.username == "user"
    assert cfg.password == ""
