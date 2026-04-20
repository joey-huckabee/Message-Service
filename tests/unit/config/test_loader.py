"""Unit tests for :mod:`message_service.config.loader`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from message_service.config.loader import (
    format_validation_errors,
    load_config,
)
from message_service.config.schema import Config
from message_service.domain.errors import ConfigurationError

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


_MINIMAL_TOML = """
[grpc]
host = "0.0.0.0"
port = 50051

[dashboard]
host = "0.0.0.0"
port = 8080

[persistence]
sqlite_path = "./data/service.sqlite"
[persistence.filesystem]
report_directory = "./data/reports"

[templates]
manifest_path = "./config/templates.manifest.toml"
[templates.email_body_template_ref]
name = "default_body"
version = "1.0"

[tags]
vocabulary_path = "./config/tags.toml"

[mail]
from_address = "svc@example.com"
[mail.smtp]
host = "smtp.example.com"
port = 587
"""


def _write_config(tmp_path: Path, body: str = _MINIMAL_TOML) -> Path:
    """Write a TOML config into tmp_path and return its path."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(body)
    return config_file


# -----------------------------------------------------------------------------
# Happy path (L3-CFG-004, L3-CFG-005)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-005")
def test_load_config_returns_frozen_config(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path)
    cfg = load_config(config_file)
    assert isinstance(cfg, Config)
    assert cfg.grpc.port == 50051


@pytest.mark.requirement("L3-CFG-004")
def test_load_config_accepts_string_path(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path)
    cfg = load_config(str(config_file))
    assert cfg.grpc.host == "0.0.0.0"


# -----------------------------------------------------------------------------
# File existence and readability (L3-CFG-015)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-015")
def test_missing_file_raises_configuration_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(missing)
    assert "not found" in str(exc_info.value)
    assert exc_info.value.details["config_path"] == str(missing.resolve())


@pytest.mark.requirement("L3-CFG-015")
def test_directory_instead_of_file_raises_configuration_error(tmp_path: Path) -> None:
    """Passing a directory path SHALL be rejected."""
    with pytest.raises(ConfigurationError):
        load_config(tmp_path)


# -----------------------------------------------------------------------------
# TOML syntax (L2-CFG-003)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-CFG-003")
def test_invalid_toml_raises_configuration_error(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path, body="this is === not valid toml [[[")
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(config_file)
    assert "not valid TOML" in str(exc_info.value)


@pytest.mark.requirement("L2-CFG-003")
def test_non_utf8_file_raises_configuration_error(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b"\xff\xfe\x00\x00not utf-8")
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(config_file)
    msg = str(exc_info.value)
    # Either UTF-8 or TOML error is acceptable depending on byte shape;
    # assert we got a ConfigurationError rather than something unhelpful.
    assert "UTF-8" in msg or "TOML" in msg


# -----------------------------------------------------------------------------
# Schema validation (L2-CFG-005)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L2-CFG-005")
def test_schema_violation_raises_validation_error(tmp_path: Path) -> None:
    """Out-of-range port should bubble up as ValidationError, not ConfigurationError."""
    body = _MINIMAL_TOML.replace("port = 50051", "port = 70000")
    config_file = _write_config(tmp_path, body=body)
    with pytest.raises(ValidationError):
        load_config(config_file)


@pytest.mark.requirement("L3-CFG-006")
def test_unknown_section_rejected_with_extra_forbid(tmp_path: Path) -> None:
    body = _MINIMAL_TOML + '\n[my_made_up_section]\nkey = "value"\n'
    config_file = _write_config(tmp_path, body=body)
    with pytest.raises(ValidationError):
        load_config(config_file)


# -----------------------------------------------------------------------------
# Path resolution (L3-CFG-010, L3-CFG-011)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-010")
def test_relative_paths_resolved_against_config_dir(tmp_path: Path) -> None:
    config_file = _write_config(tmp_path)
    cfg = load_config(config_file)
    assert cfg.persistence.sqlite_path.is_absolute()
    expected = (tmp_path / "data" / "service.sqlite").resolve()
    assert cfg.persistence.sqlite_path == expected


@pytest.mark.requirement("L3-CFG-010")
def test_absolute_paths_pass_through(tmp_path: Path) -> None:
    absolute_path = tmp_path / "absolute_service.sqlite"
    # TOML literal strings (single-quoted) do not interpret escape
    # sequences. This matters on Windows where the tmp_path contains
    # backslashes (`C:\Users\...`); a regular double-quoted TOML string
    # would try to read `\U` as an 8-hex-digit Unicode escape and fail.
    body = _MINIMAL_TOML.replace(
        'sqlite_path = "./data/service.sqlite"',
        f"sqlite_path = '{absolute_path}'",
    )
    config_file = _write_config(tmp_path, body=body)
    cfg = load_config(config_file)
    assert cfg.persistence.sqlite_path == absolute_path


@pytest.mark.requirement("L3-CFG-011")
def test_all_four_path_fields_are_resolved(tmp_path: Path) -> None:
    """Every path field listed in L3-CFG-011 SHALL be resolved."""
    config_file = _write_config(tmp_path)
    cfg = load_config(config_file)
    assert cfg.persistence.sqlite_path.is_absolute()
    assert cfg.persistence.filesystem.report_directory.is_absolute()
    assert cfg.templates.manifest_path.is_absolute()
    assert cfg.tags.vocabulary_path.is_absolute()


# -----------------------------------------------------------------------------
# Env-var substitution (L3-CFG-012, L3-CFG-013, L3-CFG-014)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-012")
def test_env_var_substitution_in_substitutable_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSG_TEST_SECRET", "hunter2")
    body = _MINIMAL_TOML + '\npassword = "${env:MSG_TEST_SECRET}"\n'
    config_file = _write_config(tmp_path, body=body)
    cfg = load_config(config_file)
    assert cfg.mail.smtp.password == "hunter2"


@pytest.mark.requirement("L3-CFG-012")
def test_env_var_substitution_multiple_vars_in_one_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSG_USER_PART", "admin")
    monkeypatch.setenv("MSG_PW_PART", "pw123")
    body = _MINIMAL_TOML + '\nusername = "${env:MSG_USER_PART}_${env:MSG_PW_PART}"\n'
    config_file = _write_config(tmp_path, body=body)
    cfg = load_config(config_file)
    assert cfg.mail.smtp.username == "admin_pw123"


@pytest.mark.requirement("L3-CFG-013")
def test_missing_env_var_raises_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MSG_DEFINITELY_UNSET", raising=False)
    body = _MINIMAL_TOML + '\npassword = "${env:MSG_DEFINITELY_UNSET}"\n'
    config_file = _write_config(tmp_path, body=body)
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(config_file)
    assert exc_info.value.details["env_var"] == "MSG_DEFINITELY_UNSET"


@pytest.mark.requirement("L3-CFG-014")
def test_substitution_does_not_apply_to_non_substitutable_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ${env:...} literal in a non-substitutable field SHALL pass through unchanged."""
    monkeypatch.setenv("MSG_TEST_VAR", "should_not_be_used")
    # smtp.host is NOT a SubstitutableStr; the literal should come through.
    body = _MINIMAL_TOML.replace(
        'host = "smtp.example.com"',
        'host = "${env:MSG_TEST_VAR}"',
    )
    config_file = _write_config(tmp_path, body=body)
    cfg = load_config(config_file)
    # host preserved literally (no substitution applied)
    assert cfg.mail.smtp.host == "${env:MSG_TEST_VAR}"


# -----------------------------------------------------------------------------
# Validation error formatting (L3-CFG-007, L3-CFG-008)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CFG-008")
def test_format_validation_errors_produces_numbered_lines(tmp_path: Path) -> None:
    """Output SHALL match the '  [N] <json_pointer>: <message>' format."""
    body = _MINIMAL_TOML.replace("port = 50051", "port = 70000")
    config_file = _write_config(tmp_path, body=body)
    try:
        load_config(config_file)
        pytest.fail("expected ValidationError")
    except ValidationError as exc:
        formatted = format_validation_errors(exc)

    # Each line starts with "  [N] /" — two leading spaces, bracketed index,
    # space, then a JSON Pointer path.
    for line in formatted.splitlines():
        assert line.startswith("  [")
        assert "] /" in line, f"line did not contain '] /': {line!r}"


@pytest.mark.requirement("L3-CFG-008")
def test_format_validation_errors_numbering_starts_at_one(tmp_path: Path) -> None:
    body = _MINIMAL_TOML.replace("port = 50051", "port = 70000").replace(
        "port = 8080", "port = 80000"
    )
    config_file = _write_config(tmp_path, body=body)
    try:
        load_config(config_file)
        pytest.fail("expected ValidationError")
    except ValidationError as exc:
        formatted = format_validation_errors(exc)

    lines = formatted.splitlines()
    assert lines[0].startswith("  [1] ")
    if len(lines) > 1:
        assert lines[1].startswith("  [2] ")
