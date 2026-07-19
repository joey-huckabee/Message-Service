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
    AdminAccountConfig,
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
@pytest.mark.requirement("L3-API-010")
@pytest.mark.parametrize("port", [0, -1, 65_536, 99_999])
def test_grpc_port_out_of_range_rejected(tmp_path: Path, port: int) -> None:
    data = _minimal_valid_data(tmp_path)
    data["grpc"]["port"] = port  # type: ignore[index]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.requirement("L2-CFG-004")
@pytest.mark.requirement("L3-API-010")
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
# L3-API-001 / L3-API-009: GrpcConfig defaults + max_concurrent_rpcs
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-009")
def test_grpc_config_defaults_when_keys_missing() -> None:
    """L3-API-009: ``grpc`` section with no host/port SHALL use defaults
    rather than failing startup. Default host = "0.0.0.0"; default port = 50051.
    """
    cfg = GrpcConfig.model_validate({})
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 50_051


@pytest.mark.requirement("L3-OBS-014")
def test_audit_cleanup_interval_default_is_24_hours() -> None:
    """L3-OBS-014: ``cleanup_interval_hours`` SHALL default to 24."""
    cfg = AuditConfig.model_validate({})
    assert cfg.cleanup_interval_hours == 24


@pytest.mark.requirement("L3-OBS-014")
def test_audit_cleanup_interval_rejects_zero() -> None:
    """L3-OBS-014: ``cleanup_interval_hours`` SHALL be ge=1."""
    with pytest.raises(ValidationError):
        AuditConfig.model_validate({"cleanup_interval_hours": 0})


@pytest.mark.requirement("L3-PERS-027")
def test_filesystem_persistence_report_retention_days_default_is_90() -> None:
    """L3-PERS-027: ``report_retention_days`` default SHALL be 90; ge=1."""
    cfg = FilesystemPersistenceConfig.model_validate({"report_directory": "/tmp/x"})
    assert cfg.report_retention_days == 90


@pytest.mark.requirement("L3-PERS-027")
def test_filesystem_persistence_report_retention_days_rejects_zero() -> None:
    """L3-PERS-027: ``report_retention_days`` SHALL reject 0 / negative."""
    with pytest.raises(ValidationError):
        FilesystemPersistenceConfig.model_validate(
            {"report_directory": "/tmp/x", "report_retention_days": 0}
        )


@pytest.mark.requirement("L3-PERS-029")
def test_filesystem_persistence_prune_interval_default_is_86400() -> None:
    """L3-PERS-029: ``prune_interval_seconds`` default SHALL be 86400."""
    cfg = FilesystemPersistenceConfig.model_validate({"report_directory": "/tmp/x"})
    assert cfg.prune_interval_seconds == 86_400


@pytest.mark.requirement("L3-PERS-029")
def test_filesystem_persistence_max_prunes_per_iteration_default_is_1000() -> None:
    """L3-PERS-029: ``max_prunes_per_iteration`` default SHALL be 1000."""
    cfg = FilesystemPersistenceConfig.model_validate({"report_directory": "/tmp/x"})
    assert cfg.max_prunes_per_iteration == 1_000


@pytest.mark.requirement("L3-PERS-029")
def test_filesystem_persistence_pruner_intervals_reject_zero() -> None:
    """L3-PERS-029: prune_interval_seconds + max_prunes_per_iteration SHALL be ge=1."""
    with pytest.raises(ValidationError):
        FilesystemPersistenceConfig.model_validate(
            {"report_directory": "/tmp/x", "prune_interval_seconds": 0}
        )
    with pytest.raises(ValidationError):
        FilesystemPersistenceConfig.model_validate(
            {"report_directory": "/tmp/x", "max_prunes_per_iteration": 0}
        )


@pytest.mark.requirement("L3-DASH-003")
def test_dashboard_config_defaults_when_keys_missing() -> None:
    """L3-DASH-003: ``dashboard`` section with no host/port SHALL use
    defaults: host = "0.0.0.0"; port = 8080.
    """
    cfg = DashboardConfig.model_validate({})
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8080


@pytest.mark.requirement("L3-API-001")
def test_grpc_config_max_concurrent_rpcs_default_is_100() -> None:
    """L3-API-001: ``max_concurrent_rpcs`` default SHALL be 100."""
    cfg = GrpcConfig.model_validate({})
    assert cfg.max_concurrent_rpcs == 100


@pytest.mark.requirement("L3-API-001")
def test_grpc_config_max_concurrent_rpcs_must_be_positive() -> None:
    """L3-API-001: zero or negative SHALL be rejected at validation time."""
    with pytest.raises(ValidationError):
        GrpcConfig.model_validate({"port": 50051, "max_concurrent_rpcs": 0})
    with pytest.raises(ValidationError):
        GrpcConfig.model_validate({"port": 50051, "max_concurrent_rpcs": -1})


@pytest.mark.requirement("L3-API-001")
def test_grpc_config_max_concurrent_rpcs_override_accepted() -> None:
    """L3-API-001: an operator-supplied value SHALL be honored."""
    cfg = GrpcConfig.model_validate({"max_concurrent_rpcs": 250})
    assert cfg.max_concurrent_rpcs == 250


# -----------------------------------------------------------------------------
# L3-API-019: GrpcConfig max_in_flight_rpcs (rejecting concurrency limit)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-019")
def test_grpc_config_max_in_flight_rpcs_default_is_zero() -> None:
    """L3-API-019: ``max_in_flight_rpcs`` default SHALL be 0 (limit disabled)."""
    cfg = GrpcConfig.model_validate({})
    assert cfg.max_in_flight_rpcs == 0


@pytest.mark.requirement("L3-API-019")
def test_grpc_config_max_in_flight_rpcs_negative_rejected() -> None:
    """L3-API-019: a negative value SHALL be rejected at load time (floor 0)."""
    with pytest.raises(ValidationError):
        GrpcConfig.model_validate({"max_in_flight_rpcs": -1})


@pytest.mark.requirement("L3-API-019")
def test_grpc_config_max_in_flight_rpcs_positive_accepted() -> None:
    """L3-API-019: a positive value SHALL be honored (enables the limit)."""
    cfg = GrpcConfig.model_validate({"max_in_flight_rpcs": 8})
    assert cfg.max_in_flight_rpcs == 8


# -----------------------------------------------------------------------------
# L3-AUTH-018: AdminAccountConfig ([auth.admin])
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-AUTH-018")
def test_auth_admin_defaults_to_absent() -> None:
    """L3-AUTH-018: `[auth.admin]` is optional; absent → `auth.admin is None`."""
    from message_service.config.schema import AuthConfig

    cfg = AuthConfig.model_validate({})
    assert cfg.admin is None


@pytest.mark.requirement("L3-AUTH-018")
def test_auth_admin_present_parses() -> None:
    """L3-AUTH-018: a valid admin section parses to email + password."""
    admin = AdminAccountConfig.model_validate({"email": "admin@example.com", "password": "s3cret"})
    assert admin.email == "admin@example.com"
    assert admin.password == "s3cret"


@pytest.mark.requirement("L3-AUTH-018")
@pytest.mark.parametrize("password", ["", "   ", "\t"])
def test_auth_admin_empty_password_rejected(password: str) -> None:
    """L3-AUTH-018: an empty/whitespace password is rejected at load time."""
    with pytest.raises(ValidationError):
        AdminAccountConfig.model_validate({"email": "admin@example.com", "password": password})


@pytest.mark.requirement("L3-AUTH-018")
def test_auth_admin_invalid_email_rejected() -> None:
    """L3-AUTH-018: `email` is validated as an email address."""
    with pytest.raises(ValidationError):
        AdminAccountConfig.model_validate({"email": "not-an-email", "password": "s3cret"})


@pytest.mark.requirement("L3-AUTH-018")
def test_auth_admin_forbids_extra_keys() -> None:
    """The admin section rejects unknown keys (frozen/forbid, like the rest)."""
    with pytest.raises(ValidationError):
        AdminAccountConfig.model_validate(
            {"email": "admin@example.com", "password": "s3cret", "role": "superuser"}
        )


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
@pytest.mark.requirement("L3-SWEEP-012")
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
@pytest.mark.requirement("L3-MAIL-004")
def test_invalid_from_address_rejected(tmp_path: Path) -> None:
    data = _minimal_valid_data(tmp_path)
    data["mail"]["from_address"] = "not an email"  # type: ignore[index]
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.requirement("L2-MAIL-003")
@pytest.mark.requirement("L3-MAIL-004")
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


# -----------------------------------------------------------------------------
# Per-pipeline subject templates (L3-MAIL-033)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_defaults_to_empty() -> None:
    """Omitting subject_templates yields an empty mapping (v1 behavior)."""
    assert PipelinesConfig(registered=["etl-nightly"]).subject_templates == {}


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_valid_mapping_loads() -> None:
    """A well-formed template for a registered pipeline validates."""
    cfg = PipelinesConfig(
        registered=["etl-nightly", "backup-daily"],
        subject_templates={"etl-nightly": "[NIGHTLY:{pipeline_type}] run {run_id}"},
    )
    assert cfg.subject_templates["etl-nightly"] == "[NIGHTLY:{pipeline_type}] run {run_id}"


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_rejects_unregistered_pipeline_key() -> None:
    """A template keyed on an unregistered pipeline_type is dead config → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            subject_templates={"not-registered": "{run_id}"},
        )


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_rejects_unknown_placeholder() -> None:
    """A template referencing a placeholder other than the two allowed → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            subject_templates={"etl-nightly": "[{severity}] {run_id}"},
        )


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_rejects_malformed_braces() -> None:
    """A template with unbalanced braces is not valid str.format → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            subject_templates={"etl-nightly": "[{pipeline_type] run {run_id}"},
        )


@pytest.mark.requirement("L3-MAIL-033")
def test_subject_templates_rejects_raw_crlf() -> None:
    """A template containing raw CR/LF is a header-injection risk → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            subject_templates={"etl-nightly": "run {run_id}\r\nBcc: x@example.com"},
        )


# -----------------------------------------------------------------------------
# Per-pipeline email body template overrides (L3-TMPL-033)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-033")
def test_body_template_overrides_defaults_to_empty() -> None:
    """Omitting email_body_template_overrides yields an empty mapping."""
    assert PipelinesConfig(registered=["etl-nightly"]).email_body_template_overrides == {}


@pytest.mark.requirement("L3-TMPL-033")
def test_body_template_overrides_valid_mapping_loads() -> None:
    """A (name, version) ref for a registered pipeline validates."""
    cfg = PipelinesConfig(
        registered=["etl-nightly", "backup-daily"],
        email_body_template_overrides={
            "etl-nightly": {"name": "nightly_body", "version": "2.0"},
        },
    )
    ref = cfg.email_body_template_overrides["etl-nightly"]
    assert (ref.name, ref.version) == ("nightly_body", "2.0")


@pytest.mark.requirement("L3-TMPL-033")
def test_body_template_overrides_rejects_unregistered_pipeline_key() -> None:
    """An override keyed on an unregistered pipeline_type is dead config → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            email_body_template_overrides={
                "not-registered": {"name": "x", "version": "1.0"},
            },
        )


# -----------------------------------------------------------------------------
# Per-pipeline orphan disposition overrides (L3-SWEEP-022)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-SWEEP-022")
def test_orphan_disposition_overrides_defaults_to_empty() -> None:
    """Omitting orphan_disposition_overrides yields an empty mapping."""
    assert PipelinesConfig(registered=["etl-nightly"]).orphan_disposition_overrides == {}


@pytest.mark.requirement("L3-SWEEP-022")
def test_orphan_disposition_overrides_valid_mapping_loads() -> None:
    """A valid mapping (including an empty action list) validates."""
    cfg = PipelinesConfig(
        registered=["etl-nightly", "test-pipeline"],
        orphan_disposition_overrides={
            "etl-nightly": ["NOTIFY_ADMINS", "DISCARD_SILENTLY"],
            "test-pipeline": [],
        },
    )
    assert cfg.orphan_disposition_overrides["etl-nightly"] == ["NOTIFY_ADMINS", "DISCARD_SILENTLY"]
    assert cfg.orphan_disposition_overrides["test-pipeline"] == []


@pytest.mark.requirement("L3-SWEEP-022")
def test_orphan_disposition_overrides_rejects_unregistered_pipeline_key() -> None:
    """An override keyed on an unregistered pipeline_type is dead config → reject."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            orphan_disposition_overrides={"not-registered": ["DISCARD_SILENTLY"]},
        )


@pytest.mark.requirement("L3-SWEEP-022")
def test_orphan_disposition_overrides_rejects_unknown_action_id() -> None:
    """An unknown action identifier is rejected by the DispositionAction Literal."""
    with pytest.raises(ValidationError):
        PipelinesConfig(
            registered=["etl-nightly"],
            orphan_disposition_overrides={"etl-nightly": ["BOGUS_ACTION"]},
        )


# -----------------------------------------------------------------------------
# Audit archive directory (L3-OBS-041)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-OBS-041")
def test_audit_archive_directory_defaults_to_none() -> None:
    """Omitting archive_directory yields None (no archival)."""
    assert AuditConfig().archive_directory is None


@pytest.mark.requirement("L3-OBS-041")
def test_audit_archive_directory_accepts_a_path() -> None:
    """A configured archive_directory parses to a Path."""
    cfg = AuditConfig(archive_directory="/var/audit-archive")
    assert cfg.archive_directory == Path("/var/audit-archive")
