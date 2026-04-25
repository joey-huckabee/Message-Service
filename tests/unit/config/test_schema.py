"""Unit tests for :mod:`message_service.config.schema`.

Tests cover model construction with valid inputs, rejection of invalid
inputs, frozen/extra-forbid enforcement, and the SubstitutableStr marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from message_service.config.schema import (
    SUBSTITUTABLE_MARKER,
    Argon2Config,
    AuditConfig,
    AuthConfig,
    Config,
    DashboardConfig,
    FilesystemPersistenceConfig,
    GrpcConfig,
    MailConfig,
    MailRetryConfig,
    ObservabilityConfig,
    PersistenceConfig,
    PipelinesConfig,
    ServiceConfig,
    SmtpConfig,
    SweeperConfig,
    TagsConfig,
    TemplatesConfig,
)

# -----------------------------------------------------------------------------
# Helper: minimally valid raw dict for Config (L3-CFG-005 composition mirrors this)
# -----------------------------------------------------------------------------


def _minimal_valid_data(tmp_path: Path) -> dict[str, object]:
    """Return the smallest dict that validates as a Config.

    Uses tmp_path-rooted paths so path fields pass the Path-ify step.
    """
    return {
        "grpc": {"host": "0.0.0.0", "port": 50051},
        "dashboard": {"host": "0.0.0.0", "port": 8080},
        "persistence": {
            "sqlite_path": str(tmp_path / "service.sqlite"),
            "filesystem": {"report_directory": str(tmp_path / "reports")},
        },
        "templates": {
            "manifest_path": str(tmp_path / "manifest.toml"),
            "email_body_template_ref": {"name": "default_body", "version": "1.0"},
        },
        "tags": {"vocabulary_path": str(tmp_path / "tags.toml")},
        "mail": {
            "from_address": "svc@example.com",
            "smtp": {"host": "smtp.example.com", "port": 587},
        },
    }


# -----------------------------------------------------------------------------
# Top-level composition (L3-CFG-005)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-005")
def test_config_model_has_all_declared_sections(tmp_path: Path) -> None:
    """Every section from L2/L3 config reqs SHALL be a field on Config."""
    cfg = Config.model_validate(_minimal_valid_data(tmp_path))
    assert isinstance(cfg.grpc, GrpcConfig)
    assert isinstance(cfg.dashboard, DashboardConfig)
    assert isinstance(cfg.persistence, PersistenceConfig)
    assert isinstance(cfg.persistence.filesystem, FilesystemPersistenceConfig)
    assert isinstance(cfg.templates, TemplatesConfig)
    assert isinstance(cfg.tags, TagsConfig)
    assert isinstance(cfg.sweeper, SweeperConfig)
    assert isinstance(cfg.mail, MailConfig)
    assert isinstance(cfg.mail.smtp, SmtpConfig)
    assert isinstance(cfg.mail.retry, MailRetryConfig)
    assert isinstance(cfg.auth, AuthConfig)
    assert isinstance(cfg.auth.argon2, Argon2Config)
    assert isinstance(cfg.observability, ObservabilityConfig)
    assert isinstance(cfg.observability.audit, AuditConfig)
    assert isinstance(cfg.service, ServiceConfig)
    assert isinstance(cfg.pipelines, PipelinesConfig)


# -----------------------------------------------------------------------------
# Frozen models (L3-CFG-016)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-016")
def test_config_is_frozen(tmp_path: Path) -> None:
    """Attempts to mutate a loaded Config SHALL raise ValidationError."""
    cfg = Config.model_validate(_minimal_valid_data(tmp_path))
    with pytest.raises(ValidationError):
        cfg.grpc.port = 9999  # type: ignore[misc]


@pytest.mark.requirement("L3-CFG-016")
def test_nested_config_is_frozen(tmp_path: Path) -> None:
    """Frozen-ness extends to nested models."""
    cfg = Config.model_validate(_minimal_valid_data(tmp_path))
    with pytest.raises(ValidationError):
        cfg.mail.smtp.use_starttls = False  # type: ignore[misc]


# -----------------------------------------------------------------------------
# extra='forbid' (L3-CFG-006)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-006")
def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """A typo at the top level SHALL be caught as a validation error."""
    data = _minimal_valid_data(tmp_path)
    data["unknwon_section"] = {}
    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate(data)
    assert any("unknwon_section" in str(e) for e in exc_info.value.errors())


@pytest.mark.requirement("L3-CFG-006")
def test_unknown_nested_key_is_rejected(tmp_path: Path) -> None:
    """Typos inside nested sections SHALL be caught too."""
    data = _minimal_valid_data(tmp_path)
    data["grpc"]["prt"] = 50052  # type: ignore[index]
    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate(data)
    assert any("prt" in str(e) for e in exc_info.value.errors())


# -----------------------------------------------------------------------------
# SubstitutableStr marker (L3-CFG-014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-014")
def test_substitutable_marker_is_on_smtp_credentials() -> None:
    """SMTP username and password SHALL carry the substitutable marker."""
    username_meta = SmtpConfig.model_fields["username"].metadata
    password_meta = SmtpConfig.model_fields["password"].metadata
    assert SUBSTITUTABLE_MARKER in username_meta
    assert SUBSTITUTABLE_MARKER in password_meta


@pytest.mark.requirement("L3-CFG-014")
def test_non_substitutable_fields_do_not_carry_marker() -> None:
    """Other string fields SHALL NOT carry the marker by default."""
    host_meta = SmtpConfig.model_fields["host"].metadata
    assert SUBSTITUTABLE_MARKER not in host_meta


# -----------------------------------------------------------------------------
# Port range validation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-CFG-004")
@pytest.mark.parametrize("port", [0, -1, 65_536, 99_999])
def test_grpc_port_out_of_range_rejected(tmp_path: Path, port: int) -> None:
    data = _minimal_valid_data(tmp_path)
    data["grpc"]["port"] = port  # type: ignore[index]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.requirement("L2-CFG-004")
@pytest.mark.parametrize("port", [1, 587, 8080, 65_535])
def test_grpc_port_within_range_accepted(tmp_path: Path, port: int) -> None:
    data = _minimal_valid_data(tmp_path)
    data["grpc"]["port"] = port  # type: ignore[index]
    # The L3-DASH-004 cross-validator rejects shared listener ports;
    # set the dashboard to a different value so this test stays focused
    # on the gRPC range (the collision case has its own dedicated test).
    data["dashboard"]["port"] = port + 1 if port < 65_535 else port - 1  # type: ignore[index]
    cfg = Config.model_validate(data)
    assert cfg.grpc.port == port


# -----------------------------------------------------------------------------
# Log level (L3-OBS-021)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-021")
@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_log_level_accepts_canonical_values(tmp_path: Path, level: str) -> None:
    data = _minimal_valid_data(tmp_path)
    data["observability"] = {"log_level": level}
    cfg = Config.model_validate(data)
    assert cfg.observability.log_level == level


@pytest.mark.requirement("L3-OBS-021")
def test_log_level_rejects_unknown(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["observability"] = {"log_level": "TRACE"}
    with pytest.raises(ValidationError):
        Config.model_validate(data)


# -----------------------------------------------------------------------------
# Sweeper disposition set
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-SWEEP-007")
def test_sweeper_disposition_rejects_unknown_action(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["sweeper"] = {"disposition_actions": ["SHOUT_AT_USER"]}
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.requirement("L2-SWEEP-007")
def test_sweeper_disposition_accepts_multiple_actions(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["sweeper"] = {"disposition_actions": ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]}
    cfg = Config.model_validate(data)
    assert cfg.sweeper.disposition_actions == [
        "NOTIFY_ADMINS",
        "DISCARD_SILENTLY",
    ]


@pytest.mark.requirement("L3-SWEEP-011")
def test_sweeper_disposition_accepts_empty_list(tmp_path: Path) -> None:
    """Empty disposition_actions is permitted per L3-SWEEP-011.

    Orphaned runs still get the ORPHANED state transition; they simply
    receive no further action beyond it (equivalent to a single
    ``DISCARD_SILENTLY`` action).
    """
    data = _minimal_valid_data(tmp_path)
    data["sweeper"] = {"disposition_actions": []}
    cfg = Config.model_validate(data)
    assert cfg.sweeper.disposition_actions == []


# -----------------------------------------------------------------------------
# Email validation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-MAIL-003")
def test_invalid_from_address_rejected(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["mail"]["from_address"] = "not an email"  # type: ignore[index]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.requirement("L2-MAIL-003")
def test_invalid_admin_recipient_rejected(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["mail"]["admin_recipients"] = ["ops@example.com", "malformed"]  # type: ignore[index]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-CFG-004")
def test_optional_sections_fill_from_defaults(tmp_path: Path) -> None:
    """Omitted sections with default_factory SHALL be populated automatically."""
    cfg = Config.model_validate(_minimal_valid_data(tmp_path))
    # sweeper section omitted but should still be present with defaults
    assert cfg.sweeper.run_timeout_seconds == 3_600
    assert cfg.sweeper.poll_interval_seconds == 60
    # auth, observability, service, pipelines likewise
    assert cfg.auth.session_idle_timeout_seconds == 3_600
    assert cfg.observability.log_level == "INFO"
    assert cfg.service.shutdown_grace_period_seconds == 30
    assert cfg.pipelines.registered == []
